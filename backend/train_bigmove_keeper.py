"""
Train horizon-aware P(big_move) keeper heads.

big_move = absolute close-to-close move is at least the horizon-specific
meaningful-move boundary. Full quiet / meaningful / large / extreme bucket
metadata is saved with the model bundle for live interpretation.
"""
from __future__ import annotations

import os

import joblib
import numpy as np
import pandas as pd

from keeper_head_training import (
    BUCKET_TAG, FEATURES, HORIZONS, TRAIN_DAYS_TAG, derive_buckets_bps,
    train_split_frac,
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
    "bigmove_keeper_model.pkl",
)
HEAD_VERSION = f"2026-07-03-bigmove-keeper5-bpslabels-horizons-iso-split-{TRAIN_DAYS_TAG}-{BUCKET_TAG}"


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
    # BPS labels (2026-07-03): thresholds + per-row labels in bps of each row's own price, so the
    # label means the same thing at $15k and $115k (mandatory for 1200-1500d training windows).
    buckets = derive_buckets_bps(close, fit_frac=train_split_frac())      # auto-derived p75/p90/p97 of RELATIVE moves (bps)
    px_now = float(close[-1])

    models = {}
    for h in HORIZONS:
        if h not in buckets:
            continue
        threshold = buckets[h][0]            # bps
        delta = future_close_delta(close, h)
        rel = rel_bps(np.abs(delta), close)
        mask = np.isfinite(rel)
        y = (rel[mask] >= threshold).astype(int)
        model = fit_binary_head(X_all[mask], y, horizon_bars=h)
        if model:
            models[int(h)] = model
        print(f"big_move_{h}m >= {threshold:.1f}bps (~${threshold*px_now/1e4:.0f} now): {model_summary(model)}")

    bundle = {
        "models": models,
        "features": FEATURES,
        "horizons": sorted(models),
        "label_units": "bps",
        # Provenance for the TARGET DEFINITION: which span the p75/p90/p97 came from.
        # Without it a bundle cannot show whether its labels were defined using the
        # span it was later scored on.
        "threshold_fit_frac": train_split_frac(),
        "move_threshold_bps_by_horizon": {h: v[0] for h, v in buckets.items()},
        "move_buckets_bps_by_horizon": buckets,
        # back-compat display keys: the $-equivalent AT THE LATEST TRAINING PRICE (informational only)
        "move_threshold_usd_by_horizon": {h: round(v[0] * px_now / 1e4) for h, v in buckets.items()},
        "move_buckets_usd_by_horizon": {h: tuple(round(x * px_now / 1e4) for x in v) for h, v in buckets.items()},
        "version": HEAD_VERSION,
        "note": "P(big_move|keepers): abs future close move >= horizon meaningful bucket (BPS-relative labels). Calibrated per horizon.",
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
