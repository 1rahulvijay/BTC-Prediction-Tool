"""Conservative allocation from lower-bound edge, never raw confidence."""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True, slots=True)
class AllocationCandidate:
    strategy_id: str
    expectancy_lower_bound: float
    capacity_notional: float
    max_drawdown_fraction: float
    correlation_penalty: float
    liquidity_score: float
    calibration_score: float


def allocate(
    candidates: list[AllocationCandidate],
    capital: float,
    risk_budget_fraction: float = 0.01,
    max_strategy_fraction: float = 0.005,
) -> dict[str, float]:
    if capital <= 0:
        raise ValueError("capital must be positive")
    budget = capital * max(0.0, risk_budget_fraction)
    scores: dict[str, float] = {}
    for item in candidates:
        values = (
            item.expectancy_lower_bound,
            item.capacity_notional,
            item.max_drawdown_fraction,
            item.correlation_penalty,
            item.liquidity_score,
            item.calibration_score,
        )
        if not all(math.isfinite(value) for value in values):
            continue
        if item.expectancy_lower_bound <= 0 or item.capacity_notional <= 0:
            continue
        if item.max_drawdown_fraction <= 0:
            continue
        quality = (
            item.expectancy_lower_bound
            * max(0.0, min(1.0, item.liquidity_score))
            * max(0.0, min(1.0, item.calibration_score))
            * max(0.0, 1.0 - item.correlation_penalty)
            / item.max_drawdown_fraction
        )
        if quality > 0:
            scores[item.strategy_id] = quality
    total = sum(scores.values())
    if total <= 0:
        return {}
    result: dict[str, float] = {}
    cap_per_strategy = capital * max(0.0, max_strategy_fraction)
    by_id = {item.strategy_id: item for item in candidates}
    for strategy_id, score in scores.items():
        proposed = budget * score / total
        result[strategy_id] = min(
            proposed,
            cap_per_strategy,
            by_id[strategy_id].capacity_notional,
        )
    return result
