"""
test_deadfeatures_30d.py — does retiring the dead (backfill-constant) features hurt?
===================================================================================
~78 of the app's 136 features are LIVE-ONLY microstructure (order book, live flow,
derivatives, walls, cross-exchange). In the historical backfill they're CONSTANT
(broadcast), so during training they are zero-variance and carry NO information —
the trees can't split on them. This script proves that on 30 days OOS:

  1. Build the 136-feature matrix from the research-matrix OHLCV (the backfill
     condition: no live order-flow overlay).
  2. Flag every feature whose train-slice variance ~ 0  → the retirement candidates.
  3. Train big_move + direction with ALL 136 vs the LIVE-ACTIVE subset → compare AUC
     on the last 30 days. If equal, retiring the dead features is offline-NEUTRAL
     (cleaner model, less train/serve mismatch, no accuracy loss).

Honest scope: this reproduces the no-overlay condition, so it slightly OVER-counts
(real training back-fills CVD/VPIN). The principle holds: zero-variance ⇒ zero offline
signal ⇒ safe to retire. Whether they help LIVE can only be tested with live data.

Usage:  python backend/tests/test_deadfeatures_30d.py [--test-days 30]
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import argparse
import os
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from features import build_features_from_klines, FEATURE_NAMES, NUM_FEATURES

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-days", type=int, default=30)
    args = ap.parse_args()
    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan).dropna(
        subset=["open", "high", "low", "close", "volume", "ts_ms"]).reset_index(drop=True)
    klines = [{"open": o, "high": h, "low": lo, "close": c, "volume": v, "time": t}
              for o, h, lo, c, v, t in zip(df.open, df.high, df.low, df.close, df.volume, df.ts_ms)]
    print(f"klines: {len(klines):,}  building {NUM_FEATURES}-feature matrix (backfill condition)...")
    X = build_features_from_klines(klines)                 # (N-1, 136), live-only cols constant
    closes = df["close"].values[1:]                        # align to feature rows (return-based)
    ts = df["ts_ms"].values[1:]
    n = min(len(X), len(closes))
    X, closes, ts = X[:n], closes[:n], ts[:n]

    split = ts.max() - args.test_days * 86400_000
    tr, te = ts < split, ts >= split
    print(f"matrix {X.shape}  train={tr.sum():,}  test(last {args.test_days}d)={te.sum():,}")

    # Dead features = ~zero variance on the TRAIN slice.
    var = X[tr].var(axis=0)
    scale = np.maximum(np.abs(X[tr]).mean(axis=0), 1e-9)
    dead = (var / (scale ** 2 + 1e-12) < 1e-8) | (var < 1e-12)
    live_idx = np.where(~dead)[0]
    print(f"\nDEAD (backfill-constant) features: {int(dead.sum())} / {NUM_FEATURES}   "
          f"LIVE-ACTIVE: {len(live_idx)}")
    dead_names = [FEATURE_NAMES[i] for i in np.where(dead)[0]] if len(FEATURE_NAMES) == NUM_FEATURES else []
    if dead_names:
        print("  sample dead:", ", ".join(dead_names[:14]), "..." if len(dead_names) > 14 else "")

    # Labels at h=5 (forward, no lookahead) — big_move + direction.
    h = 5
    fwd = np.full(n, np.nan); fwd[:-h] = (closes[h:] - closes[:-h]) / closes[:-h]
    ok = ~np.isnan(fwd)
    big = (np.abs(fwd) > np.nanquantile(np.abs(fwd[tr & ok]), 0.75)).astype(int)
    direc = (fwd > 0).astype(int)

    def auc(cols, y):
        mtr, mte = tr & ok, te & ok
        sc = StandardScaler().fit(np.nan_to_num(X[mtr][:, cols]))
        clf = LogisticRegression(max_iter=1000, class_weight="balanced").fit(
            sc.transform(np.nan_to_num(X[mtr][:, cols])), y[mtr])
        p = clf.predict_proba(sc.transform(np.nan_to_num(X[mte][:, cols])))[:, 1]
        return roc_auc_score(y[mte], p) if len(np.unique(y[mte])) > 1 else float("nan")

    allc = np.arange(NUM_FEATURES)
    print("\n=== big_move (timing) AUC — last 30d OOS ===")
    print(f"  ALL {NUM_FEATURES} features : {auc(allc, big):.4f}")
    print(f"  LIVE-ACTIVE {len(live_idx)} : {auc(live_idx, big):.4f}")
    print("=== direction sign AUC — last 30d OOS (expect ~0.50) ===")
    print(f"  ALL {NUM_FEATURES} features : {auc(allc, direc):.4f}")
    print(f"  LIVE-ACTIVE {len(live_idx)} : {auc(live_idx, direc):.4f}")
    print("\nVerdict: if the LIVE-ACTIVE AUCs match the ALL-feature AUCs, the dead features "
          "carry zero offline signal — retiring them is safe (no loss) and removes the "
          "train/serve zero-vs-live mismatch. (Live signal, if any, needs live-shadow data.)")


if __name__ == "__main__":
    main()
