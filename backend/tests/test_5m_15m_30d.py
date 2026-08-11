"""
test_5m_15m_30d.py — quick honest scorecard for the 5m & 15m models over 30 days.
=================================================================================
Self-contained sanity check for the two Polymarket betting horizons. Trains on the
OLDER data, tests on the LAST 30 days (out-of-sample) of research_matrix_1m.parquet,
and reports the three things that matter — for 5m and 15m:

  1. DIRECTION sign-truth  → expect ~0.50 (coin-flip; the proven ceiling)
  2. P(big_move) AUC       → expect ~0.60-0.64 (the real timing edge)
  3. MAGNITUDE band 80%    → conformal coverage of the expected-drop/up band

No app, no saved models needed — it fits + tests internally so it's a clean OOS read.

Usage:  python backend/tests/test_5m_15m_30d.py [--test-days 30]
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
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import roc_auc_score

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
# NOTE: ret_5m is the REALIZED forward 5m return (the outcome) — NOT a feature. Including
# it leaks the label (gave a fake 99.8% sign-acc). Direction uses only past-state features.
DIR_FEATS = ["rv_15m", "rv_30m", "vpin", "compression_ratio", "shock_magnitude",
             "perp_spot_basis_bps", "cvd_divergence"]
MOVE_FEATS = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-days", type=int, default=30)
    args = ap.parse_args()
    if not os.path.exists(MATRIX):
        print(f"ERROR: {MATRIX} not found."); return
    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=list(set(DIR_FEATS + MOVE_FEATS + ["close", "ts_ms"]))).reset_index(drop=True)
    split_ts = df["ts_ms"].max() - args.test_days * 86400_000
    tr = df["ts_ms"].values < split_ts
    te = ~tr
    print(f"rows={len(df):,}  train={tr.sum():,}  test(last {args.test_days}d)={te.sum():,}")
    close = df["close"].values

    print(f"\n{'h':>3} | {'dir sign-acc':>12} | {'P(big_move) AUC':>15} | {'band 80% cov':>12}")
    print("-" * 54)
    for h in (5, 15):
        # forward signed return (bps) and labels, no lookahead
        fwd = np.full(len(close), np.nan)
        fwd[:-h] = (close[h:] - close[:-h]) / close[:-h] * 10000.0
        ok = ~np.isnan(fwd)
        ytr_dir = (fwd > 0).astype(int)
        big = (np.abs(fwd) > np.nanquantile(np.abs(fwd[tr & ok]), 0.75)).astype(int)

        # 1. DIRECTION sign-truth (committed up/down) — expect ~0.50
        mtr, mte = tr & ok, te & ok
        sc = StandardScaler().fit(df[DIR_FEATS].values[mtr])
        clf = LogisticRegression(max_iter=1000, class_weight="balanced").fit(
            sc.transform(df[DIR_FEATS].values[mtr]), ytr_dir[mtr])
        pdir = clf.predict_proba(sc.transform(df[DIR_FEATS].values[mte]))[:, 1]
        dir_acc = ((pdir >= 0.5).astype(int) == ytr_dir[mte]).mean()

        # 2. P(big_move) AUC — the timing edge
        scb = StandardScaler().fit(df[MOVE_FEATS].values[mtr])
        clb = LogisticRegression(max_iter=1000, class_weight="balanced").fit(
            scb.transform(df[MOVE_FEATS].values[mtr]), big[mtr])
        pbig = clb.predict_proba(scb.transform(df[MOVE_FEATS].values[mte]))[:, 1]
        big_auc = roc_auc_score(big[mte], pbig) if len(np.unique(big[mte])) > 1 else float("nan")

        # 3. MAGNITUDE band — signed q10/q90 + CQR -> 80% coverage on the test 30 days
        Xtr, ytr = df[MOVE_FEATS].values[mtr], fwd[mtr]
        ca = slice(int(len(ytr) * 0.8), None)   # last 20% of TRAIN as conformal calib
        q10 = GradientBoostingRegressor(loss="quantile", alpha=0.10, n_estimators=150,
                                        max_depth=3, learning_rate=0.05, random_state=7).fit(Xtr, ytr)
        q90 = GradientBoostingRegressor(loss="quantile", alpha=0.90, n_estimators=150,
                                        max_depth=3, learning_rate=0.05, random_state=7).fit(Xtr, ytr)
        lo_ca, hi_ca = q10.predict(Xtr[ca]), q90.predict(Xtr[ca])
        cqr = float(np.quantile(np.maximum(lo_ca - ytr[ca], ytr[ca] - hi_ca), 0.80))
        Xte, yte = df[MOVE_FEATS].values[mte], fwd[mte]
        cov = np.mean((yte >= q10.predict(Xte) - cqr) & (yte <= q90.predict(Xte) + cqr)) * 100

        print(f"{h:>3}m| {dir_acc*100:>11.1f}% | {big_auc:>15.3f} | {cov:>11.1f}%")

    print("\nRead: dir sign-acc ~50% confirms the coin-flip ceiling (expected). "
          "P(big_move) AUC >~0.58 = the real timing edge. band ~80% = honest calibrated range.")


if __name__ == "__main__":
    main()
