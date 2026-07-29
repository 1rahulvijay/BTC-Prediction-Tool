"""Tests the ceiling-breaking levers, instead of only measuring the hurdle they face.

WHY
    ceiling_analysis.py measured that the 5m/15m taker lane cannot clear costs, and named four
    levers. Naming a lever is not testing it. Two of the four are testable on existing data
    right now, and were asserted to be promising without being run:

        LEVER 2  longer horizons    - 240m move is 4.4x cost even as a taker
        LEVER 4  fewer trades       - momentum spent ~368% of capital on costs

    The other two are genuinely blocked and are reported as such rather than approximated:

        LEVER 1  maker execution    - needs queue position, needs sequenced L2 depth
        LEVER 3  binary-contract target - needs Polymarket settlement joins

    This script runs the two testable levers across horizons and selectivity thresholds. If the
    hurdle argument is right, longer horizons and stricter selection should move results toward
    zero - but moving toward zero is not the same as becoming positive, and only an actual
    positive out-of-sample result would break the ceiling.

    python research/ceiling_levers_test.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Backtest, causal_frame, split  # noqa: E402

HORIZONS = (15, 60, 240, 720)
# Percentile of |z_240| above which a signal is taken. Higher = fewer, more extreme trades.
SELECTIVITY = (0.0, 0.90, 0.99, 0.999)


def momentum(part, cut):
    strong = part["z_240"].abs() >= part["z_240"].abs().quantile(cut) if cut > 0 else True
    return np.where((part["ret_5"] > 0) & (part["ret_15"] > 0) & strong, 1,
                    np.where((part["ret_5"] < 0) & (part["ret_15"] < 0) & strong, -1, 0))


def reversion(part, cut):
    threshold = part["z_60"].abs().quantile(cut) if cut > 0 else 2.0
    strong = part["z_60"].abs() >= max(threshold, 2.0)
    return np.where(strong, -np.sign(part["z_60"]), 0)


STRATEGIES = {"momentum": momentum, "mean-reversion": reversion}


def run(part, fn, cut) -> Backtest:
    book = Backtest()
    direction = fn(part, cut)
    active = direction != 0
    for gross, side in zip(part.loc[active, "fwd"], direction[active]):
        book.trade(float(gross) * float(side))
    return book


def significance_gate(test, fn, cut, configurations_searched: int) -> None:
    """A positive number found by searching 32 configurations needs the repo's own gates.

    Three independent checks, because they fail for different reasons:
      day-block LCB     - is the profit spread across days, or concentrated in a few?
      multiple testing  - would this many searches produce this result by chance?
      matched random    - does the ENTRY SELECTION beat random entries of the same count?
    """
    rng = np.random.default_rng(20260729)
    direction = fn(test, cut)
    active = direction != 0
    net = (test.loc[active, "fwd"].to_numpy() * direction[active]) - 9.0 / 1e4
    count = len(net)

    stamps = test.loc[active, "ts_ms"].to_numpy()
    days = (stamps // 86_400_000).astype(int)
    unique_days = np.unique(days)
    by_day = {d: net[days == d] for d in unique_days}
    draws = []
    for _ in range(2000):
        picked = rng.integers(0, len(unique_days), len(unique_days))
        draws.append(np.concatenate([by_day[unique_days[i]] for i in picked]).mean())
    draws = np.sort(np.asarray(draws))
    lcb = draws[int(0.05 * len(draws))]
    p_value = float((draws <= 0).mean())

    universe = test["fwd"].to_numpy()
    control = []
    for _ in range(2000):
        idx = rng.integers(0, len(universe), count)
        control.append((universe[idx] * rng.choice([-1, 1], count) - 9.0 / 1e4).mean())
    control = np.asarray(control)
    empirical = (1 + int((control >= net.mean()).sum())) / (len(control) + 1)

    threshold = 0.05 / configurations_searched
    print("")
    print(f"  SIGNIFICANCE GATE ({count} trades over {len(unique_days)} distinct days)")
    print(f"    net bps/trade          : {net.mean() * 1e4:+.2f}")
    print(f"    day-block 5% LCB       : {lcb * 1e4:+.2f} bps   "
          f"{'PASS' if lcb > 0 else 'FAIL'}")
    print(f"    bootstrap p            : {p_value:.4f} vs Bonferroni {threshold:.5f}   "
          f"{'PASS' if p_value < threshold else 'FAIL'}")
    print(f"    matched-random control : p = {empirical:.4f}   "
          f"{'PASS' if empirical < 0.05 else 'FAIL'}")
    verdict = lcb > 0 and p_value < threshold and empirical < 0.05
    print(f"    OVERALL                : {'PROMOTABLE CANDIDATE' if verdict else 'NOT A DISCOVERY'}")
    if not verdict and empirical < 0.05:
        print("    (entry selection is non-random, but the profit concentrates in few days")
        print("     and does not survive correction for the configurations searched)")


def main() -> int:
    print("=" * 92)
    print("CEILING LEVERS - horizon (lever 2) x selectivity (lever 4), OUT-OF-SAMPLE")
    print("=" * 92)

    positives = []
    for name, fn in STRATEGIES.items():
        print(f"\n{name}")
        print(f"{'horizon':>9}{'selectivity':>13}{'OOS trades':>12}"
              f"{'OOS return %':>14}{'gross bps':>12}")
        print("-" * 92)
        for horizon in HORIZONS:
            frame = causal_frame(200_000, horizon)
            _, test = split(frame)
            for cut in SELECTIVITY:
                book = run(test, fn, cut)
                if book.trades < 30:
                    print(f"{horizon:>7}m{cut:>13.3f}{book.trades:>12}"
                          f"{'(too few)':>14}{'-':>12}")
                    continue
                direction = fn(test, cut)
                active = direction != 0
                gross = ((test.loc[active, "fwd"].to_numpy() * direction[active]).mean()
                         * 1e4)
                flag = "  <-- POSITIVE" if book.total_return_pct > 0 else ""
                if book.total_return_pct > 0:
                    positives.append((name, horizon, cut, book.total_return_pct, gross))
                print(f"{horizon:>7}m{cut:>13.3f}{book.trades:>12}"
                      f"{book.total_return_pct:>14.2f}{gross:>12.2f}{flag}")

    print("\n" + "=" * 92)
    print("VERDICT")
    print("=" * 92)
    if positives:
        print(f"  {len(positives)} configuration(s) POSITIVE out-of-sample:")
        for name, horizon, cut, ret, gross in positives:
            print(f"    {name} @ {horizon}m, selectivity {cut}: "
                  f"{ret:+.2f}% ({gross:+.2f} bps gross)")
        print("")
        print("  Applying the repository's own gates to the strongest candidate:")
        best = max(positives, key=lambda row: row[3])
        frame = causal_frame(200_000, best[1])
        _, test = split(frame)
        significance_gate(test, STRATEGIES[best[0]], best[2],
                          len(STRATEGIES) * len(HORIZONS) * len(SELECTIVITY))
    else:
        print("  NO configuration is positive out-of-sample.")
        print("  Levers 2 and 4 do not break the ceiling on this data. The remaining")
        print("  untested levers are the two that are data-blocked:")
        print("    LEVER 1  maker execution      - needs sequenced L2 depth")
        print("    LEVER 3  binary-contract target - needs Polymarket settlement joins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
