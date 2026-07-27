"""Train arrival-slippage, full-fill, quote-survival, and capacity heads."""
from __future__ import annotations

import argparse
import gc
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from trade_forecast.model_common import (
    atomic_joblib_dump,
    chronological_purged_split,
    clean_xy,
    fit_classifier_members,
    fit_quantile_members,
    load_verified_dataset,
    write_model_manifest,
)
from trade_forecast.trade_schema import CONFIG_VERSION, FEATURE_COLUMNS, HORIZONS, MODE, policy_hash


DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_DATASET = (
    DATA / "research" / "complete_trade_forecast" / "complete_trade_dataset.parquet"
)
DEFAULT_ARTIFACT = DATA / "saved_models" / "complete_trade_execution_heads.pkl"
# q10 capacity is a binding Clarification-001 eligibility gate. It cannot be inferred from q50:
# median capacity would fail in roughly half of comparable books, exactly when liquidity matters.
EXECUTION_QUANTILES = (0.10, 0.50, 0.80, 0.95)


def _families() -> tuple[str, ...]:
    raw = os.environ.get("BTC_TRADE_FORECAST_FAMILIES", "hgb,lgb,cat")
    return tuple(item.strip().lower() for item in raw.split(",") if item.strip()) or ("hgb",)


def train(
    dataset_path: Path,
    artifact_path: Path,
    *,
    families: tuple[str, ...],
    seed: int = 20260726,
) -> dict[str, Any]:
    started = time.time()
    frame, dataset_manifest = load_verified_dataset(dataset_path)
    # `entry_quote_survived` is now a REAL label computed in the dataset builder against the
    # size-aware decision VWAP. The previous definition here was `entry_eligible`, i.e. merely
    # "some entry existed after latency" - which is true even when the size vanished and the price
    # moved two cents against us, so the head was trained to predict a much easier event than the
    # one its name promised.
    if "entry_quote_survived" not in frame.columns:
        raise RuntimeError(
            "dataset predates real quote-survival labels; rebuild with "
            "build_complete_trade_dataset.py before training execution heads"
        )
    group_columns = ["round_id", "horizon", "seconds_left", "side"]
    complete_capacity = (
        frame.loc[frame["entry_complete"] == 1]
        .groupby(group_columns)["requested_qty"]
        .max()
        .rename("max_executable_qty")
        .reset_index()
    )
    frame = frame.merge(complete_capacity, on=group_columns, how="left")
    frame["max_executable_qty"] = frame["max_executable_qty"].fillna(0.0)
    bundle: dict[str, Any] = {
        "version": CONFIG_VERSION,
        "mode": MODE,
        "artifact_type": "complete_trade_execution_heads",
        "feature_columns": list(FEATURE_COLUMNS),
        "policy_hash": policy_hash(),
        "horizons": {},
    }
    metrics: dict[str, Any] = {}
    for horizon in HORIZONS:
        mask = frame["horizon"].to_numpy(dtype=int) == horizon
        try:
            split = chronological_purged_split(frame, eligible_mask=mask)
        except ValueError as exc:
            bundle["horizons"][int(horizon)] = {"quantiles": {}, "events": {}}
            metrics[str(horizon)] = {
                "supported": False,
                "reason": str(exc),
            }
            continue
        horizon_bundle: dict[str, Any] = {"quantiles": {}, "events": {}}
        horizon_metrics: dict[str, Any] = {"quantiles": {}, "events": {}}
        for target in ("entry_arrival_slippage", "max_executable_qty"):
            target_bundle = {}
            target_metrics = {}
            for quantile in EXECUTION_QUANTILES:
                x_train, y_train, _ = clean_xy(
                    frame,
                    split["train"] & mask,
                    target,
                    require_complete_entry=(target == "entry_arrival_slippage"),
                )
                x_cal, y_cal, _ = clean_xy(
                    frame,
                    split["calibration"] & mask,
                    target,
                    require_complete_entry=(target == "entry_arrival_slippage"),
                )
                x_test, y_test, _ = clean_xy(
                    frame,
                    split["test"] & mask,
                    target,
                    require_complete_entry=(target == "entry_arrival_slippage"),
                )
                if len(y_train) < 200 or len(y_cal) < 50 or len(y_test) < 50:
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
                    seed=seed + horizon * 1000 + int(quantile * 100),
                )
                target_bundle[float(quantile)] = members
                target_metrics[str(quantile)] = {
                    "supported": True,
                    "train_n": int(len(y_train)),
                    **fitted_metrics,
                }
                del x_train, y_train, x_cal, y_cal, x_test, y_test
                gc.collect()
            horizon_bundle["quantiles"][target] = target_bundle
            horizon_metrics["quantiles"][target] = target_metrics
        for target in (
            "entry_complete",
            "entry_quote_survived",
            "entry_worse_by_1c",
            "entry_worse_by_2c",
        ):
            sets = {}
            for split_name in ("train", "calibration", "test"):
                sets[split_name] = clean_xy(frame, split[split_name] & mask, target)
            if (
                len(sets["train"][1]) < 200
                or len(sets["calibration"][1]) < 50
                or len(sets["test"][1]) < 50
                or len(np.unique(sets["train"][1])) < 2
            ):
                horizon_bundle["events"][target] = {"supported": False}
                horizon_metrics["events"][target] = {
                    "supported": False,
                    "reason": "insufficient_rows_or_classes",
                }
                continue
            members, calibrator, fitted_metrics = fit_classifier_members(
                sets["train"][0],
                sets["train"][1].astype(int),
                sets["calibration"][0],
                sets["calibration"][1].astype(int),
                sets["test"][0],
                sets["test"][1].astype(int),
                families=families,
                seed=seed + horizon * 100,
            )
            horizon_bundle["events"][target] = {
                "supported": True,
                "members": members,
                "calibrator": calibrator,
            }
            horizon_metrics["events"][target] = {"supported": True, **fitted_metrics}
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
        artifact_type="complete_trade_execution_heads",
        extra={
            "training_status": bundle["training_status"],
            "metrics": metrics,
            "families_requested": list(families),
            "elapsed_seconds": round(time.time() - started, 2),
        },
    )
    print(
        f"[train-execution] saved {artifact_path} status={bundle['training_status']} "
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
