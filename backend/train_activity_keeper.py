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
    BUCKET_TAG, FEATURES, HORIZONS, TRAIN_DAYS_TAG, derive_buckets_bps,
    train_split_frac,
    fit_binary_head, future_window, model_summary, rel_bps,
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
    "activity_keeper_model.pkl",
)
HEAD_VERSION = f"2026-07-03-activity-range-keeper5-bpslabels-horizons-iso-split-{TRAIN_DAYS_TAG}-{BUCKET_TAG}"


def _ensemble():
    from keeper_head_training import ensemble
    return ensemble()


def main():
    if not os.path.exists(MATRIX):
        print(f"ERROR: {MATRIX} not found.")
        return

    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=FEATURES + ["high", "low", "close"]).copy()
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)
    X_all = df[FEATURES].values
    buckets = derive_buckets_bps(close, fit_frac=train_split_frac())      # BPS labels (2026-07-03): price-level-proof for long windows
    px_now = float(close[-1])

    models = {}
    for h in HORIZONS:
        if h not in buckets:
            continue
        threshold = buckets[h][0]            # bps
        future_high = future_window(high, h, np.max)
        future_low = future_window(low, h, np.min)
        range_rel = rel_bps(future_high - future_low, close)
        mask = np.isfinite(range_rel)
        y = (range_rel[mask] >= threshold).astype(int)
        model = fit_binary_head(X_all[mask], y, horizon_bars=h)
        if model:
            models[int(h)] = model
        print(f"activity_range_{h}m >= {threshold:.1f}bps (~${threshold*px_now/1e4:.0f} now): {model_summary(model)}")

    bundle = {
        "models": models,
        "features": FEATURES,
        "horizons": sorted(models),
        "label_units": "bps",
        # Provenance for the TARGET DEFINITION: which span the p75/p90/p97 came from.
        # Without it a bundle cannot show whether its labels were defined using the
        # span it was later scored on.
        "threshold_fit_frac": train_split_frac(),
        "range_threshold_bps_by_horizon": {h: v[0] for h, v in buckets.items()},
        "move_buckets_bps_by_horizon": buckets,
        "range_threshold_usd_by_horizon": {h: round(v[0] * px_now / 1e4) for h, v in buckets.items()},
        "move_buckets_usd_by_horizon": {h: tuple(round(x * px_now / 1e4) for x in v) for h, v in buckets.items()},
        "version": HEAD_VERSION,
        "note": "P(active_range|keepers): future high-low range >= horizon meaningful bucket. Proxy for activity, not true volume.",
    }
    if 5 in models:
        bundle.update({k: v for k, v in models[5].items() if k not in ("pipe", "iso")})
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
