"""Purged multi-window challenger harness.

This script compares W90, W400, W1265-recency, and W1265-regime-similarity
experts on identical prediction IDs. It is research-only: outputs are written to
an isolated run directory and never replace the live champion automatically.

It also runs the predeclared direction sample budgets (40K/100K/250K/all) and
stacker budgets (6K/25K/50K), with chronological tests and explicit purge gaps.
Models are trained sequentially and released between fits for a 16 GB laptop.
"""
from __future__ import annotations

import argparse
import gc
import os
import sys
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    brier_score_loss,
    log_loss,
    roc_auc_score,
)
from sklearn.model_selection import TimeSeriesSplit
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


HERE = Path(__file__).resolve()
ROOT = HERE.parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from artifact_identity import (  # noqa: E402
    atomic_write_json,
    current_training_identity,
    training_identity_issues,
    write_artifact_manifest,
)
from target_windows import expert_specs_for_target  # noqa: E402


DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
MATRIX = DATA / "research_matrix_1m.parquet"
OUT_ROOT = DATA / "research" / "multiwindow_experts"
DIRECTION_BUDGETS = (40_000, 100_000, 250_000, 0)
STACKER_BUDGETS = (6_000, 25_000, 50_000)
DEFAULT_FAMILIES = ("logreg", "histgb", "rf")


def _log(message: str) -> None:
    print(time.strftime("%H:%M:%S"), message, flush=True)


def _model_factory(name: str, threads: int):
    if name == "logreg":
        return Pipeline(
            [
                ("scale", StandardScaler()),
                (
                    "model",
                    LogisticRegression(
                        max_iter=500,
                        C=0.1,
                        class_weight="balanced",
                        random_state=41,
                    ),
                ),
            ]
        )
    if name == "histgb":
        return HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_iter=100,
            max_depth=5,
            min_samples_leaf=40,
            l2_regularization=0.1,
            random_state=42,
        )
    if name == "rf":
        return RandomForestClassifier(
            n_estimators=180,
            max_depth=9,
            min_samples_leaf=40,
            max_features="sqrt",
            class_weight="balanced",
            n_jobs=threads,
            random_state=43,
        )
    if name == "xgb":
        import xgboost as xgb

        return xgb.XGBClassifier(
            n_estimators=180,
            max_depth=4,
            learning_rate=0.03,
            subsample=0.8,
            colsample_bytree=0.8,
            n_jobs=threads,
            tree_method="hist",
            eval_metric="logloss",
            random_state=44,
        )
    if name == "lgbm":
        import lightgbm as lgb

        return lgb.LGBMClassifier(
            n_estimators=180,
            max_depth=5,
            learning_rate=0.03,
            subsample=0.8,
            colsample_bytree=0.8,
            n_jobs=threads,
            verbosity=-1,
            random_state=45,
        )
    if name == "catboost":
        from catboost import CatBoostClassifier

        return CatBoostClassifier(
            iterations=180,
            depth=5,
            learning_rate=0.03,
            loss_function="Logloss",
            thread_count=threads,
            allow_writing_files=False,
            verbose=False,
            random_seed=46,
        )
    raise ValueError(f"unknown model family: {name}")


def _fit(model, X, y, weights=None):
    if weights is None:
        return model.fit(X, y)
    if isinstance(model, Pipeline):
        return model.fit(X, y, model__sample_weight=weights)
    try:
        return model.fit(X, y, sample_weight=weights)
    except TypeError:
        return model.fit(X, y)


def _probability(model, X) -> np.ndarray:
    values = np.asarray(model.predict_proba(X))
    classes = list(getattr(model, "classes_", [0, 1]))
    if hasattr(model, "named_steps"):
        classes = list(getattr(model.named_steps["model"], "classes_", classes))
    return values[:, classes.index(1)]


def build_causal_features(frame: pd.DataFrame) -> pd.DataFrame:
    close = pd.to_numeric(frame["close"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    total_taker = (
        pd.to_numeric(frame["taker_buy"], errors="coerce")
        + pd.to_numeric(frame["taker_sell"], errors="coerce")
    ).replace(0, np.nan)
    result = pd.DataFrame(index=frame.index)
    result["return_1m_bps"] = close.pct_change(fill_method=None) * 10_000.0
    result["range_1m_bps"] = (
        (pd.to_numeric(frame["high"], errors="coerce")
         - pd.to_numeric(frame["low"], errors="coerce"))
        / close.replace(0, np.nan)
        * 10_000.0
    )
    result["body_1m_bps"] = (
        (close - pd.to_numeric(frame["open"], errors="coerce"))
        / close.replace(0, np.nan)
        * 10_000.0
    )
    result["log_volume"] = np.log1p(volume.clip(lower=0))
    result["volume_z_60"] = (
        (volume - volume.rolling(60, min_periods=20).mean())
        / volume.rolling(60, min_periods=20).std().replace(0, np.nan)
    )
    result["taker_imbalance"] = (
        pd.to_numeric(frame["taker_buy"], errors="coerce")
        - pd.to_numeric(frame["taker_sell"], errors="coerce")
    ) / total_taker

    excluded_prefixes = ("future_",)
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
    for column in frame.columns:
        if (
            column in excluded
            or any(column.startswith(prefix) for prefix in excluded_prefixes)
        ):
            continue
        series = pd.to_numeric(frame[column], errors="coerce")
        if series.notna().any():
            result[column] = series

    timestamp = pd.to_datetime(frame["ts_ms"], unit="ms", utc=True)
    minute_of_day = timestamp.dt.hour * 60 + timestamp.dt.minute
    result["session_sin"] = np.sin(2 * np.pi * minute_of_day / 1440.0)
    result["session_cos"] = np.cos(2 * np.pi * minute_of_day / 1440.0)
    result["is_weekend"] = timestamp.dt.dayofweek.ge(5).astype(float)
    return result.replace([np.inf, -np.inf], np.nan).fillna(0.0).astype("float32")


def target_for_horizon(frame: pd.DataFrame, horizon: int) -> np.ndarray:
    close = pd.to_numeric(frame["close"], errors="coerce").to_numpy(dtype=float)
    future = np.roll(close, -horizon)
    y = (future > close).astype(np.int8)
    y[-horizon:] = -1
    return y


def _similarity_weights(
    X: np.ndarray,
    train_idx: np.ndarray,
    similarity_idx: np.ndarray,
    recent_rows: int = 1440,
) -> np.ndarray:
    train = X[np.ix_(train_idx, similarity_idx)]
    recent = train[-min(recent_rows, len(train)):]
    center = np.median(recent, axis=0)
    q25, q75 = np.quantile(train, [0.25, 0.75], axis=0)
    scale = np.maximum(q75 - q25, 1e-5)
    distance = np.mean(
        np.square(np.clip((train - center) / scale, -6.0, 6.0)), axis=1
    )
    values = np.clip(np.exp(-0.5 * distance), 0.15, 1.0)
    return values / max(float(values.mean()), 1e-9)


def _weights(
    mode: str,
    X: np.ndarray,
    train_idx: np.ndarray,
    similarity_idx: np.ndarray,
) -> np.ndarray | None:
    if mode == "uniform":
        return None
    similarity = (
        _similarity_weights(X, train_idx, similarity_idx)
        if mode in {"similarity", "recency_similarity"}
        else None
    )
    if mode == "similarity":
        return similarity
    age = len(train_idx) - 1 - np.arange(len(train_idx))
    half_life = max(1440.0, len(train_idx) / 3.0)
    values = np.power(0.5, age / half_life)
    if mode == "recency_similarity":
        values = values * similarity
    return values / max(float(values.mean()), 1e-9)


def _metrics(y: np.ndarray, probability: np.ndarray) -> dict[str, float]:
    probability = np.clip(np.asarray(probability, dtype=float), 1e-6, 1 - 1e-6)
    prediction = probability >= 0.5
    return {
        "n": int(len(y)),
        "auc": float(roc_auc_score(y, probability)) if len(np.unique(y)) > 1 else np.nan,
        "accuracy": float(accuracy_score(y, prediction)),
        "brier": float(brier_score_loss(y, probability)),
        "log_loss": float(log_loss(y, probability, labels=[0, 1])),
    }


def _windowed_train_indices(
    train_idx: np.ndarray,
    timestamps: np.ndarray,
    validation_start: int,
    days: int,
) -> np.ndarray:
    cutoff_ms = int(timestamps[validation_start]) - int(days) * 86_400_000
    return train_idx[timestamps[train_idx] >= cutoff_ms]


def run_experiment(
    frame: pd.DataFrame,
    *,
    horizons: list[int],
    families: list[str],
    folds: int,
    test_days: int,
    threads: int,
    output_dir: Path,
    save_models: bool,
    run_budgets: bool,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    X_frame = build_causal_features(frame)
    X = X_frame.to_numpy(dtype=np.float32)
    timestamps = pd.to_numeric(frame["ts_ms"], errors="coerce").to_numpy(dtype=np.int64)
    feature_names = list(X_frame.columns)
    similarity_names = (
        "volume_z_60",
        "rv_15m",
        "rv_30m",
        "rv_60m",
        "rv_term",
        "vol_accel",
        "compression_ratio",
        "range_15m",
        "shock_magnitude",
        "funding_velocity",
        "perp_spot_basis_bps",
        "vol_spot",
        "vol_perp",
    )
    similarity_idx = np.asarray(
        [
            feature_names.index(name)
            for name in similarity_names
            if name in feature_names
        ],
        dtype=np.int64,
    )
    if not len(similarity_idx):
        raise ValueError("no predeclared regime-similarity features are available")
    metrics_rows: list[dict[str, Any]] = []
    budget_rows: list[dict[str, Any]] = []
    all_oof: list[pd.DataFrame] = []
    model_files: list[str] = []

    for horizon in horizons:
        target_name = f"direction_{horizon}m"
        y = target_for_horizon(frame, horizon)
        valid = y >= 0
        valid_indices = np.flatnonzero(valid)
        X_h = X[valid]
        y_h = y[valid]
        ts_h = timestamps[valid]
        n = len(y_h)
        test_size = min(test_days * 1440, max(200, n // (folds + 2)))
        purge = 60 + int(horizon)
        splitter = TimeSeriesSplit(
            n_splits=folds, test_size=test_size, gap=purge
        )
        expert_specs = expert_specs_for_target(target_name)
        oof_by_id: dict[int, dict[str, Any]] = {}

        for fold, (base_train, validation) in enumerate(
            splitter.split(X_h), start=1
        ):
            validation_start = int(validation[0])
            for local_idx in validation:
                source_idx = int(valid_indices[local_idx])
                oof_by_id.setdefault(
                    source_idx,
                    {
                        "prediction_id": f"{horizon}m-{int(ts_h[local_idx])}",
                        "source_row": source_idx,
                        "ts_ms": int(ts_h[local_idx]),
                        "horizon": horizon,
                        "fold": fold,
                        "actual_up": int(y_h[local_idx]),
                    },
                )
            for expert_name, spec in expert_specs.items():
                train_idx = _windowed_train_indices(
                    base_train, ts_h, validation_start, int(spec["days"])
                )
                if len(train_idx) < 500:
                    continue
                weights = _weights(
                    spec["weight_mode"], X_h, train_idx, similarity_idx
                )
                for family in families:
                    key = f"{expert_name}__{family}"
                    _log(
                        f"[OOF] h={horizon} fold={fold}/{folds} "
                        f"expert={expert_name} model={family} "
                        f"train={len(train_idx):,} test={len(validation):,}"
                    )
                    started = time.time()
                    model = _model_factory(family, threads)
                    _fit(model, X_h[train_idx], y_h[train_idx], weights)
                    probability = _probability(model, X_h[validation])
                    fold_metrics = _metrics(y_h[validation], probability)
                    metrics_rows.append(
                        {
                            "target": target_name,
                            "fold": fold,
                            "expert": expert_name,
                            "window_days": int(spec["days"]),
                            "weight_mode": spec["weight_mode"],
                            "model": family,
                            "train_n": int(len(train_idx)),
                            "test_start_ts_ms": int(ts_h[validation[0]]),
                            "test_end_ts_ms": int(ts_h[validation[-1]]),
                            "purge_rows": purge,
                            "elapsed_seconds": round(time.time() - started, 3),
                            **fold_metrics,
                        }
                    )
                    for local_idx, probability_value in zip(validation, probability):
                        source_idx = int(valid_indices[local_idx])
                        oof_by_id[source_idx][key] = float(probability_value)
                    del model, probability
                    gc.collect()

        oof = pd.DataFrame(list(oof_by_id.values())).sort_values("ts_ms")
        probability_columns = [
            column for column in oof.columns if "__" in column
        ]
        for column in probability_columns:
            available = oof[column].notna()
            if available.any():
                aggregate = _metrics(
                    oof.loc[available, "actual_up"].to_numpy(),
                    oof.loc[available, column].to_numpy(),
                )
                expert, family = column.split("__", 1)
                metrics_rows.append(
                    {
                        "target": target_name,
                        "fold": "ALL_OOF",
                        "expert": expert,
                        "model": family,
                        "purge_rows": purge,
                        **aggregate,
                    }
                )

        # Causal champion: for each fold, choose the expert with best Brier on
        # strictly earlier folds. The post-outcome oracle is reported only as an
        # unattainable upper bound and is never serialized as a deployable model.
        oof["causal_selected_expert"] = None
        oof["causal_probability"] = np.nan
        prior_scores: dict[str, list[float]] = {}
        for fold in sorted(oof["fold"].unique()):
            fold_mask = oof["fold"].eq(fold)
            if prior_scores:
                winner = min(prior_scores, key=lambda key: np.mean(prior_scores[key]))
                oof.loc[fold_mask, "causal_selected_expert"] = winner
                oof.loc[fold_mask, "causal_probability"] = oof.loc[fold_mask, winner]
            for column in probability_columns:
                rows = oof.loc[fold_mask, ["actual_up", column]].dropna()
                if len(rows):
                    prior_scores.setdefault(column, []).append(
                        brier_score_loss(rows["actual_up"], rows[column])
                    )

        # Budget experiments use the latest untouched 20% and never tune on it.
        if run_budgets:
            cut = int(n * 0.80)
            train_pool = np.arange(max(0, cut - purge), dtype=np.int64)
            test_idx = np.arange(cut, n, dtype=np.int64)
            for budget in DIRECTION_BUDGETS:
                budget_idx = (
                    train_pool
                    if budget == 0 or len(train_pool) <= budget
                    else train_pool[-budget:]
                )
                for family in families:
                    _log(
                        f"[BUDGET direction] h={horizon} model={family} "
                        f"budget={'ALL' if budget == 0 else budget:,}"
                    )
                    model = _model_factory(family, threads)
                    _fit(model, X_h[budget_idx], y_h[budget_idx])
                    probability = _probability(model, X_h[test_idx])
                    budget_rows.append(
                        {
                            "experiment": "direction_budget",
                            "target": target_name,
                            "model": family,
                            "budget": "ALL" if budget == 0 else int(budget),
                            "train_n": int(len(budget_idx)),
                            "test_n": int(len(test_idx)),
                            "purge_rows": purge,
                            **_metrics(y_h[test_idx], probability),
                        }
                    )
                    del model, probability
                    gc.collect()

            stack_frame = oof.dropna(
                subset=["actual_up"] + probability_columns
            ).sort_values("ts_ms")
            if len(stack_frame) >= 2000:
                stack_cut = int(len(stack_frame) * 0.80)
                stack_test = stack_frame.iloc[stack_cut:]
                for budget in STACKER_BUDGETS:
                    stack_train = stack_frame.iloc[
                        max(0, stack_cut - purge - budget): max(0, stack_cut - purge)
                    ]
                    if len(stack_train) < 500:
                        continue
                    stacker = Pipeline(
                        [
                            ("scale", StandardScaler()),
                            (
                                "model",
                                LogisticRegression(
                                    max_iter=500,
                                    C=0.1,
                                    class_weight="balanced",
                                    random_state=51,
                                ),
                            ),
                        ]
                    )
                    stacker.fit(
                        stack_train[probability_columns],
                        stack_train["actual_up"],
                    )
                    probability = _probability(
                        stacker, stack_test[probability_columns]
                    )
                    budget_rows.append(
                        {
                            "experiment": "stacker_budget",
                            "target": target_name,
                            "model": "logreg_stacker",
                            "budget": int(budget),
                            "train_n": int(len(stack_train)),
                            "test_n": int(len(stack_test)),
                            "purge_rows": purge,
                            **_metrics(
                                stack_test["actual_up"].to_numpy(), probability
                            ),
                        }
                    )

        if save_models:
            for expert_name, spec in expert_specs.items():
                train_idx = _windowed_train_indices(
                    np.arange(n, dtype=np.int64),
                    ts_h,
                    n - 1,
                    int(spec["days"]),
                )
                weights = _weights(
                    spec["weight_mode"], X_h, train_idx, similarity_idx
                )
                for family in families:
                    _log(
                        f"[FINAL SHADOW] h={horizon} expert={expert_name} "
                        f"model={family} train={len(train_idx):,}"
                    )
                    model = _model_factory(family, threads)
                    _fit(model, X_h[train_idx], y_h[train_idx], weights)
                    relative = (
                        Path("models")
                        / target_name
                        / f"{expert_name}__{family}.joblib"
                    )
                    destination = output_dir / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    joblib.dump(
                        {
                            "model": model,
                            "features": feature_names,
                            "target": target_name,
                            "expert": expert_name,
                            "window_days": int(spec["days"]),
                            "weight_mode": spec["weight_mode"],
                            "trained_through_ts_ms": int(ts_h[-1]),
                            "shadow_only": True,
                        },
                        destination,
                    )
                    model_files.append(relative.as_posix())
                    del model
                    gc.collect()

        all_oof.append(oof)

    oof_path = output_dir / "oracle_shadow_predictions.parquet"
    pd.concat(all_oof, ignore_index=True).to_parquet(oof_path, index=False)
    metrics_path = output_dir / "metrics.csv"
    pd.DataFrame(metrics_rows).to_csv(metrics_path, index=False)
    budgets_path = output_dir / "budget_experiments.csv"
    pd.DataFrame(budget_rows).to_csv(budgets_path, index=False)
    return {
        "rows": int(len(frame)),
        "features": feature_names,
        "horizons": horizons,
        "families": families,
        "folds": folds,
        "test_days_per_fold": test_days,
        "purged_oof_path": str(oof_path),
        "metrics_path": str(metrics_path),
        "budget_metrics_path": str(budgets_path),
        "model_files": model_files,
    }


def _synthetic_frame(rows: int = 5000) -> pd.DataFrame:
    rng = np.random.default_rng(12)
    returns = rng.normal(0, 0.0005, rows)
    close = 70_000 * np.exp(np.cumsum(returns))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.0002, rows))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.0002, rows))
    buy = rng.uniform(10, 100, rows)
    sell = rng.uniform(10, 100, rows)
    return pd.DataFrame(
        {
            "ts_ms": np.arange(rows, dtype=np.int64) * 60_000,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": buy + sell,
            "trade_count": rng.integers(100, 1000, rows),
            "taker_buy": buy,
            "taker_sell": sell,
            "rv_15m": pd.Series(returns).rolling(15).std().fillna(0),
            "vpin_15m": np.abs(buy - sell) / (buy + sell),
        }
    )


def selftest() -> None:
    import tempfile

    irregular_ts = np.asarray(
        [0, 60_000, 120_000, 5 * 86_400_000, 5 * 86_400_000 + 60_000],
        dtype=np.int64,
    )
    selected = _windowed_train_indices(
        np.arange(4, dtype=np.int64), irregular_ts, 4, 1
    )
    assert selected.tolist() == [3]

    with tempfile.TemporaryDirectory() as tmp:
        result = run_experiment(
            _synthetic_frame(),
            horizons=[5],
            families=["logreg"],
            folds=2,
            test_days=1,
            threads=1,
            output_dir=Path(tmp),
            save_models=False,
            run_budgets=False,
        )
        oof = pd.read_parquet(result["purged_oof_path"])
        metrics = pd.read_csv(result["metrics_path"])
        assert oof["prediction_id"].is_unique
        assert {"W90__logreg", "W400__logreg"}.issubset(oof.columns)
        assert not metrics.empty and metrics["purge_rows"].dropna().min() >= 65
    print("multiwindow_experiment self-test: ALL PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--matrix", default=str(MATRIX))
    parser.add_argument("--horizons", nargs="+", type=int, default=[5, 15])
    parser.add_argument(
        "--families",
        nargs="+",
        default=list(DEFAULT_FAMILIES),
        choices=["logreg", "histgb", "rf", "xgb", "lgbm", "catboost"],
    )
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--test-days", type=int, default=30)
    parser.add_argument("--threads", type=int, default=max(2, (os.cpu_count() or 4) - 4))
    parser.add_argument("--save-models", action="store_true")
    parser.add_argument("--skip-budgets", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0

    matrix_path = Path(args.matrix)
    if not matrix_path.exists():
        raise SystemExit(f"matrix not found: {matrix_path}")
    requested_days = int(
        os.environ.get("BTC_HISTORICAL_DAYS")
        or os.environ.get("BTC_BACKFILL_DAYS")
        or 1265
    )
    identity = current_training_identity(
        requested_days=requested_days,
        code_paths=[__file__, BACKEND / "target_windows.py"],
        full_refit=False,
    )
    identity_issues = training_identity_issues(identity)
    if identity_issues:
        raise SystemExit(
            "research-matrix identity contract failed; experiment not started:\n- "
            + "\n- ".join(identity_issues)
        )
    run_id = time.strftime("%Y%m%d_%H%M%S")
    output_dir = OUT_ROOT / run_id
    _log(f"[start] matrix={matrix_path} requested_days={requested_days}")
    frame = pd.read_parquet(matrix_path)
    _log(f"[load] rows={len(frame):,} columns={len(frame.columns)}")
    result = run_experiment(
        frame,
        horizons=args.horizons,
        families=args.families,
        folds=args.folds,
        test_days=args.test_days,
        threads=args.threads,
        output_dir=output_dir,
        save_models=args.save_models,
        run_budgets=not args.skip_budgets,
    )
    identity = current_training_identity(
        requested_days=requested_days,
        feature_names=result["features"],
        code_paths=[__file__, BACKEND / "target_windows.py"],
        full_refit=False,
    )
    run_manifest = {
        **result,
        **identity,
        "run_id": run_id,
        "shadow_only": True,
        "automatic_promotion": False,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    atomic_write_json(output_dir / "run_manifest.json", run_manifest)
    artifact_files = [
        "run_manifest.json",
        "metrics.csv",
        "budget_experiments.csv",
        "oracle_shadow_predictions.parquet",
        *result["model_files"],
    ]
    write_artifact_manifest(
        output_dir,
        identity,
        artifact_type="multiwindow_oracle_shadow",
        extra={
            "artifact_files": artifact_files,
            "shadow_only": True,
            "automatic_promotion": False,
            "run_id": run_id,
        },
    )
    _log(f"[done] results={output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
