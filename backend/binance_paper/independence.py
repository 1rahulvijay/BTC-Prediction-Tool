"""Effective sample size for overlapping trades. Replaces a count that claimed independence.

THE DEFECT
    The promotion gate was named `independent_trades_500` and implemented as

        len(trades) >= 500

    That proves COUNT, not independence. Five hundred trades can be one volatility episode, one
    repeated signal, or 500 entries thrown off by the same shock - and because 5m and 15m
    positions overlap in time, consecutive trades routinely share the same price path. A day-block
    bound computed over 500 correlated observations is far narrower than the evidence supports,
    so the gate was most permissive exactly when the sample was most redundant.

WHAT IS MEASURED INSTEAD
    non_overlapping_episodes  greedy interval selection: the largest set of trades whose
                              holding windows do not intersect. One episode, one observation.
    effective_sample_size     n / (1 + 2 * sum of positive serial autocorrelations) on the
                              per-trade P&L series - the standard correction for a correlated
                              sample.
    largest_cluster_share     the biggest fraction of trades falling in any single day.
    active_evidence_days      days with at least one trade, NOT first-to-last calendar span,
                              which counts elapsed time through outages as if it were evidence.

    python -m backend.binance_paper.independence --selftest
"""
from __future__ import annotations

import argparse
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def non_overlapping_episodes(intervals) -> int:
    """Greedy maximum set of mutually non-overlapping [entry, exit] windows.

    Sorting by END time is what makes greedy optimal here; sorting by start does not."""
    ordered = sorted((int(a), int(b)) for a, b in intervals if b is not None and a is not None)
    ordered.sort(key=lambda pair: pair[1])
    count, last_end = 0, None
    for start, end in ordered:
        if last_end is None or start >= last_end:
            count += 1
            last_end = end
    return count


def autocorrelation(values, lag: int) -> float:
    n = len(values)
    if n <= lag + 1:
        return 0.0
    mean = sum(values) / n
    denom = sum((v - mean) ** 2 for v in values)
    if denom <= 0:
        return 0.0
    num = sum((values[i] - mean) * (values[i + lag] - mean) for i in range(n - lag))
    return num / denom


def effective_sample_size(values, max_lag: int = 20) -> float:
    """n corrected for serial correlation. Equals n when the series is independent."""
    n = len(values)
    if n < 3:
        return float(n)
    total = 0.0
    for lag in range(1, min(max_lag, n - 1) + 1):
        rho = autocorrelation(values, lag)
        if rho <= 0:
            break                    # standard initial-positive-sequence truncation
        total += rho
    return float(n) / (1.0 + 2.0 * total)


def evidence_profile(trades) -> dict:
    """`trades` need entry_time_ms, exit_time_ms and net_pnl_usd."""
    if not trades:
        return {"trades": 0, "non_overlapping_episodes": 0, "effective_sample_size": 0.0,
                "active_evidence_days": 0, "largest_cluster_share": None}
    intervals = [(t.get("entry_time_ms"), t.get("exit_time_ms")) for t in trades
                 if t.get("entry_time_ms") is not None and t.get("exit_time_ms") is not None]
    values = [float(t["net_pnl_usd"]) for t in trades if t.get("net_pnl_usd") is not None]
    by_day: dict[int, int] = {}
    for t in trades:
        exit_ms = t.get("exit_time_ms")
        if exit_ms is not None:
            by_day[int(exit_ms) // 86_400_000] = by_day.get(int(exit_ms) // 86_400_000, 0) + 1
    return {
        "trades": len(trades),
        "non_overlapping_episodes": non_overlapping_episodes(intervals),
        "effective_sample_size": round(effective_sample_size(values), 1),
        # Days that actually CONTAIN a trade. First-to-last span counts outages as evidence.
        "active_evidence_days": len(by_day),
        "largest_cluster_share": (max(by_day.values()) / len(trades)) if by_day else None,
    }


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    # --- OVERLAP -------------------------------------------------------------------
    disjoint = [(0, 10), (20, 30), (40, 50)]
    check(non_overlapping_episodes(disjoint) == 3, "three disjoint windows are three episodes")
    stacked = [(0, 100), (1, 101), (2, 102), (3, 103)]
    check(non_overlapping_episodes(stacked) == 1,
          "four windows sharing almost the same path are ONE episode, not four")
    check(non_overlapping_episodes([(0, 10)] * 500) == 1,
          "500 identical overlapping trades collapse to 1 - the exact case the count gate "
          "waved through")
    check(non_overlapping_episodes([]) == 0, "no trades is zero episodes")
    check(non_overlapping_episodes([(0, 10), (10, 20)]) == 2,
          "windows that merely touch at the boundary do not overlap")

    # --- EFFECTIVE SAMPLE SIZE ------------------------------------------------------
    import random
    rng = random.Random(0)
    independent = [rng.gauss(0, 1) for _ in range(600)]
    ess_ind = effective_sample_size(independent)
    check(ess_ind > 400, f"an independent series keeps most of its n ({ess_ind:.0f} of 600)")

    correlated, previous = [], 0.0
    for _ in range(600):
        previous = 0.9 * previous + rng.gauss(0, 1)
        correlated.append(previous)
    ess_corr = effective_sample_size(correlated)
    check(ess_corr < ess_ind / 3,
          f"a strongly autocorrelated series collapses to {ess_corr:.0f} - 600 correlated "
          f"trades are not 600 observations")
    check(effective_sample_size([1.0] * 100) == 100.0,
          "a constant series has zero variance and is not silently rescaled")

    # --- PROFILE --------------------------------------------------------------------
    day = 86_400_000
    trades = [{"entry_time_ms": day * 3 + i, "exit_time_ms": day * 3 + i + 60_000,
               "net_pnl_usd": 1.0} for i in range(10)]
    profile = evidence_profile(trades)
    check(profile["trades"] == 10, "trade count is still reported, just not called independence")
    check(profile["non_overlapping_episodes"] < profile["trades"],
          "overlapping trades yield FEWER episodes than trades")
    check(profile["active_evidence_days"] == 1,
          "ten trades in one day is ONE active evidence day")
    check(profile["largest_cluster_share"] == 1.0,
          "and the whole sample is a single cluster, which the gate must see")

    spread = [{"entry_time_ms": day * d, "exit_time_ms": day * d + 60_000,
               "net_pnl_usd": 1.0} for d in range(10)]
    spread_profile = evidence_profile(spread)
    check(spread_profile["active_evidence_days"] == 10,
          "the same ten trades spread over ten days is ten active days")
    check(spread_profile["largest_cluster_share"] == 0.1,
          "...and a cluster share of 0.1 - so the metric distinguishes the two samples")

    check(evidence_profile([])["trades"] == 0, "an empty sample profiles without raising")

    print(f"\nEVIDENCE INDEPENDENCE SELFTEST: PASS ({checks} checks)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.parse_args()
    raise SystemExit(selftest())
