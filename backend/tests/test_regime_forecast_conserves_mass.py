"""A regime forecast must conserve probability mass across duplicate state labels.

WHAT WAS WRONG
    `state_labels` is many-to-one: several hidden HMM states can carry the same public regime,
    and two RANGE states is ordinary after a refit. `forecast_transitions` did:

        dist[label] = round(float(future_belief[s]), 4)      # ASSIGN

    so the last state with a given label overwrote every earlier one. With

        hidden 0 -> RANGE = 0.18
        hidden 2 -> RANGE = 0.31

    P(RANGE) was reported as 0.31 rather than 0.49, and the returned distribution summed to
    well under 1. A forecast that silently discards probability mass still feeds regime
    routing and strategy selection.

    `get_confidence_vector()` sixteen lines below already accumulated correctly - the two
    functions disagreed about the same arithmetic over the same map, which is why this
    survived: the correct version was right there to be read.

    python backend/tests/test_regime_forecast_conserves_mass.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import sys
from pathlib import Path

import numpy as np

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, f"FAILED: {text}"
    CHECKS += 1
    print(f"  PASS  {text}")


def main() -> int:
    import regime as rg

    cls = None
    for name in dir(rg):
        obj = getattr(rg, name)
        if isinstance(obj, type) and hasattr(obj, "forecast_transitions"):
            cls = obj
            break
    assert cls is not None, "no class exposing forecast_transitions"

    det = cls.__new__(cls)               # bypass __init__: this is a pure-arithmetic test
    det.hmm_ready = True
    # FIVE hidden states, TWO of them labelled RANGE. That duplication is the whole defect;
    # a fixture with distinct labels passes either implementation.
    det.state_labels = {0: "RANGE", 1: "HIGH_VOL", 2: "RANGE", 3: "TREND_UP", 4: "TREND_DOWN"}
    det._belief = np.array([0.18, 0.11, 0.31, 0.22, 0.18])
    det._transmat = np.eye(5)            # identity: belief is carried through unchanged
    det.current_regime = "RANGE"

    out = det.forecast_transitions(steps=[5])
    check(out.get("available") is True, "the forecast is available for this fixture")
    dist = out["forecasts"]["5bar"]

    total = sum(dist.values())
    check(abs(total - 1.0) < 1e-3,
          f"the distribution sums to 1 ({total:.4f}) - it summed to less whenever two hidden "
          f"states shared a label, because the second assignment discarded the first")

    check(abs(dist["RANGE"] - (0.18 + 0.31)) < 1e-3,
          f"P(RANGE) is the SUM of both RANGE states ({dist['RANGE']:.4f} = 0.18 + 0.31), not "
          f"whichever one happened to be written last")
    check(dist["RANGE"] > dist["TREND_UP"],
          "and RANGE therefore outranks TREND_UP (0.49 vs 0.22) - under the old code it "
          "reported 0.31 and the ranking of the two regimes could invert")

    check(set(dist) == {"RANGE", "HIGH_VOL", "TREND_UP", "TREND_DOWN"},
          "the four distinct public regimes each appear once")

    # Agreement with the function that was already correct.
    vec = det.get_confidence_vector()
    check(abs(vec["RANGE"] - dist["RANGE"]) < 1e-3,
          "and under an identity transition matrix the forecast agrees with "
          "get_confidence_vector() - the two no longer disagree about the same map")

    # Distinct labels must still behave: this is not a fix that merely sums everything.
    det.state_labels = {0: "RANGE", 1: "HIGH_VOL", 2: "TREND_UP"}
    det._belief = np.array([0.5, 0.3, 0.2])
    det._transmat = np.eye(3)
    d2 = det.forecast_transitions(steps=[5])["forecasts"]["5bar"]
    check(abs(d2["RANGE"] - 0.5) < 1e-3 and abs(sum(d2.values()) - 1.0) < 1e-3,
          "with no duplicate labels each regime keeps its own mass - the aggregation adds "
          "only what belongs together")

    # INCOMPLETE LABEL MAP. If a hidden state carries no public regime, aggregation alone
    # sums to less than 1 and the result is not a distribution. This is the case that makes
    # the renormalisation load-bearing rather than a no-op: with a fully-labelled map the
    # belief is already normalised upstream, so removing it changes nothing observable, and
    # a mutation deleting it survived until this fixture existed.
    #
    # Redistributing the unlabelled mass proportionally is the deliberate choice: a caller
    # routing on these numbers needs a distribution over the regimes it can ACT on, not one
    # that quietly sums to 0.8.
    det.state_labels = {0: "RANGE", 1: "HIGH_VOL"}          # state 2 has no public label
    det._belief = np.array([0.5, 0.3, 0.2])
    det._transmat = np.eye(3)
    d3 = det.forecast_transitions(steps=[5])["forecasts"]["5bar"]
    check(abs(sum(d3.values()) - 1.0) < 1e-3,
          f"with an unlabelled hidden state the result is STILL a distribution "
          f"({sum(d3.values()):.4f}) - aggregation alone would have returned 0.8")
    check(abs(d3["RANGE"] / d3["HIGH_VOL"] - (0.5 / 0.3)) < 1e-2,
          "and the labelled regimes keep their RELATIVE weights (0.5:0.3) - normalising "
          "rescales, it does not reorder")

    print(f"\nREGIME FORECAST CONSERVES MASS: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
