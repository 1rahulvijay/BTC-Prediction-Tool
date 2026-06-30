"""
Train horizon-aware Big-Drop Risk keeper heads.

big_drop = future low trades below current close by at least the horizon-specific
dollar threshold.
"""
from __future__ import annotations

import os

import joblib
import numpy as np
import pandas as pd

from keeper_head_training import (
    BUCKET_TAG, FEATURES, HORIZONS, TRAIN_DAYS_TAG, buckets_for_training,
    fit_binary_head, future_window, model_summary,
)

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
OUT = os.path.join(DATA_DIR, "saved_models", "bigdrop_keeper_model.pkl")
HEAD_VERSION = f"2026-06-18-bigdrop-keeper4-horizons-buckets-iso-split-{TRAIN_DAYS_TAG}-{BUCKET_TAG}"


def _ensemble():
    from keeper_head_training import ensemble
    return ensemble()


def main():
    if not os.path.exists(MATRIX):
        print(f"ERROR: {MATRIX} not found.")
        return

    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=FEATURES + ["close", "low"]).copy()
    close = df["close"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    X_all = df[FEATURES].values
    buckets = buckets_for_training(close)   # auto-derived p75/p90/p97 from THIS matrix; env-overridable

    models = {}
    for h in HORIZONS:
        if h not in buckets:
            continue
        threshold = buckets[h][0]
        future_low = future_window(low, h, np.min)
        drop_usd = future_low - close
        mask = ~np.isnan(drop_usd)
        y = (drop_usd[mask] <= -threshold).astype(int)
        model = fit_binary_head(X_all[mask], y)
        if model:
            models[int(h)] = model
        print(f"big_drop_{h}m <= -${threshold:.0f}: {model_summary(model)}")

    bundle = {
        "models": models,
        "features": FEATURES,
        "horizons": sorted(models),
        "drop_threshold_usd_by_horizon": {h: v[0] for h, v in buckets.items()},
        "move_buckets_usd_by_horizon": buckets,
        "version": HEAD_VERSION,
        "note": "P(big_drop|keepers): future low <= close - horizon meaningful bucket. Calibrated per horizon.",
    }
    if 5 in models:
        bundle.update({k: v for k, v in models[5].items() if k not in ("pipe", "iso")})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    joblib.dump(bundle, OUT)
    print(f"Saved {OUT}")


if __name__ == "__main__":
    main()
