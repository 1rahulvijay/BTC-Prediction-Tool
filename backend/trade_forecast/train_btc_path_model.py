"""Train time-indexed BTC quantiles and mutually exclusive first-passage risks."""
from __future__ import annotations

import argparse
import gc
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import log_loss, roc_auc_score


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from trade_forecast.model_common import (
    atomic_joblib_dump,
    chronological_purged_split,
    clean_xy,
    fit_quantile_members,
    load_verified_dataset,
    make_classifier,
    write_model_manifest,
)
from trade_forecast.trade_schema import (
    BTC_FEATURE_COLUMNS,
    CONFIG_VERSION,
    FUTURE_OFFSETS_S,
    HORIZONS,
    MODE,
    QUANTILES,
    policy_hash,
)


DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_DATASET = (
    DATA / "research" / "complete_trade_forecast" / "complete_trade_dataset.parquet"
)
DEFAULT_ARTIFACT = DATA / "saved_models" / "complete_trade_btc_path.pkl"
EVENT_CLASSES = ("ANCHOR", "LOWER", "NONE", "UPPER")


def _families() -> tuple[str, ...]:
    raw = os.environ.get("BTC_TRADE_FORECAST_FAMILIES", "hgb,lgb,cat")
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip()) or ("hgb",)


def _probabilities(model: Any, x: np.ndarray) -> np.ndarray:
    raw = np.asarray(model.predict_proba(x), dtype=float)
    output = np.zeros((len(x), len(EVENT_CLASSES)), dtype=float)
    for source, label in enumerate(model.classes_):
        if str(label) in EVENT_CLASSES:
            output[:, EVENT_CLASSES.index(str(label))] = raw[:, source]
    return output


def fit_competing_risk(
    train_x: np.ndarray,
    train_y: np.ndarray,
    calibration_x: np.ndarray,
    calibration_y: np.ndarray,
    test_x: np.ndarray,
    test_y: np.ndarray,
    families: tuple[str, ...],
    seed: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    members = []
    calibration_predictions = []
    test_predictions = []
    failures: dict[str, str] = {}
    for index, family in enumerate(families):
        try:
            print(
                f"[fit-btc] competing-risk family={family} train={len(train_y):,} "
                f"cal={len(calibration_y):,} test={len(test_y):,}",
                flush=True,
            )
            model = make_classifier(family, seed + index)
            model.fit(train_x, train_y)
            members.append((family, model))
            calibration_predictions.append(_probabilities(model, calibration_x))
            test_predictions.append(_probabilities(model, test_x))
        except Exception as exc:
            failures[family] = f"{type(exc).__name__}: {exc}"
        finally:
            gc.collect()
    if not members:
        raise RuntimeError(f"all competing-risk families failed: {failures}")
    raw_cal = np.mean(calibration_predictions, axis=0)
    raw_test = np.mean(test_predictions, axis=0)
    calibrators: dict[str, IsotonicRegression | None] = {}
    calibrated_test = np.zeros_like(raw_test)
    for index, label in enumerate(EVENT_CLASSES):
        binary_cal = (calibration_y == label).astype(int)
        if len(binary_cal) >= 100 and len(np.unique(binary_cal)) == 2:
            calibrator = IsotonicRegression(out_of_bounds="clip")
            calibrator.fit(raw_cal[:, index], binary_cal)
            calibrated_test[:, index] = calibrator.predict(raw_test[:, index])
            calibrators[label] = calibrator
        else:
            calibrated_test[:, index] = raw_test[:, index]
            calibrators[label] = None
    denominator = calibrated_test.sum(axis=1, keepdims=True)
    calibrated_test = np.divide(
        calibrated_test,
        denominator,
        out=np.full_like(calibrated_test, 1.0 / len(EVENT_CLASSES)),
        where=denominator > 0,
    )
    class_to_index = {label: index for index, label in enumerate(EVENT_CLASSES)}
    y_index = np.asarray([class_to_index[str(label)] for label in test_y], dtype=int)
    metrics = {
        "test_n": int(len(test_y)),
        "classes": list(EVENT_CLASSES),
        "log_loss": float(log_loss(y_index, calibrated_test, labels=range(len(EVENT_CLASSES)))),
        "families": [name for name, _ in members],
        "family_failures": failures,
    }
    try:
        truth = np.eye(len(EVENT_CLASSES))[y_index]
        metrics["macro_ovr_auc"] = float(
            roc_auc_score(truth, calibrated_test, average="macro", multi_class="ovr")
        )
    except ValueError:
        metrics["macro_ovr_auc"] = None
    return {"members": members, "calibrators": calibrators}, metrics


def train(
    dataset_path: Path,
    artifact_path: Path,
    *,
    families: tuple[str, ...],
    seed: int = 20260726,
) -> dict[str, Any]:
    started = time.time()
    source, dataset_manifest = load_verified_dataset(dataset_path)
    # BTC targets do not depend on chosen contract or quantity. Keep exactly one
    # UP/1-share row per round checkpoint to avoid pseudo-replication.
    frame = source[(source["side"] == "UP") & (source["requested_qty"] == 1)].copy()
    for offset in FUTURE_OFFSETS_S:
        frame[f"btc_return_{offset}s_bps"] = (
            frame[f"btc_delta_{offset}s"] / frame["current_btc"] * 10_000.0
        )
    frame["btc_return_settlement_bps"] = (
        frame["btc_delta_settlement"] / frame["current_btc"] * 10_000.0
    )
    frame["btc_actual_mfe_bps"] = frame["btc_actual_mfe"] / frame["current_btc"] * 10_000.0
    frame["btc_actual_mae_bps"] = frame["btc_actual_mae"] / frame["current_btc"] * 10_000.0
    bundle: dict[str, Any] = {
        "version": CONFIG_VERSION,
        "mode": MODE,
        "artifact_type": "complete_trade_btc_path",
        "feature_columns": list(BTC_FEATURE_COLUMNS),
        "policy_hash": policy_hash(),
        "horizons": {},
    }
    metrics: dict[str, Any] = {}
    for horizon in HORIZONS:
        horizon_mask = frame["horizon"].to_numpy(dtype=int) == horizon
        try:
            split = chronological_purged_split(
                frame,
                eligible_mask=horizon_mask,
            )
        except ValueError as exc:
            bundle["horizons"][int(horizon)] = {
                "quantiles": {},
                "competing_risk": {"supported": False},
            }
            metrics[str(horizon)] = {
                "supported": False,
                "reason": str(exc),
            }
            continue
        horizon_bundle: dict[str, Any] = {"quantiles": {}, "competing_risk": None}
        horizon_metrics: dict[str, Any] = {"quantiles": {}}
        targets = {
            **{
                f"{offset}s": f"btc_return_{offset}s_bps"
                for offset in FUTURE_OFFSETS_S
            },
            "settlement": "btc_return_settlement_bps",
            "mfe": "btc_actual_mfe_bps",
            "mae": "btc_actual_mae_bps",
            "first_event_time": "btc_first_event_s",
        }
        for label, target in targets.items():
            target_models: dict[float, Any] = {}
            target_metrics: dict[str, Any] = {}
            for quantile in QUANTILES:
                x_train, y_train, _ = clean_xy(
                    frame,
                    split["train"] & horizon_mask,
                    target,
                    feature_columns=BTC_FEATURE_COLUMNS,
                )
                x_cal, y_cal, _ = clean_xy(
                    frame,
                    split["calibration"] & horizon_mask,
                    target,
                    feature_columns=BTC_FEATURE_COLUMNS,
                )
                x_test, y_test, _ = clean_xy(
                    frame,
                    split["test"] & horizon_mask,
                    target,
                    feature_columns=BTC_FEATURE_COLUMNS,
                )
                if len(y_train) < 100 or len(y_cal) < 30 or len(y_test) < 30:
                    target_metrics[str(quantile)] = {
                        "supported": False,
                        "train_n": int(len(y_train)),
                        "calibration_n": int(len(y_cal)),
                        "test_n": int(len(y_test)),
                    }
                    continue
                members, fitted_metrics = fit_quantile_members(
                    x_train,
                    y_train.astype(float),
                    x_cal,
                    y_cal.astype(float),
                    x_test,
                    y_test.astype(float),
                    quantile=quantile,
                    families=families,
                    seed=seed + horizon * 1000 + len(horizon_metrics["quantiles"]) * 10,
                )
                target_models[float(quantile)] = members
                target_metrics[str(quantile)] = {
                    "supported": True,
                    "train_n": int(len(y_train)),
                    **fitted_metrics,
                }
                del x_train, y_train, x_cal, y_cal, x_test, y_test
                gc.collect()
            horizon_bundle["quantiles"][label] = target_models
            horizon_metrics["quantiles"][label] = target_metrics

        valid_event = frame["btc_first_event"].isin(EVENT_CLASSES).to_numpy()
        sets = {}
        for split_name in ("train", "calibration", "test"):
            selected = frame.loc[split[split_name] & horizon_mask & valid_event].dropna(
                subset=[*BTC_FEATURE_COLUMNS, "btc_first_event"]
            )
            x = selected.loc[:, BTC_FEATURE_COLUMNS].to_numpy(dtype=np.float32)
            finite = np.isfinite(x).all(axis=1)
            sets[split_name] = (
                x[finite],
                selected["btc_first_event"].to_numpy(dtype=str)[finite],
            )
        if (
            len(sets["train"][1]) >= 100
            and len(sets["calibration"][1]) >= 30
            and len(sets["test"][1]) >= 30
            and len(np.unique(sets["train"][1])) >= 2
        ):
            head, risk_metrics = fit_competing_risk(
                sets["train"][0],
                sets["train"][1],
                sets["calibration"][0],
                sets["calibration"][1],
                sets["test"][0],
                sets["test"][1],
                families,
                seed + horizon * 100,
            )
            horizon_bundle["competing_risk"] = {"supported": True, **head}
            horizon_metrics["competing_risk"] = {"supported": True, **risk_metrics}
        else:
            horizon_bundle["competing_risk"] = {"supported": False}
            horizon_metrics["competing_risk"] = {
                "supported": False,
                "reason": "insufficient_rows_or_classes",
                "counts": {key: int(len(value[1])) for key, value in sets.items()},
            }
        bundle["horizons"][int(horizon)] = horizon_bundle
        metrics[str(horizon)] = horizon_metrics
    bundle["training_status"] = (
        "SHADOW_EVIDENCE_READY"
        if dataset_manifest.get("promotable")
        else "PILOT_ONLY_NOT_PROMOTABLE"
    )
    bundle["metrics"] = metrics
    atomic_joblib_dump(bundle, artifact_path)
    manifest = write_model_manifest(
        artifact_path,
        dataset_path,
        dataset_manifest,
        artifact_type="complete_trade_btc_path",
        feature_columns=BTC_FEATURE_COLUMNS,
        extra={
            "training_status": bundle["training_status"],
            "metrics": metrics,
            "families_requested": list(families),
            "elapsed_seconds": round(time.time() - started, 2),
        },
    )
    print(
        f"[train-btc] saved {artifact_path} status={bundle['training_status']} "
        f"elapsed={time.time()-started:.1f}s",
        flush=True,
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--families", nargs="+", default=list(_families()))
    args = parser.parse_args()
    train(args.dataset.resolve(), args.artifact.resolve(), families=tuple(args.families))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
