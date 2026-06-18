"""
Train horizon-aware directional big-up / big-down keeper heads.

These are confirmation heads only. They answer whether the close-to-close move
is at least the horizon-specific dollar threshold up or down by horizon.
"""
from __future__ import annotations

import os

import joblib
import numpy as np
import pandas as pd

from keeper_head_training import (
    FEATURES, HORIZONS, TRAIN_DAYS_TAG, fit_binary_head, future_close_delta,
    model_summary, move_buckets_by_horizon, move_threshold_for,
    move_thresholds_by_horizon,
)

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
OUT = os.path.join(DATA_DIR, "saved_models", "directional_keeper_model.pkl")
HEAD_VERSION = f"2026-06-18-directional-keeper4-horizons-buckets-iso-split-{TRAIN_DAYS_TAG}"


def _ensemble():
    from keeper_head_training import ensemble
    return ensemble()


def main():
    if not os.path.exists(MATRIX):
        print(f"ERROR: {MATRIX} not found.")
        return

    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=FEATURES + ["close"]).copy()
    close = df["close"].to_numpy(dtype=float)
    X_all = df[FEATURES].values

    models = {}
    for h in HORIZONS:
        threshold = move_threshold_for(h)
        delta = future_close_delta(close, h)
        mask = ~np.isnan(delta)
        up = fit_binary_head(X_all[mask], (delta[mask] >= threshold).astype(int))
        down = fit_binary_head(X_all[mask], (delta[mask] <= -threshold).astype(int))
        row = {}
        if up:
            row["big_up"] = up
        if down:
            row["big_down"] = down
        if row:
            models[int(h)] = row
        print(f"big_up_{h}m >= +${threshold:.0f}: {model_summary(up)}")
        print(f"big_down_{h}m <= -${threshold:.0f}: {model_summary(down)}")

    bundle = {
        "models": models,
        "features": FEATURES,
        "horizons": sorted(models),
        "move_threshold_usd_by_horizon": move_thresholds_by_horizon(),
        "move_buckets_usd_by_horizon": move_buckets_by_horizon(),
        "version": HEAD_VERSION,
        "note": "Directional big-up/down confirmation heads by horizon; not direct trade triggers.",
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    joblib.dump(bundle, OUT)
    print(f"Saved {OUT}")


if __name__ == "__main__":
    main()
