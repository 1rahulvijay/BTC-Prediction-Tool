"""Conservative shadow ranking of BUY UP, BUY DOWN, and NO_TRADE."""
from __future__ import annotations

from typing import Any

from .scenario_engine import evaluate_plans
from .trade_labels import required_exit_bid


MIN_P_PROFIT = 0.60
MIN_FULL_FILL_PROBABILITY = 0.70
TAIL_RISK_PENALTY = 0.50
UNCERTAINTY_PENALTY = 0.25
LIQUIDITY_PENALTY = 0.01


def _event_probability(events: dict[str, Any], key: str) -> float | None:
    value = events.get(key)
    try:
        return min(1.0, max(0.0, float(value))) if value is not None else None
    except (TypeError, ValueError):
        return None


def _maximum_safe_entry_ask(
    *,
    plan_name: str,
    share_path: dict[str, Any],
    p_settlement_win: float,
) -> float | None:
    """Highest model-implied entry whose expected and q10 net PnL stay positive."""

    def clears(entry: float) -> bool:
        plan = evaluate_plans(
            entry_vwap=entry,
            share_path=share_path,
            p_settlement_win=p_settlement_win,
        ).get(plan_name)
        return bool(
            plan
            and float(plan["expected_pnl"]) > 0.0
            and float(plan["pnl_q10"]) > 0.0
        )

    low, high = 0.000001, 0.999999
    if not clears(low):
        return None
    for _ in range(45):
        middle = (low + high) / 2.0
        if clears(middle):
            low = middle
        else:
            high = middle
    return low


def optimize_candidate(
    candidate: dict[str, Any],
    *,
    data_healthy: bool,
    evidence_promotable: bool,
) -> dict[str, Any]:
    reasons: list[str] = []
    entry_vwap = candidate.get("predicted_entry_vwap")
    share = candidate.get("share_forecast") or {}
    execution = candidate.get("execution_forecast") or {}
    if entry_vwap is None:
        reasons.append("predicted_entry_vwap_unavailable")
    if not share.get("path"):
        reasons.append("future_executable_bid_path_unavailable")
    p_settlement = _event_probability(
        share.get("events") or {}, "label_settlement_win"
    )
    if p_settlement is None:
        reasons.append("settlement_probability_unavailable")
    full_fill = _event_probability(
        execution.get("events") or {}, "entry_complete"
    )
    if full_fill is None:
        reasons.append("full_fill_probability_unavailable")
    if not data_healthy:
        reasons.append("data_unhealthy")
    if not evidence_promotable:
        reasons.append("insufficient_forward_evidence")
    if reasons:
        return {
            "action": "NO_TRADE",
            "side": candidate.get("side"),
            "reason_codes": reasons,
            "plans": {},
            "score": 0.0,
        }

    plans = evaluate_plans(
        entry_vwap=float(entry_vwap),
        share_path=share["path"],
        p_settlement_win=float(p_settlement),
    )
    ranked = []
    requested = float(candidate.get("requested_qty") or 0.0)
    capacity_q50 = float(
        ((execution.get("capacity") or {}).get("q50") or 0.0)
    )
    for name, plan in plans.items():
        uncertainty = max(0.0, float(plan["pnl_q90"]) - float(plan["pnl_q10"]))
        liquidity_shortfall = max(0.0, requested - capacity_q50) / max(1.0, requested)
        score = (
            float(plan["expected_pnl"])
            - TAIL_RISK_PENALTY * abs(min(0.0, float(plan["cvar_05"])))
            - UNCERTAINTY_PENALTY * uncertainty
            - LIQUIDITY_PENALTY * liquidity_shortfall
        )
        ranked.append((score, name, plan))
    ranked.sort(key=lambda item: item[0], reverse=True)
    if not ranked:
        return {
            "action": "NO_TRADE",
            "side": candidate.get("side"),
            "reason_codes": ["no_frozen_plan_evaluable"],
            "plans": plans,
            "score": 0.0,
        }
    score, plan_name, plan = ranked[0]
    gate_reasons = []
    if float(plan["expected_pnl"]) <= 0.0:
        gate_reasons.append("expected_net_pnl_not_positive")
    if float(plan["pnl_q10"]) <= 0.0:
        gate_reasons.append("pnl_lower_bound_not_positive")
    if float(plan["p_profit"]) < MIN_P_PROFIT:
        gate_reasons.append("profit_probability_below_gate")
    if float(full_fill) < MIN_FULL_FILL_PROBABILITY:
        gate_reasons.append("full_fill_probability_below_gate")
    if capacity_q50 + 1e-9 < requested:
        gate_reasons.append("median_capacity_below_requested_quantity")
    if score <= 0.0:
        gate_reasons.append("robust_utility_does_not_beat_no_trade")
    action = f"BUY_{candidate['side']}" if not gate_reasons else "NO_TRADE"
    maximum_safe_entry = _maximum_safe_entry_ask(
        plan_name=plan_name,
        share_path=share["path"],
        p_settlement_win=float(p_settlement),
    )
    return {
        "action": action,
        "side": candidate.get("side"),
        "requested_qty": requested,
        "recommended_exit_plan": plan_name,
        "score": round(float(score), 6),
        "expected_pnl": round(float(plan["expected_pnl"]), 6),
        "p_profit": round(float(plan["p_profit"]), 5),
        "pnl_q10": round(float(plan["pnl_q10"]), 6),
        "pnl_q25": round(float(plan["pnl_q25"]), 6),
        "pnl_q50": round(float(plan["pnl_q50"]), 6),
        "pnl_q75": round(float(plan["pnl_q75"]), 6),
        "pnl_q90": round(float(plan["pnl_q90"]), 6),
        "cvar_05": round(float(plan["cvar_05"]), 6),
        "profit_factor": (
            round(float(plan["profit_factor"]), 5)
            if plan.get("profit_factor") is not None
            else None
        ),
        "expected_holding_s": plan.get("expected_holding_s"),
        "maximum_safe_entry_ask": (
            round(float(maximum_safe_entry), 6)
            if maximum_safe_entry is not None
            else None
        ),
        "break_even_bid": required_exit_bid(float(entry_vwap), 0.0),
        "target_bid": required_exit_bid(float(entry_vwap), 0.03),
        "stop_bid": required_exit_bid(float(entry_vwap), -0.03),
        "reason_codes": gate_reasons,
        "plans": plans,
    }


def choose_trade(
    candidates: list[dict[str, Any]],
    *,
    data_healthy: bool,
    evidence_promotable: bool,
) -> dict[str, Any]:
    evaluated = [
        optimize_candidate(
            candidate,
            data_healthy=data_healthy,
            evidence_promotable=evidence_promotable,
        )
        for candidate in candidates
    ]
    actionable = [item for item in evaluated if item["action"].startswith("BUY_")]
    if not actionable:
        reasons = sorted(
            {reason for item in evaluated for reason in item.get("reason_codes", [])}
        )
        return {
            "action": "NO_TRADE",
            "mode": "SHADOW_ONLY",
            "reason_codes": reasons or ["no_candidate_beats_no_trade"],
            "candidates": evaluated,
        }
    winner = max(actionable, key=lambda item: float(item["score"]))
    return {**winner, "mode": "SHADOW_ONLY", "candidates": evaluated}


def selftest() -> None:
    blocked = optimize_candidate(
        {"side": "UP", "requested_qty": 10},
        data_healthy=True,
        evidence_promotable=False,
    )
    assert blocked["action"] == "NO_TRADE"
    assert "insufficient_forward_evidence" in blocked["reason_codes"]
    result = choose_trade([], data_healthy=True, evidence_promotable=False)
    assert result["action"] == "NO_TRADE"
    print("trade_plan_optimizer self-test: ALL PASS")


if __name__ == "__main__":
    selftest()
