#!/usr/bin/env python
"""Leak-safe 120-day LONG, SHORT, and ACT/SKIP research lane.

This script is deliberately isolated from the live application. It:

* uses the latest requested slice of ``research_matrix_1m.parquet``;
* creates LONG/SHORT profitability labels after round-trip costs;
* produces purged expanding-window out-of-fold base-model probabilities;
* trains ACT/SKIP meta-models only on earlier out-of-fold predictions;
* evaluates economic results on non-overlapping decision timestamps; and
* writes models and evidence into a run-specific research directory.

It does not fit a dynamic-exit policy. That experiment is closed until
fundamentally new causal execution information is available.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

# Manifest written in the same step as the artifact; see
# backend/test_trainers_write_manifests.py for why this is a gate.
from verified_io import write_manifest as write_integrity_manifest

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_MATRIX = DATA_DIR / "research_matrix_1m.parquet"
DEFAULT_OUTPUT_ROOT = DATA_DIR / "research" / "trade_policy_heads_120d"
DEFAULT_MODELS = ("logreg", "histgb", "extra_trees")
OPTIONAL_MODELS = ("xgboost", "lightgbm", "catboost")
META_CONTEXT_CANDIDATES = (
    "return_1m_bps",
    "ret_sum_5m_bps",
    "ret_sum_15m_bps",
    "realized_vol_15m_bps",
    "realized_vol_60m_bps",
    "volume_z_60m",
    "trade_count_z_60m",
    "taker_imbalance",
    "taker_delta_ratio_15m",
    "path_efficiency_15m",
    "rv_term",
    "compression_ratio",
    "vpin",
)

_LOG_FILE: Path | None = None


@dataclass(frozen=True)
class RunConfig:
    matrix: str
    days: int
    horizons: list[int]
    models: list[str]
    meta_models: list[str]
    folds: int
    test_days: int
    fee_bps_per_side: float
    slippage_bps_per_side: float
    funding_bps_per_trade: float
    act_threshold: float
    max_train_rows: int
    max_features: int
    threads: int
    save_models: bool
    run_name: str

    @property
    def round_trip_cost_bps(self) -> float:
        return (
            2.0 * (self.fee_bps_per_side + self.slippage_bps_per_side)
            + self.funding_bps_per_trade
        )


@dataclass(frozen=True)
class Fold:
    number: int
    train_idx: np.ndarray
    test_idx: np.ndarray
    train_start_ms: int
    train_end_ms: int
    test_start_ms: int
    test_end_ms: int


def log(message: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {message}"
    print(line, flush=True)
    if _LOG_FILE is not None:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _numeric(frame: pd.DataFrame, name: str, default: float = 0.0) -> pd.Series:
    if name not in frame:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce")


def safe_div(numerator: Any, denominator: Any, default: float = 0.0) -> Any:
    num, den = np.broadcast_arrays(
        np.asarray(numerator, dtype=float),
        np.asarray(denominator, dtype=float),
    )
    output = np.full(num.shape, default, dtype=float)
    valid = np.isfinite(num) & np.isfinite(den) & (np.abs(den) > 1e-12)
    np.divide(num, den, out=output, where=valid)
    return output


def load_recent_matrix(path: Path, days: int) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"research matrix not found: {path}")
    timestamps = pd.read_parquet(path, columns=["ts_ms"])
    if timestamps.empty:
        raise ValueError("research matrix is empty")
    max_ts = int(pd.to_numeric(timestamps["ts_ms"], errors="coerce").max())
    cutoff = max_ts - int(days) * 86_400_000
    try:
        frame = pd.read_parquet(path, filters=[("ts_ms", ">=", cutoff)])
    except Exception:  # noqa: BLE001 - retry without predicate pushdown
        frame = pd.read_parquet(path)
        frame = frame[pd.to_numeric(frame["ts_ms"], errors="coerce") >= cutoff]
    required = {
        "ts_ms",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "trade_count",
        "taker_buy",
        "taker_sell",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"research matrix missing required columns: {missing}")
    frame = (
        frame.sort_values("ts_ms")
        .drop_duplicates("ts_ms", keep="last")
        .reset_index(drop=True)
    )
    if len(frame) < 10_000:
        raise ValueError(f"too few matrix rows for this experiment: {len(frame):,}")
    gaps = pd.to_numeric(frame["ts_ms"], errors="coerce").diff().dropna()
    gap_rate = float((gaps != 60_000).mean()) if len(gaps) else 0.0
    log(
        f"[data] rows={len(frame):,} range="
        f"{pd.to_datetime(frame.ts_ms.iloc[0], unit='ms', utc=True)} -> "
        f"{pd.to_datetime(frame.ts_ms.iloc[-1], unit='ms', utc=True)} "
        f"non_1m_gap_rate={gap_rate:.5%}"
    )
    return frame


def build_causal_features(frame: pd.DataFrame) -> pd.DataFrame:
    close = _numeric(frame, "close")
    open_ = _numeric(frame, "open")
    high = _numeric(frame, "high")
    low = _numeric(frame, "low")
    volume = _numeric(frame, "volume")
    trade_count = _numeric(frame, "trade_count")
    taker_buy = _numeric(frame, "taker_buy")
    taker_sell = _numeric(frame, "taker_sell")
    total_taker = (taker_buy + taker_sell).replace(0.0, np.nan)

    result = pd.DataFrame(index=frame.index)
    result["return_1m_bps"] = close.pct_change(fill_method=None) * 10_000.0
    result["range_1m_bps"] = safe_div(high - low, close, 0.0) * 10_000.0
    result["body_1m_bps"] = safe_div(close - open_, open_, 0.0) * 10_000.0
    result["close_position"] = safe_div(close - low, high - low, 0.5)
    result["log_volume"] = np.log1p(volume.clip(lower=0.0))
    result["log_trade_count"] = np.log1p(trade_count.clip(lower=0.0))
    result["taker_imbalance"] = safe_div(taker_buy - taker_sell, total_taker, 0.0)

    for window in (3, 5, 10, 15, 30, 60, 120, 240):
        minimum = max(2, window // 3)
        returns = result["return_1m_bps"]
        result[f"ret_sum_{window}m_bps"] = returns.rolling(
            window, min_periods=minimum
        ).sum()
        result[f"realized_vol_{window}m_bps"] = returns.rolling(
            window, min_periods=minimum
        ).std()
        rolling_high = high.rolling(window, min_periods=minimum).max()
        rolling_low = low.rolling(window, min_periods=minimum).min()
        result[f"range_{window}m_bps"] = (
            safe_div(rolling_high - rolling_low, close, 0.0) * 10_000.0
        )
        result[f"position_{window}m"] = safe_div(
            close - rolling_low, rolling_high - rolling_low, 0.5
        )
        result[f"volume_z_{window}m"] = safe_div(
            volume - volume.rolling(window, min_periods=minimum).mean(),
            volume.rolling(window, min_periods=minimum).std(),
            0.0,
        )
        result[f"trade_count_z_{window}m"] = safe_div(
            trade_count - trade_count.rolling(window, min_periods=minimum).mean(),
            trade_count.rolling(window, min_periods=minimum).std(),
            0.0,
        )
        delta = taker_buy - taker_sell
        result[f"taker_delta_ratio_{window}m"] = safe_div(
            delta.rolling(window, min_periods=minimum).sum(),
            total_taker.rolling(window, min_periods=minimum).sum(),
            0.0,
        )
        path = returns.abs().rolling(window, min_periods=minimum).sum()
        result[f"path_efficiency_{window}m"] = safe_div(
            result[f"ret_sum_{window}m_bps"].abs(), path, 0.0
        )

    excluded = {
        "ts_ms",
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "trade_count",
        "taker_buy",
        "taker_sell",
        "ret_5m",
        "tradable_move_label",
        "fail_fast_label",
    }
    extra_columns: dict[str, pd.Series] = {}
    for column in frame.columns:
        if column in excluded or column.startswith(("future_", "target_")):
            continue
        if column in result:
            continue
        series = pd.to_numeric(frame[column], errors="coerce")
        if series.notna().any():
            extra_columns[column] = series

    timestamp = pd.to_datetime(frame["ts_ms"], unit="ms", utc=True)
    minute_of_day = timestamp.dt.hour * 60 + timestamp.dt.minute
    time_columns = pd.DataFrame(
        {
            "session_sin": np.sin(2 * np.pi * minute_of_day / 1440.0),
            "session_cos": np.cos(2 * np.pi * minute_of_day / 1440.0),
            "weekday_sin": np.sin(2 * np.pi * timestamp.dt.dayofweek / 7.0),
            "weekday_cos": np.cos(2 * np.pi * timestamp.dt.dayofweek / 7.0),
            "is_weekend": timestamp.dt.dayofweek.ge(5).astype(float),
        },
        index=frame.index,
    )
    parts = [result]
    if extra_columns:
        parts.append(pd.DataFrame(extra_columns, index=frame.index))
    parts.append(time_columns)
    return pd.concat(parts, axis=1).replace([np.inf, -np.inf], np.nan).copy()


def select_features(
    features: pd.DataFrame, train_idx: np.ndarray, max_features: int
) -> list[str]:
    selected: list[str] = []
    train = features.iloc[train_idx]
    for column in features.columns:
        series = train[column]
        if series.notna().mean() < 0.60:
            continue
        if series.nunique(dropna=True) <= 1:
            continue
        selected.append(column)
    if max_features > 0:
        selected = selected[:max_features]
    if not selected:
        raise ValueError("no usable causal features")
    return selected


def build_side_labels(
    frame: pd.DataFrame, horizon: int, round_trip_cost_bps: float
) -> pd.DataFrame:
    close = _numeric(frame, "close")
    timestamps = pd.to_numeric(frame["ts_ms"], errors="coerce")
    future_close = close.shift(-horizon)
    future_ts = timestamps.shift(-horizon)
    exact_horizon = (future_ts - timestamps) == horizon * 60_000
    gross_return_bps = (future_close / close - 1.0) * 10_000.0
    long_net = gross_return_bps - round_trip_cost_bps
    short_net = -gross_return_bps - round_trip_cost_bps
    labels = pd.DataFrame(
        {
            "gross_return_bps": gross_return_bps,
            "long_net_bps": long_net,
            "short_net_bps": short_net,
            "long_profitable": (long_net > 0.0).astype(float),
            "short_profitable": (short_net > 0.0).astype(float),
            "valid": exact_horizon & gross_return_bps.notna(),
        },
        index=frame.index,
    )
    labels.loc[~labels["valid"], ["long_profitable", "short_profitable"]] = np.nan
    return labels


def make_expanding_folds(
    timestamps_ms: np.ndarray,
    *,
    folds: int,
    test_days: int,
    embargo_minutes: int,
) -> list[Fold]:
    if folds < 2:
        raise ValueError("at least two folds are required for causal ACT/SKIP testing")
    if test_days < 1:
        raise ValueError("test_days must be positive")
    timestamps = np.asarray(timestamps_ms, dtype=np.int64)
    first = int(timestamps.min())
    last_exclusive = int(timestamps.max()) + 60_000
    test_span = int(test_days) * 86_400_000
    total_test_span = folds * test_span
    first_test_start = last_exclusive - total_test_span
    if first_test_start <= first:
        raise ValueError("not enough history for requested folds and test_days")

    output: list[Fold] = []
    embargo_ms = int(embargo_minutes) * 60_000
    for number in range(1, folds + 1):
        test_start = first_test_start + (number - 1) * test_span
        test_end = min(test_start + test_span, last_exclusive)
        train_end = test_start - embargo_ms
        train_idx = np.flatnonzero(timestamps < train_end)
        test_idx = np.flatnonzero(
            (timestamps >= test_start) & (timestamps < test_end)
        )
        if not len(train_idx) or not len(test_idx):
            raise ValueError(f"empty train/test partition for fold {number}")
        output.append(
            Fold(
                number=number,
                train_idx=train_idx,
                test_idx=test_idx,
                train_start_ms=int(timestamps[train_idx[0]]),
                train_end_ms=int(timestamps[train_idx[-1]]),
                test_start_ms=int(timestamps[test_idx[0]]),
                test_end_ms=int(timestamps[test_idx[-1]]),
            )
        )
    return output


def parse_model_names(value: str) -> list[str]:
    names = [part.strip().lower() for part in value.split(",") if part.strip()]
    if names == ["core"]:
        return list(DEFAULT_MODELS)
    if names == ["all"]:
        return list(DEFAULT_MODELS + OPTIONAL_MODELS)
    allowed = set(DEFAULT_MODELS + OPTIONAL_MODELS)
    unknown = sorted(set(names) - allowed)
    if unknown:
        raise ValueError(f"unknown model families: {unknown}")
    if not names:
        raise ValueError("at least one model family is required")
    return names


def model_factory(name: str, threads: int) -> Callable[[], Any]:
    if name == "logreg":
        return lambda: Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        max_iter=600,
                        C=0.25,
                        random_state=41,
                    ),
                ),
            ]
        )
    if name == "histgb":
        return lambda: HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=140,
            max_depth=5,
            min_samples_leaf=50,
            l2_regularization=0.2,
            random_state=42,
        )
    if name == "extra_trees":
        return lambda: ExtraTreesClassifier(
            n_estimators=180,
            max_depth=10,
            min_samples_leaf=40,
            max_features="sqrt",
            n_jobs=threads,
            random_state=43,
        )
    if name == "xgboost":
        def make_xgb() -> Any:
            import xgboost as xgb

            return xgb.XGBClassifier(
                n_estimators=200,
                max_depth=4,
                learning_rate=0.03,
                subsample=0.8,
                colsample_bytree=0.8,
                n_jobs=threads,
                tree_method="hist",
                eval_metric="logloss",
                random_state=44,
            )

        return make_xgb
    if name == "lightgbm":
        def make_lgbm() -> Any:
            import lightgbm as lgb

            return lgb.LGBMClassifier(
                n_estimators=200,
                max_depth=5,
                learning_rate=0.03,
                subsample=0.8,
                colsample_bytree=0.8,
                n_jobs=threads,
                verbosity=-1,
                random_state=45,
            )

        return make_lgbm
    if name == "catboost":
        def make_catboost() -> Any:
            from catboost import CatBoostClassifier

            return CatBoostClassifier(
                iterations=200,
                depth=5,
                learning_rate=0.03,
                loss_function="Logloss",
                thread_count=threads,
                allow_writing_files=False,
                verbose=False,
                random_seed=46,
            )

        return make_catboost
    raise ValueError(f"unknown model family: {name}")


def positive_probability(model: Any, matrix: np.ndarray) -> np.ndarray:
    probability = np.asarray(model.predict_proba(matrix), dtype=float)
    classes = list(getattr(model, "classes_", [0, 1]))
    if isinstance(model, Pipeline):
        classes = list(getattr(model.named_steps["model"], "classes_", classes))
    if 1 not in classes:
        raise ValueError(f"model has no positive class: {classes}")
    return probability[:, classes.index(1)]


def impute_train_test(
    features: pd.DataFrame,
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    columns: list[str],
) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    train = features.iloc[train_idx][columns]
    test = features.iloc[test_idx][columns]
    medians = train.median(numeric_only=True).fillna(0.0)
    train_values = train.fillna(medians).fillna(0.0).to_numpy(dtype=np.float32)
    test_values = test.fillna(medians).fillna(0.0).to_numpy(dtype=np.float32)
    return train_values, test_values, {
        column: float(medians.get(column, 0.0)) for column in columns
    }


def safe_auc(y_true: np.ndarray, probability: np.ndarray) -> float:
    if len(np.unique(y_true)) < 2:
        return math.nan
    return float(roc_auc_score(y_true, probability))


def expected_calibration_error(
    y_true: np.ndarray, probability: np.ndarray, bins: int = 10
) -> float:
    y = np.asarray(y_true, dtype=float)
    p = np.clip(np.asarray(probability, dtype=float), 1e-6, 1.0 - 1e-6)
    if not len(y):
        return math.nan
    edges = np.linspace(0.0, 1.0, bins + 1)
    value = 0.0
    for index in range(bins):
        if index == bins - 1:
            mask = (p >= edges[index]) & (p <= edges[index + 1])
        else:
            mask = (p >= edges[index]) & (p < edges[index + 1])
        if mask.any():
            value += float(mask.mean()) * abs(float(y[mask].mean() - p[mask].mean()))
    return value


def wilson_lower_bound(successes: int, count: int, z: float = 1.96) -> float:
    if count <= 0:
        return math.nan
    rate = successes / count
    denominator = 1.0 + z * z / count
    centre = rate + z * z / (2.0 * count)
    spread = z * math.sqrt(
        rate * (1.0 - rate) / count + z * z / (4.0 * count * count)
    )
    return (centre - spread) / denominator


def binary_metrics(
    y_true: np.ndarray, probability: np.ndarray
) -> dict[str, float | int]:
    y = np.asarray(y_true, dtype=int)
    p = np.clip(np.asarray(probability, dtype=float), 1e-6, 1.0 - 1e-6)
    prediction = (p >= 0.5).astype(int)
    return {
        "n": len(y),
        "positive_rate": float(y.mean()) if len(y) else math.nan,
        "auc": safe_auc(y, p),
        "average_precision": float(average_precision_score(y, p)),
        "accuracy": float(accuracy_score(y, prediction)),
        "balanced_accuracy": float(balanced_accuracy_score(y, prediction)),
        "precision": float(precision_score(y, prediction, zero_division=0)),
        "recall": float(recall_score(y, prediction, zero_division=0)),
        "f1": float(f1_score(y, prediction, zero_division=0)),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "ece_10bin": expected_calibration_error(y, p),
    }


def economic_metrics(
    timestamp_ms: np.ndarray, net_bps: np.ndarray, act_mask: np.ndarray
) -> dict[str, float | int]:
    timestamps = np.asarray(timestamp_ms, dtype=np.int64)
    pnl = np.asarray(net_bps, dtype=float)
    act = np.asarray(act_mask, dtype=bool)
    selected = pnl[act]
    if not len(selected):
        return {
            "trades": 0,
            "coverage": 0.0,
            "win_rate": math.nan,
            "wilson_lb": math.nan,
            "mean_net_bps": math.nan,
            "total_net_bps": 0.0,
            "profit_factor": math.nan,
            "max_drawdown_bps": 0.0,
            "daily_sharpe": math.nan,
        }
    wins = int((selected > 0.0).sum())
    gains = float(selected[selected > 0.0].sum())
    losses = float(-selected[selected < 0.0].sum())
    equity = np.cumsum(selected)
    peaks = np.maximum.accumulate(np.r_[0.0, equity])
    drawdown = np.r_[0.0, equity] - peaks
    selected_dates = pd.to_datetime(timestamps[act], unit="ms", utc=True).date
    daily = pd.Series(selected).groupby(selected_dates).sum()
    daily_sharpe = math.nan
    if len(daily) > 1 and float(daily.std(ddof=1)) > 0.0:
        daily_sharpe = float(
            daily.mean() / daily.std(ddof=1) * math.sqrt(365.0)
        )
    return {
        "trades": len(selected),
        "coverage": float(act.mean()),
        "win_rate": float(wins / len(selected)),
        "wilson_lb": wilson_lower_bound(wins, len(selected)),
        "mean_net_bps": float(selected.mean()),
        "total_net_bps": float(selected.sum()),
        "profit_factor": float(gains / losses) if losses > 0.0 else math.inf,
        "max_drawdown_bps": float(-drawdown.min()),
        "daily_sharpe": daily_sharpe,
    }


def _metric_row(
    *,
    horizon: int,
    fold: int | str,
    layer: str,
    target: str,
    model: str,
    metrics: dict[str, Any],
) -> dict[str, Any]:
    return {
        "horizon": horizon,
        "fold": fold,
        "layer": layer,
        "target": target,
        "model": model,
        **metrics,
    }


def _decision_indices(test_idx: np.ndarray, timestamps: np.ndarray, horizon: int) -> np.ndarray:
    minute_number = timestamps[test_idx] // 60_000
    return test_idx[(minute_number % horizon) == 0]


def _fit_meta_fold(
    *,
    family: str,
    prior: pd.DataFrame,
    current: pd.DataFrame,
    feature_columns: list[str],
    threads: int,
) -> np.ndarray:
    train = prior[feature_columns].replace([np.inf, -np.inf], np.nan)
    test = current[feature_columns].replace([np.inf, -np.inf], np.nan)
    medians = train.median(numeric_only=True).fillna(0.0)
    train_values = train.fillna(medians).fillna(0.0).to_numpy(dtype=np.float32)
    test_values = test.fillna(medians).fillna(0.0).to_numpy(dtype=np.float32)
    target = prior["candidate_profitable"].to_numpy(dtype=np.int8)
    if len(np.unique(target)) < 2:
        raise ValueError("ACT/SKIP training target has one class")
    model = model_factory(family, threads)()
    model.fit(train_values, target)
    probability = positive_probability(model, test_values)
    del model, train_values, test_values
    gc.collect()
    return probability


def run_research(config: RunConfig, output_dir: Path) -> dict[str, Any]:
    started = time.monotonic()
    matrix_path = Path(config.matrix).resolve()
    frame = load_recent_matrix(matrix_path, config.days)
    timestamps = pd.to_numeric(frame["ts_ms"], errors="raise").to_numpy(np.int64)
    features = build_causal_features(frame)
    metrics_rows: list[dict[str, Any]] = []
    prediction_parts: list[pd.DataFrame] = []
    selected_features_by_horizon: dict[str, list[str]] = {}
    skipped_models: dict[str, str] = {}

    for horizon in config.horizons:
        log(f"[h={horizon}m] building cost-aware labels")
        labels = build_side_labels(frame, horizon, config.round_trip_cost_bps)
        folds = make_expanding_folds(
            timestamps,
            folds=config.folds,
            test_days=config.test_days,
            embargo_minutes=horizon,
        )
        first_train = folds[0].train_idx
        first_train = first_train[labels.iloc[first_train]["valid"].to_numpy(bool)]
        feature_columns = select_features(features, first_train, config.max_features)
        selected_features_by_horizon[str(horizon)] = feature_columns
        context_columns = [
            column for column in META_CONTEXT_CANDIDATES if column in feature_columns
        ]
        prior_meta_parts: list[pd.DataFrame] = []
        horizon_predictions: list[pd.DataFrame] = []
        log(
            f"[h={horizon}m] features={len(feature_columns)} "
            f"positive_rates=LONG {labels.long_profitable.mean():.2%} "
            f"SHORT {labels.short_profitable.mean():.2%}"
        )

        for fold in folds:
            fold_started = time.monotonic()
            train_idx = fold.train_idx[labels.iloc[fold.train_idx]["valid"].to_numpy(bool)]
            if config.max_train_rows > 0 and len(train_idx) > config.max_train_rows:
                train_idx = train_idx[-config.max_train_rows :]
            decision_idx = _decision_indices(fold.test_idx, timestamps, horizon)
            decision_idx = decision_idx[
                labels.iloc[decision_idx]["valid"].to_numpy(bool)
            ]
            if not len(train_idx) or not len(decision_idx):
                raise ValueError(f"h={horizon} fold={fold.number} has no valid rows")
            x_train, x_test, _ = impute_train_test(
                features, train_idx, decision_idx, feature_columns
            )
            fold_output = pd.DataFrame(
                {
                    "timestamp_ms": timestamps[decision_idx],
                    "timestamp": pd.to_datetime(
                        timestamps[decision_idx], unit="ms", utc=True
                    ),
                    "horizon": horizon,
                    "fold": fold.number,
                    "gross_return_bps": labels.iloc[decision_idx][
                        "gross_return_bps"
                    ].to_numpy(float),
                    "long_net_bps": labels.iloc[decision_idx][
                        "long_net_bps"
                    ].to_numpy(float),
                    "short_net_bps": labels.iloc[decision_idx][
                        "short_net_bps"
                    ].to_numpy(float),
                    "long_profitable": labels.iloc[decision_idx][
                        "long_profitable"
                    ].to_numpy(np.int8),
                    "short_profitable": labels.iloc[decision_idx][
                        "short_profitable"
                    ].to_numpy(np.int8),
                }
            )
            for column in context_columns:
                fold_output[column] = features.iloc[decision_idx][column].to_numpy()

            successful_models: list[str] = []
            for family in config.models:
                try:
                    factory = model_factory(family, config.threads)
                    side_probabilities: dict[str, np.ndarray] = {}
                    for side in ("long", "short"):
                        target = labels.iloc[train_idx][
                            f"{side}_profitable"
                        ].to_numpy(np.int8)
                        if len(np.unique(target)) < 2:
                            raise ValueError(f"{side} training target has one class")
                        model_started = time.monotonic()
                        model = factory()
                        model.fit(x_train, target)
                        probability = positive_probability(model, x_test)
                        side_probabilities[side] = probability
                        fold_output[f"p_{side}_{family}"] = probability
                        metrics_rows.append(
                            _metric_row(
                                horizon=horizon,
                                fold=fold.number,
                                layer="side_head",
                                target=side.upper(),
                                model=family,
                                metrics=binary_metrics(
                                    fold_output[f"{side}_profitable"].to_numpy(),
                                    probability,
                                ),
                            )
                        )
                        log(
                            f"[h={horizon}m fold={fold.number}/{config.folds}] "
                            f"{side.upper()} {family} fitted in "
                            f"{time.monotonic() - model_started:.1f}s"
                        )
                        del model
                        gc.collect()
                    successful_models.append(family)
                except Exception as exc:  # noqa: BLE001 - optional model boundary
                    key = f"h{horizon}_fold{fold.number}_{family}"
                    skipped_models[key] = str(exc)
                    log(f"[skip] {key}: {exc}")

            if not successful_models:
                raise RuntimeError(
                    f"no base models succeeded for h={horizon} fold={fold.number}"
                )
            long_columns = [f"p_long_{name}" for name in successful_models]
            short_columns = [f"p_short_{name}" for name in successful_models]
            fold_output["p_long_ensemble"] = fold_output[long_columns].mean(axis=1)
            fold_output["p_short_ensemble"] = fold_output[short_columns].mean(axis=1)
            fold_output["long_disagreement"] = fold_output[long_columns].std(
                axis=1, ddof=0
            )
            fold_output["short_disagreement"] = fold_output[short_columns].std(
                axis=1, ddof=0
            )
            fold_output["candidate_side"] = np.where(
                fold_output["p_long_ensemble"] >= fold_output["p_short_ensemble"],
                "LONG",
                "SHORT",
            )
            fold_output["candidate_is_long"] = (
                fold_output["candidate_side"] == "LONG"
            ).astype(float)
            fold_output["selected_probability"] = np.where(
                fold_output["candidate_side"] == "LONG",
                fold_output["p_long_ensemble"],
                fold_output["p_short_ensemble"],
            )
            fold_output["probability_gap"] = (
                fold_output["p_long_ensemble"] - fold_output["p_short_ensemble"]
            ).abs()
            fold_output["candidate_net_bps"] = np.where(
                fold_output["candidate_side"] == "LONG",
                fold_output["long_net_bps"],
                fold_output["short_net_bps"],
            )
            fold_output["candidate_profitable"] = (
                fold_output["candidate_net_bps"] > 0.0
            ).astype(np.int8)
            fold_output["direction_correct"] = np.where(
                fold_output["candidate_side"] == "LONG",
                fold_output["gross_return_bps"] > 0.0,
                fold_output["gross_return_bps"] < 0.0,
            ).astype(np.int8)

            for side in ("long", "short"):
                metrics_rows.append(
                    _metric_row(
                        horizon=horizon,
                        fold=fold.number,
                        layer="side_head",
                        target=side.upper(),
                        model="mean_ensemble",
                        metrics=binary_metrics(
                            fold_output[f"{side}_profitable"].to_numpy(),
                            fold_output[f"p_{side}_ensemble"].to_numpy(),
                        ),
                    )
                )

            always_act = np.ones(len(fold_output), dtype=bool)
            metrics_rows.append(
                _metric_row(
                    horizon=horizon,
                    fold=fold.number,
                    layer="economic",
                    target="ACT/SKIP",
                    model="always_act",
                    metrics=economic_metrics(
                        fold_output["timestamp_ms"].to_numpy(),
                        fold_output["candidate_net_bps"].to_numpy(),
                        always_act,
                    ),
                )
            )

            meta_features = [
                "p_long_ensemble",
                "p_short_ensemble",
                "selected_probability",
                "probability_gap",
                "long_disagreement",
                "short_disagreement",
                "candidate_is_long",
                *context_columns,
            ]
            if prior_meta_parts:
                prior = pd.concat(prior_meta_parts, ignore_index=True)
                for family in config.meta_models:
                    try:
                        probability = _fit_meta_fold(
                            family=family,
                            prior=prior,
                            current=fold_output,
                            feature_columns=meta_features,
                            threads=config.threads,
                        )
                        fold_output[f"p_act_{family}"] = probability
                        act = probability >= config.act_threshold
                        fold_output[f"act_{family}"] = act
                        metrics_rows.append(
                            _metric_row(
                                horizon=horizon,
                                fold=fold.number,
                                layer="act_skip_head",
                                target="ACT",
                                model=family,
                                metrics=binary_metrics(
                                    fold_output["candidate_profitable"].to_numpy(),
                                    probability,
                                ),
                            )
                        )
                        economic = economic_metrics(
                            fold_output["timestamp_ms"].to_numpy(),
                            fold_output["candidate_net_bps"].to_numpy(),
                            act,
                        )
                        economic["skip_avoid_rate"] = float(
                            (
                                fold_output.loc[
                                    ~act, "candidate_net_bps"
                                ].to_numpy(float)
                                <= 0.0
                            ).mean()
                        ) if (~act).any() else math.nan
                        metrics_rows.append(
                            _metric_row(
                                horizon=horizon,
                                fold=fold.number,
                                layer="economic",
                                target="ACT/SKIP",
                                model=f"meta_{family}",
                                metrics=economic,
                            )
                        )
                    except Exception as exc:  # noqa: BLE001 - optional model boundary
                        key = f"h{horizon}_fold{fold.number}_meta_{family}"
                        skipped_models[key] = str(exc)
                        log(f"[skip] {key}: {exc}")
            else:
                log(
                    f"[h={horizon}m fold={fold.number}] seed fold: "
                    "base OOF predictions collected; no meta score yet"
                )

            prior_meta_parts.append(fold_output.copy())
            horizon_predictions.append(fold_output)
            log(
                f"[h={horizon}m fold={fold.number}/{config.folds}] complete "
                f"train={len(train_idx):,} decisions={len(decision_idx):,} "
                f"elapsed={time.monotonic() - fold_started:.1f}s"
            )

        horizon_frame = pd.concat(horizon_predictions, ignore_index=True)
        prediction_parts.append(horizon_frame)
        for side in ("long", "short"):
            for family in [*config.models, "mean_ensemble"]:
                column = (
                    f"p_{side}_ensemble"
                    if family == "mean_ensemble"
                    else f"p_{side}_{family}"
                )
                if column not in horizon_frame:
                    continue
                valid = horizon_frame[column].notna()
                metrics_rows.append(
                    _metric_row(
                        horizon=horizon,
                        fold="POOLED",
                        layer="side_head",
                        target=side.upper(),
                        model=family,
                        metrics=binary_metrics(
                            horizon_frame.loc[
                                valid, f"{side}_profitable"
                            ].to_numpy(),
                            horizon_frame.loc[valid, column].to_numpy(),
                        ),
                    )
                )
        metrics_rows.append(
            _metric_row(
                horizon=horizon,
                fold="POOLED",
                layer="economic",
                target="ACT/SKIP",
                model="always_act",
                metrics=economic_metrics(
                    horizon_frame["timestamp_ms"].to_numpy(),
                    horizon_frame["candidate_net_bps"].to_numpy(),
                    np.ones(len(horizon_frame), dtype=bool),
                ),
            )
        )
        for family in config.meta_models:
            column = f"p_act_{family}"
            if column not in horizon_frame:
                continue
            valid = horizon_frame[column].notna()
            metrics_rows.append(
                _metric_row(
                    horizon=horizon,
                    fold="POOLED",
                    layer="act_skip_head",
                    target="ACT",
                    model=family,
                    metrics=binary_metrics(
                        horizon_frame.loc[
                            valid, "candidate_profitable"
                        ].to_numpy(),
                        horizon_frame.loc[valid, column].to_numpy(),
                    ),
                )
            )
            act = (
                horizon_frame.loc[valid, column].to_numpy()
                >= config.act_threshold
            )
            economic = economic_metrics(
                horizon_frame.loc[valid, "timestamp_ms"].to_numpy(),
                horizon_frame.loc[valid, "candidate_net_bps"].to_numpy(),
                act,
            )
            economic["skip_avoid_rate"] = float(
                (
                    horizon_frame.loc[valid, "candidate_net_bps"].to_numpy()[~act]
                    <= 0.0
                ).mean()
            ) if (~act).any() else math.nan
            metrics_rows.append(
                _metric_row(
                    horizon=horizon,
                    fold="POOLED",
                    layer="economic",
                    target="ACT/SKIP",
                    model=f"meta_{family}",
                    metrics=economic,
                )
            )

    predictions = pd.concat(prediction_parts, ignore_index=True)
    metrics = pd.DataFrame(metrics_rows)
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(output_dir / "metrics.csv", index=False)
    predictions.to_csv(output_dir / "oof_predictions.csv", index=False)
    try:
        predictions.to_parquet(output_dir / "oof_predictions.parquet", index=False)
    except Exception as exc:  # noqa: BLE001 - CSV remains canonical fallback
        log(f"[warn] parquet prediction write failed: {exc}")

    if config.save_models:
        save_errors = save_research_models(
            config=config,
            output_dir=output_dir,
            frame=frame,
            features=features,
            predictions=predictions,
            selected_features_by_horizon=selected_features_by_horizon,
        )
        skipped_models.update(save_errors)

    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "auto_deployed": False,
        "dynamic_exit_trained": False,
        "dynamic_exit_reason": (
            "CONDITIONAL_STOPPING_V1 is closed until fundamentally new causal "
            "execution information exists."
        ),
        "config": asdict(config),
        "round_trip_cost_bps": config.round_trip_cost_bps,
        "source": {
            "path": str(matrix_path),
            "bytes": matrix_path.stat().st_size,
            "mtime_ns": matrix_path.stat().st_mtime_ns,
            "sha256": sha256_file(matrix_path),
            "rows_used": len(frame),
            "first_ts_ms": int(timestamps[0]),
            "last_ts_ms": int(timestamps[-1]),
        },
        "selected_features": selected_features_by_horizon,
        "skipped_models": skipped_models,
        "elapsed_seconds": time.monotonic() - started,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, allow_nan=False, default=str),
        encoding="utf-8",
    )

    summary = build_summary(metrics)
    summary.to_csv(output_dir / "summary.csv", index=False)
    log(
        f"[done] output={output_dir} elapsed={time.monotonic() - started:.1f}s "
        f"metrics={len(metrics):,} predictions={len(predictions):,}"
    )
    return manifest


def save_research_models(
    *,
    config: RunConfig,
    output_dir: Path,
    frame: pd.DataFrame,
    features: pd.DataFrame,
    predictions: pd.DataFrame,
    selected_features_by_horizon: dict[str, list[str]],
) -> dict[str, str]:
    errors: dict[str, str] = {}
    model_dir = output_dir / "models"
    model_dir.mkdir(parents=True, exist_ok=True)
    timestamps = pd.to_numeric(frame["ts_ms"], errors="raise").to_numpy(np.int64)
    for horizon in config.horizons:
        labels = build_side_labels(frame, horizon, config.round_trip_cost_bps)
        valid_idx = np.flatnonzero(labels["valid"].to_numpy(bool))
        if config.max_train_rows > 0 and len(valid_idx) > config.max_train_rows:
            valid_idx = valid_idx[-config.max_train_rows :]
        columns = selected_features_by_horizon[str(horizon)]
        train = features.iloc[valid_idx][columns]
        medians = train.median(numeric_only=True).fillna(0.0)
        matrix = train.fillna(medians).fillna(0.0).to_numpy(dtype=np.float32)
        for family in config.models:
            for side in ("long", "short"):
                target = labels.iloc[valid_idx][f"{side}_profitable"].to_numpy(
                    np.int8
                )
                if len(np.unique(target)) < 2:
                    continue
                try:
                    model = model_factory(family, config.threads)()
                    model.fit(matrix, target)
                    artifact = {
                        "research_only": True,
                        "auto_deploy": False,
                        "model": model,
                        "family": family,
                        "side": side.upper(),
                        "horizon": horizon,
                        "features": columns,
                        "medians": {
                            column: float(medians.get(column, 0.0))
                            for column in columns
                        },
                        "round_trip_cost_bps": config.round_trip_cost_bps,
                        "train_rows": len(valid_idx),
                        "train_start_ms": int(timestamps[valid_idx[0]]),
                        "train_end_ms": int(timestamps[valid_idx[-1]]),
                    }
                    _artifact_path = model_dir / f"h{horizon}_{side}_{family}.joblib"
                    joblib.dump(artifact, _artifact_path, compress=3)
                    write_integrity_manifest(_artifact_path)
                    del model, artifact
                    gc.collect()
                except Exception as exc:  # noqa: BLE001 - optional model boundary
                    errors[f"h{horizon}_save_{side}_{family}"] = str(exc)
                    log(f"[save-skip] h={horizon} {side} {family}: {exc}")

        horizon_oof = predictions[predictions["horizon"] == horizon].copy()
        context_columns = [
            column
            for column in META_CONTEXT_CANDIDATES
            if column in horizon_oof.columns
        ]
        meta_features = [
            "p_long_ensemble",
            "p_short_ensemble",
            "selected_probability",
            "probability_gap",
            "long_disagreement",
            "short_disagreement",
            "candidate_is_long",
            *context_columns,
        ]
        for family in config.meta_models:
            train = horizon_oof[meta_features].replace([np.inf, -np.inf], np.nan)
            medians = train.median(numeric_only=True).fillna(0.0)
            matrix = train.fillna(medians).fillna(0.0).to_numpy(dtype=np.float32)
            target = horizon_oof["candidate_profitable"].to_numpy(np.int8)
            if len(np.unique(target)) < 2:
                continue
            try:
                model = model_factory(family, config.threads)()
                model.fit(matrix, target)
                artifact = {
                    "research_only": True,
                    "auto_deploy": False,
                    "model": model,
                    "family": family,
                    "target": "ACT_IF_CANDIDATE_NET_PNL_POSITIVE",
                    "horizon": horizon,
                    "features": meta_features,
                    "medians": {
                        column: float(medians.get(column, 0.0))
                        for column in meta_features
                    },
                    "act_threshold": config.act_threshold,
                    "train_rows": len(horizon_oof),
                    "training_source": "purged expanding-window OOF base predictions",
                }
                _artifact_path = model_dir / f"h{horizon}_act_skip_{family}.joblib"
                joblib.dump(artifact, _artifact_path, compress=3)
                write_integrity_manifest(_artifact_path)
                del model, artifact
                gc.collect()
            except Exception as exc:  # noqa: BLE001 - optional model boundary
                errors[f"h{horizon}_save_meta_{family}"] = str(exc)
                log(f"[save-skip] h={horizon} ACT/SKIP {family}: {exc}")
    return errors


def build_summary(metrics: pd.DataFrame) -> pd.DataFrame:
    pooled = metrics[metrics["fold"].astype(str) == "POOLED"].copy()
    preferred = [
        "horizon",
        "layer",
        "target",
        "model",
        "n",
        "auc",
        "average_precision",
        "accuracy",
        "balanced_accuracy",
        "brier",
        "ece_10bin",
        "trades",
        "coverage",
        "win_rate",
        "wilson_lb",
        "mean_net_bps",
        "total_net_bps",
        "profit_factor",
        "max_drawdown_bps",
        "daily_sharpe",
        "skip_avoid_rate",
    ]
    return pooled[[column for column in preferred if column in pooled.columns]]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX))
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--horizons", type=int, nargs="+", default=[5, 15])
    parser.add_argument("--models", default="all")
    parser.add_argument("--meta-models", default="logreg,histgb")
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--test-days", type=int, default=15)
    parser.add_argument("--fee-bps-per-side", type=float, default=5.0)
    parser.add_argument("--slippage-bps-per-side", type=float, default=1.0)
    parser.add_argument("--funding-bps-per-trade", type=float, default=0.0)
    parser.add_argument("--act-threshold", type=float, default=0.58)
    parser.add_argument("--max-train-rows", type=int, default=0)
    parser.add_argument("--max-features", type=int, default=80)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--log-file", default="")
    parser.add_argument("--save-models", action=argparse.BooleanOptionalAction, default=True)
    return parser.parse_args()


def main() -> None:
    global _LOG_FILE
    args = parse_args()
    if args.days < 7:
        raise ValueError("days must be at least 7")
    if any(horizon < 1 for horizon in args.horizons):
        raise ValueError("horizons must be positive")
    if not 0.50 <= args.act_threshold < 1.0:
        raise ValueError("act-threshold must be in [0.50, 1.0)")
    if min(
        args.fee_bps_per_side,
        args.slippage_bps_per_side,
        args.funding_bps_per_trade,
    ) < 0.0:
        raise ValueError("cost assumptions cannot be negative")
    run_name = args.run_name or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = DEFAULT_OUTPUT_ROOT / run_name
    _LOG_FILE = (
        Path(args.log_file).resolve()
        if args.log_file
        else output_dir / "run.log"
    )
    config = RunConfig(
        matrix=str(Path(args.matrix).resolve()),
        days=args.days,
        horizons=sorted(set(args.horizons)),
        models=parse_model_names(args.models),
        meta_models=parse_model_names(args.meta_models),
        folds=args.folds,
        test_days=args.test_days,
        fee_bps_per_side=args.fee_bps_per_side,
        slippage_bps_per_side=args.slippage_bps_per_side,
        funding_bps_per_trade=args.funding_bps_per_trade,
        act_threshold=args.act_threshold,
        max_train_rows=max(0, args.max_train_rows),
        max_features=max(0, args.max_features),
        threads=max(1, args.threads),
        save_models=bool(args.save_models),
        run_name=run_name,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    log(f"[start] config={json.dumps(asdict(config), sort_keys=True)}")
    log(
        f"[costs] round_trip={config.round_trip_cost_bps:.2f}bps "
        f"(fee={config.fee_bps_per_side:.2f}bps/side, "
        f"slippage={config.slippage_bps_per_side:.2f}bps/side, "
        f"funding={config.funding_bps_per_trade:.2f}bps/trade)"
    )
    run_research(config, output_dir)


if __name__ == "__main__":
    main()
