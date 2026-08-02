"""Resolved model-vote and time-to-expiry calibration experiments."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from research.phase5_standalone.common.engine_types import EngineContext, EngineResult
from research.phase5_standalone.common.metrics import EMPTY_ECONOMICS
from research.phase5_standalone.common.temporal_split import chronological_four_way_split

from .data import load_contract


def _clean_direction(value: Any) -> str | None:
    text = str(value or "").upper()
    if text in {"1", "1.0", "TRUE"}:
        return "UP"
    if text in {"0", "0.0", "FALSE"}:
        return "DOWN"
    return text if text in {"UP", "DOWN"} else None


def _model_vote_groups(context: EngineContext) -> tuple[pd.DataFrame, dict, dict]:
    loaded = load_contract(context.data_dir, context.protocol.payload["data_contract"],
                           context.maximum_rows)
    frame = loaded.frame.copy()
    frame["direction"] = frame["direction"].map(_clean_direction)
    frame["actual_direction"] = frame["actual_direction"].map(_clean_direction)
    frame = frame.dropna(subset=["direction", "actual_direction", "model", "horizon"])
    frame = frame[frame.get("resolved", True).fillna(False).astype(bool)]
    frame["vote"] = (frame["direction"] == "UP").astype(int)
    frame["correct"] = (frame["direction"] == frame["actual_direction"]).astype(int)
    frame["group_key"] = frame["timestamp"].astype(str) + ":" + frame["horizon"].astype(str)
    return frame, loaded.identity, loaded.causal_summary


def _minority_frame(frame: pd.DataFrame) -> pd.DataFrame:
    models = sorted(frame["model"].astype(str).unique())
    rows: list[dict[str, Any]] = []
    for _, group in frame.groupby("group_key", sort=False):
        group = group.drop_duplicates("model", keep="last")
        if len(group) < 3:
            continue
        up = int(group["vote"].sum())
        down = int(len(group) - up)
        if up == down or min(up, down) == 0:
            continue
        majority = "UP" if up > down else "DOWN"
        minority = "DOWN" if majority == "UP" else "UP"
        actual = str(group["actual_direction"].iloc[-1])
        row: dict[str, Any] = {
            "_ts_ms": int(group["_ts_ms"].max()),
            "horizon": float(group["horizon"].iloc[-1]),
            "model_count": float(len(group)),
            "up_fraction": float(up / len(group)),
            "disagreement": float(2 * min(up, down) / len(group)),
            "majority_correct": int(majority == actual),
            "minority_correct": int(minority == actual),
        }
        votes = dict(zip(group["model"].astype(str), group["vote"].astype(float)))
        row.update({f"vote_{model}": votes.get(model, 0.5) for model in models})
        rows.append(row)
    return pd.DataFrame(rows).sort_values("_ts_ms").reset_index(drop=True)


def _minority_correctness(context: EngineContext, frame: pd.DataFrame, identity: dict,
                          causal: dict) -> EngineResult:
    grouped = _minority_frame(frame)
    if len(grouped) < 500 or grouped["minority_correct"].nunique() < 2:
        raise ValueError(f"only {len(grouped)} usable disagreement groups")
    split = chronological_four_way_split(grouped["_ts_ms"], purge_rows=1,
                                         **context.split_args)
    features = [column for column in grouped if column.startswith("vote_")]
    features += ["horizon", "model_count", "up_fraction", "disagreement"]
    x = grouped[features].replace([np.inf, -np.inf], np.nan)
    y = grouped["minority_correct"].to_numpy(int)
    model = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                          LogisticRegression(max_iter=500, class_weight="balanced",
                                             random_state=context.seed))
    model.fit(x.iloc[split.train], y[split.train])
    raw_cal = model.predict_proba(x.iloc[split.calibration])[:, 1]
    calibrator = IsotonicRegression(out_of_bounds="clip").fit(raw_cal, y[split.calibration])
    p_policy = calibrator.predict(model.predict_proba(x.iloc[split.policy])[:, 1])
    thresholds = [0.50, 0.55, 0.60, 0.65, 0.70]
    majority_policy = grouped["majority_correct"].to_numpy(int)[split.policy]
    minority_policy = y[split.policy]
    scores = {
        threshold: float(np.mean(np.where(p_policy >= threshold,
                                          minority_policy, majority_policy)))
        for threshold in thresholds
    }
    locked = max(scores, key=scores.get)
    p_test = calibrator.predict(model.predict_proba(x.iloc[split.test])[:, 1])
    majority_test = grouped["majority_correct"].to_numpy(int)[split.test]
    minority_test = y[split.test]
    chosen = np.where(p_test >= locked, minority_test, majority_test)
    base_accuracy = float(majority_test.mean())
    selected_accuracy = float(chosen.mean())
    lift = selected_accuracy - base_accuracy
    diagnostics = {
        "groups": int(len(grouped)),
        "models": sorted(frame["model"].astype(str).unique()),
        "features": features,
        "locked_switch_threshold": float(locked),
        "policy_threshold_scores": {str(k): v for k, v in scores.items()},
        "untouched_auc_minority_correct": float(roc_auc_score(minority_test, p_test))
        if len(np.unique(minority_test)) == 2 else None,
        "untouched_majority_accuracy": base_accuracy,
        "untouched_meta_selected_accuracy": selected_accuracy,
        "untouched_accuracy_lift": float(lift),
        "switch_rate": float(np.mean(p_test >= locked)),
    }
    if lift <= 0:
        status, reasons = "FAIL_NO_EDGE", ["minority selector did not beat normal majority voting"]
    else:
        status, reasons = "FAIL_UNSTABLE", [
            "accuracy lift has no executable-return or model-release identity evidence"
        ]
    return EngineResult(status, "Minority-versus-consensus selector on resolved model votes",
                        diagnostics, dict(EMPTY_ECONOMICS), reasons, identity, causal,
                        split.boundaries)


def _false_consensus(context: EngineContext, frame: pd.DataFrame, identity: dict,
                     causal: dict) -> EngineResult:
    vote = frame.pivot_table(index="group_key", columns="model", values="vote", aggfunc="last")
    correct = frame.pivot_table(index="group_key", columns="model", values="correct", aggfunc="last")
    common = vote.dropna(axis=0, how="any")
    common_correct = correct.reindex(common.index).dropna(axis=0, how="any")
    common = common.reindex(common_correct.index)
    if len(common) < 100:
        raise ValueError(f"only {len(common)} complete model-vote groups")
    prediction_corr = common.corr().fillna(0.0)
    error_corr = (1.0 - common_correct).corr().fillna(0.0)
    eigenvalues = np.maximum(np.linalg.eigvalsh(prediction_corr.to_numpy(float)), 0.0)
    effective = float(eigenvalues.sum() ** 2 / max(np.square(eigenvalues).sum(), 1e-12))
    pair_values = prediction_corr.to_numpy(float)[np.triu_indices(len(prediction_corr), 1)]
    error_values = error_corr.to_numpy(float)[np.triu_indices(len(error_corr), 1)]
    model_count = int(common.shape[1])
    diagnostics = {
        "complete_groups": int(len(common)),
        "model_count": model_count,
        "effective_independent_model_count": effective,
        "effective_fraction": float(effective / model_count),
        "mean_pair_prediction_correlation": float(np.nanmean(pair_values)),
        "mean_pair_error_correlation": float(np.nanmean(error_values)),
        "prediction_correlation": prediction_corr.round(4).to_dict(),
        "error_correlation": error_corr.round(4).to_dict(),
        "feature_overlap_available": False,
    }
    reasons = ["per-model feature lineage is absent, so feature-overlap proof is incomplete"]
    if effective < model_count / 2:
        reasons.append("effective independent model count is below half the roster")
    return EngineResult("FAIL_UNSTABLE", "Effective ensemble independence diagnostic",
                        diagnostics, dict(EMPTY_ECONOMICS), reasons, identity, causal)


def run_ensemble_audit(context: EngineContext) -> EngineResult:
    frame, identity, causal = _model_vote_groups(context)
    mode = str(context.protocol.payload["method"]["mode"])
    if mode == "minority_correctness":
        return _minority_correctness(context, frame, identity, causal)
    if mode == "false_consensus":
        return _false_consensus(context, frame, identity, causal)
    raise ValueError(f"unknown ensemble-audit mode {mode}")


def _expiry_bucket(seconds: float) -> str:
    for low, high, label in [
        (600, math.inf, "15-10m"), (300, 600, "10-5m"), (180, 300, "5-3m"),
        (120, 180, "3-2m"), (60, 120, "2-1m"), (30, 60, "60-30s"),
        (15, 30, "30-15s"), (0, 15, "15-0s"),
    ]:
        if low <= seconds < high:
            return label
    return "outside"


def run_expiry_calibration(context: EngineContext) -> EngineResult:
    loaded = load_contract(context.data_dir, context.protocol.payload["data_contract"],
                           context.maximum_rows)
    frame = loaded.frame.copy()
    frame = frame[frame["eligible"].fillna(False).astype(bool)]
    frame["current_side"] = frame["current_side"].map(_clean_direction)
    frame["settled_side"] = frame["settled_side"].map(_clean_direction)
    frame["target"] = (frame["current_side"] == frame["settled_side"]).astype(int)
    frame["probability"] = pd.to_numeric(frame["p_hold_cur"], errors="coerce")
    frame["market_probability"] = np.where(
        frame["current_side"] == "UP", frame["up_mid"], frame["down_mid"])
    frame["market_probability"] = pd.to_numeric(frame["market_probability"], errors="coerce")
    frame["bucket"] = pd.to_numeric(frame["seconds_left"], errors="coerce").map(_expiry_bucket)
    frame = frame.dropna(subset=["probability", "market_probability", "seconds_left"])
    frame = frame[(frame["probability"].between(0, 1)) &
                  (frame["market_probability"].between(0, 1))]
    frame = frame.sort_values("_ts_ms").reset_index(drop=True)
    if len(frame) < 1_000:
        raise ValueError(f"only {len(frame)} eligible calibration rows")
    split = chronological_four_way_split(frame["_ts_ms"], purge_rows=1,
                                         **context.split_args)
    calibrators: dict[str, IsotonicRegression] = {}
    global_cal = IsotonicRegression(out_of_bounds="clip").fit(
        frame["probability"].to_numpy(float)[split.calibration],
        frame["target"].to_numpy(int)[split.calibration])
    for bucket in frame["bucket"].unique():
        idx = split.calibration[frame["bucket"].to_numpy()[split.calibration] == bucket]
        if len(idx) >= 50 and frame["target"].to_numpy()[idx].sum() not in {0, len(idx)}:
            calibrators[str(bucket)] = IsotonicRegression(out_of_bounds="clip").fit(
                frame["probability"].to_numpy(float)[idx], frame["target"].to_numpy(int)[idx])
    test = frame.iloc[split.test].copy()
    raw = test["probability"].to_numpy(float)
    target = test["target"].to_numpy(int)
    global_p = np.asarray(global_cal.predict(raw), dtype=float)
    bucket_p = global_p.copy()
    for bucket, calibrator in calibrators.items():
        mask = test["bucket"].astype(str).to_numpy() == bucket
        bucket_p[mask] = calibrator.predict(raw[mask])
    rows = {}
    for bucket, group in test.assign(bucket_probability=bucket_p).groupby("bucket"):
        y = group["target"].to_numpy(int)
        rows[str(bucket)] = {
            "rows": int(len(group)),
            "hold_rate": float(y.mean()),
            "raw_brier": float(brier_score_loss(y, group["probability"])),
            "bucket_calibrated_brier": float(brier_score_loss(y, group["bucket_probability"])),
            "market_brier": float(brier_score_loss(y, group["market_probability"])),
        }
    raw_brier = float(brier_score_loss(target, raw))
    calibrated_brier = float(brier_score_loss(target, bucket_p))
    market_brier = float(brier_score_loss(target, test["market_probability"]))
    diagnostics = {
        "bucket_surface": rows,
        "calibrated_buckets": sorted(calibrators),
        "untouched_raw_brier": raw_brier,
        "untouched_bucket_calibrated_brier": calibrated_brier,
        "untouched_market_brier": market_brier,
        "incremental_brier_vs_market": float(market_brier - calibrated_brier),
    }
    if calibrated_brier >= market_brier:
        status, reasons = "FAIL_NO_EDGE", ["calibrated model probability did not beat market probability"]
    else:
        status, reasons = "FAIL_UNSTABLE", [
            "Brier improvement is informational; executable entry economics were not established"
        ]
    return EngineResult(status, "Time-to-expiry calibration surface on untouched checkpoints",
                        diagnostics, dict(EMPTY_ECONOMICS), reasons, loaded.identity,
                        loaded.causal_summary, split.boundaries)
