#!/usr/bin/env python
"""Standalone event-time specialist research lane.

This experiment uses raw Binance spot and perpetual aggregate trades at one-second
resolution. It deliberately does not import, overwrite, or promote production models.

Heads:
  * first-barrier direction: UP before DOWN, conditioned on a barrier being reached
  * movement: either barrier is reached within the horizon
  * round-trip: both barriers are reached within the horizon
  * ACT/SKIP: the frozen direction ensemble is likely to be correct and resolved

The historical cache has trades but no reconstructable limit-order queue. Results from
this script therefore measure event-time trade-flow information, not executable profit.
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import math
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from backfill_trade_features import load_aggtrades  # noqa: E402

DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
CACHE_DIR = DATA_DIR / "backfill_cache"
DEFAULT_OUTPUT_ROOT = DATA_DIR / "research" / "event_time_specialists"

SECONDS_PER_DAY = 86_400
DEFAULT_HORIZONS = (5, 15, 30, 60)
DEFAULT_BARRIERS_BPS = {5: 1.0, 15: 1.5, 30: 2.0, 60: 3.0}
TARGETS = ("direction", "movement", "roundtrip")
MODEL_NAMES = ("logreg", "histgb", "lightgbm", "catboost")
ACT_THRESHOLD = 0.65
WARMUP_SECONDS = 120

_LOG_PATH: Path | None = None


@dataclass(frozen=True)
class Config:
    days: int
    start: str | None
    end: str | None
    horizons: list[int]
    barriers_bps: dict[int, float]
    sample_every_seconds: int
    models: list[str]
    max_train_rows: int
    threads: int
    output_root: str


def log(message: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {message}"
    print(line, flush=True)
    if _LOG_PATH is not None:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return None if not np.isfinite(value) else float(value)
    if isinstance(value, np.ndarray):
        return json_safe(value.tolist())
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def available_paired_dates(cache_dir: Path) -> list[str]:
    spot: set[str] = set()
    perp: set[str] = set()
    spot_re = re.compile(r"^BTCUSDT-aggTrades-(\d{4}-\d{2}-\d{2})\.csv$")
    perp_re = re.compile(r"^BTCUSDT-perp-aggTrades-(\d{4}-\d{2}-\d{2})\.csv$")
    for path in cache_dir.glob("BTCUSDT*aggTrades-*.csv"):
        match = spot_re.match(path.name)
        if match:
            spot.add(match.group(1))
            continue
        match = perp_re.match(path.name)
        if match:
            perp.add(match.group(1))
    return sorted(spot & perp)


def select_dates(config: Config) -> list[str]:
    paired = available_paired_dates(CACHE_DIR)
    if not paired:
        raise FileNotFoundError(f"no paired spot/perpetual aggTrade days in {CACHE_DIR}")
    if config.start or config.end:
        if not (config.start and config.end):
            raise ValueError("--start and --end must be provided together")
        chosen = [day for day in paired if config.start <= day <= config.end]
    else:
        chosen = paired[-config.days :]
    if len(chosen) < config.days and not config.start:
        raise ValueError(f"requested {config.days} days but only {len(chosen)} paired days exist")
    expected = pd.date_range(chosen[0], chosen[-1], freq="D").strftime("%Y-%m-%d").tolist()
    if chosen != expected:
        missing = sorted(set(expected) - set(chosen))
        raise ValueError(f"selected event window is not continuous; missing={missing[:10]}")
    return chosen


def _last_per_index(index: np.ndarray, values: np.ndarray, size: int) -> np.ndarray:
    output = np.full(size, np.nan, dtype=np.float64)
    if not len(index):
        return output
    last_mask = np.r_[index[1:] != index[:-1], True]
    output[index[last_mask]] = values[last_mask]
    # Forward fill is causal. Do not backfill leading seconds from the first future trade;
    # those anchors remain NaN and are removed by the feature-validity gate.
    return pd.Series(output).ffill().to_numpy(dtype=np.float64)


def aggregate_one_second(
    date: str,
    trades: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray],
) -> dict[str, np.ndarray]:
    ts_ms, price, quantity, buyer_maker = trades
    if len(ts_ms) < 2:
        raise ValueError(f"{date}: insufficient trades")
    if bool(np.any(np.diff(ts_ms) < 0)):
        order = np.argsort(ts_ms, kind="stable")
        ts_ms, price, quantity, buyer_maker = (
            np.asarray(values)[order] for values in (ts_ms, price, quantity, buyer_maker)
        )
    day_start_s = int(pd.Timestamp(date, tz="UTC").timestamp())
    index = ts_ms.astype(np.int64) // 1_000 - day_start_s
    keep = (index >= 0) & (index < SECONDS_PER_DAY)
    index = index[keep].astype(np.int64)
    price = price[keep].astype(np.float64)
    quantity = quantity[keep].astype(np.float64)
    buyer_maker = buyer_maker[keep].astype(bool)
    if not len(index):
        raise ValueError(f"{date}: no trades fall inside UTC day")

    signed = np.where(buyer_maker, -quantity, quantity)
    volume = np.bincount(index, weights=quantity, minlength=SECONDS_PER_DAY)
    signed_volume = np.bincount(index, weights=signed, minlength=SECONDS_PER_DAY)
    count = np.bincount(index, minlength=SECONDS_PER_DAY)
    last = _last_per_index(index, price, SECONDS_PER_DAY)
    high = np.full(SECONDS_PER_DAY, -np.inf, dtype=np.float64)
    low = np.full(SECONDS_PER_DAY, np.inf, dtype=np.float64)
    np.maximum.at(high, index, price)
    np.minimum.at(low, index, price)
    inactive = count == 0
    high[inactive] = last[inactive]
    low[inactive] = last[inactive]
    return {
        "last": last.astype(np.float32),
        "high": high.astype(np.float32),
        "low": low.astype(np.float32),
        "volume": volume.astype(np.float32),
        "signed": signed_volume.astype(np.float32),
        "count": count.astype(np.float32),
    }


def load_event_window(dates: list[str]) -> dict[str, np.ndarray]:
    pieces: dict[str, list[np.ndarray]] = {
        "timestamp_s": [],
        "spot_last": [],
        "spot_high": [],
        "spot_low": [],
        "spot_volume": [],
        "spot_signed": [],
        "spot_count": [],
        "perp_last": [],
        "perp_high": [],
        "perp_low": [],
        "perp_volume": [],
        "perp_signed": [],
        "perp_count": [],
    }
    started = time.perf_counter()
    for number, date in enumerate(dates, start=1):
        spot_path = CACHE_DIR / f"BTCUSDT-aggTrades-{date}.csv"
        perp_path = CACHE_DIR / f"BTCUSDT-perp-aggTrades-{date}.csv"
        day_started = time.perf_counter()
        spot_raw = load_aggtrades(str(spot_path))
        spot_n = len(spot_raw[0])
        spot = aggregate_one_second(date, spot_raw)
        del spot_raw
        gc.collect()
        perp_raw = load_aggtrades(str(perp_path))
        perp_n = len(perp_raw[0])
        perp = aggregate_one_second(date, perp_raw)
        del perp_raw
        gc.collect()

        day_start_s = int(pd.Timestamp(date, tz="UTC").timestamp())
        pieces["timestamp_s"].append(
            np.arange(day_start_s, day_start_s + SECONDS_PER_DAY, dtype=np.int64)
        )
        for name in ("last", "high", "low", "volume", "signed", "count"):
            pieces[f"spot_{name}"].append(spot[name])
            pieces[f"perp_{name}"].append(perp[name])
        log(
            f"[data {number}/{len(dates)}] {date} "
            f"spot={spot_n:,} perp={perp_n:,} elapsed={time.perf_counter()-day_started:.1f}s"
        )
        del spot, perp
        gc.collect()
    output = {name: np.concatenate(values) for name, values in pieces.items()}
    # A day can begin a few seconds before its first trade. Carry the preceding day's final
    # observed price forward across that boundary, but never fill the beginning of the entire
    # research window from a future observation.
    for venue in ("spot", "perp"):
        last_key = f"{venue}_last"
        last = pd.Series(output[last_key]).ffill().to_numpy(dtype=np.float32)
        output[last_key] = last
        for extreme in ("high", "low"):
            key = f"{venue}_{extreme}"
            values = output[key]
            values[~np.isfinite(values)] = last[~np.isfinite(values)]
    gaps = np.diff(output["timestamp_s"])
    if len(gaps) and bool(np.any(gaps != 1)):
        raise ValueError("one-second event window contains timestamp gaps")
    log(
        f"[data] seconds={len(output['timestamp_s']):,} days={len(dates)} "
        f"elapsed={time.perf_counter()-started:.1f}s"
    )
    return output


def rolling_sum(values: np.ndarray, window: int) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    cumulative = np.concatenate(([0.0], np.cumsum(array, dtype=np.float64)))
    result = np.full(len(array), np.nan, dtype=np.float64)
    result[window - 1 :] = cumulative[window:] - cumulative[:-window]
    return result


def lag_return_bps(price: np.ndarray, lag: int) -> np.ndarray:
    values = np.asarray(price, dtype=np.float64)
    result = np.full(len(values), np.nan, dtype=np.float64)
    denominator = values[:-lag]
    valid = denominator > 0
    target = result[lag:]
    target[valid] = (values[lag:][valid] / denominator[valid] - 1.0) * 10_000.0
    return result


def rolling_rms(values: np.ndarray, window: int) -> np.ndarray:
    squares = np.square(np.nan_to_num(values, nan=0.0), dtype=np.float64)
    total = rolling_sum(squares, window)
    return np.sqrt(np.maximum(total / float(window), 0.0))


def build_causal_features(
    events: dict[str, np.ndarray],
    *,
    sample_every_seconds: int,
    max_horizon: int,
) -> tuple[pd.DataFrame, np.ndarray]:
    length = len(events["timestamp_s"])
    anchors = np.arange(
        WARMUP_SECONDS,
        length - max_horizon - 1,
        sample_every_seconds,
        dtype=np.int64,
    )
    timestamp = events["timestamp_s"][anchors]
    spot = events["spot_last"].astype(np.float64)
    perp = events["perp_last"].astype(np.float64)
    basis = (perp / spot - 1.0) * 10_000.0
    spot_ret_1 = lag_return_bps(spot, 1)
    perp_ret_1 = lag_return_bps(perp, 1)

    feature: dict[str, np.ndarray] = {}
    second_of_day = timestamp % SECONDS_PER_DAY
    feature["hour_sin"] = np.sin(2.0 * np.pi * second_of_day / SECONDS_PER_DAY)
    feature["hour_cos"] = np.cos(2.0 * np.pi * second_of_day / SECONDS_PER_DAY)
    day = (timestamp // SECONDS_PER_DAY) % 7
    feature["weekday_sin"] = np.sin(2.0 * np.pi * day / 7.0)
    feature["weekday_cos"] = np.cos(2.0 * np.pi * day / 7.0)
    feature["basis_bps"] = basis[anchors]

    for lag in (1, 3, 5, 10, 30, 60):
        spot_return = lag_return_bps(spot, lag)
        perp_return = lag_return_bps(perp, lag)
        feature[f"spot_ret_{lag}s_bps"] = spot_return[anchors]
        feature[f"perp_ret_{lag}s_bps"] = perp_return[anchors]
        feature[f"perp_lead_{lag}s_bps"] = (perp_return - spot_return)[anchors]
        feature[f"basis_change_{lag}s_bps"] = (basis - np.roll(basis, lag))[anchors]

    for window in (1, 3, 5, 10, 30, 60):
        spot_volume = rolling_sum(events["spot_volume"], window)
        perp_volume = rolling_sum(events["perp_volume"], window)
        spot_signed = rolling_sum(events["spot_signed"], window)
        perp_signed = rolling_sum(events["perp_signed"], window)
        spot_flow = spot_signed / np.maximum(spot_volume, 1e-9)
        perp_flow = perp_signed / np.maximum(perp_volume, 1e-9)
        feature[f"spot_flow_{window}s"] = spot_flow[anchors]
        feature[f"perp_flow_{window}s"] = perp_flow[anchors]
        feature[f"flow_divergence_{window}s"] = (perp_flow - spot_flow)[anchors]
        feature[f"flow_agreement_{window}s"] = (perp_flow * spot_flow)[anchors]
        feature[f"log_spot_volume_{window}s"] = np.log1p(spot_volume[anchors])
        feature[f"log_perp_volume_{window}s"] = np.log1p(perp_volume[anchors])
        spot_count = rolling_sum(events["spot_count"], window)
        perp_count = rolling_sum(events["perp_count"], window)
        feature[f"log_spot_intensity_{window}s"] = np.log1p(
            spot_count[anchors] / float(window)
        )
        feature[f"log_perp_intensity_{window}s"] = np.log1p(
            perp_count[anchors] / float(window)
        )

    for window in (10, 30, 60):
        feature[f"spot_rms_{window}s_bps"] = rolling_rms(spot_ret_1, window)[anchors]
        feature[f"perp_rms_{window}s_bps"] = rolling_rms(perp_ret_1, window)[anchors]
        spot_range = (
            pd.Series(events["spot_high"])
            .rolling(window, min_periods=window)
            .max()
            .to_numpy()
            - pd.Series(events["spot_low"])
            .rolling(window, min_periods=window)
            .min()
            .to_numpy()
        )
        feature[f"spot_range_{window}s_bps"] = (
            spot_range / np.maximum(spot, 1e-9) * 10_000.0
        )[anchors]

    frame = pd.DataFrame(feature, dtype=np.float32)
    finite = np.isfinite(frame.to_numpy(dtype=np.float32)).all(axis=1)
    if not bool(finite.all()):
        dropped = int((~finite).sum())
        log(f"[features] dropping {dropped:,} non-finite anchors")
        frame = frame.loc[finite].reset_index(drop=True)
        anchors = anchors[finite]
    log(f"[features] anchors={len(frame):,} columns={frame.shape[1]}")
    return frame, anchors


def build_first_barrier_labels(
    price: np.ndarray,
    anchors: np.ndarray,
    *,
    horizon_seconds: int,
    barrier_bps: float,
) -> pd.DataFrame:
    entry = np.asarray(price, dtype=np.float64)[anchors]
    first_up = np.full(len(anchors), -1, dtype=np.int16)
    first_down = np.full(len(anchors), -1, dtype=np.int16)
    final_return = np.full(len(anchors), np.nan, dtype=np.float64)
    for step in range(1, horizon_seconds + 1):
        future = np.asarray(price, dtype=np.float64)[anchors + step]
        return_bps = (future / entry - 1.0) * 10_000.0
        first_up[(first_up < 0) & (return_bps >= barrier_bps)] = step
        first_down[(first_down < 0) & (return_bps <= -barrier_bps)] = step
        if step == horizon_seconds:
            final_return = return_bps
    movement = (first_up >= 0) | (first_down >= 0)
    roundtrip = (first_up >= 0) & (first_down >= 0)
    ambiguous = roundtrip & (first_up == first_down)
    direction = np.full(len(anchors), -1, dtype=np.int8)
    direction[(first_up >= 0) & ((first_down < 0) | (first_up < first_down))] = 1
    direction[(first_down >= 0) & ((first_up < 0) | (first_down < first_up))] = 0
    direction[ambiguous] = -1
    return pd.DataFrame(
        {
            "direction": direction,
            "movement": movement.astype(np.int8),
            "roundtrip": roundtrip.astype(np.int8),
            "ambiguous": ambiguous.astype(np.int8),
            "first_up_s": first_up,
            "first_down_s": first_down,
            "final_return_bps": final_return.astype(np.float32),
        }
    )


def model_factory(name: str, threads: int) -> Callable[[], Any]:
    if name == "logreg":
        return lambda: Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        max_iter=500,
                        C=0.25,
                        class_weight="balanced",
                        random_state=701,
                    ),
                ),
            ]
        )
    if name == "histgb":
        return lambda: HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=160,
            max_depth=6,
            min_samples_leaf=80,
            l2_regularization=0.3,
            random_state=702,
        )
    if name == "lightgbm":
        def make_lightgbm() -> Any:
            from lightgbm import LGBMClassifier

            return LGBMClassifier(
                n_estimators=240,
                max_depth=6,
                learning_rate=0.035,
                num_leaves=31,
                min_child_samples=80,
                subsample=0.8,
                colsample_bytree=0.8,
                class_weight="balanced",
                n_jobs=threads,
                verbosity=-1,
                random_state=703,
            )

        return make_lightgbm
    if name == "catboost":
        def make_catboost() -> Any:
            from catboost import CatBoostClassifier

            return CatBoostClassifier(
                iterations=240,
                depth=6,
                learning_rate=0.035,
                loss_function="Logloss",
                auto_class_weights="Balanced",
                thread_count=threads,
                allow_writing_files=False,
                verbose=False,
                random_seed=704,
            )

        return make_catboost
    raise ValueError(f"unknown model: {name}")


def positive_probability(model: Any, matrix: np.ndarray) -> np.ndarray:
    probability = np.asarray(model.predict_proba(matrix), dtype=float)
    classes = list(getattr(model, "classes_", [0, 1]))
    if isinstance(model, Pipeline):
        classes = list(getattr(model.named_steps["model"], "classes_", classes))
    if 1 not in classes:
        raise ValueError(f"positive class unavailable: {classes}")
    return probability[:, classes.index(1)]


def safe_auc(target: np.ndarray, probability: np.ndarray) -> float:
    return (
        float(roc_auc_score(target, probability))
        if len(np.unique(target)) == 2
        else math.nan
    )


def expected_calibration_error(
    target: np.ndarray, probability: np.ndarray, bins: int = 10
) -> float:
    y = np.asarray(target, dtype=float)
    p = np.clip(np.asarray(probability, dtype=float), 1e-6, 1.0 - 1e-6)
    edges = np.linspace(0.0, 1.0, bins + 1)
    result = 0.0
    for index in range(bins):
        right = p <= edges[index + 1] if index == bins - 1 else p < edges[index + 1]
        mask = (p >= edges[index]) & right
        if bool(mask.any()):
            result += float(mask.mean()) * abs(float(y[mask].mean() - p[mask].mean()))
    return result


def binary_metrics(target: np.ndarray, probability: np.ndarray) -> dict[str, float | int]:
    y = np.asarray(target, dtype=int)
    p = np.clip(np.asarray(probability, dtype=float), 1e-6, 1.0 - 1e-6)
    prediction = (p >= 0.5).astype(int)
    return {
        "n": len(y),
        "positive_rate": float(y.mean()),
        "auc": safe_auc(y, p),
        "average_precision": float(average_precision_score(y, p)),
        "accuracy": float(accuracy_score(y, prediction)),
        "balanced_accuracy": float(balanced_accuracy_score(y, prediction)),
        "precision": float(precision_score(y, prediction, zero_division=0)),
        "recall": float(recall_score(y, prediction, zero_division=0)),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "ece_10bin": expected_calibration_error(y, p),
    }


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


def capped_indices(indices: np.ndarray, maximum: int) -> np.ndarray:
    if maximum <= 0 or len(indices) <= maximum:
        return indices
    positions = np.linspace(0, len(indices) - 1, maximum, dtype=np.int64)
    return indices[positions]


def period_masks(
    timestamp_s: np.ndarray,
    *,
    horizon: int,
) -> dict[str, np.ndarray]:
    start = int(timestamp_s.min())
    end = int(timestamp_s.max()) + 1
    span = end - start
    train_end = start + int(span * 0.60)
    calibration_end = start + int(span * 0.70)
    meta_end = start + int(span * 0.80)
    aligned = timestamp_s % horizon == 0
    label_end = timestamp_s + horizon
    return {
        "train": aligned & (timestamp_s < train_end) & (label_end < train_end),
        "calibration": aligned
        & (timestamp_s >= train_end)
        & (timestamp_s < calibration_end)
        & (label_end < calibration_end),
        "meta": aligned
        & (timestamp_s >= calibration_end)
        & (timestamp_s < meta_end)
        & (label_end < meta_end),
        "test": aligned & (timestamp_s >= meta_end) & (label_end <= end),
    }


def fit_specialist_target(
    *,
    target_name: str,
    labels: pd.DataFrame,
    feature_values: np.ndarray,
    masks: dict[str, np.ndarray],
    model_names: list[str],
    max_train_rows: int,
    threads: int,
    horizon: int,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, np.ndarray]]]:
    direction = labels["direction"].to_numpy(dtype=np.int8)
    target = labels[target_name].to_numpy(dtype=np.int8)
    valid = direction >= 0 if target_name == "direction" else np.ones(len(labels), dtype=bool)
    index: dict[str, np.ndarray] = {
        name: np.flatnonzero(mask & valid) for name, mask in masks.items()
    }
    if min(len(index["train"]), len(index["calibration"]), len(index["test"])) < 100:
        raise ValueError(f"{horizon}s {target_name}: insufficient samples in a split")
    train_idx = capped_indices(index["train"], max_train_rows)
    if len(np.unique(target[train_idx])) < 2:
        raise ValueError(f"{horizon}s {target_name}: training target has one class")

    all_period_idx = {
        "calibration": np.flatnonzero(masks["calibration"]),
        "meta": np.flatnonzero(masks["meta"]),
        "test": np.flatnonzero(masks["test"]),
    }
    predictions: dict[str, dict[str, np.ndarray]] = {
        period: {} for period in all_period_idx
    }
    metrics: list[dict[str, Any]] = []
    for model_name in model_names:
        started = time.perf_counter()
        model = model_factory(model_name, threads)()
        model.fit(feature_values[train_idx], target[train_idx])
        raw_calibration_valid = positive_probability(
            model, feature_values[index["calibration"]]
        )
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(raw_calibration_valid, target[index["calibration"]])
        for period, period_idx in all_period_idx.items():
            raw = positive_probability(model, feature_values[period_idx])
            predictions[period][model_name] = np.asarray(
                calibrator.predict(raw), dtype=np.float64
            )
        test_all_idx = all_period_idx["test"]
        test_valid_in_all = valid[test_all_idx]
        test_probability = predictions["test"][model_name][test_valid_in_all]
        row = {
            "horizon_seconds": horizon,
            "head": target_name,
            "model": model_name,
            **binary_metrics(target[test_all_idx][test_valid_in_all], test_probability),
            "train_n": len(train_idx),
            "calibration_n": len(index["calibration"]),
            "elapsed_seconds": time.perf_counter() - started,
        }
        metrics.append(row)
        log(
            f"[fit] h={horizon}s head={target_name} model={model_name} "
            f"auc={row['auc']:.4f} brier={row['brier']:.4f} "
            f"n={row['n']:,} elapsed={row['elapsed_seconds']:.1f}s"
        )
        del model, calibrator
        gc.collect()

    for period in predictions:
        stack = np.column_stack([predictions[period][name] for name in model_names])
        predictions[period]["ensemble"] = stack.mean(axis=1)
        predictions[period]["model_std"] = stack.std(axis=1)
    test_all_idx = all_period_idx["test"]
    test_valid_in_all = valid[test_all_idx]
    ensemble_row = {
        "horizon_seconds": horizon,
        "head": target_name,
        "model": "mean_ensemble",
        **binary_metrics(
            target[test_all_idx][test_valid_in_all],
            predictions["test"]["ensemble"][test_valid_in_all],
        ),
        "train_n": len(train_idx),
        "calibration_n": len(index["calibration"]),
        "elapsed_seconds": math.nan,
    }
    metrics.append(ensemble_row)
    log(
        f"[ensemble] h={horizon}s head={target_name} "
        f"auc={ensemble_row['auc']:.4f} brier={ensemble_row['brier']:.4f}"
    )
    return metrics, predictions


def fit_act_skip(
    *,
    labels: pd.DataFrame,
    features: pd.DataFrame,
    masks: dict[str, np.ndarray],
    head_predictions: dict[str, dict[str, dict[str, np.ndarray]]],
    horizon: int,
    threads: int,
) -> tuple[list[dict[str, Any]], dict[str, np.ndarray]]:
    meta_idx = np.flatnonzero(masks["meta"])
    test_idx = np.flatnonzero(masks["test"])
    direction = labels["direction"].to_numpy(dtype=np.int8)

    compact_names = [
        name
        for name in (
            "basis_bps",
            "perp_lead_5s_bps",
            "perp_lead_30s_bps",
            "spot_flow_5s",
            "perp_flow_5s",
            "flow_divergence_5s",
            "spot_flow_30s",
            "perp_flow_30s",
            "spot_rms_30s_bps",
            "spot_rms_60s_bps",
        )
        if name in features.columns
    ]

    def make_meta(period: str, period_idx: np.ndarray) -> np.ndarray:
        p_direction = head_predictions["direction"][period]["ensemble"]
        p_move = head_predictions["movement"][period]["ensemble"]
        p_roundtrip = head_predictions["roundtrip"][period]["ensemble"]
        direction_std = head_predictions["direction"][period]["model_std"]
        engineered = np.column_stack(
            [
                p_direction,
                np.abs(p_direction - 0.5) * 2.0,
                direction_std,
                p_move,
                p_roundtrip,
                p_move * (1.0 - p_roundtrip),
            ]
        )
        context = features.iloc[period_idx][compact_names].to_numpy(dtype=np.float32)
        return np.column_stack([engineered, context]).astype(np.float32)

    meta_matrix = make_meta("meta", meta_idx)
    test_matrix = make_meta("test", test_idx)
    meta_direction_probability = head_predictions["direction"]["meta"]["ensemble"]
    test_direction_probability = head_predictions["direction"]["test"]["ensemble"]
    meta_predicted_side = (meta_direction_probability >= 0.5).astype(np.int8)
    test_predicted_side = (test_direction_probability >= 0.5).astype(np.int8)
    meta_target = (
        (direction[meta_idx] >= 0) & (meta_predicted_side == direction[meta_idx])
    ).astype(np.int8)
    test_target = (
        (direction[test_idx] >= 0) & (test_predicted_side == direction[test_idx])
    ).astype(np.int8)
    if len(np.unique(meta_target)) < 2:
        raise ValueError(f"{horizon}s ACT/SKIP meta target has one class")
    # The meta period is kept separate from base-model calibration, then split again
    # chronologically: first half fits ACT/SKIP and second half calibrates its probability.
    # Without this slice, the frozen 0.65 threshold would be an arbitrary model score.
    meta_cut = len(meta_target) // 2
    act_train = np.arange(0, meta_cut, dtype=np.int64)
    act_calibration = np.arange(meta_cut, len(meta_target), dtype=np.int64)
    if min(len(act_train), len(act_calibration)) < 100:
        raise ValueError(f"{horizon}s ACT/SKIP has insufficient train/calibration rows")
    if len(np.unique(meta_target[act_train])) < 2:
        raise ValueError(f"{horizon}s ACT/SKIP training slice has one class")
    if len(np.unique(meta_target[act_calibration])) < 2:
        raise ValueError(f"{horizon}s ACT/SKIP calibration slice has one class")

    names = ("logreg", "histgb")
    probability: dict[str, np.ndarray] = {}
    metrics: list[dict[str, Any]] = []
    for name in names:
        started = time.perf_counter()
        model = model_factory(name, threads)()
        model.fit(meta_matrix[act_train], meta_target[act_train])
        raw_calibration = positive_probability(model, meta_matrix[act_calibration])
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(raw_calibration, meta_target[act_calibration])
        probability[name] = np.asarray(
            calibrator.predict(positive_probability(model, test_matrix)), dtype=float
        )
        row = {
            "horizon_seconds": horizon,
            "head": "act_skip",
            "model": name,
            **binary_metrics(test_target, probability[name]),
            "train_n": len(act_train),
            "calibration_n": len(act_calibration),
            "elapsed_seconds": time.perf_counter() - started,
        }
        metrics.append(row)
        log(
            f"[fit] h={horizon}s head=act_skip model={name} "
            f"auc={row['auc']:.4f} brier={row['brier']:.4f}"
        )
        del model, calibrator
        gc.collect()

    ensemble = np.column_stack([probability[name] for name in names]).mean(axis=1)
    act = ensemble >= ACT_THRESHOLD
    successes = int(test_target[act].sum())
    count = int(act.sum())
    selected_precision = successes / count if count else math.nan
    row = {
        "horizon_seconds": horizon,
        "head": "act_skip",
        "model": "mean_ensemble",
        **binary_metrics(test_target, ensemble),
        "train_n": len(act_train),
        "calibration_n": len(act_calibration),
        "elapsed_seconds": math.nan,
        "act_threshold": ACT_THRESHOLD,
        "act_count": count,
        "act_coverage": float(act.mean()),
        "act_precision": selected_precision,
        "act_wilson_lb": wilson_lower_bound(successes, count),
        "all_anchor_correct_rate": float(test_target.mean()),
    }
    metrics.append(row)
    log(
        f"[act] h={horizon}s count={count:,} coverage={row['act_coverage']:.2%} "
        f"precision={selected_precision:.2%} LB={row['act_wilson_lb']:.2%}"
    )
    return metrics, {
        "probability": ensemble,
        "act": act,
        "target": test_target,
        "predicted_side": test_predicted_side,
    }


def write_summary(
    *,
    run_dir: Path,
    config: Config,
    dates: list[str],
    feature_count: int,
    metrics: pd.DataFrame,
    label_stats: list[dict[str, Any]],
    direction_by_day: pd.DataFrame,
    elapsed: float,
) -> None:
    lines = [
        "# Event-Time Specialist Experiment",
        "",
        f"Run: `{run_dir.name}`",
        "",
        "Status: **HISTORICAL INFORMATION TEST ONLY - NOT A TRADING POLICY**",
        "",
        "## Data",
        "",
        f"- Period: `{dates[0]}` through `{dates[-1]}` ({len(dates)} days)",
        "- Source: raw Binance spot and USD-M perpetual aggregate trades",
        "- Resolution: one second",
        f"- Causal features: {feature_count}",
        "- Split: 60% base training, 10% probability calibration, 10% ACT/SKIP meta training, 20% locked test",
        "- Decisions are horizon-spaced, so forward label windows do not overlap within a horizon.",
        "",
        "The archive does not contain historical limit-order additions, cancellations, queue depth,",
        "or synchronized Coinbase/Bybit events. No result here establishes executable profit.",
        "",
        "## Targets",
        "",
        "| Horizon | Barrier | Resolved | Movement | Round-trip | Ambiguous |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for row in label_stats:
        lines.append(
            f"| {row['horizon_seconds']}s | {row['barrier_bps']:.2f} bps | "
            f"{row['resolved_rate']:.1%} | {row['movement_rate']:.1%} | "
            f"{row['roundtrip_rate']:.1%} | {row['ambiguous_rate']:.2%} |"
        )
    lines.extend(
        [
            "",
            "## Locked-Test Metrics",
            "",
            "| Horizon | Head | Model | n | AUC | Avg precision | Brier | Balanced accuracy |",
            "|---:|---|---|---:|---:|---:|---:|---:|",
        ]
    )
    selected = metrics[metrics["model"] == "mean_ensemble"].copy()
    for _, row in selected.iterrows():
        lines.append(
            f"| {int(row.horizon_seconds)}s | {row['head']} | mean ensemble | "
            f"{int(row['n']):,} | {row['auc']:.4f} | {row['average_precision']:.4f} | "
            f"{row['brier']:.4f} | {row['balanced_accuracy']:.2%} |"
        )
    lines.extend(
        [
            "",
            "## Direction Stability By Locked-Test Day",
            "",
            "| Horizon | Days | Minimum daily AUC | Median daily AUC | Maximum daily AUC | Days above 0.50 |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for horizon, group in direction_by_day.groupby("horizon_seconds"):
        lines.append(
            f"| {int(horizon)}s | {len(group)} | {group.auc.min():.4f} | "
            f"{group.auc.median():.4f} | {group.auc.max():.4f} | "
            f"{int((group.auc > 0.5).sum())}/{len(group)} |"
        )
    lines.extend(
        [
            "",
            "## Frozen ACT/SKIP Gate",
            "",
            f"The ACT threshold was fixed at `{ACT_THRESHOLD:.2f}` before the locked test.",
            "",
            "| Horizon | Test anchors | ACT count | Coverage | Correct when ACT | Wilson lower bound |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    acts = metrics[
        (metrics["head"] == "act_skip") & (metrics["model"] == "mean_ensemble")
    ]
    for _, row in acts.iterrows():
        lines.append(
            f"| {int(row.horizon_seconds)}s | {int(row['n']):,} | "
            f"{int(row.get('act_count', 0)):,} | {row.get('act_coverage', math.nan):.2%} | "
            f"{row.get('act_precision', math.nan):.2%} | "
            f"{row.get('act_wilson_lb', math.nan):.2%} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "- `direction` asks which fixed relative barrier is reached first, not where a 5m/15m candle closes.",
            "- `movement` asks whether either barrier is touched.",
            "- `roundtrip` identifies two-sided/choppy paths.",
            "- `act_skip` estimates whether the frozen direction ensemble will resolve correctly.",
            "- ACT/SKIP is a reliability filter, not an economic-profit head.",
            "",
            "No artifact from this run is eligible for production promotion. A production candidate requires",
            "synchronized top-of-book/queue data, realistic spread/fees/slippage, at least eight weeks of",
            "forward episodes, at least 500 independently resolved paper candidates, and a positive",
            "day-block lower confidence bound after costs.",
            "",
            f"Runtime: {elapsed:.1f} seconds.",
            "",
        ]
    )
    (run_dir / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")


def run(config: Config) -> Path:
    global _LOG_PATH
    started = time.perf_counter()
    dates = select_dates(config)
    run_name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = Path(config.output_root) / run_name
    run_dir.mkdir(parents=True, exist_ok=False)
    _LOG_PATH = run_dir / "run.log"
    log(f"[start] config={json.dumps(json_safe(asdict(config)), sort_keys=True)}")
    log(f"[dates] {dates[0]} -> {dates[-1]} ({len(dates)} days)")

    events = load_event_window(dates)
    features, anchors = build_causal_features(
        events,
        sample_every_seconds=config.sample_every_seconds,
        max_horizon=max(config.horizons),
    )
    timestamp_s = events["timestamp_s"][anchors]
    feature_values = features.to_numpy(dtype=np.float32)
    label_stats: list[dict[str, Any]] = []
    metric_rows: list[dict[str, Any]] = []
    prediction_frames: list[pd.DataFrame] = []

    for horizon in config.horizons:
        barrier = config.barriers_bps[horizon]
        labels = build_first_barrier_labels(
            events["spot_last"],
            anchors,
            horizon_seconds=horizon,
            barrier_bps=barrier,
        )
        masks = period_masks(timestamp_s, horizon=horizon)
        aligned = timestamp_s % horizon == 0
        label_stats.append(
            {
                "horizon_seconds": horizon,
                "barrier_bps": barrier,
                "n": int(aligned.sum()),
                "resolved_rate": float((labels.loc[aligned, "direction"] >= 0).mean()),
                "movement_rate": float(labels.loc[aligned, "movement"].mean()),
                "roundtrip_rate": float(labels.loc[aligned, "roundtrip"].mean()),
                "ambiguous_rate": float(labels.loc[aligned, "ambiguous"].mean()),
            }
        )
        log(
            f"[labels] h={horizon}s barrier={barrier:.2f}bps "
            f"resolved={label_stats[-1]['resolved_rate']:.2%} "
            f"movement={label_stats[-1]['movement_rate']:.2%} "
            f"roundtrip={label_stats[-1]['roundtrip_rate']:.2%}"
        )

        head_predictions: dict[str, dict[str, dict[str, np.ndarray]]] = {}
        for target_name in TARGETS:
            rows, predictions = fit_specialist_target(
                target_name=target_name,
                labels=labels,
                feature_values=feature_values,
                masks=masks,
                model_names=config.models,
                max_train_rows=config.max_train_rows,
                threads=config.threads,
                horizon=horizon,
            )
            metric_rows.extend(rows)
            head_predictions[target_name] = predictions

        act_rows, act = fit_act_skip(
            labels=labels,
            features=features,
            masks=masks,
            head_predictions=head_predictions,
            horizon=horizon,
            threads=config.threads,
        )
        metric_rows.extend(act_rows)
        test_idx = np.flatnonzero(masks["test"])
        prediction_frames.append(
            pd.DataFrame(
                {
                    "timestamp_s": timestamp_s[test_idx],
                    "horizon_seconds": horizon,
                    "barrier_bps": barrier,
                    "direction_label": labels.iloc[test_idx]["direction"].to_numpy(),
                    "movement_label": labels.iloc[test_idx]["movement"].to_numpy(),
                    "roundtrip_label": labels.iloc[test_idx]["roundtrip"].to_numpy(),
                    "p_up_first": head_predictions["direction"]["test"]["ensemble"],
                    "p_movement": head_predictions["movement"]["test"]["ensemble"],
                    "p_roundtrip": head_predictions["roundtrip"]["test"]["ensemble"],
                    "p_direction_correct": act["probability"],
                    "act": act["act"],
                    "act_correct": act["target"],
                    "predicted_side": act["predicted_side"],
                }
            )
        )
        del labels, head_predictions, act
        gc.collect()

    metrics = pd.DataFrame(metric_rows)
    predictions = pd.concat(prediction_frames, ignore_index=True)
    predictions["day"] = pd.to_datetime(
        predictions["timestamp_s"], unit="s", utc=True
    ).dt.strftime("%Y-%m-%d")
    direction_day_rows: list[dict[str, Any]] = []
    for (horizon, day), group in predictions.groupby(["horizon_seconds", "day"]):
        valid = group[group["direction_label"] >= 0]
        if valid.empty:
            continue
        target = valid["direction_label"].to_numpy(dtype=int)
        probability = valid["p_up_first"].to_numpy(dtype=float)
        direction_day_rows.append(
            {
                "horizon_seconds": int(horizon),
                "day": str(day),
                "n": len(valid),
                "auc": safe_auc(target, probability),
                "accuracy": float(accuracy_score(target, probability >= 0.5)),
                "up_rate": float(target.mean()),
            }
        )
    direction_by_day = pd.DataFrame(direction_day_rows)
    metrics.to_csv(run_dir / "metrics.csv", index=False)
    predictions.to_parquet(run_dir / "locked_predictions.parquet", index=False)
    direction_by_day.to_csv(run_dir / "direction_by_day.csv", index=False)
    (run_dir / "label_stats.json").write_text(
        json.dumps(json_safe(label_stats), indent=2), encoding="utf-8"
    )
    feature_manifest = {
        "feature_count": len(features.columns),
        "features": list(features.columns),
        "causality": "all features use data at or before anchor timestamp",
        "source_limitations": [
            "historical Binance aggregate trades only",
            "no historical order additions/cancellations",
            "no queue depth",
            "no synchronized historical Coinbase/Bybit events",
        ],
    }
    (run_dir / "feature_manifest.json").write_text(
        json.dumps(feature_manifest, indent=2), encoding="utf-8"
    )
    manifest = {
        "config": asdict(config),
        "dates": dates,
        "script": str(Path(__file__).resolve()),
        "script_sha256": sha256_file(Path(__file__).resolve()),
        "input_files": {
            day: {
                "spot": str(CACHE_DIR / f"BTCUSDT-aggTrades-{day}.csv"),
                "perp": str(CACHE_DIR / f"BTCUSDT-perp-aggTrades-{day}.csv"),
            }
            for day in dates
        },
        "production_artifacts_changed": False,
        "eligible_for_production": False,
    }
    (run_dir / "run_manifest.json").write_text(
        json.dumps(json_safe(manifest), indent=2), encoding="utf-8"
    )
    elapsed = time.perf_counter() - started
    write_summary(
        run_dir=run_dir,
        config=config,
        dates=dates,
        feature_count=len(features.columns),
        metrics=metrics,
        label_stats=label_stats,
        direction_by_day=direction_by_day,
        elapsed=elapsed,
    )
    log(f"[done] output={run_dir} elapsed={elapsed:.1f}s")
    return run_dir


def selftest() -> int:
    n = 2_000
    timestamp = np.arange(1_700_000_000, 1_700_000_000 + n, dtype=np.int64)
    base = 100.0 + np.sin(np.arange(n) / 20.0) * 0.05
    event = {
        "timestamp_s": timestamp,
        "spot_last": base.astype(np.float32),
        "spot_high": (base + 0.01).astype(np.float32),
        "spot_low": (base - 0.01).astype(np.float32),
        "spot_volume": np.ones(n, dtype=np.float32),
        "spot_signed": np.sin(np.arange(n) / 7.0).astype(np.float32),
        "spot_count": np.full(n, 3, dtype=np.float32),
        "perp_last": (base * 1.0001).astype(np.float32),
        "perp_high": (base * 1.0001 + 0.01).astype(np.float32),
        "perp_low": (base * 1.0001 - 0.01).astype(np.float32),
        "perp_volume": np.full(n, 1.2, dtype=np.float32),
        "perp_signed": np.cos(np.arange(n) / 9.0).astype(np.float32),
        "perp_count": np.full(n, 4, dtype=np.float32),
    }
    feature, anchors = build_causal_features(
        event, sample_every_seconds=5, max_horizon=60
    )
    if feature.empty or len(feature) != len(anchors):
        raise AssertionError("feature construction failed")
    changed = {key: values.copy() for key, values in event.items()}
    changed["spot_last"][1_200:] *= 1.1
    changed["spot_high"][1_200:] *= 1.1
    changed["spot_low"][1_200:] *= 1.1
    changed_feature, changed_anchors = build_causal_features(
        changed, sample_every_seconds=5, max_horizon=60
    )
    before = anchors < 1_100
    if not np.array_equal(anchors, changed_anchors):
        raise AssertionError("future perturbation changed anchor identity")
    if not np.allclose(
        feature.loc[before].to_numpy(),
        changed_feature.loc[before].to_numpy(),
        atol=1e-6,
    ):
        raise AssertionError("future data leaked into causal features")

    price = np.full(100, 100.0, dtype=np.float64)
    price[11:15] = 100.02
    price[15:] = 99.98
    labels = build_first_barrier_labels(
        price, np.array([10]), horizon_seconds=10, barrier_bps=1.0
    )
    if int(labels.iloc[0].direction) != 1 or int(labels.iloc[0].roundtrip) != 1:
        raise AssertionError("first-barrier order label is wrong")
    masks = period_masks(event["timestamp_s"][anchors], horizon=15)
    for name, mask in masks.items():
        if not bool(mask.any()):
            raise AssertionError(f"empty selftest split: {name}")
    train_timestamps = event["timestamp_s"][anchors][masks["train"]]
    train_end = int(event["timestamp_s"][anchors].min()) + int(
        (event["timestamp_s"][anchors].max() + 1 - event["timestamp_s"][anchors].min())
        * 0.60
    )
    if int(train_timestamps.max()) + 15 >= train_end:
        raise AssertionError("training label crosses split boundary")
    print("SELFTEST PASS")
    return 0


def parse_barriers(value: str, horizons: list[int]) -> dict[int, float]:
    if not value:
        missing = [horizon for horizon in horizons if horizon not in DEFAULT_BARRIERS_BPS]
        if missing:
            raise ValueError(
                f"no default barriers for {missing}; pass --barriers 'seconds:bps,...'"
            )
        return {horizon: DEFAULT_BARRIERS_BPS[horizon] for horizon in horizons}
    parsed: dict[int, float] = {}
    for item in value.split(","):
        horizon, barrier = item.split(":", 1)
        parsed[int(horizon)] = float(barrier)
    missing = sorted(set(horizons) - set(parsed))
    if missing:
        raise ValueError(f"missing barrier definitions for horizons {missing}")
    if any(value <= 0 for value in parsed.values()):
        raise ValueError("barriers must be positive")
    return {horizon: parsed[horizon] for horizon in horizons}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--horizons", nargs="+", type=int, default=list(DEFAULT_HORIZONS))
    parser.add_argument(
        "--barriers",
        default="",
        help="comma list of horizon_seconds:barrier_bps; defaults are 5:1,15:1.5,30:2,60:3",
    )
    parser.add_argument("--sample-every-seconds", type=int, default=5)
    parser.add_argument(
        "--models",
        default=",".join(MODEL_NAMES),
        help=f"comma list from {','.join(MODEL_NAMES)}",
    )
    parser.add_argument("--max-train-rows", type=int, default=250_000)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    models = [name.strip() for name in args.models.split(",") if name.strip()]
    unknown = sorted(set(models) - set(MODEL_NAMES))
    if unknown:
        parser.error(f"unknown models: {unknown}")
    if args.days < 5:
        parser.error("--days must be at least 5 for the chronological four-way split")
    if args.sample_every_seconds < 1:
        parser.error("--sample-every-seconds must be positive")
    horizons = sorted(set(args.horizons))
    config = Config(
        days=args.days,
        start=args.start,
        end=args.end,
        horizons=horizons,
        barriers_bps=parse_barriers(args.barriers, horizons),
        sample_every_seconds=args.sample_every_seconds,
        models=models,
        max_train_rows=args.max_train_rows,
        threads=max(1, args.threads),
        output_root=args.output_root,
    )
    run(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
