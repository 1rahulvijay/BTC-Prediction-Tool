"""Constrained same-target stacking and regime-conditioned specialists."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from statistics import mean
from typing import Mapping, Sequence

import numpy as np

from .forecast_ledger import EvidenceKind
from .model_roles import ModelRole, TargetContract


class EnsembleMethod(StrEnum):
    EQUAL = "EQUAL"
    INVERSE_BRIER = "INVERSE_BRIER"
    CONSTRAINED = "CONSTRAINED"


@dataclass(frozen=True, slots=True)
class ForecastObservation:
    observed_at_ns: int
    outcome: int
    probabilities: Mapping[str, float]
    evidence_kind: EvidenceKind
    contract_key: str
    regime: str = "UNKNOWN"

    def __post_init__(self) -> None:
        if self.observed_at_ns <= 0 or self.outcome not in (0, 1):
            raise ValueError("invalid forecast observation")
        if not self.evidence_kind.meta_training_eligible:
            raise ValueError("stackers train only on OOF or forward forecasts")
        if not self.probabilities:
            raise ValueError("probabilities are required")
        if not self.contract_key.strip():
            raise ValueError("contract_key is required")
        if any(
            not math.isfinite(float(value)) or not 0 <= float(value) <= 1
            for value in self.probabilities.values()
        ):
            raise ValueError("model probabilities must be finite in [0, 1]")


@dataclass(frozen=True, slots=True)
class EnsembleFit:
    contract: TargetContract
    method: EnsembleMethod
    model_ids: tuple[str, ...]
    weights: tuple[float, ...]
    fitted_samples: int
    training_start_ns: int
    training_end_ns: int
    market_prior_model_id: str | None
    market_prior_minimum_weight: float

    def predict(
        self,
        probabilities: Mapping[str, float],
        *,
        forecast_at_ns: int,
    ) -> float:
        if forecast_at_ns <= self.training_end_ns:
            raise ValueError("ensemble forecast must occur after its training evidence")
        if set(probabilities) != set(self.model_ids):
            raise ValueError("prediction model set differs from fitted ensemble")
        values = [float(probabilities[name]) for name in self.model_ids]
        if any(not math.isfinite(value) or not 0 <= value <= 1 for value in values):
            raise ValueError("probabilities must be finite in [0, 1]")
        return float(sum(weight * value for weight, value in zip(self.weights, values)))


def _project_simplex(values: np.ndarray) -> np.ndarray:
    """Euclidean projection onto non-negative weights summing to one."""

    ordered = np.sort(values)[::-1]
    cumulative = np.cumsum(ordered)
    candidates = np.nonzero(
        ordered * np.arange(1, len(values) + 1) > (cumulative - 1.0)
    )[0]
    if len(candidates) == 0:
        return np.full_like(values, 1.0 / len(values))
    rho = int(candidates[-1])
    theta = (cumulative[rho] - 1.0) / (rho + 1)
    return np.maximum(values - theta, 0.0)


def _enforce_prior_floor(
    weights: np.ndarray,
    model_ids: Sequence[str],
    prior_id: str | None,
    floor: float,
) -> np.ndarray:
    if prior_id is None or floor <= 0:
        return weights
    if prior_id not in model_ids:
        raise ValueError("market prior is not present in model_ids")
    index = model_ids.index(prior_id)
    if weights[index] >= floor:
        return weights
    remainder = 1.0 - floor
    others = weights.copy()
    others[index] = 0.0
    total = float(others.sum())
    if total <= 0:
        others[:] = remainder / max(1, len(weights) - 1)
        others[index] = 0.0
    else:
        others *= remainder / total
    others[index] = floor
    return others


def fit_probability_ensemble(
    contract: TargetContract,
    observations: Sequence[ForecastObservation],
    *,
    method: EnsembleMethod = EnsembleMethod.CONSTRAINED,
    minimum_samples: int = 20,
    market_prior_model_id: str | None = None,
    market_prior_minimum_weight: float = 0.0,
    iterations: int = 2_000,
) -> EnsembleFit:
    if len(observations) < minimum_samples:
        raise ValueError(f"insufficient_meta_training_samples:{len(observations)}")
    if not 0 <= market_prior_minimum_weight <= 1:
        raise ValueError("market_prior_minimum_weight must be in [0, 1]")
    if contract.role is ModelRole.SETTLEMENT and (
        market_prior_model_id is None or market_prior_minimum_weight < 0.50
    ):
        raise ValueError(
            "settlement ensembles require a market prior with weight floor >= 0.50"
        )
    ordered = sorted(observations, key=lambda item: item.observed_at_ns)
    if any(item.contract_key != contract.key for item in ordered):
        raise ValueError("target_contract_mismatch in meta-training observations")
    model_ids = tuple(sorted(ordered[0].probabilities))
    if any(tuple(sorted(item.probabilities)) != model_ids for item in ordered):
        raise ValueError("all observations must contain the same model set")
    x = np.asarray(
        [[float(item.probabilities[name]) for name in model_ids] for item in ordered],
        dtype=np.float64,
    )
    y = np.asarray([item.outcome for item in ordered], dtype=np.float64)
    if method is EnsembleMethod.EQUAL:
        weights = np.full(len(model_ids), 1.0 / len(model_ids))
    elif method is EnsembleMethod.INVERSE_BRIER:
        losses = np.mean((x - y[:, None]) ** 2, axis=0)
        inverse = 1.0 / np.maximum(losses, 1e-8)
        weights = inverse / inverse.sum()
    elif method is EnsembleMethod.CONSTRAINED:
        weights = np.full(len(model_ids), 1.0 / len(model_ids))
        spectral = float(np.linalg.norm(x, ord=2) ** 2)
        learning_rate = min(0.25, len(ordered) / max(2.0 * spectral, 1e-9))
        for _ in range(iterations):
            gradient = 2.0 * x.T @ (x @ weights - y) / len(ordered)
            weights = _project_simplex(weights - learning_rate * gradient)
            weights = _enforce_prior_floor(
                weights,
                model_ids,
                market_prior_model_id,
                market_prior_minimum_weight,
            )
    else:
        raise ValueError(f"unsupported ensemble method:{method}")
    weights = _enforce_prior_floor(
        weights,
        model_ids,
        market_prior_model_id,
        market_prior_minimum_weight,
    )
    weights /= weights.sum()
    return EnsembleFit(
        contract=contract,
        method=method,
        model_ids=model_ids,
        weights=tuple(float(value) for value in weights),
        fitted_samples=len(ordered),
        training_start_ns=ordered[0].observed_at_ns,
        training_end_ns=ordered[-1].observed_at_ns,
        market_prior_model_id=market_prior_model_id,
        market_prior_minimum_weight=market_prior_minimum_weight,
    )


def _correlation(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("correlation inputs must have equal length >= 2")
    left_mean, right_mean = mean(left), mean(right)
    numerator = sum(
        (a - left_mean) * (b - right_mean) for a, b in zip(left, right)
    )
    left_var = sum((value - left_mean) ** 2 for value in left)
    right_var = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_var * right_var)
    return numerator / denominator if denominator > 0 else 0.0


def diversity_matrix(
    observations: Sequence[ForecastObservation],
    champion_model_id: str,
) -> list[dict[str, float | str]]:
    if len(observations) < 2:
        raise ValueError("diversity analysis requires at least two observations")
    model_ids = tuple(sorted(observations[0].probabilities))
    if champion_model_id not in model_ids:
        raise ValueError("champion_model_id is absent")
    outcomes = [float(item.outcome) for item in observations]
    champion = [
        float(item.probabilities[champion_model_id]) for item in observations
    ]
    champion_brier = mean(
        (prediction - outcome) ** 2
        for prediction, outcome in zip(champion, outcomes)
    )
    rows: list[dict[str, float | str]] = []
    for candidate_id in model_ids:
        if candidate_id == champion_model_id:
            continue
        candidate = [
            float(item.probabilities[candidate_id]) for item in observations
        ]
        champion_errors = [
            prediction - outcome
            for prediction, outcome in zip(champion, outcomes)
        ]
        candidate_errors = [
            prediction - outcome
            for prediction, outcome in zip(candidate, outcomes)
        ]
        blended = [(left + right) / 2.0 for left, right in zip(champion, candidate)]
        blended_brier = mean(
            (prediction - outcome) ** 2
            for prediction, outcome in zip(blended, outcomes)
        )
        rows.append(
            {
                "champion_model_id": champion_model_id,
                "candidate_model_id": candidate_id,
                "probability_correlation": _correlation(champion, candidate),
                "error_correlation": _correlation(
                    champion_errors, candidate_errors
                ),
                "incremental_brier_gain": champion_brier - blended_brier,
            }
        )
    return rows


def observations_from_ledger_rows(
    rows: Sequence[Mapping[str, object]],
    contract: TargetContract,
    *,
    minimum_models: int = 2,
) -> list[ForecastObservation]:
    """Pivot long ledger rows into causal same-candidate model panels."""

    if minimum_models < 2:
        raise ValueError("minimum_models must be at least two")
    groups: dict[tuple[int, str], list[Mapping[str, object]]] = {}
    for row in rows:
        key = (int(row["forecast_at_ns"]), str(row["candidate_id"]))
        groups.setdefault(key, []).append(row)
    result: list[ForecastObservation] = []
    for (observed_at_ns, _), group in sorted(groups.items()):
        outcomes = {float(item["actual_outcome"]) for item in group}
        evidence = {EvidenceKind(str(item["evidence_kind"])) for item in group}
        regimes = {str(item["regime"]) for item in group}
        if len(outcomes) != 1 or len(evidence) != 1 or len(regimes) != 1:
            raise ValueError("candidate rows disagree on outcome/evidence/regime")
        outcome = outcomes.pop()
        if outcome not in (0.0, 1.0):
            raise ValueError("probability ensemble requires a binary outcome")
        probabilities: dict[str, float] = {}
        for item in group:
            probability = item["predicted_probability"]
            if probability is None:
                continue
            model_key = f"{item['model_id']}:{item['model_version']}"
            if model_key in probabilities:
                raise ValueError("duplicate model forecast for candidate")
            probabilities[model_key] = float(probability)
        if len(probabilities) < minimum_models:
            continue
        result.append(
            ForecastObservation(
                observed_at_ns=observed_at_ns,
                outcome=int(outcome),
                probabilities=probabilities,
                evidence_kind=evidence.pop(),
                contract_key=contract.key,
                regime=regimes.pop(),
            )
        )
    return result


@dataclass(frozen=True, slots=True)
class RegimeMixture:
    global_fit: EnsembleFit
    regime_fits: Mapping[str, EnsembleFit]

    def predict(
        self,
        probabilities: Mapping[str, float],
        regime_probabilities: Mapping[str, float],
        *,
        forecast_at_ns: int,
    ) -> float:
        if not regime_probabilities:
            return self.global_fit.predict(
                probabilities, forecast_at_ns=forecast_at_ns
            )
        if any(
            not math.isfinite(float(value)) or float(value) < 0
            for value in regime_probabilities.values()
        ):
            raise ValueError("regime probabilities must be finite and non-negative")
        total = sum(float(value) for value in regime_probabilities.values())
        if total <= 0:
            return self.global_fit.predict(
                probabilities, forecast_at_ns=forecast_at_ns
            )
        result = 0.0
        for regime, value in regime_probabilities.items():
            fit = self.regime_fits.get(regime, self.global_fit)
            result += float(value) / total * fit.predict(
                probabilities, forecast_at_ns=forecast_at_ns
            )
        return result


def fit_regime_mixture(
    contract: TargetContract,
    observations: Sequence[ForecastObservation],
    *,
    method: EnsembleMethod = EnsembleMethod.CONSTRAINED,
    minimum_global_samples: int = 50,
    minimum_regime_samples: int = 30,
    market_prior_model_id: str | None = None,
    market_prior_minimum_weight: float = 0.0,
) -> RegimeMixture:
    global_fit = fit_probability_ensemble(
        contract,
        observations,
        method=method,
        minimum_samples=minimum_global_samples,
        market_prior_model_id=market_prior_model_id,
        market_prior_minimum_weight=market_prior_minimum_weight,
    )
    by_regime: dict[str, list[ForecastObservation]] = {}
    for item in observations:
        by_regime.setdefault(item.regime, []).append(item)
    fits = {
        regime: fit_probability_ensemble(
            contract,
            rows,
            method=method,
            minimum_samples=minimum_regime_samples,
            market_prior_model_id=market_prior_model_id,
            market_prior_minimum_weight=market_prior_minimum_weight,
        )
        for regime, rows in by_regime.items()
        if len(rows) >= minimum_regime_samples
    }
    return RegimeMixture(global_fit=global_fit, regime_fits=fits)
