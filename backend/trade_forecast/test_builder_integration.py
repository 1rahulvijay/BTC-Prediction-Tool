"""Synthetic integration tests that EXECUTE the dataset-builder label path.

WHY THIS FILE EXISTS
    `test_audit_fixes.py` checks rules, constants and pure helpers. It did not call
    `_attach_execution_labels()`, so a `NameError: name 'own_book' is not defined` introduced in
    that function survived a full green suite and would have crashed on the first eligible
    candidate of a multi-hour rebuild. Inspecting rules is not the same as running the code.

    Every test here builds synthetic books and runs the real functions end to end.

    python -m backend.trade_forecast.test_builder_integration
"""
from __future__ import annotations

import sys
from typing import Any

sys.path.insert(0, __file__.rsplit("trade_forecast", 1)[0])
sys.path.insert(0, __file__.rsplit("trade_forecast", 1)[0] + "research")

from executable_fill_engine import BookState               # noqa: E402
from .build_complete_trade_dataset import (                # noqa: E402
    _attach_btc_targets,
    _attach_execution_labels,
)
from .trade_schema import FUTURE_OFFSETS_S, QUOTE_SURVIVAL_TOLERANCE  # noqa: E402

NS = 1_000_000_000
_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _book(seq: int, ts_s: float, bid: float, ask: float, depth: float = 500.0) -> BookState:
    """A synchronized two-sided book with real ladders on both sides."""
    return BookState(
        seq=seq,
        recv_ts_ns=int(ts_s * NS),
        best_bid=bid,
        best_ask=ask,
        best_bid_size=depth,
        best_ask_size=depth,
        spread=round(ask - bid, 6),
        asks=[(ask, depth), (round(ask + 0.01, 4), depth)],
        bids=[(bid, depth), (round(bid - 0.01, 4), depth)],
    )


def _row(decision_s: float, seconds_left: int, qty: float = 5.0) -> dict[str, Any]:
    return {
        "decision_ts_ns": int(decision_s * NS),
        "requested_qty": qty,
        "seconds_left": seconds_left,
        "horizon": 5,
        "own_bid": 0.59,
        "own_ask": 0.60,
        "current_btc": 60_000.0,
        "anchor_price": 59_990.0,
        "settlement_price": 60_020.0,
    }


def _scenario(*, decision_s: float, seconds_left: int, arrival_ask: float, qty: float = 5.0,
              depth: float = 500.0) -> dict[str, Any]:
    """Run the REAL execution-label path over synthetic books."""
    decision_book = _book(1, decision_s, 0.59, 0.60, depth)
    books = [decision_book]
    # Post-latency arrival plus a few later observations for the exit path.
    for i, offset in enumerate((0.5, 1.5, 3.0, 8.0, 20.0), start=2):
        ask = arrival_ask if offset == 0.5 else round(arrival_ask + 0.01, 4)
        books.append(_book(i, decision_s + offset, round(ask - 0.01, 4), ask, depth))
    row = _row(decision_s, seconds_left, qty)
    _attach_execution_labels(row, books, 1.0, decision_book)
    return row


def test_executes_without_crashing() -> None:
    print("B1  the execution-label path RUNS (the NameError regression)")
    try:
        row = _scenario(decision_s=1000.0, seconds_left=120, arrival_ask=0.60)
        chk(True, "_attach_execution_labels() executes end to end")
    except NameError as exc:
        chk(False, f"_attach_execution_labels() raised NameError: {exc}")
        return
    except Exception as exc:                                   # noqa: BLE001
        chk(False, f"_attach_execution_labels() raised {type(exc).__name__}: {exc}")
        return
    chk("decision_ask_vwap" in row, "decision_ask_vwap is populated from the DECISION book")
    chk("entry_quote_survived" in row, "entry_quote_survived is populated")
    chk(row.get("decision_ask_vwap") is not None, "decision VWAP resolved for the requested size")


def test_quote_survival_semantics() -> None:
    print("B2  quote survival reflects the real arrival price")
    unchanged = _scenario(decision_s=1000.0, seconds_left=120, arrival_ask=0.60)
    worse = _scenario(decision_s=1000.0, seconds_left=120, arrival_ask=0.65)
    chk(
        unchanged.get("entry_quote_survived") == 1,
        "an unchanged arrival price survives",
    )
    chk(
        worse.get("entry_quote_survived") == 0,
        "a 5c worse arrival does NOT survive",
    )
    slip = worse.get("entry_vwap_slippage")
    chk(
        slip is not None and slip > QUOTE_SURVIVAL_TOLERANCE,
        f"slippage is measured against the decision VWAP ({slip})",
    )
    chk(worse.get("entry_worse_by_1c") == 1, "entry_worse_by_1c fires on a 5c move")
    chk(worse.get("entry_worse_by_2c") == 1, "entry_worse_by_2c fires on a 5c move")
    chk(unchanged.get("entry_worse_by_1c") == 0, "worse-by flags stay 0 when price holds")


def test_crossing_labels_are_terminal() -> None:
    print("B3  crossing labels are 0/1, never NULL past expiry")
    row = _scenario(decision_s=1000.0, seconds_left=30, arrival_ask=0.60)
    for offset in FUTURE_OFFSETS_S:
        for event in ("break_even", "target_3c", "stop_3c"):
            key = f"label_{event}_by_{offset}s"
            if key not in row:
                continue
            chk(
                row[key] in (0, 1),
                f"{key} is a definite 0/1 at a 30s checkpoint (offset {offset}s)",
            )
            if offset > 30:
                break
        break
    late = [
        row[f"label_target_3c_by_{o}s"]
        for o in FUTURE_OFFSETS_S
        if o > 30 and f"label_target_3c_by_{o}s" in row
    ]
    chk(bool(late), "offsets beyond the round exist in the frozen grid")
    chk(
        all(v in (0, 1) for v in late),
        "post-expiry crossing labels are terminal 0/1, NOT None (no upward selection bias)",
    )
    flags = [
        row[f"horizon_terminated_early_{o}s"]
        for o in FUTURE_OFFSETS_S
        if f"horizon_terminated_early_{o}s" in row
    ]
    chk(bool(flags) and any(flags), "horizon_terminated_early_* records which offsets outran the round")


def test_price_targets_are_null_past_expiry() -> None:
    print("B4  exact future PRICE targets are NULL past expiry")
    import numpy as np
    import pandas as pd

    ts = np.array([1000.0, 1005.0, 1010.0, 1020.0, 1030.0])
    btc = np.array([60_000.0, 60_010.0, 60_020.0, 60_030.0, 60_040.0])
    # `_btc_at` reads the "frame" for the observation row, so the fixture must mirror the real
    # timeline shape rather than only its arrays.
    timeline = {
        "ts": ts,
        "btc": btc,
        "frame": pd.DataFrame({"ts": ts, "btc_price": btc}),
    }
    row = _row(1000.0, seconds_left=30)
    _attach_btc_targets(row, timeline, 1000.0, 1030.0)
    inside = row.get("btc_price_5s")
    beyond = row.get("btc_price_120s")
    chk(beyond is None, "btc_price_120s is NULL at a 30s checkpoint (no such executable price)")
    chk(row.get("btc_delta_120s") is None, "its delta is NULL too, never 0.0")
    chk(inside is not None, "btc_price_5s inside the round is still populated")
    # The distinction that matters: an EVENT over the life is terminal 0/1, a PRICE is undefined.
    chk(
        beyond is None and row.get("btc_price_30s") is not None,
        "the boundary is exact: 30s valid at a 30s checkpoint, 120s not",
    )


def test_partial_fill_is_not_survival() -> None:
    print("B5  a book too thin for the size cannot report survival")
    thin = _scenario(decision_s=1000.0, seconds_left=120, arrival_ask=0.60, qty=5000.0, depth=10.0)
    chk(
        thin.get("entry_complete") in (0, None),
        "a requested size beyond the ladder is not a complete entry",
    )
    chk(
        thin.get("entry_quote_survived") in (0, None),
        "an incomplete entry never counts as a surviving quote",
    )


def run() -> int:
    for test in (
        test_executes_without_crashing,
        test_quote_survival_semantics,
        test_crossing_labels_are_terminal,
        test_price_targets_are_null_past_expiry,
        test_partial_fill_is_not_survival,
    ):
        test()
    print("\nBUILDER INTEGRATION", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    sys.exit(run())
