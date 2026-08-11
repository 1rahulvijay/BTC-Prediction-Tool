"""
probe_range_expansion.py - is the NEXT window's range / vol-expansion predictable at open?
==========================================================================================
The path band predicts the ABSOLUTE high/low. This asks the RELATIVE, regime-transition question that
tells you which windows are worth playing at all:
  (1) BIG window?   next-window $-range in the top tercile        -> only engage active windows
  (2) EXPANSION?    next-window range > the just-finished window  -> vol about to expand (size up) vs contract (sit out)

Volatility clusters AND mean-reverts, so both should be PREDICTABLE from the vol keepers known at open.
Leak-free: keepers at open vs the future window range. 360d matrix. ASCII output.

Usage:
  python backend/research/standalone/probe_range_expansion.py
  python backend/research/standalone/probe_range_expansion.py --selftest
"""
from __future__ import annotations

try:
    from . import _bootstrap as _research_bootstrap  # noqa: F401
except ImportError:
    import _bootstrap as _research_bootstrap  # noqa: F401

del _research_bootstrap


import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import probe_ta_matrix as TA  # noqa: E402

warnings.filterwarnings("ignore")
MATRIX = os.path.join(TA.ROOT, "data", "research_matrix_1m.parquet")
FEATURES = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio", "shock_magnitude"]


def _test(df, mask, y, label):
    d = pd.concat([df[FEATURES], y.rename("y")], axis=1)[mask].replace([np.inf, -np.inf], np.nan).dropna()
    if len(d) < 2000 or d["y"].nunique() < 2:
        print(f"  {label}: insufficient (n={len(d)})"); return
    agg = TA.wf_clf(d[FEATURES], d["y"]); nm, _ = TA.shuffle_null_clf(d[FEATURES], d["y"])
    v = ("PREDICTABLE" if agg["auc"] >= 0.55 and agg["above_half"] >= agg["n_folds"] - 1 else "coin-flip")
    print(f"  {label}: base={d['y'].mean():.3f}  walk-fwd AUC {agg['auc']:.3f}+-{agg['auc_std']:.3f} "
          f"(null {nm:.3f}) -> {v}")


def run():
    df = pd.read_parquet(MATRIX)
    for w in (5, 15):
        next_hi = pd.concat([df["high"].shift(-k) for k in range(1, w + 1)], axis=1).max(axis=1)
        next_lo = pd.concat([df["low"].shift(-k) for k in range(1, w + 1)], axis=1).min(axis=1)
        prev_hi = pd.concat([df["high"].shift(k) for k in range(0, w)], axis=1).max(axis=1)
        prev_lo = pd.concat([df["low"].shift(k) for k in range(0, w)], axis=1).min(axis=1)
        next_range = next_hi - next_lo
        prev_range = prev_hi - prev_lo
        valid = next_range.notna() & prev_range.notna() & (prev_range > 0)

        big_thr = float(next_range[valid].quantile(0.667))
        big = (next_range >= big_thr).astype(float)
        expansion = (next_range > prev_range).astype(float)
        # vol autocorrelation (does range cluster?)
        ac = float(np.corrcoef(prev_range[valid], next_range[valid])[0, 1])

        print("\n" + "=" * 84)
        print(f"BTC {w}m  -  RANGE / vol-expansion predictability   "
              f"(median next-range ${float(next_range[valid].median()):.0f}, range autocorr {ac:.3f})")
        _test(df, valid, big, f"(1) BIG window (next range >= ${big_thr:.0f}, top 1/3) ")
        _test(df, valid, expansion, "(2) EXPANSION (next range > prev range)        ")
        # split: of predicted-big vs predicted-small, realized round-trip/activity (descriptive value)
        print(f"  base rates: big-window {float(big[valid].mean()):.3f} · expansion {float(expansion[valid].mean()):.3f}")

    print("\nREAD: a high range autocorr + PREDICTABLE 'big window' = activity is forecastable -> SKIP the "
          "quiet bottom tercile (no $ to capture, no fade room) and only engage active windows. EXPANSION "
          "predictable = you can anticipate a vol breakout (size up) vs a contraction (sit out). This is the "
          "window-SELECTION filter that sits in front of the path/touch/fade plays.")


def selftest():
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"close": 100 + rng.normal(0, 1, 3000).cumsum() * 0.01})
    df["high"] = df["close"] + 0.5; df["low"] = df["close"] - 0.5
    nh = pd.concat([df["high"].shift(-k) for k in range(1, 6)], axis=1).max(axis=1)
    ok = nh.notna().sum() > 2000
    print("selftest:", "PASS" if ok else "FAIL"); return ok


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
