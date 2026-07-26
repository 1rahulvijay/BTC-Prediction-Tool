"""Shared, leakage-resistant training and artifact helpers."""
from __future__ import annotations

import gc
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import calibration_curve
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import (
    brier_score_loss,
    mean_pinball_loss,
    roc_auc_score,
)


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"

try:
    from artifact_identity import (
        atomic_write_json,
        hash_file,
        hash_json,
        hash_paths,
    )
except ImportError:
    from backend.artifact_identity import (
        atomic_write_json,
        hash_file,
        hash_json,
        hash_paths,
    )

from .trade_schema import CONFIG_VERSION, FEATURE_COLUMNS, PROMOTION_GATE, policy_hash


def _dataset_code_hash() -> str:
    return hash_paths(
        [
            Path(__file__).with_name("build_complete_trade_dataset.py"),
            Path(__file__).with_name("trade_schema.py"),
            Path(__file__).with_name("trade_labels.py"),
            BACKEND / "polymarket_fee.py",
            BACKEND / "research" / "executable_fill_engine.py",
            BACKEND / "research" / "executable_surface_config.py",
        ]
    )


def _model_code_hash(artifact_type: str) -> str:
    specialized = {
        "complete_trade_share_path": (
            "train_share_path_model.py",
            "share_path_serving.py",
        ),
        "complete_trade_btc_path": (
            "train_btc_path_model.py",
            "btc_path_serving.py",
        ),
        "complete_trade_execution_heads": (
            "train_execution_heads.py",
            "execution_serving.py",
        ),
    }
    paths = [
        Path(__file__),
        Path(__file__).with_name("trade_schema.py"),
        Path(__file__).with_name("trade_labels.py"),
        BACKEND / "polymarket_fee.py",
    ]
    paths.extend(
        Path(__file__).with_name(name)
        for name in specialized.get(str(artifact_type), ())
    )
    return hash_paths(paths)


def load_verified_dataset(path: Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    manifest_path = path.with_suffix(".manifest.json")
    if not path.is_file() or not manifest_path.is_file():
        raise FileNotFoundError(f"dataset or manifest missing: {path}")
    with open(manifest_path, "r", encoding="utf-8") as handle:
        manifest = json.load(handle)
    reasons: list[str] = []
    if manifest.get("dataset_version") != CONFIG_VERSION:
        reasons.append("dataset version mismatch")
    if manifest.get("policy_hash") != policy_hash():
        reasons.append("policy hash mismatch")
    if manifest.get("feature_schema_hash") != hash_json(list(FEATURE_COLUMNS)):
        reasons.append("feature schema mismatch")
    if manifest.get("dataset_sha256") != hash_file(path):
        reasons.append("dataset hash mismatch")
    if manifest.get("code_hash") != _dataset_code_hash():
        reasons.append("dataset construction code mismatch")
    if manifest.get("promotable") and not (manifest.get("source") or {}).get(
        "l2_sha256"
    ):
        reasons.append("promotable dataset is missing its L2 source hash")
    source = manifest.get("source") or {}
    for label in ("l2", "btc", "settlements"):
        raw_path = source.get(f"{label}_path")
        expected_hash = source.get(f"{label}_sha256")
        if not raw_path:
            reasons.append(f"{label} source path missing")
            continue
        source_path = Path(str(raw_path))
        if not source_path.is_file():
            reasons.append(f"{label} source missing")
        elif expected_hash and hash_file(source_path) != expected_hash:
            reasons.append(f"{label} source hash mismatch")
    if reasons:
        raise RuntimeError("dataset rejected: " + "; ".join(reasons))
    return pd.read_parquet(path), manifest


def chronological_purged_split(
    frame: pd.DataFrame,
    *,
    train_fraction: float = 0.70,
    calibration_fraction: float = 0.15,
    purge_seconds: int = 15 * 60,
    eligible_mask: np.ndarray | None = None,
) -> dict[str, np.ndarray]:
    """Split by round start, keeping overlapping 5m/15m rounds in one partition."""
    eligible = (
        np.ones(len(frame), dtype=bool)
        if eligible_mask is None
        else np.asarray(eligible_mask, dtype=bool)
    )
    if len(eligible) != len(frame):
        raise ValueError("eligible mask length does not match the frame")
    times = np.asarray(
        sorted(
            frame.loc[eligible, "round_start_ts"].dropna().astype(int).unique()
        )
    )
    if len(times) < 20:
        raise ValueError("at least 20 distinct round timestamps are required")
    train_index = max(1, min(len(times) - 3, int(len(times) * train_fraction)))
    calibration_index = max(
        train_index + 1,
        min(
            len(times) - 1,
            int(len(times) * (train_fraction + calibration_fraction)),
        ),
    )
    train_end = int(times[train_index - 1])
    calibration_start = train_end + int(purge_seconds)
    calibration_end = int(times[calibration_index - 1])
    test_start = calibration_end + int(purge_seconds)
    values = frame["round_start_ts"].to_numpy(dtype=int)
    masks = {
        "train": eligible & (values <= train_end),
        "calibration": eligible
        & (values >= calibration_start)
        & (values <= calibration_end),
        "test": eligible & (values >= test_start),
    }
    for name, mask in masks.items():
        if int(mask.sum()) == 0:
            raise ValueError(f"purged split produced an empty {name} partition")
    return masks


def clean_xy(
    frame: pd.DataFrame,
    mask: np.ndarray,
    target: str,
    *,
    require_complete_entry: bool = False,
    feature_columns: tuple[str, ...] = FEATURE_COLUMNS,
    _allow_invalid_candidates: bool = False,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rows for training. `candidate_valid == 1` is MANDATORY.

    Finiteness is not validity. A row can be perfectly finite and still be stale, crossed,
    malformed, or missing a required feature that was replaced upstream - the builder records
    exactly that in `candidate_valid` / `candidate_reasons`, and nothing was reading it. Every
    training, calibration, test and family-selection path goes through here, so enforcing it
    once closes all of them.

    `_allow_invalid_candidates` exists ONLY for named diagnostics that deliberately inspect
    rejected rows. It is underscore-prefixed and keyword-only so it cannot be passed by accident.
    """
    selected = frame.loc[mask].copy()
    if not _allow_invalid_candidates:
        # FAIL CLOSED on absence. "Filter it if the column happens to exist" meant a stale
        # dataset predating the column trained completely unfiltered - the exact fail-open this
        # was meant to remove.
        if "candidate_valid" not in selected.columns:
            raise RuntimeError(
                "dataset is missing the mandatory candidate_valid column; rebuild with "
                "build_complete_trade_dataset.py before training"
            )
        selected = selected[selected["candidate_valid"] == 1]
    if require_complete_entry:
        selected = selected[selected["entry_complete"] == 1]
    selected = selected.dropna(subset=[*feature_columns, target])
    x = selected.loc[:, feature_columns].to_numpy(dtype=np.float32)
    y = selected[target].to_numpy()
    groups = selected["round_start_ts"].to_numpy(dtype=np.int64)
    finite = np.isfinite(x).all(axis=1) & np.isfinite(y.astype(float))
    return x[finite], y[finite], groups[finite]


def _model_threads() -> int:
    try:
        return max(1, min(6, int(os.environ.get("BTC_RESEARCH_MODEL_THREADS", "4"))))
    except ValueError:
        return 4


def make_quantile_model(family: str, quantile: float, seed: int):
    family = family.lower()
    if family == "hgb":
        return HistGradientBoostingRegressor(
            loss="quantile",
            quantile=float(quantile),
            max_iter=180,
            learning_rate=0.05,
            max_leaf_nodes=31,
            min_samples_leaf=30,
            l2_regularization=1.0,
            random_state=seed,
        )
    if family == "lgb":
        from lightgbm import LGBMRegressor

        return LGBMRegressor(
            objective="quantile",
            alpha=float(quantile),
            n_estimators=260,
            learning_rate=0.04,
            num_leaves=31,
            min_child_samples=30,
            subsample=0.9,
            colsample_bytree=0.9,
            reg_lambda=1.0,
            device_type="cpu",
            n_jobs=_model_threads(),
            random_state=seed,
            verbosity=-1,
        )
    if family == "cat":
        from catboost import CatBoostRegressor

        return CatBoostRegressor(
            loss_function=f"Quantile:alpha={float(quantile)}",
            iterations=260,
            depth=7,
            learning_rate=0.04,
            l2_leaf_reg=4.0,
            random_seed=seed,
            thread_count=_model_threads(),
            task_type="CPU",
            verbose=False,
            allow_writing_files=False,
        )
    raise ValueError(f"unsupported model family: {family}")


def make_classifier(family: str, seed: int):
    family = family.lower()
    if family == "hgb":
        return HistGradientBoostingClassifier(
            max_iter=180,
            learning_rate=0.05,
            max_leaf_nodes=31,
            min_samples_leaf=30,
            l2_regularization=1.0,
            random_state=seed,
        )
    if family == "lgb":
        from lightgbm import LGBMClassifier

        return LGBMClassifier(
            n_estimators=260,
            learning_rate=0.04,
            num_leaves=31,
            min_child_samples=30,
            subsample=0.9,
            colsample_bytree=0.9,
            reg_lambda=1.0,
            device_type="cpu",
            n_jobs=_model_threads(),
            random_state=seed,
            verbosity=-1,
        )
    if family == "cat":
        from catboost import CatBoostClassifier

        return CatBoostClassifier(
            loss_function="Logloss",
            iterations=260,
            depth=7,
            learning_rate=0.04,
            l2_leaf_reg=4.0,
            random_seed=seed,
            thread_count=_model_threads(),
            task_type="CPU",
            verbose=False,
            allow_writing_files=False,
        )
    raise ValueError(f"unsupported model family: {family}")


def fit_quantile_members(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_calibration: np.ndarray,
    y_calibration: np.ndarray,
    x_test: np.ndarray,
    y_test: np.ndarray,
    *,
    quantile: float,
    families: tuple[str, ...],
    seed: int,
    log: Callable[[str], None] = print,
) -> tuple[list[tuple[str, Any]], dict[str, Any]]:
    candidates: list[tuple[float, str, Any, np.ndarray]] = []
    failures: dict[str, str] = {}
    for index, family in enumerate(families):
        try:
            log(
                f"[fit] quantile={quantile:.2f} family={family} "
                f"train={len(y_train):,} test={len(y_test):,}"
            )
            model = make_quantile_model(family, quantile, seed + index)
            model.fit(x_train, y_train)
            calibration_prediction = np.asarray(
                model.predict(x_calibration), dtype=float
            )
            selection_loss = float(
                mean_pinball_loss(
                    y_calibration,
                    calibration_prediction,
                    alpha=quantile,
                )
            )
            candidates.append(
                (
                    selection_loss,
                    family,
                    model,
                    np.asarray(model.predict(x_test), dtype=float),
                )
            )
        except Exception as exc:
            failures[family] = f"{type(exc).__name__}: {exc}"
        finally:
            gc.collect()
    if not candidates:
        raise RuntimeError(f"all quantile families failed: {failures}")
    candidates.sort(key=lambda item: item[0])
    selection_loss, selected_family, selected_model, test_prediction = candidates[0]
    members = [(selected_family, selected_model)]
    family_selection = [
        {"family": family, "calibration_pinball": loss, "selected": index == 0}
        for index, (loss, family, _, _) in enumerate(candidates)
    ]
    candidates = []
    gc.collect()
    return members, {
        "test_n": int(len(y_test)),
        "pinball_loss": float(
            mean_pinball_loss(y_test, test_prediction, alpha=quantile)
        ),
        "selected_family": selected_family,
        "selection_pinball": selection_loss,
        "family_selection": family_selection,
        "families": [selected_family],
        "family_failures": failures,
    }


def _positive_probability(model: Any, x: np.ndarray) -> np.ndarray:
    probability = np.asarray(model.predict_proba(x), dtype=float)
    classes = np.asarray(model.classes_)
    index = int(np.where(classes == 1)[0][0])
    return probability[:, index]


def fit_classifier_members(
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_calibration: np.ndarray,
    y_calibration: np.ndarray,
    x_test: np.ndarray,
    y_test: np.ndarray,
    *,
    families: tuple[str, ...],
    seed: int,
    log: Callable[[str], None] = print,
) -> tuple[list[tuple[str, Any]], IsotonicRegression | None, dict[str, Any]]:
    if len(np.unique(y_train)) < 2:
        raise ValueError("classification training target contains fewer than two classes")
    candidates: list[tuple[float, str, Any, np.ndarray, np.ndarray]] = []
    failures: dict[str, str] = {}
    selection_stop = max(1, len(y_calibration) // 2)
    for index, family in enumerate(families):
        try:
            log(
                f"[fit] classifier family={family} train={len(y_train):,} "
                f"cal={len(y_calibration):,} test={len(y_test):,}"
            )
            model = make_classifier(family, seed + index)
            model.fit(x_train, y_train)
            calibration_prediction = _positive_probability(model, x_calibration)
            test_prediction = _positive_probability(model, x_test)
            selection_brier = float(
                brier_score_loss(
                    y_calibration[:selection_stop],
                    calibration_prediction[:selection_stop],
                )
            )
            candidates.append(
                (
                    selection_brier,
                    family,
                    model,
                    calibration_prediction,
                    test_prediction,
                )
            )
        except Exception as exc:
            failures[family] = f"{type(exc).__name__}: {exc}"
        finally:
            gc.collect()
    if not candidates:
        raise RuntimeError(f"all classifier families failed: {failures}")
    candidates.sort(key=lambda item: item[0])
    selection_brier, selected_family, selected_model, raw_calibration, raw_test = (
        candidates[0]
    )
    members = [(selected_family, selected_model)]
    family_selection = [
        {"family": family, "selection_brier": score, "selected": index == 0}
        for index, (score, family, _, _, _) in enumerate(candidates)
    ]
    candidates = []
    gc.collect()
    calibrator: IsotonicRegression | None = None
    calibration_y = y_calibration[selection_stop:]
    calibration_probability = raw_calibration[selection_stop:]
    if len(calibration_y) >= 100 and len(np.unique(calibration_y)) == 2:
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(calibration_probability, calibration_y)
        test_probability = np.asarray(calibrator.predict(raw_test), dtype=float)
    else:
        test_probability = raw_test
    auc = (
        float(roc_auc_score(y_test, test_probability))
        if len(np.unique(y_test)) == 2
        else None
    )
    metrics: dict[str, Any] = {
        "test_n": int(len(y_test)),
        "base_rate": float(np.mean(y_test)),
        "auc": auc,
        "brier": float(brier_score_loss(y_test, test_probability)),
        "calibrated": calibrator is not None,
        "families": [selected_family],
        "selected_family": selected_family,
        "selection_brier": selection_brier,
        "family_selection": family_selection,
        "family_failures": failures,
    }
    try:
        observed, predicted = calibration_curve(
            y_test, test_probability, n_bins=10, strategy="quantile"
        )
        metrics["calibration_curve"] = [
            {"predicted": float(p), "observed": float(o)}
            for p, o in zip(predicted, observed)
        ]
    except ValueError:
        metrics["calibration_curve"] = []
    return members, calibrator, metrics


def predict_member_mean(members: list[tuple[str, Any]], x: np.ndarray) -> np.ndarray:
    return np.mean(
        [np.asarray(model.predict(x), dtype=float) for _, model in members], axis=0
    )


def predict_classifier(
    members: list[tuple[str, Any]],
    calibrator: IsotonicRegression | None,
    x: np.ndarray,
) -> np.ndarray:
    raw = np.mean([_positive_probability(model, x) for _, model in members], axis=0)
    return np.asarray(calibrator.predict(raw), dtype=float) if calibrator else raw


def atomic_joblib_dump(value: Any, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    os.close(descriptor)
    try:
        joblib.dump(value, temporary, compress=3)
        os.replace(temporary, target)
    finally:
        try:
            os.remove(temporary)
        except OSError:
            pass


def write_model_manifest(
    artifact_path: Path,
    dataset_path: Path,
    dataset_manifest: dict[str, Any],
    *,
    artifact_type: str,
    extra: dict[str, Any],
    feature_columns: tuple[str, ...] = FEATURE_COLUMNS,
) -> dict[str, Any]:
    manifest = {
        "manifest_version": 1,
        "artifact_type": artifact_type,
        "created_at": time.time(),
        "dataset_path": str(dataset_path.resolve()),
        "dataset_sha256": dataset_manifest["dataset_sha256"],
        "dataset_version": dataset_manifest["dataset_version"],
        "independent_rounds": dataset_manifest["independent_rounds"],
        "calendar_weeks": dataset_manifest["calendar_weeks"],
        "input_promotable": bool(dataset_manifest.get("promotable")),
        "feature_columns": list(feature_columns),
        "feature_schema_hash": hash_json(list(feature_columns)),
        "policy_hash": policy_hash(),
        "code_hash": _model_code_hash(artifact_type),
        "artifact_sha256": hash_file(artifact_path),
        "promotion_gate": PROMOTION_GATE,
        **extra,
    }
    atomic_write_json(artifact_path.with_suffix(".manifest.json"), manifest)
    return manifest


def artifact_issues(
    artifact_path: Path,
    *,
    require_promotable: bool = False,
    expected_feature_columns: tuple[str, ...] = FEATURE_COLUMNS,
) -> tuple[dict[str, Any], list[str]]:
    manifest_path = artifact_path.with_suffix(".manifest.json")
    if not artifact_path.is_file() or not manifest_path.is_file():
        return {}, ["artifact_or_manifest_missing"]
    try:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, ValueError) as exc:
        return {}, [f"manifest_unreadable:{exc}"]
    issues: list[str] = []
    if manifest.get("dataset_version") != CONFIG_VERSION:
        issues.append("dataset_version_mismatch")
    if manifest.get("policy_hash") != policy_hash():
        issues.append("policy_hash_mismatch")
    if manifest.get("feature_schema_hash") != hash_json(list(expected_feature_columns)):
        issues.append("feature_schema_mismatch")
    if manifest.get("artifact_sha256") != hash_file(artifact_path):
        issues.append("artifact_hash_mismatch")
    if manifest.get("code_hash") != _model_code_hash(
        str(manifest.get("artifact_type") or "")
    ):
        issues.append("model_code_mismatch")
    raw_dataset_path = manifest.get("dataset_path")
    if not raw_dataset_path:
        issues.append("training_dataset_path_missing")
    else:
        dataset_path = Path(str(raw_dataset_path))
        if not dataset_path.is_file():
            issues.append("training_dataset_missing")
        elif manifest.get("dataset_sha256") != hash_file(dataset_path):
            issues.append("training_dataset_hash_mismatch")
    if require_promotable and not manifest.get("input_promotable"):
        issues.append("insufficient_forward_evidence")
    return manifest, issues


def selftest() -> None:
    times = np.arange(100, dtype=int) * 300
    frame = pd.DataFrame(
        {
            "round_start_ts": np.repeat(times, 2),
            **{column: 1.0 for column in FEATURE_COLUMNS if column != "round_start_ts"},
            "target": np.tile([0, 1], 100),
            "entry_complete": 1,
        }
    )
    split = chronological_purged_split(frame)
    train_max = frame.loc[split["train"], "round_start_ts"].max()
    cal_min = frame.loc[split["calibration"], "round_start_ts"].min()
    test_min = frame.loc[split["test"], "round_start_ts"].min()
    assert cal_min - train_max >= 900
    assert test_min - frame.loc[split["calibration"], "round_start_ts"].max() >= 900
    print("complete-trade model_common self-test: ALL PASS")


if __name__ == "__main__":
    selftest()
