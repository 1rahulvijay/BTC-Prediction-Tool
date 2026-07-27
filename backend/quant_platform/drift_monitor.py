"""Small deterministic drift and calibration diagnostics."""
from __future__ import annotations

import math
from typing import Iterable


def brier_score(probabilities: Iterable[float], outcomes: Iterable[int]) -> float:
    pairs = list(zip(probabilities, outcomes, strict=True))
    if not pairs:
        raise ValueError("at least one resolved prediction is required")
    errors = []
    for probability, outcome in pairs:
        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            raise ValueError("probabilities must be finite and in [0, 1]")
        if outcome not in (0, 1):
            raise ValueError("outcomes must be binary")
        errors.append((probability - outcome) ** 2)
    return sum(errors) / len(errors)


def population_stability_index(
    reference: Iterable[float],
    current: Iterable[float],
    bins: int = 10,
    epsilon: float = 1e-6,
) -> float:
    ref = [float(value) for value in reference if math.isfinite(float(value))]
    cur = [float(value) for value in current if math.isfinite(float(value))]
    if len(ref) < bins or len(cur) < bins:
        raise ValueError("insufficient samples for requested bins")
    ordered = sorted(ref)
    edges = [
        ordered[min(len(ordered) - 1, int(len(ordered) * i / bins))]
        for i in range(1, bins)
    ]

    def counts(values: list[float]) -> list[int]:
        result = [0] * bins
        for value in values:
            index = 0
            while index < len(edges) and value > edges[index]:
                index += 1
            result[index] += 1
        return result

    ref_counts = counts(ref)
    cur_counts = counts(cur)
    score = 0.0
    for ref_count, cur_count in zip(ref_counts, cur_counts, strict=True):
        ref_ratio = max(epsilon, ref_count / len(ref))
        cur_ratio = max(epsilon, cur_count / len(cur))
        score += (cur_ratio - ref_ratio) * math.log(cur_ratio / ref_ratio)
    return score
