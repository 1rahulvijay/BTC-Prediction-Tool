"""
probe_path_timing.py - is the WITHIN-window timing of the high/low predictable?
==============================================================================
The trade plan predicts HOW FAR price travels (magnitude -- proven). This asks the next
question: WHEN inside the 5m/15m window does the high/low occur, and is that conditionally
predictable from the vol keepers (so we could add an "expect the move early/late" hint)?

Two parts:
  1. DISTRIBUTION of time-to-high / time-to-low across the window's minutes. A random walk
     gives Levy's ARCSINE law -- a U-shape (extremes cluster at the start & end, rarely middle).
     That is a universal shape, NOT a per-window signal.
  2. CONDITIONAL predictability: can the parity-proven keepers (rv_15m.. shock_magnitude) predict
     whether the high lands in the FIRST half vs SECOND half of the window, out-of-sample? If AUC
     ~0.50, timing is a coin-flip like direction; if >0.55, there is a usable early/late hint.

Intra-window minute paths reconstructed from research_matrix_1m.parquet (1-min high/low). Causal:
keepers known at the window open; timing target is strictly inside the future window. ASCII output.

Usage:
  python backend/probe_path_timing.py
  python backend/probe_path_timing.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_ta_matrix as TA  # noqa: E402

warnings.filterwarnings("ignore")
MATRIX = os.path.join(TA.ROOT, "data", "research_matrix_1m.parquet")
FEATURES = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio", "shock_magnitude"]


def _timing(df, w):
    """time-to-high / time-to-low (minute 1..w) of the future window, from 1-min highs/lows."""
    H = np.column_stack([df["high"].shift(-k).values for k in range(1, w + 1)])
    L = np.column_stack([df["low"].shift(-k).values for k in range(1, w + 1)])
    ok = ~np.isnan(H).any(axis=1) & ~np.isnan(L).any(axis=1)
    tth = np.full(len(df), np.nan); ttl = np.full(len(df), np.nan)
    tth[ok] = np.argmax(H[ok], axis=1) + 1
    ttl[ok] = np.argmin(L[ok], axis=1) + 1
    return tth, ttl


def run():
    df = pd.read_parquet(MATRIX)
    for w in (5, 15):
        tth, ttl = _timing(df, w)
        m = ~np.isnan(tth)
        th = tth[m].astype(int)
        print("\n" + "=" * 80)
        print(f"BTC {w}m  -  WITHIN-WINDOW TIMING of the high/low  (n={m.sum()})")
        # 1) distribution (arcsine / U-shape check)
        dist = np.bincount(th, minlength=w + 1)[1:] / len(th)
        print("  time-to-HIGH distribution by minute: " +
              " ".join(f"m{i+1}:{dist[i]*100:.0f}%" for i in range(w)))
        edges = dist[0] + dist[-1]; mid = dist[len(dist)//2]
        print(f"    edges(min1+min{w})={edges*100:.0f}%  vs middle(min{w//2+1})={mid*100:.0f}%  "
              f"-> {'U-shaped (arcsine, universal)' if edges > 2*mid else 'flat'}")
        # 2) conditional predictability: high in FIRST half vs SECOND half, from keepers
        first_half = (tth <= (w / 2.0)).astype(float)
        d = pd.concat([df[FEATURES], pd.Series(first_half, name="y")], axis=1)
        d = d.replace([np.inf, -np.inf], np.nan).dropna()
        d = d[(~np.isnan(tth))[d.index]] if False else d.dropna()
        agg = TA.wf_clf(d[FEATURES], d["y"])
        nm, ns = TA.shuffle_null_clf(d[FEATURES], d["y"])
        base = float(max(d["y"].mean(), 1 - d["y"].mean()))
        verdict = ("PREDICTABLE (usable hint)" if agg["auc"] >= 0.55 and agg["above_half"] >= agg["n_folds"] - 1
                   else "coin-flip (timing not conditionally predictable)")
        print(f"  predict HIGH-in-first-half from keepers: walk-fwd AUC {agg['auc']:.3f}+-{agg['auc_std']:.3f} "
              f"(null {nm:.3f}, base {base:.2f}) -> {verdict}")
        # also: does the move happen FAST? fraction of range realized by the window's midpoint
        half = max(1, w // 2)
        Hh = np.column_stack([df["high"].shift(-k).values for k in range(1, half + 1)])
        Lh = np.column_stack([df["low"].shift(-k).values for k in range(1, half + 1)])
        Hf = np.column_stack([df["high"].shift(-k).values for k in range(1, w + 1)])
        Lf = np.column_stack([df["low"].shift(-k).values for k in range(1, w + 1)])
        ok = ~np.isnan(Hf).any(axis=1)
        rng_half = (np.nanmax(Hh[ok], axis=1) - np.nanmin(Lh[ok], axis=1))
        rng_full = (np.nanmax(Hf[ok], axis=1) - np.nanmin(Lf[ok], axis=1))
        frac = np.median(rng_half / (rng_full + 1e-9))
        print(f"  speed: median fraction of total range already traveled by the HALFWAY point = {frac*100:.0f}%")
    print("\nREAD: a U-shaped time-to-high distribution that keepers CANNOT predict (AUC~0.50) means the "
          "*shape* is known (extremes at the edges) but the *which-window* timing is a coin-flip, like "
          "direction. A >0.55 AUC would justify an 'expect the move early/late' field on the trade plan.")


def selftest():
    rng = np.random.default_rng(0); n = 3000
    df = pd.DataFrame({"high": 100 + np.abs(rng.normal(0, 1, n)).cumsum() * 0.0 + rng.normal(0, 1, n),
                       "low": 100 + rng.normal(0, 1, n) - 2})
    tth, ttl = _timing(df, 5)
    ok = np.isfinite(tth).sum() > 1000 and set(np.unique(tth[np.isfinite(tth)])) <= {1, 2, 3, 4, 5}
    print(f"selftest: time-to-high computed, values in 1..5 = {ok}")
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if not os.path.exists(MATRIX):
        print(f"missing {MATRIX}"); sys.exit(2)
    run()


if __name__ == "__main__":
    main()
