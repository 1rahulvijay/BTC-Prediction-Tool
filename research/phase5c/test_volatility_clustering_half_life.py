"""PHASE5C_103 - how long does elevated volatility stay elevated?

WHY IT DECIDES SOMETHING
    Holding period, update frequency and cooldown are all currently fixed constants. If
    volatility mean-reverts in 20 minutes, a 2-hour hold spends most of its life in a different
    regime from the one that justified entry. The half-life is the number those constants
    should be derived from.

    Two estimates, because one alone is easy to fool:

        AR(1) half-life   ln(0.5) / ln(phi) on log realized volatility
        survival          empirical P(still elevated) after k minutes, given elevated now

    360 days supports this; the 21-day Polymarket window would not.

    python research/phase5c/test_volatility_clustering_half_life.py
"""
from __future__ import annotations

RESEARCH_STATUS = "VALID_DIAGNOSTIC"
CAPITAL_AUTHORITY = False

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import BINANCE_DAYS, load_bars  # noqa: E402

WINDOW_M = 15                     # realized-vol estimation window, minutes
ELEVATED_QUANTILE = 0.80          # declared: "elevated" is the top quintile
HORIZONS_M = (15, 30, 60, 120, 240)


def ar1_half_life(series: np.ndarray) -> dict:
    """Half-life of an AR(1) fitted to the series. phi >= 1 means no mean reversion."""
    series = np.asarray(series, float)
    series = series[np.isfinite(series)]
    if len(series) < 50:
        return {}
    current, following = series[:-1], series[1:]
    phi = float(np.polyfit(current, following, 1)[0])
    if phi <= 0 or phi >= 1:
        return {"phi": phi, "half_life_windows": float("inf")}
    return {"phi": phi, "half_life_windows": float(np.log(0.5) / np.log(phi))}


def survival(elevated: np.ndarray, horizon_windows: int) -> float:
    """P(still elevated `horizon_windows` later | elevated now)."""
    if horizon_windows >= len(elevated):
        return float("nan")
    now = elevated[:-horizon_windows]
    later = elevated[horizon_windows:]
    return float(later[now].mean()) if now.any() else float("nan")


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    rng = np.random.default_rng(13)
    # phi = 0.5 -> half-life exactly 1 window.
    series = np.zeros(5000)
    for index in range(1, 5000):
        series[index] = 0.5 * series[index - 1] + rng.normal(0, 0.1)
    fitted = ar1_half_life(series)
    check(abs(fitted["phi"] - 0.5) < 0.05, "the AR(1) coefficient is recovered")
    check(abs(fitted["half_life_windows"] - 1.0) < 0.2,
          "phi=0.5 gives a half-life of one window, as ln(0.5)/ln(0.5) requires")

    persistent = np.zeros(5000)
    for index in range(1, 5000):
        persistent[index] = 0.95 * persistent[index - 1] + rng.normal(0, 0.1)
    check(ar1_half_life(persistent)["half_life_windows"]
          > fitted["half_life_windows"] * 5,
          "a more persistent process has a much longer half-life")
    check(ar1_half_life(np.arange(200, dtype=float))["half_life_windows"] == float("inf"),
          "a non-reverting series reports infinite half-life rather than a negative one")

    flags = np.array([True] * 100 + [False] * 100)
    check(survival(flags, 150) < 0.5,
          "a state that ends does not survive a horizon longer than itself")
    check(np.isnan(survival(flags, 500)),
          "a horizon longer than the sample is NOT MEASURED rather than zero")
    check(ar1_half_life(np.array([1.0, 2.0])) == {},
          "too few observations return nothing rather than a fitted number")

    print(f"\nVOLATILITY HALF-LIFE SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    print("=" * 96)
    print("PHASE5C-103  VOLATILITY HALF-LIFE - how long does an elevated regime last?")
    print("=" * 96)
    frame = load_bars()
    close = frame["close"].to_numpy(float)
    returns = np.diff(np.log(close))
    total = len(returns) // WINDOW_M
    realized = np.array([np.sqrt(np.sum(returns[i * WINDOW_M:(i + 1) * WINDOW_M] ** 2))
                         for i in range(total)])
    log_rv = np.log(np.clip(realized, 1e-12, None))

    print(f"  {len(frame):,} bars over ~{BINANCE_DAYS} days | {total:,} disjoint "
          f"{WINDOW_M}-minute volatility windows")

    fitted = ar1_half_life(log_rv)
    half_life_m = fitted["half_life_windows"] * WINDOW_M
    print()
    print(f"  AR(1) on log realized vol: phi {fitted['phi']:.4f}")
    print(f"  half-life {fitted['half_life_windows']:.2f} windows = "
          f"{half_life_m:.1f} minutes")

    cutoff = np.quantile(realized, ELEVATED_QUANTILE)
    elevated = realized >= cutoff
    print()
    print(f"  'elevated' = top {1 - ELEVATED_QUANTILE:.0%} of windows (RV >= {cutoff:.5f})")
    print(f"{'horizon':>10}{'P(still elevated)':>20}")
    for horizon in HORIZONS_M:
        steps = horizon // WINDOW_M
        rate = survival(elevated, steps)
        print(f"{horizon:>9}m{rate:>20.1%}")
    print(f"{'baseline':>10}{1 - ELEVATED_QUANTILE:>20.1%}")

    print()
    print(f"  Read against the action engine's horizons: a {half_life_m:.0f}-minute half-life")
    print("  means a 120-minute hold spends most of its life in a regime different from the one")
    print("  that justified entry. Fixed holding constants are not obviously the right shape.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
