"""Regression tests for causal labels, calibration and relearn promotion."""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    from dead_feature_classifier import classify
    from features import (
        FEATURE_NAMES,
        _ffill_zeros,
        build_features_from_klines,
        build_sequences,
        compute_adaptive_threshold_series,
    )
    from model import LOOKBACK, _purged_calibration_splits
    from model_contract import MODEL_FEATURE_NAMES, MODEL_NUM_FEATURES
    from model_promotion import promotion_required
    from research.anchor_multihead_bakeoff import fold_matrices

    rng = np.random.default_rng(20260731)
    closes = 60_000.0 + np.cumsum(rng.normal(0.0, 4.0, 2_000))
    atr_values = np.abs(rng.normal(30.0, 5.0, 2_000))
    original = compute_adaptive_threshold_series(closes, atr_values)

    extended_closes = np.concatenate([closes, np.full(100, 120_000.0)])
    extended_atr = np.concatenate([atr_values, np.full(100, 5_000.0)])
    extended = compute_adaptive_threshold_series(extended_closes, extended_atr)
    assert np.allclose(original, extended[:len(original)])
    assert np.isfinite(original).all()
    assert np.all((original >= 0.0) & (original <= 0.003))
    assert np.array_equal(
        _ffill_zeros(np.array([0.0, 0.0, 10.0, 0.0, 12.0])),
        np.array([0.0, 0.0, 10.0, 10.0, 12.0]),
    )

    epoch = 1_786_000_000_000
    base_klines = []
    base_close = 60_000.0 + np.cumsum(rng.normal(0.0, 5.0, 220))
    for index, close in enumerate(base_close):
        base_klines.append({
            "time": epoch + index * 60_000,
            "open": float(close - 1.0),
            "high": float(close + 4.0),
            "low": float(close - 4.0),
            "close": float(close),
            "volume": float(10.0 + abs(rng.normal())),
        })
    base_cvd = rng.normal(0.0, 5.0, len(base_klines))
    base_features = build_features_from_klines(
        base_klines,
        signal_history={"cvd_1m": base_cvd},
    )
    future_klines = list(base_klines)
    future_close = 120_000.0
    for offset in range(30):
        future_klines.append({
            "time": epoch + (220 + offset) * 60_000,
            "open": future_close,
            "high": future_close + 2_000.0,
            "low": future_close - 2_000.0,
            "close": future_close,
            "volume": 100_000.0,
        })
    extended_features = build_features_from_klines(
        future_klines,
        signal_history={
            "cvd_1m": np.concatenate([base_cvd, np.full(30, 1_000_000.0)]),
        },
    )
    assert np.allclose(base_features, extended_features[:len(base_features)])

    close_path = np.full(100, 100.0)
    high_path = close_path.copy()
    low_path = close_path.copy()
    high_path[6] = 101.0
    low_path[6] = 99.0
    _, dual_touch_labels = build_sequences(
        np.zeros((99, 2), dtype=np.float32),
        close_path,
        lookback=5,
        horizons=[5],
        atr_arr=np.full(100, 1.0),
        highs=high_path,
        lows=low_path,
    )
    assert int(np.argmax(dual_touch_labels[5][0])) == 1

    labels = np.resize(np.array([0, 1, 2], dtype=np.int64), 3_000)
    splits = _purged_calibration_splits(labels, horizon=15)
    assert len(splits) >= 2
    for train_idx, validation_idx in splits:
        assert train_idx[-1] + LOOKBACK + 15 < validation_idx[0]
        assert set(labels[train_idx]) == {0, 1, 2}
        assert set(labels[validation_idx]) == {0, 1, 2}
    assert not _purged_calibration_splits(np.resize([0, 2], 3_000), horizon=5)

    import pandas as pd

    train_frame = pd.DataFrame({"feature": [1.0, np.nan, 3.0]})
    test_frame = pd.DataFrame({"feature": [1_000.0, np.nan]})
    train_matrix, test_matrix = fold_matrices(
        train_frame,
        test_frame,
        features=["feature"],
    )
    assert train_matrix[:, 0].tolist() == [1.0, 2.0, 3.0]
    assert test_matrix[:, 0].tolist() == [1_000.0, 2.0]

    classified = classify()
    retired = {
        "regime_transition_prob",
        "regime_entropy",
        "vol_forecast_1m",
        "vol_forecast_5m",
        "vol_forecast_15m",
        "mtf_support_distance",
    }
    assert retired <= set(FEATURE_NAMES)
    assert all(classified[name][1] == "RETIRE" for name in retired)
    assert retired.isdisjoint(MODEL_FEATURE_NAMES)
    assert MODEL_NUM_FEATURES == 63

    for reason in ("forced-startup", "manual-ui", "scheduled", "auto-learning"):
        assert promotion_required(True, reason)
        assert not promotion_required(False, reason)

    print("training-integrity-20260731: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
