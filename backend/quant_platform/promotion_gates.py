"""Frozen, stress-aware strategy promotion gates."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
import math


class PromotionStatus(StrEnum):
    PAPER_ONLY = "PAPER_ONLY"
    ELIGIBLE_FOR_LIVE_REVIEW = "ELIGIBLE_FOR_LIVE_REVIEW"


@dataclass(frozen=True, slots=True)
class PromotionThresholds:
    min_forward_trades: int = 500
    min_forward_weeks: int = 8
    min_trading_days: int = 30
    min_mean_expectancy: float = 0.0
    min_day_block_lower_bound: float = 0.0
    min_profit_factor: float = 1.20
    max_single_day_profit_concentration: float = 0.20
    max_single_regime_profit_concentration: float = 0.50
    max_pbo: float = 0.20
    min_deflated_sharpe_probability: float = 0.95
    max_paper_live_divergence: float = 0.20
    max_drawdown: float = 0.10


@dataclass(frozen=True, slots=True)
class StrategyEvidence:
    forward_trades: int
    forward_weeks: int
    trading_days: int
    mean_expectancy: float
    day_block_lower_bound: float
    profit_factor: float
    positive_fee_stress_50: bool
    positive_slippage_stress_50: bool
    positive_latency_1000ms: bool
    majority_weeks_positive: bool
    single_day_profit_concentration: float
    single_regime_profit_concentration: float
    maximum_drawdown: float
    pbo: float
    deflated_sharpe_probability: float
    paper_live_execution_divergence: float


@dataclass(frozen=True, slots=True)
class PromotionDecision:
    status: PromotionStatus
    reasons: tuple[str, ...]
    metrics: dict


def evaluate_promotion(
    evidence: StrategyEvidence,
    thresholds: PromotionThresholds | None = None,
) -> PromotionDecision:
    gate = thresholds or PromotionThresholds()
    for name, value in asdict(evidence).items():
        if isinstance(value, float) and not math.isfinite(value):
            return PromotionDecision(
                PromotionStatus.PAPER_ONLY,
                (f"non_finite:{name}",),
                asdict(evidence),
            )
    reasons: list[str] = []
    checks = (
        (evidence.forward_trades >= gate.min_forward_trades, "forward_trades"),
        (evidence.forward_weeks >= gate.min_forward_weeks, "forward_weeks"),
        (evidence.trading_days >= gate.min_trading_days, "trading_days"),
        (evidence.mean_expectancy > gate.min_mean_expectancy, "mean_expectancy"),
        (
            evidence.day_block_lower_bound > gate.min_day_block_lower_bound,
            "day_block_lower_bound",
        ),
        (evidence.profit_factor > gate.min_profit_factor, "profit_factor"),
        (evidence.positive_fee_stress_50, "fee_stress"),
        (evidence.positive_slippage_stress_50, "slippage_stress"),
        (evidence.positive_latency_1000ms, "latency_stress"),
        (evidence.majority_weeks_positive, "weekly_stability"),
        (
            evidence.single_day_profit_concentration
            < gate.max_single_day_profit_concentration,
            "single_day_concentration",
        ),
        (
            evidence.single_regime_profit_concentration
            < gate.max_single_regime_profit_concentration,
            "single_regime_concentration",
        ),
        (evidence.maximum_drawdown < gate.max_drawdown, "maximum_drawdown"),
        (evidence.pbo < gate.max_pbo, "pbo"),
        (
            evidence.deflated_sharpe_probability
            > gate.min_deflated_sharpe_probability,
            "deflated_sharpe",
        ),
        (
            evidence.paper_live_execution_divergence
            < gate.max_paper_live_divergence,
            "paper_live_divergence",
        ),
    )
    reasons.extend(f"gate_failed:{name}" for passed, name in checks if not passed)
    return PromotionDecision(
        PromotionStatus.PAPER_ONLY if reasons else PromotionStatus.ELIGIBLE_FOR_LIVE_REVIEW,
        tuple(reasons),
        asdict(evidence),
    )
