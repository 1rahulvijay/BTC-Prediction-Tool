"""Train and serve must produce the SAME label for the same path. Pins P0-1 and P0-4.

The defect: `build_sequences` labelled first-touch, `PredictionVerifier` graded endpoint. Both
sides now call `target_contract`, and this test asserts the agreement END TO END - through the
real `build_sequences` and the real `_grade`, not through the shared helper alone. A test that
only exercised the helper would pass even if one side stopped calling it.

    python backend/tests/test_target_contract_parity.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
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


#: A real epoch-millisecond base. The fixtures used to count from 0 in units of 1000, which
#: is neither seconds nor milliseconds - and a fake unit cannot expose a unit mismatch.
BASE_MS = 1_785_000_000_000


def label_via_serving(entry, bars, threshold, contract=tc.FIRST_TOUCH_TRIPLE_BARRIER_V1):
    verifier = PredictionVerifier.__new__(PredictionVerifier)
    klines = [{"time": BASE_MS + (i + 1) * 60_000, "high": h, "low": lo, "close": entry,
               "is_closed": True} for i, (h, lo) in enumerate(bars)]
    pred = {"predicted_price": entry, "predicted_at": BASE_MS,
            "verify_at": BASE_MS + len(bars) * 60_000,
            "neutral_band": threshold, "target_contract": contract}
    # P1-1: _grade returns a GradeResult, so the resolution PRICE and TIMESTAMP travel with
    # the direction instead of being re-derived by each caller from whatever it had to hand.
    result = verifier._grade(pred, entry, threshold, klines)
    return result.direction, result.status


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
    _r = verifier._grade(
        {"predicted_price": entry, "predicted_at": BASE_MS,
         "verify_at": BASE_MS + 300_000, "neutral_band": threshold,
         "target_contract": tc.FIRST_TOUCH_TRIPLE_BARRIER_V1},
        98.0, threshold, None)
    d, s = _r.direction, _r.status
    check(d is None and s == "GRADE_UNAVAILABLE:no_intrabar_path",
          "first-touch without the intrabar path is refused, not graded on the endpoint")

    # ---- P0-11: ENDPOINT GRADES AT THE HORIZON END, NOT AT LOOP TIME ----------------
    # The bar at verify_at closed DOWN; by the time the loop resolved it, price had run up.
    # Grading from `current_price` would call this UP. It must follow the as-of close.
    late_klines = [
        {"time": BASE_MS + 60_000, "high": entry, "low": entry, "close": entry,
         "is_closed": True},
        {"time": BASE_MS + 300_000, "high": entry, "low": lower - 1.0, "close": lower - 1.0,
         "is_closed": True},                      # the horizon-end bar: clearly DOWN
        {"time": BASE_MS + 540_000, "high": upper + 5.0, "low": entry, "close": upper + 5.0,
         "is_closed": True},                      # AFTER verify_at - must be ignored
    ]
    endpoint_pred = {"predicted_price": entry, "predicted_at": BASE_MS,
                     "verify_at": BASE_MS + 300_000, "neutral_band": threshold,
                     "target_contract": tc.ENDPOINT_SETTLEMENT_V1}
    _r = verifier._grade(endpoint_pred, upper + 5.0, threshold, late_klines)
    late_dir, late_status = _r.direction, _r.status
    check(late_dir == tc.DOWN and late_status == "GRADED_ENDPOINT",
          "a LATE resolution grades from the bar at the horizon end, not from the price the "
          "loop happened to see - the P0-11 defect")
    check(endpoint_pred.get("resolution_event_ts") == BASE_MS + 300_000,
          "and it records WHICH event it resolved against")
    check(tc.label_endpoint(entry, upper + 5.0, threshold) == tc.UP,
          "loop-time price would have said UP - so the two genuinely differ here")

    # ---- A QUIET PATH STILL AGREES --------------------------------------------------
    inside_hi = entry + (upper - entry) / 2
    inside_lo = entry - (entry - lower) / 2
    q_cls, q_valid = label_via_training(
        [entry] * n, [inside_hi] * n, [inside_lo] * n, horizon)
    q_dir, _ = label_via_serving(entry, [(inside_hi, inside_lo)] * horizon, threshold)
    check(NAMES[q_cls] == tc.NEUTRAL and q_dir == tc.NEUTRAL and q_valid,
          "a quiet path is NEUTRAL on both sides - agreement is not achieved by refusing "
          "everything")

    # ---- THE HEAD SPLIT, END TO END --------------------------------------------------
    # The SAME row must produce different labels for the two heads on a path where the
    # contracts disagree - otherwise the "split" is a naming exercise.
    _X, Ypath, Vpath, Ysettle = build_sequences(
        np.zeros((n - 1, 2), dtype=np.float32), np.asarray(closes, dtype=np.float64),
        lookback=5, horizons=[horizon], atr_arr=np.full(n, 1.0),
        highs=np.asarray(highs, dtype=np.float64), lows=np.asarray(lows, dtype=np.float64),
        return_valid_mask=True, return_settlement_labels=True)
    Ybanded = Ysettle[tc.ENDPOINT_SETTLEMENT_V1]
    Ybinary = Ysettle[tc.ROLLING_EXCHANGE_RETURN_SIGN_V1]
    path_label = NAMES[int(np.argmax(Ypath[horizon][0]))]
    settle_label = NAMES[int(np.argmax(Ybanded[horizon][0]))]
    binary_label = tc.BINARY_CLASS_ORDER[int(np.argmax(Ybinary[horizon][0]))]
    check(path_label == tc.UP and settle_label == tc.DOWN,
          f"one row, two heads: PATH says {path_label} and SETTLEMENT says {settle_label} - "
          f"the split produces genuinely different training targets")
    check(binary_label == tc.DOWN,
          "and the BINARY settlement label agrees with the banded one on this row, so the "
          "third contract is not simply inverting the second")
    check(Ybanded[horizon].shape == Ypath[horizon].shape,
          "the banded settlement labels are shaped like the path labels, so a trainer can "
          "consume either without reshaping")
    check(Ybinary[horizon].shape == (len(Ypath[horizon]), 2),
          "while the binary labels have TWO columns - the class count is the contract, and a "
          "third column would be a NEUTRAL outcome the venue never pays")
    check(bool(np.all(Ybanded[horizon].sum(axis=1) == 1.0))
          and bool(np.all(Ybinary[horizon].sum(axis=1) == 1.0)),
          "every settlement row is one-hot under BOTH contracts - endpoint direction has no "
          "ambiguous case, unlike first-touch where one bar can touch both barriers")
    check(tc.assert_admissible(tc.STOP_TARGET_PLANNING, tc.TRAINING_CONTRACT)
          == tc.TRAINING_CONTRACT,
          "the head that DOES exist is admissible for the path questions it answers")

    # ---- PRODUCTION TIMESTAMP UNITS --------------------------------------------------
    # The fixtures above build klines and prediction bounds in ONE artificial unit, so they
    # could never catch a unit mismatch. Production does not: data_ingestion stores
    # `"time": k["t"] // 1000` (SECONDS) while the verifier builds verify_at from now_ms
    # (MILLISECONDS). Comparing them raw made every first-touch path empty and every endpoint
    # grade select the newest bar instead of the horizon bar.
    ingestion_src = (Path(__file__).resolve().parents[1] / "data_ingestion.py").read_text(
        encoding="utf-8")
    check('"time": k["t"] // 1000' in ingestion_src,
          "production really does emit kline time in SECONDS - asserted against the ingestion "
          "source so this test fails if that contract changes")

    now_ms = 1_785_000_000_000
    entry_price = 100.0
    # Klines exactly as ingestion emits them: SECONDS, and real epoch magnitudes.
    seconds_klines = [
        {"time": (now_ms + i * 60_000) // 1000, "high": entry_price, "low": entry_price,
         "close": entry_price, "is_closed": True} for i in range(1, 4)
    ]
    seconds_klines[0]["high"] = entry_price * (1 + threshold) + 0.5      # touch UP first
    seconds_klines[-1]["close"] = entry_price * 0.98                     # settle DOWN

    prod_pred = {"predicted_price": entry_price, "predicted_at": now_ms,
                 "verify_at": now_ms + 5 * 60_000, "neutral_band": threshold,
                 "target_contract": tc.FIRST_TOUCH_TRIPLE_BARRIER_V1}
    _r = verifier._grade(prod_pred, entry_price, threshold, seconds_klines)
    direction, status = _r.direction, _r.status
    check(status == "GRADED_FIRST_TOUCH",
          f"a SECONDS-valued production kline now grades first-touch (got {status}) - before "
          f"normalisation the path was always empty and every grade returned "
          f"GRADE_UNAVAILABLE, then aged out as INVALID_LATE")
    check(direction == tc.UP, "and it finds the upper barrier touched first")

    endpoint_pred = dict(prod_pred, target_contract=tc.ENDPOINT_SETTLEMENT_V1)
    _r = verifier._grade(endpoint_pred, entry_price, threshold, seconds_klines)
    end_dir, end_status = _r.direction, _r.status
    check(end_status == "GRADED_ENDPOINT" and end_dir == tc.DOWN,
          "endpoint grading resolves from the bar at the horizon, not the newest one")
    check(endpoint_pred["resolution_event_ts"] >= 1_577_836_800_000,
          "and the recorded resolution timestamp is in MILLISECONDS, not raw seconds")

    check(tc.kline_open_ms({"time": 1_785_000_000}) == 1_785_000_000_000,
          "seconds are promoted to milliseconds")
    check(tc.kline_open_ms({"time": 1_785_000_000_000}) == 1_785_000_000_000,
          "milliseconds pass through unchanged, so a normalised feed is not double-scaled")

    print(f"\nTARGET CONTRACT PARITY: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
