"""BTC matrix, transportability, feed-ablation and cross-venue engines."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .causal_loader import load_source
from .cost_model import BinanceCost
from .engine_types import EngineContext, EngineResult
from .matched_controls import matched_nonoverlapping_random_actions
from .metrics import EMPTY_ECONOMICS, economic_metrics, economic_verdict
from .modeling import (discriminator_auc, fit_locked_binary_policy,
                       score_locked_binary_policy)
from .temporal_split import chronological_four_way_split


def _numeric_clean(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.replace([np.inf, -np.inf], np.nan).dropna(subset=columns).reset_index(drop=True)


def _split(context: EngineContext, frame: pd.DataFrame, purge_rows: int):
    return chronological_four_way_split(frame["_ts_ms"], purge_rows=purge_rows,
                                        **context.split_args)


def _non_overlapping_actions(actions: np.ndarray, timestamps_ms: np.ndarray,
                             holding_seconds: int) -> np.ndarray:
    """Keep the first signal until its declared capital-holding interval ends."""
    result = np.asarray(actions).copy()
    next_free_ms = -1
    for index, timestamp in enumerate(np.asarray(timestamps_ms, dtype=np.int64)):
        if result[index] == 0:
            continue
        if timestamp < next_free_ms:
            result[index] = 0
            continue
        next_free_ms = int(timestamp) + int(holding_seconds) * 1000
    return result


def _weighted_interval_ceiling(values: np.ndarray, timestamps_ms: np.ndarray,
                               holding_seconds: int) -> tuple[float, int]:
    """Exact maximum value of non-overlapping fixed-duration opportunities."""
    value = np.maximum(0.0, np.asarray(values, dtype=float))
    ts = np.asarray(timestamps_ms, dtype=np.int64)
    n = len(value)
    best = np.zeros(n + 1, dtype=float)
    count = np.zeros(n + 1, dtype=np.int64)
    duration_ms = int(holding_seconds) * 1000
    for i in range(1, n + 1):
        previous = int(np.searchsorted(ts, ts[i - 1] - duration_ms, side="right"))
        take = value[i - 1] + best[previous]
        if take > best[i - 1]:
            best[i] = take
            count[i] = count[previous] + int(value[i - 1] > 0)
        else:
            best[i] = best[i - 1]
            count[i] = count[i - 1]
    return float(best[-1]), int(count[-1])


def _binary_economics(
    context: EngineContext,
    frame: pd.DataFrame,
    actions: np.ndarray,
    indices: np.ndarray,
    return_column: str,
    *,
    hold_seconds: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    returns = frame[return_column].to_numpy(float)[indices]
    interval_timestamps = frame["_ts_ms"].to_numpy(np.int64)[indices]
    actions = _non_overlapping_actions(actions, interval_timestamps, hold_seconds)
    active = actions != 0
    action = actions[active].astype(float)
    gross = returns[active] * action
    cost = BinanceCost().round_trip_bps(context.cost_multiplier) / 10_000.0
    costs = np.full(len(gross), cost, dtype=float)
    net = gross - costs
    timestamps = interval_timestamps[active]
    metrics = economic_metrics(
        gross_pnls=gross,
        net_pnls=net,
        timestamps_ms=timestamps,
        opportunities=len(indices),
        turnover=float(2 * len(net)),
        capital_duration_seconds=float(len(net) * hold_seconds),
        cost_per_action=costs,
        seed=context.seed,
    )
    random_actions = matched_nonoverlapping_random_actions(
        actions, timestamps_ms=interval_timestamps, holding_seconds=hold_seconds,
        seed=context.seed + 41)
    random_active = random_actions != 0
    random_gross = returns[random_active] * random_actions[random_active]
    random_net = random_gross - cost
    control = {
        "name": "matched_random_same_day_action_count_and_sign_balance",
        "actions": int(random_active.sum()),
        "gross_pnl": float(random_gross.sum()),
        "net_pnl": float(random_net.sum()),
        "candidate_minus_control_net_pnl": float(net.sum() - random_net.sum()),
    }
    return metrics, control


def _matrix_frame(context: EngineContext) -> tuple[pd.DataFrame, dict, dict, list[str], str]:
    payload = context.protocol.payload
    contract = payload["data_contract"]
    loaded = load_source(context.data_dir, contract, maximum_rows=context.maximum_rows)
    method = payload["method"]
    features = list(method["features"])
    target = str(method.get("target", "future_direction_5m"))
    return_column = str(method.get("return_column", "ret_5m"))
    columns = [*features, target, return_column, "_ts_ms"]
    if str(method.get("return_unit", "fraction")).lower() == "usd":
        columns.append("close")
    frame = _numeric_clean(loaded.frame, columns)
    if len(frame) < int(payload["promotion_gates"].get("minimum_rows", 500)):
        raise ValueError(f"only {len(frame)} complete rows")
    frame[target] = (frame[target] > 0.5).astype(int)
    if str(method.get("return_unit", "fraction")).lower() == "usd":
        frame["_economic_return"] = frame[return_column] / frame["close"]
        return_column = "_economic_return"
    return frame, loaded.identity, loaded.causal_summary, features, return_column


def run_btc_signal(context: EngineContext) -> EngineResult:
    frame, identity, causal, features, return_column = _matrix_frame(context)
    method = context.protocol.payload["method"]
    target = str(method.get("target", "future_direction_5m"))
    hold_seconds = int(method.get("holding_seconds", 300))
    split = _split(context, frame, int(method.get("purge_rows", 5)))
    returns = frame[return_column].to_numpy(float)
    cost = BinanceCost().round_trip_bps(context.cost_multiplier) / 10_000.0

    def policy_scorer(actions: np.ndarray, indices: np.ndarray) -> float:
        actions = _non_overlapping_actions(actions,
                                           frame["_ts_ms"].to_numpy(np.int64)[indices],
                                           hold_seconds)
        active = actions != 0
        return float(np.sum(returns[indices][active] * actions[active] - cost))

    locked, selection = fit_locked_binary_policy(
        frame, features=features, target=target, split=split,
        thresholds=list(map(float, method.get("thresholds", [0.55, 0.60, 0.65]))),
        policy_scorer=policy_scorer, seed=context.seed,
    )
    scored = score_locked_binary_policy(locked, frame, target, split.test)
    economics, control = _binary_economics(context, frame, scored["actions"], split.test,
                                            return_column, hold_seconds=hold_seconds)
    status, reasons = economic_verdict(economics, context.protocol.payload["promotion_gates"])
    if status == "PASS_CANDIDATE" and control["candidate_minus_control_net_pnl"] <= 0:
        status = "FAIL_UNSTABLE"
        reasons.append("candidate did not beat the matched-random control")
    diagnostics = {
        "locked_model": locked.model_name,
        "locked_threshold": locked.threshold,
        "model_selection": selection,
        "untouched_test_probability": scored["metrics"],
        "control": control,
        "feature_count": len(features),
        "features": features,
    }
    return EngineResult(status, "Locked BTC classifier evaluated after full costs", diagnostics,
                        economics, reasons, identity, causal, split.boundaries)


def run_alpha_upper_bound(context: EngineContext) -> EngineResult:
    baseline = run_btc_signal(context)
    loaded = load_source(context.data_dir, context.protocol.payload["data_contract"],
                         maximum_rows=context.maximum_rows)
    frame = _numeric_clean(loaded.frame, ["_ts_ms", "ret_5m", "close"])
    frame["_economic_return"] = frame["ret_5m"] / frame["close"]
    split = _split(context, frame, 5)
    returns = frame["_economic_return"].to_numpy(float)[split.test]
    cost = BinanceCost().round_trip_bps(context.cost_multiplier) / 10_000.0
    oracle = np.maximum(np.abs(returns) - cost, 0.0)
    oracle_total, oracle_actions = _weighted_interval_ceiling(
        oracle, frame["_ts_ms"].to_numpy(np.int64)[split.test], 300)
    oracle_metrics = {
        "constrained_oracle_actions": oracle_actions,
        "constrained_oracle_net_pnl_per_unit": oracle_total,
        "constrained_oracle_mean_pnl": float(oracle.mean()),
        "model_regret_to_constrained_oracle": float(
            oracle_total - float(baseline.economics.get("net_pnl") or 0.0)),
        "note": "Ex-post weighted-interval oracle enforces one open 5m position; it is a ceiling, not a deployable strategy.",
    }
    baseline.diagnostics["extractability_ceiling"] = oracle_metrics
    if oracle_metrics["constrained_oracle_net_pnl_per_unit"] <= 0:
        baseline.status = "FAIL_AFTER_COSTS"
        baseline.reasons.append("even the constrained ex-post oracle is non-positive")
    elif baseline.status == "INSUFFICIENT_SAMPLE":
        baseline.status = "FAIL_NO_EDGE"
        baseline.reasons = ["constrained oracle has opportunity, but the locked models extracted no test actions"]
    baseline.summary = "Constrained oracle ceiling plus locked classifier regret"
    return baseline


def run_history_transport(context: EngineContext) -> EngineResult:
    loaded = load_source(context.data_dir, context.protocol.payload["data_contract"],
                         maximum_rows=context.maximum_rows)
    features = list(context.protocol.payload["method"]["features"])
    frame = _numeric_clean(loaded.frame, ["_ts_ms", *features])
    n = len(frame)
    if n < 1_000:
        return EngineResult("INSUFFICIENT_SAMPLE", "Too few rows for domain discrimination", {},
                            dict(EMPTY_ECONOMICS), [f"only {n} complete rows"], loaded.identity,
                            loaded.causal_summary)
    boundary = int(n * 0.70)
    # Interleave within each domain for a classifier diagnostic; labels never become alpha labels.
    label = np.zeros(n, dtype=int)
    label[boundary:] = 1
    history_idx = np.arange(boundary)
    live_idx = np.arange(boundary, n)
    train_idx = np.r_[history_idx[::2], live_idx[::2]]
    test_idx = np.r_[history_idx[1::2], live_idx[1::2]]
    ordered = np.r_[train_idx, test_idx]
    arranged = frame.iloc[ordered].reset_index(drop=True)
    arranged_label = label[ordered]
    result = discriminator_auc(arranged, features, arranged_label, len(train_idx), context.seed)
    auc = result["auc"]
    if auc is None:
        status, reasons = "INSUFFICIENT_SAMPLE", ["both domains were not represented"]
    elif auc >= 0.65:
        status, reasons = "FAIL_UNSTABLE", ["history/live discriminator is materially above chance"]
    else:
        status, reasons = "PASS_CANDIDATE", ["archive is not easily separable from the recent domain"]
    diagnostics = {"history_live_discriminator": result, "live_fraction": 0.30,
                   "interpretation": "High AUC is evidence against transportability, not alpha."}
    return EngineResult(status, "History-versus-recent domain discriminator", diagnostics,
                        dict(EMPTY_ECONOMICS), reasons, loaded.identity, loaded.causal_summary)


def run_feed_ablation(context: EngineContext) -> EngineResult:
    result = run_btc_signal(context)
    # The untouched economic result remains the primary verdict. Every ablation is independently
    # locked on TRAIN/CALIBRATION/POLICY before it touches the same final period.
    groups = context.protocol.payload["method"].get("feature_groups", {})
    frame, _, _, all_features, return_column = _matrix_frame(context)
    method = context.protocol.payload["method"]
    target = str(method.get("target", "future_direction_5m"))
    split = _split(context, frame, int(method.get("purge_rows", 5)))
    returns = frame[return_column].to_numpy(float)
    cost = BinanceCost().round_trip_bps(context.cost_multiplier) / 10_000.0
    hold_seconds = int(method.get("holding_seconds", 300))

    def ablation_scorer(actions: np.ndarray, indices: np.ndarray) -> float:
        locked_actions = _non_overlapping_actions(
            actions, frame["_ts_ms"].to_numpy(np.int64)[indices], hold_seconds)
        active = locked_actions != 0
        return float(np.sum(returns[indices][active] * locked_actions[active] - cost))

    ablations = {}
    for group, removed in groups.items():
        features = [feature for feature in all_features if feature not in set(removed)]
        if not features:
            continue
        locked, _ = fit_locked_binary_policy(
            frame, features=features, target=target, split=split,
            thresholds=list(map(float, method.get("thresholds", [0.55, 0.60, 0.65]))),
            policy_scorer=ablation_scorer,
            seed=context.seed,
        )
        scored = score_locked_binary_policy(locked, frame, target, split.test)
        test_actions = _non_overlapping_actions(
            scored["actions"], frame["_ts_ms"].to_numpy(np.int64)[split.test], hold_seconds)
        active = test_actions != 0
        pnl = returns[split.test][active] * test_actions[active] - cost
        ablations[group] = {
            "removed_features": removed,
            "locked_model": locked.model_name,
            "locked_threshold": locked.threshold,
            "test_actions": int(active.sum()),
            "test_net_pnl": float(pnl.sum()),
            "incremental_net_pnl_of_source_group": float(
                (result.economics.get("net_pnl") or 0.0) - pnl.sum()),
            "test_brier": scored["metrics"]["brier"],
        }
    result.diagnostics["declared_source_groups"] = groups
    result.diagnostics["leave_one_source_group_out"] = ablations
    return result


def run_signal_context(context: EngineContext) -> EngineResult:
    result = run_btc_signal(context)
    frame, _, _, _, return_column = _matrix_frame(context)
    method = context.protocol.payload["method"]
    split = _split(context, frame, int(method.get("purge_rows", 5)))
    train = frame.iloc[split.train]
    test = frame.iloc[split.test].copy()
    signal = np.sign(test["cvd_change"].to_numpy(float))
    returns = test[return_column].to_numpy(float)
    cost = BinanceCost().round_trip_bps(context.cost_multiplier) / 10_000.0
    vol_cut = float(train["rv_15m"].median())
    hour = pd.to_datetime(test["_ts_ms"], unit="ms", utc=True).dt.hour.to_numpy()
    contexts = {
        "low_volatility": test["rv_15m"].to_numpy(float) <= vol_cut,
        "high_volatility": test["rv_15m"].to_numpy(float) > vol_cut,
        "basis_positive": test["perp_spot_basis_bps"].to_numpy(float) > 0,
        "basis_nonpositive": test["perp_spot_basis_bps"].to_numpy(float) <= 0,
        "utc_00_08": hour < 8,
        "utc_08_16": (hour >= 8) & (hour < 16),
        "utc_16_24": hour >= 16,
    }
    rows = {}
    for name, mask in contexts.items():
        context_actions = _non_overlapping_actions(
            np.where(mask, signal, 0), test["_ts_ms"].to_numpy(np.int64),
            int(method.get("holding_seconds", 300)))
        active = context_actions != 0
        pnl = returns[active] * context_actions[active] - cost
        inverse = -returns[active] * context_actions[active] - cost
        rows[name] = {
            "actions": int(active.sum()),
            "follow_signal_net_pnl": float(pnl.sum()),
            "reverse_signal_net_pnl": float(inverse.sum()),
            "preferred_sign": ("FOLLOW" if pnl.sum() > inverse.sum() else "REVERSE"),
        }
    result.diagnostics["frozen_context_signal_economics"] = rows
    result.diagnostics["context_warning"] = (
        "Context rows are diagnostics on the untouched period; they are not thresholds to tune "
        "after observing this report. A later protocol must freeze any chosen context."
    )
    result.summary = "Locked model plus fixed-context CVD sign-reversal diagnostics"
    return result


def run_magnitude_diagnostic(context: EngineContext) -> EngineResult:
    loaded = load_source(context.data_dir, context.protocol.payload["data_contract"],
                         maximum_rows=context.maximum_rows)
    method = context.protocol.payload["method"]
    features = list(method["features"])
    magnitude = str(method.get("magnitude_column", "future_abs_move_5m"))
    frame = _numeric_clean(loaded.frame, ["_ts_ms", magnitude, *features])
    split = _split(context, frame, int(method.get("purge_rows", 5)))
    threshold = float(np.quantile(frame[magnitude].to_numpy(float)[split.train],
                                  float(method.get("training_quantile", 0.75))))
    frame["_large_move"] = (frame[magnitude] >= threshold).astype(int)
    locked, selection = fit_locked_binary_policy(
        frame, features=features, target="_large_move", split=split,
        thresholds=[0.55, 0.60, 0.65],
        policy_scorer=lambda actions, idx: float(np.mean((actions == 1) == (frame["_large_move"].to_numpy()[idx] == 1))),
        seed=context.seed,
    )
    scored = score_locked_binary_policy(locked, frame, "_large_move", split.test)
    diagnostics = {"locked_threshold_value": threshold, "locked_model": locked.model_name,
                   "model_selection": selection, "untouched_test": scored["metrics"]}
    return EngineResult(
        "BLOCKED_DATA",
        "Magnitude predictability measured; executable magnitude instrument is not in this dataset",
        diagnostics, dict(EMPTY_ECONOMICS),
        ["a magnitude forecast alone does not define executable PnL"],
        loaded.identity, loaded.causal_summary, split.boundaries,
    )


def run_crossvenue(context: EngineContext) -> EngineResult:
    loaded = load_source(context.data_dir, context.protocol.payload["data_contract"],
                         maximum_rows=context.maximum_rows)
    features = list(context.protocol.payload["method"]["features"])
    frame = _numeric_clean(loaded.frame, ["_ts_ms", *features, "binance"])
    frame["_future_return"] = frame["binance"].shift(-1) / frame["binance"] - 1.0
    frame["_target"] = (frame["_future_return"] > 0).astype(int)
    frame = frame.dropna(subset=["_future_return"]).reset_index(drop=True)
    # Reuse the locked BTC engine by supplying the derived in-memory frame directly.
    split = _split(context, frame, 1)
    cost = BinanceCost().round_trip_bps(context.cost_multiplier) / 10_000.0
    returns = frame["_future_return"].to_numpy(float)
    locked, selection = fit_locked_binary_policy(
        frame, features=features, target="_target", split=split,
        thresholds=[0.55, 0.60, 0.65],
        policy_scorer=lambda actions, idx: float(np.sum(
            returns[idx][actions != 0] * actions[actions != 0] - cost)), seed=context.seed)
    scored = score_locked_binary_policy(locked, frame, "_target", split.test)
    economics, control = _binary_economics(context, frame, scored["actions"], split.test,
                                            "_future_return", hold_seconds=1)
    status, reasons = economic_verdict(economics, context.protocol.payload["promotion_gates"])
    if status == "PASS_CANDIDATE" and control["candidate_minus_control_net_pnl"] <= 0:
        status, reasons = "FAIL_UNSTABLE", ["does not beat matched dislocations"]
    return EngineResult(status, "Cross-venue lag/lead classifier at executable cost proxy",
                        {"model_selection": selection, "untouched_test": scored["metrics"],
                         "control": control}, economics, reasons, loaded.identity,
                        loaded.causal_summary, split.boundaries)


def run_readiness(context: EngineContext) -> EngineResult:
    payload = context.protocol.payload
    requirements = list(payload["method"].get("required_artifacts", []))
    missing = [item for item in requirements if not (context.data_dir.parent / item).exists()
               and not (context.data_dir / item).exists()]
    reason = str(payload["method"].get("blocked_reason") or "required causal dataset is unavailable")
    diagnostics = {"required_artifacts": requirements, "missing_artifacts": missing}
    return EngineResult("BLOCKED_DATA", "Prerequisite audit", diagnostics,
                        dict(EMPTY_ECONOMICS), [reason], {}, {})
