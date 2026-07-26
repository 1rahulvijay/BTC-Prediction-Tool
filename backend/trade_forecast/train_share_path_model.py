"""Train future executable share-bid quantiles and complete-trade event heads."""
from __future__ import annotations

import argparse
import gc
import os
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


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
    predict_classifier,
    write_model_manifest,
)
from trade_forecast.trade_schema import (
    CLASSIFICATION_TARGETS,
    CONFIG_VERSION,
    CROSSING_TARGETS,
    FEATURE_COLUMNS,
    FUTURE_OFFSETS_S,
    HORIZONS,
    MODE,
    PROMOTION_GATE,
    QUANTILES,
    SHARE_SUMMARY_TARGETS,
    policy_hash,
)


DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_DATASET = (
    DATA / "research" / "complete_trade_forecast" / "complete_trade_dataset.parquet"
)
DEFAULT_ARTIFACT = DATA / "saved_models" / "complete_trade_share_path.pkl"


def _families_from_environment() -> tuple[str, ...]:
    raw = os.environ.get("BTC_TRADE_FORECAST_FAMILIES", "hgb,lgb,cat")
    values = tuple(value.strip().lower() for value in raw.split(",") if value.strip())
    return values or ("hgb",)


def _day_block_lower_bound(values: pd.DataFrame, seed: int = 20260726) -> float | None:
    daily = (
        values.assign(day=pd.to_datetime(values["round_start_ts"], unit="s", utc=True).dt.date)
        .groupby("day")["realized_net"]
        .mean()
        .to_numpy(dtype=float)
    )
    if len(daily) < 5:
        return None
    rng = np.random.default_rng(seed)
    samples = daily[rng.integers(0, len(daily), size=(5000, len(daily)))].mean(axis=1)
    return float(np.percentile(samples, 2.5))


def evaluate_m0(
    frame: pd.DataFrame,
    test_mask: np.ndarray,
    event_head: dict[str, Any] | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": "NOT_EVALUABLE",
        "passed": False,
        "reasons": [],
        "buckets": [],
    }
    if not event_head or not event_head.get("supported"):
        result["reasons"].append("p_ever_profitable_head_unavailable")
        return result
    required = [
        *FEATURE_COLUMNS,
        "entry_complete",
        "label_ever_profitable",
        "plan_take_3c_or_stop_3c_net",
        "plan_hold_to_settlement_net",
        "stress_1000ms_take_3c_or_stop_3c_net",
        "round_start_ts",
    ]
    selected = frame.loc[test_mask].dropna(subset=required).copy()
    selected = selected[selected["entry_complete"] == 1]
    if len(selected) < 100:
        result["reasons"].append("fewer_than_100_test_candidates")
        return result
    x = selected.loc[:, FEATURE_COLUMNS].to_numpy(dtype=np.float32)
    finite = np.isfinite(x).all(axis=1)
    selected = selected.loc[finite].copy()
    x = x[finite]
    score = predict_classifier(event_head["members"], event_head["calibrator"], x)
    selected["score"] = score
    selected["realized_net"] = selected["plan_take_3c_or_stop_3c_net"].astype(float)
    selected["hold_net"] = selected["plan_hold_to_settlement_net"].astype(float)
    selected["stress_net"] = selected[
        "stress_1000ms_take_3c_or_stop_3c_net"
    ].astype(float)
    selected["week"] = pd.to_datetime(
        selected["round_start_ts"], unit="s", utc=True
    ).dt.strftime("%G-%V")
    selected["hour"] = pd.to_datetime(
        selected["round_start_ts"], unit="s", utc=True
    ).dt.hour
    try:
        selected["vol_regime"] = pd.qcut(
            selected["btc_vol_60s_pct"].rank(method="first"),
            3,
            labels=["LOW_VOL", "MID_VOL", "HIGH_VOL"],
        )
    except ValueError:
        selected["vol_regime"] = "UNKNOWN"
    try:
        selected["bucket"] = pd.qcut(
            selected["score"].rank(method="first"), 5, labels=["Q1", "Q2", "Q3", "Q4", "Q5"]
        )
    except ValueError:
        result["reasons"].append("score_has_insufficient_rank_variation")
        return result
    buckets = []
    for label, group in selected.groupby("bucket", observed=True):
        buckets.append(
            {
                "bucket": str(label),
                "n": int(len(group)),
                "mean_score": float(group["score"].mean()),
                "realized_ev": float(group["realized_net"].mean()),
                "win_rate": float((group["realized_net"] > 0).mean()),
                "mfe": float(group["actual_mfe"].mean()),
                "mae": float(group["actual_mae"].mean()),
                "ever_profitable": float(group["label_ever_profitable"].mean()),
                "first_profitable_s": (
                    float(group["actual_first_profitable_s"].dropna().mean())
                    if group["actual_first_profitable_s"].notna().any()
                    else None
                ),
                "entry_fill_rate": float(group["entry_complete"].mean()),
            }
        )
    result["buckets"] = buckets
    by_name = {row["bucket"]: row for row in buckets}
    q5 = selected[selected["bucket"] == "Q5"]
    q5_lb = _day_block_lower_bound(q5)
    q5_minus_q3 = (
        by_name["Q5"]["realized_ev"] - by_name["Q3"]["realized_ev"]
        if "Q5" in by_name and "Q3" in by_name
        else None
    )
    evs = [by_name.get(f"Q{index}", {}).get("realized_ev") for index in range(1, 6)]
    comparable = [value for value in evs if value is not None]
    monotonic_steps = sum(
        float(right) >= float(left) for left, right in zip(comparable, comparable[1:])
    )
    broad_monotonic = len(comparable) == 5 and monotonic_steps >= 3
    q5_weekly = q5.groupby("week")["realized_net"].mean()
    q5_regime = q5.groupby("vol_regime", observed=True)["realized_net"].agg(
        ["count", "mean"]
    )
    q5_stress_ev = float(q5["stress_net"].mean()) if len(q5) else None
    q5_hold_ev = float(q5["hold_net"].mean()) if len(q5) else None
    q5_profit = float(q5.loc[q5["realized_net"] > 0, "realized_net"].sum())
    q5_loss = float(-q5.loc[q5["realized_net"] < 0, "realized_net"].sum())
    q5_profit_factor = q5_profit / q5_loss if q5_loss > 1e-12 else None
    max_hour_share = (
        float(q5["hour"].value_counts(normalize=True).max()) if len(q5) else 1.0
    )
    result.update(
        {
            "status": "EVALUATED",
            "q5_day_block_lb": q5_lb,
            "q5_minus_q3": q5_minus_q3,
            "broad_monotonic": broad_monotonic,
            "test_weeks": int(selected["week"].nunique()),
            "q5_weekly_ev": {
                str(key): float(value) for key, value in q5_weekly.items()
            },
            "q5_vol_regime_ev": {
                str(index): {
                    "n": int(row["count"]),
                    "ev": float(row["mean"]),
                }
                for index, row in q5_regime.iterrows()
            },
            "q5_stress_1000ms_ev": q5_stress_ev,
            "q5_hold_to_settlement_ev": q5_hold_ev,
            "q5_profit_factor": q5_profit_factor,
            "q5_max_single_hour_share": max_hour_share,
        }
    )
    if q5_lb is None:
        result["reasons"].append("fewer_than_5_independent_test_days")
    elif q5_lb <= PROMOTION_GATE["m0_q5_day_block_lb_min"]:
        result["reasons"].append("q5_day_block_lower_bound_not_positive")
    if q5_minus_q3 is None or q5_minus_q3 < PROMOTION_GATE["m0_q5_minus_q3_min"]:
        result["reasons"].append("q5_does_not_beat_q3_by_frozen_margin")
    if not broad_monotonic:
        result["reasons"].append("bucket_order_not_broadly_monotonic")
    if int(selected["week"].nunique()) < PROMOTION_GATE["m0_min_test_weeks"]:
        result["reasons"].append("insufficient_independent_test_weeks")
    elif (q5_weekly <= 0.0).any():
        result["reasons"].append("q5_not_positive_in_every_test_week")
    if len(q5_regime) < 3 or any(
        int(row["count"]) < 20 or float(row["mean"]) <= 0.0
        for _, row in q5_regime.iterrows()
    ):
        result["reasons"].append("q5_not_stable_across_volatility_regimes")
    if q5_stress_ev is None or q5_stress_ev <= 0.0:
        result["reasons"].append("q5_does_not_survive_1000ms_latency")
    if q5_hold_ev is None or q5_lb is None or by_name.get("Q5", {}).get(
        "realized_ev", float("-inf")
    ) <= q5_hold_ev:
        result["reasons"].append("q5_does_not_beat_hold_to_settlement_control")
    if (
        q5_profit_factor is None
        or q5_profit_factor < PROMOTION_GATE["m0_min_profit_factor"]
    ):
        result["reasons"].append("q5_profit_factor_below_gate")
    if max_hour_share > PROMOTION_GATE["m0_max_single_hour_share"]:
        result["reasons"].append("q5_concentrated_in_one_utc_hour")
    result["passed"] = not result["reasons"]
    return result


def train(
    dataset_path: Path,
    artifact_path: Path,
    *,
    families: tuple[str, ...],
    seed: int = 20260726,
) -> dict[str, Any]:
    started = time.time()
    frame, dataset_manifest = load_verified_dataset(dataset_path)
    bundle: dict[str, Any] = {
        "version": CONFIG_VERSION,
        "mode": MODE,
        "artifact_type": "complete_trade_share_path",
        "feature_columns": list(FEATURE_COLUMNS),
        "policy_hash": policy_hash(),
        "families_requested": list(families),
        "horizons": {},
    }
    all_metrics: dict[str, Any] = {}
    m0_by_horizon: dict[str, Any] = {}
    for horizon in HORIZONS:
        horizon_mask = frame["horizon"].to_numpy(dtype=int) == int(horizon)
        try:
            split = chronological_purged_split(
                frame,
                eligible_mask=horizon_mask,
            )
        except ValueError as exc:
            bundle["horizons"][int(horizon)] = {
                "quantiles": {},
                "summary_quantiles": {},
                "events": {},
            }
            all_metrics[str(horizon)] = {
                "supported": False,
                "reason": str(exc),
            }
            m0_by_horizon[str(horizon)] = {
                "status": "NOT_EVALUABLE",
                "passed": False,
                "reasons": ["horizon_split_unavailable"],
                "buckets": [],
            }
            continue
        horizon_bundle: dict[str, Any] = {
            "quantiles": {},
            "ask_quantiles": {},
            "summary_quantiles": {},
            "events": {},
        }
        metrics: dict[str, Any] = {
            "quantiles": {},
            "ask_quantiles": {},
            "summary_quantiles": {},
            "events": {},
        }
        for offset in FUTURE_OFFSETS_S:
            target = f"share_bid_logit_delta_{offset}s"
            offset_models: dict[float, Any] = {}
            offset_metrics: dict[str, Any] = {}
            for quantile in QUANTILES:
                x_train, y_train, _ = clean_xy(
                    frame,
                    split["train"] & horizon_mask,
                    target,
                    require_complete_entry=True,
                )
                x_cal, y_cal, _ = clean_xy(
                    frame,
                    split["calibration"] & horizon_mask,
                    target,
                    require_complete_entry=True,
                )
                x_test, y_test, _ = clean_xy(
                    frame,
                    split["test"] & horizon_mask,
                    target,
                    require_complete_entry=True,
                )
                if len(y_train) < 200 or len(y_cal) < 50 or len(y_test) < 50:
                    offset_metrics[str(quantile)] = {
                        "supported": False,
                        "reason": "insufficient_rows",
                        "train_n": int(len(y_train)),
                        "calibration_n": int(len(y_cal)),
                        "test_n": int(len(y_test)),
                    }
                    continue
                members, member_metrics = fit_quantile_members(
                    x_train,
                    y_train.astype(float),
                    x_cal,
                    y_cal.astype(float),
                    x_test,
                    y_test.astype(float),
                    quantile=quantile,
                    families=families,
                    seed=seed + horizon * 1000 + offset * 10 + int(quantile * 100),
                )
                offset_models[float(quantile)] = members
                offset_metrics[str(quantile)] = {
                    "supported": True,
                    "train_n": int(len(y_train)),
                    **member_metrics,
                }
                del x_train, y_train, x_cal, y_cal, x_test, y_test
                gc.collect()
            horizon_bundle["quantiles"][int(offset)] = offset_models
            metrics["quantiles"][str(offset)] = offset_metrics

        for offset in FUTURE_OFFSETS_S:
            target = f"share_ask_logit_delta_{offset}s"
            offset_models: dict[float, Any] = {}
            offset_metrics: dict[str, Any] = {}
            for quantile in (0.10, 0.50, 0.90):
                x_train, y_train, _ = clean_xy(
                    frame,
                    split["train"] & horizon_mask,
                    target,
                    require_complete_entry=True,
                )
                x_cal, y_cal, _ = clean_xy(
                    frame,
                    split["calibration"] & horizon_mask,
                    target,
                    require_complete_entry=True,
                )
                x_test, y_test, _ = clean_xy(
                    frame,
                    split["test"] & horizon_mask,
                    target,
                    require_complete_entry=True,
                )
                if len(y_train) < 200 or len(y_cal) < 50 or len(y_test) < 50:
                    offset_metrics[str(quantile)] = {
                        "supported": False,
                        "reason": "insufficient_rows",
                        "train_n": int(len(y_train)),
                        "calibration_n": int(len(y_cal)),
                        "test_n": int(len(y_test)),
                    }
                    continue
                members, member_metrics = fit_quantile_members(
                    x_train,
                    y_train.astype(float),
                    x_cal,
                    y_cal.astype(float),
                    x_test,
                    y_test.astype(float),
                    quantile=quantile,
                    families=families,
                    seed=seed + horizon * 1000 + 500 + offset + int(quantile * 100),
                )
                offset_models[float(quantile)] = members
                offset_metrics[str(quantile)] = {
                    "supported": True,
                    "train_n": int(len(y_train)),
                    **member_metrics,
                }
                del x_train, y_train, x_cal, y_cal, x_test, y_test
                gc.collect()
            horizon_bundle["ask_quantiles"][int(offset)] = offset_models
            metrics["ask_quantiles"][str(offset)] = offset_metrics

        for target_index, target in enumerate(SHARE_SUMMARY_TARGETS):
            target_models: dict[float, Any] = {}
            target_metrics: dict[str, Any] = {}
            for quantile in (0.10, 0.50, 0.90):
                x_train, y_train, _ = clean_xy(
                    frame,
                    split["train"] & horizon_mask,
                    target,
                    require_complete_entry=True,
                )
                x_cal, y_cal, _ = clean_xy(
                    frame,
                    split["calibration"] & horizon_mask,
                    target,
                    require_complete_entry=True,
                )
                x_test, y_test, _ = clean_xy(
                    frame,
                    split["test"] & horizon_mask,
                    target,
                    require_complete_entry=True,
                )
                if len(y_train) < 200 or len(y_cal) < 50 or len(y_test) < 50:
                    target_metrics[str(quantile)] = {
                        "supported": False,
                        "reason": "insufficient_rows",
                        "train_n": int(len(y_train)),
                        "calibration_n": int(len(y_cal)),
                        "test_n": int(len(y_test)),
                    }
                    continue
                members, member_metrics = fit_quantile_members(
                    x_train,
                    y_train.astype(float),
                    x_cal,
                    y_cal.astype(float),
                    x_test,
                    y_test.astype(float),
                    quantile=quantile,
                    families=families,
                    seed=(
                        seed
                        + horizon * 1000
                        + 700
                        + target_index * 50
                        + int(quantile * 100)
                    ),
                )
                target_models[float(quantile)] = members
                target_metrics[str(quantile)] = {
                    "supported": True,
                    "train_n": int(len(y_train)),
                    **member_metrics,
                }
                del x_train, y_train, x_cal, y_cal, x_test, y_test
                gc.collect()
            horizon_bundle["summary_quantiles"][target] = target_models
            metrics["summary_quantiles"][target] = target_metrics

        for target in (*CLASSIFICATION_TARGETS, *CROSSING_TARGETS):
            require_complete = target != "entry_complete"
            x_train, y_train, _ = clean_xy(
                frame,
                split["train"] & horizon_mask,
                target,
                require_complete_entry=require_complete,
            )
            x_cal, y_cal, _ = clean_xy(
                frame,
                split["calibration"] & horizon_mask,
                target,
                require_complete_entry=require_complete,
            )
            x_test, y_test, _ = clean_xy(
                frame,
                split["test"] & horizon_mask,
                target,
                require_complete_entry=require_complete,
            )
            if (
                len(y_train) < 200
                or len(y_cal) < 50
                or len(y_test) < 50
                or len(np.unique(y_train)) < 2
            ):
                metrics["events"][target] = {
                    "supported": False,
                    "reason": "insufficient_rows_or_classes",
                    "train_n": int(len(y_train)),
                    "calibration_n": int(len(y_cal)),
                    "test_n": int(len(y_test)),
                }
                horizon_bundle["events"][target] = {"supported": False}
                continue
            members, calibrator, event_metrics = fit_classifier_members(
                x_train,
                y_train.astype(int),
                x_cal,
                y_cal.astype(int),
                x_test,
                y_test.astype(int),
                families=families,
                seed=seed + horizon * 1000 + len(metrics["events"]) * 10,
            )
            horizon_bundle["events"][target] = {
                "supported": True,
                "members": members,
                "calibrator": calibrator,
            }
            metrics["events"][target] = {
                "supported": True,
                "train_n": int(len(y_train)),
                "calibration_n": int(len(y_cal)),
                **event_metrics,
            }
            del x_train, y_train, x_cal, y_cal, x_test, y_test
            gc.collect()
        bundle["horizons"][int(horizon)] = horizon_bundle
        all_metrics[str(horizon)] = metrics
        m0_by_horizon[str(horizon)] = evaluate_m0(
            frame,
            split["test"] & horizon_mask,
            horizon_bundle["events"].get("label_ever_profitable"),
        )

    evidence_gate = bool(dataset_manifest.get("promotable"))
    m0_passed = evidence_gate and all(
        result.get("passed") for result in m0_by_horizon.values()
    )
    bundle["training_status"] = (
        "SHADOW_M0_PASSED" if m0_passed else "PILOT_ONLY_NOT_PROMOTABLE"
    )
    bundle["metrics"] = all_metrics
    bundle["m0"] = m0_by_horizon
    atomic_joblib_dump(bundle, artifact_path)
    manifest = write_model_manifest(
        artifact_path,
        dataset_path,
        dataset_manifest,
        artifact_type="complete_trade_share_path",
        extra={
            "training_status": bundle["training_status"],
            "m0_passed": m0_passed,
            "m0": m0_by_horizon,
            "metrics": all_metrics,
            "families_requested": list(families),
            "elapsed_seconds": round(time.time() - started, 2),
        },
    )
    print(
        f"[train-share] saved {artifact_path} status={bundle['training_status']} "
        f"elapsed={time.time()-started:.1f}s",
        flush=True,
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--families", nargs="+", default=list(_families_from_environment()))
    args = parser.parse_args()
    train(
        args.dataset.resolve(),
        args.artifact.resolve(),
        families=tuple(args.families),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
