"""Event-burstiness and Binance top-of-book resilience experiments."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from research.phase5_standalone.common.engine_types import EngineContext, EngineResult
from research.phase5_standalone.common.engines_matrix import _binary_economics
from research.phase5_standalone.common.metrics import EMPTY_ECONOMICS, economic_verdict
from research.phase5_standalone.common.temporal_split import chronological_four_way_split

from .data import load_contract


def _auc_model(frame: pd.DataFrame, features: list[str], target: str, split,
               seed: int) -> dict[str, Any]:
    x = frame[features].replace([np.inf, -np.inf], np.nan)
    y = frame[target].to_numpy(int)
    if len(np.unique(y[split.train])) < 2:
        raise ValueError("training target has one class")
    model = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                          LogisticRegression(max_iter=500, class_weight="balanced",
                                             random_state=seed))
    model.fit(x.iloc[split.train], y[split.train])
    probability = model.predict_proba(x.iloc[split.test])[:, 1]
    return {
        "auc": float(roc_auc_score(y[split.test], probability))
        if len(np.unique(y[split.test])) == 2 else None,
        "rows": int(len(split.test)),
        "positive_rate": float(y[split.test].mean()),
        "probability": probability,
        "target": y[split.test],
    }


def _event_bins(context: EngineContext) -> tuple[pd.DataFrame, dict, dict]:
    loaded = load_contract(context.data_dir, context.protocol.payload["data_contract"],
                           context.maximum_rows)
    events = loaded.frame.copy()
    events["price"] = pd.to_numeric(events["price"], errors="coerce")
    events["size"] = pd.to_numeric(events["size"], errors="coerce").fillna(0.0)
    events = events.dropna(subset=["price"])
    events["side_sign"] = events["side"].astype(str).str.upper().map(
        {"BUY": 1.0, "B": 1.0, "SELL": -1.0, "S": -1.0}).fillna(0.0)
    events["second"] = events["_ts_ms"] // 1000
    bins = events.groupby("second", sort=True).agg(
        _ts_ms=("_ts_ms", "max"),
        price=("price", "last"),
        event_count=("price", "size"),
        volume=("size", "sum"),
        signed_volume=("side_sign", lambda value: float(value.sum())),
    ).reset_index(drop=True)
    bins["return"] = bins["price"].pct_change().fillna(0.0)
    bins["count_mean_30"] = bins["event_count"].rolling(30, min_periods=5).mean()
    bins["count_var_30"] = bins["event_count"].rolling(30, min_periods=5).var()
    bins["fano_30"] = bins["count_var_30"] / bins["count_mean_30"].replace(0, np.nan)
    bins["quiet_fraction_30"] = (bins["event_count"] == 0).rolling(30, min_periods=5).mean()
    buy_fraction = ((bins["signed_volume"] + bins["event_count"]) /
                    (2 * bins["event_count"].replace(0, np.nan))).clip(0, 1)
    bins["sign_entropy"] = -(buy_fraction * np.log2(buy_fraction.clip(1e-9, 1)) +
                             (1 - buy_fraction) * np.log2((1 - buy_fraction).clip(1e-9, 1)))
    bins["signed_run"] = np.sign(bins["signed_volume"]).rolling(10, min_periods=3).sum().abs()
    bins["future_return_30"] = bins["price"].shift(-30) / bins["price"] - 1.0
    bins["future_range_proxy_30"] = bins["return"].abs().shift(-1).rolling(30).sum().shift(-29)
    return bins.replace([np.inf, -np.inf], np.nan), loaded.identity, loaded.causal_summary


def run_event_research(context: EngineContext) -> EngineResult:
    frame, identity, causal = _event_bins(context)
    mode = str(context.protocol.payload["method"]["mode"])
    if mode == "burstiness":
        features = ["fano_30", "quiet_fraction_30", "event_count", "volume",
                    "signed_volume", "count_mean_30"]
        source = "future_range_proxy_30"
    elif mode == "trade_sign_entropy":
        features = ["sign_entropy", "signed_run", "signed_volume", "event_count", "volume"]
        source = "future_return_30"
    else:
        raise ValueError(f"unknown event-research mode {mode}")
    frame = frame.dropna(subset=[*features, source]).reset_index(drop=True)
    if len(frame) < 500:
        raise ValueError(f"only {len(frame)} complete one-second bins")
    split = chronological_four_way_split(frame["_ts_ms"], purge_rows=30,
                                         **context.split_args)
    if mode == "burstiness":
        threshold = float(frame[source].iloc[split.train].quantile(0.75))
        frame["target"] = (frame[source] > threshold).astype(int)
    else:
        threshold = 0.0
        frame["target"] = (frame[source] > 0).astype(int)
    model = _auc_model(frame, features, "target", split, context.seed)
    span_days = float((frame["_ts_ms"].iloc[-1] - frame["_ts_ms"].iloc[0]) / 86_400_000)
    diagnostics = {
        "mode": mode,
        "features": features,
        "train_target_threshold": threshold,
        "untouched_auc": model["auc"],
        "untouched_rows": model["rows"],
        "span_days": span_days,
    }
    if span_days < 5:
        status, reasons = "INSUFFICIENT_SAMPLE", [
            f"event archive spans {span_days:.3f} days; at least five independent days are required"
        ]
    elif model["auc"] is None or model["auc"] < 0.55:
        status, reasons = "FAIL_NO_EDGE", ["untouched AUC did not clear 0.55"]
    else:
        status, reasons = "FAIL_UNSTABLE", ["predictive event statistic has no executable policy"]
    return EngineResult(status, f"{mode.replace('_', ' ').title()} event-time diagnostic",
                        diagnostics, dict(EMPTY_ECONOMICS), reasons, identity, causal,
                        split.boundaries)


def _l2_frame(context: EngineContext) -> tuple[pd.DataFrame, dict, dict]:
    loaded = load_contract(context.data_dir, context.protocol.payload["data_contract"],
                           context.maximum_rows)
    frame = loaded.frame.copy()
    columns = ["mid", "microprice", "spread_bps", "obi_20", "obi_near", "bid_usd",
               "ask_usd", "depth_slope", "ofi"]
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["mid", "spread_bps", "bid_usd", "ask_usd"]).sort_values(
        "_ts_ms").reset_index(drop=True)
    median_step_ms = max(1, int(np.median(np.diff(frame["_ts_ms"]))))
    frame.attrs["median_step_ms"] = median_step_ms
    return frame, loaded.identity, loaded.causal_summary


def _steps(frame: pd.DataFrame, seconds: float) -> int:
    return max(1, int(round(seconds * 1000 / frame.attrs["median_step_ms"])))


def _l2_resilience(context: EngineContext, frame: pd.DataFrame, identity: dict,
                   causal: dict) -> EngineResult:
    train_end = int(len(frame) * 0.50)
    threshold = float(frame["ofi"].abs().iloc[:train_end].quantile(0.95))
    horizon = _steps(frame, 30)
    frame["future_mid"] = frame["mid"].shift(-horizon)
    frame["future_spread"] = frame["spread_bps"].shift(-horizon)
    frame["future_depth"] = (frame["bid_usd"] + frame["ask_usd"]).shift(-horizon)
    events = frame[(frame["ofi"].abs() >= threshold) & frame["future_mid"].notna()].copy()
    if len(events) < 100:
        raise ValueError(f"only {len(events)} aggressive-flow events")
    sign = np.sign(events["ofi"])
    events["retained_markout_bps"] = sign * (events["future_mid"] / events["mid"] - 1) * 10_000
    events["spread_recovery"] = events["future_spread"] <= events["spread_bps"]
    events["depth_recovery"] = events["future_depth"] >= (events["bid_usd"] + events["ask_usd"])
    ts = events["_ts_ms"].to_numpy(np.int64)
    split = chronological_four_way_split(ts, purge_rows=1, **context.split_args)
    test = events.iloc[split.test]
    diagnostics = {
        "ofi_threshold_from_training_half": threshold,
        "events": int(len(events)),
        "untouched_events": int(len(test)),
        "untouched_mean_retained_markout_bps": float(test["retained_markout_bps"].mean()),
        "untouched_spread_recovery_rate": float(test["spread_recovery"].mean()),
        "untouched_depth_recovery_rate": float(test["depth_recovery"].mean()),
        "median_snapshot_interval_ms": frame.attrs["median_step_ms"],
    }
    return EngineResult("FAIL_UNSTABLE", "Aggressive-flow market-resilience diagnostic",
                        diagnostics, dict(EMPTY_ECONOMICS),
                        ["resilience markout has not cleared full execution costs"], identity,
                        causal, split.boundaries)


def _l2_replenishment(context: EngineContext, frame: pd.DataFrame, identity: dict,
                      causal: dict) -> EngineResult:
    horizon = _steps(frame, 15)
    frame["bid_change"] = frame["bid_usd"].pct_change()
    frame["ask_change"] = frame["ask_usd"].pct_change()
    frame["future_bid"] = frame["bid_usd"].shift(-horizon)
    frame["future_ask"] = frame["ask_usd"].shift(-horizon)
    frame["future_return"] = frame["mid"].shift(-horizon) / frame["mid"] - 1
    train = frame.iloc[:int(len(frame) * 0.50)]
    bid_cut = float(train["bid_change"].quantile(0.05))
    ask_cut = float(train["ask_change"].quantile(0.05))
    depleted_bid = frame[(frame["bid_change"] <= bid_cut) & frame["future_bid"].notna()]
    depleted_ask = frame[(frame["ask_change"] <= ask_cut) & frame["future_ask"].notna()]
    diagnostics = {
        "bid_depletion_threshold": bid_cut,
        "ask_depletion_threshold": ask_cut,
        "bid_events": int(len(depleted_bid)),
        "ask_events": int(len(depleted_ask)),
        "bid_replenishment_rate": float((depleted_bid["future_bid"] >= depleted_bid["bid_usd"]).mean()),
        "ask_replenishment_rate": float((depleted_ask["future_ask"] >= depleted_ask["ask_usd"]).mean()),
        "markout_after_bid_depletion_bps": float(depleted_bid["future_return"].mean() * 10_000),
        "markout_after_ask_depletion_bps": float(depleted_ask["future_return"].mean() * 10_000),
    }
    return EngineResult("FAIL_UNSTABLE", "Bid/ask replenishment asymmetry diagnostic",
                        diagnostics, dict(EMPTY_ECONOMICS),
                        ["top-of-book snapshots cannot establish maker queue position"],
                        identity, causal)


def _l2_spread_shock(context: EngineContext, frame: pd.DataFrame, identity: dict,
                     causal: dict) -> EngineResult:
    horizon = _steps(frame, 30)
    frame["past_vol"] = frame["mid"].pct_change().abs().rolling(max(5, horizon)).mean()
    frame["future_return"] = frame["mid"].shift(-horizon) / frame["mid"] - 1
    frame["future_range"] = frame["mid"].pct_change().abs().shift(-1).rolling(horizon).sum().shift(-(horizon - 1))
    cut = float(frame["spread_bps"].iloc[:int(len(frame) * 0.50)].quantile(0.95))
    complete = frame.dropna(subset=["past_vol", "future_return", "future_range"])
    shock = complete[complete["spread_bps"] >= cut]
    control_pool = complete[complete["spread_bps"] < cut].copy()
    if len(shock) < 100:
        raise ValueError(f"only {len(shock)} spread shocks")
    # Match each shock to nearest-volatility non-shock without claiming unique reuse.
    pool_vol = control_pool["past_vol"].to_numpy(float)
    matched = control_pool.iloc[[int(np.argmin(np.abs(pool_vol - value)))
                                 for value in shock["past_vol"].to_numpy(float)]]
    diagnostics = {
        "spread_threshold_bps": cut,
        "shock_rows": int(len(shock)),
        "shock_mean_abs_markout_bps": float(shock["future_return"].abs().mean() * 10_000),
        "matched_mean_abs_markout_bps": float(matched["future_return"].abs().mean() * 10_000),
        "shock_mean_future_range_bps": float(shock["future_range"].mean() * 10_000),
        "matched_mean_future_range_bps": float(matched["future_range"].mean() * 10_000),
    }
    lift = diagnostics["shock_mean_abs_markout_bps"] - diagnostics["matched_mean_abs_markout_bps"]
    status = "FAIL_UNSTABLE" if lift > 0 else "FAIL_NO_EDGE"
    return EngineResult(status, "Spread-shock matched-volatility diagnostic", diagnostics,
                        dict(EMPTY_ECONOMICS),
                        ["direction and executable net edge were not established"], identity, causal)


def _l2_microprice(context: EngineContext, frame: pd.DataFrame, identity: dict,
                   causal: dict) -> EngineResult:
    available = [seconds for seconds in [0.1, 1, 5, 15, 30]
                 if _steps(frame, seconds) * frame.attrs["median_step_ms"] <= seconds * 1000 * 2]
    rows = {}
    for seconds in available:
        horizon = _steps(frame, seconds)
        future = frame["mid"].shift(-horizon)
        valid = future.notna()
        markout = (future[valid] / frame.loc[valid, "mid"] - 1) * 10_000
        micro_signal = np.sign(frame.loc[valid, "microprice"] - frame.loc[valid, "mid"])
        obi_signal = np.sign(frame.loc[valid, "obi_near"])
        rows[f"{seconds:g}s"] = {
            "rows": int(valid.sum()),
            "microprice_signed_markout_bps": float(np.mean(markout * micro_signal)),
            "imbalance_signed_markout_bps": float(np.mean(markout * obi_signal)),
            "midprice_no_signal_markout_bps": float(markout.mean()),
            "full_cost_hurdle_bps": float(9.0 * context.cost_multiplier),
        }
    best = max((row["microprice_signed_markout_bps"] for row in rows.values()), default=-math.inf)
    status = "FAIL_AFTER_COSTS" if best > 0 else "FAIL_NO_EDGE"
    reasons = ["microprice markout did not clear the declared 9 bps round-trip cost"]
    return EngineResult(status, "Microprice versus midprice and imbalance markouts",
                        {"horizons": rows, "median_snapshot_interval_ms": frame.attrs["median_step_ms"]},
                        dict(EMPTY_ECONOMICS), reasons, identity, causal)


def _l2_impact_asymmetry(context: EngineContext, frame: pd.DataFrame, identity: dict,
                         causal: dict) -> EngineResult:
    horizon = _steps(frame, 30)
    frame["future_return"] = frame["mid"].shift(-horizon) / frame["mid"] - 1
    cut = float(frame["ofi"].abs().iloc[:int(len(frame) * 0.50)].quantile(0.90))
    buy = frame[(frame["ofi"] >= cut) & frame["future_return"].notna()]
    sell = frame[(frame["ofi"] <= -cut) & frame["future_return"].notna()]
    diagnostics = {
        "ofi_threshold": cut,
        "buy_events": int(len(buy)),
        "sell_events": int(len(sell)),
        "buy_markout_bps": float(buy["future_return"].mean() * 10_000),
        "sell_directional_markout_bps": float(-sell["future_return"].mean() * 10_000),
        "impact_asymmetry_bps": float((buy["future_return"].mean() + sell["future_return"].mean()) * 10_000),
    }
    return EngineResult("FAIL_UNSTABLE", "Buy/sell OFI impact-asymmetry diagnostic",
                        diagnostics, dict(EMPTY_ECONOMICS),
                        ["OFI is a proxy, not exact aggressive notional"], identity, causal)


def _l2_toxic_veto(context: EngineContext, frame: pd.DataFrame, identity: dict,
                   causal: dict) -> EngineResult:
    horizon_seconds = 30
    horizon = _steps(frame, horizon_seconds)
    frame["future_return"] = frame["mid"].shift(-horizon) / frame["mid"] - 1
    frame["base_action"] = np.sign(frame["microprice"] - frame["mid"])
    frame["toxicity"] = (
        frame["ofi"].abs().rank(pct=True) +
        frame["spread_bps"].rank(pct=True) +
        (frame["bid_usd"] + frame["ask_usd"]).rank(pct=True, ascending=False)
    ) / 3
    frame = frame.dropna(subset=["future_return", "base_action", "toxicity"]).reset_index(drop=True)
    split = chronological_four_way_split(frame["_ts_ms"], purge_rows=horizon,
                                         **context.split_args)
    thresholds = [0.70, 0.80, 0.90, 0.95]
    cost = 9.0 * context.cost_multiplier / 10_000
    scores = {}
    for threshold in thresholds:
        action = np.where(frame["toxicity"].to_numpy()[split.policy] <= threshold,
                          frame["base_action"].to_numpy()[split.policy], 0)
        active = action != 0
        scores[threshold] = float(np.sum(
            frame["future_return"].to_numpy()[split.policy][active] * action[active] - cost))
    locked = max(scores, key=scores.get)
    base = frame["base_action"].to_numpy()[split.test]
    gated = np.where(frame["toxicity"].to_numpy()[split.test] <= locked, base, 0)
    base_econ, _ = _binary_economics(context, frame, base, split.test, "future_return",
                                     hold_seconds=horizon_seconds)
    gated_econ, control = _binary_economics(context, frame, gated, split.test, "future_return",
                                            hold_seconds=horizon_seconds)
    status, reasons = economic_verdict(gated_econ, context.protocol.payload["promotion_gates"])
    lift = float((gated_econ.get("net_pnl") or 0) - (base_econ.get("net_pnl") or 0))
    if status == "PASS_CANDIDATE" and lift <= 0:
        status, reasons = "FAIL_NO_EDGE", ["toxicity veto did not improve the unchanged base"]
    diagnostics = {
        "base_policy": "sign(microprice-midprice)",
        "locked_toxicity_threshold": locked,
        "policy_scores": {str(k): v for k, v in scores.items()},
        "base_economics": base_econ,
        "gated_minus_base_net_pnl": lift,
        "control": control,
    }
    return EngineResult(status, "Toxic-flow veto on an unchanged microprice policy",
                        diagnostics, gated_econ, reasons, identity, causal, split.boundaries)


def run_l2_research(context: EngineContext) -> EngineResult:
    frame, identity, causal = _l2_frame(context)
    mode = str(context.protocol.payload["method"]["mode"])
    functions = {
        "resilience": _l2_resilience,
        "replenishment": _l2_replenishment,
        "spread_shock": _l2_spread_shock,
        "microprice": _l2_microprice,
        "impact_asymmetry": _l2_impact_asymmetry,
        "toxic_veto": _l2_toxic_veto,
    }
    if mode not in functions:
        raise ValueError(f"unknown L2-research mode {mode}")
    return functions[mode](context, frame, identity, causal)
