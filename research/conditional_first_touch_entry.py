"""
Conditional entry: does ANY state make the first-touch barrier stop being a martingale?

The unconditional process is a martingale - `binance_first_touch_lane.py` measured EV at
exactly -cost in all eighteen cells. That says the barrier geometry carries no information on
its own. It does NOT say no conditioning state exists, and that is the last untested question
in the sweep.

TWO WAYS TO GET A FAKE ANSWER HERE, AND BOTH ARE GUARDED.

  1. LEAKAGE. The research matrix carries `future_close_5m`, `future_high_5m`,
     `future_low_5m`, `ret_5m`, `future_direction_5m`, `tradable_move_label` and
     `fail_fast_label`. Those are OUTCOMES sitting in the same table as the features.
     Conditioning on any of them produces spectacular alpha and means nothing. The feature
     list here is an explicit ALLOW-list of causal columns, not a deny-list of known-bad ones -
     a deny-list silently admits every future column added later.

  2. MULTIPLE TESTING. Searching features x buckets x barrier pairs guarantees winners. With
     ~180 cells, roughly nine clear p<0.05 by chance alone, and the best of them will look
     convincing. The null here is therefore a MAX-STATISTIC PERMUTATION: shuffle the feature
     values against the outcomes, re-run the ENTIRE search, take the best cell, and repeat.
     The real best is only evidence if it beats the distribution of shuffled bests.

     This is the correct null because it is the same search, and it prices the search itself.

Read-only. Exits non-zero only on a data problem.

    python research/conditional_first_touch_entry.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

MATRIX = os.environ.get("BTC_RESEARCH_MATRIX", str(ROOT / "data" / "research_matrix_1m.parquet"))

#: CAUSAL columns only, as an ALLOW-list. Everything here is observable at the bar that ends
#: before entry. Nothing named future_*, ret_*, or *_label may appear.
FEATURES = [
    "rv_15m", "rv_30m", "rv_60m", "rv_term", "compression_ratio", "range_15m",
    "shock_magnitude", "micro_range_15m", "vpin_15m", "vpin_30m", "vpin_50m",
    "cvd_1m", "cvd_5m", "cvd_divergence", "perp_spot_basis_bps", "large_trade_imbalance",
    "count_accel_5m", "vol_accel", "funding_velocity",
]
#: Anything matching these is an outcome and must never be a feature. Asserted, not assumed.
LEAK_MARKERS = ("future", "ret_", "label", "close", "high", "low", "open")

HORIZON_MIN = 5
GRID = [(10, 10), (15, 30), (30, 15)]
N_BUCKETS = 5
PERMUTATIONS = 300


def round_trip_cost_bps() -> float:
    try:
        from binance_paper.config import EngineConfig
        cfg = EngineConfig.from_env()
        return 2.0 * (cfg.fee_rate_bps + cfg.slippage_bps)
    except Exception:
        return 12.0


def main() -> int:
    if not Path(MATRIX).exists():
        print(f"no research matrix at {MATRIX}")
        return 2

    # The allow-list is checked against the leak markers before anything is read.
    bad = [f for f in FEATURES if any(m in f.lower() for m in LEAK_MARKERS)]
    if bad:
        print(f"REFUSING: feature list contains outcome-shaped columns: {bad}")
        return 2

    available = pq.ParquetFile(MATRIX).schema_arrow.names
    feats = [f for f in FEATURES if f in available]
    table = pq.read_table(MATRIX, columns=["ts_ms", "open", "high", "low"] + feats)
    ts = table.column("ts_ms").to_pylist()
    op = table.column("open").to_pylist()
    hi = table.column("high").to_pylist()
    lo = table.column("low").to_pylist()
    n = len(ts)
    cost = round_trip_cost_bps()

    print("=" * 78)
    print("CONDITIONAL ENTRY - can any state break the first-touch martingale?")
    print("=" * 78)
    print(f"\n{n:,} bars   {len(feats)} causal features   {HORIZON_MIN}m horizon   "
          f"cost {cost:.1f} bps")
    print(f"search space: {len(feats)} features x {N_BUCKETS} buckets x {len(GRID)} pairs "
          f"= {len(feats) * N_BUCKETS * len(GRID):,} cells")
    print("Excluded as OUTCOMES, not features: " + ", ".join(
        c for c in available if any(m in c.lower() for m in ("future", "label")) )[:70] + " ...")

    # Outcomes computed ONCE per barrier pair. They do not depend on the feature, so the
    # permutation only ever shuffles the bucket assignment - which is what makes 300 full
    # re-searches affordable and keeps the null exactly the same search.
    outcomes = {}
    for target_bps, stop_bps in GRID:
        up_f, dn_f = target_bps / 10_000.0, stop_bps / 10_000.0
        rows = []
        for i in range(0, n - HORIZON_MIN - 1, HORIZON_MIN):
            entry = op[i + 1]
            if not entry or entry <= 0:
                continue
            upper, lower = entry * (1.0 + up_f), entry * (1.0 - dn_f)
            pnl = None
            for k in range(i + 1, min(i + 1 + HORIZON_MIN, n)):
                tu, td = hi[k] >= upper, lo[k] <= lower
                if tu and td:
                    pnl = "AMB"
                    break
                if tu:
                    pnl = target_bps - cost
                    break
                if td:
                    pnl = -stop_bps - cost
                    break
            if pnl == "AMB":
                continue
            if pnl is None:
                exit_px = op[min(i + HORIZON_MIN, n - 1)]
                pnl = (exit_px / entry - 1.0) * 10_000.0 - cost
            rows.append((i, pnl))
        outcomes[(target_bps, stop_bps)] = rows

    # NUMPY FROM HERE. The pure-Python version of this search took longer than the study was
    # worth; the statistics are identical, the loop is not.
    import numpy as np

    col_vals = {f: np.asarray(table.column(f).to_pylist(), dtype=float) for f in feats}
    buckets = {}
    for f in feats:
        v = col_vals[f]
        finite = v[np.isfinite(v)]
        if finite.size < 1000:
            continue
        edges = np.quantile(finite, [(j + 1) / N_BUCKETS for j in range(N_BUCKETS - 1)])
        b_all = np.searchsorted(edges, v, side="left")
        b_all[~np.isfinite(v)] = -1
        buckets[f] = b_all
    feats = [f for f in feats if f in buckets]
    if not feats:
        print("\nno feature had enough finite values")
        return 0

    # Per (feature, pair): the bucket id and the pnl for every usable window.
    cells = {}
    for pair, rows in outcomes.items():
        idx = np.asarray([i for i, _ in rows], dtype=np.int64)
        pnl = np.asarray([v for _, v in rows], dtype=float)
        for f in feats:
            b = buckets[f][idx]
            keep = b >= 0
            cells[(f, pair)] = (b[keep].astype(np.int64), pnl[keep])

    def best_cell(shuffle_rng=None):
        """Best bucket mean over the whole grid. With an rng the pnl is permuted first -
one shuffled world, searched exactly as the real one is."""
        best = None
        for (f, pair), (b, pnl) in cells.items():
            p_use = pnl if shuffle_rng is None else pnl[shuffle_rng.permutation(pnl.size)]
            counts = np.bincount(b, minlength=N_BUCKETS)
            sums = np.bincount(b, weights=p_use, minlength=N_BUCKETS)
            with np.errstate(invalid="ignore", divide="ignore"):
                means = np.where(counts >= 500, sums / np.maximum(counts, 1), -np.inf)
            j = int(np.argmax(means))
            if np.isfinite(means[j]) and (best is None or means[j] > best[0]):
                best = (float(means[j]), f, pair, j, int(counts[j]))
        return best

    best = best_cell()
    if best is None:
        print("\nno cell had enough observations")
        return 0
    m, f, pair, b, cnt = best
    print("\nBEST CELL FOUND BY THE SEARCH")
    print("-" * 78)
    print(f"  feature {f}   quintile {b + 1}/{N_BUCKETS}   barriers "
          f"{pair[0]}/{pair[1]}   n={cnt:,}")
    print(f"  mean EV {m:+.2f} bps   (unconditional is ~{-cost:.0f} bps)")

    print(f"\nMAX-STATISTIC PERMUTATION NULL - {PERMUTATIONS} shuffles of the SAME search")
    print("-" * 78)
    print("  Each shuffle permutes the outcomes against the buckets, destroying any real")
    print("  relationship, then runs the ENTIRE search again. The best cell of a shuffled run")
    print("  is what a searcher would have found in pure noise.")
    rng = np.random.default_rng(20260808)
    null_best = []
    for _ in range(PERMUTATIONS):
        nb = best_cell(rng)
        if nb:
            null_best.append(nb[0])
    null_best.sort()
    if null_best:
        p95 = null_best[int(0.95 * (len(null_best) - 1))]
        beat = sum(1 for v in null_best if v >= m)
        print(f"  shuffled best: median {null_best[len(null_best) // 2]:+.2f} bps   "
              f"95th pct {p95:+.2f} bps   max {null_best[-1]:+.2f} bps")
        print(f"  p(shuffled best >= real best) = {beat / len(null_best):.3f}")
        print()
        # SIGNIFICANCE IS NOT PROFITABILITY, AND THE VERDICT NEEDS BOTH.
        #
        # An earlier version branched on the p-value alone and printed "a CANDIDATE for a
        # pre-registered forward test" for a cell returning -11.74 bps. A p-value below 0.05
        # on a LOSS is not a candidate for anything - it says the loss is reliably a loss.
        # That is a check passing while the property it guarantees is false.
        significant = beat / len(null_best) <= 0.05
        profitable = m > 0.0
        if not profitable:
            print(f"  -> THE BEST CELL LOSES {abs(m):.2f} bps. Whatever its p-value, a")
            print("     conditioning state that loses money is not an entry rule. It is")
            print(f"     {abs(m) - cost:+.2f} bps against the unconditional -{cost:.0f}.")
            if significant:
                print("     (It does clear the permutation null, which means the loss is")
                print("      reliable - not that the cell is tradeable.)")
        elif not significant:
            print("  -> THE REAL BEST IS INSIDE THE NOISE DISTRIBUTION. The search found what")
            print("     a search finds in random data. No conditional entry state survives.")
        else:
            print("  -> positive AND outside the noise distribution: a CANDIDATE for a")
            print("     pre-registered forward test, not a finding.")
        print(f"  for scale: the best cell a SHUFFLE produced was {null_best[-1]:+.2f} bps"
              + ("  - better than the real best" if null_best[-1] > m else ""))


    print("\n" + "=" * 78)
    print("The unconditional process is a martingale. This asked whether any observable state")
    print("changes that, and priced the search that asked. A best cell that a shuffle can")
    print("reproduce is not a discovery - it is the cost of looking.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
