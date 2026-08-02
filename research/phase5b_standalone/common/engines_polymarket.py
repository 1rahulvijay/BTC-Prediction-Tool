"""Polymarket path structure, execution asymmetry and response decomposition."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, brier_score_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from research.phase5_standalone.common.cost_model import polymarket_fee_per_share
from research.phase5_standalone.common.engine_types import EngineContext, EngineResult
from research.phase5_standalone.common.metrics import (
    EMPTY_ECONOMICS,
    economic_metrics,
    economic_verdict,
)
from research.phase5_standalone.common.temporal_split import chronological_four_way_split

from .data import load_contract, load_db_table


def _side(value: Any) -> str | None:
    text = str(value or "").upper()
    if text in {"UP", "1", "1.0", "TRUE"}:
        return "UP"
    if text in {"DOWN", "0", "0.0", "FALSE"}:
        return "DOWN"
    return None


def _pm_frame(context: EngineContext) -> tuple[pd.DataFrame, dict, dict]:
    loaded = load_contract(context.data_dir, context.protocol.payload["data_contract"],
                           context.maximum_rows)
    frame = loaded.frame.copy()
    frame = frame[frame["eligible"].fillna(False).astype(bool)].copy()
    frame["current_side"] = frame["current_side"].map(_side)
    frame["settled_side"] = frame["settled_side"].map(_side)
    numeric = ["snapshot_ts", "seconds_left", "checkpoint_s", "distance_bps", "vol_60s_pct",
               "p_hold_cur", "up_bid", "up_ask", "up_mid", "up_spread", "up_top_ask_size",
               "up_d1", "down_bid", "down_ask", "down_mid", "down_spread",
               "down_top_ask_size", "down_d1"]
    for column in numeric:
        if column in frame:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["current_side", "settled_side", "distance_bps", "snapshot_ts"])
    return frame.sort_values(["slug", "horizon", "snapshot_ts"]).reset_index(drop=True), \
        loaded.identity, loaded.causal_summary


def _fit_binary_wait(frame: pd.DataFrame, features: list[str], split, seed: int):
    x = frame[features].replace([np.inf, -np.inf], np.nan)
    y = frame["wait_better"].to_numpy(int)
    model = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                          LogisticRegression(max_iter=500, class_weight="balanced",
                                             random_state=seed))
    model.fit(x.iloc[split.train], y[split.train])
    raw_cal = model.predict_proba(x.iloc[split.calibration])[:, 1]
    calibration_y = y[split.calibration]
    if len(np.unique(raw_cal)) < 2 or len(np.unique(calibration_y)) < 2:
        calibrator = None
    else:
        calibrator = IsotonicRegression(out_of_bounds="clip").fit(raw_cal, calibration_y)

    def predict(indices):
        raw = model.predict_proba(x.iloc[indices])[:, 1]
        return np.asarray(calibrator.predict(raw) if calibrator else raw, dtype=float)

    return predict


def _anchor_pinning(context: EngineContext, frame: pd.DataFrame, identity: dict,
                    causal: dict) -> EngineResult:
    rows = []
    for _, group in frame.groupby(["slug", "horizon"], sort=False):
        group = group.sort_values("snapshot_ts").reset_index(drop=True)
        distance = group["distance_bps"].to_numpy(float)
        for index in range(len(group) - 1):
            future = distance[index + 1:]
            signs = np.sign(future)
            signs = signs[signs != 0]
            crossings = int(np.sum(signs[1:] != signs[:-1])) if len(signs) > 1 else 0
            if np.max(np.abs(future), initial=0.0) <= 5.0:
                state = "PINNED"
            elif crossings >= 2:
                state = "MULTIPLE_CROSSINGS"
            else:
                state = "UP_ESCAPE" if group["settled_side"].iloc[index] == "UP" else "DOWN_ESCAPE"
            row = group.iloc[index].to_dict()
            row["state"] = state
            row["future_crossings"] = crossings
            rows.append(row)
    data = pd.DataFrame(rows).sort_values("_ts_ms").reset_index(drop=True)
    if len(data) < 500 or data["state"].nunique() < 2:
        raise ValueError(f"only {len(data)} multi-state path rows")
    features = [column for column in context.protocol.payload["method"]["features"] if column in data]
    split = chronological_four_way_split(data["_ts_ms"], purge_rows=1, **context.split_args)
    x = data[features]
    y = data["state"].astype(str)
    model = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                          LogisticRegression(max_iter=700, class_weight="balanced",
                                             random_state=context.seed))
    model.fit(x.iloc[split.train], y.iloc[split.train])
    prediction = model.predict(x.iloc[split.test])
    majority = y.iloc[split.train].value_counts().idxmax()
    accuracy = float(accuracy_score(y.iloc[split.test], prediction))
    baseline = float(np.mean(y.iloc[split.test] == majority))
    diagnostics = {
        "states": data["state"].value_counts().to_dict(),
        "features": features,
        "untouched_accuracy": accuracy,
        "majority_state": majority,
        "majority_accuracy": baseline,
        "accuracy_lift": accuracy - baseline,
    }
    status = "FAIL_UNSTABLE" if accuracy > baseline else "FAIL_NO_EDGE"
    return EngineResult(status, "Anchor pinning versus escape state classifier", diagnostics,
                        dict(EMPTY_ECONOMICS),
                        ["state classification has not established executable entry economics"],
                        identity, causal, split.boundaries)


def _probability_stickiness(context: EngineContext, frame: pd.DataFrame, identity: dict,
                            causal: dict) -> EngineResult:
    ordered = frame.copy()
    ordered["next_up_mid"] = ordered.groupby(["slug", "horizon"])["up_mid"].shift(-1)
    ordered["next_distance"] = ordered.groupby(["slug", "horizon"])["distance_bps"].shift(-1)
    ordered["btc_shock_bps"] = ordered["next_distance"] - ordered["distance_bps"]
    ordered["probability_change"] = ordered["next_up_mid"] - ordered["up_mid"]
    ordered = ordered.dropna(subset=["up_mid", "next_up_mid", "btc_shock_bps"])
    ordered = ordered[ordered["btc_shock_bps"].abs() >= 1.0]
    ordered["bucket"] = pd.cut(ordered["up_mid"],
                               bins=[0, 0.10, 0.25, 0.75, 0.90, 1.0],
                               labels=["very_low", "moderately_low", "central",
                                       "moderately_high", "very_high"], include_lowest=True)
    rows = {}
    for bucket, group in ordered.groupby("bucket", observed=True):
        elasticity = group["probability_change"] / group["btc_shock_bps"]
        rows[str(bucket)] = {
            "rows": int(len(group)),
            "median_abs_response_per_bps": float(elasticity.abs().median()),
            "same_direction_response_rate": float(np.mean(
                np.sign(group["probability_change"]) == np.sign(group["btc_shock_bps"]))),
            "delayed_or_zero_response_rate": float(np.mean(group["probability_change"].abs() < 0.005)),
        }
    central = rows.get("central", {}).get("median_abs_response_per_bps")
    extremes = [rows.get(name, {}).get("median_abs_response_per_bps")
                for name in ("very_low", "very_high")]
    extremes = [value for value in extremes if value is not None]
    ratio = float(np.mean(extremes) / central) if extremes and central else None
    return EngineResult("FAIL_UNSTABLE", "Probability stickiness near bounded extremes",
                        {"probability_buckets": rows, "extreme_to_central_elasticity_ratio": ratio},
                        dict(EMPTY_ECONOMICS),
                        ["response asymmetry is descriptive and has no locked action rule"],
                        identity, causal)


def _yes_no_asymmetry(context: EngineContext, frame: pd.DataFrame, identity: dict,
                      causal: dict) -> EngineResult:
    data = frame.dropna(subset=["up_ask", "up_bid", "down_ask", "down_bid"]).copy()
    data["up_direct_cost"] = data["up_ask"] + data["up_ask"].map(polymarket_fee_per_share)
    data["up_complement_cost"] = 1.0 - data["down_bid"]
    data["down_direct_cost"] = data["down_ask"] + data["down_ask"].map(polymarket_fee_per_share)
    data["down_complement_cost"] = 1.0 - data["up_bid"]
    data["up_direct_advantage"] = data["up_complement_cost"] - data["up_direct_cost"]
    data["down_direct_advantage"] = data["down_complement_cost"] - data["down_direct_cost"]
    diagnostics = {
        "rows": int(len(data)),
        "up_direct_cheaper_rate": float((data["up_direct_advantage"] > 0).mean()),
        "down_direct_cheaper_rate": float((data["down_direct_advantage"] > 0).mean()),
        "median_up_direct_advantage_cents": float(data["up_direct_advantage"].median() * 100),
        "median_down_direct_advantage_cents": float(data["down_direct_advantage"].median() * 100),
        "up_depth_d1_median": float(data["up_d1"].median()),
        "down_depth_d1_median": float(data["down_d1"].median()),
        "shorting_assumption": "1-opposite_bid is a settlement-equivalent diagnostic, not a guaranteed available short",
    }
    return EngineResult("FAIL_UNSTABLE", "YES/NO settlement-equivalent execution comparison",
                        diagnostics, dict(EMPTY_ECONOMICS),
                        ["complement expression requires inventory/redeem mechanics not proven by this dataset"],
                        identity, causal)


def _liquidity_persistence(context: EngineContext, frame: pd.DataFrame, identity: dict,
                           causal: dict) -> EngineResult:
    ordered = frame.copy()
    for column in ["up_spread", "down_spread", "up_d1", "down_d1"]:
        ordered[f"next_{column}"] = ordered.groupby(["slug", "horizon"])[column].shift(-1)
    complete = ordered.dropna(subset=["next_up_spread", "next_down_spread", "next_up_d1", "next_down_d1"])
    diagnostics = {
        "rows": int(len(complete)),
        "up_spread_persistence": float(complete[["up_spread", "next_up_spread"]].corr().iloc[0, 1]),
        "down_spread_persistence": float(complete[["down_spread", "next_down_spread"]].corr().iloc[0, 1]),
        "up_depth_persistence": float(complete[["up_d1", "next_up_d1"]].corr().iloc[0, 1]),
        "down_depth_persistence": float(complete[["down_d1", "next_down_d1"]].corr().iloc[0, 1]),
        "favored_side_spread_advantage_rate": float(np.mean(np.where(
            complete["current_side"] == "UP",
            complete["up_spread"] <= complete["down_spread"],
            complete["down_spread"] <= complete["up_spread"]))),
    }
    return EngineResult("FAIL_UNSTABLE", "Token liquidity asymmetry persistence",
                        diagnostics, dict(EMPTY_ECONOMICS),
                        ["liquidity persistence improves routing only after a passing alpha exists"],
                        identity, causal)


def _sequential_voi(context: EngineContext, frame: pd.DataFrame, identity: dict,
                    causal: dict) -> EngineResult:
    ordered = frame.copy()
    for side in ("up", "down"):
        ordered[f"next_{side}_ask"] = ordered.groupby(["slug", "horizon"])[f"{side}_ask"].shift(-1)
    ordered["next_seconds_left"] = ordered.groupby(["slug", "horizon"])["seconds_left"].shift(-1)
    # One decision per round at the frozen 60-second checkpoint.
    data = ordered[ordered["checkpoint_s"] == 60].copy()
    data["ask_now"] = np.where(data["current_side"] == "UP", data["up_ask"], data["down_ask"])
    data["ask_wait"] = np.where(data["current_side"] == "UP", data["next_up_ask"], data["next_down_ask"])
    data["won"] = (data["current_side"] == data["settled_side"]).astype(float)
    data["fee_now"] = (data["ask_now"].map(polymarket_fee_per_share) *
                       context.cost_multiplier)
    data["fee_wait"] = (data["ask_wait"].map(polymarket_fee_per_share) *
                        context.cost_multiplier)
    data["pnl_now"] = data["won"] - data["ask_now"] - data["fee_now"]
    data["pnl_wait"] = data["won"] - data["ask_wait"] - data["fee_wait"]
    data["wait_better"] = (data["pnl_wait"] > data["pnl_now"]).astype(int)
    features = [column for column in context.protocol.payload["method"]["features"] if column in data]
    data = data.dropna(subset=["ask_now", "ask_wait", "next_seconds_left", *features]).sort_values(
        "_ts_ms").reset_index(drop=True)
    if len(data) < 500 or data["wait_better"].nunique() < 2:
        raise ValueError(f"only {len(data)} complete 60-second decisions")
    split = chronological_four_way_split(data["_ts_ms"], purge_rows=1, **context.split_args)
    predict = _fit_binary_wait(data, features, split, context.seed)
    p_policy = predict(split.policy)
    thresholds = [0.50, 0.55, 0.60, 0.65, 0.70]
    scores = {threshold: float(np.sum(np.where(
        p_policy >= threshold,
        data["pnl_wait"].to_numpy()[split.policy],
        data["pnl_now"].to_numpy()[split.policy]))) for threshold in thresholds}
    locked = max(scores, key=scores.get)
    p_test = predict(split.test)
    wait = p_test >= locked
    gross = np.where(wait, data["won"].to_numpy()[split.test] - data["ask_wait"].to_numpy()[split.test],
                     data["won"].to_numpy()[split.test] - data["ask_now"].to_numpy()[split.test])
    fees = np.where(wait, data["fee_wait"].to_numpy()[split.test],
                    data["fee_now"].to_numpy()[split.test])
    net = gross - fees
    economics = economic_metrics(
        gross_pnls=gross, net_pnls=net,
        timestamps_ms=data["_ts_ms"].to_numpy(np.int64)[split.test],
        opportunities=len(split.test), turnover=float(len(net)),
        capital_duration_seconds=float(np.where(
            wait, data["next_seconds_left"].to_numpy()[split.test],
            data["seconds_left"].to_numpy()[split.test]).sum()),
        cost_per_action=fees, seed=context.seed)
    status, reasons = economic_verdict(economics, context.protocol.payload["promotion_gates"])
    now_net = data["pnl_now"].to_numpy()[split.test]
    lift = float(net.sum() - now_net.sum())
    if status == "PASS_CANDIDATE" and lift <= 0:
        status, reasons = "FAIL_NO_EDGE", ["waiting policy did not beat always acting now"]
    diagnostics = {
        "locked_wait_threshold": locked,
        "policy_scores": {str(k): v for k, v in scores.items()},
        "untouched_wait_rate": float(wait.mean()),
        "selected_minus_always_now_net_pnl": lift,
        "always_now_net_pnl": float(now_net.sum()),
        "oracle_wait_or_now_net_pnl": float(np.maximum(
            data["pnl_now"].to_numpy()[split.test],
            data["pnl_wait"].to_numpy()[split.test]).sum()),
    }
    return EngineResult(status, "Sequential value of one additional checkpoint",
                        diagnostics, economics, reasons, identity, causal, split.boundaries)


def _data_quality(context: EngineContext, frame: pd.DataFrame, identity: dict,
                  causal: dict) -> EngineResult:
    data = frame.dropna(subset=["checkpoint_age_s", "p_hold_cur"]).copy()
    data["target"] = (data["current_side"] == data["settled_side"]).astype(int)
    data["age_bucket"] = pd.cut(data["checkpoint_age_s"],
                                bins=[-0.001, 0.25, 1, 2, 5, 15, np.inf],
                                labels=["<=250ms", "250ms-1s", "1-2s", "2-5s", "5-15s", ">15s"])
    rows = {}
    for bucket, group in data.groupby("age_bucket", observed=True):
        probability = group["p_hold_cur"].clip(0, 1)
        rows[str(bucket)] = {
            "rows": int(len(group)),
            "mean_age_s": float(group["checkpoint_age_s"].mean()),
            "brier": float(brier_score_loss(group["target"], probability)),
            "accuracy_at_50pct": float(np.mean((probability >= 0.5) == group["target"])),
        }
    return EngineResult("FAIL_UNSTABLE", "Data-quality-conditioned calibration surface",
                        {"checkpoint_age_surface": rows,
                         "unavailable_conditions": ["recorder_restart_proximity", "clock_skew"]},
                        dict(EMPTY_ECONOMICS),
                        ["only checkpoint age is available atomically in the current dataset"],
                        identity, causal)


def run_pm_research(context: EngineContext) -> EngineResult:
    frame, identity, causal = _pm_frame(context)
    mode = str(context.protocol.payload["method"]["mode"])
    functions = {
        "anchor_pinning": _anchor_pinning,
        "probability_stickiness": _probability_stickiness,
        "yes_no_asymmetry": _yes_no_asymmetry,
        "liquidity_persistence": _liquidity_persistence,
        "sequential_voi": _sequential_voi,
        "data_quality": _data_quality,
    }
    if mode not in functions:
        raise ValueError(f"unknown PM-research mode {mode}")
    return functions[mode](context, frame, identity, causal)


def run_pm_l2_research(context: EngineContext) -> EngineResult:
    mode = str(context.protocol.payload["method"]["mode"])
    if mode != "response_decomposition":
        raise ValueError(f"unknown PM-L2 mode {mode}")
    updates = load_contract(context.data_dir, context.protocol.payload["data_contract"],
                            context.maximum_rows)
    trades = load_db_table(
        context.data_dir,
        database="polymarket_l2.duckdb",
        table="pm_l2_trades",
        columns=["asset_id", "aggressor_side", "price", "size", "fee_rate_bps"],
        timestamp="recv_ts_ns",
        maximum_rows=context.maximum_rows,
    )
    frame = updates.frame.copy()
    frame["previous_size"] = pd.to_numeric(frame["previous_size"], errors="coerce")
    frame["new_size"] = pd.to_numeric(frame["new_size"], errors="coerce")
    frame["delta_size"] = frame["new_size"] - frame["previous_size"]
    frame["side"] = frame["side"].astype(str).str.upper()
    withdrawal = frame[frame["delta_size"] < 0]
    addition = frame[frame["delta_size"] > 0]
    trade_frame = trades.frame.copy()
    trade_frame["size"] = pd.to_numeric(trade_frame["size"], errors="coerce").fillna(0)
    diagnostics = {
        "level_updates": int(len(frame)),
        "withdrawal_events": int(len(withdrawal)),
        "new_liquidity_events": int(len(addition)),
        "withdrawn_size": float(-withdrawal["delta_size"].sum()),
        "added_size": float(addition["delta_size"].sum()),
        "aggressive_trades": int(len(trade_frame)),
        "aggressive_trade_size": float(trade_frame["size"].sum()),
        "withdrawal_share_of_book_events": float(len(withdrawal) / max(1, len(frame))),
        "note": "mechanical complement adjustment is not identifiable without an atomic paired-token event join",
    }
    causal_summary = {"updates": updates.causal_summary, "trades": trades.causal_summary}
    identity = {"updates": updates.identity, "trades": trades.identity}
    return EngineResult("FAIL_UNSTABLE", "Polymarket response-component accounting",
                        diagnostics, dict(EMPTY_ECONOMICS),
                        ["decomposition is descriptive; paired-token mechanical adjustment remains unobserved"],
                        identity, causal_summary)
