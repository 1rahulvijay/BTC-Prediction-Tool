"""P1-1: one graded row must describe ONE moment.

    python backend/test_resolution_observation_consistency.py

THE DEFECT
    `_grade` resolved direction from the horizon-end bar (or the first-touch path), and then
    every magnitude field beside it was computed from `current_price` - the MAIN LOOP's price
    at whatever instant it reached that line:

        actual_move_usd  = current_price - predicted_price
        target_error_usd = current_price - target_price
        actual_price     = current_price
        _actual_strict   = "UP" if current_price >= predicted_price else "DOWN"

    So a single row could carry `actual_direction = DOWN`, decided at the horizon boundary,
    next to a POSITIVE `actual_move_usd` measured twenty seconds later. That fed magnitude
    error, target error, expectancy, lean-hit, the calibration labels and - through
    `_actual_strict` - the LEARNED REGIME WEIGHTS, which decide how much each model seat is
    trusted per regime.

THE FIXTURE
    Price dips through the lower barrier in the FIRST bar, then rallies hard and stays up. So:
        first touch (the trained contract) -> DOWN, resolving in bar 1
        last bar of the window             -> far ABOVE entry
        loop-time price                    -> far ABOVE entry
    Any of the three wrong choices produces a positive move against a DOWN direction, which is
    exactly the incoherence being tested. A path where they agreed would pass against the bug.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import target_contract as tc                                # noqa: E402
from prediction_verifier import PredictionVerifier          # noqa: E402

_OK = True
BASE_MS = 1_785_000_000_000
ENTRY = 100.0
BAND = 0.01                     # +/-1% -> barriers at 99.0 / 101.0


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _klines():
    """Bar 1 pierces 99.0 and closes at 98.5; bars 2-5 rally to ~110. SECONDS, as production."""
    specs = [
        (100.2, 98.40, 98.50),      # <- resolving bar: lower barrier touched FIRST
        (105.0, 98.60, 104.0),
        (108.0, 103.5, 107.5),
        (111.0, 107.0, 110.0),
        (112.0, 109.5, 111.5),      # last bar of the window: far above entry
    ]
    return [{"time": (BASE_MS + (i + 1) * 60_000) // 1000,      # SECONDS on purpose
             "high": h, "low": lo, "close": c, "is_closed": True}
            for i, (h, lo, c) in enumerate(specs)]


def main() -> int:
    klines = _klines()
    verify_at = BASE_MS + 300_000
    loop_price = 111.5              # what the main loop would have seen

    print("the fixture is actually adversarial")
    chk(tc.label_first_touch(ENTRY, [k["high"] for k in klines],
                             [k["low"] for k in klines], BAND) == tc.DOWN,
        "first touch is DOWN")
    chk(klines[-1]["close"] > ENTRY and loop_price > ENTRY,
        "while both the window's last close and the loop price are ABOVE entry - so any "
        "wrong observation yields a positive move against a DOWN call")

    print("the grader resolves at the bar that DECIDED the label")
    result = tc.grade(contract=tc.TRAINING_CONTRACT, entry=ENTRY, threshold=BAND,
                      klines=klines, entry_ts=BASE_MS, verify_ts=verify_at)
    chk(result.direction == tc.DOWN and result.status == "GRADED_FIRST_TOUCH",
        "direction is DOWN under the trained contract")
    chk(result.resolution_price == 98.50,
        f"resolution price is the TOUCHING bar's close (got {result.resolution_price}), not "
        f"the window's last close 111.5")
    chk(result.resolution_event_ts == BASE_MS + 60_000,
        "and the resolution timestamp is that same bar")
    chk(result.resolution_basis == "first_touch_bar_close",
        "the row records WHICH observation this is")

    print("the resolving bar is located, not assumed to be the first or the last")
    # The fixture above touches on bar 0, where "index 0", "first bar" and several off-by-one
    # errors all coincide. This one touches on bar 2, so the index has to be genuinely correct.
    mid_specs = [
        (100.5, 99.60, 100.2),      # bar 0: no touch
        (100.8, 99.20, 100.4),      # bar 1: no touch (99.2 > 99.0)
        (100.9, 98.70, 98.90),      # bar 2: TOUCHES the lower barrier
        (106.0, 98.80, 105.0),      # bars 3-4 rally away
        (112.0, 104.0, 111.0),
    ]
    mid = [{"time": (BASE_MS + (i + 1) * 60_000) // 1000,
            "high": h, "low": lo, "close": c, "is_closed": True}
           for i, (h, lo, c) in enumerate(mid_specs)]
    mid_result = tc.grade(contract=tc.TRAINING_CONTRACT, entry=ENTRY, threshold=BAND,
                          klines=mid, entry_ts=BASE_MS, verify_ts=verify_at)
    chk(mid_result.direction == tc.DOWN, "a mid-window touch still grades DOWN")
    chk(mid_result.resolution_price == 98.90,
        f"and resolves at BAR 2's close 98.90 (got {mid_result.resolution_price}) - not bar 0, "
        f"not the last bar")
    chk(mid_result.resolution_event_ts == BASE_MS + 3 * 60_000,
        "with bar 2's timestamp, so an off-by-one in the index cannot hide")
    outcome, idx = tc.first_touch_at(ENTRY, [b["high"] for b in mid],
                                     [b["low"] for b in mid], BAND)
    chk(outcome == tc.DOWN and idx == 2,
        f"first_touch_at reports the resolving index directly (got {idx}, expected 2)")

    print("a NEUTRAL timeout resolves at the horizon end, because expiry IS the event")
    flat = [{"time": (BASE_MS + (i + 1) * 60_000) // 1000,
             "high": 100.1, "low": 99.9, "close": 100.0 + i * 0.01, "is_closed": True}
            for i in range(5)]
    timeout = tc.grade(contract=tc.TRAINING_CONTRACT, entry=ENTRY, threshold=BAND,
                       klines=flat, entry_ts=BASE_MS, verify_ts=verify_at)
    chk(timeout.direction == tc.NEUTRAL, "neither barrier reached -> NEUTRAL")
    chk(timeout.resolution_event_ts == BASE_MS + 300_000,
        "and it resolves at the LAST bar - the horizon expiring is what decided it")

    print("the verified row is internally coherent")
    v = PredictionVerifier()
    v.pending_predictions.append({
        "id": "p1", "horizon": 5, "predicted_price": ENTRY, "target_price": 102.0,
        "direction": "DOWN", "raw_direction": "DOWN", "predicted_at": BASE_MS,
        "verify_at": verify_at, "neutral_band": BAND,
        "target_contract": tc.TRAINING_CONTRACT,
        "model_dirs": {"xgb": 0, "lgb": 2},          # xgb votes DOWN, lgb votes UP
        "regime": "RANGE", "confidence": 0.7,
    })
    done = v.check_and_verify(loop_price, verify_at + 1_000, klines=klines)
    chk(len(done) == 1, "the row resolved")
    row = done[0]

    chk(row["actual_direction"] == tc.DOWN, "direction DOWN")
    chk(row["actual_price"] == 98.50,
        f"actual_price is the resolution observation (got {row['actual_price']}), not the "
        f"loop price {loop_price}")
    chk(row["actual_move_usd"] < 0,
        f"actual_move_usd is NEGATIVE ({row['actual_move_usd']}) and so AGREES with the DOWN "
        f"direction - the defect produced +11.50 here")
    chk(abs(row["actual_move_usd"] - (98.50 - ENTRY)) < 1e-6,
        "and it is measured from that same price")
    chk(abs(row["target_error_usd"] - (98.50 - 102.0)) < 1e-6,
        "target error uses the same observation too")
    chk(row["lean_hit"] is True,
        "lean_hit agrees: a DOWN lean on a DOWN resolution is a hit - under the loop price it "
        "was scored a MISS")
    chk(row["resolution_basis"] == "first_touch_bar_close"
        and row["resolution_event_ts"] == BASE_MS + 60_000,
        "the basis and event timestamp are stored on the row")
    chk(row["loop_price_at_verification"] == loop_price,
        "the loop price is still RECORDED - discarding it would hide the discrepancy rather "
        "than fix it")
    chk(row["actual_change_pct"] < 0,
        "the reported percentage change carries the same sign as the direction")

    print("the learned regime weights use the graded outcome")
    chk(list(v.regime_model_stats["RANGE"]["xgb"]) == [1],
        "a DOWN vote on a DOWN resolution is credited - the loop-time sign scored it a miss")
    chk(list(v.regime_model_stats["RANGE"]["lgb"]) == [0],
        "and an UP vote is not")

    print("the loop price cannot influence the row at all")
    v2 = PredictionVerifier()
    v2.pending_predictions.append({
        "id": "p2", "horizon": 5, "predicted_price": ENTRY, "target_price": 102.0,
        "direction": "DOWN", "raw_direction": "DOWN", "predicted_at": BASE_MS,
        "verify_at": verify_at, "neutral_band": BAND,
        "target_contract": tc.TRAINING_CONTRACT, "model_dirs": {}, "regime": "RANGE",
        "confidence": 0.7,
    })
    absurd = v2.check_and_verify(9_999_999.0, verify_at + 1_000, klines=klines)[0]
    for field in ("actual_price", "actual_move_usd", "target_error_usd", "actual_change_pct",
                  "actual_direction", "hit", "lean_hit"):
        chk(absurd[field] == row[field],
            f"{field} is identical under an absurd loop price - it is no longer an input")

    print("\nRESOLUTION OBSERVATION CONSISTENCY:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
