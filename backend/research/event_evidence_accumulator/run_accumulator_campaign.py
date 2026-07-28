#!/usr/bin/env python
"""Replay frozen event-time predictions as independent settlement candidates.

This is a research-only bridge between short-lived 5s/15s directional forecasts
and 5m/15m settlement probabilities. It never imports a serving model or policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESEARCH = BACKEND / "research"
for candidate in (BACKEND, RESEARCH):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from backfill_trade_features import load_aggtrades  # noqa: E402
from train_event_time_specialists import (  # noqa: E402
    CACHE_DIR,
    aggregate_one_second,
)


PROTOCOL_PATH = Path(__file__).with_name("frozen_protocol.json")
DEFAULT_OUTPUT_ROOT = ROOT / "data" / "research" / "event_evidence_accumulator"
RANDOM_SEED = 20260728
_LOG_PATH: Path | None = None


def log(message: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {message}"
    print(line, flush=True)
    if _LOG_PATH is not None:
        with _LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def logit(values: np.ndarray) -> np.ndarray:
    probability = np.clip(np.asarray(values, dtype=float), 1e-6, 1.0 - 1e-6)
    return np.log(probability / (1.0 - probability))


def safe_auc(target: np.ndarray, probability: np.ndarray) -> float:
    y = np.asarray(target, dtype=int)
    return float(roc_auc_score(y, probability)) if len(np.unique(y)) == 2 else math.nan


def probability_metrics(
    target: np.ndarray, probability: np.ndarray, prefix: str
) -> dict[str, float | int]:
    y = np.asarray(target, dtype=int)
    p = np.clip(np.asarray(probability, dtype=float), 1e-6, 1.0 - 1e-6)
    return {
        f"{prefix}_auc": safe_auc(y, p),
        f"{prefix}_brier": float(brier_score_loss(y, p)),
        f"{prefix}_log_loss": float(log_loss(y, p, labels=[0, 1])),
        f"{prefix}_accuracy": float(accuracy_score(y, p >= 0.5)),
    }


def wilson_lower(successes: int, count: int, z: float = 1.959963984540054) -> float:
    if count <= 0:
        return math.nan
    rate = successes / count
    denominator = 1.0 + z * z / count
    centre = rate + z * z / (2.0 * count)
    spread = z * math.sqrt(rate * (1.0 - rate) / count + z * z / (4.0 * count * count))
    return float((centre - spread) / denominator)


def day_block_lower(frame: pd.DataFrame, column: str) -> float:
    daily = frame.groupby("day")[column].mean().to_numpy(float)
    if not len(daily):
        return math.nan
    generator = np.random.default_rng(RANDOM_SEED)
    samples = np.empty(2_000, dtype=float)
    for index in range(len(samples)):
        samples[index] = generator.choice(daily, size=len(daily), replace=True).mean()
    return float(np.quantile(samples, 0.025))


def load_protocol() -> dict[str, Any]:
    if not PROTOCOL_PATH.exists():
        raise FileNotFoundError(PROTOCOL_PATH)
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


def resolve_input(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def align_prediction_heads(path: Path, horizons: list[int]) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    raw = pd.read_parquet(path)
    required = {
        "timestamp_s",
        "horizon_seconds",
        "p_up_first",
        "p_movement",
        "p_roundtrip",
    }
    missing = sorted(required - set(raw.columns))
    if missing:
        raise ValueError(f"{path} missing prediction fields: {missing}")
    pieces: dict[int, pd.DataFrame] = {}
    for horizon in horizons:
        subset = raw[raw["horizon_seconds"] == horizon][
            ["timestamp_s", "p_up_first", "p_movement", "p_roundtrip"]
        ].copy()
        if subset.empty:
            raise ValueError(f"{path} has no {horizon}s predictions")
        subset = subset.sort_values("timestamp_s").drop_duplicates("timestamp_s")
        subset = subset.rename(
            columns={
                "timestamp_s": f"source_timestamp_{horizon}",
                "p_up_first": f"p_up_{horizon}",
                "p_movement": f"p_move_{horizon}",
                "p_roundtrip": f"p_roundtrip_{horizon}",
            }
        )
        pieces[horizon] = subset

    master = pieces[horizons[0]].rename(
        columns={f"source_timestamp_{horizons[0]}": "timestamp_s"}
    )
    master[f"source_timestamp_{horizons[0]}"] = master["timestamp_s"]
    for horizon in horizons[1:]:
        master = pd.merge_asof(
            master.sort_values("timestamp_s"),
            pieces[horizon],
            left_on="timestamp_s",
            right_on=f"source_timestamp_{horizon}",
            direction="backward",
            tolerance=horizon,
        )
    for horizon in horizons:
        master[f"age_{horizon}"] = (
            master["timestamp_s"] - master[f"source_timestamp_{horizon}"]
        )
    required_values = [
        column
        for horizon in horizons
        for column in (
            f"p_up_{horizon}",
            f"p_move_{horizon}",
            f"p_roundtrip_{horizon}",
            f"age_{horizon}",
        )
    ]
    values = master[required_values].to_numpy(float)
    master["quality_score"] = np.isfinite(values).all(axis=1).astype(float)
    for horizon in horizons:
        age = master[f"age_{horizon}"]
        master.loc[(age < 0) | (age > horizon), "quality_score"] = 0.0
    master = master[master["quality_score"] == 1.0].reset_index(drop=True)
    probability_columns = [
        column for column in master if column.startswith(("p_up_", "p_move_", "p_roundtrip_"))
    ]
    probabilities = master[probability_columns].to_numpy(float)
    if not bool(
        np.isfinite(probabilities).all()
        and ((probabilities >= 0.0) & (probabilities <= 1.0)).all()
    ):
        raise AssertionError("aligned event probabilities are not finite and bounded")
    return master


def dates_for_price_window(minimum_ts: int, maximum_ts: int) -> list[str]:
    start = pd.Timestamp(minimum_ts, unit="s", tz="UTC").floor("D")
    end = pd.Timestamp(maximum_ts + 15 * 60, unit="s", tz="UTC").ceil("D")
    return pd.date_range(start, end, inclusive="left", freq="D").strftime("%Y-%m-%d").tolist()


def load_spot_seconds(dates: list[str]) -> pd.DataFrame:
    timestamp_parts: list[np.ndarray] = []
    price_parts: list[np.ndarray] = []
    started = time.perf_counter()
    for number, date in enumerate(dates, start=1):
        path = CACHE_DIR / f"BTCUSDT-aggTrades-{date}.csv"
        if not path.exists():
            raise FileNotFoundError(path)
        raw = load_aggtrades(str(path))
        aggregated = aggregate_one_second(date, raw)
        day_start = int(pd.Timestamp(date, tz="UTC").timestamp())
        timestamp_parts.append(
            np.arange(day_start, day_start + 24 * 60 * 60, dtype=np.int64)
        )
        price_parts.append(aggregated["last"])
        log(
            f"[price {number}/{len(dates)}] {date} trades={len(raw[0]):,} "
            f"elapsed={time.perf_counter()-started:.1f}s"
        )
        del raw, aggregated
    timestamp = np.concatenate(timestamp_parts)
    price = pd.Series(np.concatenate(price_parts), dtype=float).ffill().to_numpy(float)
    one_second_return = np.full(len(price), np.nan, dtype=float)
    valid = (price[1:] > 0) & (price[:-1] > 0)
    one_second_return[1:][valid] = np.log(price[1:][valid] / price[:-1][valid]) * 10_000.0
    rv_60 = (
        pd.Series(np.square(one_second_return))
        .rolling(60, min_periods=30)
        .mean()
        .pow(0.5)
        .to_numpy(float)
    )
    return pd.DataFrame(
        {"timestamp_s": timestamp, "spot_price": price, "rv_60s_bps": rv_60}
    )


def build_market_frame(
    aligned: pd.DataFrame, prices: pd.DataFrame, market_horizon_minutes: int
) -> pd.DataFrame:
    data = aligned.copy()
    duration = market_horizon_minutes * 60
    data["round_start_s"] = (data["timestamp_s"] // duration) * duration
    data["round_end_s"] = data["round_start_s"] + duration
    price_map = prices.set_index("timestamp_s")
    data["spot_price"] = price_map["spot_price"].reindex(data["timestamp_s"]).to_numpy()
    data["rv_60s_bps"] = price_map["rv_60s_bps"].reindex(data["timestamp_s"]).to_numpy()
    data["anchor_price"] = price_map["spot_price"].reindex(data["round_start_s"]).to_numpy()
    data["settlement_price"] = (
        price_map["spot_price"].reindex(data["round_end_s"]).to_numpy()
    )
    data["seconds_left"] = data["round_end_s"] - data["timestamp_s"]
    data = data[
        (data["seconds_left"] > 0)
        & np.isfinite(data["spot_price"])
        & np.isfinite(data["anchor_price"])
        & np.isfinite(data["settlement_price"])
        & np.isfinite(data["rv_60s_bps"])
    ].copy()
    data["distance_bps"] = (
        (data["spot_price"] / data["anchor_price"]) - 1.0
    ) * 10_000.0
    remaining_std = data["rv_60s_bps"].clip(lower=0.01) * np.sqrt(data["seconds_left"])
    data["z_distance"] = (data["distance_bps"] / remaining_std.clip(lower=0.25)).clip(
        -12.0, 12.0
    )
    data["seconds_ratio"] = data["seconds_left"] / duration
    data["current_side"] = np.sign(data["distance_bps"]).astype(int)
    data["settlement_up"] = (data["settlement_price"] > data["anchor_price"]).astype(int)
    data["market_horizon_minutes"] = market_horizon_minutes
    data["market_id"] = (
        str(market_horizon_minutes) + "m-" + data["round_start_s"].astype(str)
    )
    data["day"] = pd.to_datetime(data["round_start_s"], unit="s", utc=True).dt.strftime(
        "%Y-%m-%d"
    )
    return data.sort_values(["market_id", "timestamp_s"]).reset_index(drop=True)


def assign_development_split(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    markets = (
        data[["market_id", "round_start_s"]]
        .drop_duplicates("market_id")
        .sort_values(["round_start_s", "market_id"])
        .reset_index(drop=True)
    )
    count = len(markets)
    fit_end = max(1, int(count * 0.60))
    calibration_end = max(fit_end + 1, int(count * 0.80))
    calibration_end = min(calibration_end, count - 1)
    mapping: dict[str, str] = {}
    for index, market_id in enumerate(markets["market_id"]):
        mapping[market_id] = (
            "fit"
            if index < fit_end
            else "calibration"
            if index < calibration_end
            else "selection"
        )
    data["development_split"] = data["market_id"].map(mapping)
    if bool((data.groupby("market_id")["development_split"].nunique() != 1).any()):
        raise AssertionError("market crosses development splits")
    return data


def add_accumulator_features(
    frame: pd.DataFrame,
    weights: dict[int, float],
    half_life_seconds: int,
) -> pd.DataFrame:
    data = frame.copy()
    instantaneous = np.zeros(len(data), dtype=float)
    movement = np.zeros(len(data), dtype=float)
    for horizon, weight in weights.items():
        instantaneous += (
            weight * logit(data[f"p_up_{horizon}"].to_numpy()) * data[f"p_move_{horizon}"]
        )
        movement += weight * data[f"p_move_{horizon}"].to_numpy(float)
    data["instantaneous_score"] = instantaneous * data["quality_score"].to_numpy(float)
    data["movement_score"] = movement

    alpha = 1.0 - math.exp(-math.log(2.0) * 5.0 / half_life_seconds)
    persistent = np.empty(len(data), dtype=float)
    slope = np.empty(len(data), dtype=float)
    market_ids = data["market_id"].to_numpy()
    for index, score in enumerate(data["instantaneous_score"].to_numpy(float)):
        if index == 0 or market_ids[index] != market_ids[index - 1]:
            persistent[index] = score
            slope[index] = 0.0
        else:
            persistent[index] = alpha * score + (1.0 - alpha) * persistent[index - 1]
            slope[index] = persistent[index] - persistent[index - 1]
    data["persistent_score"] = persistent
    data["score_slope"] = slope
    positive = pd.Series((instantaneous >= 0).astype(float), index=data.index)
    positive_ratio = (
        positive.groupby(data["market_id"], sort=False)
        .rolling(10, min_periods=10)
        .mean()
        .reset_index(level=0, drop=True)
    )
    data["agreement_ratio"] = np.where(
        persistent >= 0, positive_ratio, 1.0 - positive_ratio
    )
    data["late_evidence"] = persistent * (1.0 - data["seconds_ratio"])
    return data


def candidate_episodes(
    frame: pd.DataFrame, state_config: dict[str, Any]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    candidates: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    for market_id, group in frame.groupby("market_id", sort=False):
        state = "NEUTRAL"
        watch_side = 0
        confirmed = False
        sign_history: deque[int] = deque(
            maxlen=int(state_config["agreement_observations"])
        )
        for _, row in group.sort_values("timestamp_s").iterrows():
            score = float(row["persistent_score"])
            side = 1 if score >= 0 else -1
            sign_history.append(1 if float(row["instantaneous_score"]) >= 0 else -1)
            agreement = (
                sum(value == side for value in sign_history) / len(sign_history)
                if sign_history
                else 0.0
            )
            full_history = len(sign_history) == sign_history.maxlen
            five_side = 1 if float(row["p_up_5"]) >= 0.5 else -1
            fifteen_side = 1 if float(row["p_up_15"]) >= 0.5 else -1
            thirty_ok = (
                float(row["p_up_30"]) >= state_config["long_30s_veto_below"]
                if side > 0
                else float(row["p_up_30"]) <= state_config["short_30s_veto_above"]
            )
            sixty_ok = (
                float(row["p_up_60"]) >= state_config["long_60s_veto_below"]
                if side > 0
                else float(row["p_up_60"]) <= state_config["short_60s_veto_above"]
            )
            shared = (
                five_side == side
                and fifteen_side == side
                and thirty_ok
                and sixty_ok
                and float(row["movement_score"])
                >= state_config["minimum_weighted_movement_probability"]
                and float(row["quality_score"]) >= 1.0
            )
            watch = shared and abs(score) >= state_config["watch_absolute_score"]
            confirm = (
                watch
                and full_history
                and abs(score) >= state_config["confirm_absolute_score"]
                and agreement >= state_config["minimum_agreement_ratio"]
                and side * float(row["score_slope"])
                >= -state_config["maximum_adverse_score_slope"]
            )
            before = state
            if state == "NEUTRAL":
                if watch:
                    state = "WATCH_LONG" if side > 0 else "WATCH_SHORT"
                    watch_side = side
            elif state in {"WATCH_LONG", "WATCH_SHORT"}:
                if not watch or side != watch_side:
                    state = (
                        ("WATCH_LONG" if side > 0 else "WATCH_SHORT")
                        if watch
                        else "NEUTRAL"
                    )
                    watch_side = side if watch else 0
                elif confirm and not confirmed:
                    state = "CONFIRMED_LONG" if side > 0 else "CONFIRMED_SHORT"
                    record = row.to_dict()
                    record.update(
                        {
                            "candidate_side": "LONG" if side > 0 else "SHORT",
                            "candidate_side_value": side,
                            "agreement_ratio": agreement,
                            "state_before": before,
                            "state_after": state,
                        }
                    )
                    candidates.append(record)
                    confirmed = True
            elif state in {"CONFIRMED_LONG", "CONFIRMED_SHORT"}:
                state = "COOLDOWN"
            if state != before:
                transitions.append(
                    {
                        "timestamp_s": int(row["timestamp_s"]),
                        "market_id": market_id,
                        "market_horizon_minutes": int(row["market_horizon_minutes"]),
                        "state_before": before,
                        "state_after": state,
                        "persistent_score": score,
                        "movement_score": float(row["movement_score"]),
                        "agreement_ratio": agreement,
                    }
                )
    candidate_frame = pd.DataFrame(candidates)
    if not candidate_frame.empty and bool(candidate_frame.duplicated("market_id").any()):
        raise AssertionError("state machine emitted multiple candidates for one market")
    return candidate_frame, pd.DataFrame(transitions)


BASELINE_FEATURES = ["z_distance", "seconds_ratio", "current_side", "rv_60s_bps"]
EVIDENCE_FEATURES = BASELINE_FEATURES + [
    "persistent_score",
    "late_evidence",
    "movement_score",
]


def market_weights(frame: pd.DataFrame) -> np.ndarray:
    counts = frame.groupby("market_id")["market_id"].transform("size").to_numpy(float)
    return 1.0 / np.maximum(counts, 1.0)


def fit_aggregators(frame: pd.DataFrame) -> dict[str, Any]:
    fit = frame[frame["development_split"] == "fit"]
    calibration = frame[frame["development_split"] == "calibration"]
    if fit.empty or calibration.empty:
        raise ValueError("empty aggregator fit/calibration period")
    models: dict[str, Any] = {}
    for name, features in (
        ("baseline", BASELINE_FEATURES),
        ("evidence", EVIDENCE_FEATURES),
    ):
        model = Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        C=0.25,
                        max_iter=1_000,
                        random_state=RANDOM_SEED,
                    ),
                ),
            ]
        )
        model.fit(
            fit[features],
            fit["settlement_up"],
            model__sample_weight=market_weights(fit),
        )
        raw_calibration = model.predict_proba(calibration[features])[:, 1]
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(
            raw_calibration,
            calibration["settlement_up"],
            sample_weight=market_weights(calibration),
        )
        models[name] = {
            "model": model,
            "calibrator": calibrator,
            "features": features,
        }
    return models


def predict_aggregators(frame: pd.DataFrame, bundle: dict[str, Any]) -> pd.DataFrame:
    data = frame.copy()
    for name, item in bundle.items():
        raw = item["model"].predict_proba(data[item["features"]])[:, 1]
        data[f"p_{name}"] = np.clip(item["calibrator"].predict(raw), 0.0, 1.0)
    return data


def candidate_metrics(
    frame: pd.DataFrame, scope: str, phase: str
) -> dict[str, Any]:
    if frame.empty:
        return {
            "phase": phase,
            "scope": scope,
            "candidates": 0,
        }
    target = frame["settlement_up"].to_numpy(int)
    side_correct = (
        ((frame["candidate_side_value"] > 0) & (target == 1))
        | ((frame["candidate_side_value"] < 0) & (target == 0))
    ).astype(int)
    successes = int(side_correct.sum())
    return {
        "phase": phase,
        "scope": scope,
        "candidates": len(frame),
        "unique_markets": int(frame["market_id"].nunique()),
        "unique_days": int(frame["day"].nunique()),
        "candidate_direction_accuracy": float(side_correct.mean()),
        "candidate_direction_wilson_lb": wilson_lower(successes, len(frame)),
        "candidate_day_block_lower_95": day_block_lower(
            frame.assign(side_correct=side_correct), "side_correct"
        ),
        **probability_metrics(target, frame["p_baseline"], "baseline"),
        **probability_metrics(target, frame["p_evidence"], "evidence"),
    }


def evaluate_scopes(frame: pd.DataFrame, phase: str) -> pd.DataFrame:
    rows = [candidate_metrics(frame, "all", phase)]
    if not frame.empty:
        for horizon, group in frame.groupby("market_horizon_minutes"):
            rows.append(candidate_metrics(group, f"{int(horizon)}m", phase))
    return pd.DataFrame(rows)


def run_configuration(
    development_frames: dict[int, pd.DataFrame],
    weights: dict[int, float],
    half_life: int,
    state_config: dict[str, Any],
) -> tuple[pd.DataFrame, dict[int, dict[str, Any]]]:
    candidate_parts: list[pd.DataFrame] = []
    bundles: dict[int, dict[str, Any]] = {}
    for market_horizon, base_frame in development_frames.items():
        scored = add_accumulator_features(base_frame, weights, half_life)
        candidates, _ = candidate_episodes(scored, state_config)
        bundle = fit_aggregators(scored)
        selection = candidates[candidates["development_split"] == "selection"].copy()
        if not selection.empty:
            selection = predict_aggregators(selection, bundle)
            candidate_parts.append(selection)
        bundles[market_horizon] = bundle
    combined = (
        pd.concat(candidate_parts, ignore_index=True)
        if candidate_parts
        else pd.DataFrame()
    )
    return combined, bundles


def select_configuration(
    development_frames: dict[int, pd.DataFrame],
    protocol: dict[str, Any],
) -> tuple[dict[str, Any], pd.DataFrame]:
    rows: list[dict[str, Any]] = []
    configurations: dict[tuple[str, int], dict[str, Any]] = {}
    minimum = int(protocol["selection"]["minimum_selection_candidates_per_market_horizon"])
    for scheme_name, raw_weights in protocol["weight_schemes"].items():
        weights = {int(key): float(value) for key, value in raw_weights.items()}
        for half_life in protocol["persistence_half_lives_seconds"]:
            log(f"[select] scheme={scheme_name} half_life={half_life}s")
            candidates, _ = run_configuration(
                development_frames,
                weights,
                int(half_life),
                protocol["state_machine"],
            )
            metrics = evaluate_scopes(candidates, "selection")
            per_horizon = metrics[metrics["scope"].isin(["5m", "15m"])]
            supported = (
                len(per_horizon) == 2
                and bool((per_horizon["candidates"] >= minimum).all())
            )
            overall = metrics[metrics["scope"] == "all"].iloc[0]
            brier_delta = (
                float(overall.get("evidence_brier", math.nan))
                - float(overall.get("baseline_brier", math.nan))
            )
            log_loss_delta = (
                float(overall.get("evidence_log_loss", math.nan))
                - float(overall.get("baseline_log_loss", math.nan))
            )
            for _, metric in metrics.iterrows():
                row = metric.to_dict()
                row.update(
                    {
                        "scheme": scheme_name,
                        "half_life_seconds": int(half_life),
                        "supported": supported,
                        "brier_delta": brier_delta,
                        "log_loss_delta": log_loss_delta,
                    }
                )
                rows.append(row)
            configurations[(scheme_name, int(half_life))] = {
                "scheme": scheme_name,
                "weights": weights,
                "half_life_seconds": int(half_life),
                "supported": supported,
                "brier_delta": brier_delta,
                "log_loss_delta": log_loss_delta,
            }
    summary = pd.DataFrame(rows)
    supported_configs = [item for item in configurations.values() if item["supported"]]
    if not supported_configs:
        raise ValueError("no accumulator configuration met the frozen selection support")
    selected = min(
        supported_configs,
        key=lambda item: (item["brier_delta"], item["log_loss_delta"], item["scheme"]),
    )
    return selected, summary


def locked_evaluation(
    selected: dict[str, Any],
    development_frames: dict[int, pd.DataFrame],
    locked_frames: dict[int, pd.DataFrame],
    state_config: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[int, dict[str, Any]]]:
    candidate_parts: list[pd.DataFrame] = []
    transition_parts: list[pd.DataFrame] = []
    bundles: dict[int, dict[str, Any]] = {}
    for market_horizon in sorted(development_frames):
        development = add_accumulator_features(
            development_frames[market_horizon],
            selected["weights"],
            selected["half_life_seconds"],
        )
        bundle = fit_aggregators(development)
        locked = add_accumulator_features(
            locked_frames[market_horizon],
            selected["weights"],
            selected["half_life_seconds"],
        )
        candidates, transitions = candidate_episodes(locked, state_config)
        if not candidates.empty:
            candidates = predict_aggregators(candidates, bundle)
            candidate_parts.append(candidates)
        if not transitions.empty:
            transition_parts.append(transitions)
        bundles[market_horizon] = bundle
    candidates = (
        pd.concat(candidate_parts, ignore_index=True)
        if candidate_parts
        else pd.DataFrame()
    )
    transitions = (
        pd.concat(transition_parts, ignore_index=True)
        if transition_parts
        else pd.DataFrame()
    )
    metrics = evaluate_scopes(candidates, "locked")
    return candidates, transitions, metrics, bundles


def evaluate_gates(metrics: pd.DataFrame, candidates: pd.DataFrame) -> dict[str, bool]:
    by_scope = metrics.set_index("scope")
    required_scopes = ["all", "5m", "15m"]
    has_scopes = all(scope in by_scope.index for scope in required_scopes)
    if not has_scopes:
        return {"required_scopes_present": False}
    positive_days = (
        candidates.assign(
            side_correct=(
                (
                    (candidates["candidate_side_value"] > 0)
                    & (candidates["settlement_up"] == 1)
                )
                | (
                    (candidates["candidate_side_value"] < 0)
                    & (candidates["settlement_up"] == 0)
                )
            ).astype(int)
        )
        .groupby("day")["side_correct"]
        .mean()
    )
    return {
        "at_least_200_independent_candidates": bool(by_scope.loc["all", "candidates"] >= 200),
        "at_least_50_candidates_each_horizon": bool(
            (by_scope.loc[["5m", "15m"], "candidates"] >= 50).all()
        ),
        "evidence_brier_better_all_scopes": bool(
            (
                by_scope.loc[required_scopes, "evidence_brier"]
                < by_scope.loc[required_scopes, "baseline_brier"]
            ).all()
        ),
        "evidence_log_loss_better_all_scopes": bool(
            (
                by_scope.loc[required_scopes, "evidence_log_loss"]
                < by_scope.loc[required_scopes, "baseline_log_loss"]
            ).all()
        ),
        "candidate_accuracy_above_half_all_scopes": bool(
            (by_scope.loc[required_scopes, "candidate_direction_accuracy"] > 0.5).all()
        ),
        "overall_wilson_lower_above_half": bool(
            by_scope.loc["all", "candidate_direction_wilson_lb"] > 0.5
        ),
        "positive_accuracy_at_least_75pct_days": bool(
            len(positive_days) > 0 and (positive_days > 0.5).mean() >= 0.75
        ),
    }


def write_results(
    run_dir: Path,
    selected: dict[str, Any],
    selection: pd.DataFrame,
    metrics: pd.DataFrame,
    candidates: pd.DataFrame,
    gates: dict[str, bool],
    elapsed: float,
) -> None:
    lines = [
        "# Event Evidence Accumulator Results",
        "",
        f"Run: `{run_dir.name}`",
        "",
        "Status: **COMPLETE - RESEARCH ONLY**",
        "",
        "## Selected Configuration",
        "",
        f"- Weight scheme: `{selected['scheme']}`",
        f"- Weights: `{json.dumps(selected['weights'], sort_keys=True)}`",
        f"- Persistence half-life: `{selected['half_life_seconds']}s`",
        f"- Selection Brier delta: `{selected['brier_delta']:.6f}`",
        "",
        "The configuration was selected on the older period only. The later period remained locked.",
        "",
        "## Locked Candidate Metrics",
        "",
        "| Scope | Candidates | Direction accuracy | Wilson LB | Baseline Brier | Evidence Brier | Baseline log loss | Evidence log loss |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, row in metrics.iterrows():
        lines.append(
            f"| {row.scope} | {int(row.candidates):,} | "
            f"{row.candidate_direction_accuracy:.2%} | "
            f"{row.candidate_direction_wilson_lb:.2%} | "
            f"{row.baseline_brier:.4f} | {row.evidence_brier:.4f} | "
            f"{row.baseline_log_loss:.4f} | {row.evidence_log_loss:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Frozen Continuation Gates",
            "",
            "| Gate | Result |",
            "|---|---|",
        ]
    )
    for name, passed in gates.items():
        lines.append(f"| {name.replace('_', ' ')} | {'PASS' if passed else 'FAIL'} |")
    lines.extend(
        [
            "",
            "## Evidence Boundary",
            "",
            f"- Raw locked event observations: `{164143:,}`.",
            f"- Independent locked candidates: `{len(candidates):,}`.",
            f"- Unique locked markets: `{candidates['market_id'].nunique() if not candidates.empty else 0:,}`.",
            f"- Unique locked days: `{candidates['day'].nunique() if not candidates.empty else 0:,}`.",
            "- The test reconstructs Binance UTC-aligned settlement direction.",
            "- It has no later-period executable Polymarket asks, fill path, fees or slippage.",
            "- It cannot establish profit and cannot promote a live or paper policy.",
            "",
            f"Configurations tested: `{selection[['scheme', 'half_life_seconds']].drop_duplicates().shape[0]}`.",
            f"Runtime: `{elapsed:.1f}s`.",
            "",
        ]
    )
    (run_dir / "RESULTS.md").write_text("\n".join(lines), encoding="utf-8")


def selftest() -> int:
    rows = 20
    timestamp = np.arange(1_700_000_000, 1_700_000_000 + rows * 5, 5)
    frame = pd.DataFrame(
        {
            "timestamp_s": timestamp,
            "market_id": ["5m-test"] * rows,
            "market_horizon_minutes": [5] * rows,
            "seconds_ratio": np.linspace(0.9, 0.2, rows),
            "z_distance": np.linspace(0.0, 1.0, rows),
            "current_side": [1] * rows,
            "rv_60s_bps": [0.2] * rows,
            "settlement_up": [1] * rows,
            "quality_score": [1.0] * rows,
            "development_split": ["selection"] * rows,
            "round_start_s": [timestamp[0]] * rows,
            "round_end_s": [timestamp[-1] + 5] * rows,
            "day": ["2023-11-14"] * rows,
        }
    )
    for horizon in (5, 15, 30, 60):
        frame[f"p_up_{horizon}"] = 0.8
        frame[f"p_move_{horizon}"] = 0.8
        frame[f"p_roundtrip_{horizon}"] = 0.1
    scored = add_accumulator_features(
        frame,
        {5: 0.55, 15: 0.30, 30: 0.10, 60: 0.05},
        10,
    )
    candidates, _ = candidate_episodes(
        scored,
        {
            "watch_absolute_score": 0.08,
            "confirm_absolute_score": 0.12,
            "minimum_weighted_movement_probability": 0.25,
            "agreement_observations": 10,
            "minimum_agreement_ratio": 0.8,
            "maximum_adverse_score_slope": 0.03,
            "long_30s_veto_below": 0.4,
            "short_30s_veto_above": 0.6,
            "long_60s_veto_below": 0.35,
            "short_60s_veto_above": 0.65,
        },
    )
    if len(candidates) != 1 or candidates.iloc[0]["candidate_side"] != "LONG":
        raise AssertionError("candidate state machine failed")
    mirrored = frame.copy()
    for horizon in (5, 15, 30, 60):
        mirrored[f"p_up_{horizon}"] = 1.0 - mirrored[f"p_up_{horizon}"]
    mirrored_scored = add_accumulator_features(
        mirrored,
        {5: 0.55, 15: 0.30, 30: 0.10, 60: 0.05},
        10,
    )
    if not np.allclose(
        scored["persistent_score"], -mirrored_scored["persistent_score"], atol=1e-12
    ):
        raise AssertionError("accumulator is not mirror symmetric")
    if wilson_lower(100, 100) <= 0.9:
        raise AssertionError("Wilson lower-bound test failed")
    print("SELFTEST PASS")
    return 0


def run(output_root: Path) -> Path:
    global _LOG_PATH
    started = time.perf_counter()
    protocol = load_protocol()
    run_dir = output_root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=False)
    _LOG_PATH = run_dir / "run.log"
    protocol_snapshot = run_dir / "frozen_protocol_snapshot.json"
    script_snapshot = run_dir / "campaign_script_snapshot.py"
    shutil.copy2(PROTOCOL_PATH, protocol_snapshot)
    shutil.copy2(Path(__file__).resolve(), script_snapshot)
    log(
        f"[start] protocol={protocol['protocol_id']} "
        f"hash={sha256_file(protocol_snapshot)}"
    )

    horizons = [int(value) for value in protocol["micro_horizons_seconds"]]
    development_path = resolve_input(protocol["inputs"]["development_predictions"])
    locked_path = resolve_input(protocol["inputs"]["locked_predictions"])
    development_aligned = align_prediction_heads(development_path, horizons)
    locked_aligned = align_prediction_heads(locked_path, horizons)
    minimum_ts = min(
        int(development_aligned["timestamp_s"].min()),
        int(locked_aligned["timestamp_s"].min()),
    )
    maximum_ts = max(
        int(development_aligned["timestamp_s"].max()),
        int(locked_aligned["timestamp_s"].max()),
    )
    dates = dates_for_price_window(minimum_ts, maximum_ts)
    prices = load_spot_seconds(dates)

    development_frames: dict[int, pd.DataFrame] = {}
    locked_frames: dict[int, pd.DataFrame] = {}
    for market_horizon in protocol["market_horizons_minutes"]:
        market_horizon = int(market_horizon)
        development_frames[market_horizon] = assign_development_split(
            build_market_frame(development_aligned, prices, market_horizon)
        )
        locked_frames[market_horizon] = build_market_frame(
            locked_aligned, prices, market_horizon
        )
        log(
            f"[rounds] h={market_horizon}m development="
            f"{development_frames[market_horizon]['market_id'].nunique()} locked="
            f"{locked_frames[market_horizon]['market_id'].nunique()}"
        )

    selected, selection = select_configuration(development_frames, protocol)
    log(
        f"[selected] scheme={selected['scheme']} "
        f"half_life={selected['half_life_seconds']}s "
        f"selection_brier_delta={selected['brier_delta']:.6f}"
    )
    candidates, transitions, locked_metrics, bundles = locked_evaluation(
        selected,
        development_frames,
        locked_frames,
        protocol["state_machine"],
    )
    gates = evaluate_gates(locked_metrics, candidates)
    continuation_passed = bool(all(gates.values()))
    log(
        f"[locked] candidates={len(candidates)} "
        f"accuracy={locked_metrics.loc[locked_metrics.scope == 'all', 'candidate_direction_accuracy'].iloc[0]:.2%} "
        f"continuation_passed={continuation_passed}"
    )

    selection.to_csv(run_dir / "configuration_selection.csv", index=False)
    candidates.to_parquet(run_dir / "locked_candidates.parquet", index=False)
    transitions.to_parquet(run_dir / "locked_state_transitions.parquet", index=False)
    locked_metrics.to_csv(run_dir / "locked_candidate_metrics.csv", index=False)
    if not candidates.empty:
        day_metrics = (
            candidates.assign(
                side_correct=(
                    (
                        (candidates["candidate_side_value"] > 0)
                        & (candidates["settlement_up"] == 1)
                    )
                    | (
                        (candidates["candidate_side_value"] < 0)
                        & (candidates["settlement_up"] == 0)
                    )
                ).astype(int)
            )
            .groupby(["day", "market_horizon_minutes"])
            .agg(
                candidates=("market_id", "size"),
                accuracy=("side_correct", "mean"),
                mean_score=("persistent_score", "mean"),
            )
            .reset_index()
        )
    else:
        day_metrics = pd.DataFrame()
    day_metrics.to_csv(run_dir / "candidate_by_day.csv", index=False)
    joblib.dump(
        {
            "protocol_id": protocol["protocol_id"],
            "selected_configuration": selected,
            "aggregators": bundles,
            "production_eligible": False,
            "event_head_models_included": False,
        },
        run_dir / "selected_research_aggregators.joblib",
        compress=3,
    )
    effective_sample = {
        "development_raw_prediction_rows": int(
            pd.read_parquet(development_path, columns=["timestamp_s"]).shape[0]
        ),
        "locked_raw_prediction_rows": int(
            pd.read_parquet(locked_path, columns=["timestamp_s"]).shape[0]
        ),
        "development_aligned_5s_observations": len(development_aligned),
        "locked_aligned_5s_observations": len(locked_aligned),
        "locked_independent_candidates": len(candidates),
        "locked_unique_markets": int(candidates["market_id"].nunique())
        if not candidates.empty
        else 0,
        "locked_unique_days": int(candidates["day"].nunique())
        if not candidates.empty
        else 0,
    }
    (run_dir / "effective_sample_size.json").write_text(
        json.dumps(effective_sample, indent=2), encoding="utf-8"
    )
    elapsed = time.perf_counter() - started
    write_results(
        run_dir,
        selected,
        selection,
        locked_metrics,
        candidates,
        gates,
        elapsed,
    )
    manifest = {
        "run_id": run_dir.name,
        "protocol": str(protocol_snapshot),
        "protocol_sha256": sha256_file(protocol_snapshot),
        "script": str(script_snapshot),
        "script_sha256": sha256_file(script_snapshot),
        "inputs": {
            "development_predictions": str(development_path),
            "development_sha256": sha256_file(development_path),
            "locked_predictions": str(locked_path),
            "locked_sha256": sha256_file(locked_path),
            "spot_trade_dates": dates,
        },
        "selected_configuration": selected,
        "continuation_gates_passed": continuation_passed,
        "production_artifacts_changed": False,
        "eligible_for_production": False,
        "economic_evidence_available": False,
        "elapsed_seconds": elapsed,
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(json_safe(manifest), indent=2), encoding="utf-8"
    )
    log(f"[done] output={run_dir} elapsed={elapsed:.1f}s")
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    run(args.output_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
