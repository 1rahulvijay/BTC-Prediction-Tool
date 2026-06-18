#!/usr/bin/env python
"""
Run a multi-head BTC up/down bakeoff on the Binance anchor dataset.

This script is report-only. It does not modify live app models or saved model
bundles. It trains on the oldest 70% of rounds and predicts/scores the newest
30%, grouped by round_id to avoid same-round leakage.

Outputs:
  data/research/updown_bakeoff_metrics.csv
  data/research/updown_bakeoff_predictions.csv       optional, enabled by --save-predictions
  data/research/updown_bakeoff_run_summary.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import (
    ExtraTreesClassifier,
    ExtraTreesRegressor,
    GradientBoostingRegressor,
    HistGradientBoostingClassifier,
    HistGradientBoostingRegressor,
    RandomForestClassifier,
    RandomForestRegressor,
)
from sklearn.linear_model import ElasticNet, LogisticRegression, Ridge
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    f1_score,
    log_loss,
    mean_absolute_error,
    mean_pinball_loss,
    mean_squared_error,
    precision_score,
    recall_score,
    r2_score,
    roc_auc_score,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESEARCH = ROOT / "data" / "research"
THREADS = max(1, int(os.environ.get("BTC_BAKEOFF_THREADS", "4")))
RANDOM_STATE = 42


CLASSIFICATION_TASKS = {
    "up_win": {
        "target": "target_up_win",
        "models": ("majority", "analytic_up", "logistic", "histgb", "lightgbm", "xgboost", "catboost"),
        "notes": "Raw UP/DOWN winner. Expected to be hard; reject unless stable OOS edge exists.",
    },
    "current_side_hold": {
        "target": "target_current_side_hold",
        "models": ("majority", "analytic_hold", "last_side_persistence", "logistic", "histgb", "lightgbm", "xgboost", "catboost", "rf", "extra_trees"),
        "notes": "Core fair-value model: if current side is ahead, does it stay ahead?",
    },
    "line_cross": {
        "target": "target_line_cross",
        "models": ("majority", "logistic", "histgb", "lightgbm", "xgboost", "catboost"),
        "notes": "Danger head. Used to block trades when flip/anchor-cross risk is high.",
    },
    "big_move_10bps": {
        "target": "target_big_move_10bps",
        "models": ("majority", "logistic", "histgb", "lightgbm", "xgboost", "rf", "extra_trees"),
        "notes": "Tradability/timing head. Use percentile thresholds, not raw side direction.",
    },
    "big_move_20bps": {
        "target": "target_big_move_20bps",
        "models": ("majority", "logistic", "histgb", "lightgbm", "xgboost", "rf", "extra_trees"),
        "notes": "Stricter tradability/timing head.",
    },
}


REGRESSION_TASKS = {
    "expiry_return_bps": "target_expiry_return_bps",
    "max_up_bps": "target_max_up_bps",
    "max_down_bps": "target_max_down_bps",
    "range_bps": "target_range_bps",
    "log_quote_volume": "target_log_quote_volume",
    "log_volume": "target_log_volume",
    "log_trades": "target_log_trades",
}


def expected_calibration_error(y_true, prob, bins=10) -> float:
    y = np.asarray(y_true, dtype=float)
    p = np.clip(np.asarray(prob, dtype=float), 1e-6, 1 - 1e-6)
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for i in range(bins):
        if i == bins - 1:
            mask = (p >= edges[i]) & (p <= edges[i + 1])
        else:
            mask = (p >= edges[i]) & (p < edges[i + 1])
        n = int(mask.sum())
        if n:
            ece += (n / len(y)) * abs(float(y[mask].mean()) - float(p[mask].mean()))
    return float(ece)


def safe_auc(y_true, prob) -> float:
    return float(roc_auc_score(y_true, prob)) if len(np.unique(y_true)) > 1 else 0.5


def precision_at_top(y_true, score, pct: float) -> tuple[float, int, float]:
    y = np.asarray(y_true, dtype=int)
    s = np.asarray(score, dtype=float)
    if len(y) == 0:
        return 0.0, 0, 0.0
    n = max(1, int(np.ceil(len(y) * pct)))
    idx = np.argsort(s)[-n:]
    base = float(y.mean()) if len(y) else 0.0
    prec = float(y[idx].mean()) if len(idx) else 0.0
    lift = float(prec / base) if base > 0 else 0.0
    return prec, int(n), lift


def threshold_realized(y_true, prob, threshold: float) -> tuple[int, float]:
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(prob, dtype=float)
    mask = p >= threshold
    if int(mask.sum()) == 0:
        return 0, float("nan")
    return int(mask.sum()), float(y[mask].mean())


def binary_metrics(y_true, prob, task_name: str) -> dict:
    y = np.asarray(y_true, dtype=int)
    p = np.clip(np.asarray(prob, dtype=float), 1e-6, 1 - 1e-6)
    pred = (p >= 0.5).astype(int)
    p5, n5, lift5 = precision_at_top(y, p, 0.05)
    p10, n10, lift10 = precision_at_top(y, p, 0.10)
    hi90_n, hi90_rate = threshold_realized(y, p, 0.90)
    hi93_n, hi93_rate = threshold_realized(y, p, 0.93)
    hi95_n, hi95_rate = threshold_realized(y, p, 0.95)

    out = {
        "n_test": int(len(y)),
        "positive_rate": float(y.mean()) if len(y) else 0.0,
        "accuracy": float(accuracy_score(y, pred)),
        "auc": safe_auc(y, p),
        "brier": float(brier_score_loss(y, p)),
        "log_loss": float(log_loss(y, p, labels=[0, 1])),
        "ece_10bin": expected_calibration_error(y, p),
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "precision_top5_pct": p5,
        "top5_n": n5,
        "lift_top5": lift5,
        "precision_top10_pct": p10,
        "top10_n": n10,
        "lift_top10": lift10,
        "p_ge_90_n": hi90_n,
        "p_ge_90_realized": hi90_rate,
        "p_ge_93_n": hi93_n,
        "p_ge_93_realized": hi93_rate,
        "p_ge_95_n": hi95_n,
        "p_ge_95_realized": hi95_rate,
    }
    if task_name == "line_cross":
        safe_mask = p <= 0.25
        danger_pred = (p > 0.25).astype(int)
        out["line_cross_recall_at_025"] = float(recall_score(y, danger_pred, zero_division=0))
        out["line_cross_precision_at_025"] = float(precision_score(y, danger_pred, zero_division=0))
        out["false_safe_rate_at_025"] = float(y[safe_mask].mean()) if int(safe_mask.sum()) else float("nan")
        out["safe_allowed_n_at_025"] = int(safe_mask.sum())
    return out


def regression_metrics(y_true, pred) -> dict:
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(pred, dtype=float)
    rmse = mean_squared_error(y, p) ** 0.5
    out = {
        "n_test": int(len(y)),
        "mae": float(mean_absolute_error(y, p)),
        "rmse": float(rmse),
        "r2": float(r2_score(y, p)) if len(y) > 2 else 0.0,
        "spearman": float(pd.Series(y).corr(pd.Series(p), method="spearman")),
    }
    if np.nanstd(y) > 0:
        out["target_std"] = float(np.nanstd(y))
    if np.any(y != 0):
        out["direction_from_prediction_acc"] = float(np.mean((p >= 0) == (y >= 0)))
    return out


def optional_classifier_models():
    models = {}
    try:
        from lightgbm import LGBMClassifier
        models["lightgbm"] = LGBMClassifier(
            n_estimators=350, learning_rate=0.03, num_leaves=31, subsample=0.85,
            colsample_bytree=0.85, random_state=RANDOM_STATE, n_jobs=THREADS, verbose=-1
        )
    except Exception:
        pass
    try:
        from xgboost import XGBClassifier
        models["xgboost"] = XGBClassifier(
            n_estimators=300, max_depth=4, learning_rate=0.03, subsample=0.85,
            colsample_bytree=0.85, eval_metric="logloss", random_state=RANDOM_STATE,
            n_jobs=THREADS, tree_method="hist",
        )
    except Exception:
        pass
    try:
        from catboost import CatBoostClassifier
        models["catboost"] = CatBoostClassifier(
            iterations=300, depth=4, learning_rate=0.04, verbose=False,
            random_seed=RANDOM_STATE, thread_count=THREADS, allow_writing_files=False
        )
    except Exception:
        pass
    return models


def optional_regressor_models():
    models = {}
    try:
        from lightgbm import LGBMRegressor
        models["lightgbm_reg"] = LGBMRegressor(
            n_estimators=350, learning_rate=0.03, num_leaves=31, subsample=0.85,
            colsample_bytree=0.85, random_state=RANDOM_STATE, n_jobs=THREADS, verbose=-1
        )
    except Exception:
        pass
    try:
        from xgboost import XGBRegressor
        models["xgboost_reg"] = XGBRegressor(
            n_estimators=300, max_depth=4, learning_rate=0.03, subsample=0.85,
            colsample_bytree=0.85, random_state=RANDOM_STATE, n_jobs=THREADS, tree_method="hist",
        )
    except Exception:
        pass
    try:
        from catboost import CatBoostRegressor
        models["catboost_reg"] = CatBoostRegressor(
            iterations=300, depth=4, learning_rate=0.04, verbose=False,
            random_seed=RANDOM_STATE, thread_count=THREADS, allow_writing_files=False
        )
    except Exception:
        pass
    return models


def classifier_registry():
    base = {
        "logistic": make_pipeline(
            StandardScaler(),
            LogisticRegression(max_iter=1000, C=0.5, class_weight="balanced", random_state=RANDOM_STATE),
        ),
        "histgb": HistGradientBoostingClassifier(
            max_iter=180, max_leaf_nodes=31, learning_rate=0.05,
            l2_regularization=0.05, random_state=RANDOM_STATE,
        ),
        "rf": RandomForestClassifier(
            n_estimators=160, max_depth=9, min_samples_leaf=40, max_features="sqrt",
            class_weight="balanced_subsample", n_jobs=THREADS, random_state=RANDOM_STATE,
        ),
        "extra_trees": ExtraTreesClassifier(
            n_estimators=160, max_depth=10, min_samples_leaf=30, max_features="sqrt",
            class_weight="balanced", n_jobs=THREADS, random_state=RANDOM_STATE,
        ),
    }
    base.update(optional_classifier_models())
    return base


def regressor_registry():
    base = {
        "ridge": make_pipeline(StandardScaler(), Ridge(alpha=10.0, random_state=RANDOM_STATE)),
        "elasticnet": make_pipeline(StandardScaler(), ElasticNet(alpha=0.001, l1_ratio=0.15, max_iter=3000, random_state=RANDOM_STATE)),
        "histgb_reg": HistGradientBoostingRegressor(
            max_iter=180, max_leaf_nodes=31, learning_rate=0.05,
            l2_regularization=0.05, random_state=RANDOM_STATE,
        ),
        "rf_reg": RandomForestRegressor(
            n_estimators=120, max_depth=10, min_samples_leaf=40, max_features="sqrt",
            n_jobs=THREADS, random_state=RANDOM_STATE,
        ),
        "extra_trees_reg": ExtraTreesRegressor(
            n_estimators=120, max_depth=10, min_samples_leaf=30, max_features="sqrt",
            n_jobs=THREADS, random_state=RANDOM_STATE,
        ),
    }
    base.update(optional_regressor_models())
    return base


def load_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def build_dataset_if_needed(args):
    feature_path = Path(args.features)
    manifest_path = Path(args.manifest)
    if feature_path.exists() and manifest_path.exists() and not args.rebuild:
        print(f"[dataset] using existing {feature_path}")
        return
    cmd = [
        sys.executable,
        str(ROOT / "backend" / "build_binance_updown_feature_dataset.py"),
        "--days", str(args.days),
        "--horizons", *[str(h) for h in args.horizons],
        "--out", str(Path(args.out)),
        "--tie-policy", args.tie_policy,
    ]
    if args.refresh_cache:
        cmd.append("--refresh-cache")
    print("[dataset] running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=str(ROOT))


def round_split_mask(df: pd.DataFrame, split: float) -> tuple[np.ndarray, np.ndarray]:
    rounds = (
        df[["round_id", "round_start"]]
        .drop_duplicates("round_id")
        .sort_values("round_start")
        .reset_index(drop=True)
    )
    cut = int(len(rounds) * split)
    train_ids = set(rounds.iloc[:cut]["round_id"])
    test_ids = set(rounds.iloc[cut:]["round_id"])
    return df["round_id"].isin(train_ids).to_numpy(), df["round_id"].isin(test_ids).to_numpy()


def maybe_sample_train(train_idx: np.ndarray, max_train_rows: int, rng: np.random.RandomState) -> np.ndarray:
    idx = np.where(train_idx)[0]
    if max_train_rows and len(idx) > max_train_rows:
        keep = rng.choice(idx, size=max_train_rows, replace=False)
        out = np.zeros_like(train_idx, dtype=bool)
        out[keep] = True
        return out
    return train_idx


def fit_predict_classifier(name: str, estimator, X_train, y_train, X_test, calibrate: bool):
    if name == "majority":
        return np.full(len(X_test), float(np.mean(y_train)))
    model = clone(estimator)
    if calibrate and len(np.unique(y_train)) > 1 and name not in {"lightgbm", "xgboost", "catboost"}:
        # Isotonic on the training fold only. The outer 30% remains untouched.
        cv = min(3, max(2, int(np.bincount(y_train.astype(int)).min())))
        try:
            cal = CalibratedClassifierCV(model, method="isotonic", cv=cv)
            cal.fit(X_train, y_train)
            return cal.predict_proba(X_test)[:, 1]
        except Exception:
            pass
    model.fit(X_train, y_train)
    return model.predict_proba(X_test)[:, 1]


def add_prediction_rows(rows, df_test, task, target, horizon, model, pred, y_true, limit: int):
    if limit <= 0:
        return
    n = min(limit, len(df_test))
    sample = df_test.iloc[:n]
    for i, (_, r) in enumerate(sample.iterrows()):
        rows.append({
            "timestamp": r["timestamp"],
            "round_id": r["round_id"],
            "horizon_min": horizon,
            "task": task,
            "target": target,
            "model": model,
            "prediction": float(pred[i]),
            "actual": float(y_true[i]),
            "anchor_price": float(r.get("anchor_price", np.nan)),
            "close": float(r.get("close", np.nan)),
            "seconds_left": float(r.get("seconds_left", np.nan)),
        })


def run_classification(df_h, feature_cols, args) -> tuple[list[dict], list[dict]]:
    metrics_rows = []
    prediction_rows = []
    registry = classifier_registry()
    rng = np.random.RandomState(RANDOM_STATE)

    for task, cfg in CLASSIFICATION_TASKS.items():
        target = cfg["target"]
        if target not in df_h.columns:
            continue
        work = df_h.dropna(subset=[target]).copy()
        if len(work) < 500:
            continue
        train_mask, test_mask = round_split_mask(work, args.split)
        train_mask = maybe_sample_train(train_mask, args.max_train_rows, rng)
        y_train = work.loc[train_mask, target].astype(int).to_numpy()
        y_test = work.loc[test_mask, target].astype(int).to_numpy()
        X_train = work.loc[train_mask, feature_cols].to_numpy(dtype=np.float32)
        X_test = work.loc[test_mask, feature_cols].to_numpy(dtype=np.float32)
        test_df = work.loc[test_mask].reset_index(drop=True)
        if len(np.unique(y_train)) < 2 or len(y_test) < 50:
            continue

        model_names = cfg["models"]
        for model_name in model_names:
            started = time.time()
            try:
                if model_name == "majority":
                    prob = np.full(len(y_test), float(np.mean(y_train)))
                elif model_name == "analytic_hold":
                    if "analytic_p_hold" not in work.columns:
                        continue
                    prob = np.clip(test_df["analytic_p_hold"].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
                elif model_name == "analytic_up":
                    if "analytic_p_up" not in work.columns:
                        continue
                    prob = np.clip(test_df["analytic_p_up"].to_numpy(dtype=float), 1e-6, 1 - 1e-6)
                elif model_name == "last_side_persistence":
                    prob = np.where(test_df["current_side"].to_numpy(dtype=float) == 0, 0.5, 1.0)
                    prob = np.clip(prob, 1e-6, 1 - 1e-6)
                else:
                    if model_name not in registry:
                        continue
                    prob = fit_predict_classifier(model_name, registry[model_name], X_train, y_train, X_test, args.calibrate)
                row = {
                    "kind": "classification",
                    "task": task,
                    "target": target,
                    "horizon_min": int(work["horizon_min"].iloc[0]),
                    "model": model_name,
                    "n_train": int(len(y_train)),
                    "elapsed_sec": round(time.time() - started, 2),
                    "notes": cfg["notes"],
                }
                row.update(binary_metrics(y_test, prob, task))
                metrics_rows.append(row)
                if args.save_predictions:
                    add_prediction_rows(prediction_rows, test_df, task, target, int(work["horizon_min"].iloc[0]), model_name, prob, y_test, args.prediction_limit_per_model)
                print(f"[class] h={row['horizon_min']} task={task} model={model_name} auc={row.get('auc', 0):.3f} brier={row.get('brier', 0):.4f} n={row['n_test']}")
            except Exception as exc:
                metrics_rows.append({
                    "kind": "classification",
                    "task": task,
                    "target": target,
                    "horizon_min": int(work["horizon_min"].iloc[0]),
                    "model": model_name,
                    "n_train": int(len(y_train)),
                    "n_test": int(len(y_test)),
                    "error": str(exc)[:300],
                    "elapsed_sec": round(time.time() - started, 2),
                })
                print(f"[class ERROR] h={int(work['horizon_min'].iloc[0])} task={task} model={model_name}: {exc}")
    return metrics_rows, prediction_rows


def run_regression(df_h, feature_cols, args) -> tuple[list[dict], list[dict]]:
    metrics_rows = []
    prediction_rows = []
    registry = regressor_registry()
    rng = np.random.RandomState(RANDOM_STATE)

    for task, target in REGRESSION_TASKS.items():
        if target not in df_h.columns:
            continue
        work = df_h.dropna(subset=[target]).copy()
        if len(work) < 500:
            continue
        train_mask, test_mask = round_split_mask(work, args.split)
        train_mask = maybe_sample_train(train_mask, args.max_train_rows, rng)
        y_train = work.loc[train_mask, target].to_numpy(dtype=float)
        y_test = work.loc[test_mask, target].to_numpy(dtype=float)
        X_train = work.loc[train_mask, feature_cols].to_numpy(dtype=np.float32)
        X_test = work.loc[test_mask, feature_cols].to_numpy(dtype=np.float32)
        test_df = work.loc[test_mask].reset_index(drop=True)

        for model_name, estimator in registry.items():
            started = time.time()
            try:
                model = clone(estimator)
                model.fit(X_train, y_train)
                pred = model.predict(X_test)
                row = {
                    "kind": "regression",
                    "task": task,
                    "target": target,
                    "horizon_min": int(work["horizon_min"].iloc[0]),
                    "model": model_name,
                    "n_train": int(len(y_train)),
                    "elapsed_sec": round(time.time() - started, 2),
                }
                row.update(regression_metrics(y_test, pred))
                metrics_rows.append(row)
                if args.save_predictions:
                    add_prediction_rows(prediction_rows, test_df, task, target, int(work["horizon_min"].iloc[0]), model_name, pred, y_test, args.prediction_limit_per_model)
                print(f"[reg] h={row['horizon_min']} task={task} model={model_name} mae={row.get('mae', 0):.3f} rmse={row.get('rmse', 0):.3f}")
            except Exception as exc:
                metrics_rows.append({
                    "kind": "regression",
                    "task": task,
                    "target": target,
                    "horizon_min": int(work["horizon_min"].iloc[0]),
                    "model": model_name,
                    "n_train": int(len(y_train)),
                    "n_test": int(len(y_test)),
                    "error": str(exc)[:300],
                    "elapsed_sec": round(time.time() - started, 2),
                })
                print(f"[reg ERROR] h={int(work['horizon_min'].iloc[0])} task={task} model={model_name}: {exc}")
    return metrics_rows, prediction_rows


def run_quantile_ranges(df_h, feature_cols, args) -> list[dict]:
    rows = []
    rng = np.random.RandomState(RANDOM_STATE)
    for task, target in {
        "quantile_expiry_return_bps": "target_expiry_return_bps",
        "quantile_max_up_bps": "target_max_up_bps",
        "quantile_max_down_bps": "target_max_down_bps",
        "quantile_range_bps": "target_range_bps",
    }.items():
        if target not in df_h.columns:
            continue
        work = df_h.dropna(subset=[target]).copy()
        train_mask, test_mask = round_split_mask(work, args.split)
        train_mask = maybe_sample_train(train_mask, args.max_train_rows, rng)
        y_train = work.loc[train_mask, target].to_numpy(dtype=float)
        y_test = work.loc[test_mask, target].to_numpy(dtype=float)
        X_train = work.loc[train_mask, feature_cols].to_numpy(dtype=np.float32)
        X_test = work.loc[test_mask, feature_cols].to_numpy(dtype=np.float32)
        if len(y_test) < 50:
            continue
        started = time.time()
        try:
            q_models = {}
            preds = {}
            for alpha in (0.10, 0.50, 0.90):
                model = GradientBoostingRegressor(
                    loss="quantile",
                    alpha=alpha,
                    n_estimators=160,
                    max_depth=3,
                    learning_rate=0.05,
                    random_state=RANDOM_STATE,
                )
                model.fit(X_train, y_train)
                q_models[alpha] = model
                preds[alpha] = model.predict(X_test)
            lo = np.minimum(preds[0.10], preds[0.90])
            hi = np.maximum(preds[0.10], preds[0.90])
            median = preds[0.50]
            rows.append({
                "kind": "quantile_regression",
                "task": task,
                "target": target,
                "horizon_min": int(work["horizon_min"].iloc[0]),
                "model": "gradient_boosting_quantile",
                "n_train": int(len(y_train)),
                "n_test": int(len(y_test)),
                "elapsed_sec": round(time.time() - started, 2),
                "pinball_q10": float(mean_pinball_loss(y_test, preds[0.10], alpha=0.10)),
                "pinball_q50": float(mean_pinball_loss(y_test, median, alpha=0.50)),
                "pinball_q90": float(mean_pinball_loss(y_test, preds[0.90], alpha=0.90)),
                "interval_80_coverage": float(np.mean((y_test >= lo) & (y_test <= hi))),
                "interval_80_avg_width": float(np.mean(hi - lo)),
                "median_mae": float(mean_absolute_error(y_test, median)),
            })
            print(f"[quant] h={int(work['horizon_min'].iloc[0])} task={task} coverage={rows[-1]['interval_80_coverage']:.3f}")
        except Exception as exc:
            rows.append({
                "kind": "quantile_regression",
                "task": task,
                "target": target,
                "horizon_min": int(work["horizon_min"].iloc[0]),
                "model": "gradient_boosting_quantile",
                "error": str(exc)[:300],
                "elapsed_sec": round(time.time() - started, 2),
            })
            print(f"[quant ERROR] h={int(work['horizon_min'].iloc[0])} task={task}: {exc}")
    return rows


def run_bakeoff(args):
    build_dataset_if_needed(args)
    feature_path = Path(args.features)
    manifest_path = Path(args.manifest)
    metrics_path = Path(args.metrics_csv)
    predictions_path = Path(args.predictions_csv)
    summary_path = Path(args.summary_json)
    metrics_path.parent.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest(manifest_path)
    feature_cols = manifest["feature_cols"]
    df = pd.read_parquet(feature_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df["round_start"] = pd.to_datetime(df["round_start"], utc=True)
    df = df.replace([np.inf, -np.inf], np.nan)

    if args.max_features and len(feature_cols) > args.max_features:
        # Cheap variance filter. Keeps this bakeoff practical without leaking test labels.
        variances = df[feature_cols].var(numeric_only=True).sort_values(ascending=False)
        feature_cols = list(variances.head(args.max_features).index)
        print(f"[features] variance-selected top {len(feature_cols)} features")

    print(f"[load] rows={len(df):,} features={len(feature_cols):,} horizons={sorted(df['horizon_min'].unique())}")
    all_metrics = []
    all_predictions = []
    started_all = time.time()

    for horizon in args.horizons:
        df_h = df[df["horizon_min"] == horizon].copy()
        if df_h.empty:
            print(f"[skip] no rows for horizon={horizon}")
            continue
        print(f"\n=== HORIZON {horizon}m rows={len(df_h):,} rounds={df_h['round_id'].nunique():,} ===")
        cls_rows, cls_preds = run_classification(df_h, feature_cols, args)
        reg_rows, reg_preds = run_regression(df_h, feature_cols, args)
        quant_rows = run_quantile_ranges(df_h, feature_cols, args)
        all_metrics.extend(cls_rows)
        all_metrics.extend(reg_rows)
        all_metrics.extend(quant_rows)
        all_predictions.extend(cls_preds)
        all_predictions.extend(reg_preds)

        pd.DataFrame(all_metrics).to_csv(metrics_path, index=False)
        if args.save_predictions and all_predictions:
            pd.DataFrame(all_predictions).to_csv(predictions_path, index=False)
        print(f"[checkpoint] metrics -> {metrics_path}")

    summary = {
        "created_at_utc": pd.Timestamp.utcnow().isoformat(),
        "days": args.days,
        "horizons": args.horizons,
        "split": args.split,
        "feature_path": str(feature_path),
        "manifest_path": str(manifest_path),
        "metrics_csv": str(metrics_path),
        "predictions_csv": str(predictions_path) if args.save_predictions else None,
        "n_metric_rows": len(all_metrics),
        "elapsed_sec": round(time.time() - started_all, 2),
        "feature_count_used": len(feature_cols),
        "thread_count": THREADS,
    }
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\n[DONE] metrics: {metrics_path}")
    if args.save_predictions:
        print(f"[DONE] predictions: {predictions_path}")
    print(f"[DONE] summary: {summary_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=120)
    parser.add_argument("--horizons", type=int, nargs="+", default=[5, 15, 30])
    parser.add_argument("--split", type=float, default=0.70, help="Chronological round split: train oldest split, test newest remainder.")
    parser.add_argument("--out", default=str(DEFAULT_RESEARCH))
    parser.add_argument("--features", default=str(DEFAULT_RESEARCH / "binance_updown_features.parquet"))
    parser.add_argument("--manifest", default=str(DEFAULT_RESEARCH / "binance_updown_feature_manifest.json"))
    parser.add_argument("--metrics-csv", default=str(DEFAULT_RESEARCH / "updown_bakeoff_metrics.csv"))
    parser.add_argument("--predictions-csv", default=str(DEFAULT_RESEARCH / "updown_bakeoff_predictions.csv"))
    parser.add_argument("--summary-json", default=str(DEFAULT_RESEARCH / "updown_bakeoff_run_summary.json"))
    parser.add_argument("--tie-policy", choices=["down", "up", "neutral"], default="down")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild dataset even if feature parquet exists.")
    parser.add_argument("--refresh-cache", action="store_true", help="Redownload Binance klines instead of using cached klines.")
    parser.add_argument("--calibrate", action="store_true", help="Use isotonic CV calibration for sklearn classifiers where practical.")
    parser.add_argument("--save-predictions", action="store_true", help="Save per-row predictions for the newest 30%% test split.")
    parser.add_argument("--prediction-limit-per-model", type=int, default=5000, help="Limit saved prediction rows per model/task/horizon; 0 disables row predictions.")
    parser.add_argument("--max-train-rows", type=int, default=0, help="Optional random cap per horizon/task to speed experimentation. 0 = all train rows.")
    parser.add_argument("--max-features", type=int, default=0, help="Optional variance filter to top N features. 0 = all generated features.")
    args = parser.parse_args()

    if not 0.5 <= args.split <= 0.9:
        raise ValueError("--split should be between 0.5 and 0.9")
    run_bakeoff(args)


if __name__ == "__main__":
    main()
