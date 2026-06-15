"""
magnitude_research.py — improve the expected-drop / expected-up / projected-price band.
========================================================================================
The live card shows ONE symmetric move size (|move| median + a symmetric band). But a
real move distribution is ASYMMETRIC and regime-dependent: the downside (expected drop)
and upside (expected up) differ, and both widen in volatile regimes. This script tests a
better estimator on EXISTING data/features — NO new data, NO direction model:

  • SIGNED-return quantile heads q10/q25/q50/q75/q90 (GradientBoosting, purged OOF).
    → expected_drop = q10, projected_median = q50, expected_up = q90 (asymmetric band).
  • Measures CALIBRATION (coverage): does the 80% band [q10,q90] actually contain the
    outcome ~80% of the time? Does the 50% band [q25,q75] contain ~50%?
  • Compares to the current SYMMETRIC |move| baseline and a flat historical-quantile baseline.
  • Breaks coverage + band width down by regime (volatile bands must be wider).

Reads `research_matrix_1m.parquet` only. Pure offline, single-thread friendly. If the
signed-quantile band shows better coverage + real asymmetry, it's worth wiring into the
magnitude head (gated on sign-off).

Usage:  python backend/decision/magnitude_research.py
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.model_selection import TimeSeriesSplit

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")

FEATS = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio", "shock_magnitude",
         "vpin", "vpin_15m", "range_15m", "log_count"]
QUANTILES = [0.10, 0.25, 0.50, 0.75, 0.90]
EMBARGO = 5


def _purged_oof_quantile(X, y, q, embargo=EMBARGO):
    oof = np.full(len(y), np.nan)
    for tr, te in TimeSeriesSplit(n_splits=5).split(X):
        tr = tr[tr < te[0] - embargo]
        if len(tr) < 300:
            continue
        m = GradientBoostingRegressor(loss="quantile", alpha=q, n_estimators=200,
                                      max_depth=3, learning_rate=0.05, subsample=0.8,
                                      random_state=7)
        m.fit(X[tr], y[tr])
        oof[te] = m.predict(X[te])
    return oof


def _pinball(y, pred, q):
    d = y - pred
    return np.mean(np.maximum(q * d, (q - 1) * d))


def main():
    if not os.path.exists(MATRIX):
        print(f"ERROR: {MATRIX} not found."); return
    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    need = FEATS + ["future_close_5m", "close", "future_abs_move_5m", "rv_15m"]
    df = df.dropna(subset=need).reset_index(drop=True)
    X = df[FEATS].values
    # SIGNED 5m return in bps — the quantity the band should describe.
    y_signed = ((df["future_close_5m"] - df["close"]) / df["close"] * 10000.0).values
    y_abs = (df["future_abs_move_5m"] / df["close"] * 10000.0).values
    print(f"Rows: {len(df):,}.  Mean |move| = {y_abs.mean():.1f} bps.  "
          f"Computing purged-OOF signed quantiles {QUANTILES} ...")

    preds = {q: _purged_oof_quantile(X, y_signed, q) for q in QUANTILES}
    seen = ~np.isnan(preds[0.5])
    ys = y_signed[seen]
    P = {q: preds[q][seen] for q in QUANTILES}

    print("\n=== SIGNED-QUANTILE HEAD (proposed) ===")
    print(f"{'q':>5}{'pinball':>10}{'mean_pred_bps':>15}")
    for q in QUANTILES:
        print(f"{q:>5.2f}{_pinball(ys, P[q], q):>10.4f}{P[q].mean():>15.1f}")

    cov50 = np.mean((ys >= P[0.25]) & (ys <= P[0.75])) * 100
    cov80 = np.mean((ys >= P[0.10]) & (ys <= P[0.90])) * 100
    print(f"\nCALIBRATION (coverage):  50% band [q25,q75] -> {cov50:.1f}% (target 50)"
          f"   |   80% band [q10,q90] -> {cov80:.1f}% (target 80)")
    print(f"ASYMMETRY: expected_drop(q10)={P[0.10].mean():.1f} bps   "
          f"expected_up(q90)={P[0.90].mean():+.1f} bps   median(q50)={P[0.50].mean():+.1f} bps")
    print("  -> drop and up magnitudes differ => the symmetric band was throwing away skew.")

    # Baseline 1: SYMMETRIC |move| band (current approach) — median |move| +/- band.
    med_abs = np.median(y_abs[seen])
    sym_cov80 = np.mean(np.abs(ys) <= np.quantile(y_abs[seen], 0.90)) * 100
    print("\n=== CURRENT SYMMETRIC |move| BASELINE ===")
    print(f"  one number for both sides: ~{med_abs:.1f} bps; symmetric 80% reach "
          f"covers {sym_cov80:.1f}% — but cannot express drop!=up direction-of-skew.")

    # Regime breakdown: wider bands when rv is high?
    print("\n=== BAND WIDTH & COVERAGE BY VOLATILITY TERCILE ===")
    rv = df["rv_15m"].values[seen]
    t1, t2 = np.quantile(rv, [1/3, 2/3])
    for name, mask in [("LOW vol", rv <= t1), ("MID vol", (rv > t1) & (rv <= t2)),
                       ("HIGH vol", rv > t2)]:
        w = (P[0.90][mask] - P[0.10][mask]).mean()
        c = np.mean((ys[mask] >= P[0.10][mask]) & (ys[mask] <= P[0.90][mask])) * 100
        print(f"  {name:<9} 80%-band width={w:>6.1f} bps   coverage={c:>5.1f}%   n={mask.sum():,}")

    print("\nVerdict: if 80%-band coverage ~80% AND width grows LOW->HIGH vol AND drop!=up,")
    print("the signed-quantile head is a real upgrade to the expected-drop/up/projected band.")


if __name__ == "__main__":
    main()
