"""P0-8: the backtest must grade the contract the model was TRAINED on.

THE DEFECT
    backtester.py contained ZERO references to target_contract. It computed

        actual_ret = (future - current) / current      -> banded endpoint direction

    while the ensemble trains on FIRST_TOUCH_TRIPLE_BARRIER_V1. Different random variables,
    so every backtest number described a different model answering a different question. A
    good result was edge that did not exist; a bad one could discard a sound model. Neither
    announces itself.

    run() had ALWAYS accepted highs/lows. The intrabar path was passed and ignored, so first
    touch was gradeable the whole time.

WHY THE HEADLINE CHECK IS A DIFFERENCE
    Asserting "it refuses bad input" leaves the actual grading untested - a mutant that graded
    by endpoint sign regardless of contract passed a refusal-only probe. The load-bearing
    assertion is that the SAME data under the two contracts yields DIFFERENT results. If the
    contract were ignored they would be identical.

    python backend/tests/test_backtest_target_contract.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import logging
import sys
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
logging.disable(logging.CRITICAL)

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def main() -> int:
    import target_contract as tc
    from backtester import Backtester

    rng = np.random.default_rng(3)
    n = 1200
    closes = 100 * np.cumprod(1 + rng.normal(0, 0.0012, n))
    # Wide intrabar excursions, so first touch and endpoint genuinely disagree on many rows.
    highs = closes * (1 + np.abs(rng.normal(0, 0.0030, n)))
    lows = closes * (1 - np.abs(rng.normal(0, 0.0030, n)))
    feats = rng.normal(0, 1, (n, 4)).astype(np.float32)

    def predict(seq, h):
        return (0.34, 0.33, 0.33)

    # ---- the headline: the contract CHANGES the answer --------------------------------
    path = Backtester().run(feats, closes, [5], predict, highs=highs, lows=lows,
                            target_contract=tc.FIRST_TOUCH_TRIPLE_BARRIER_V1)
    endp = Backtester().run(feats, closes, [5], predict, highs=highs, lows=lows,
                            target_contract=tc.ENDPOINT_SETTLEMENT_V1)
    cm_path = path[5].get("confusion_matrix")
    cm_endp = endp[5].get("confusion_matrix")
    check(cm_path and cm_endp, "both contracts produce a graded result")
    check(cm_path != cm_endp,
          "the SAME data graded under the two contracts gives DIFFERENT confusion matrices - "
          "if the backtest ignored the contract these would be identical, which is exactly "
          "what it did before")

    # ---- refusals, so a wrong grade is never substituted for a missing one -------------
    try:
        Backtester().run(feats, closes, [5], predict)      # fabricates bars internally
        raise AssertionError("first touch was graded on fabricated bars")
    except ValueError as exc:
        check("PATH contract" in str(exc) or "REAL" in str(exc),
              "grading a PATH contract on FABRICATED bars RAISES - the fallback invents a "
              "0.2% range, and inventing barriers is as wrong as ignoring them")

    try:
        Backtester().run(feats, closes, [5], predict, target_contract="made_up_contract")
        raise AssertionError("an unknown contract was graded")
    except ValueError:
        global CHECKS
        CHECKS += 1
        print("  PASS  and an unknown contract RAISES rather than defaulting to a rule the "
              "model was not trained under")

    # The endpoint contract needs no path, so it must still work without real bars.
    endp_nopath = Backtester().run(feats, closes, [5], predict,
                                   target_contract=tc.ENDPOINT_SETTLEMENT_V1)
    check(endp_nopath[5].get("confusion_matrix") is not None,
          "while an ENDPOINT contract still grades without an intrabar path, because it does "
          "not need one - the refusal is targeted, not blanket")

    # ---- the default must be the training contract ------------------------------------
    default = Backtester().run(feats, closes, [5], predict, highs=highs, lows=lows)
    check(default[5]["confusion_matrix"] == cm_path,
          "the DEFAULT grading equals the first-touch result, so a caller that passes no "
          "contract gets the one the ensemble actually trains on")

    print(f"\nBACKTEST TARGET CONTRACT: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
