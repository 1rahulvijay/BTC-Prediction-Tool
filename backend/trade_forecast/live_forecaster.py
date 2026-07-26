"""Compose live complete-trade SHADOW forecasts without changing Champion behavior."""
from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict, deque
from typing import Any

from . import btc_path_serving, execution_serving, share_path_serving
from .trade_forecast_logger import LOG_HEALTH, log_forecast_monitored
from .trade_labels import required_exit_bid, taker_fee
from .trade_plan_optimizer import choose_trade
from .trade_schema import (
    FEATURE_COLUMNS,
    MAX_LOOKBACK_ERROR_S,
    PROMOTION_GATE,
    ENTRY_CHECKPOINTS_S,
    MODE,
    QUANTITIES,
    policy_hash,
)


# TIME-bounded, not count-bounded. maxlen=240 means "240 observations", which at a fast update
# rate is a few seconds and at a slow one is many minutes - the same deque could or could not
# cover a 60s lookback depending on market activity. HISTORY_WINDOW_S is what the lookbacks
# actually need; the generous maxlen only caps memory.
HISTORY_WINDOW_S = 180.0
# The emergency cap is memory protection ONLY and must never be the thing that decides coverage.
# If it ever engages, the lookback window is no longer guaranteed, so it warns rather than
# silently truncating.
HISTORY_HARD_CAP = 20_000
_HISTORY: dict[str, deque] = defaultdict(deque)
_CACHE: dict[str, tuple[float, dict[str, Any], dict[str, Any]]] = {}
_LOGGED: set[str] = set()
INFERENCE_INTERVAL_S = 5.0
DEFAULT_TRADE_QUANTITY = 10


def _ladder_vwap(levels: list, quantity: float) -> tuple[float | None, float]:
    remaining = float(quantity)
    filled = cost = 0.0
    for raw in levels or []:
        try:
            price, size = float(raw[0]), float(raw[1])
        except (TypeError, ValueError, IndexError):
            continue
        take = min(remaining, max(0.0, size))
        cost += take * price
        filled += take
        remaining -= take
        if remaining <= 1e-9:
            break
    return ((cost / filled) if filled > 0 else None, filled)


def _history_value(history: deque, now_s: float, seconds: int, key: str) -> float | None:
    """Observation nearest the requested lookback, or None.

    The previous version accepted ANY older observation, so a "30s return" could be measured
    against a print from six minutes ago and still be reported as a 30s return. Same rule and
    same tolerance as the historical builder (MAX_LOOKBACK_ERROR_S), because a live feature that
    means something different from its training counterpart is training-serving skew."""
    target = now_s - seconds
    selected = None
    for item in history:
        if item["ts"] <= target:
            selected = item
        else:
            break
    if not selected or selected.get(key) is None:
        return None
    if abs(float(selected["ts"]) - target) > MAX_LOOKBACK_ERROR_S:
        return None
    return float(selected[key])


def _safe_div(a: float, b: float) -> float:
    return float(a) / float(b) if abs(float(b)) > 1e-12 else 0.0


def _finite(value: Any) -> float | None:
    """None for anything that is not a real number. Never a neutral stand-in."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _feature_values(
    round_data: dict[str, Any],
    share_prices: dict[str, Any],
    side: str,
    quantity: int,
    history: deque,
) -> dict[str, float] | None:
    own = share_prices.get(side.lower()) or {}
    opposite = "down" if side == "UP" else "up"
    opp = share_prices.get(opposite) or {}
    required = ("bid", "ask", "bid_size", "ask_size", "bid_ladder", "ask_ladder")
    if any(own.get(field) is None for field in required):
        return None
    current_btc = float(round_data.get("current_price") or 0.0)
    anchor = float(round_data.get("price_to_beat") or 0.0)
    if current_btc <= 0 or anchor <= 0:
        return None
    now_s = float(share_prices.get("ts") or time.time())
    side_sign = 1.0 if side == "UP" else -1.0
    signed = (current_btc - anchor) * side_sign
    own_bid = float(own["bid"])
    own_ask = float(own["ask"])
    bid_depth = sum(float(level[1]) for level in own["bid_ladder"] if len(level) >= 2)
    ask_depth = sum(float(level[1]) for level in own["ask_ladder"] if len(level) >= 2)
    bid_size = float(own["bid_size"])
    ask_size = float(own["ask_size"])
    # NO SILENT NEUTRAL IMPUTATION. A missing P(Hold) is not a 50/50 market, a missing return is
    # not a flat market, and a missing history is not a calm one. Substituting the neutral value
    # makes a data failure indistinguishable from a genuine reading - and the model, trained on
    # real values, treats the fabricated one with full confidence. Missing required input means
    # NO FORECAST, which the caller surfaces as NO_DATA / NO_TRADE.
    p_hold_current = round_data.get("p_hold")
    current_position = round_data.get("current_position")
    if p_hold_current is None or current_position is None:
        return None
    p_hold_value = _finite(p_hold_current)
    if p_hold_value is None:
        return None
    p_hold_side = p_hold_value if side == current_position else 1.0 - p_hold_value

    # The opposite-side book is a REQUIRED input (opp_bid/ask/spread/sizes are all in
    # FEATURE_COLUMNS). Defaulting it to zero states "the other side is worthless", which is a
    # strong and usually false claim, not an absence of information.
    if any(opp.get(field) is None for field in ("bid", "ask", "bid_size", "ask_size")):
        return None
    vol_60s = _finite(round_data.get("vol_60s_pct"))
    if vol_60s is None:
        return None

    missing: list[str] = []

    def btc_return(seconds: int) -> float:
        previous = _history_value(history, now_s, seconds, "btc")
        value = _finite(previous)
        if not value:
            # A REQUIRED feature with no data. Returning 0.0 told the model "perfectly flat
            # market" with full confidence, while the historical builder invalidates the same
            # candidate - textbook training-serving skew. `_missing_optional` cannot repair it
            # because it is not in FEATURE_COLUMNS and the model never sees it.
            missing.append(f"btc_return_{seconds}s_bps")
            return None
        return _safe_div(current_btc - value, value) * 10_000.0

    def bid_velocity(seconds: int) -> float:
        previous = _history_value(history, now_s, seconds, f"{side.lower()}_bid")
        value = _finite(previous)
        if value is None:
            missing.append(f"contract_bid_velocity_{seconds}s")
            return None
        return own_bid - value

    values: dict[str, Any] = {
        "horizon": float(round_data.get("horizon") or 0),
        "seconds_left": float(round_data.get("seconds_left") or 0.0),
        "seconds_elapsed": float(round_data.get("horizon") or 0) * 60.0
        - float(round_data.get("seconds_left") or 0.0),
        "requested_qty": float(quantity),
        "side_up": 1.0 if side == "UP" else 0.0,
        "side_is_leader": 1.0 if signed >= 0.0 else 0.0,
        "current_btc": current_btc,
        "anchor_price": anchor,
        "distance_usd_side": signed,
        "distance_bps_side": _safe_div(signed, anchor) * 10_000.0,
        "abs_distance_bps": abs(_safe_div(signed, anchor) * 10_000.0),
        "btc_return_5s_bps": btc_return(5),
        "btc_return_15s_bps": btc_return(15),
        "btc_return_30s_bps": btc_return(30),
        "btc_return_60s_bps": btc_return(60),
        "btc_vol_60s_pct": vol_60s,
        "p_hold_side": p_hold_side,
        "own_bid": own_bid,
        "own_ask": own_ask,
        "own_spread": float(own.get("spread") or own_ask - own_bid),
        "own_bid_size": bid_size,
        "own_ask_size": ask_size,
        "own_bid_depth": bid_depth,
        "own_ask_depth": ask_depth,
        "own_bid_levels": float(len(own["bid_ladder"])),
        "own_ask_levels": float(len(own["ask_ladder"])),
        "opp_bid": float(opp["bid"]),
        "opp_ask": float(opp["ask"]),
        "opp_spread": float(opp.get("spread") or float(opp["ask"]) - float(opp["bid"])),
        "opp_bid_size": float(opp["bid_size"]),
        "opp_ask_size": float(opp["ask_size"]),
        "contract_bid_velocity_5s": bid_velocity(5),
        "contract_bid_velocity_15s": bid_velocity(15),
        "contract_bid_velocity_30s": bid_velocity(30),
        # Derived from two nullable inputs, so it is nullable too. Computing it eagerly crashed
        # once btc_return started returning None; the required-feature gate below turns a None
        # here into NO FORECAST, which is the intended behaviour.
        "btc_share_sensitivity_30s": _sensitivity_30s(
            bid_velocity(30), current_btc, btc_return(30)
        ),
        "top_imbalance": _safe_div(bid_size - ask_size, bid_size + ask_size),
        "depth_imbalance": _safe_div(bid_depth - ask_depth, bid_depth + ask_depth),
        "decision_quote_age_s": float(share_prices.get("age_seconds") or 0.0),
    }
    # OPTIONAL features that were unavailable are reported, not hidden. `missing` is populated by
    # the history-dependent helpers above; a caller may still forecast without them, but the
    # forecast records which inputs were absent and how stale the quote was.
    values["_missing_optional"] = sorted(set(missing))
    values["_quote_age_s"] = float(share_prices.get("age_seconds") or 0.0)
    # SAME CONTRACT AS THE BUILDER. Every FEATURE_COLUMNS entry must be present and finite, or
    # there is no forecast - the caller surfaces MISSING_REQUIRED_FEATURE / NO_DATA.
    absent = [
        name for name in FEATURE_COLUMNS
        if values.get(name) is None or not _is_finite(values.get(name))
    ]
    if absent:
        values["_missing_required"] = absent
        return None
    return values


def _sensitivity_30s(velocity, current_btc, return_bps):
    """Share move per dollar of BTC move. None if either input is unavailable."""
    if velocity is None or return_bps is None:
        return None
    delta = float(current_btc) * float(return_bps) / 10_000.0
    return _safe_div(float(velocity), delta)


def _prune_history(history: deque, now_s: float) -> None:
    """Drop observations older than HISTORY_WINDOW_S.

    The window was previously enforced by `deque(maxlen=240)`, i.e. by COUNT. At a fast update
    rate 240 observations span a few seconds - not enough for a 60s lookback - and at a slow rate
    they span many minutes. HISTORY_WINDOW_S existed as a constant but nothing pruned by it, so
    the intended time bound was never actually applied."""
    cutoff = float(now_s) - HISTORY_WINDOW_S
    while history and float(history[0]["ts"]) < cutoff:
        history.popleft()
    if len(history) > HISTORY_HARD_CAP:
        print(
            f"[trade-forecast] history hard cap hit ({len(history)} obs in "
            f"{HISTORY_WINDOW_S:.0f}s); lookback coverage is NOT guaranteed",
            flush=True,
        )
        while len(history) > HISTORY_HARD_CAP:
            history.popleft()


def _is_finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _capacity_table(share_prices: dict[str, Any], side: str) -> list[dict[str, Any]]:
    quote = share_prices.get(side.lower()) or {}
    rows = []
    for quantity in QUANTITIES:
        ask_vwap, ask_fill = _ladder_vwap(quote.get("ask_ladder") or [], quantity)
        bid_vwap, bid_fill = _ladder_vwap(quote.get("bid_ladder") or [], quantity)
        rows.append(
            {
                "quantity": quantity,
                "current_ask_vwap": round(ask_vwap, 5) if ask_vwap is not None else None,
                "entry_available": ask_fill >= quantity - 1e-9,
                "current_bid_vwap": round(bid_vwap, 5) if bid_vwap is not None else None,
                "exit_available": bid_fill >= quantity - 1e-9,
            }
        )
    return rows


def _candidate(
    round_data: dict[str, Any],
    share_prices: dict[str, Any],
    side: str,
    quantity: int,
    history: deque,
) -> dict[str, Any]:
    values = _feature_values(round_data, share_prices, side, quantity, history)
    if values is None:
        return {
            "side": side,
            "requested_qty": quantity,
            "status": "MISSING_LADDER_OR_FEATURES",
        }
    quote = share_prices[side.lower()]
    current_vwap, current_fill = _ladder_vwap(
        quote.get("ask_ladder") or [], quantity
    )
    execution = execution_serving.score(int(round_data["horizon"]), values)
    slippage = (execution.get("entry_slippage") or {}).get("q80")
    predicted_entry = (
        min(0.999999, max(0.000001, float(current_vwap) + float(slippage)))
        if current_vwap is not None
        and current_fill >= quantity - 1e-9
        and slippage is not None
        else None
    )
    share = share_path_serving.score_candidate(int(round_data["horizon"]), values)
    return {
        "side": side,
        "requested_qty": quantity,
        "features": values,
        "current_ask": quote.get("ask"),
        "current_bid": quote.get("bid"),
        "current_full_qty_ask_vwap": current_vwap,
        "current_full_qty_entry_available": current_fill
        >= quantity - 1e-9,
        "predicted_entry_vwap": predicted_entry,
        "share_forecast": share,
        "execution_forecast": execution,
        "capacity": _capacity_table(share_prices, side),
        "break_even_bid": (
            required_exit_bid(predicted_entry, 0.0)
            if predicted_entry is not None
            else None
        ),
        "target_3c_bid": (
            required_exit_bid(predicted_entry, 0.03)
            if predicted_entry is not None
            else None
        ),
        "stop_3c_bid": (
            required_exit_bid(predicted_entry, -0.03)
            if predicted_entry is not None
            else None
        ),
    }


def _logger_rows(
    round_data: dict[str, Any],
    result: dict[str, Any],
    now_ms: int,
) -> None:
    seconds_left = float(round_data.get("seconds_left") or 0.0)
    checkpoints = ENTRY_CHECKPOINTS_S.get(int(round_data.get("horizon") or 0), ())
    nearest = min(checkpoints, key=lambda value: abs(value - seconds_left), default=None)
    if nearest is None or abs(nearest - seconds_left) > 1.5:
        return
    evaluations = {
        (item.get("side"), float(item.get("requested_qty") or 0.0)): item
        for item in (result.get("decision") or {}).get("candidates") or []
    }
    for candidate in result.get("candidates_raw") or []:
        evaluation = evaluations.get(
            (
                candidate.get("side"),
                float(candidate.get("requested_qty") or 0.0),
            )
        ) or {}
        if not candidate.get("features"):
            continue
        key = (
            f"{round_data.get('id')}|{nearest}|{candidate['side']}|"
            f"{candidate['requested_qty']}"
        )
        if key in _LOGGED:
            continue
        # NOTE: the de-duplication key is marked ONLY AFTER a confirmed write (see the end of
        # this block). Marking it here meant a failed insert permanently suppressed every later
        # retry for that checkpoint - the evidence was gone, and the run looked merely quiet.
        forecast_id = hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]
        share = candidate.get("share_forecast") or {}
        events = share.get("events") or {}
        summary = share.get("summary") or {}
        model_hash = hashlib.sha256(
            json.dumps(
                {
                    "share": share_path_serving.status().get("artifact_hash"),
                    "btc": btc_path_serving.status().get("artifact_hash"),
                    "execution": execution_serving.status().get("artifact_hash"),
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        forecast = {
            "forecast_id": forecast_id,
            "snapshot_id": key,
            "decision_ts": int(now_ms),
            "round_id": round_data.get("id"),
            "horizon": int(round_data.get("horizon") or 0),
            "price_to_beat": round_data.get("price_to_beat"),
            "current_btc": round_data.get("current_price"),
            "side": candidate["side"],
            "seconds_left": seconds_left,
            "requested_qty": candidate["requested_qty"],
            "entry_ask": candidate.get("current_ask"),
            "predicted_entry_vwap": candidate.get("predicted_entry_vwap"),
            "predicted_entry_fee": (
                taker_fee(float(candidate["predicted_entry_vwap"]))
                if candidate.get("predicted_entry_vwap") is not None
                else None
            ),
            "break_even_bid": candidate.get("break_even_bid"),
            "target_bid": candidate.get("target_3c_bid"),
            "stop_bid": candidate.get("stop_3c_bid"),
            "p_ever_profitable": events.get("label_ever_profitable"),
            "p_lockable_profit": events.get("label_lockable_1c"),
            "p_target_before_stop": events.get("label_take_3c_before_stop_3c"),
            "p_settlement_win": events.get("label_settlement_win"),
            "predicted_mfe": (summary.get("actual_mfe") or {}).get("q50"),
            "predicted_mae": (summary.get("actual_mae") or {}).get("q50"),
            "predicted_first_profitable_s": (
                summary.get("actual_first_profitable_s") or {}
            ).get("q50"),
            "pnl_q10": evaluation.get("pnl_q10"),
            "pnl_q25": evaluation.get("pnl_q25"),
            "pnl_q50": evaluation.get("pnl_q50"),
            "pnl_q75": evaluation.get("pnl_q75"),
            "pnl_q90": evaluation.get("pnl_q90"),
            "expected_pnl": evaluation.get("expected_pnl"),
            "cvar": evaluation.get("cvar_05"),
            "recommended_action": evaluation.get("action"),
            "recommended_exit_plan": evaluation.get("recommended_exit_plan"),
            "reason_codes": evaluation.get("reason_codes") or [],
            "model_hash": model_hash,
            "feature_hash": hashlib.sha256(
                json.dumps(
                    candidate["features"], sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
            ).hexdigest(),
            "policy_hash": policy_hash(),
            "mode": MODE,
            "evidence_status": share.get("status"),
            "created_at": int(now_ms),
        }
        paths = []
        btc_path = (result.get("btc_forecast") or {}).get("path") or {}
        crossing_path = share.get("crossing_path") or {}
        for offset, point in (share.get("path") or {}).items():
            btc = btc_path.get(str(offset)) or {}
            crossing = crossing_path.get(str(offset)) or {}
            paths.append(
                {
                    "offset_seconds": int(offset),
                    **{f"btc_{key}": value for key, value in btc.items()},
                    **{f"share_bid_{key}": value for key, value in point.items()},
                    "p_break_even_cross": crossing.get("break_even"),
                    "p_target_cross": crossing.get("target_3c"),
                    "p_stop_cross": crossing.get("stop_3c"),
                }
            )
        # Logging must never interrupt the price-to-beat ticker - but a failure must never be
        # invisible either, and it must never be PERMANENT. The de-dup key is committed only on a
        # confirmed write, so a transient failure is retried on the next tick instead of silently
        # deleting that checkpoint from the evidence set forever.
        if log_forecast_monitored(forecast, paths):
            _LOGGED.add(key)
        else:
            _LOGGED.discard(key)


def score_round(
    round_data: dict[str, Any],
    share_prices: dict[str, Any] | None,
    now_ms: int,
) -> dict[str, Any]:
    if not share_prices:
        return {
            "mode": MODE,
            "status": "WAITING_FOR_FRESH_FULL_LADDER",
            "action": "NO_TRADE",
            "reason": "The recorder has not supplied a fresh matching full book.",
        }
    round_id = str(round_data.get("id") or "")
    if not round_id:
        return {
            "mode": MODE,
            "status": "INVALID_ROUND",
            "action": "NO_TRADE",
        }
    history = _HISTORY[round_id]
    observation = {
        "ts": float(share_prices.get("ts") or now_ms / 1000.0),
        "btc": float(round_data.get("current_price") or 0.0),
        "up_bid": (share_prices.get("up") or {}).get("bid"),
        "down_bid": (share_prices.get("down") or {}).get("bid"),
    }
    if not history or observation["ts"] > history[-1]["ts"]:
        history.append(observation)
        _prune_history(history, observation["ts"])
    cached = _CACHE.get(round_id)
    seconds_left = float(round_data.get("seconds_left") or 0.0)
    checkpoints = ENTRY_CHECKPOINTS_S.get(int(round_data.get("horizon") or 0), ())
    nearest = min(checkpoints, key=lambda value: abs(value - seconds_left), default=None)
    checkpoint_due = bool(
        nearest is not None
        and abs(float(nearest) - seconds_left) <= 1.5
        and any(
            f"{round_id}|{nearest}|{side}|{quantity}" not in _LOGGED
            for side in ("UP", "DOWN")
            for quantity in QUANTITIES
        )
    )
    if cached and time.time() - cached[0] < INFERENCE_INTERVAL_S and not checkpoint_due:
        _logger_rows(round_data, cached[2], now_ms)
        return cached[1]
    quantities = QUANTITIES if checkpoint_due else (DEFAULT_TRADE_QUANTITY,)
    candidates = [
        _candidate(round_data, share_prices, side, quantity, history)
        for side in ("UP", "DOWN")
        for quantity in quantities
    ]
    valid = [candidate for candidate in candidates if candidate.get("features")]
    if valid:
        generic = dict(valid[0]["features"])
        # BTC head was trained on the canonical UP-side/1-share row.
        up = next((item for item in valid if item["side"] == "UP"), valid[0])
        generic = dict(up["features"])
        generic["requested_qty"] = 1.0
        btc_forecast = btc_path_serving.score(int(round_data["horizon"]), generic)
    else:
        btc_forecast = {
            "status": "MISSING_FEATURES",
            "promotable": False,
            "path": {},
        }
    evidence_promotable = bool(
        valid
        and btc_forecast.get("promotable")
        and all(
            candidate["share_forecast"].get("promotable")
            and candidate["execution_forecast"].get("promotable")
            for candidate in valid
        )
    )
    data_healthy = (
        float(share_prices.get("age_seconds") or 999.0) <= 5.0
        and not bool(round_data.get("ref_captured_late_ms"))
        and {candidate["side"] for candidate in valid} == {"UP", "DOWN"}
    )
    decision = choose_trade(
        valid,
        data_healthy=data_healthy,
        evidence_promotable=evidence_promotable,
    )
    internal_result = {
        "mode": MODE,
        "status": (
            "SHADOW_EVALUATED"
            if evidence_promotable
            else "PILOT_ONLY_NOT_ACTIONABLE"
        ),
        "action": decision["action"],
        "reason_codes": decision.get("reason_codes") or [],
        "decision": decision,
        "btc_forecast": btc_forecast,
        "candidates_raw": candidates,
        "models": {
            "share": share_path_serving.status(),
            "btc": btc_path_serving.status(),
            "execution": execution_serving.status(),
        },
        "champion_unchanged": True,
        "plain_reason": (
            # Generated from M0_V2, never a duplicated literal: the gate said 500 while the
            # protocol required 1,000, so the app told the operator the wrong number.
            f"No trade: the complete-trade models do not yet have the required "
            f"{PROMOTION_GATE['min_independent_rounds']:,} independent rounds and "
            f"{PROMOTION_GATE['min_calendar_weeks']} calendar weeks of forward L2 evidence."
            if not evidence_promotable
            else "Shadow estimate only; the production Champion remains unchanged."
        ),
    }
    public_candidates = []
    public_evaluations = {
        (
            item.get("side"),
            float(item.get("requested_qty") or 0.0),
        ): item
        for item in (decision.get("candidates") or [])
    }
    for candidate in candidates:
        if candidate.get("requested_qty") != DEFAULT_TRADE_QUANTITY:
            continue
        share = candidate.get("share_forecast") or {}
        execution = candidate.get("execution_forecast") or {}
        evaluation = public_evaluations.get(
            (
                candidate.get("side"),
                float(candidate.get("requested_qty") or 0.0),
            )
        ) or {}
        public_candidates.append(
            {
                "side": candidate.get("side"),
                "requested_qty": candidate.get("requested_qty"),
                "status": candidate.get("status") or share.get("status"),
                "current_ask": candidate.get("current_ask"),
                "current_bid": candidate.get("current_bid"),
                "current_full_qty_ask_vwap": candidate.get(
                    "current_full_qty_ask_vwap"
                ),
                "current_full_qty_entry_available": candidate.get(
                    "current_full_qty_entry_available"
                ),
                "predicted_entry_vwap": candidate.get("predicted_entry_vwap"),
                "break_even_bid": candidate.get("break_even_bid"),
                "target_3c_bid": candidate.get("target_3c_bid"),
                "stop_3c_bid": candidate.get("stop_3c_bid"),
                "capacity": candidate.get("capacity") or [],
                "events": share.get("events") or {},
                "summary": share.get("summary") or {},
                "path": share.get("path") or {},
                "ask_path": share.get("ask_path") or {},
                "crossing_path": share.get("crossing_path") or {},
                "execution_events": execution.get("events") or {},
                "execution_capacity": execution.get("capacity") or {},
                "evaluation": {
                    key: evaluation.get(key)
                    for key in (
                        "action",
                        "recommended_exit_plan",
                        "expected_pnl",
                        "p_profit",
                        "pnl_q10",
                        "pnl_q25",
                        "pnl_q50",
                        "pnl_q75",
                        "pnl_q90",
                        "cvar_05",
                        "profit_factor",
                        "expected_holding_s",
                        "maximum_safe_entry_ask",
                        "reason_codes",
                    )
                },
                "reason_codes": sorted(
                    set(
                        (share.get("reason_codes") or [])
                        + (execution.get("reason_codes") or [])
                    )
                ),
            }
        )
    public_decision = {
        key: value
        for key, value in decision.items()
        if key not in ("candidates", "plans")
    }
    result = {
        key: value
        for key, value in internal_result.items()
        if key not in ("candidates_raw", "decision")
    }
    result["decision"] = public_decision
    result["candidates"] = public_candidates
    _CACHE[round_id] = (time.time(), result, internal_result)
    if len(_HISTORY) > 20:
        active = set(list(_HISTORY)[-20:])
        for stale_id in list(_HISTORY):
            if stale_id not in active:
                _HISTORY.pop(stale_id, None)
                _CACHE.pop(stale_id, None)
    _logger_rows(round_data, internal_result, now_ms)
    return result


def status() -> dict[str, Any]:
    return {
        "mode": MODE,
        "share_model": share_path_serving.status(),
        "btc_model": btc_path_serving.status(),
        "execution_model": execution_serving.status(),
        "tracked_rounds": len(_HISTORY),
    }


def selftest() -> None:
    assert _ladder_vwap([[0.5, 5], [0.6, 10]], 10) == (0.55, 10.0)
    result = score_round({}, None, 1)
    assert result["action"] == "NO_TRADE"
    print("live_forecaster self-test: ALL PASS")


if __name__ == "__main__":
    selftest()
