"""Economic metrics and fail-closed Phase 5 verdicts."""
from __future__ import annotations

import math
from typing import Any

import numpy as np

from .block_bootstrap import block_mean_interval


EMPTY_ECONOMICS: dict[str, Any] = {
    "opportunities": 0,
    "actions": 0,
    "gross_pnl": None,
    "net_pnl": None,
    "pnl_per_opportunity": None,
    "profit_factor": None,
    "maximum_drawdown": None,
    "expected_shortfall_5pct": None,
    "turnover": None,
    "capital_duration_seconds": None,
    "largest_day_profit_share": None,
    "net_pnl_cost_1_5x": None,
    "net_pnl_cost_2x": None,
    "day_lower_confidence_bound": None,
    "week_lower_confidence_bound": None,
}


def _max_drawdown(pnls: np.ndarray) -> float:
    equity = np.cumsum(np.asarray(pnls, dtype=float))
    if not len(equity):
        return 0.0
    peaks = np.maximum.accumulate(np.r_[0.0, equity])[:-1]
    return float(np.min(equity - peaks))


def economic_metrics(
    *,
    gross_pnls: np.ndarray,
    net_pnls: np.ndarray,
    timestamps_ms: np.ndarray,
    opportunities: int,
    turnover: float,
    capital_duration_seconds: float,
    cost_per_action: np.ndarray,
    seed: int,
) -> dict[str, Any]:
    gross = np.asarray(gross_pnls, dtype=float)
    net = np.asarray(net_pnls, dtype=float)
    ts = np.asarray(timestamps_ms, dtype=np.int64)
    costs = np.asarray(cost_per_action, dtype=float)
    if not (len(gross) == len(net) == len(ts) == len(costs)):
        raise ValueError("economic metric arrays must align")
    positive = float(net[net > 0].sum())
    negative = float(-net[net < 0].sum())
    day = block_mean_interval(net, ts, block="day", seed=seed)
    week = block_mean_interval(net, ts, block="week", seed=seed + 1)
    day_sums: dict[int, float] = {}
    for stamp, value in zip(ts, net):
        day_sums[int(stamp // 86_400_000)] = day_sums.get(int(stamp // 86_400_000), 0.0) + value
    profitable_days = [max(0.0, value) for value in day_sums.values()]
    positive_total = sum(profitable_days)
    concentration = max(profitable_days, default=0.0) / positive_total if positive_total else 1.0
    return {
        "opportunities": int(opportunities),
        "actions": int(len(net)),
        "gross_pnl": float(gross.sum()),
        "net_pnl": float(net.sum()),
        "pnl_per_opportunity": float(net.sum() / max(1, opportunities)),
        "profit_factor": float(positive / negative) if negative > 0 else (math.inf if positive else 0.0),
        "maximum_drawdown": _max_drawdown(net),
        "expected_shortfall_5pct": float(net[net <= np.quantile(net, 0.05)].mean()) if len(net) else None,
        "turnover": float(turnover),
        "capital_duration_seconds": float(capital_duration_seconds),
        "largest_day_profit_share": float(concentration),
        "net_pnl_cost_1_5x": float((net - 0.5 * costs).sum()),
        "net_pnl_cost_2x": float((net - costs).sum()),
        "day_lower_confidence_bound": day["lower"],
        "week_lower_confidence_bound": week["lower"],
        "day_blocks": day["blocks"],
        "week_blocks": week["blocks"],
    }


def economic_verdict(metrics: dict[str, Any], gates: dict[str, Any]) -> tuple[str, list[str]]:
    actions = int(metrics.get("actions") or 0)
    minimum = int(gates.get("minimum_test_actions", 100))
    if actions < minimum:
        return "INSUFFICIENT_SAMPLE", [f"{actions} test actions < required {minimum}"]
    gross = float(metrics.get("gross_pnl") or 0.0)
    net = float(metrics.get("net_pnl") or 0.0)
    if gross <= 0:
        return "FAIL_NO_EDGE", ["gross PnL is not positive"]
    if net <= 0:
        return "FAIL_AFTER_COSTS", ["gross PnL is positive but net PnL is not"]
    reasons: list[str] = []
    required_pf = float(gates.get("minimum_profit_factor", 1.2))
    if float(metrics.get("profit_factor") or 0.0) < required_pf:
        reasons.append(f"profit factor below {required_pf}")
    if metrics.get("day_lower_confidence_bound") is None or metrics["day_lower_confidence_bound"] <= 0:
        reasons.append("day-block lower confidence bound is not positive")
    if metrics.get("week_lower_confidence_bound") is None or metrics["week_lower_confidence_bound"] <= 0:
        reasons.append("week-block lower confidence bound is not positive")
    if float(metrics.get("largest_day_profit_share") or 1.0) > float(gates.get("maximum_day_concentration", 0.35)):
        reasons.append("profit is too concentrated in one day")
    if float(metrics.get("net_pnl_cost_1_5x") or 0.0) <= 0:
        reasons.append("net PnL fails 1.5x cost stress")
    return ("FAIL_UNSTABLE", reasons) if reasons else ("PASS_CANDIDATE", [])


def selftest() -> None:
    ts = np.arange(200) * 86_400_000 + 1_700_000_000_000
    positive = np.full(200, 1.0)
    metrics = economic_metrics(gross_pnls=positive, net_pnls=positive,
                               timestamps_ms=ts, opportunities=200, turnover=200,
                               capital_duration_seconds=200, cost_per_action=np.zeros(200), seed=1)
    status, _ = economic_verdict(metrics, {"minimum_test_actions": 100})
    assert status == "PASS_CANDIDATE"

