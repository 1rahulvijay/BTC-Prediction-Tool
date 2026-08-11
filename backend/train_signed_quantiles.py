"""
train_signed_quantiles.py — calibrated signed-magnitude band head (standalone, safe).
=====================================================================================
Produces the honest expected-drop / expected-up / projected band for the price-to-beat
card. SIGNED quantiles (q10/q50/q90) per horizon, then CONFORMALIZED (CQR) so the 80%
band actually covers ~80% — fixing the "lands outside ~50% of the time by design" vagueness.

Why standalone (not in model.train): it's a small CPU fit (minutes), trains in start.bat's
head phase BEFORE the 6h ensemble retrain, and CANNOT break that retrain. It serves via
`live_keepers.py` (same rv/compression features, parity-proven), so no live-feature gap.

Features = MOVE_FEATS (rv_15m/30m/60m, compression_ratio) — all computable live with parity.
Target = signed forward return in bps at each horizon, derived from the matrix `close` column.

Saves data/saved_models/signed_quantile_model.pkl:
  {horizon: {q10, q50, q90 (fitted), cqr_lo, cqr_hi}, features, horizons}

Usage:  python backend/train_signed_quantiles.py [--days N]
"""
from __future__ import annotations

import argparse
import os
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import GradientBoostingRegressor
from quantile_safety import (conformal_adjustment, monotone_quantiles,
                             purged_train_cal_test_slices)
from artifact_identity import resolve_history_days

# Manifest written in the same step as the artifact: without it the artifact reads as
# UNKNOWN identity, and phold_challenger refuses to deploy a calibrator while any source
# artifact fails identity enforcement - which disables
# PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1.
from verified_io import write_manifest as write_integrity_manifest

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
OUT = os.path.join(
    os.environ.get("BTC_MODEL_OUTPUT_DIR") or os.path.join(DATA_DIR, "saved_models"),
    "signed_quantile_model.pkl",
)

MOVE_FEATS = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio"]
HORIZONS = [5, 15]   # pruned 2026-06-21: band only for the tradeable markets
TRAIN_DAYS_TAG = str(resolve_history_days())

HEAD_VERSION = f"2026-08-10-cqr-purged-test-h5-15-{TRAIN_DAYS_TAG}d"


def _fit_q(X, y, q):
    return GradientBoostingRegressor(loss="quantile", alpha=q, n_estimators=200,
                                     max_depth=3, learning_rate=0.05, subsample=0.8,
                                     random_state=7).fit(X, y)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="use only the last N days (0=all)")
    args = ap.parse_args()
    if not os.path.exists(MATRIX):
        print(f"ERROR: {MATRIX} not found."); return
    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=MOVE_FEATS + ["close", "ts_ms"]).reset_index(drop=True)
    if args.days:
        cut = df["ts_ms"].max() - args.days * 86400_000
        df = df[df["ts_ms"] >= cut].reset_index(drop=True)
    close = df["close"].values
    X = df[MOVE_FEATS].values
    print(f"rows={len(df):,}  feats={MOVE_FEATS}  horizons={HORIZONS}")

    models = {}
    print(f"\n{'h':>3} {'cov80_raw':>9} {'cov80_cqr':>9} {'drop(q10)':>10} {'up(q90)':>9} {'med(q50)':>9}")
    for h in HORIZONS:
        y = np.full(len(close), np.nan)
        y[:-h] = (close[h:] - close[:-h]) / close[:-h] * 10000.0   # signed bps, no lookahead
        m = ~np.isnan(y)
        Xh, yh = X[m], y[m]
        n = len(yh)
        # The newest 2% is an untouched test. The preceding 2% calibrates CQR, with a
        # horizon-sized purge on both boundaries. Reporting calibration-set coverage here
        # used to print ~80% by construction and call it evidence; only the untouched test
        # is reported now.
        _sf = min(max(float(os.environ.get("BTC_TRAIN_SPLIT_FRAC", "0.98")), 0.5), 0.98)
        tr, ca, te = purged_train_cal_test_slices(n, h, _sf)
        Xtr, ytr = Xh[tr], yh[tr]
        Xca, yca = Xh[ca], yh[ca]
        Xte, yte = Xh[te], yh[te]
        q10, q50, q90 = _fit_q(Xtr, ytr, 0.10), _fit_q(Xtr, ytr, 0.50), _fit_q(Xtr, ytr, 0.90)
        lo_ca, _, hi_ca = monotone_quantiles(
            q10.predict(Xca), q50.predict(Xca), q90.predict(Xca))
        E = np.maximum(lo_ca - yca, yca - hi_ca)    # CQR conformity score on the recent slice
        cqr = conformal_adjustment(E, 0.80)
        lo_te, mid_t, hi_te = monotone_quantiles(
            q10.predict(Xte), q50.predict(Xte), q90.predict(Xte))
        cov80_raw = np.mean((yte >= lo_te) & (yte <= hi_te)) * 100
        cov80_cqr = np.mean((yte >= lo_te - cqr) & (yte <= hi_te + cqr)) * 100
        print(f"{h:>3} {cov80_raw:>8.1f}% {cov80_cqr:>8.1f}% "
              f"{(lo_te - cqr).mean():>9.1f} {(hi_te + cqr).mean():>+8.1f} {mid_t.mean():>+8.1f}")
        models[h] = {
            "q10": q10, "q50": q50, "q90": q90, "cqr": cqr,
            "test_metrics": {"n": int(len(yte)), "coverage_raw": float(cov80_raw / 100),
                             "coverage_cqr": float(cov80_cqr / 100)},
            "split": {"train_rows": int(len(ytr)), "calibration_rows": int(len(yca)),
                      "test_rows": int(len(yte)), "purge_rows": int(h)},
            "serve_monotone_projection": True,
        }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    joblib.dump({"models": models, "features": MOVE_FEATS, "horizons": HORIZONS,
                 "version": HEAD_VERSION,
                 "note": "signed bps quantiles; purged train/cal/test; test-only coverage; "
                         "band = [ordered q10-cqr, ordered q90+cqr] for ~80% coverage; "
                         "project close = ref_price*(1+q50/1e4); serve via live_keepers."}, OUT)
    write_integrity_manifest(OUT)
    print(f"\nSaved -> {OUT}")
    print("Serving: expected_drop=q10-cqr, expected_up=q90+cqr, projected_close from q50 "
          "(no manufactured lean drift). Features via live_keepers (parity-proven).")


if __name__ == "__main__":
    main()
