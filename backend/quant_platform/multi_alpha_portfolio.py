"""Research-only allocation across independently promoted alpha engines."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping


@dataclass(frozen=True, slots=True)
class AlphaCandidate:
    strategy_id: str
    alpha_family: str
    evidence_id: str
    venue: str
    independently_promoted: bool
    forward_decisions: int
    forward_weeks: int
    expectancy_lower_bound: float
    q20_net_return: float
    expected_shortfall: float
    capacity_notional: float
    maximum_drawdown_fraction: float
    liquidity_score: float
    calibration_score: float
    btc_directional_exposure: float
    settlement_exposure: float


def _valid_candidate(item: AlphaCandidate) -> bool:
    numeric = (
        item.expectancy_lower_bound,
        item.q20_net_return,
        item.expected_shortfall,
        item.capacity_notional,
        item.maximum_drawdown_fraction,
        item.liquidity_score,
        item.calibration_score,
        item.btc_directional_exposure,
        item.settlement_exposure,
    )
    return (
        bool(item.strategy_id.strip())
        and bool(item.alpha_family.strip())
        and bool(item.evidence_id.strip())
        and bool(item.venue.strip())
        and item.independently_promoted
        and item.forward_decisions >= 1_000
        and item.forward_weeks >= 8
        and all(math.isfinite(value) for value in numeric)
        and item.expectancy_lower_bound > 0
        and item.q20_net_return > 0
        and item.expected_shortfall >= 0
        and item.capacity_notional > 0
        and item.maximum_drawdown_fraction > 0
        and 0 <= item.liquidity_score <= 1
        and 0 <= item.calibration_score <= 1
    )


def allocate_promoted_alphas(
    candidates: list[AlphaCandidate],
    *,
    correlations: Mapping[tuple[str, str], float],
    capital: float,
    risk_budget_fraction: float = 0.01,
    maximum_strategy_fraction: float = 0.005,
    maximum_venue_fraction: float = 0.01,
    maximum_directional_exposure_fraction: float = 0.005,
    maximum_settlement_exposure_fraction: float = 0.005,
) -> dict[str, float]:
    """Return notional allocations, or no allocations when evidence is incomplete."""

    if capital <= 0:
        raise ValueError("capital must be positive")
    eligible = [item for item in candidates if _valid_candidate(item)]
    if len(eligible) < 2:
        return {}
    if len({item.strategy_id for item in eligible}) != len(eligible):
        raise ValueError("strategy_id values must be unique")
    if len({item.evidence_id for item in eligible}) != len(eligible):
        raise ValueError("promotion evidence must be independently identified")
    if len({item.alpha_family for item in eligible}) != len(eligible):
        return {}
    correlation_penalty: dict[str, float] = {}
    for item in eligible:
        peers: list[float] = []
        for other in eligible:
            if other.strategy_id == item.strategy_id:
                continue
            key = tuple(sorted((item.strategy_id, other.strategy_id)))
            if key not in correlations:
                return {}
            value = float(correlations[key])
            if not math.isfinite(value) or not -1 <= value <= 1:
                raise ValueError("correlations must be finite in [-1, 1]")
            peers.append(max(0.0, value))
        correlation_penalty[item.strategy_id] = (
            sum(peers) / len(peers) if peers else 1.0
        )
    scores = {
        item.strategy_id: (
            min(item.expectancy_lower_bound, item.q20_net_return)
            * item.liquidity_score
            * item.calibration_score
            * (1.0 - 0.75 * correlation_penalty[item.strategy_id])
            / (item.maximum_drawdown_fraction + item.expected_shortfall + 1e-9)
        )
        for item in eligible
    }
    scores = {key: value for key, value in scores.items() if value > 0}
    if len(scores) < 2:
        return {}
    budget = capital * max(0.0, risk_budget_fraction)
    total = sum(scores.values())
    by_id = {item.strategy_id: item for item in eligible}
    allocations = {
        strategy_id: min(
            budget * score / total,
            capital * max(0.0, maximum_strategy_fraction),
            by_id[strategy_id].capacity_notional,
        )
        for strategy_id, score in scores.items()
    }

    def scale_for_limit(exposures: Mapping[str, float], limit: float) -> None:
        used = sum(
            allocations[strategy_id] * abs(exposure)
            for strategy_id, exposure in exposures.items()
        )
        maximum = capital * max(0.0, limit)
        if used > maximum and used > 0:
            factor = maximum / used
            for strategy_id in exposures:
                allocations[strategy_id] *= factor

    for venue in {item.venue for item in eligible}:
        scale_for_limit(
            {
                item.strategy_id: 1.0
                for item in eligible
                if item.strategy_id in allocations and item.venue == venue
            },
            maximum_venue_fraction,
        )
    scale_for_limit(
        {
            item.strategy_id: item.btc_directional_exposure
            for item in eligible
            if item.strategy_id in allocations
        },
        maximum_directional_exposure_fraction,
    )
    scale_for_limit(
        {
            item.strategy_id: item.settlement_exposure
            for item in eligible
            if item.strategy_id in allocations
        },
        maximum_settlement_exposure_fraction,
    )
    return {
        strategy_id: amount
        for strategy_id, amount in allocations.items()
        if amount > 0
    }
