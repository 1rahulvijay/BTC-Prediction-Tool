"""PHASE5C_94 - when does the FINAL anchor crossing occur? Is the current leader likely final?

WHY IT MATTERS
    Every settlement question reduces to one thing: has the last crossing already happened? If
    it has, the current leader wins. The labels already carry the crossing history, so this is
    a distribution to read rather than a model to fit.

DESCRIPTIVE ONLY - 21 days supports no effect below ~25 points.

    python research/phase5c/test_last_crossing_timing_distribution.py
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

EXTRA = ("label_future_crossings", "label_final_cross_after_s",
         "label_first_cross_is_final", "label_no_further_crossing", "label_path_samples")


def survival_table(checkpoints, no_further):
    """P(no crossing remains) at each checkpoint - the empirical survival of the leader."""
    rows = []
    for value in sorted(np.unique(checkpoints)):
        mask = checkpoints == value
        if mask.sum() < 50:
            continue
        rows.append({"checkpoint_s": int(value), "n": int(mask.sum()),
                     "p_already_final": float(np.mean(no_further[mask]))})
    return rows


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    checkpoints = np.array([15] * 100 + [240] * 100)
    no_further = np.array([True] * 90 + [False] * 10 + [True] * 40 + [False] * 60)
    table = survival_table(checkpoints, no_further)
    check(len(table) == 2, "one row per checkpoint with enough observations")
    check(table[0]["p_already_final"] > table[1]["p_already_final"],
          "the leader is more likely already final closer to settlement")
    check(abs(table[0]["p_already_final"] - 0.90) < 1e-9,
          "the survival estimate is the plain mean of the no-further-crossing label")
    check(survival_table(np.array([15] * 10), np.array([True] * 10)) == [],
          "a checkpoint with too few observations is omitted, not reported thin")
    check(assert_descriptive_only().startswith("DESCRIPTIVE ONLY"),
          "the study announces that it makes no significance claim")

    print(f"\nLAST CROSSING SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    print("=" * 96)
    print("PHASE5C-94  LAST CROSSING TIMING - has the final crossing already happened?")
    print("=" * 96)
    frame = load_checkpoints(EXTRA)
    frame = frame[frame["label_path_samples"] > 0]
    if frame.empty:
        print("  BLOCKED: no checkpoints with a forward path.")
        return 0
    print(f"  {assert_descriptive_only()}")

    checkpoints = frame["checkpoint_s"].to_numpy(float)
    no_further = frame["label_no_further_crossing"].to_numpy(bool)
    crossings = frame["label_future_crossings"].to_numpy(float)
    final_after = frame["label_final_cross_after_s"].to_numpy(float)
    won = frame["won"].to_numpy(float)

    print(f"  rows {len(frame):,}")
    print()
    print(f"{'checkpoint':>11}{'n':>8}{'already final':>15}{'mean crossings left':>21}"
          f"{'final cross after (med)':>25}{'leader wins':>13}")
    for row in survival_table(checkpoints, no_further):
        mask = checkpoints == row["checkpoint_s"]
        remaining = crossings[mask]
        timing = final_after[mask]
        timing = timing[np.isfinite(timing)]
        print(f"{row['checkpoint_s']:>10}s{row['n']:>8,}{row['p_already_final']:>15.1%}"
              f"{np.nanmean(remaining):>21.2f}"
              f"{(np.median(timing) if len(timing) else float('nan')):>24.1f}s"
              f"{won[mask].mean():>13.1%}")

    crossed = frame[frame["label_future_crossings"] > 0]
    print()
    print(f"  of the {len(crossed):,} checkpoints whose path crosses again:")
    print(f"    the FIRST crossing is also the last : "
          f"{crossed['label_first_cross_is_final'].mean():.1%}")
    print()
    print("  Read with 4.4: 57% of crossings revert. 'Already final' is therefore a much")
    print("  stronger statement than 'currently leading', and the gap between the two columns")
    print("  above is the whole of the settlement-fragility problem.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
