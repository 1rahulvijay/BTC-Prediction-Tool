"""PHASE5C_97 - what is the final distance from the anchor? The settlement-fragility surface.

WHY
    A position is "too close to call" when the terminal margin is small relative to the
    settlement source's own uncertainty. That is a distribution, not a prediction: this reports
    the terminal |price - anchor| quantiles and the probability of finishing inside $2/$5/$10/$20
    at each checkpoint.

    It also bears directly on oracle basis. Section 4.4 measured the recorded path disagreeing
    with official settlement on 10.7% of rounds at the 15s mark. A round finishing $2 from the
    anchor is one where that disagreement decides the contract.

DESCRIPTIVE ONLY - 21 days supports no effect below ~25 points.

    python research/phase5c/test_near_settlement_terminal_margin.py
"""
from __future__ import annotations

RESEARCH_STATUS = "VALID_DIAGNOSTIC"
CAPITAL_AUTHORITY = False

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import assert_descriptive_only, load_checkpoints  # noqa: E402

MARGIN_BANDS_USD = (2, 5, 10, 20)
EXTRA = ("label_terminal_distance_usd", "label_path_samples")


def margin_profile(margins: np.ndarray, bands=MARGIN_BANDS_USD) -> dict:
    """Quantiles plus the probability of finishing inside each declared band."""
    finite = margins[np.isfinite(margins)]
    if not len(finite):
        return {}
    return {"n": int(len(finite)),
            "q10": float(np.quantile(finite, 0.10)),
            "q50": float(np.quantile(finite, 0.50)),
            "q90": float(np.quantile(finite, 0.90)),
            **{f"within_{band}": float((finite < band).mean()) for band in bands}}


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    margins = np.array([1.0, 3.0, 7.0, 15.0, 40.0])
    profile = margin_profile(margins)
    check(profile["n"] == 5 and profile["q50"] == 7.0,
          "the median terminal margin is read straight off the distribution")
    check(profile["within_2"] == 0.2 and profile["within_20"] == 0.8,
          "band probabilities count strictly-inside finishes")
    check(profile["within_2"] <= profile["within_5"] <= profile["within_20"],
          "band probabilities are monotone - a wider band cannot be less likely")
    check(margin_profile(np.array([np.nan, np.nan])) == {},
          "an all-missing input yields an empty profile rather than a fabricated zero")
    check(margin_profile(np.array([1.0, np.nan, 3.0]))["n"] == 2,
          "missing margins are excluded from the count, not treated as zero distance")

    print(f"\nTERMINAL MARGIN SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    print("=" * 96)
    print("PHASE5C-97  TERMINAL MARGIN - how close to the anchor do rounds actually finish?")
    print("=" * 96)
    frame = load_checkpoints(EXTRA)
    frame = frame[frame["label_path_samples"] > 0]
    if frame.empty:
        print("  BLOCKED: no checkpoints with a forward path.")
        return 0
    print(f"  {assert_descriptive_only()}")

    margins = np.abs(frame["label_terminal_distance_usd"].to_numpy(float))
    checkpoints = frame["checkpoint_s"].to_numpy(float)

    print(f"  rows {len(frame):,}")
    print()
    header = "".join(f"{f'<${b}':>9}" for b in MARGIN_BANDS_USD)
    print(f"{'checkpoint':>11}{'n':>8}{'q10':>9}{'q50':>9}{'q90':>9}{header}")
    for value in sorted(np.unique(checkpoints)):
        mask = checkpoints == value
        profile = margin_profile(margins[mask])
        if not profile or profile["n"] < 200:
            continue
        bands = "".join(f"{profile[f'within_{b}']:>8.1%} " for b in MARGIN_BANDS_USD)
        print(f"{int(value):>10}s{profile['n']:>8,}{profile['q10']:>9.1f}"
              f"{profile['q50']:>9.1f}{profile['q90']:>9.1f}{bands}")

    overall = margin_profile(margins)
    print()
    print(f"  Across every checkpoint: median terminal margin ${overall['q50']:.1f}, "
          f"q10 ${overall['q10']:.1f}.")
    print(f"  {overall['within_2']:.1%} of rounds finish within $2 of the anchor and "
          f"{overall['within_5']:.1%} within $5.")
    print()
    print("  Those are the rounds where the settlement SOURCE decides the contract, not the")
    print("  price path - and 4.4 measured the recorded path disagreeing with official")
    print("  settlement on 10.7% of rounds at T-15s. A position taken inside that band is")
    print("  exposed to oracle basis rather than to a forecast.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
