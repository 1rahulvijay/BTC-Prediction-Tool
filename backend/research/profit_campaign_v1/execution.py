"""Shared executable LONG/SHORT accounting for PROFIT_CAMPAIGN_V1."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import bisect
import math
from typing import Iterable

import numpy as np
import pandas as pd


NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True, slots=True)
class LadderFill:
    requested_quantity: float
    filled_quantity: float
    average_price: float | None
    top_price: float | None
    slippage_bps: float | None

    @property
    def fill_fraction(self) -> float:
        return (
            self.filled_quantity / self.requested_quantity
            if self.requested_quantity > 0
            else 0.0
        )


@dataclass(frozen=True, slots=True)
class RoundTrip:
    decision_ts_ns: int
    entry_ts_ns: int | None
    exit_ts_ns: int | None
    horizon_seconds: int
    latency_ms: int
    capital_usd: float
    action: str
    requested_quantity: float
    filled_quantity: float
    fill_fraction: float
    entry_price: float | None
    exit_price: float | None
    entry_slippage_bps: float | None
    exit_slippage_bps: float | None
    gross_pnl_usd: float | None
    fee_usd: float | None
    impact_reserve_usd: float | None
    funding_usd: float | None
    net_pnl_usd: float | None
    net_return_bps: float | None
    mfe_usd: float | None
    mae_usd: float | None
    target_before_stop: bool | None
    time_to_first_profit_seconds: float | None
    observed_holding_seconds: float | None
    status: str
    reason: str | None

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ExecutablePath:
    decision_ts_ns: int
    entry_ts_ns: int
    action: str
    horizon_seconds: int
    capital_usd: float
    requested_quantity: float
    filled_quantity: float
    entry_price: float
    entry_mid: float
    entry_fee_usd: float
    entry_impact_reserve_usd: float
    book_indices: tuple[int, ...]
    timestamps_ns: tuple[int, ...]
    net_pnl_usd: tuple[float, ...]
    exit_prices: tuple[float, ...]
    exit_slippage_bps: tuple[float, ...]
    exit_fee_usd: tuple[float, ...]
    exit_impact_reserve_usd: tuple[float, ...]
    funding_usd: tuple[float, ...]


def walk_ladder(
    prices: Iterable[float],
    quantities: Iterable[float],
    requested_quantity: float,
) -> LadderFill:
    requested = float(requested_quantity)
    if not math.isfinite(requested) or requested <= 0:
        raise ValueError("requested quantity must be finite and positive")
    remaining = requested
    notional = 0.0
    filled = 0.0
    top_price: float | None = None
    for price_raw, quantity_raw in zip(prices, quantities):
        price = float(price_raw)
        quantity = float(quantity_raw)
        if price <= 0 or quantity <= 0:
            continue
        if top_price is None:
            top_price = price
        take = min(remaining, quantity)
        notional += take * price
        filled += take
        remaining -= take
        if remaining <= 1e-12:
            break
    average = notional / filled if filled > 0 else None
    slippage = None
    if average is not None and top_price is not None:
        slippage = abs(average / top_price - 1.0) * 10_000.0
    return LadderFill(
        requested_quantity=requested,
        filled_quantity=filled,
        average_price=average,
        top_price=top_price,
        slippage_bps=slippage,
    )


def _entry_fill(row: pd.Series, action: str, quantity: float) -> LadderFill:
    if action == "LONG":
        return walk_ladder(row["ask_prices"], row["ask_quantities"], quantity)
    if action == "SHORT":
        return walk_ladder(row["bid_prices"], row["bid_quantities"], quantity)
    raise ValueError(f"unsupported action: {action}")


def _exit_fill(row: pd.Series, action: str, quantity: float) -> LadderFill:
    if action == "LONG":
        return walk_ladder(row["bid_prices"], row["bid_quantities"], quantity)
    if action == "SHORT":
        return walk_ladder(row["ask_prices"], row["ask_quantities"], quantity)
    raise ValueError(f"unsupported action: {action}")


def _funding_pnl(
    *,
    action: str,
    quantity: float,
    entry_ts_ns: int,
    exit_ts_ns: int,
    funding_events: pd.DataFrame | None,
    books: pd.DataFrame,
    book_timestamps: np.ndarray,
    book_mids: np.ndarray,
) -> float:
    if funding_events is None or funding_events.empty:
        return 0.0
    funding_ts = funding_events.attrs.get("_funding_ts_ns")
    funding_rates = funding_events.attrs.get("_funding_rates")
    if funding_ts is None or funding_rates is None:
        funding_ts = funding_events["funding_ts_ns"].to_numpy(np.int64)
        funding_rates = funding_events["funding_rate"].to_numpy(float)
        funding_events.attrs["_funding_ts_ns"] = funding_ts
        funding_events.attrs["_funding_rates"] = funding_rates
    left = int(np.searchsorted(funding_ts, entry_ts_ns, side="right"))
    right = int(np.searchsorted(funding_ts, exit_ts_ns, side="right"))
    if left >= right:
        return 0.0
    direction = 1.0 if action == "LONG" else -1.0
    total = 0.0
    for event_index in range(left, right):
        index = int(
            np.searchsorted(
                book_timestamps, int(funding_ts[event_index]), side="left"
            )
        )
        index = min(index, len(book_mids) - 1)
        total += (
            -direction
            * quantity
            * book_mids[index]
            * float(funding_rates[event_index])
        )
    return total


def load_funding_events(path: str | None) -> pd.DataFrame:
    if path is None:
        return pd.DataFrame(columns=["funding_ts_ns", "funding_rate"])
    frame = pd.read_parquet(path)
    required = {"funding_time", "funding_rate"}
    if not required <= set(frame.columns):
        raise ValueError(f"funding archive missing columns: {sorted(required - set(frame.columns))}")
    result = frame[["funding_time", "funding_rate"]].copy()
    result = result.drop_duplicates("funding_time").dropna()
    result["funding_ts_ns"] = result["funding_time"].astype(np.int64) * 1_000_000
    output = result[["funding_ts_ns", "funding_rate"]].sort_values(
        "funding_ts_ns"
    )
    output.attrs["_funding_ts_ns"] = output["funding_ts_ns"].to_numpy(np.int64)
    output.attrs["_funding_rates"] = output["funding_rate"].to_numpy(float)
    return output


def first_eligible_index(
    receive_timestamps: np.ndarray,
    *,
    decision_ts_ns: int,
    latency_ms: int,
    signal_expiry_ms: int,
) -> int | None:
    target = decision_ts_ns + latency_ms * 1_000_000
    index = bisect.bisect_left(receive_timestamps, target)
    if index >= len(receive_timestamps):
        return None
    if receive_timestamps[index] > decision_ts_ns + signal_expiry_ms * 1_000_000:
        return None
    return index


def _book_is_fresh(
    books: pd.DataFrame,
    index: int,
    maximum_book_age_ms: int,
) -> bool:
    row = books.iloc[index]
    return (
        int(row["receive_ts_ns"]) - int(row["exchange_ts_ns"])
        <= maximum_book_age_ms * 1_000_000
    )


def _path_has_receive_gap(
    receive_timestamps: np.ndarray,
    start_index: int,
    end_index: int,
    maximum_book_age_ms: int,
) -> bool:
    if end_index <= start_index:
        return False
    maximum_gap_ns = maximum_book_age_ms * 1_000_000
    return bool(
        np.any(
            np.diff(receive_timestamps[start_index : end_index + 1])
            > maximum_gap_ns
        )
    )


def simulate_round_trip(
    books: pd.DataFrame,
    *,
    decision_ts_ns: int,
    action: str,
    horizon_seconds: int,
    latency_ms: int,
    capital_usd: float,
    fee_bps: float,
    impact_bps: float,
    signal_expiry_ms: int,
    minimum_fill_fraction: float,
    maximum_book_age_ms: int = 10_000,
    funding_events: pd.DataFrame | None = None,
    target_net_bps: float = 15.0,
    stop_net_bps: float = 10.0,
    include_path_metrics: bool = True,
    receive_timestamps: np.ndarray | None = None,
    mid_prices: np.ndarray | None = None,
) -> RoundTrip:
    receive = (
        books["receive_ts_ns"].to_numpy(np.int64)
        if receive_timestamps is None
        else receive_timestamps
    )
    mids = (
        books["mid"].to_numpy(float) if mid_prices is None else mid_prices
    )
    entry_index = first_eligible_index(
        receive,
        decision_ts_ns=decision_ts_ns,
        latency_ms=latency_ms,
        signal_expiry_ms=signal_expiry_ms,
    )
    if entry_index is None:
        return _rejected(
            decision_ts_ns,
            horizon_seconds,
            latency_ms,
            capital_usd,
            action,
            "no_fresh_book_after_latency",
        )
    if not _book_is_fresh(books, entry_index, maximum_book_age_ms):
        return _rejected(
            decision_ts_ns,
            horizon_seconds,
            latency_ms,
            capital_usd,
            action,
            "stale_entry_book",
        )
    entry_row = books.iloc[entry_index]
    reference = (
        float(entry_row["best_ask"])
        if action == "LONG"
        else float(entry_row["best_bid"])
    )
    quantity = capital_usd / reference
    entry = _entry_fill(entry_row, action, quantity)
    if entry.average_price is None or entry.fill_fraction < minimum_fill_fraction:
        return _rejected(
            decision_ts_ns,
            horizon_seconds,
            latency_ms,
            capital_usd,
            action,
            "insufficient_entry_depth",
            requested_quantity=quantity,
            filled_quantity=entry.filled_quantity,
        )
    quantity = entry.filled_quantity
    exit_target = int(entry_row["receive_ts_ns"]) + horizon_seconds * NS_PER_SECOND
    exit_index = bisect.bisect_left(receive, exit_target, lo=entry_index + 1)
    if exit_index >= len(books):
        return _rejected(
            decision_ts_ns,
            horizon_seconds,
            latency_ms,
            capital_usd,
            action,
            "no_exit_book",
            requested_quantity=quantity,
            filled_quantity=quantity,
            entry_ts_ns=int(entry_row["receive_ts_ns"]),
        )
    if (
        receive[exit_index] - exit_target
        > maximum_book_age_ms * 1_000_000
    ):
        return _rejected(
            decision_ts_ns,
            horizon_seconds,
            latency_ms,
            capital_usd,
            action,
            "stale_exit_book",
            requested_quantity=quantity,
            filled_quantity=quantity,
            entry_ts_ns=int(entry_row["receive_ts_ns"]),
        )
    if _path_has_receive_gap(
        receive,
        entry_index,
        exit_index,
        maximum_book_age_ms,
    ):
        return _rejected(
            decision_ts_ns,
            horizon_seconds,
            latency_ms,
            capital_usd,
            action,
            "book_gap_during_holding_period",
            requested_quantity=quantity,
            filled_quantity=quantity,
            entry_ts_ns=int(entry_row["receive_ts_ns"]),
        )
    if not _book_is_fresh(books, exit_index, maximum_book_age_ms):
        return _rejected(
            decision_ts_ns,
            horizon_seconds,
            latency_ms,
            capital_usd,
            action,
            "stale_exit_book",
            requested_quantity=quantity,
            filled_quantity=quantity,
            entry_ts_ns=int(entry_row["receive_ts_ns"]),
        )
    exit_row = books.iloc[exit_index]
    exit_fill = _exit_fill(exit_row, action, quantity)
    if exit_fill.average_price is None or exit_fill.fill_fraction < 1.0 - 1e-9:
        return _rejected(
            decision_ts_ns,
            horizon_seconds,
            latency_ms,
            capital_usd,
            action,
            "insufficient_exit_depth",
            requested_quantity=quantity,
            filled_quantity=exit_fill.filled_quantity,
            entry_ts_ns=int(entry_row["receive_ts_ns"]),
        )

    entry_price = float(entry.average_price)
    exit_price = float(exit_fill.average_price)
    sign = 1.0 if action == "LONG" else -1.0
    gross = sign * quantity * (exit_price - entry_price)
    entry_notional = quantity * entry_price
    exit_notional = quantity * exit_price
    fee = (entry_notional + exit_notional) * fee_bps / 10_000.0
    impact = (entry_notional + exit_notional) * impact_bps / 10_000.0
    funding = _funding_pnl(
        action=action,
        quantity=quantity,
        entry_ts_ns=int(entry_row["receive_ts_ns"]),
        exit_ts_ns=int(exit_row["receive_ts_ns"]),
        funding_events=funding_events,
        books=books,
        book_timestamps=receive,
        book_mids=mids,
    )
    net = gross - fee - impact + funding

    mfe = net
    mae = net
    first_profit: float | None = None
    target_before_stop: bool | None = None
    target_usd = capital_usd * target_net_bps / 10_000.0
    stop_usd = -capital_usd * stop_net_bps / 10_000.0
    path_indices = (
        range(entry_index + 1, exit_index + 1)
        if include_path_metrics
        else ()
    )
    for path_index in path_indices:
        path_row = books.iloc[path_index]
        path_fill = _exit_fill(path_row, action, quantity)
        if (
            path_fill.average_price is None
            or path_fill.fill_fraction < 1.0 - 1e-9
        ):
            continue
        path_price = float(path_fill.average_price)
        path_notional = quantity * path_price
        path_gross = sign * quantity * (path_price - entry_price)
        path_fee = (entry_notional + path_notional) * fee_bps / 10_000.0
        path_impact = (
            entry_notional + path_notional
        ) * impact_bps / 10_000.0
        path_funding = _funding_pnl(
            action=action,
            quantity=quantity,
            entry_ts_ns=int(entry_row["receive_ts_ns"]),
            exit_ts_ns=int(path_row["receive_ts_ns"]),
            funding_events=funding_events,
            books=books,
            book_timestamps=receive,
            book_mids=mids,
        )
        path_net = path_gross - path_fee - path_impact + path_funding
        mfe = max(mfe, path_net)
        mae = min(mae, path_net)
        elapsed = (
            int(path_row["receive_ts_ns"]) - int(entry_row["receive_ts_ns"])
        ) / NS_PER_SECOND
        if first_profit is None and path_net > 0:
            first_profit = elapsed
        if target_before_stop is None:
            if path_net >= target_usd:
                target_before_stop = True
            elif path_net <= stop_usd:
                target_before_stop = False
    return RoundTrip(
        decision_ts_ns=decision_ts_ns,
        entry_ts_ns=int(entry_row["receive_ts_ns"]),
        exit_ts_ns=int(exit_row["receive_ts_ns"]),
        horizon_seconds=horizon_seconds,
        latency_ms=latency_ms,
        capital_usd=capital_usd,
        action=action,
        requested_quantity=capital_usd / reference,
        filled_quantity=quantity,
        fill_fraction=entry.fill_fraction,
        entry_price=entry_price,
        exit_price=exit_price,
        entry_slippage_bps=entry.slippage_bps,
        exit_slippage_bps=exit_fill.slippage_bps,
        gross_pnl_usd=gross,
        fee_usd=fee,
        impact_reserve_usd=impact,
        funding_usd=funding,
        net_pnl_usd=net,
        net_return_bps=net / max(entry_notional, 1e-12) * 10_000.0,
        mfe_usd=mfe,
        mae_usd=mae,
        target_before_stop=target_before_stop,
        time_to_first_profit_seconds=first_profit,
        observed_holding_seconds=(
            int(exit_row["receive_ts_ns"]) - int(entry_row["receive_ts_ns"])
        )
        / NS_PER_SECOND,
        status="FILLED" if entry.fill_fraction >= 1.0 - 1e-9 else "PARTIAL",
        reason=None,
    )


def build_executable_path(
    books: pd.DataFrame,
    *,
    decision_ts_ns: int,
    action: str,
    horizon_seconds: int,
    latency_ms: int,
    capital_usd: float,
    fee_bps: float,
    impact_bps: float,
    signal_expiry_ms: int,
    minimum_fill_fraction: float,
    maximum_book_age_ms: int = 10_000,
    funding_events: pd.DataFrame | None = None,
    receive_timestamps: np.ndarray | None = None,
    mid_prices: np.ndarray | None = None,
) -> ExecutablePath | None:
    receive = (
        books["receive_ts_ns"].to_numpy(np.int64)
        if receive_timestamps is None
        else receive_timestamps
    )
    mids = books["mid"].to_numpy(float) if mid_prices is None else mid_prices
    entry_index = first_eligible_index(
        receive,
        decision_ts_ns=decision_ts_ns,
        latency_ms=latency_ms,
        signal_expiry_ms=signal_expiry_ms,
    )
    if entry_index is None:
        return None
    if not _book_is_fresh(books, entry_index, maximum_book_age_ms):
        return None
    entry_row = books.iloc[entry_index]
    reference = (
        float(entry_row["best_ask"])
        if action == "LONG"
        else float(entry_row["best_bid"])
    )
    requested = capital_usd / reference
    entry = _entry_fill(entry_row, action, requested)
    if entry.average_price is None or entry.fill_fraction < minimum_fill_fraction:
        return None
    quantity = entry.filled_quantity
    entry_price = float(entry.average_price)
    entry_notional = quantity * entry_price
    entry_fee = entry_notional * fee_bps / 10_000.0
    entry_impact = entry_notional * impact_bps / 10_000.0
    end_ts = int(entry_row["receive_ts_ns"]) + horizon_seconds * NS_PER_SECOND
    final_index = bisect.bisect_left(receive, end_ts, lo=entry_index + 1)
    if final_index >= len(books):
        return None
    if (
        receive[final_index] - end_ts
        > maximum_book_age_ms * 1_000_000
    ):
        return None
    if _path_has_receive_gap(
        receive,
        entry_index,
        final_index,
        maximum_book_age_ms,
    ):
        return None
    if not _book_is_fresh(books, final_index, maximum_book_age_ms):
        return None
    sign = 1.0 if action == "LONG" else -1.0
    path_indices = []
    timestamps = []
    net_values = []
    exit_prices = []
    slippage = []
    exit_fees = []
    exit_impacts = []
    funding_values = []
    for index in range(entry_index + 1, final_index + 1):
        row = books.iloc[index]
        fill = _exit_fill(row, action, quantity)
        if fill.average_price is None or fill.fill_fraction < 1.0 - 1e-9:
            continue
        exit_price = float(fill.average_price)
        exit_notional = quantity * exit_price
        gross = sign * quantity * (exit_price - entry_price)
        exit_fee = exit_notional * fee_bps / 10_000.0
        exit_impact = exit_notional * impact_bps / 10_000.0
        funding = _funding_pnl(
            action=action,
            quantity=quantity,
            entry_ts_ns=int(entry_row["receive_ts_ns"]),
            exit_ts_ns=int(row["receive_ts_ns"]),
            funding_events=funding_events,
            books=books,
            book_timestamps=receive,
            book_mids=mids,
        )
        path_indices.append(index)
        timestamps.append(int(row["receive_ts_ns"]))
        net_values.append(
            gross
            - entry_fee
            - exit_fee
            - entry_impact
            - exit_impact
            + funding
        )
        exit_prices.append(exit_price)
        slippage.append(float(fill.slippage_bps or 0.0))
        exit_fees.append(exit_fee)
        exit_impacts.append(exit_impact)
        funding_values.append(funding)
    if not net_values:
        return None
    return ExecutablePath(
        decision_ts_ns=decision_ts_ns,
        entry_ts_ns=int(entry_row["receive_ts_ns"]),
        action=action,
        horizon_seconds=horizon_seconds,
        capital_usd=capital_usd,
        requested_quantity=requested,
        filled_quantity=quantity,
        entry_price=entry_price,
        entry_mid=float(entry_row["mid"]),
        entry_fee_usd=entry_fee,
        entry_impact_reserve_usd=entry_impact,
        book_indices=tuple(path_indices),
        timestamps_ns=tuple(timestamps),
        net_pnl_usd=tuple(net_values),
        exit_prices=tuple(exit_prices),
        exit_slippage_bps=tuple(slippage),
        exit_fee_usd=tuple(exit_fees),
        exit_impact_reserve_usd=tuple(exit_impacts),
        funding_usd=tuple(funding_values),
    )


def _rejected(
    decision_ts_ns: int,
    horizon_seconds: int,
    latency_ms: int,
    capital_usd: float,
    action: str,
    reason: str,
    *,
    requested_quantity: float = 0.0,
    filled_quantity: float = 0.0,
    entry_ts_ns: int | None = None,
) -> RoundTrip:
    return RoundTrip(
        decision_ts_ns=decision_ts_ns,
        entry_ts_ns=entry_ts_ns,
        exit_ts_ns=None,
        horizon_seconds=horizon_seconds,
        latency_ms=latency_ms,
        capital_usd=capital_usd,
        action=action,
        requested_quantity=requested_quantity,
        filled_quantity=filled_quantity,
        fill_fraction=(
            filled_quantity / requested_quantity if requested_quantity > 0 else 0.0
        ),
        entry_price=None,
        exit_price=None,
        entry_slippage_bps=None,
        exit_slippage_bps=None,
        gross_pnl_usd=None,
        fee_usd=None,
        impact_reserve_usd=None,
        funding_usd=None,
        net_pnl_usd=None,
        net_return_bps=None,
        mfe_usd=None,
        mae_usd=None,
        target_before_stop=None,
        time_to_first_profit_seconds=None,
        observed_holding_seconds=None,
        status="REJECTED",
        reason=reason,
    )
