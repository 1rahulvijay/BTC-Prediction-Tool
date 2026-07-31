"""Pure decision logic for the model/head-driven Polymarket paper strategy.

The strategy may only enter from the existing rules-first Champion's PAPER_BET decision. That
decision already requires a fresh executable ask, exact crypto taker fee, depth, P(Hold) head
permission, meta filtering and positive edge after the configured buffer. The default Champion
calibration lockdown therefore keeps this strategy dormant until explicitly and visibly enabled.
"""
from __future__ import annotations

import math
from typing import Any


RULE_ID = "CHAMPION_DYNAMIC_PAPER_V1"


def _float(value: Any) -> float | None:
    try:
        number = float(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None
    return number if number is not None and math.isfinite(number) else None


def entry_decision(round_data: dict, quote: dict | None) -> dict:
    champion = round_data.get("champion") or {}
    side = str(round_data.get("current_position") or "")
    seconds_left = _float(round_data.get("seconds_left"))
    p_hold = _float(round_data.get("p_hold"))
    edge = _float(champion.get("edge"))
    reasons: list[str] = []
    if round_data.get("ref_captured_late_ms"):
        reasons.append("late_or_incomplete_anchor")
    if champion.get("action") != "PAPER_BET" or champion.get("bet_candidate") is not True:
        reasons.append("champion_did_not_authorize_paper_entry")
    if side not in ("UP", "DOWN"):
        reasons.append("leader_side_unavailable")
    if p_hold is None or not 0.0 <= p_hold <= 1.0:
        reasons.append("p_hold_unavailable")
    if edge is None or edge <= 0.0:
        reasons.append("positive_net_edge_unavailable")
    if seconds_left is None or seconds_left <= 10.0:
        reasons.append("too_late_for_executable_exit")
    if not quote:
        reasons.append("fresh_executable_quote_unavailable")
    elif str(quote.get("side") or "") != side:
        reasons.append("quote_side_mismatch")
    else:
        ask = _float(quote.get("ask"))
        bid = _float(quote.get("bid"))
        spread = _float(quote.get("spread"))
        depth = _float(quote.get("depth", quote.get("ask_size")))
        fee_in = _float(quote.get("fee", quote.get("fee_in")))
        if None in (ask, bid, spread, depth, fee_in):
            reasons.append("quote_fields_incomplete")
        elif not (0.0 < ask < 1.0 and 0.0 <= bid <= ask and spread >= 0.0 and fee_in >= 0.0):
            reasons.append("quote_prices_invalid")
        elif spread > 0.03:
            reasons.append("spread_too_wide")
        elif depth < 1.0:
            reasons.append("one_share_not_fillable")
    if reasons:
        return {"action": "NO_TRADE", "reason_codes": reasons}

    target_net = round(max(0.01, min(0.05, float(edge) * 0.50)), 6)
    stop_net = round(-max(0.02, min(0.05, (1.0 - float(p_hold)) * 0.25)), 6)
    state = {
        "open": True,
        "side": side,
        "entry": float(quote["ask"]),
        "fee_in": float(quote.get("fee", quote.get("fee_in")) or 0.0),
        "entry_seconds_left": float(seconds_left),
        "entry_p_hold": float(p_hold),
        "entry_edge": float(edge),
        "target_net": target_net,
        "stop_net": stop_net,
        "champion_confidence": int(_float(champion.get("confidence")) or 0),
        "risk_flags": list(champion.get("risk_flags") or []),
    }
    return {"action": "ENTER", "side": side, "state": state, "reason_codes": []}


def exit_decision(state: dict, round_data: dict, quote: dict | None) -> dict:
    if not state or not state.get("open"):
        return {"action": "HOLD", "reason_codes": ["position_not_open"]}
    if not quote:
        return {"action": "HOLD", "reason_codes": ["fresh_exit_quote_unavailable"]}
    if str(quote.get("side") or "") != str(state.get("side") or ""):
        return {"action": "HOLD", "reason_codes": ["exit_quote_side_mismatch"]}
    bid = _float(quote.get("bid"))
    fee_out = _float(quote.get("fee_out"))
    if bid is None or fee_out is None or not 0.0 <= bid < 1.0 or fee_out < 0.0:
        return {"action": "HOLD", "reason_codes": ["exit_quote_invalid"]}

    entry = _float(state.get("entry"))
    fee_in = _float(state.get("fee_in", 0.0))
    target_net = _float(state.get("target_net"))
    stop_net = _float(state.get("stop_net"))
    if None in (entry, fee_in, target_net, stop_net) or entry <= 0.0 or fee_in < 0.0:
        return {"action": "HOLD", "reason_codes": ["position_state_invalid"]}
    net = bid - fee_out - entry - fee_in
    reason = None
    if net >= target_net:
        reason = "DYNAMIC_TARGET"
    elif net <= stop_net:
        reason = "DYNAMIC_STOP"
    else:
        current_leader = str(round_data.get("current_position") or "")
        p_hold = _float(round_data.get("p_hold"))
        p_side = None
        if p_hold is not None and current_leader in ("UP", "DOWN"):
            p_side = p_hold if current_leader == state["side"] else 1.0 - p_hold
        if p_side is not None and p_side <= 0.35:
            reason = "MODEL_INVALIDATED"
        else:
            champion = round_data.get("champion") or {}
            if (
                net > 0.0
                and champion.get("action") != "PAPER_BET"
                and p_side is not None
                and p_side <= (_float(state.get("entry_p_hold")) or 0.0) - 0.10
            ):
                reason = "EDGE_DECAY_PROFIT_LOCK"
            seconds_left = _float(round_data.get("seconds_left"))
            if reason is None and seconds_left is not None and seconds_left <= 10.0:
                if p_side is not None and p_side < 0.50:
                    reason = "LAST_CHANCE_MODEL_EXIT"
    if reason is None:
        return {"action": "HOLD", "net_pnl": net, "reason_codes": []}
    return {
        "action": "EXIT",
        "exit_reason": reason,
        "net_pnl": net,
        "exit_gross": bid,
        "exit_fee": fee_out,
        "reason_codes": [reason.lower()],
    }
