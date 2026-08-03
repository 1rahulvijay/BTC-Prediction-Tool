"""PHASE5C_89 - WHY does the market beat calibrated P(hold)? Decompose the Brier score.

THE QUESTION
    Two independent studies now agree the market's quoted ask is the better probability. This
    asks which component of the model is at fault, using Murphy's decomposition:

        Brier = reliability - resolution + uncertainty

        reliability   miscalibration. Lower is better. Fixable by recalibrating.
        resolution    discrimination. HIGHER is better. Not fixable by recalibrating - it is
                      the information content, and a model that lacks it lacks a signal.
        uncertainty   the base rate's own variance. Identical for every forecaster on the same
                      sample, so it cannot explain any difference between them.

    The diagnosis matters because the two failures have opposite remedies. If P(hold) is merely
    miscalibrated, an isotonic map fixes it. If it lacks resolution, no calibration will help
    and the model needs different information - or should be replaced by the market price.

DESCRIPTIVE ONLY
    21 days supports no effect below ~25 points. This reports the decomposition and never
    claims a difference is significant.

    python research/phase5c/test_brier_decomposition_market_vs_model.py
"""
from __future__ import annotations

RESEARCH_STATUS = "VALID_DIAGNOSTIC"
CAPITAL_AUTHORITY = False

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    assert_descriptive_only, load_checkpoints, murphy_decomposition, side_ask,
)

TIME_BUCKETS = ((0, 30, "T-30s and later"), (30, 90, "T-90s to T-30s"),
                (90, 300, "T-5m to T-90s"), (300, 10_000, "before T-5m"))


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    outcomes = np.array([1.0] * 500 + [0.0] * 500)
    perfect = np.where(outcomes == 1, 0.99, 0.01)
    constant = np.full(1000, 0.5)
    biased = np.clip(perfect + 0.25, 0, 1)

    good = murphy_decomposition(perfect, outcomes)
    flat = murphy_decomposition(constant, outcomes)
    shifted = murphy_decomposition(biased, outcomes)

    check(good["resolution"] > flat["resolution"],
          "a discriminating forecast has HIGHER resolution than a constant one")
    check(abs(flat["resolution"]) < 1e-9,
          "a constant forecast has exactly zero resolution - it separates nothing")
    check(shifted["reliability"] > good["reliability"],
          "adding a constant bias raises RELIABILITY error while leaving information intact")
    check(abs(shifted["resolution"] - good["resolution"]) < 0.02,
          "a pure bias barely touches resolution - which is why calibration can fix it")
    check(abs(good["brier"] - (good["reliability"] - good["resolution"]
                               + good["uncertainty"])) < 1e-9,
          "the decomposition adds back to the Brier score exactly")
    check(abs(flat["uncertainty"] - 0.25) < 1e-9,
          "uncertainty is the base rate variance, identical across forecasters")
    try:
        assert_descriptive_only(3.0)
        check(False, "unreachable")
    except ValueError:
        check(True, "a significance claim below the window's MDE is REFUSED at runtime")

    print(f"\nBRIER DECOMPOSITION SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    print("=" * 96)
    print("PHASE5C-89  BRIER DECOMPOSITION - which component makes P(hold) lose to the market?")
    print("=" * 96)
    frame = load_checkpoints()
    if frame.empty:
        print("  BLOCKED: no eligible settled checkpoints.")
        return 0
    print(f"  {assert_descriptive_only()}")

    won = frame["won"].to_numpy(float)
    ask = np.clip(side_ask(frame), 1e-6, 1 - 1e-6)
    model = np.clip(frame["p_hold_cur"].to_numpy(float), 1e-6, 1 - 1e-6)
    seconds = frame["checkpoint_s"].to_numpy(float)

    print(f"  rows {len(frame):,} | base rate {won.mean():.4f}")
    print()
    print(f"{'bucket':<20}{'forecaster':<10}{'Brier':>9}{'reliab':>9}{'resol':>9}{'uncert':>9}")
    for low, high, label in (("", "", "ALL"),) if False else \
            [(l, h, n) for l, h, n in TIME_BUCKETS] + [(0, 10_000, "ALL")]:
        mask = (seconds >= low) & (seconds < high)
        if mask.sum() < 200:
            continue
        for name, values in (("MARKET", ask), ("P(hold)", model)):
            part = murphy_decomposition(values[mask], won[mask])
            print(f"{label:<20}{name:<10}{part['brier']:>9.4f}{part['reliability']:>9.4f}"
                  f"{part['resolution']:>9.4f}{part['uncertainty']:>9.4f}")

    overall_market = murphy_decomposition(ask, won)
    overall_model = murphy_decomposition(model, won)
    print()
    reliability_gap = overall_model["reliability"] - overall_market["reliability"]
    resolution_gap = overall_market["resolution"] - overall_model["resolution"]
    print(f"  P(hold) loses {overall_model['brier'] - overall_market['brier']:+.4f} of Brier.")
    print(f"    from worse CALIBRATION (reliability) : {reliability_gap:+.4f}")
    print(f"    from worse DISCRIMINATION (resolution): {resolution_gap:+.4f}")
    print()
    if resolution_gap > reliability_gap:
        print("  Dominated by RESOLUTION. Recalibration cannot fix this - the model carries")
        print("  less information than the price. That is a signal problem, not a mapping one,")
        print("  and it is why the market-prior residual is the only supported direction.")
    else:
        print("  Dominated by RELIABILITY. The information is present but the mapping is off,")
        print("  which recalibration can address.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
