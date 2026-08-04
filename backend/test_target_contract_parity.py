"""Train and serve must produce the SAME label for the same path. Pins P0-1 and P0-4.

The defect: `build_sequences` labelled first-touch, `PredictionVerifier` graded endpoint. Both
sides now call `target_contract`, and this test asserts the agreement END TO END - through the
real `build_sequences` and the real `_grade`, not through the shared helper alone. A test that
only exercised the helper would pass even if one side stopped calling it.

    python backend/test_target_contract_parity.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import target_contract as tc                                    # noqa: E402
from features import build_sequences, compute_adaptive_threshold_series   # noqa: E402
from prediction_verifier import PredictionVerifier              # noqa: E402

CHECKS = 0
NAMES = {0: tc.DOWN, 1: tc.NEUTRAL, 2: tc.UP}


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def label_via_training(closes, highs, lows, horizon):
    """The label `build_sequences` assigns to the first decision row."""
    n = len(closes)
    _, Y, valid = build_sequences(
        np.zeros((n - 1, 2), dtype=np.float32), np.asarray(closes, dtype=np.float64),
        lookback=5, horizons=[horizon], atr_arr=np.full(n, 1.0),
        highs=np.asarray(highs, dtype=np.float64), lows=np.asarray(lows, dtype=np.float64),
        return_valid_mask=True)
    return int(np.argmax(Y[horizon][0])), bool(valid[horizon][0])


def label_via_serving(entry, bars, threshold, contract=tc.FIRST_TOUCH_TRIPLE_BARRIER_V1):
    verifier = PredictionVerifier.__new__(PredictionVerifier)
    klines = [{"time": (i + 1) * 1000, "high": h, "low": lo, "is_closed": True}
              for i, (h, lo) in enumerate(bars)]
    pred = {"predicted_price": entry, "predicted_at": 0, "verify_at": len(bars) * 1000,
            "neutral_band": threshold, "target_contract": contract}
    return verifier._grade(pred, entry, threshold, klines)


def main() -> int:
    entry, horizon, pad = 100.0, 5, 5      # build_sequences decides at index == lookback

    # The band is ADAPTIVE (ATR-derived, cost-floored). Deriving it rather than assuming a
    # value is the point: production stamps this same number onto the prediction as
    # `neutral_band`, and an assumed threshold would make the two sides disagree for a reason
    # that has nothing to do with the contract. A hardcoded 0.01 here silently tested nothing.
    probe = compute_adaptive_threshold_series(np.full(40, entry), np.full(40, 1.0))
    threshold = float(probe[pad])
    upper, lower = entry * (1 + threshold), entry * (1 - threshold)
    print(f"  adaptive band {threshold:.5f} -> barriers {lower:.4f} / {upper:.4f}")

    n = pad + 1 + horizon + 20

    # ---- THE DEFECT PATH: touch UP first, then settle DOWN --------------------------
    closes = [entry] * (pad + 1) + [99.0] * (n - pad - 1)
    highs = [entry] * (pad + 1) + [upper + 0.02] + [entry - 0.10] * (n - pad - 2)
    lows = [entry] * (pad + 1) + [entry - 0.001] + [99.0] * (n - pad - 2)

    train_class, train_valid = label_via_training(closes, highs, lows, horizon)
    bars = list(zip(highs[pad + 1:pad + 1 + horizon], lows[pad + 1:pad + 1 + horizon]))
    serve_dir, serve_status = label_via_serving(entry, bars, threshold)

    check(NAMES[train_class] == tc.UP,
          "training labels the reversal path UP (upper barrier touched first)")
    check(serve_dir == tc.UP, "serving grades the SAME path UP - the two sides now agree")
    check(serve_status == "GRADED_FIRST_TOUCH", "and it graded under the first-touch contract")
    check(train_valid, "the row is a usable directional label")

    old_answer = tc.label_endpoint(entry, closes[-1], threshold)
    check(old_answer == tc.DOWN,
          "the retired endpoint rule called that same path DOWN - the contradiction this test "
          "exists to prevent recurring")
    check(old_answer != serve_dir,
          "and it DISAGREES with what the model was trained to predict")

    # ---- AMBIGUOUS: excluded on both sides ------------------------------------------
    a_highs = [entry] * (pad + 1) + [upper + 0.02] + [entry] * (n - pad - 2)
    a_lows = [entry] * (pad + 1) + [lower - 0.02] + [entry] * (n - pad - 2)
    _cls, a_valid = label_via_training([entry] * n, a_highs, a_lows, horizon)
    check(not a_valid, "training marks the double-touch row AMBIGUOUS and excludes it")
    _dir, a_status = label_via_serving(entry, [(upper + 0.02, lower - 0.02)], threshold)
    check(_dir is None and a_status == "GRADE_UNAVAILABLE:ambiguous_bar",
          "serving REFUSES to grade it rather than manufacturing a hit or a miss")

    # ---- REFUSALS -------------------------------------------------------------------
    d, s = label_via_serving(entry, [(entry, entry)], threshold, contract="not_a_contract")
    check(d is None and s.startswith("UNKNOWN_CONTRACT"),
          "an unknown contract is refused, never defaulted to a rule")

    verifier = PredictionVerifier.__new__(PredictionVerifier)
    d, s = verifier._grade(
        {"predicted_price": entry, "predicted_at": 0, "verify_at": 5000,
         "neutral_band": threshold, "target_contract": tc.FIRST_TOUCH_TRIPLE_BARRIER_V1},
        98.0, threshold, None)
    check(d is None and s == "GRADE_UNAVAILABLE:no_intrabar_path",
          "first-touch without the intrabar path is refused, not graded on the endpoint")

    # ---- A QUIET PATH STILL AGREES --------------------------------------------------
    inside_hi = entry + (upper - entry) / 2
    inside_lo = entry - (entry - lower) / 2
    q_cls, q_valid = label_via_training(
        [entry] * n, [inside_hi] * n, [inside_lo] * n, horizon)
    q_dir, _ = label_via_serving(entry, [(inside_hi, inside_lo)] * horizon, threshold)
    check(NAMES[q_cls] == tc.NEUTRAL and q_dir == tc.NEUTRAL and q_valid,
          "a quiet path is NEUTRAL on both sides - agreement is not achieved by refusing "
          "everything")

    print(f"\nTARGET CONTRACT PARITY: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
