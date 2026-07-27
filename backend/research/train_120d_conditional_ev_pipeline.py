#!/usr/bin/env python
"""Purged 120-day magnitude -> conditional direction -> EV experiment.

Research only. The primary policy is frozen before results:

* magnitude: P(abs(return) clears round-trip cost);
* direction: P(UP | magnitude event);
* distribution: q10/q50/q90 signed future return;
* ACT only when P(move)>=0.50, conditional direction confidence>=0.55, and
  the adverse quantile remains profitable after costs.

No output is loaded by the live application.
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import (
    ExtraTreesRegressor,
    HistGradientBoostingRegressor,
)
from sklearn.linear_model import Ridge
from sklearn.metrics import (
    mean_absolute_error,
    mean_pinball_loss,
    mean_squared_error,
    r2_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from train_120d_trade_policy_heads import (
    DATA_DIR,
    DEFAULT_MATRIX,
    binary_metrics,
    build_causal_features,
    build_side_labels,
    economic_metrics,
    impute_train_test,
    load_recent_matrix,
    make_expanding_folds,
    model_factory,
    parse_model_names,
    positive_probability,
    select_features,
    sha256_file,
)

DEFAULT_OUTPUT_ROOT = DATA_DIR / "research" / "conditional_ev_120d"
REGRESSOR_NAMES = (
    "ridge",
    "histgb",
    "extra_trees",
    "xgboost",
    "lightgbm",
    "catboost",
)
QUANTILE_NAMES = ("histgb", "lightgbm", "catboost")
QUANTILES = (0.10, 0.50, 0.90)
PRIMARY_MOVE_THRESHOLD = 0.50
PRIMARY_DIRECTION_CONFIDENCE = 0.55

_LOG_FILE: Path | None = None


@dataclass(frozen=True)
class Config:
    matrix: str
    days: int
    horizons: list[int]
    classifier_models: list[str]
    regressor_models: list[str]
    quantile_models: list[str]
    folds: int
    test_days: int
    fee_bps_per_side: float
    slippage_bps_per_side: float
    max_train_rows: int
    max_features: int
    threads: int
    run_name: str

    @property
    def cost_bps(self) -> float:
        return 2.0 * (self.fee_bps_per_side + self.slippage_bps_per_side)

    @property
    def stress_extra_bps(self) -> float:
        return self.slippage_bps_per_side


def log(message: str) -> None:
    line = f"{datetime.now(timezone.utc).strftime('%H:%M:%S')} {message}"
    print(line, flush=True)
    if _LOG_FILE is not None:
        _LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _LOG_FILE.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")


def parse_names(value: str, allowed: tuple[str, ...]) -> list[str]:
    names = [part.strip().lower() for part in value.split(",") if part.strip()]
    if names == ["all"]:
        return list(allowed)
    unknown = sorted(set(names) - set(allowed))
    if unknown:
        raise ValueError(f"unknown families: {unknown}")
    if not names:
        raise ValueError("at least one family is required")
    return names


def regressor_factory(name: str, threads: int) -> Callable[[], Any]:
    if name == "ridge":
        return lambda: Pipeline(
            [("scale", StandardScaler()), ("model", Ridge(alpha=10.0))]
        )
    if name == "histgb":
        return lambda: HistGradientBoostingRegressor(
            loss="squared_error",
            learning_rate=0.05,
            max_iter=140,
            max_depth=5,
            min_samples_leaf=50,
            l2_regularization=0.2,
            random_state=51,
        )
    if name == "extra_trees":
        return lambda: ExtraTreesRegressor(
            n_estimators=180,
            max_depth=10,
            min_samples_leaf=40,
            max_features="sqrt",
            n_jobs=threads,
            random_state=52,
        )
    if name == "xgboost":
        def make_xgb() -> Any:
            import xgboost as xgb

            return xgb.XGBRegressor(
                n_estimators=200,
                max_depth=4,
                learning_rate=0.03,
                subsample=0.8,
                colsample_bytree=0.8,
                n_jobs=threads,
                tree_method="hist",
                objective="reg:squarederror",
                random_state=53,
            )

        return make_xgb
    if name == "lightgbm":
        def make_lgbm() -> Any:
            import lightgbm as lgb

            return lgb.LGBMRegressor(
                n_estimators=200,
                max_depth=5,
                learning_rate=0.03,
                subsample=0.8,
                colsample_bytree=0.8,
                n_jobs=threads,
                verbosity=-1,
                random_state=54,
            )

        return make_lgbm
    if name == "catboost":
        def make_catboost() -> Any:
            from catboost import CatBoostRegressor

            return CatBoostRegressor(
                iterations=200,
                depth=5,
                learning_rate=0.03,
                loss_function="RMSE",
                thread_count=threads,
                allow_writing_files=False,
                verbose=False,
                random_seed=55,
            )

        return make_catboost
    raise ValueError(name)


def quantile_factory(name: str, quantile: float, threads: int) -> Callable[[], Any]:
    if name == "histgb":
        return lambda: HistGradientBoostingRegressor(
            loss="quantile",
            quantile=quantile,
            learning_rate=0.05,
            max_iter=140,
            max_depth=5,
            min_samples_leaf=50,
            l2_regularization=0.2,
            random_state=61 + int(quantile * 10),
        )
    if name == "lightgbm":
        def make_lgbm() -> Any:
            import lightgbm as lgb

            return lgb.LGBMRegressor(
                objective="quantile",
                alpha=quantile,
                n_estimators=200,
                max_depth=5,
                learning_rate=0.03,
                n_jobs=threads,
                verbosity=-1,
                random_state=62 + int(quantile * 10),
            )

        return make_lgbm
    if name == "catboost":
        def make_catboost() -> Any:
            from catboost import CatBoostRegressor

            return CatBoostRegressor(
                iterations=200,
                depth=5,
                learning_rate=0.03,
                loss_function=f"Quantile:alpha={quantile}",
                thread_count=threads,
                allow_writing_files=False,
                verbose=False,
                random_seed=63 + int(quantile * 10),
            )

        return make_catboost
    raise ValueError(name)


def regression_metrics(y_true: np.ndarray, prediction: np.ndarray) -> dict[str, float | int]:
    y = np.asarray(y_true, dtype=float)
    pred = np.asarray(prediction, dtype=float)
    return {
        "n": len(y),
        "mae_bps": float(mean_absolute_error(y, pred)),
        "rmse_bps": float(math.sqrt(mean_squared_error(y, pred))),
        "r2": float(r2_score(y, pred)),
        "spearman": float(pd.Series(y).corr(pd.Series(pred), method="spearman")),
        "direction_accuracy": float(((y > 0.0) == (pred > 0.0)).mean()),
    }


def quantile_metrics(
    y_true: np.ndarray, prediction: np.ndarray, quantile: float
) -> dict[str, float | int]:
    y = np.asarray(y_true, dtype=float)
    pred = np.asarray(prediction, dtype=float)
    return {
        "n": len(y),
        "quantile": quantile,
        "pinball": float(mean_pinball_loss(y, pred, alpha=quantile)),
        "coverage": float((y <= pred).mean()),
    }


def normalize_quantiles(
    q10: np.ndarray, q50: np.ndarray, q90: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray, float]:
    raw = np.column_stack([q10, q50, q90]).astype(float)
    crossing = float(((raw[:, 0] > raw[:, 1]) | (raw[:, 1] > raw[:, 2])).mean())
    ordered = np.sort(raw, axis=1)
    return ordered[:, 0], ordered[:, 1], ordered[:, 2], crossing


def policy_columns(frame: pd.DataFrame, cost_bps: float) -> pd.DataFrame:
    out = frame.copy()
    out["predicted_side"] = np.where(out["p_up_given_move"] >= 0.5, "LONG", "SHORT")
    out["direction_confidence"] = np.maximum(
        out["p_up_given_move"], 1.0 - out["p_up_given_move"]
    )
    out["candidate_net_bps"] = np.where(
        out["predicted_side"] == "LONG",
        out["long_net_bps"],
        out["short_net_bps"],
    )
    out["direction_correct"] = np.where(
        out["predicted_side"] == "LONG",
        out["gross_return_bps"] > 0.0,
        out["gross_return_bps"] < 0.0,
    ).astype(np.int8)
    base_gate = (
        (out["p_move"] >= PRIMARY_MOVE_THRESHOLD)
        & (out["direction_confidence"] >= PRIMARY_DIRECTION_CONFIDENCE)
    )
    safe_side = (
        ((out["predicted_side"] == "LONG") & (out["q10_return_bps"] > cost_bps))
        | ((out["predicted_side"] == "SHORT") & (-out["q90_return_bps"] > cost_bps))
    )
    median_side = (
        ((out["predicted_side"] == "LONG") & (out["q50_return_bps"] > cost_bps))
        | ((out["predicted_side"] == "SHORT") & (-out["q50_return_bps"] > cost_bps))
    )
    mean_side = (
        ((out["predicted_side"] == "LONG") & (out["mean_return_bps"] > cost_bps))
        | ((out["predicted_side"] == "SHORT") & (-out["mean_return_bps"] > cost_bps))
    )
    out["act_primary_q10"] = base_gate & safe_side
    out["act_median_diagnostic"] = base_gate & median_side
    out["act_mean_diagnostic"] = base_gate & mean_side
    return out


def day_block_lower_bound(
    timestamps_ms: np.ndarray,
    net_bps: np.ndarray,
    act_mask: np.ndarray,
    *,
    draws: int = 2000,
) -> float:
    acted = np.asarray(act_mask, dtype=bool)
    if not acted.any():
        return math.nan
    days = pd.to_datetime(
        np.asarray(timestamps_ms, dtype=np.int64)[acted], unit="ms", utc=True
    ).date
    sample = pd.DataFrame(
        {"day": days, "pnl": np.asarray(net_bps, dtype=float)[acted]}
    )
    groups = [group["pnl"].to_numpy() for _, group in sample.groupby("day")]
    if len(groups) < 5:
        return math.nan
    rng = np.random.default_rng(20260727)
    values = np.empty(draws, dtype=float)
    for draw in range(draws):
        chosen = rng.integers(0, len(groups), size=len(groups))
        values[draw] = float(np.concatenate([groups[index] for index in chosen]).mean())
    return float(np.quantile(values, 0.025))


def promotion_result(
    pooled: pd.DataFrame,
    fold_economics: list[dict[str, Any]],
    *,
    stress_extra_bps: float,
) -> dict[str, Any]:
    acted = pooled["act_primary_q10"].to_numpy(bool)
    economics = economic_metrics(
        pooled["timestamp_ms"].to_numpy(),
        pooled["candidate_net_bps"].to_numpy(),
        acted,
    )
    lower = day_block_lower_bound(
        pooled["timestamp_ms"].to_numpy(),
        pooled["candidate_net_bps"].to_numpy(),
        acted,
    )
    stressed = economic_metrics(
        pooled["timestamp_ms"].to_numpy(),
        pooled["candidate_net_bps"].to_numpy() - stress_extra_bps,
        acted,
    )
    positive_folds = sum(
        1
        for row in fold_economics
        if row["policy"] == "primary_q10"
        and math.isfinite(float(row.get("mean_net_bps", math.nan)))
        and float(row["mean_net_bps"]) > 0.0
    )
    primary_folds = [
        row for row in fold_economics if row["policy"] == "primary_q10"
    ]
    final_fold_positive = bool(
        primary_folds
        and math.isfinite(float(primary_folds[-1].get("mean_net_bps", math.nan)))
        and float(primary_folds[-1]["mean_net_bps"]) > 0.0
    )
    checks = {
        "mean_net_positive": bool(
            math.isfinite(float(economics["mean_net_bps"]))
            and float(economics["mean_net_bps"]) > 0.0
        ),
        "day_block_lower_bound_positive": bool(
            math.isfinite(lower) and lower > 0.0
        ),
        "profit_factor_gt_1_10": bool(
            not math.isnan(float(economics["profit_factor"]))
            and float(economics["profit_factor"]) > 1.10
        ),
        "trades_ge_200": int(economics["trades"]) >= 200,
        "coverage_ge_1pct": float(economics["coverage"]) >= 0.01,
        "positive_folds_ge_3": positive_folds >= 3,
        "final_fold_positive": final_fold_positive,
        "slippage_stress_positive": bool(
            math.isfinite(float(stressed["mean_net_bps"]))
            and float(stressed["mean_net_bps"]) > 0.0
        ),
    }
    return {
        "promote": all(checks.values()),
        "checks": checks,
        "day_block_lower_bound_bps": lower,
        "positive_folds": positive_folds,
        "stress_mean_net_bps": stressed["mean_net_bps"],
        "economics": economics,
    }


def json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(item) for item in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (int, np.integer)):
        return int(value)
    return value


def _decision_indices(
    test_idx: np.ndarray, timestamps: np.ndarray, horizon: int
) -> np.ndarray:
    return test_idx[((timestamps[test_idx] // 60_000) % horizon) == 0]


def _metric_row(
    horizon: int,
    fold: int | str,
    layer: str,
    target: str,
    model: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    return {
        "horizon": horizon,
        "fold": fold,
        "layer": layer,
        "target": target,
        "model": model,
        **values,
    }


def run(config: Config, output_dir: Path) -> dict[str, Any]:
    started = time.monotonic()
    source = Path(config.matrix).resolve()
    frame = load_recent_matrix(source, config.days)
    timestamps = pd.to_numeric(frame["ts_ms"], errors="raise").to_numpy(np.int64)
    features = build_causal_features(frame)
    all_metrics: list[dict[str, Any]] = []
    all_economics: list[dict[str, Any]] = []
    all_predictions: list[pd.DataFrame] = []
    skipped: dict[str, str] = {}
    crossings: dict[str, float] = {}

    for horizon in config.horizons:
        labels = build_side_labels(frame, horizon, config.cost_bps)
        labels["move_event"] = (
            labels["gross_return_bps"].abs() > config.cost_bps
        ).astype(float)
        labels["direction_up"] = (labels["gross_return_bps"] > 0.0).astype(float)
        labels.loc[~labels["valid"], ["move_event", "direction_up"]] = np.nan
        folds = make_expanding_folds(
            timestamps,
            folds=config.folds,
            test_days=config.test_days,
            embargo_minutes=horizon,
        )
        first_train = folds[0].train_idx[
            labels.iloc[folds[0].train_idx]["valid"].to_numpy(bool)
        ]
        feature_columns = select_features(features, first_train, config.max_features)
        horizon_parts: list[pd.DataFrame] = []
        log(
            f"[h={horizon}m] features={len(feature_columns)} "
            f"move_rate={labels.move_event.mean():.2%}"
        )

        for fold in folds:
            fold_started = time.monotonic()
            train_idx = fold.train_idx[
                labels.iloc[fold.train_idx]["valid"].to_numpy(bool)
            ]
            if config.max_train_rows > 0 and len(train_idx) > config.max_train_rows:
                train_idx = train_idx[-config.max_train_rows :]
            decision_idx = _decision_indices(fold.test_idx, timestamps, horizon)
            decision_idx = decision_idx[
                labels.iloc[decision_idx]["valid"].to_numpy(bool)
            ]
            x_train, x_test, _ = impute_train_test(
                features, train_idx, decision_idx, feature_columns
            )
            move_train = labels.iloc[train_idx]["move_event"].to_numpy(np.int8)
            direction_train_all = labels.iloc[train_idx]["direction_up"].to_numpy(np.int8)
            move_mask = move_train == 1
            y_return_train = labels.iloc[train_idx][
                "gross_return_bps"
            ].to_numpy(float)
            out = pd.DataFrame(
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
                    "move_event": labels.iloc[decision_idx][
                        "move_event"
                    ].to_numpy(np.int8),
                    "direction_up": labels.iloc[decision_idx][
                        "direction_up"
                    ].to_numpy(np.int8),
                }
            )

            classifier_success: list[str] = []
            for family in config.classifier_models:
                try:
                    family_started = time.monotonic()
                    magnitude_model = model_factory(family, config.threads)()
                    magnitude_model.fit(x_train, move_train)
                    out[f"p_move_{family}"] = positive_probability(
                        magnitude_model, x_test
                    )
                    del magnitude_model
                    gc.collect()

                    direction_model = model_factory(family, config.threads)()
                    direction_model.fit(
                        x_train[move_mask], direction_train_all[move_mask]
                    )
                    out[f"p_up_given_move_{family}"] = positive_probability(
                        direction_model, x_test
                    )
                    del direction_model
                    gc.collect()
                    classifier_success.append(family)
                    log(
                        f"[h={horizon} fold={fold.number}] classifiers {family} "
                        f"{time.monotonic() - family_started:.1f}s"
                    )
                except Exception as exc:  # noqa: BLE001 - optional family boundary
                    skipped[f"h{horizon}_f{fold.number}_classifier_{family}"] = str(exc)
                    log(f"[skip] classifier {family}: {exc}")
            if not classifier_success:
                raise RuntimeError("no classifier family succeeded")
            out["p_move"] = out[
                [f"p_move_{name}" for name in classifier_success]
            ].mean(axis=1)
            out["p_up_given_move"] = out[
                [f"p_up_given_move_{name}" for name in classifier_success]
            ].mean(axis=1)
            all_metrics.append(
                _metric_row(
                    horizon,
                    fold.number,
                    "magnitude",
                    "MOVE",
                    "mean_ensemble",
                    binary_metrics(out["move_event"], out["p_move"]),
                )
            )
            actual_move = out["move_event"].to_numpy(bool)
            if actual_move.any():
                all_metrics.append(
                    _metric_row(
                        horizon,
                        fold.number,
                        "conditional_direction",
                        "UP_GIVEN_MOVE",
                        "mean_ensemble",
                        binary_metrics(
                            out.loc[actual_move, "direction_up"],
                            out.loc[actual_move, "p_up_given_move"],
                        ),
                    )
                )

            regressor_success: list[str] = []
            for family in config.regressor_models:
                try:
                    family_started = time.monotonic()
                    model = regressor_factory(family, config.threads)()
                    model.fit(x_train, y_return_train)
                    out[f"mean_return_{family}"] = np.asarray(
                        model.predict(x_test), dtype=float
                    )
                    del model
                    gc.collect()
                    regressor_success.append(family)
                    log(
                        f"[h={horizon} fold={fold.number}] regressor {family} "
                        f"{time.monotonic() - family_started:.1f}s"
                    )
                except Exception as exc:  # noqa: BLE001 - optional family boundary
                    skipped[f"h{horizon}_f{fold.number}_regressor_{family}"] = str(exc)
                    log(f"[skip] regressor {family}: {exc}")
            if not regressor_success:
                raise RuntimeError("no return regressor succeeded")
            out["mean_return_bps"] = out[
                [f"mean_return_{name}" for name in regressor_success]
            ].mean(axis=1)
            all_metrics.append(
                _metric_row(
                    horizon,
                    fold.number,
                    "return_regression",
                    "SIGNED_RETURN_BPS",
                    "mean_ensemble",
                    regression_metrics(
                        out["gross_return_bps"], out["mean_return_bps"]
                    ),
                )
            )

            quantile_columns: dict[float, list[str]] = {
                quantile: [] for quantile in QUANTILES
            }
            for family in config.quantile_models:
                for quantile in QUANTILES:
                    try:
                        family_started = time.monotonic()
                        model = quantile_factory(
                            family, quantile, config.threads
                        )()
                        model.fit(x_train, y_return_train)
                        column = f"q{int(quantile * 100):02d}_{family}"
                        out[column] = np.asarray(model.predict(x_test), dtype=float)
                        quantile_columns[quantile].append(column)
                        del model
                        gc.collect()
                        log(
                            f"[h={horizon} fold={fold.number}] quantile "
                            f"{family} q={quantile:.2f} "
                            f"{time.monotonic() - family_started:.1f}s"
                        )
                    except Exception as exc:  # noqa: BLE001 - optional family boundary
                        key = (
                            f"h{horizon}_f{fold.number}_quantile_"
                            f"{family}_{quantile}"
                        )
                        skipped[key] = str(exc)
                        log(f"[skip] quantile {family} {quantile}: {exc}")
            if any(not columns for columns in quantile_columns.values()):
                raise RuntimeError("one or more quantile levels have no model")
            raw_quantiles = {
                quantile: out[columns].mean(axis=1).to_numpy(float)
                for quantile, columns in quantile_columns.items()
            }
            q10, q50, q90, crossing = normalize_quantiles(
                raw_quantiles[0.10],
                raw_quantiles[0.50],
                raw_quantiles[0.90],
            )
            out["q10_return_bps"] = q10
            out["q50_return_bps"] = q50
            out["q90_return_bps"] = q90
            crossings[f"h{horizon}_fold{fold.number}"] = crossing
            for quantile, column in (
                (0.10, "q10_return_bps"),
                (0.50, "q50_return_bps"),
                (0.90, "q90_return_bps"),
            ):
                all_metrics.append(
                    _metric_row(
                        horizon,
                        fold.number,
                        "return_quantile",
                        "SIGNED_RETURN_BPS",
                        f"ensemble_q{int(quantile * 100):02d}",
                        quantile_metrics(
                            out["gross_return_bps"], out[column], quantile
                        ),
                    )
                )

            out = policy_columns(out, config.cost_bps)
            for policy, column in (
                ("always_direction", None),
                ("primary_q10", "act_primary_q10"),
                ("median_diagnostic", "act_median_diagnostic"),
                ("mean_diagnostic", "act_mean_diagnostic"),
            ):
                act = (
                    np.ones(len(out), dtype=bool)
                    if column is None
                    else out[column].to_numpy(bool)
                )
                values = economic_metrics(
                    out["timestamp_ms"].to_numpy(),
                    out["candidate_net_bps"].to_numpy(),
                    act,
                )
                row = {
                    "horizon": horizon,
                    "fold": fold.number,
                    "policy": policy,
                    **values,
                }
                all_economics.append(row)
            horizon_parts.append(out)
            log(
                f"[h={horizon} fold={fold.number}/{config.folds}] complete "
                f"train={len(train_idx):,} move_train={int(move_mask.sum()):,} "
                f"test={len(out):,} elapsed={time.monotonic() - fold_started:.1f}s"
            )

        pooled = pd.concat(horizon_parts, ignore_index=True)
        all_predictions.append(pooled)
        all_metrics.append(
            _metric_row(
                horizon,
                "POOLED",
                "magnitude",
                "MOVE",
                "mean_ensemble",
                binary_metrics(pooled["move_event"], pooled["p_move"]),
            )
        )
        actual_move = pooled["move_event"].to_numpy(bool)
        all_metrics.append(
            _metric_row(
                horizon,
                "POOLED",
                "conditional_direction",
                "UP_GIVEN_MOVE",
                "mean_ensemble",
                binary_metrics(
                    pooled.loc[actual_move, "direction_up"],
                    pooled.loc[actual_move, "p_up_given_move"],
                ),
            )
        )
        all_metrics.append(
            _metric_row(
                horizon,
                "POOLED",
                "return_regression",
                "SIGNED_RETURN_BPS",
                "mean_ensemble",
                regression_metrics(
                    pooled["gross_return_bps"], pooled["mean_return_bps"]
                ),
            )
        )
        for quantile, column in (
            (0.10, "q10_return_bps"),
            (0.50, "q50_return_bps"),
            (0.90, "q90_return_bps"),
        ):
            all_metrics.append(
                _metric_row(
                    horizon,
                    "POOLED",
                    "return_quantile",
                    "SIGNED_RETURN_BPS",
                    f"ensemble_q{int(quantile * 100):02d}",
                    quantile_metrics(
                        pooled["gross_return_bps"], pooled[column], quantile
                    ),
                )
            )
        for policy, column in (
            ("always_direction", None),
            ("primary_q10", "act_primary_q10"),
            ("median_diagnostic", "act_median_diagnostic"),
            ("mean_diagnostic", "act_mean_diagnostic"),
        ):
            act = (
                np.ones(len(pooled), dtype=bool)
                if column is None
                else pooled[column].to_numpy(bool)
            )
            all_economics.append(
                {
                    "horizon": horizon,
                    "fold": "POOLED",
                    "policy": policy,
                    **economic_metrics(
                        pooled["timestamp_ms"].to_numpy(),
                        pooled["candidate_net_bps"].to_numpy(),
                        act,
                    ),
                }
            )

    predictions = pd.concat(all_predictions, ignore_index=True)
    metrics = pd.DataFrame(all_metrics)
    economics = pd.DataFrame(all_economics)
    output_dir.mkdir(parents=True, exist_ok=True)
    metrics.to_csv(output_dir / "metrics.csv", index=False)
    economics.to_csv(output_dir / "economics.csv", index=False)
    predictions.to_csv(output_dir / "oof_predictions.csv", index=False)
    predictions.to_parquet(output_dir / "oof_predictions.parquet", index=False)

    promotion: dict[str, Any] = {}
    for horizon in config.horizons:
        pooled = predictions[predictions["horizon"] == horizon]
        folds = [
            row
            for row in all_economics
            if row["horizon"] == horizon and row["fold"] != "POOLED"
        ]
        promotion[str(horizon)] = promotion_result(
            pooled,
            folds,
            stress_extra_bps=config.stress_extra_bps,
        )
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "research_only": True,
        "auto_deployed": False,
        "frozen_primary_rule": {
            "move_probability_min": PRIMARY_MOVE_THRESHOLD,
            "conditional_direction_confidence_min": PRIMARY_DIRECTION_CONFIDENCE,
            "long": "q10_return_bps > round_trip_cost_bps",
            "short": "-q90_return_bps > round_trip_cost_bps",
        },
        "promotion_requirements": {
            "mean_net_bps": ">0",
            "day_block_lower_bound_bps": ">0",
            "profit_factor": ">1.10",
            "trades": ">=200",
            "coverage": ">=1%",
            "positive_folds": ">=3/4",
            "final_fold": "positive",
            "slippage_stress": "positive with 50% higher slippage",
        },
        "config": asdict(config),
        "source": {
            "path": str(source),
            "rows": len(frame),
            "first_ts_ms": int(timestamps[0]),
            "last_ts_ms": int(timestamps[-1]),
            "sha256": sha256_file(source),
        },
        "quantile_crossing_rates_before_ordering": crossings,
        "skipped": skipped,
        "promotion": promotion,
        "elapsed_seconds": time.monotonic() - started,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(json_safe(manifest), indent=2, allow_nan=False, default=str),
        encoding="utf-8",
    )
    log(
        f"[done] output={output_dir} elapsed={manifest['elapsed_seconds']:.1f}s "
        f"predictions={len(predictions):,} promotion="
        f"{ {key: value['promote'] for key, value in promotion.items()} }"
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matrix", default=str(DEFAULT_MATRIX))
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--horizons", type=int, nargs="+", default=[5, 15])
    parser.add_argument("--classifier-models", default="all")
    parser.add_argument("--regressor-models", default="all")
    parser.add_argument("--quantile-models", default="all")
    parser.add_argument("--folds", type=int, default=4)
    parser.add_argument("--test-days", type=int, default=15)
    parser.add_argument("--fee-bps-per-side", type=float, default=5.0)
    parser.add_argument("--slippage-bps-per-side", type=float, default=1.0)
    parser.add_argument("--max-train-rows", type=int, default=0)
    parser.add_argument("--max-features", type=int, default=80)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--log-file", default="")
    return parser.parse_args()


def main() -> None:
    global _LOG_FILE
    args = parse_args()
    run_name = args.run_name or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = DEFAULT_OUTPUT_ROOT / run_name
    _LOG_FILE = (
        Path(args.log_file).resolve()
        if args.log_file
        else output_dir / "run.log"
    )
    config = Config(
        matrix=str(Path(args.matrix).resolve()),
        days=max(7, args.days),
        horizons=sorted(set(args.horizons)),
        classifier_models=parse_model_names(args.classifier_models),
        regressor_models=parse_names(args.regressor_models, REGRESSOR_NAMES),
        quantile_models=parse_names(args.quantile_models, QUANTILE_NAMES),
        folds=max(2, args.folds),
        test_days=max(1, args.test_days),
        fee_bps_per_side=max(0.0, args.fee_bps_per_side),
        slippage_bps_per_side=max(0.0, args.slippage_bps_per_side),
        max_train_rows=max(0, args.max_train_rows),
        max_features=max(1, args.max_features),
        threads=max(1, args.threads),
        run_name=run_name,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    log(f"[start] config={json.dumps(asdict(config), sort_keys=True)}")
    run(config, output_dir)


if __name__ == "__main__":
    main()
