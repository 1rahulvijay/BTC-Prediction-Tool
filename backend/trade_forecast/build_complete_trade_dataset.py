"""Build immutable, executable complete-trade labels from the L2 snapshot.

The builder enforces the temporal boundary:

* features come from the last synchronized book at/before the decision;
* entry comes from the first book at/after decision + frozen latency;
* entry walks asks and future exits walk bids;
* requested-size completion is explicit (partial fills are never renamed full);
* settlement comes only from the official settlement export.

The available July 2026 snapshot is below the promotion gate. Its output is useful
for mechanics and pilot fitting only.
"""
from __future__ import annotations

import argparse
import bisect
import gc
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
RESEARCH = BACKEND / "research"
for directory in (str(BACKEND), str(RESEARCH)):
    if directory not in sys.path:
        sys.path.insert(0, directory)

from artifact_identity import atomic_write_json, hash_file, hash_json, hash_paths
from executable_fill_engine import (
    BookState,
    ladder_vwap,
    net_path,
)
from trade_forecast.trade_labels import (
    evaluate_exit_plan,
    logit,
    required_exit_bid,
    summarize_realized_path,
)
from trade_forecast.trade_schema import (
    BTC_TOUCH_BPS,
    CONFIG_VERSION,
    ENTRY_CHECKPOINTS_S,
    ENTRY_LATENCY_MS,
    FEATURE_COLUMNS,
    FUTURE_OFFSETS_S,
    OFFICIAL_RESOLUTION_SOURCES,
    target_offset_valid,
    HORIZONS,
    MAX_BTC_OBSERVATION_AGE_S,
    MAX_DECISION_BOOK_AGE_S,
    MAX_FUTURE_OBSERVATION_LAG_S,
    M0_STRESS_LATENCY_MS,
    MODE,
    PROMOTION_GATE,
    QUANTITIES,
    QUOTE_SURVIVAL_TOLERANCE,
    policy_hash,
    validate_candidate,
)


DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")


def _latest_l2_snapshot() -> Path:
    directory = DATA / "research_snapshots"
    candidates = list(directory.glob("polymarket_l2_*.duckdb"))
    return (
        max(candidates, key=lambda path: path.stat().st_mtime_ns)
        if candidates
        else directory / "polymarket_l2_latest.duckdb"
    )


DEFAULT_L2 = _latest_l2_snapshot()
DEFAULT_BTC = DATA / "pm_export_snapshots.parquet"
DEFAULT_SETTLEMENTS = DATA / "pm_export_settlements.parquet"
DEFAULT_OUTPUT = DATA / "research" / "complete_trade_forecast" / "complete_trade_dataset.parquet"
NS = 1_000_000_000


def _stable_source_hash(
    path: Path,
    expected_stat: tuple[int, int],
    *,
    enabled: bool = True,
) -> str | None:
    digest = hash_file(path) if enabled else None
    current = (path.stat().st_size, path.stat().st_mtime_ns)
    if current != expected_stat:
        raise RuntimeError(f"source changed while hashing: {path}")
    return digest


def _safe_div(numerator: float, denominator: float, default: float = 0.0) -> float:
    return float(numerator) / float(denominator) if abs(float(denominator)) > 1e-12 else default


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _last_index_at_or_before(timestamps: np.ndarray, target: float) -> int:
    return int(np.searchsorted(timestamps, target, side="right") - 1)


def _first_index_at_or_after(timestamps: np.ndarray, target: float) -> int:
    return int(np.searchsorted(timestamps, target, side="left"))


def load_rounds(
    conn: duckdb.DuckDBPyConnection,
    settlements_path: Path,
    horizons: tuple[int, ...],
) -> list[dict[str, Any]]:
    sql_path = settlements_path.as_posix().replace("'", "''")
    placeholders = ",".join("?" for _ in horizons)
    sources = ",".join(
        "'" + s.replace("'", "''") + "'" for s in OFFICIAL_RESOLUTION_SOURCES
    )
    rows = conn.execute(
        f"""
        WITH markets AS (
            SELECT condition_id, slug, horizon, start_ts, end_ts,
                   MAX(CASE WHEN outcome='UP' THEN asset_id END) AS up_asset,
                   MAX(CASE WHEN outcome='DOWN' THEN asset_id END) AS down_asset
            FROM pm_l2_markets
            WHERE horizon IN ({placeholders})
            GROUP BY 1,2,3,4,5
        )
        SELECT m.condition_id, m.slug, m.horizon, m.start_ts, m.end_ts,
               m.up_asset, m.down_asset, s.anchor_price, s.expiry_btc,
               s.settled_side, s.resolution_source
        FROM markets m
        JOIN read_parquet('{sql_path}') s ON s.slug=m.slug AND s.horizon=m.horizon
        WHERE m.up_asset IS NOT NULL AND m.down_asset IS NOT NULL
          AND s.settled_side IN (0,1)
          AND s.anchor_price > 0 AND s.expiry_btc > 0
          -- Official settlement only. An unofficial or inferred outcome is not ground truth,
          -- and a mislabelled settlement silently inverts the sign of every label built on it.
          AND s.resolution_source IN ({sources})
        ORDER BY m.start_ts, m.horizon
        """,
        list(horizons),
    ).fetchall()
    columns = (
        "condition_id",
        "slug",
        "horizon",
        "start_ts",
        "end_ts",
        "up_asset",
        "down_asset",
        "anchor_price",
        "expiry_btc",
        "settled_side",
        "resolution_source",
    )
    if not rows:
        # An empty result here is indistinguishable from "no data yet" downstream, and a dataset
        # built from zero rounds still produces a well-formed parquet and a confident-looking
        # manifest. Fail loudly instead, and say which gate emptied it.
        available = conn.execute(
            f"SELECT DISTINCT resolution_source FROM read_parquet('{sql_path}')"
        ).fetchall()
        raise RuntimeError(
            "no settled rounds passed the load gate. "
            f"allowed resolution_source={list(OFFICIAL_RESOLUTION_SOURCES)}; "
            f"present in {settlements_path.name}={[r[0] for r in available]}"
        )
    return [dict(zip(columns, row)) for row in rows]


def load_btc_timelines(path: Path, wanted_slugs: set[str]) -> dict[str, dict[str, Any]]:
    if not path.is_file() or not wanted_slugs:
        return {}
    conn = duckdb.connect()
    try:
        conn.execute("CREATE TEMP TABLE wanted_slugs(slug VARCHAR)")
        conn.executemany("INSERT INTO wanted_slugs VALUES (?)", [(slug,) for slug in wanted_slugs])
        sql_path = path.as_posix().replace("'", "''")
        frame = conn.execute(
            f"""
            SELECT p.ts, p.slug, p.seconds_left, p.seconds_elapsed,
                   p.anchor_price, p.btc_price, p.vol_60s_pct,
                   p.current_side, p.p_hold_cur, p.p_hold_up, p.p_hold_down
            FROM read_parquet('{sql_path}') p
            JOIN wanted_slugs w USING(slug)
            WHERE p.ts IS NOT NULL AND p.btc_price > 0 AND p.anchor_price > 0
            ORDER BY p.slug, p.ts
            """
        ).fetchdf()
    finally:
        conn.close()
    output: dict[str, dict[str, Any]] = {}
    for slug, group in frame.groupby("slug", sort=False):
        group = group.drop_duplicates("ts", keep="last").sort_values("ts").reset_index(drop=True)
        output[str(slug)] = {
            "frame": group,
            "ts": group["ts"].to_numpy(dtype=float),
            "btc": group["btc_price"].to_numpy(dtype=float),
        }
    return output


def load_books(
    conn: duckdb.DuckDBPyConnection, asset_ids: list[str]
) -> dict[str, list[BookState]]:
    if not asset_ids:
        return {}
    placeholders = ",".join("?" for _ in asset_ids)
    summaries = conn.execute(
        f"""
        SELECT asset_id, seq, recv_ts_ns, best_bid, best_ask,
               best_bid_size, best_ask_size, spread
        FROM pm_l2_book_summaries
        WHERE asset_id IN ({placeholders})
          AND valid AND synchronized
          AND best_bid IS NOT NULL AND best_ask IS NOT NULL
        ORDER BY asset_id, recv_ts_ns, seq
        """,
        asset_ids,
    ).fetchall()
    ladders: dict[tuple[str, int], tuple[list, list]] = defaultdict(lambda: ([], []))
    for asset, seq, side, price, size in conn.execute(
        f"""
        SELECT asset_id, seq, side, price, size
        FROM pm_l2_book_levels
        WHERE asset_id IN ({placeholders}) AND size > 0
        ORDER BY asset_id, seq, side, level_index
        """,
        asset_ids,
    ).fetchall():
        bids, asks = ladders[(asset, int(seq))]
        (bids if str(side).upper() == "BUY" else asks).append(
            (float(price), float(size))
        )
    output: dict[str, list[BookState]] = defaultdict(list)
    for asset, seq, recv_ns, best_bid, best_ask, bid_size, ask_size, spread in summaries:
        bids, asks = ladders.get((asset, int(seq)), ([], []))
        output[str(asset)].append(
            BookState(
                seq=int(seq),
                recv_ts_ns=int(recv_ns),
                best_bid=float(best_bid),
                best_ask=float(best_ask),
                best_bid_size=float(bid_size or 0.0),
                best_ask_size=float(ask_size or 0.0),
                spread=float(spread or (float(best_ask) - float(best_bid))),
                bids=bids or [(float(best_bid), float(bid_size or 0.0))],
                asks=asks or [(float(best_ask), float(ask_size or 0.0))],
            )
        )
    return dict(output)


def _book_before(books: list[BookState], decision_ns: int) -> tuple[int, BookState | None]:
    timestamps = [book.recv_ts_ns for book in books]
    index = bisect.bisect_right(timestamps, int(decision_ns)) - 1
    return (index, books[index] if index >= 0 else None)


def _book_velocity(books: list[BookState], decision_index: int, seconds: int) -> float:
    if decision_index < 0:
        return 0.0
    current = books[decision_index]
    target_ns = current.recv_ts_ns - int(seconds * NS)
    timestamps = [book.recv_ts_ns for book in books]
    prior_index = bisect.bisect_right(timestamps, target_ns) - 1
    if prior_index < 0:
        return 0.0
    return float(current.best_bid) - float(books[prior_index].best_bid)


def _btc_at(
    timeline: dict[str, Any],
    target_s: float,
    *,
    direction: str,
    max_lag_s: float,
) -> tuple[int, pd.Series | None, float | None]:
    timestamps = timeline["ts"]
    if direction == "before":
        index = _last_index_at_or_before(timestamps, target_s)
        lag = target_s - timestamps[index] if index >= 0 else None
    else:
        index = _first_index_at_or_after(timestamps, target_s)
        lag = timestamps[index] - target_s if index < len(timestamps) else None
    if index < 0 or index >= len(timestamps) or lag is None or lag > max_lag_s:
        return (-1, None, lag)
    return (index, timeline["frame"].iloc[index], float(lag))


def _historical_btc_return(
    timeline: dict[str, Any], current_index: int, seconds: int
) -> float:
    if current_index < 0:
        return 0.0
    timestamps = timeline["ts"]
    target = timestamps[current_index] - seconds
    prior = _last_index_at_or_before(timestamps, target)
    if prior < 0:
        return 0.0
    now_price = float(timeline["btc"][current_index])
    prior_price = float(timeline["btc"][prior])
    return _safe_div(now_price - prior_price, prior_price) * 10_000.0


def _depth_imbalance(book: BookState) -> tuple[float, float]:
    top_denominator = book.best_bid_size + book.best_ask_size
    top = _safe_div(book.best_bid_size - book.best_ask_size, top_denominator)
    bid_depth = sum(float(size) for _, size in book.bids)
    ask_depth = sum(float(size) for _, size in book.asks)
    depth = _safe_div(bid_depth - ask_depth, bid_depth + ask_depth)
    return top, depth


def _candidate_features(
    *,
    round_data: dict[str, Any],
    side: str,
    quantity: int,
    seconds_left: int,
    decision_ns: int,
    own_books: list[BookState],
    own_index: int,
    own_book: BookState,
    opp_book: BookState,
    btc_timeline: dict[str, Any],
    btc_index: int,
    btc_row: pd.Series,
) -> dict[str, Any]:
    side_up = side == "UP"
    current_btc = float(btc_row["btc_price"])
    anchor = float(round_data["anchor_price"])
    signed_distance = current_btc - anchor
    distance_side = signed_distance if side_up else -signed_distance
    distance_bps_side = _safe_div(distance_side, anchor) * 10_000.0
    p_hold = btc_row.get("p_hold_up" if side_up else "p_hold_down")
    if not math.isfinite(float(p_hold)) if p_hold is not None else True:
        p_hold = None
    top_imbalance, depth_imbalance = _depth_imbalance(own_book)
    velocity_30 = _book_velocity(own_books, own_index, 30)
    btc_return_30 = _historical_btc_return(btc_timeline, btc_index, 30)
    btc_delta_30 = current_btc * btc_return_30 / 10_000.0
    return {
        "round_id": str(round_data["condition_id"]),
        "slug": str(round_data["slug"]),
        # THE INDEPENDENT UNIT. One market moment = one decision opportunity, at which a deployable
        # policy emits exactly one action (BUY UP at one qty / BUY DOWN at one qty / NO_TRADE).
        # Every row sharing an exposure_id is a CANDIDATE for that single decision, not a separate
        # trade. Counting candidates as trades inflates n, profit factor, weekly coverage and Q5
        # occupancy all at once - the July pilot has 395 rounds but 24,996 rows, so treating rows
        # as independent overstates the sample by ~63x and lets BUY UP and BUY DOWN on the same
        # instant both land in Q5.
        "exposure_id": f"{round_data['condition_id']}@{int(seconds_left)}",
        "round_start_ts": int(round_data["start_ts"]),
        "round_end_ts": int(round_data["end_ts"]),
        "decision_ts_ns": int(decision_ns),
        "decision_ts": decision_ns / NS,
        "horizon": int(round_data["horizon"]),
        "seconds_left": int(seconds_left),
        "seconds_elapsed": int(round_data["horizon"]) * 60 - int(seconds_left),
        "side": side,
        "requested_qty": int(quantity),
        "side_up": int(side_up),
        "side_is_leader": int(distance_side >= 0.0),
        "current_btc": current_btc,
        "anchor_price": anchor,
        "distance_usd_side": distance_side,
        "distance_bps_side": distance_bps_side,
        "abs_distance_bps": abs(distance_bps_side),
        "btc_return_5s_bps": _historical_btc_return(btc_timeline, btc_index, 5),
        "btc_return_15s_bps": _historical_btc_return(btc_timeline, btc_index, 15),
        "btc_return_30s_bps": btc_return_30,
        "btc_return_60s_bps": _historical_btc_return(btc_timeline, btc_index, 60),
        "btc_vol_60s_pct": float(btc_row.get("vol_60s_pct") or 0.0),
        "p_hold_side": float(p_hold) if p_hold is not None else 0.5,
        "own_bid": own_book.best_bid,
        "own_ask": own_book.best_ask,
        "own_spread": own_book.spread,
        "own_bid_size": own_book.best_bid_size,
        "own_ask_size": own_book.best_ask_size,
        "own_bid_depth": sum(float(size) for _, size in own_book.bids),
        "own_ask_depth": sum(float(size) for _, size in own_book.asks),
        "own_bid_levels": len(own_book.bids),
        "own_ask_levels": len(own_book.asks),
        "opp_bid": opp_book.best_bid,
        "opp_ask": opp_book.best_ask,
        "opp_spread": opp_book.spread,
        "opp_bid_size": opp_book.best_bid_size,
        "opp_ask_size": opp_book.best_ask_size,
        "contract_bid_velocity_5s": _book_velocity(own_books, own_index, 5),
        "contract_bid_velocity_15s": _book_velocity(own_books, own_index, 15),
        "contract_bid_velocity_30s": velocity_30,
        "btc_share_sensitivity_30s": _safe_div(velocity_30, btc_delta_30),
        "top_imbalance": top_imbalance,
        "depth_imbalance": depth_imbalance,
        "decision_quote_age_s": (decision_ns - own_book.recv_ts_ns) / NS,
        "settlement_price": float(round_data["expiry_btc"]),
        "settlement_side": "UP" if int(round_data["settled_side"]) == 1 else "DOWN",
        "resolution_source": str(round_data["resolution_source"]),
        "config_version": CONFIG_VERSION,
        "policy_hash": policy_hash(),
    }


def _attach_btc_targets(
    row: dict[str, Any],
    timeline: dict[str, Any],
    decision_s: float,
    round_end_s: float,
) -> None:
    current = float(row["current_btc"])
    # A future offset that runs past this round's expiry is NOT a prediction target: the contract
    # has already settled, so any BTC price there is information the trade could never have used.
    # Entry checkpoints go down to 30s and 60s while FUTURE_OFFSETS_S reaches 120s, so without
    # this guard a 30s-left decision carried 60s and 120s post-settlement BTC information.
    # Invalid targets are NULL (unknown), never 0.0 (a confident "no move").
    seconds_left = float(row["seconds_left"])
    for offset in FUTURE_OFFSETS_S:
        if not target_offset_valid(offset, seconds_left):
            row[f"btc_price_{offset}s"] = None
            row[f"btc_delta_{offset}s"] = None
            continue
        _, future, _ = _btc_at(
            timeline,
            decision_s + offset,
            direction="after",
            max_lag_s=MAX_FUTURE_OBSERVATION_LAG_S,
        )
        price = float(future["btc_price"]) if future is not None else None
        row[f"btc_price_{offset}s"] = price
        row[f"btc_delta_{offset}s"] = price - current if price is not None else None
    row["btc_price_settlement"] = float(row["settlement_price"])
    row["btc_delta_settlement"] = float(row["settlement_price"]) - current
    timestamps = timeline["ts"]
    start = _first_index_at_or_after(timestamps, decision_s)
    stop = int(np.searchsorted(timestamps, round_end_s, side="right"))
    future_prices = timeline["btc"][max(0, start) : max(0, stop)]
    future_times = timestamps[max(0, start) : max(0, stop)]
    if len(future_prices):
        deltas = future_prices - current
        row["btc_actual_mfe"] = float(np.max(deltas))
        row["btc_actual_mae"] = float(np.min(deltas))
        threshold = current * float(BTC_TOUCH_BPS[int(row["horizon"])]) / 10_000.0
        current_side = 1 if current >= float(row["anchor_price"]) else -1
        event, event_s = "NONE", None
        for observed_at, price in zip(future_times, future_prices):
            elapsed = float(observed_at - decision_s)
            if current_side > 0 and price < float(row["anchor_price"]):
                event, event_s = "ANCHOR", elapsed
                break
            if current_side < 0 and price >= float(row["anchor_price"]):
                event, event_s = "ANCHOR", elapsed
                break
            if price - current >= threshold:
                event, event_s = "UPPER", elapsed
                break
            if current - price >= threshold:
                event, event_s = "LOWER", elapsed
                break
        row["btc_first_event"] = event
        row["btc_first_event_s"] = event_s
        row["btc_touch_threshold_bps"] = float(BTC_TOUCH_BPS[int(row["horizon"])])
        row["label_btc_touch_upper"] = int(np.max(deltas) >= threshold)
        row["label_btc_touch_lower"] = int(np.min(deltas) <= -threshold)
        if current_side > 0:
            row["label_btc_cross_anchor"] = int(np.min(future_prices) < float(row["anchor_price"]))
        else:
            row["label_btc_cross_anchor"] = int(np.max(future_prices) >= float(row["anchor_price"]))


def _attach_execution_labels(
    row: dict[str, Any],
    books: list[BookState],
    settle_value: float,
    decision_book: BookState,
) -> None:
    """`decision_book` is the last synchronized book at/before the decision - the book the
    decision was actually made against. Quote survival compares arrival to THAT book's size-aware
    VWAP, so it must be passed in explicitly rather than re-derived here."""
    decision_ns = int(row["decision_ts_ns"])
    requested = float(row["requested_qty"])
    path = net_path(
        books,
        decision_ns,
        ENTRY_LATENCY_MS,
        requested,
        settle_value,
        eligibility={
            "min_ask": 0.0,
            "max_ask": 1.0,
            "max_spread": 1.0,
            "min_top_ask_size": 0.0,
            "max_book_staleness_s": MAX_DECISION_BOOK_AGE_S,
        },
    )
    # The price the decision was actually made at, for the size actually requested. `own_ask` is
    # top-of-book and says nothing about filling 25 or 100 shares, so quote survival cannot be
    # defined against it - a quote can "survive" at the top while the size behind it evaporates.
    decision_ask_vwap, decision_ask_fill = ladder_vwap(decision_book.asks, requested)
    row["decision_ask_vwap"] = decision_ask_vwap
    row["decision_ask_fillable"] = int(decision_ask_fill >= requested - 1e-9)
    row.update(
        {
            "entry_eligible": int(path.eligible),
            "entry_reason": path.reason or "",
            "actual_entry_vwap": path.entry_vwap if path.eligible else None,
            "actual_entry_fee": path.entry_fee_per_share if path.eligible else None,
            "actual_filled_qty": path.filled_qty if path.eligible else 0.0,
            "entry_fill_fraction": (
                min(1.0, _safe_div(path.filled_qty, requested)) if path.eligible else 0.0
            ),
            "entry_complete": int(path.eligible and path.filled_qty >= requested - 1e-9),
            "actual_entry_latency_ms": (
                (path.entry_ts_s - decision_ns / NS) * 1000.0 if path.eligible else None
            ),
        }
    )
    if not path.eligible:
        return
    stress = net_path(
        books,
        decision_ns,
        M0_STRESS_LATENCY_MS,
        requested,
        settle_value,
        eligibility={
            "min_ask": 0.0,
            "max_ask": 1.0,
            "max_spread": 1.0,
            "min_top_ask_size": 0.0,
            "max_book_staleness_s": MAX_DECISION_BOOK_AGE_S,
        },
    )
    row["stress_1000ms_entry_complete"] = int(
        stress.eligible and stress.filled_qty >= requested - 1e-9
    )
    if row["stress_1000ms_entry_complete"]:
        stress_plan = evaluate_exit_plan(
            "TAKE_3C_OR_STOP_3C",
            list(stress.ts),
            list(stress.net),
            float(stress.settle_net),
        )
        row["stress_1000ms_take_3c_or_stop_3c_net"] = stress_plan["net"]
    entry_book_index = next(
        (index for index, book in enumerate(books) if book.seq == path.entry_seq), -1
    )
    entry_book = books[entry_book_index] if entry_book_index >= 0 else None
    row["entry_top_ask"] = entry_book.best_ask if entry_book else None
    row["entry_arrival_slippage"] = path.entry_vwap - float(row["own_ask"])
    # QUOTE SURVIVAL, measured against the size-aware decision price. Survival means BOTH that the
    # full quantity is still fillable on arrival AND that it is not materially worse than what was
    # quoted at the decision. "Some entry existed after latency" is a much weaker claim and is
    # what `entry_eligible` already says.
    decision_vwap = row.get("decision_ask_vwap")
    row["entry_vwap_slippage"] = (
        path.entry_vwap - float(decision_vwap) if decision_vwap is not None else None
    )
    row["entry_quote_survived"] = (
        int(
            bool(row["entry_complete"])
            and decision_vwap is not None
            and path.entry_vwap <= float(decision_vwap) + QUOTE_SURVIVAL_TOLERANCE
        )
        if decision_vwap is not None
        else None
    )
    for cents in (1, 2):
        row[f"entry_worse_by_{cents}c"] = (
            int(path.entry_vwap >= float(decision_vwap) + cents / 100.0)
            if decision_vwap is not None
            else None
        )
    row["break_even_bid"] = required_exit_bid(path.entry_vwap, 0.0)
    row["target_1c_bid"] = required_exit_bid(path.entry_vwap, 0.01)
    row["target_3c_bid"] = required_exit_bid(path.entry_vwap, 0.03)
    row["target_5c_bid"] = required_exit_bid(path.entry_vwap, 0.05)
    if not row["entry_complete"]:
        row["entry_reason"] = "partial_entry_fill"
        return

    books_by_seq = {book.seq: book for book in books}
    exit_vwaps: list[float] = []
    for seq in path.seqs:
        exit_book = books_by_seq.get(seq)
        vwap, filled = ladder_vwap(exit_book.bids if exit_book else [], requested)
        if vwap is None or filled < requested - 1e-9:
            raise AssertionError("net_path emitted a sequence without a full requested-size exit")
        exit_vwaps.append(float(vwap))

    row.update(summarize_realized_path(path.ts, path.net, path.settle_net))
    seconds_left = float(row["seconds_left"])
    for offset in FUTURE_OFFSETS_S:
        observed = [
            float(value)
            for elapsed, value in zip(path.ts, path.net)
            if float(elapsed) <= float(offset)
        ]
        # SETTLEMENT IS A TERMINAL COMPETING EVENT, NOT CENSORING.
        #
        # An earlier version of this code marked non-crossing late-checkpoint cases NULL, reasoning
        # that we "cannot know" what the missing seconds would have done. That was WRONG, and
        # wrong in the dangerous direction. The position cannot exist past settlement, so
        # "did +3c occur within 120s of entry?" is fully answered by the contract's own lifetime:
        # if it did not cross before the contract terminated, it did not cross. Answer 0.
        #
        # Marking those NULL drops DEFINITE FAILURES while retaining early successes - a textbook
        # upward selection bias that would have made every crossing head look better than it is.
        #
        # Three states, kept distinct:
        #   1     the event occurred before terminal settlement
        #   0     the event did not occur before terminal settlement (terminal, not unknown)
        #   NULL  evidence was genuinely missing or corrupt BEFORE the terminal boundary
        #
        # NULL is reserved for the exact future-PRICE targets (btc_price_120s,
        # share_bid_vwap_120s): there is no executable price 120s out when the contract settled
        # at 30s, so those really are undefined. An EVENT over the position's life is not.
        terminal = not target_offset_valid(offset, seconds_left)
        evidence_ok = bool(path.ts)          # a path was observed at all before termination

        def _label(hit: bool) -> int | None:
            if hit:
                return 1
            return 0 if evidence_ok else None

        row[f"label_break_even_by_{offset}s"] = _label(
            any(value > 0.0 for value in observed)
        )
        row[f"label_target_3c_by_{offset}s"] = _label(
            any(value >= 0.03 for value in observed)
        )
        row[f"label_stop_3c_by_{offset}s"] = _label(
            any(value <= -0.03 for value in observed)
        )
        # Records that the horizon outran the contract. The LABEL is still a real 0/1; this flag
        # exists so an analyst can stratify on it, not so anyone can drop the row.
        row[f"horizon_terminated_early_{offset}s"] = int(terminal)
    row["label_settlement_win"] = int(settle_value == 1.0)
    row["settlement_net"] = path.settle_net
    row["actual_exit_observations"] = len(path.net)
    current_bid = float(row["own_bid"])
    for offset in FUTURE_OFFSETS_S:
        wanted = float(offset)
        # Past expiry the contract is resolved; a "future share price" there is not a price the
        # position could have been exited at. NULL, never a stale carried-forward quote.
        if not target_offset_valid(offset, seconds_left):
            row[f"share_bid_vwap_{offset}s"] = None
            row[f"share_bid_logit_delta_{offset}s"] = None
            row[f"share_ask_vwap_{offset}s"] = None
            row[f"share_ask_logit_delta_{offset}s"] = None
            continue
        index = next((i for i, elapsed in enumerate(path.ts) if elapsed >= wanted), -1)
        if index < 0 or path.ts[index] - wanted > MAX_FUTURE_OBSERVATION_LAG_S:
            bid = None
        else:
            bid = exit_vwaps[index]
        exit_book = books_by_seq.get(path.seqs[index]) if index >= 0 else None
        ask, ask_filled = ladder_vwap(
            exit_book.asks if exit_book else [],
            requested,
        )
        if ask_filled < requested - 1e-9:
            ask = None
        row[f"share_bid_vwap_{offset}s"] = bid
        row[f"share_bid_logit_delta_{offset}s"] = (
            logit(bid) - logit(current_bid) if bid is not None else None
        )
        row[f"share_ask_vwap_{offset}s"] = ask
        row[f"share_ask_logit_delta_{offset}s"] = (
            logit(ask) - logit(float(row["own_ask"])) if ask is not None else None
        )



def build_dataset(
    *,
    l2_path: Path,
    btc_path: Path,
    settlements_path: Path,
    output_path: Path,
    horizons: tuple[int, ...],
    max_rounds: int = 0,
    batch_size: int = 8,
    hash_source: bool = True,
) -> dict[str, Any]:
    started = time.time()
    if not l2_path.is_file():
        raise FileNotFoundError(l2_path)
    if not btc_path.is_file():
        raise FileNotFoundError(btc_path)
    if not settlements_path.is_file():
        raise FileNotFoundError(settlements_path)
    source_stats = {
        "l2": (l2_path.stat().st_size, l2_path.stat().st_mtime_ns),
        "btc": (btc_path.stat().st_size, btc_path.stat().st_mtime_ns),
        "settlements": (
            settlements_path.stat().st_size,
            settlements_path.stat().st_mtime_ns,
        ),
    }
    conn = duckdb.connect(str(l2_path), read_only=True)
    try:
        rounds = load_rounds(conn, settlements_path, horizons)
        if max_rounds:
            rounds = rounds[: int(max_rounds)]
        btc = load_btc_timelines(btc_path, {str(item["slug"]) for item in rounds})
        matched = [item for item in rounds if str(item["slug"]) in btc]
        print(
            f"[dataset] rounds official={len(rounds):,} btc-matched={len(matched):,} "
            f"horizons={list(horizons)}",
            flush=True,
        )
        output_rows: list[dict[str, Any]] = []
        skip_counts: dict[str, int] = defaultdict(int)
        rounds_used: set[str] = set()
        for batch_start in range(0, len(matched), max(1, int(batch_size))):
            chunk = matched[batch_start : batch_start + max(1, int(batch_size))]
            asset_ids = [
                str(asset)
                for item in chunk
                for asset in (item["up_asset"], item["down_asset"])
            ]
            books_by_asset = load_books(conn, asset_ids)
            for round_data in chunk:
                timeline = btc[str(round_data["slug"])]
                side_books = {
                    "UP": books_by_asset.get(str(round_data["up_asset"])) or [],
                    "DOWN": books_by_asset.get(str(round_data["down_asset"])) or [],
                }
                if not side_books["UP"] or not side_books["DOWN"]:
                    skip_counts["missing_side_books"] += 1
                    continue
                for seconds_left in ENTRY_CHECKPOINTS_S[int(round_data["horizon"])]:
                    decision_s = float(round_data["end_ts"]) - float(seconds_left)
                    decision_ns = int(decision_s * NS)
                    btc_index, btc_row, _ = _btc_at(
                        timeline,
                        decision_s,
                        direction="before",
                        max_lag_s=MAX_BTC_OBSERVATION_AGE_S,
                    )
                    if btc_row is None:
                        skip_counts["missing_fresh_btc"] += 1
                        continue
                    decision_books: dict[str, tuple[int, BookState]] = {}
                    for side in ("UP", "DOWN"):
                        index, book = _book_before(side_books[side], decision_ns)
                        if book is None:
                            continue
                        if (decision_ns - book.recv_ts_ns) / NS > MAX_DECISION_BOOK_AGE_S:
                            continue
                        decision_books[side] = (index, book)
                    if len(decision_books) != 2:
                        skip_counts["missing_fresh_two_sided_decision_book"] += 1
                        continue
                    for side in ("UP", "DOWN"):
                        opposite = "DOWN" if side == "UP" else "UP"
                        own_index, own_book = decision_books[side]
                        _, opp_book = decision_books[opposite]
                        settle_value = float(
                            (side == "UP" and int(round_data["settled_side"]) == 1)
                            or (side == "DOWN" and int(round_data["settled_side"]) == 0)
                        )
                        for quantity in QUANTITIES:
                            row = _candidate_features(
                                round_data=round_data,
                                side=side,
                                quantity=quantity,
                                seconds_left=seconds_left,
                                decision_ns=decision_ns,
                                own_books=side_books[side],
                                own_index=own_index,
                                own_book=own_book,
                                opp_book=opp_book,
                                btc_timeline=timeline,
                                btc_index=btc_index,
                                btc_row=btc_row,
                            )
                            reasons = validate_candidate(row)
                            row["candidate_valid"] = int(not reasons)
                            row["candidate_reasons"] = ",".join(reasons)
                            _attach_btc_targets(
                                row, timeline, decision_s, float(round_data["end_ts"])
                            )
                            _attach_execution_labels(
                                row, side_books[side], settle_value, own_book
                            )
                            output_rows.append(row)
                            rounds_used.add(str(round_data["condition_id"]))
            del books_by_asset
            gc.collect()
            print(
                f"[dataset] rounds {min(batch_start + len(chunk), len(matched)):,}/"
                f"{len(matched):,} rows={len(output_rows):,} elapsed={time.time()-started:.1f}s",
                flush=True,
            )
    finally:
        conn.close()

    if not output_rows:
        raise RuntimeError("no complete-trade rows were produced")
    for label, path in (
        ("l2", l2_path),
        ("btc", btc_path),
        ("settlements", settlements_path),
    ):
        current = (path.stat().st_size, path.stat().st_mtime_ns)
        if current != source_stats[label]:
            raise RuntimeError(
                f"{label} source changed while the immutable dataset was built"
            )
    frame = pd.DataFrame(output_rows)
    for column in FEATURE_COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan
    frame.sort_values(
        ["round_start_ts", "horizon", "seconds_left", "side", "requested_qty"],
        inplace=True,
    )
    l2_hash = _stable_source_hash(
        l2_path,
        source_stats["l2"],
        enabled=hash_source,
    )
    btc_hash = _stable_source_hash(btc_path, source_stats["btc"])
    settlements_hash = _stable_source_hash(
        settlements_path,
        source_stats["settlements"],
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    dataset_sha256 = hash_file(temporary)
    # Remove the old manifest before swapping the dataset. A crash can then only
    # leave an unservable dataset, never a new dataset accepted by a stale manifest.
    output_path.with_suffix(".manifest.json").unlink(missing_ok=True)
    os.replace(temporary, output_path)

    weeks = len(
        {
            time.strftime("%Y-%W", time.gmtime(float(value)))
            for value in frame["round_start_ts"].unique()
        }
    )
    independent_rounds = int(frame["round_id"].nunique())
    # Rows are CANDIDATES; exposures are DECISIONS. Publishing both makes the inflation factor
    # impossible to miss - the July pilot is 395 rounds / ~25k rows, and any metric quoted per row
    # overstates the evidence by that ratio.
    independent_exposures = int(frame["exposure_id"].nunique())
    promotable = (
        independent_rounds >= PROMOTION_GATE["min_independent_rounds"]
        and weeks >= PROMOTION_GATE["min_calendar_weeks"]
    )
    manifest = {
        "manifest_version": 1,
        "dataset_version": CONFIG_VERSION,
        "mode": MODE,
        "status": "PROMOTION_CAPABLE_INPUT" if promotable else "PILOT_ONLY_NOT_PROMOTABLE",
        "created_at": time.time(),
        "rows": int(len(frame)),
        "independent_rounds": independent_rounds,
        "independent_exposures": independent_exposures,
        "candidates_per_exposure": round(len(frame) / max(1, independent_exposures), 2),
        "calendar_weeks": weeks,
        "horizons": sorted(int(value) for value in frame["horizon"].unique()),
        "min_round_start_ts": int(frame["round_start_ts"].min()),
        "max_round_start_ts": int(frame["round_start_ts"].max()),
        "entry_complete_rate": float(frame["entry_complete"].mean()),
        "feature_columns": list(FEATURE_COLUMNS),
        "feature_schema_hash": hash_json(list(FEATURE_COLUMNS)),
        "policy_hash": policy_hash(),
        "code_hash": hash_paths(
            [
                Path(__file__),
                Path(__file__).with_name("trade_schema.py"),
                Path(__file__).with_name("trade_labels.py"),
                BACKEND / "polymarket_fee.py",
                RESEARCH / "executable_fill_engine.py",
                RESEARCH / "executable_surface_config.py",
            ]
        ),
        "dataset_sha256": dataset_sha256,
        "source": {
            "l2_path": str(l2_path),
            "l2_size": l2_path.stat().st_size,
            "l2_mtime_ns": l2_path.stat().st_mtime_ns,
            "l2_sha256": l2_hash,
            "btc_path": str(btc_path),
            "btc_sha256": btc_hash,
            "settlements_path": str(settlements_path),
            "settlements_sha256": settlements_hash,
        },
        "promotion_gate": PROMOTION_GATE,
        "promotable": promotable,
        "skip_counts": dict(skip_counts),
        "elapsed_seconds": round(time.time() - started, 2),
    }
    atomic_write_json(output_path.with_suffix(".manifest.json"), manifest)
    print(
        f"[dataset] wrote {len(frame):,} rows (candidates) / "
        f"{independent_exposures:,} exposures (decisions) / {independent_rounds:,} rounds / "
        f"{weeks} weeks -> {output_path}",
        flush=True,
    )
    print(f"[dataset] status={manifest['status']}", flush=True)
    return manifest


def selftest() -> None:
    assert _safe_div(2, 4) == 0.5
    assert _safe_div(1, 0) == 0.0
    times = np.asarray([1.0, 3.0, 7.0])
    assert _last_index_at_or_before(times, 3.0) == 1
    assert _first_index_at_or_after(times, 3.1) == 2
    print("complete-trade dataset helper self-test: ALL PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--l2-db", type=Path, default=DEFAULT_L2)
    parser.add_argument("--btc-snapshots", type=Path, default=DEFAULT_BTC)
    parser.add_argument("--settlements", type=Path, default=DEFAULT_SETTLEMENTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--horizons", nargs="+", type=int, default=list(HORIZONS))
    parser.add_argument("--max-rounds", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--skip-source-hash", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0
    invalid = sorted(set(args.horizons) - set(HORIZONS))
    if invalid:
        parser.error(f"unsupported horizons: {invalid}")
    build_dataset(
        l2_path=args.l2_db.resolve(),
        btc_path=args.btc_snapshots.resolve(),
        settlements_path=args.settlements.resolve(),
        output_path=args.output.resolve(),
        horizons=tuple(sorted(set(args.horizons))),
        max_rounds=args.max_rounds,
        batch_size=args.batch_size,
        hash_source=not args.skip_source_hash,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
