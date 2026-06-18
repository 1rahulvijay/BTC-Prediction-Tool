"""
Train horizon-aware market activity/range keeper heads.

This is the deployable stand-in for future-volume modeling. It predicts whether
the next horizon's high-low range is at least the horizon-specific dollar threshold.
"""
from __future__ import annotations

import os

import joblib
import numpy as np
import pandas as pd

from keeper_head_training import (
    FEATURES, HORIZONS, TRAIN_DAYS_TAG, fit_binary_head, future_window,
    model_summary, move_buckets_by_horizon, move_threshold_for,
    move_thresholds_by_horizon,
)

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
OUT = os.path.join(DATA_DIR, "saved_models", "activity_keeper_model.pkl")
HEAD_VERSION = f"2026-06-18-activity-range-keeper4-horizons-buckets-iso-split-{TRAIN_DAYS_TAG}"


def _ensemble():
    from keeper_head_training import ensemble
    return ensemble()


def main():
    if not os.path.exists(MATRIX):
        print(f"ERROR: {MATRIX} not found.")
        return

    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=FEATURES + ["high", "low"]).copy()
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    X_all = df[FEATURES].values

    models = {}
    for h in HORIZONS:
        threshold = move_threshold_for(h)
        future_high = future_window(high, h, np.max)
        future_low = future_window(low, h, np.min)
        future_range = future_high - future_low
        mask = ~np.isnan(future_range)
        y = (future_range[mask] >= threshold).astype(int)
        model = fit_binary_head(X_all[mask], y)
        if model:
            models[int(h)] = model
        print(f"activity_range_{h}m >= ${threshold:.0f}: {model_summary(model)}")

    bundle = {
        "models": models,
        "features": FEATURES,
        "horizons": sorted(models),
        "range_threshold_usd_by_horizon": move_thresholds_by_horizon(),
        "move_buckets_usd_by_horizon": move_buckets_by_horizon(),
        "version": HEAD_VERSION,
        "note": "P(active_range|keepers): future high-low range >= horizon meaningful bucket. Proxy for activity, not true volume.",
    }
    if 5 in models:
        bundle.update({k: v for k, v in models[5].items() if k not in ("pipe", "iso")})
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    joblib.dump(bundle, OUT)
    print(f"Saved {OUT}")


if __name__ == "__main__":
    main()
