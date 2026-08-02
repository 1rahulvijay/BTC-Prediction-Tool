"""Event-flow, L2, Polymarket and forward-ledger engines."""
from __future__ import annotations

from typing import Any

import duckdb
import numpy as np
import pandas as pd

from .causal_loader import DataUnavailable, SchemaUnavailable, load_source
from .cost_model import polymarket_fee_per_share
from .engine_types import EngineContext, EngineResult
from .matched_controls import (matched_nonoverlapping_random_actions,
                               matched_random_actions)
from .metrics import EMPTY_ECONOMICS, economic_metrics, economic_verdict
from .modeling import fit_locked_binary_policy, score_locked_binary_policy
from .temporal_split import chronological_four_way_split


def _numeric(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    result = frame.copy()
    for column in columns:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    return result.replace([np.inf, -np.inf], np.nan).dropna(subset=columns).reset_index(drop=True)


def _non_overlapping(actions: np.ndarray, timestamps_ms: np.ndarray,
                     holding_seconds: int) -> np.ndarray:
    result = np.asarray(actions).copy()
    next_free = -1
    for index, timestamp in enumerate(np.asarray(timestamps_ms, dtype=np.int64)):
        if result[index] == 0:
            continue
        if timestamp < next_free:
            result[index] = 0
        else:
            next_free = int(timestamp) + int(holding_seconds) * 1000
    return result


def _event_seconds(frame: pd.DataFrame) -> pd.DataFrame:
    data = frame.copy()
    data["_second"] = data["_ts_ms"] // 1000
    data["_price"] = pd.to_numeric(data.get("price"), errors="coerce")
    data["_size"] = pd.to_numeric(data.get("size"), errors="coerce").fillna(0.0)
    side = data.get("side", pd.Series("", index=data.index)).astype(str).str.upper()
    data["_signed_size"] = np.where(side.isin(["BUY", "BID", "B"]), data["_size"],
                                    np.where(side.isin(["SELL", "ASK", "S"]), -data["_size"], 0.0))
    grouped = data.groupby("_second", sort=True).agg(
        _ts_ms=("_ts_ms", "max"),
        event_count=("_second", "size"),
        volume=("_size", "sum"),
        signed_flow=("_signed_size", "sum"),
        price=("_price", "last"),
    ).reset_index(drop=True)
    grouped["price"] = grouped["price"].ffill()
    grouped["flow_5s"] = grouped["signed_flow"].rolling(5, min_periods=1).sum()
    grouped["flow_15s"] = grouped["signed_flow"].rolling(15, min_periods=1).sum()
    grouped["count_5s"] = grouped["event_count"].rolling(5, min_periods=1).sum()
    grouped["impact_efficiency"] = grouped["price"].pct_change().abs() / (grouped["volume"] + 1e-9)
    grouped["reaction_core_ratio"] = grouped["flow_5s"].abs() / (
        (grouped["flow_15s"] - grouped["flow_5s"]).abs() + 1e-9)
    grouped["impact_efficiency_change"] = grouped["impact_efficiency"].diff()
    grouped["future_return"] = grouped["price"].shift(-5) / grouped["price"] - 1.0
    grouped["target"] = (grouped["future_return"] > 0).astype(int)
    return grouped.dropna(subset=["price", "future_return"]).reset_index(drop=True)


def run_event_flow(context: EngineContext) -> EngineResult:
    loaded = load_source(context.data_dir, context.protocol.payload["data_contract"],
                         maximum_rows=context.maximum_rows)
    seconds = _event_seconds(loaded.frame)
    if len(seconds) < int(context.protocol.payload["promotion_gates"].get("minimum_rows", 500)):
        return EngineResult("INSUFFICIENT_SAMPLE", "Too few event-time seconds", {},
                            dict(EMPTY_ECONOMICS), [f"only {len(seconds)} aggregated seconds"],
                            loaded.identity, loaded.causal_summary)
    mode = context.protocol.payload["method"].get("mode", "continuation")
    features = ["event_count", "volume", "signed_flow", "flow_5s", "flow_15s",
                "count_5s", "impact_efficiency", "reaction_core_ratio",
                "impact_efficiency_change"]
    seconds = seconds.replace([np.inf, -np.inf], np.nan).dropna(subset=features).reset_index(drop=True)
    split = chronological_four_way_split(seconds["_ts_ms"], purge_rows=5, **context.split_args)
    if mode == "segmentation":
        sign = np.sign(seconds["signed_flow"].to_numpy(float))
        changes = np.r_[True, sign[1:] != sign[:-1]]
        segment = np.cumsum(changes)
        sizes = pd.Series(segment).value_counts().to_numpy()
        observed = float(np.mean(sizes)) if len(sizes) else 0.0
        rng = np.random.default_rng(context.seed)
        random_sign = rng.permutation(sign)
        random_segments = np.cumsum(np.r_[True, random_sign[1:] != random_sign[:-1]])
        baseline = float(pd.Series(random_segments).value_counts().mean())
        span_days = ((int(seconds["_ts_ms"].max()) - int(seconds["_ts_ms"].min()))
                     / 86_400_000.0)
        enough_span = span_days >= 5.0
        status = ("PASS_CANDIDATE" if enough_span and observed > baseline * 1.10
                  and len(sizes) >= 100 else
                  "INSUFFICIENT_SAMPLE" if not enough_span else "FAIL_NO_EDGE")
        return EngineResult(status, "Deterministic signed-run segmentation versus shuffled runs",
                            {"segments": int(len(sizes)), "mean_segment_seconds": observed,
                             "shuffled_mean_segment_seconds": baseline,
                             "observed_span_days": span_days,
                             "post_segment_profit_claim": False}, dict(EMPTY_ECONOMICS),
                            ([] if status == "PASS_CANDIDATE" else
                             ["fewer than five observed days"] if not enough_span else
                             ["segments lack stable persistence lift"]),
                            loaded.identity, loaded.causal_summary, split.boundaries)
    flow_side = np.sign(seconds["flow_5s"].to_numpy(float))
    flow_side[flow_side == 0] = 1.0
    base_side = -flow_side if mode in {"exhaustion", "overshoot", "impact_decay"} else flow_side
    seconds["trade_return"] = seconds["future_return"].to_numpy(float) * base_side
    seconds["target"] = (seconds["trade_return"] > 0).astype(int)
    cost = 9.0 * context.cost_multiplier / 10_000.0
    returns = seconds["trade_return"].to_numpy(float)

    def event_policy_scorer(actions: np.ndarray, indices: np.ndarray) -> float:
        locked_actions = _non_overlapping(
            actions, seconds["_ts_ms"].to_numpy(np.int64)[indices], 5)
        active_actions = locked_actions != 0
        return float(np.sum(returns[indices][active_actions]
                            * locked_actions[active_actions] - cost))

    locked, selection = fit_locked_binary_policy(
        seconds, features=features, target="target", split=split,
        thresholds=[0.55, 0.60, 0.65],
        policy_scorer=event_policy_scorer, seed=context.seed)
    scored = score_locked_binary_policy(locked, seconds, "target", split.test)
    if mode == "event_language":
        return EngineResult(
            "BLOCKED_DATA", "Self-supervised next-event surprise diagnostic",
            {"model_selection": selection, "untouched_test": scored["metrics"],
             "surprise_definition": "-log(P(observed next direction))"},
            dict(EMPTY_ECONOMICS),
            ["this protocol explicitly does not trade; economic promotion is unavailable"],
            loaded.identity, loaded.causal_summary, split.boundaries)
    test_actions = _non_overlapping(
        scored["actions"], seconds["_ts_ms"].to_numpy(np.int64)[split.test], 5)
    active = test_actions != 0
    actions = test_actions[active]
    gross = returns[split.test][active] * actions
    costs = np.full(len(gross), cost)
    net = gross - costs
    ts = seconds["_ts_ms"].to_numpy(np.int64)[split.test][active]
    economics = economic_metrics(gross_pnls=gross, net_pnls=net, timestamps_ms=ts,
                                  opportunities=len(split.test), turnover=2 * len(net),
                                  capital_duration_seconds=5 * len(net), cost_per_action=costs,
                                  seed=context.seed)
    status, reasons = economic_verdict(economics, context.protocol.payload["promotion_gates"])
    random_actions = matched_nonoverlapping_random_actions(
        test_actions, timestamps_ms=seconds["_ts_ms"].to_numpy(np.int64)[split.test],
        holding_seconds=5, seed=context.seed + 73)
    random_active = random_actions != 0
    random_net = (returns[split.test][random_active] * random_actions[random_active] - cost)
    control = {"actions": int(random_active.sum()), "net_pnl": float(random_net.sum()),
               "candidate_minus_control_net_pnl": float(net.sum() - random_net.sum())}
    if status == "PASS_CANDIDATE" and control["candidate_minus_control_net_pnl"] <= 0:
        status = "FAIL_UNSTABLE"
        reasons.append("event policy does not beat matched random event times")
    return EngineResult(status, f"Event-time {mode} classifier", {"model_selection": selection,
                        "untouched_test": scored["metrics"],
                        "positive_action_meaning": ("fade initiating flow" if mode in {
                            "exhaustion", "overshoot", "impact_decay"} else "follow initiating flow"),
                        "control": control}, economics, reasons,
                        loaded.identity, loaded.causal_summary, split.boundaries)


def run_l2_hazard(context: EngineContext) -> EngineResult:
    loaded = load_source(context.data_dir, context.protocol.payload["data_contract"],
                         maximum_rows=context.maximum_rows)
    required = ["_ts_ms", "spread_bps", "obi_20", "obi_near", "bid_usd", "ask_usd",
                "depth_slope", "ofi"]
    frame = _numeric(loaded.frame, required)
    frame["future_spread"] = frame["spread_bps"].shift(-5)
    frame["target"] = (frame["future_spread"] >= np.maximum(2 * frame["spread_bps"], 1.0)).astype(int)
    frame = frame.dropna(subset=["future_spread"]).reset_index(drop=True)
    split = chronological_four_way_split(frame["_ts_ms"], purge_rows=5, **context.split_args)
    features = required[2:]
    locked, selection = fit_locked_binary_policy(
        frame, features=features, target="target", split=split, thresholds=[0.55, 0.65, 0.75],
        policy_scorer=lambda actions, idx: float(np.mean((actions == 1) == (frame["target"].to_numpy()[idx] == 1))),
        seed=context.seed)
    scored = score_locked_binary_policy(locked, frame, "target", split.test)
    return EngineResult("BLOCKED_DATA", "Liquidity-withdrawal hazard diagnostic",
                        {"model_selection": selection, "untouched_test": scored["metrics"]},
                        dict(EMPTY_ECONOMICS),
                        ["PnL improvement requires an unchanged base strategy and causal veto replay"],
                        loaded.identity, loaded.causal_summary, split.boundaries)


def _pm_checkpoint_frame(context: EngineContext) -> tuple[pd.DataFrame, dict, dict]:
    loaded = load_source(context.data_dir, context.protocol.payload["data_contract"],
                         maximum_rows=context.maximum_rows)
    frame = loaded.frame.copy()
    frame = frame[pd.Series(frame["eligible"]).fillna(False).astype(bool)].copy()
    checkpoint = context.protocol.payload["method"].get("checkpoint_s")
    if checkpoint is not None:
        frame = frame[pd.to_numeric(frame["checkpoint_s"]) == int(checkpoint)].copy()
    frame = frame.sort_values(["snapshot_ts", "slug"]).drop_duplicates(
        ["slug", "horizon", "checkpoint_s"], keep="last").reset_index(drop=True)
    return frame, loaded.identity, loaded.causal_summary


def run_pm_settlement(context: EngineContext) -> EngineResult:
    frame, identity, causal = _pm_checkpoint_frame(context)
    features = list(context.protocol.payload["method"]["features"])
    needed = ["_ts_ms", "up_win", "up_ask", "down_ask", "seconds_left", *features]
    frame = _numeric(frame, needed)
    if len(frame) < int(context.protocol.payload["promotion_gates"].get("minimum_rows", 200)):
        return EngineResult("INSUFFICIENT_SAMPLE", "Too few causal settled checkpoints", {},
                            dict(EMPTY_ECONOMICS), [f"only {len(frame)} eligible checkpoints"],
                            identity, causal)
    frame["target"] = frame["up_win"].astype(int)
    split = chronological_four_way_split(frame["_ts_ms"], purge_rows=1, **context.split_args)

    def policy_value(probability: np.ndarray, indices: np.ndarray, threshold: float) -> float:
        target = frame["target"].to_numpy(int)[indices]
        up_ask = frame["up_ask"].to_numpy(float)[indices]
        down_ask = frame["down_ask"].to_numpy(float)[indices]
        side = np.where(probability >= threshold, 1,
                        np.where(probability <= 1.0 - threshold, -1, 0))
        active = side != 0
        asks = np.where(side[active] == 1, up_ask[active], down_ask[active])
        won = np.where(side[active] == 1, target[active] == 1, target[active] == 0)
        fees = np.array([polymarket_fee_per_share(value) for value in asks]) * context.cost_multiplier
        return float(np.sum(won.astype(float) - asks - fees))

    # The common model scorer supplies directional actions; reconstruct calibrated probabilities
    # during policy selection through the action score using settlement outcomes.
    target_values = frame["target"].to_numpy(int)
    asks_up = frame["up_ask"].to_numpy(float)
    asks_down = frame["down_ask"].to_numpy(float)

    def action_scorer(actions: np.ndarray, indices: np.ndarray) -> float:
        active = actions != 0
        side = actions[active]
        target = target_values[indices][active]
        asks = np.where(side == 1, asks_up[indices][active], asks_down[indices][active])
        won = np.where(side == 1, target == 1, target == 0)
        fees = np.array([polymarket_fee_per_share(value) for value in asks]) * context.cost_multiplier
        return float(np.sum(won.astype(float) - asks - fees))

    locked, selection = fit_locked_binary_policy(
        frame, features=features, target="target", split=split,
        thresholds=list(map(float, context.protocol.payload["method"].get("thresholds", [0.55, 0.65, 0.75]))),
        policy_scorer=action_scorer, seed=context.seed)
    scored = score_locked_binary_policy(locked, frame, "target", split.test)
    actions = scored["actions"]
    active = actions != 0
    side = actions[active]
    idx = split.test[active]
    target = target_values[idx]
    asks = np.where(side == 1, asks_up[idx], asks_down[idx])
    won = np.where(side == 1, target == 1, target == 0)
    fees = np.array([polymarket_fee_per_share(value) for value in asks]) * context.cost_multiplier
    gross = won.astype(float) - asks
    net = gross - fees
    economics = economic_metrics(
        gross_pnls=gross, net_pnls=net, timestamps_ms=frame["_ts_ms"].to_numpy(np.int64)[idx],
        opportunities=len(split.test), turnover=float(len(net)),
        capital_duration_seconds=float(frame["seconds_left"].to_numpy(float)[idx].sum()),
        cost_per_action=fees, seed=context.seed)
    random_actions = matched_random_actions(actions,
                                            timestamps_ms=frame["_ts_ms"].to_numpy(np.int64)[split.test],
                                            seed=context.seed + 19)
    random_active = random_actions != 0
    ridx = split.test[random_active]
    rside = random_actions[random_active]
    rasks = np.where(rside == 1, asks_up[ridx], asks_down[ridx])
    rwon = np.where(rside == 1, target_values[ridx] == 1, target_values[ridx] == 0)
    rfees = np.array([polymarket_fee_per_share(value) for value in rasks]) * context.cost_multiplier
    random_net = rwon.astype(float) - rasks - rfees
    control = {"actions": int(len(random_net)), "net_pnl": float(random_net.sum()),
               "candidate_minus_control_net_pnl": float(net.sum() - random_net.sum())}
    status, reasons = economic_verdict(economics, context.protocol.payload["promotion_gates"])
    if status == "PASS_CANDIDATE" and control["candidate_minus_control_net_pnl"] <= 0:
        status, reasons = "FAIL_UNSTABLE", ["does not beat matched random sides"]
    return EngineResult(status, "Causal Polymarket settlement classifier using executable asks",
                        {"model_selection": selection, "untouched_test": scored["metrics"],
                         "control": control}, economics, reasons, identity, causal,
                        split.boundaries)


def _build_pm_dynamic(frame: pd.DataFrame) -> pd.DataFrame:
    ordered = frame.sort_values(["slug", "horizon", "snapshot_ts"]).copy()
    rows: list[pd.DataFrame] = []
    for side in ("up", "down"):
        result = ordered.copy()
        result["side_sign"] = 1.0 if side == "up" else -1.0
        result["ask"] = pd.to_numeric(result[f"{side}_ask"], errors="coerce")
        result["bid"] = pd.to_numeric(result[f"{side}_bid"], errors="coerce")
        result["next_bid"] = result.groupby(["slug", "horizon"])["bid"].shift(-1)
        result["next_ts"] = result.groupby(["slug", "horizon"])["snapshot_ts"].shift(-1)
        result["markout"] = result["next_bid"] - result["ask"]
        result["target"] = (result["markout"] > result["ask"].map(polymarket_fee_per_share)).astype(int)
        rows.append(result)
    return pd.concat(rows, ignore_index=True).sort_values(["snapshot_ts", "slug", "side_sign"])


def run_pm_dynamic(context: EngineContext) -> EngineResult:
    frame, identity, causal = _pm_checkpoint_frame(context)
    dynamic = _build_pm_dynamic(frame)
    mode = str(context.protocol.payload["method"].get("mode", "elasticity"))
    dynamic["deadline_inverse"] = 1.0 / (pd.to_numeric(dynamic["seconds_left"], errors="coerce") + 1.0)
    dynamic["absolute_distance_bps"] = pd.to_numeric(dynamic["distance_bps"], errors="coerce").abs()
    dynamic["expiry_ts"] = pd.to_numeric(dynamic["anchor_ts"], errors="coerce") + 60.0 * pd.to_numeric(dynamic["horizon"], errors="coerce")
    dynamic["simultaneous_15m_boundary"] = ((dynamic["expiry_ts"] % 900.0).abs() < 1.0).astype(float)
    if mode == "opening_inheritance":
        rank = dynamic.groupby(["slug", "horizon", "side_sign"])["snapshot_ts"].rank(method="first")
        dynamic = dynamic[rank == 1].copy()
    base_features = list(context.protocol.payload["method"]["features"])
    if mode == "deadline_convexity":
        base_features += ["deadline_inverse", "absolute_distance_bps"]
    elif mode == "boundary_resonance":
        base_features += ["simultaneous_15m_boundary"]
    features = [*base_features, "side_sign", "ask", "bid"]
    dynamic = _numeric(dynamic, ["_ts_ms", "next_ts", "markout", "target", *features])
    if len(dynamic) < int(context.protocol.payload["promotion_gates"].get("minimum_rows", 200)):
        return EngineResult("INSUFFICIENT_SAMPLE", "Too few repeated causal quote paths", {},
                            dict(EMPTY_ECONOMICS), [f"only {len(dynamic)} side-path rows"],
                            identity, causal)
    split = chronological_four_way_split(dynamic["_ts_ms"], purge_rows=2, **context.split_args)
    markout = dynamic["markout"].to_numpy(float)
    fee = dynamic["ask"].map(polymarket_fee_per_share).to_numpy(float) * context.cost_multiplier
    locked, selection = fit_locked_binary_policy(
        dynamic, features=features, target="target", split=split,
        thresholds=[0.55, 0.65, 0.75],
        policy_scorer=lambda actions, idx: float(np.sum(
            (markout[idx] - fee[idx])[actions == 1])), seed=context.seed)
    scored = score_locked_binary_policy(locked, dynamic, "target", split.test)
    active = scored["actions"] == 1
    idx = split.test[active]
    gross = markout[idx]
    costs = fee[idx]
    net = gross - costs
    duration = (dynamic["next_ts"].to_numpy(float)[idx] - dynamic["snapshot_ts"].to_numpy(float)[idx])
    economics = economic_metrics(
        gross_pnls=gross, net_pnls=net, timestamps_ms=dynamic["_ts_ms"].to_numpy(np.int64)[idx],
        opportunities=len(split.test), turnover=float(2 * len(net)),
        capital_duration_seconds=float(np.maximum(0.0, duration).sum()),
        cost_per_action=costs, seed=context.seed)
    status, reasons = economic_verdict(economics, context.protocol.payload["promotion_gates"])
    rng = np.random.default_rng(context.seed + 97)
    duration_all = (dynamic["next_ts"].to_numpy(float)[split.test]
                    - dynamic["snapshot_ts"].to_numpy(float)[split.test])
    random_positions: list[int] = []
    side_all = dynamic["side_sign"].to_numpy(float)[split.test]
    duration_bucket = np.rint(duration_all).astype(int)
    for side_value in (-1.0, 1.0):
        for duration_value in np.unique(duration_bucket[side_all == side_value]):
            candidate_mask = active & (side_all == side_value) & (duration_bucket == duration_value)
            count = int(candidate_mask.sum())
            pool = np.flatnonzero((side_all == side_value) & (duration_bucket == duration_value))
            if count:
                chosen = rng.choice(pool, size=min(count, len(pool)), replace=False)
                random_positions.extend(chosen.tolist())
    random_positions = sorted(random_positions)
    random_idx = split.test[np.asarray(random_positions, dtype=int)] if random_positions else np.array([], dtype=int)
    random_net = markout[random_idx] - fee[random_idx]
    control = {"actions": int(len(random_idx)), "net_pnl": float(random_net.sum()),
               "candidate_minus_control_net_pnl": float(net.sum() - random_net.sum()),
               "holding_seconds_candidate": float(np.maximum(0.0, duration).sum()),
               "holding_seconds_control": float(np.maximum(0.0, duration_all[random_positions]).sum())
               if random_positions else 0.0}
    if status == "PASS_CANDIDATE" and control["candidate_minus_control_net_pnl"] <= 0:
        status = "FAIL_UNSTABLE"
        reasons.append("does not beat action-count and side-matched random quote paths")
    return EngineResult(status, f"Polymarket dynamic response: {mode}",
                        {"model_selection": selection, "untouched_test": scored["metrics"],
                         "mode": mode,
                         "population_rows": int(len(dynamic)), "control": control},
                        economics, reasons, identity, causal, split.boundaries)


def run_pm_cross_expiry(context: EngineContext) -> EngineResult:
    frame, identity, causal = _pm_checkpoint_frame(context)
    needed = ["_ts_ms", "anchor_ts", "anchor_price", "horizon", "checkpoint_s",
              "up_ask", "down_ask"]
    frame = _numeric(frame, needed)
    frame["expiry_ts"] = frame["anchor_ts"] + frame["horizon"] * 60.0
    five = frame[frame["horizon"] == 5].copy()
    fifteen = frame[frame["horizon"] == 15].copy()
    pairs = five.merge(fifteen, on=["expiry_ts", "checkpoint_s"], suffixes=("_5", "_15"))
    if pairs.empty:
        return EngineResult("INSUFFICIENT_SAMPLE", "No simultaneous 5m/15m expiry pairs", {},
                            dict(EMPTY_ECONOMICS), ["zero causally aligned cross-expiry pairs"],
                            identity, causal)
    # For two thresholds at the same expiry, YES(high threshold) implies YES(low threshold).
    high_is_five = pairs["anchor_price_5"] >= pairs["anchor_price_15"]
    high_down_ask = np.where(high_is_five, pairs["down_ask_5"], pairs["down_ask_15"])
    low_up_ask = np.where(high_is_five, pairs["up_ask_15"], pairs["up_ask_5"])
    fee = (np.array([polymarket_fee_per_share(value) for value in high_down_ask])
           + np.array([polymarket_fee_per_share(value) for value in low_up_ask]))
    fee *= context.cost_multiplier
    gross = 1.0 - high_down_ask - low_up_ask
    net = gross - fee
    pairs = pairs.assign(_gross=gross, _net=net, _fee=fee)
    pairs = pairs.sort_values("_ts_ms_5").reset_index(drop=True)
    cut = int(len(pairs) * 0.85)
    test = pairs.iloc[cut:].copy()
    active = test["_net"] > 0
    selected = test[active]
    if selected.empty:
        diagnostics = {"paired_states": int(len(pairs)), "untouched_test_pairs": int(len(test)),
                       "positive_lock_opportunities": 0,
                       "median_pair_cost": float((test["down_ask_5"] + test["up_ask_15"]).median())}
        return EngineResult("FAIL_NO_EDGE", "No executable monotonicity lock after costs",
                            diagnostics, dict(EMPTY_ECONOMICS),
                            ["no positive guaranteed-payout basket on untouched test"], identity, causal)
    timestamps = selected["_ts_ms_5"].to_numpy(np.int64)
    costs = selected["_fee"].to_numpy(float)
    economics = economic_metrics(
        gross_pnls=selected["_gross"].to_numpy(float),
        net_pnls=selected["_net"].to_numpy(float), timestamps_ms=timestamps,
        opportunities=len(test), turnover=float(2 * len(selected)),
        capital_duration_seconds=float(selected["checkpoint_s"].sum()),
        cost_per_action=costs, seed=context.seed)
    status, reasons = economic_verdict(economics, context.protocol.payload["promotion_gates"])
    diagnostics = {"paired_states": int(len(pairs)), "untouched_test_pairs": int(len(test)),
                   "positive_lock_opportunities": int(len(selected)),
                   "logic": "buy NO on higher anchor plus YES on lower anchor; payout >= $1"}
    return EngineResult(status, "Cross-expiry monotonic complete-set lock", diagnostics,
                        economics, reasons, identity, causal)


def run_l2_capacity(context: EngineContext) -> EngineResult:
    database = context.data_dir / "polymarket_l2.duckdb"
    if not database.is_file():
        raise DataUnavailable(f"missing {database}")
    con = duckdb.connect(str(database), read_only=True)
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        required = {"pm_l2_book_levels", "pm_l2_book_summaries"}
        if not required <= tables:
            raise SchemaUnavailable(f"missing L2 tables: {sorted(required - tables)}")
        summary = con.execute(
            "SELECT count(*), count(*) FILTER (WHERE synchronized AND valid), "
            "quantile_cont(best_ask_size, 0.5), max(recv_ts_ns) "
            "FROM pm_l2_book_summaries").fetchone()
        levels = con.execute("SELECT count(*), count(DISTINCT seq) FROM pm_l2_book_levels").fetchone()
    finally:
        con.close()
    diagnostics = {
        "book_summaries": int(summary[0]), "valid_synchronized_summaries": int(summary[1]),
        "median_top_ask_size": float(summary[2] or 0.0), "book_levels": int(levels[0]),
        "distinct_level_sequences": int(levels[1]),
    }
    return EngineResult("BLOCKED_DATA", "Depth is measurable but capacity requires a passing alpha",
                        diagnostics, dict(EMPTY_ECONOMICS),
                        ["no Phase 5 PASS_CANDIDATE exists to size without changing its population"],
                        {"path": str(database)}, {"sequenced_l2": True})


def run_ledger(context: EngineContext) -> EngineResult:
    database = context.data_dir / "opportunity_ledger.duckdb"
    if not database.is_file():
        raise DataUnavailable(f"missing {database}")
    con = duckdb.connect(str(database), read_only=True)
    try:
        decisions = con.execute("SELECT count(*) FROM opportunity_decisions").fetchone()[0]
        outcomes = con.execute("SELECT count(*) FROM opportunity_outcomes").fetchone()[0]
        joined = con.execute(
            "SELECT d.decision_ts, o.net_pnl FROM opportunity_decisions d JOIN opportunity_outcomes o "
            "USING(decision_id) WHERE o.net_pnl IS NOT NULL ORDER BY d.decision_ts").fetchall()
    finally:
        con.close()
    diagnostics = {"decisions": int(decisions), "outcomes": int(outcomes),
                   "joined_economic_outcomes": int(len(joined))}
    minimum = int(context.protocol.payload["promotion_gates"].get("minimum_test_actions", 100))
    if len(joined) < minimum:
        return EngineResult("INSUFFICIENT_SAMPLE", "Forward ledger has insufficient outcomes",
                            diagnostics, dict(EMPTY_ECONOMICS),
                            [f"{len(joined)} joined outcomes < required {minimum}"],
                            {"path": str(database)}, {"atomic_ledger": True})
    ts = np.array([row[0] for row in joined], dtype=np.int64)
    if np.nanmedian(np.abs(ts)) < 1e11:
        ts *= 1000
    pnl = np.array([row[1] for row in joined], dtype=float)
    economics = economic_metrics(gross_pnls=pnl, net_pnls=pnl, timestamps_ms=ts,
                                  opportunities=len(pnl), turnover=len(pnl),
                                  capital_duration_seconds=0.0, cost_per_action=np.zeros(len(pnl)),
                                  seed=context.seed)
    status, reasons = economic_verdict(economics, context.protocol.payload["promotion_gates"])
    return EngineResult(status, "Forward-ledger economic evidence", diagnostics, economics, reasons,
                        {"path": str(database)}, {"atomic_ledger": True})


def run_candidate_audit(context: EngineContext) -> EngineResult:
    method = context.protocol.payload["method"]
    relative = str(method.get("candidate_input", "research/phase5_candidate_evidence.parquet"))
    source = context.data_dir / relative
    if not source.is_file():
        return EngineResult("BLOCKED_DATA", "Candidate-dependent audit",
                            {"expected_input": str(source)}, dict(EMPTY_ECONOMICS),
                            ["no canonical per-decision candidate evidence exists"], {}, {})
    con = duckdb.connect()
    try:
        columns = [row[0] for row in con.execute(
            "DESCRIBE SELECT * FROM read_parquet(?)", [str(source)]).fetchall()]
        required = list(method.get("required_columns", ["ts_ms", "net_pnl"]))
        missing = sorted(set(required) - set(columns))
        if missing:
            raise SchemaUnavailable(f"candidate evidence missing columns: {missing}")
        # The canonical builder deliberately retains unresolved decisions for coverage. Economic
        # tests must not silently coerce their NULL outcomes into zeros or NaNs. Pull the status
        # columns when available and fail closed to resolved, economics-eligible rows below.
        optional_status = [
            column for column in ("resolved", "eligible_for_economics", "selected_action")
            if column in columns and column not in required
        ]
        projection_columns = [*required, *optional_status]
        projection = ", ".join(f'"{column}"' for column in projection_columns)
        limit = f" LIMIT {context.maximum_rows}" if context.maximum_rows > 0 else ""
        frame = con.execute(
            f"SELECT {projection} FROM read_parquet(?) ORDER BY ts_ms DESC{limit}",
            [str(source)]).fetchdf().sort_values("ts_ms").reset_index(drop=True)
    finally:
        con.close()
    recorded_rows = int(len(frame))
    if "resolved" in frame.columns:
        frame = frame[frame["resolved"].fillna(False).astype(bool)].copy()
    if "eligible_for_economics" in frame.columns:
        frame = frame[frame["eligible_for_economics"].fillna(False).astype(bool)].copy()
    minimum = int(context.protocol.payload["promotion_gates"].get("minimum_test_actions", 100))
    if len(frame) < minimum:
        return EngineResult("INSUFFICIENT_SAMPLE", "Candidate evidence is too small",
                            {"recorded_rows": recorded_rows, "economic_rows": len(frame)},
                            dict(EMPTY_ECONOMICS),
                            [f"{len(frame)} rows < required {minimum}"], {"path": str(source)}, {})
    mode = str(method.get("mode", "concentration"))
    ts = pd.to_numeric(frame["ts_ms"], errors="coerce").to_numpy(np.int64)
    pnl = pd.to_numeric(frame["net_pnl"], errors="coerce").to_numpy(float)
    diagnostics: dict[str, Any] = {
        "mode": mode,
        "recorded_rows": recorded_rows,
        "economic_rows": int(len(frame)),
    }
    tested_pnl = pnl

    if mode in {"negative_control", "randomization", "placebo"}:
        market_return = pd.to_numeric(frame["market_return"], errors="coerce").to_numpy(float)
        action = pd.to_numeric(frame["action"], errors="coerce").to_numpy(float)
        cost = pd.to_numeric(frame["cost"], errors="coerce").to_numpy(float)
        rng = np.random.default_rng(context.seed)
        if mode == "placebo":
            shifts = list(map(int, method.get("shifts_rows", [-60, -30, -10, 10, 30, 60])))
            placebo = {}
            for shift in shifts:
                moved = np.roll(action, shift)
                valid = np.ones(len(moved), dtype=bool)
                valid[:max(0, shift)] = False
                if shift < 0:
                    valid[shift:] = False
                placebo[str(shift)] = float(np.sum(moved[valid] * market_return[valid] - cost[valid]))
            diagnostics["placebo_net_pnl"] = placebo
            if max(placebo.values(), default=-np.inf) >= float(np.sum(pnl)):
                return EngineResult("FAIL_UNSTABLE", "Post/pre-event placebos are not weaker",
                                    diagnostics, dict(EMPTY_ECONOMICS),
                                    ["a shifted signal equals or beats the declared timing"],
                                    {"path": str(source)}, {})
        else:
            shuffled = rng.permutation(action)
            control_pnl = shuffled * market_return - cost
            diagnostics["control_net_pnl"] = float(control_pnl.sum())
            diagnostics["candidate_minus_control"] = float(pnl.sum() - control_pnl.sum())
            if diagnostics["candidate_minus_control"] <= 0:
                return EngineResult("FAIL_NO_EDGE", "Information-free control is not beaten",
                                    diagnostics, dict(EMPTY_ECONOMICS),
                                    ["candidate does not beat action-matched randomization"],
                                    {"path": str(source)}, {})
    elif mode == "redundancy":
        pivot = frame.pivot_table(index="ts_ms", columns="alpha_id", values="net_pnl",
                                  aggfunc="sum", fill_value=0.0)
        diagnostics["pnl_correlation"] = pivot.corr().round(6).to_dict()
        diagnostics["alpha_count"] = int(pivot.shape[1])
        if pivot.shape[1] < 2:
            return EngineResult("INSUFFICIENT_SAMPLE", "Redundancy requires two alphas",
                                diagnostics, dict(EMPTY_ECONOMICS), ["only one alpha_id"],
                                {"path": str(source)}, {})
    elif mode == "collision":
        grouped = frame.groupby("ts_ms").agg(actions=("alpha_id", "nunique"), pnl=("net_pnl", "sum"))
        collision = grouped[grouped["actions"] > 1]
        diagnostics.update({"collision_rows": int(len(collision)),
                            "collision_net_pnl": float(collision["pnl"].sum())})
        if len(collision) < minimum:
            return EngineResult("INSUFFICIENT_SAMPLE", "Too few alpha collisions", diagnostics,
                                dict(EMPTY_ECONOMICS), [f"only {len(collision)} collisions"],
                                {"path": str(source)}, {})
        tested_pnl = collision["pnl"].to_numpy(float)
        ts = collision.index.to_numpy(np.int64)
    elif mode == "reward_transport":
        gross = pd.to_numeric(frame["gross_pnl"], errors="coerce").to_numpy(float)
        current_cost = pd.to_numeric(frame["current_cost"], errors="coerce").to_numpy(float)
        tested_pnl = gross - current_cost
        diagnostics.update({"historical_net_pnl": float(pnl.sum()),
                            "transported_current_net_pnl": float(tested_pnl.sum())})
    elif mode == "context":
        grouped = frame.groupby(["alpha_id", "regime"])["net_pnl"].agg(["count", "sum", "mean"])
        diagnostics["context_metrics"] = {
            f"{alpha}|{regime}": values for (alpha, regime), values in grouped.to_dict(orient="index").items()
        }
        if not len(grouped):
            return EngineResult("INSUFFICIENT_SAMPLE", "No alpha/context cells", diagnostics,
                                dict(EMPTY_ECONOMICS), ["no populated context cells"],
                                {"path": str(source)}, {})
    elif mode == "decay":
        dated = pd.DataFrame({"ts": pd.to_datetime(ts, unit="ms", utc=True), "pnl": pnl})
        dated["week"] = dated["ts"].dt.to_period("W").astype(str)
        weekly = dated.groupby("week")["pnl"].sum()
        diagnostics["weekly_net_pnl"] = {str(key): float(value) for key, value in weekly.items()}
        diagnostics["latest_four_week_net_pnl"] = float(weekly.tail(4).sum())
        if len(weekly) < 8:
            return EngineResult("INSUFFICIENT_SAMPLE", "Decay analysis needs eight weeks",
                                diagnostics, dict(EMPTY_ECONOMICS),
                                [f"only {len(weekly)} distinct weeks"], {"path": str(source)}, {})
    elif mode == "revival":
        grouped = frame.groupby("regime")["net_pnl"].agg(["count", "sum", "mean"])
        diagnostics["regime_metrics"] = grouped.to_dict(orient="index")
        if len(grouped) < 2:
            return EngineResult("INSUFFICIENT_SAMPLE", "Revival needs multiple regimes",
                                diagnostics, dict(EMPTY_ECONOMICS), ["fewer than two regimes"],
                                {"path": str(source)}, {})
    elif mode == "conformal":
        predicted = pd.to_numeric(frame["predicted_ev"], errors="coerce").to_numpy(float)
        cut = int(len(frame) * 0.70)
        calibration_end = int(len(frame) * 0.85)
        residual = np.abs(pnl[cut:calibration_end] - predicted[cut:calibration_end])
        quantile = float(np.quantile(residual, 0.95, method="higher"))
        lower = predicted[calibration_end:] - quantile
        mask = lower > 0
        tested_pnl = pnl[calibration_end:][mask]
        ts = ts[calibration_end:][mask]
        diagnostics.update({"conformal_radius": quantile, "test_coverage_actions": int(mask.sum())})
    elif mode == "neighborhood":
        by_config = frame.groupby("configuration_id")["net_pnl"].agg(["count", "sum", "mean"])
        diagnostics["configuration_metrics"] = by_config.to_dict(orient="index")
        if len(by_config) < 3:
            return EngineResult("INSUFFICIENT_SAMPLE", "Neighborhood requires three configurations",
                                diagnostics, dict(EMPTY_ECONOMICS),
                                ["fewer than three declared neighboring configurations"],
                                {"path": str(source)}, {})

    if mode == "concentration":
        ordered = np.sort(pnl)[::-1]
        total = float(pnl[pnl > 0].sum())
        diagnostics.update({
            "top_1_trade_positive_profit_share": float(max(0.0, ordered[0]) / total) if total else 1.0,
            "top_5_trade_positive_profit_share": float(np.maximum(0.0, ordered[:5]).sum() / total) if total else 1.0,
            "top_10_trade_positive_profit_share": float(np.maximum(0.0, ordered[:10]).sum() / total) if total else 1.0,
        })
        if (by_config["sum"] <= 0).any():
            return EngineResult("FAIL_UNSTABLE", "Parameter neighborhood is not stable",
                                diagnostics, dict(EMPTY_ECONOMICS),
                                ["at least one declared neighbor is non-positive"],
                                {"path": str(source)}, {})

    zero_cost = np.zeros(len(tested_pnl))
    economics = economic_metrics(gross_pnls=tested_pnl, net_pnls=tested_pnl,
                                  timestamps_ms=ts[-len(tested_pnl):], opportunities=len(tested_pnl),
                                  turnover=len(tested_pnl), capital_duration_seconds=0.0,
                                  cost_per_action=zero_cost, seed=context.seed)
    status, reasons = economic_verdict(economics, context.protocol.payload["promotion_gates"])
    return EngineResult(status, f"Candidate audit: {mode}", diagnostics, economics, reasons,
                        {"path": str(source)}, {"per_decision_evidence": True})
