"""The preregistered COMPLETE_TRADE_M0_V2 gates, as executable functions.

Constants alone do not prove the evaluator implements the protocol. Each gate here is a pure
function over realized trades, so a behavioural test can assert it FAILS on a fixture built to
violate it - which is the only way to know the gate is real.

The concentration gate is the one most easily faked. The preregistration limits the share of
PROFIT contributed by any single hour or week, not the share of TRADES. A strategy can spread its
trade count perfectly evenly and still earn 90% of its money in one hour; counting trades would
pass it.

    python backend/trade_forecast/m0_gates.py --selftest
"""
from __future__ import annotations

import sys
from typing import Any, Sequence

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def profit_concentration(pnl: Sequence[float], buckets: Sequence[Any]) -> float:
    """Largest share of total POSITIVE profit contributed by any one bucket.

    Positive profit is the denominator, not net PnL: with net, a bucket contributing 100% of the
    gains can be masked by losses elsewhere, and the ratio becomes uninterpretable (or negative).
    Returns 1.0 when nothing was earned, which is the conservative direction."""
    values = np.asarray(list(pnl), dtype=float)
    keys = list(buckets)
    gains = np.clip(values, 0.0, None)
    total = float(gains.sum())
    if total <= 1e-12:
        return 1.0
    by: dict[Any, float] = {}
    for key, gain in zip(keys, gains):
        by[key] = by.get(key, 0.0) + float(gain)
    return max(by.values()) / total


def profit_factor(pnl: Sequence[float]) -> float | None:
    values = np.asarray(list(pnl), dtype=float)
    wins = float(values[values > 0].sum())
    losses = float(-values[values < 0].sum())
    return wins / losses if losses > 1e-12 else None


def day_block_lower_bound(
    pnl: Sequence[float], days: Sequence[Any], *, seed: int = 20260726, draws: int = 5000
) -> float | None:
    """2.5th percentile of the day-block bootstrap mean. Trades within a day share a regime."""
    values = np.asarray(list(pnl), dtype=float)
    keys = list(days)
    by: dict[Any, list[float]] = {}
    for key, value in zip(keys, values):
        by.setdefault(key, []).append(float(value))
    daily = np.array([np.mean(v) for v in by.values()], dtype=float)
    if len(daily) < 5:
        return None
    rng = np.random.default_rng(seed)
    samples = daily[rng.integers(0, len(daily), size=(draws, len(daily)))].mean(axis=1)
    return float(np.percentile(samples, 2.5))


def matched_random_difference(
    by_round: dict[Any, dict[str, Any]],
    *,
    seed: int = 20260726,
    draws: int = 2000,
) -> dict[str, Any]:
    """Selected PnL vs randomly picking one candidate per round from the SAME opportunity set.

    Takes a per-round structure rather than parallel arrays:

        {round_id: {"selected_pnl": float, "candidate_pnls": [float, ...]}}

    Parallel lists silently mis-align. If pools were filtered for emptiness while the selected
    list was not, the comparison averaged a different number of rounds on each side and the
    "difference" meant nothing. Keying by round makes that impossible to express.

    Returns an empirical p-value so the result can enter the preregistered BH family:

        p = (1 + #{random_mean >= selected_mean}) / (draws + 1)

    The +1 is the standard finite-sample correction; it keeps p strictly positive, so a lucky
    permutation can never report p = 0."""
    problems = []
    usable: dict[Any, dict[str, Any]] = {}
    for key, entry in by_round.items():
        pool = [float(v) for v in (entry.get("candidate_pnls") or [])]
        selected = entry.get("selected_pnl")
        if selected is None:
            problems.append(f"{key}: no selected_pnl")
            continue
        if not pool:
            problems.append(f"{key}: empty candidate pool")
            continue
        usable[key] = {"selected_pnl": float(selected), "candidate_pnls": pool}
    if problems:
        # Refuse rather than quietly comparing different round sets on each side.
        return {
            "valid": False,
            "problems": problems[:10],
            "rounds": len(usable),
            "p_value": None,
            "beats_random": False,
        }
    if not usable:
        return {"valid": False, "problems": ["no rounds"], "rounds": 0,
                "p_value": None, "beats_random": False}

    selected_mean = float(np.mean([e["selected_pnl"] for e in usable.values()]))
    pools = [np.asarray(e["candidate_pnls"], dtype=float) for e in usable.values()]
    rng = np.random.default_rng(seed)
    means = np.empty(draws, dtype=float)
    for i in range(draws):
        means[i] = np.mean([p[rng.integers(0, len(p))] for p in pools])
    p_value = float((1 + np.sum(means >= selected_mean)) / (draws + 1))
    return {
        "valid": True,
        "rounds": len(usable),
        "selected_mean": selected_mean,
        "random_mean": float(means.mean()),
        "difference": selected_mean - float(means.mean()),
        "random_p95": float(np.percentile(means, 95)),
        "p_value": p_value,
        "beats_random": bool(selected_mean > float(np.percentile(means, 95))),
    }


def benjamini_hochberg(p_values: Sequence[float], q: float = 0.10) -> dict[str, Any]:
    """BH step-up. Returns which hypotheses survive at false-discovery rate `q`."""
    values = np.asarray(list(p_values), dtype=float)
    n = len(values)
    if n == 0:
        return {"rejected": [], "threshold": 0.0, "n": 0}
    order = np.argsort(values)
    ranked = values[order]
    thresholds = q * (np.arange(1, n + 1) / n)
    passing = np.where(ranked <= thresholds)[0]
    if len(passing) == 0:
        return {"rejected": [], "threshold": 0.0, "n": n}
    cutoff_rank = int(passing.max())
    cutoff = float(ranked[cutoff_rank])
    return {
        "rejected": sorted(int(i) for i in order[: cutoff_rank + 1]),
        "threshold": cutoff,
        "n": n,
    }


def selftest() -> int:
    ok = True

    def chk(cond: bool, msg: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
        ok &= bool(cond)

    print("profit concentration (PROFIT share, not TRADE share)")
    # THE FIXTURE THAT MATTERS: trade count spread perfectly evenly across 4 hours, but ~90% of
    # the profit earned in hour 0. A trade-count gate passes this; the protocol's gate must not.
    hours = [0, 1, 2, 3] * 25                      # 25 trades in each of 4 hours
    pnl = [0.90 if h == 0 else 0.0037 for h in hours]
    counts = {h: hours.count(h) / len(hours) for h in set(hours)}
    chk(
        max(counts.values()) == 0.25,
        "trade-count share is a flat 25% per hour (a count gate would PASS this)",
    )
    share = profit_concentration(pnl, hours)
    chk(share > 0.50, f"profit-concentration correctly reports {share:.1%} in one hour")
    chk(share > max(counts.values()), "profit share and trade share are different numbers")

    even = [0.01] * 100
    chk(
        profit_concentration(even, hours) <= 0.30,
        "genuinely even profit passes the concentration gate",
    )
    chk(profit_concentration([0.0] * 10, [0] * 10) == 1.0,
        "no profit at all reports full concentration (conservative)")
    chk(profit_concentration([-1.0, -2.0], [0, 1]) == 1.0,
        "pure losses cannot produce a passing concentration")

    print("profit factor")
    chk(abs(profit_factor([2.0, -1.0]) - 2.0) < 1e-9, "PF = gains / losses")
    chk(profit_factor([1.0, 2.0]) is None, "no losses -> undefined, not infinite")

    print("day-block lower bound")
    chk(day_block_lower_bound([0.1] * 4, [1, 2, 3, 4]) is None, "fewer than 5 days -> None")
    lb = day_block_lower_bound([0.10] * 50, list(range(50)))
    chk(lb is not None and lb > 0, f"a consistently positive strategy has LB > 0 ({lb:.4f})")
    lb = day_block_lower_bound([0.5, -0.5] * 25, list(range(50)))
    chk(lb is not None and lb <= 0.2, "a coin-flip strategy does not get a strong LB")

    print("matched-random control")
    rng = np.random.default_rng(7)
    pools = {i: list(rng.normal(0.0, 0.05, size=8)) for i in range(200)}
    best = {i: {"selected_pnl": max(p), "candidate_pnls": p} for i, p in pools.items()}
    res = matched_random_difference(best)
    chk(res["valid"] and res["difference"] > 0 and res["beats_random"],
        f"an oracle-best selection beats matched-random ({res['difference']:+.4f})")
    chk(res["p_value"] is not None and res["p_value"] < 0.05,
        f"it also yields a usable p-value for BH ({res['p_value']:.4f})")
    chk(res["p_value"] > 0, "the +1 correction keeps p strictly positive")

    noskill = {i: {"selected_pnl": p[0], "candidate_pnls": p} for i, p in pools.items()}
    res = matched_random_difference(noskill)
    chk(not res["beats_random"], "a no-skill selection does NOT beat its matched-random control")
    chk(res["p_value"] > 0.05, f"and its p-value is not significant ({res['p_value']:.3f})")

    # Mis-alignment must be refused, not silently averaged over different round sets.
    broken = {1: {"selected_pnl": 0.5, "candidate_pnls": []},
              2: {"selected_pnl": 0.1, "candidate_pnls": [0.1, 0.2]}}
    res = matched_random_difference(broken)
    chk(not res["valid"] and res["p_value"] is None,
        "an empty candidate pool invalidates the comparison instead of skewing it")
    res = matched_random_difference({1: {"candidate_pnls": [0.1]}})
    chk(not res["valid"], "a round with no selected trade invalidates the comparison")

    print("Benjamini-Hochberg")
    res = benjamini_hochberg([0.001, 0.20, 0.60, 0.90], q=0.10)
    chk(res["rejected"] == [0], "one strong result survives BH among four tests")
    # BH is a STEP-UP rule: find the largest k with p(k) <= q*k/n, then reject ranks 1..k. With
    # q=0.10 and all p <= 0.07 it correctly rejects all four - that is the procedure, not a bug.
    res = benjamini_hochberg([0.04, 0.05, 0.06, 0.07], q=0.10)
    chk(len(res["rejected"]) == 4, "BH step-up rejects all four when every p clears its rank")
    # The correction earning its keep: p=0.04 would be "significant" uncorrected at alpha=0.05,
    # but inside a family of four with three null results BH rejects NOTHING.
    res = benjamini_hochberg([0.04, 0.30, 0.40, 0.50], q=0.10)
    chk(
        res["rejected"] == [],
        "a lone p=0.04 inside a family of 4 does NOT survive BH (uncorrected it would)",
    )
    res = benjamini_hochberg([0.30, 0.40, 0.50], q=0.10)
    chk(res["rejected"] == [], "nothing significant -> nothing rejected")
    chk(benjamini_hochberg([], q=0.10)["n"] == 0, "an empty family is handled")

    print("\nSELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
