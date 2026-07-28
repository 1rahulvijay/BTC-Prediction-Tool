"""Action selection from conservative post-cost return distributions."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable


WAIT = "WAIT"


@dataclass(frozen=True, slots=True)
class ActionReturnForecast:
    action: str
    mean_net_return: float
    q10: float
    q20: float
    q50: float
    q80: float
    q90: float
    expected_shortfall: float
    probability_positive: float
    estimated_cost: float
    reliability: float
    data_quality: float
    execution_feasible: bool
    risk_allowed: bool

    def __post_init__(self) -> None:
        if not self.action.strip() or self.action == WAIT:
            raise ValueError("candidate action must be non-empty and not WAIT")
        values = (
            self.mean_net_return,
            self.q10,
            self.q20,
            self.q50,
            self.q80,
            self.q90,
            self.expected_shortfall,
            self.probability_positive,
            self.estimated_cost,
            self.reliability,
            self.data_quality,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("action forecast values must be finite")
        if list((self.q10, self.q20, self.q50, self.q80, self.q90)) != sorted(
            (self.q10, self.q20, self.q50, self.q80, self.q90)
        ):
            raise ValueError("net-return quantiles must be non-decreasing")
        if not 0 <= self.probability_positive <= 1:
            raise ValueError("probability_positive must be in [0, 1]")
        if self.estimated_cost < 0:
            raise ValueError("estimated_cost cannot be negative")
        if self.expected_shortfall < 0:
            raise ValueError("expected_shortfall must be a non-negative loss magnitude")
        if not 0 <= self.reliability <= 1 or not 0 <= self.data_quality <= 1:
            raise ValueError("quality values must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class ActionDecision:
    action: str
    conservative_score: float
    reasons: tuple[str, ...]
    candidate: ActionReturnForecast | None


def choose_action(
    candidates: Iterable[ActionReturnForecast],
    *,
    tail_risk_reserve: float = 0.0,
    minimum_reliability: float = 0.60,
    minimum_data_quality: float = 0.80,
) -> ActionDecision:
    if tail_risk_reserve < 0:
        raise ValueError("tail_risk_reserve cannot be negative")
    accepted: list[tuple[float, ActionReturnForecast]] = []
    rejected: list[str] = []
    for item in candidates:
        reasons: list[str] = []
        if item.reliability < minimum_reliability:
            reasons.append("low_reliability")
        if item.data_quality < minimum_data_quality:
            reasons.append("poor_data_quality")
        if not item.execution_feasible:
            reasons.append("execution_infeasible")
        if not item.risk_allowed:
            reasons.append("risk_blocked")
        score = item.q20 - tail_risk_reserve
        if score <= 0:
            reasons.append("non_positive_q20_after_reserve")
        if reasons:
            rejected.append(f"{item.action}:{','.join(reasons)}")
        else:
            accepted.append((score, item))
    if not accepted:
        return ActionDecision(
            WAIT,
            0.0,
            tuple(rejected) if rejected else ("no_candidates",),
            None,
        )
    score, selected = max(accepted, key=lambda pair: pair[0])
    return ActionDecision(
        selected.action,
        score,
        ("highest_positive_conservative_net_return",),
        selected,
    )
