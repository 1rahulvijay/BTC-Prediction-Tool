"""PHASE5C_106 - does any viable stop/target GEOMETRY exist, before any direction model?

THE QUESTION THIS ANSWERS FIRST
    A strategy can have a positive mean return and still be impossible to trade, because the
    adverse excursion arrives before the favourable one and the stop fires first. That is a
    property of the PATH, not of the forecast, and it can be measured without predicting
    anything.

    So for a frozen grid of targets and stops this reports:

        P(target hit before stop)     the only quantity a bracket lives or dies on
        P(neither hit)
        net expectancy after the round trip

    If no cell in the grid clears zero, no direction model can rescue a bracket in this lane,
    and that is worth knowing before one is built.

FROZEN GRID, NO SELECTION
    Targets and stops are declared below. The full surface is published. Picking the best cell
    afterwards is how a grid of 25 becomes 25 chances to find noise.

    python research/phase5c/test_mfe_mae_joint_distribution.py
"""
from __future__ import annotations

RESEARCH_STATUS = "VALID_DIAGNOSTIC"
CAPITAL_AUTHORITY = False

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend" / "binance_alpha"))

from _common import BINANCE_DAYS, load_bars  # noqa: E402
from action_value import round_trip_bps  # noqa: E402

HORIZON_M = 60
TARGETS_BPS = (10, 20, 30, 50)
STOPS_BPS = (10, 20, 30, 50)


def first_touch(highs, lows, entry, target_bps, stop_bps, long: bool = True) -> str:
    """Which barrier is touched first inside the window: 'target', 'stop' or 'neither'.

    A bar that spans both barriers is charged as a STOP. The intrabar path is unknown and
    assuming the favourable order would manufacture edge out of missing information."""
    up = entry * (1.0 + target_bps / 10_000.0) if long else entry * (1.0 - target_bps / 10_000.0)
    down = entry * (1.0 - stop_bps / 10_000.0) if long else entry * (1.0 + stop_bps / 10_000.0)
    for high, low in zip(highs, lows):
        hit_target = high >= up if long else low <= up
        hit_stop = low <= down if long else high >= down
        if hit_target and hit_stop:
            return "stop"
        if hit_target:
            return "target"
        if hit_stop:
            return "stop"
    return "neither"


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    rising_h, rising_l = [100.5, 101.0], [99.95, 100.4]
    check(first_touch(rising_h, rising_l, 100.0, 20, 20) == "target",
          "a rising path touches the long target first")
    falling_h, falling_l = [100.05, 100.1], [99.5, 99.0]
    check(first_touch(falling_h, falling_l, 100.0, 20, 20) == "stop",
          "a falling path touches the long stop first")
    check(first_touch([100.01], [99.99], 100.0, 20, 20) == "neither",
          "a flat path touches neither barrier")
    check(first_touch([101.0], [99.0], 100.0, 20, 20) == "stop",
          "a bar spanning BOTH barriers is charged as a stop - the intrabar order is unknown "
          "and assuming the good one manufactures edge")
    check(first_touch(falling_h, falling_l, 100.0, 20, 20, long=False) == "target",
          "the short side mirrors: a falling path hits the short target")
    check(abs(round_trip_bps() - 12.0) < 1e-9,
          "the round trip comes from binance_paper.config")

    print(f"\nMFE/MAE GEOMETRY SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    print("=" * 96)
    print("PHASE5C-106  MFE/MAE GEOMETRY - does any bracket clear costs before a direction model?")
    print("=" * 96)
    cost = round_trip_bps()
    frame = load_bars()
    close = frame["close"].to_numpy(float)
    high = frame["high"].to_numpy(float)
    low = frame["low"].to_numpy(float)
    starts = np.arange(0, len(close) - HORIZON_M, HORIZON_M)     # DISJOINT windows
    print(f"  {len(frame):,} bars over ~{BINANCE_DAYS} days | {len(starts):,} disjoint "
          f"{HORIZON_M}m windows | round trip {cost:.1f} bps")
    print("  A bar spanning both barriers counts as a STOP. Full frozen surface, no selection.")

    print()
    print(f"{'target':>8}{'stop':>7}{'P(target)':>11}{'P(stop)':>10}{'P(neither)':>12}"
          f"{'net bps':>10}")
    best = None
    for target in TARGETS_BPS:
        for stop in STOPS_BPS:
            outcomes = [first_touch(high[s + 1:s + HORIZON_M + 1],
                                    low[s + 1:s + HORIZON_M + 1], close[s], target, stop)
                        for s in starts]
            hits = np.array([o == "target" for o in outcomes])
            stops = np.array([o == "stop" for o in outcomes])
            neither = np.array([o == "neither" for o in outcomes])
            # A window that touches neither is closed at the horizon; approximate it as flat,
            # which is neutral rather than favourable.
            net = hits.mean() * target - stops.mean() * stop - cost
            print(f"{target:>7}b{stop:>6}b{hits.mean():>11.1%}{stops.mean():>10.1%}"
                  f"{neither.mean():>12.1%}{net:>10.2f}")
            if best is None or net > best[0]:
                best = (net, target, stop)

    print()
    print(f"  best cell in the frozen grid: target {best[1]} / stop {best[2]} at "
          f"{best[0]:+.2f} bps")
    if best[0] > 0:
        print("  A bracket geometry clears costs before any direction model. Worth a declared")
        print("  forward test - and note this is the BEST of 16 cells, so it carries a")
        print("  multiple-comparison discount that a forward run would have to survive.")
    else:
        print("  NO cell in the grid clears costs. The adverse excursion arrives too often and")
        print("  too early, so no direction model can rescue a bracket in this lane. This is a")
        print("  property of the PATH, established without predicting anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
