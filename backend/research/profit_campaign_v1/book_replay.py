"""Causal Binance L2 reconstruction for the profit campaigns.

The supported archive contains periodic full snapshots and 100 ms diff events.
Diffs are consumed in local receive-time order. Multiple exchange events delivered
in one recorder poll become one observable book state, so sub-poll ordering cannot
manufacture latency alpha.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import heapq
import json
from pathlib import Path
import zipfile
from typing import Iterable, Iterator

import duckdb
import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from .contracts import (
    DEFAULT_BINANCE_ARCHIVE,
    DEFAULT_FUNDING_ARCHIVE,
    DEFAULT_INPUT_ROOT,
)


ARCHIVE_MEMBERS = (
    "orderbook_diffs_20260418.parquet",
    "orderbook_snapshots_20260418.parquet",
    "trades_20260418.parquet",
)
NORMALIZED_BOOKS = "normalized_books_20260418.parquet"
TRADE_FEATURES = "trade_flow_20260418.parquet"


def _timestamp_ns(value: datetime | pd.Timestamp) -> int:
    return int(pd.Timestamp(value).value)


def ensure_inputs(
    archive_path: Path = DEFAULT_BINANCE_ARCHIVE,
    input_root: Path = DEFAULT_INPUT_ROOT,
) -> dict[str, Path]:
    target = input_root / "archive5"
    target.mkdir(parents=True, exist_ok=True)
    missing = [name for name in ARCHIVE_MEMBERS if not (target / name).exists()]
    if missing:
        if not archive_path.exists():
            raise FileNotFoundError(
                f"missing Binance L2 archive and extracted inputs: {archive_path}"
            )
        with zipfile.ZipFile(archive_path) as archive:
            available = set(archive.namelist())
            absent = sorted(set(missing) - available)
            if absent:
                raise ValueError(f"Binance archive is missing members: {absent}")
            for member in missing:
                archive.extract(member, target)
    return {name: target / name for name in ARCHIVE_MEMBERS}


def ensure_funding_input(
    archive_path: Path = DEFAULT_FUNDING_ARCHIVE,
    input_root: Path = DEFAULT_INPUT_ROOT,
) -> Path | None:
    existing = list(
        input_root.glob("raw/funding_rates/symbol=BTCUSDT/year=2026/month=04/*.parquet")
    )
    if existing:
        return existing[0]
    if not archive_path.exists():
        return None
    with zipfile.ZipFile(archive_path) as archive:
        candidates = [
            name
            for name in archive.namelist()
            if (
                name.startswith("raw/funding_rates/symbol=BTCUSDT/")
                and "/year=2026/month=04/" in name
                and name.endswith(".parquet")
            )
        ]
        if not candidates:
            return None
        archive.extract(candidates[0], input_root)
        return input_root / candidates[0]


class _BookSide:
    """Mutable price map plus versioned heap for efficient top-depth extraction."""

    def __init__(self, *, bids: bool):
        self.bids = bids
        self.quantities: dict[float, tuple[float, int]] = {}
        self.heap: list[tuple[float, int, float]] = []
        self.version = 0

    def reset(self, levels: Iterable[Iterable[str | float]]) -> None:
        self.quantities.clear()
        self.heap.clear()
        self.version = 0
        for price_raw, quantity_raw in levels:
            price = float(price_raw)
            quantity = float(quantity_raw)
            if price > 0 and quantity > 0:
                self.version += 1
                self.quantities[price] = (quantity, self.version)
                key = -price if self.bids else price
                self.heap.append((key, self.version, price))
        heapq.heapify(self.heap)

    def update(self, levels: Iterable[Iterable[str | float]]) -> None:
        for price_raw, quantity_raw in levels:
            price = float(price_raw)
            quantity = float(quantity_raw)
            self.version += 1
            if quantity <= 0:
                self.quantities.pop(price, None)
                continue
            self.quantities[price] = (quantity, self.version)
            heapq.heappush(
                self.heap,
                (-price if self.bids else price, self.version, price),
            )

    def top_for_notional(
        self,
        *,
        required_notional: float,
        minimum_levels: int = 20,
        maximum_levels: int = 2_000,
    ) -> tuple[list[float], list[float]]:
        retained: list[tuple[float, int, float]] = []
        prices: list[float] = []
        quantities: list[float] = []
        notional = 0.0
        while self.heap and len(prices) < maximum_levels:
            item = heapq.heappop(self.heap)
            _, version, price = item
            current = self.quantities.get(price)
            if current is None or current[1] != version:
                continue
            quantity = current[0]
            retained.append(item)
            prices.append(price)
            quantities.append(quantity)
            notional += price * quantity
            if len(prices) >= minimum_levels and notional >= required_notional:
                break
        for item in retained:
            heapq.heappush(self.heap, item)
        return prices, quantities


@dataclass(slots=True)
class _MutableBook:
    bids: _BookSide
    asks: _BookSide
    last_update_id: int = 0
    exchange_ts_ns: int = 0
    receive_ts_ns: int = 0
    sequence_healthy: bool = False
    source_snapshot_ts_ns: int = 0

    @classmethod
    def create(cls) -> "_MutableBook":
        return cls(_BookSide(bids=True), _BookSide(bids=False))

    def reset_from_snapshot(self, row: dict) -> None:
        self.bids.reset(json.loads(row["bids"]))
        self.asks.reset(json.loads(row["asks"]))
        self.last_update_id = int(row["last_update_id"])
        self.exchange_ts_ns = _timestamp_ns(row["time"])
        self.receive_ts_ns = _timestamp_ns(row["received_at"])
        self.source_snapshot_ts_ns = self.receive_ts_ns
        self.sequence_healthy = True

    def apply_diff(self, row: dict) -> None:
        first_id = int(row["first_update_id"])
        final_id = int(row["final_update_id"])
        if final_id <= self.last_update_id:
            return
        if not (first_id <= self.last_update_id + 1 <= final_id):
            self.sequence_healthy = False
            return
        self.bids.update(json.loads(row["bids"]))
        self.asks.update(json.loads(row["asks"]))
        self.last_update_id = final_id
        self.exchange_ts_ns = max(self.exchange_ts_ns, _timestamp_ns(row["time"]))
        self.receive_ts_ns = max(
            self.receive_ts_ns, _timestamp_ns(row["received_at"])
        )


def _iter_parquet_rows(path: Path, columns: list[str]) -> Iterator[dict]:
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=4_096, columns=columns):
        values = batch.to_pydict()
        for index in range(batch.num_rows):
            yield {name: values[name][index] for name in columns}


def _emit_book(
    book: _MutableBook,
    *,
    required_notional: float,
) -> dict | None:
    if not book.sequence_healthy:
        return None
    bid_prices, bid_quantities = book.bids.top_for_notional(
        required_notional=required_notional
    )
    ask_prices, ask_quantities = book.asks.top_for_notional(
        required_notional=required_notional
    )
    if not bid_prices or not ask_prices or bid_prices[0] >= ask_prices[0]:
        return None
    mid = (bid_prices[0] + ask_prices[0]) / 2.0
    top_total = bid_quantities[0] + ask_quantities[0]
    top_imbalance = (
        (bid_quantities[0] - ask_quantities[0]) / top_total
        if top_total > 0
        else 0.0
    )
    bid_20 = sum(bid_quantities[:20])
    ask_20 = sum(ask_quantities[:20])
    depth_total = bid_20 + ask_20
    return {
        "exchange_ts_ns": book.exchange_ts_ns,
        "receive_ts_ns": book.receive_ts_ns,
        "sequence_id": str(book.last_update_id),
        "source_snapshot_ts_ns": book.source_snapshot_ts_ns,
        "sequence_healthy": True,
        "best_bid": bid_prices[0],
        "best_ask": ask_prices[0],
        "bid_size": bid_quantities[0],
        "ask_size": ask_quantities[0],
        "mid": mid,
        "spread_bps": (ask_prices[0] - bid_prices[0]) / mid * 10_000.0,
        "top_imbalance": top_imbalance,
        "depth_imbalance_20": (
            (bid_20 - ask_20) / depth_total if depth_total > 0 else 0.0
        ),
        "bid_prices": bid_prices,
        "bid_quantities": bid_quantities,
        "ask_prices": ask_prices,
        "ask_quantities": ask_quantities,
    }


def build_normalized_books(
    paths: dict[str, Path],
    output_path: Path,
    *,
    maximum_capital_usd: float,
    force: bool = False,
) -> pd.DataFrame:
    if output_path.exists() and not force:
        return pd.read_parquet(output_path)

    snapshot_columns = [
        "time",
        "last_update_id",
        "bids",
        "asks",
        "received_at",
    ]
    snapshots = list(
        _iter_parquet_rows(paths["orderbook_snapshots_20260418.parquet"], snapshot_columns)
    )
    snapshot_index = 0
    diff_columns = [
        "time",
        "first_update_id",
        "final_update_id",
        "bids",
        "asks",
        "received_at",
    ]
    diff_iter = _iter_parquet_rows(
        paths["orderbook_diffs_20260418.parquet"], diff_columns
    )
    book = _MutableBook.create()
    pending_before_snapshot: list[dict] = []
    emitted: list[dict] = []
    required_notional = max(20_000.0, maximum_capital_usd * 2.0)

    current_receive_ns: int | None = None
    current_group: list[dict] = []

    def apply_snapshot_until(limit_ns: int) -> None:
        nonlocal snapshot_index, pending_before_snapshot
        while snapshot_index < len(snapshots):
            snapshot = snapshots[snapshot_index]
            if _timestamp_ns(snapshot["received_at"]) > limit_ns:
                break
            if book.last_update_id == 0 or not book.sequence_healthy:
                book.reset_from_snapshot(snapshot)
                for buffered in pending_before_snapshot:
                    book.apply_diff(buffered)
                pending_before_snapshot = []
                point = _emit_book(book, required_notional=required_notional)
                if point is not None:
                    emitted.append(point)
            snapshot_index += 1

    def apply_group(group: list[dict], receive_ns: int) -> None:
        if not group:
            return
        apply_snapshot_until(receive_ns)
        if book.last_update_id == 0:
            pending_before_snapshot.extend(group)
            return
        for row in group:
            book.apply_diff(row)
        book.receive_ts_ns = max(book.receive_ts_ns, receive_ns)
        point = _emit_book(book, required_notional=required_notional)
        if point is not None:
            if emitted and point["receive_ts_ns"] <= emitted[-1]["receive_ts_ns"]:
                emitted[-1] = point
            else:
                emitted.append(point)

    for row in diff_iter:
        receive_ns = _timestamp_ns(row["received_at"])
        if current_receive_ns is None:
            current_receive_ns = receive_ns
        if receive_ns != current_receive_ns:
            apply_group(current_group, current_receive_ns)
            current_group = []
            current_receive_ns = receive_ns
        current_group.append(row)
    if current_receive_ns is not None:
        apply_group(current_group, current_receive_ns)
    apply_snapshot_until(2**63 - 1)

    if not emitted:
        raise ValueError("L2 reconstruction produced no healthy book states")
    frame = pd.DataFrame(emitted).sort_values("receive_ts_ns")
    if frame["receive_ts_ns"].duplicated().any():
        raise ValueError("normalized book contains duplicate receive timestamps")
    if not (frame["best_bid"] < frame["best_ask"]).all():
        raise ValueError("normalized book contains crossed quotes")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output_path, index=False)
    return frame


def build_trade_flow(
    trades_path: Path,
    output_path: Path,
    *,
    force: bool = False,
) -> pd.DataFrame:
    if output_path.exists() and not force:
        return pd.read_parquet(output_path)
    connection = duckdb.connect()
    try:
        frame = connection.execute(
            """
            SELECT
                epoch_ns(time_bucket(INTERVAL '1 second', received_at))::BIGINT
                    AS receive_ts_ns,
                count(*)::BIGINT AS trade_count,
                sum(qty)::DOUBLE AS trade_qty,
                sum(price * qty)::DOUBLE AS trade_notional,
                sum(CASE WHEN is_buyer_maker THEN -qty ELSE qty END)::DOUBLE
                    AS signed_qty,
                sum(CASE WHEN is_buyer_maker THEN -(price * qty)
                         ELSE price * qty END)::DOUBLE AS signed_notional,
                max(epoch_ns(time))::BIGINT AS max_exchange_ts_ns
            FROM read_parquet(?)
            WHERE symbol = 'BTCUSDT'
            GROUP BY 1
            ORDER BY 1
            """,
            [str(trades_path)],
        ).fetchdf()
    finally:
        connection.close()
    if frame.empty:
        raise ValueError("trade flow aggregation produced no rows")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output_path, index=False)
    return frame


def load_or_build_market_data(
    *,
    archive_path: Path = DEFAULT_BINANCE_ARCHIVE,
    input_root: Path = DEFAULT_INPUT_ROOT,
    maximum_capital_usd: float = 10_000.0,
    force: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Path]]:
    paths = ensure_inputs(archive_path, input_root)
    funding_path = ensure_funding_input(
        DEFAULT_FUNDING_ARCHIVE,
        input_root,
    )
    normalized_path = input_root / NORMALIZED_BOOKS
    flow_path = input_root / TRADE_FEATURES
    books = build_normalized_books(
        paths,
        normalized_path,
        maximum_capital_usd=maximum_capital_usd,
        force=force,
    )
    trades = build_trade_flow(
        paths["trades_20260418.parquet"], flow_path, force=force
    )
    output_paths = {
        **paths,
        "normalized_books": normalized_path,
        "trade_flow": flow_path,
    }
    if funding_path is not None:
        output_paths["funding_rates"] = funding_path
    return books, trades, output_paths


def data_quality_summary(
    books: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    maximum_gap_ms: int = 10_000,
) -> dict:
    receive = books["receive_ts_ns"].to_numpy(np.int64)
    intervals_ms = np.diff(receive) / 1_000_000.0
    exchange_lag_ms = (
        books["receive_ts_ns"].to_numpy(np.int64)
        - books["exchange_ts_ns"].to_numpy(np.int64)
    ) / 1_000_000.0
    start = pd.to_datetime(int(receive.min()), unit="ns", utc=True)
    end = pd.to_datetime(int(receive.max()), unit="ns", utc=True)
    archive_span_hours = float(
        (receive.max() - receive.min()) / 3_600_000_000_000
    )
    return {
        "book_states": int(len(books)),
        "trade_seconds": int(len(trades)),
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "archive_span_hours": archive_span_hours,
        "maximum_book_age_ms": int(maximum_gap_ms),
        "fresh_book_sessions": int(
            1 + np.sum(intervals_ms > float(maximum_gap_ms))
        ),
        "gaps_over_maximum_book_age": int(
            np.sum(intervals_ms > float(maximum_gap_ms))
        ),
        "gaps_over_60s": int(np.sum(intervals_ms > 60_000.0)),
        "maximum_receive_gap_seconds": float(intervals_ms.max() / 1_000.0),
        "utc_dates_touched": int(
            pd.Series(pd.to_datetime(receive, unit="ns", utc=True).date).nunique()
        ),
        "receive_interval_ms_q50": float(np.quantile(intervals_ms, 0.5)),
        "receive_interval_ms_q90": float(np.quantile(intervals_ms, 0.9)),
        "receive_interval_ms_q99": float(np.quantile(intervals_ms, 0.99)),
        "last_event_exchange_to_receive_ms_q50": float(
            np.quantile(exchange_lag_ms, 0.5)
        ),
        "last_event_exchange_to_receive_ms_q90": float(
            np.quantile(exchange_lag_ms, 0.9)
        ),
        "subsecond_latency_resolvable": bool(np.quantile(intervals_ms, 0.5) <= 250.0),
        "sequence_healthy_fraction": float(books["sequence_healthy"].mean()),
        "crossed_book_rows": int((books["best_bid"] >= books["best_ask"]).sum()),
    }
