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
    BUCKET_TAG, FEATURES, HORIZONS, TRAIN_DAYS_TAG, derive_buckets_bps,
    fit_binary_head, future_close_delta, model_summary, rel_bps,
)

# Manifest written in the same step as the artifact: without it the artifact reads as
# UNKNOWN identity, and phold_challenger refuses to deploy any calibrator while a source
# artifact fails identity enforcement - which disables
# PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1.
from verified_io import write_manifest as write_integrity_manifest

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
OUT = os.path.join(
    os.environ.get("BTC_MODEL_OUTPUT_DIR") or os.path.join(DATA_DIR, "saved_models"),
    "directional_keeper_model.pkl",
)
HEAD_VERSION = f"2026-07-03-directional-keeper5-bpslabels-horizons-iso-split-{TRAIN_DAYS_TAG}-{BUCKET_TAG}"


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
    buckets = derive_buckets_bps(close)      # BPS labels (2026-07-03): price-level-proof for long windows
    px_now = float(close[-1])

    models = {}
    for h in HORIZONS:
        if h not in buckets:
            continue
        threshold = buckets[h][0]            # bps
        delta_rel = rel_bps(future_close_delta(close, h), close)   # signed bps of the row's own price
        mask = np.isfinite(delta_rel)
        up = fit_binary_head(X_all[mask], (delta_rel[mask] >= threshold).astype(int),
                             horizon_bars=h)
        down = fit_binary_head(X_all[mask], (delta_rel[mask] <= -threshold).astype(int),
                               horizon_bars=h)
        row = {}
        if up:
            row["big_up"] = up
        if down:
            row["big_down"] = down
        if row:
            models[int(h)] = row
        print(f"big_up_{h}m >= +{threshold:.1f}bps (~+${threshold*px_now/1e4:.0f} now): {model_summary(up)}")
        print(f"big_down_{h}m <= -{threshold:.1f}bps (~-${threshold*px_now/1e4:.0f} now): {model_summary(down)}")

    bundle = {
        "models": models,
        "features": FEATURES,
        "horizons": sorted(models),
        "label_units": "bps",
        "move_threshold_bps_by_horizon": {h: v[0] for h, v in buckets.items()},
        "move_buckets_bps_by_horizon": buckets,
        "move_threshold_usd_by_horizon": {h: round(v[0] * px_now / 1e4) for h, v in buckets.items()},
        "move_buckets_usd_by_horizon": {h: tuple(round(x * px_now / 1e4) for x in v) for h, v in buckets.items()},
        "version": HEAD_VERSION,
        "note": "Directional big-up/down confirmation heads by horizon; not direct trade triggers.",
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    _tmp = f"{OUT}.tmp.{os.getpid()}"
    try:
        joblib.dump(bundle, _tmp)
        write_integrity_manifest(_tmp)
        os.replace(_tmp, OUT)
        write_integrity_manifest(OUT)
    finally:
        if os.path.exists(_tmp):
            os.remove(_tmp)
    print(f"Saved {OUT}")


if __name__ == "__main__":
    main()
