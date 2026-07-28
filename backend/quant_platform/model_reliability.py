"""Reliability and disagreement controls for target-specific forecasts."""
from __future__ import annotations

from dataclasses import dataclass
import math
from statistics import mean, pstdev
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ReliabilityInputs:
    data_quality: float
    distribution_quality: float
    calibration_quality: float
    regime_familiarity: float
    stability_quality: float

    def __post_init__(self) -> None:
        values = (
            self.data_quality,
            self.distribution_quality,
            self.calibration_quality,
            self.regime_familiarity,
            self.stability_quality,
        )
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
            raise ValueError("reliability inputs must be finite in [0, 1]")

    @property
    def score(self) -> float:
        return (
            self.data_quality
            * self.distribution_quality
            * self.calibration_quality
            * self.regime_familiarity
            * self.stability_quality
        )


@dataclass(frozen=True, slots=True)
class Disagreement:
    mean_probability: float
    standard_deviation: float
    range: float
    maximum_pair_gap: float


def disagreement(probabilities: Mapping[str, float]) -> Disagreement:
    if not probabilities:
        raise ValueError("at least one probability is required")
    values = [float(value) for value in probabilities.values()]
    if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
        raise ValueError("probabilities must be finite in [0, 1]")
    spread = max(values) - min(values)
    return Disagreement(
        mean_probability=mean(values),
        standard_deviation=pstdev(values),
        range=spread,
        maximum_pair_gap=spread,
    )


def reliability_adjusted_weights(
    base_weights: Mapping[str, float],
    reliability: Mapping[str, ReliabilityInputs],
    *,
    minimum_total_reliability: float = 0.10,
) -> dict[str, float]:
    if set(base_weights) != set(reliability):
        raise ValueError("base and reliability model sets differ")
    adjusted: dict[str, float] = {}
    for model_id, weight in base_weights.items():
        value = float(weight)
        if not math.isfinite(value) or value < 0:
            raise ValueError("base weights must be finite and non-negative")
        adjusted[model_id] = value * reliability[model_id].score
    total = sum(adjusted.values())
    if total < minimum_total_reliability:
        return {}
    return {model_id: value / total for model_id, value in adjusted.items()}
