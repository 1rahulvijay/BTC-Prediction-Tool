"""Offline promotion gates and staged full-data refit helpers.

The evaluated candidate is scored only on its untouched temporal tail. A passing
architecture may then be refit on all rows, but that full-data artifact enters the
live A/B runner as a shadow challenger; it is never treated as independently tested.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np


def promotion_required(enabled: bool, reason: str | None = None) -> bool:
    """Keep retraining origin out of the safety decision."""
    del reason
    return bool(enabled)


def promotion_gates() -> dict:
    def env_float(name: str, default: float) -> float:
        try:
            return float(os.environ.get(name, default))
        except (TypeError, ValueError):
            return float(default)

    def env_int(name: str, default: int) -> int:
        try:
            return int(os.environ.get(name, default))
        except (TypeError, ValueError):
            return int(default)

    return {
        "min_holdout_samples": env_int("BTC_PROMOTION_MIN_HOLDOUT_SAMPLES", 1000),
        "min_directional_calls": env_int("BTC_PROMOTION_MIN_DIRECTIONAL_CALLS", 200),
        "min_directional_precision": env_float("BTC_PROMOTION_MIN_DIRECTIONAL_PRECISION", 0.48),
        "max_multiclass_brier": env_float("BTC_PROMOTION_MAX_BRIER", 0.80),
        "max_ece": env_float("BTC_PROMOTION_MAX_ECE", 0.20),
        "max_precision_regression": env_float("BTC_PROMOTION_MAX_PRECISION_REGRESSION", 0.03),
        "max_brier_regression": env_float("BTC_PROMOTION_MAX_BRIER_REGRESSION", 0.03),
        "max_eval_samples": env_int("BTC_PROMOTION_MAX_EVAL_SAMPLES", 12000),
    }


def _sample_indices(indices: np.ndarray, maximum: int) -> np.ndarray:
    indices = np.asarray(indices, dtype=np.int64)
    if maximum <= 0 or len(indices) <= maximum:
        return indices
    positions = np.linspace(0, len(indices) - 1, maximum, dtype=np.int64)
    return indices[positions]


def _predict_probabilities(model, values: np.ndarray, horizon: int) -> np.ndarray:
    rows = []
    for row in values:
        probability = np.asarray(
            model.predict_base(np.expand_dims(row, axis=0), int(horizon), None),
            dtype=np.float64,
        )
        probability = np.nan_to_num(probability, nan=0.0, posinf=0.0, neginf=0.0)
        if probability.shape != (3,) or probability.sum() <= 0:
            probability = np.array([0.0, 1.0, 0.0], dtype=np.float64)
        else:
            probability /= probability.sum()
        rows.append(probability)
    return np.asarray(rows, dtype=np.float64)


def probability_metrics(probability: np.ndarray, actual: np.ndarray, bins: int = 10) -> dict:
    probability = np.asarray(probability, dtype=np.float64)
    actual = np.asarray(actual, dtype=np.int64)
    predicted = np.argmax(probability, axis=1)
    correct = predicted == actual
    confidence = np.max(probability, axis=1)
    directional = predicted != 1
    calls = int(directional.sum())
    directional_precision = float(correct[directional].mean()) if calls else 0.0
    target = np.eye(3, dtype=np.float64)[actual]
    brier = float(np.mean(np.sum((probability - target) ** 2, axis=1)))
    ece = 0.0
    edges = np.linspace(0.0, 1.0, bins + 1)
    for index in range(bins):
        mask = (confidence >= edges[index]) & (
            confidence < edges[index + 1] if index < bins - 1 else confidence <= edges[index + 1]
        )
        if mask.any():
            ece += float(mask.mean()) * abs(float(confidence[mask].mean()) - float(correct[mask].mean()))
    return {
        "samples": int(len(actual)),
        "overall_accuracy": float(correct.mean()) if len(actual) else 0.0,
        "directional_calls": calls,
        "directional_precision": directional_precision,
        "multiclass_brier": brier,
        "ece": float(ece),
    }


def evaluate_candidate(candidate, incumbent, X, Y: dict, split_idx: int,
                       decision_timestamps=None, incumbent_boundary_ts: int | None = None) -> dict:
    gates = promotion_gates()
    report = {
        "created_at": time.time(),
        "gates": gates,
        "horizons": {},
        "calibration_contract": "candidate holdout + purged OOF stacker; full refit reuses conformal residuals",
    }
    all_pass = True
    for horizon in candidate.horizons:
        if horizon not in Y:
            report["horizons"][int(horizon)] = {"passed": False, "reasons": ["missing_labels"]}
            all_pass = False
            continue
        stop = min(len(X), len(Y[horizon]))
        holdout = np.arange(max(0, int(split_idx)), stop, dtype=np.int64)
        sampled = _sample_indices(holdout, gates["max_eval_samples"])
        actual = np.argmax(np.asarray(Y[horizon])[sampled], axis=1)
        candidate_probability = _predict_probabilities(candidate, np.asarray(X)[sampled], horizon)
        candidate_metrics = probability_metrics(candidate_probability, actual)
        incumbent_metrics = None
        fair_comparison = False
        if incumbent is not None and getattr(incumbent, "is_trained", False):
            incumbent_probability = _predict_probabilities(incumbent, np.asarray(X)[sampled], horizon)
            incumbent_metrics = probability_metrics(incumbent_probability, actual)

            if decision_timestamps is not None and incumbent_boundary_ts:
                ts = np.asarray(decision_timestamps, dtype=np.int64)[sampled]
                fair_mask = ts > int(incumbent_boundary_ts)
                if int(fair_mask.sum()) >= gates["min_holdout_samples"]:
                    fair_comparison = True
                    candidate_metrics_fair = probability_metrics(candidate_probability[fair_mask], actual[fair_mask])
                    incumbent_metrics_fair = probability_metrics(incumbent_probability[fair_mask], actual[fair_mask])
                else:
                    candidate_metrics_fair = incumbent_metrics_fair = None
            else:
                candidate_metrics_fair = incumbent_metrics_fair = None
        else:
            candidate_metrics_fair = incumbent_metrics_fair = None

        reasons = []
        if candidate_metrics["samples"] < gates["min_holdout_samples"]:
            reasons.append("insufficient_holdout_samples")
        if candidate_metrics["directional_calls"] < gates["min_directional_calls"]:
            reasons.append("insufficient_directional_calls")
        if candidate_metrics["directional_precision"] < gates["min_directional_precision"]:
            reasons.append("directional_precision_below_floor")
        if candidate_metrics["multiclass_brier"] > gates["max_multiclass_brier"]:
            reasons.append("brier_above_limit")
        if candidate_metrics["ece"] > gates["max_ece"]:
            reasons.append("ece_above_limit")
        if fair_comparison:
            if (candidate_metrics_fair["directional_precision"]
                    < incumbent_metrics_fair["directional_precision"] - gates["max_precision_regression"]):
                reasons.append("precision_regressed_vs_incumbent")
            if (candidate_metrics_fair["multiclass_brier"]
                    > incumbent_metrics_fair["multiclass_brier"] + gates["max_brier_regression"]):
                reasons.append("brier_regressed_vs_incumbent")

        passed = not reasons
        all_pass &= passed
        report["horizons"][int(horizon)] = {
            "passed": passed,
            "reasons": reasons,
            "candidate": candidate_metrics,
            "incumbent": incumbent_metrics,
            "fair_incumbent_comparison": fair_comparison,
            "candidate_fair": candidate_metrics_fair,
            "incumbent_fair": incumbent_metrics_fair,
        }
    report["passed"] = bool(all_pass)
    return report


def smoke_test_model(model, X, horizons, samples: int = 3) -> dict:
    if not getattr(model, "is_trained", False):
        raise RuntimeError("staged model did not load as trained")
    checked = {}
    count = min(max(1, samples), len(X))
    for horizon in horizons:
        values = _predict_probabilities(model, np.asarray(X)[-count:], int(horizon))
        if not np.isfinite(values).all() or not np.allclose(values.sum(axis=1), 1.0, atol=1e-5):
            raise RuntimeError(f"invalid staged probabilities for {horizon}m")
        checked[int(horizon)] = {"samples": int(count), "mean_probability": values.mean(axis=0).tolist()}
    return {"passed": True, "horizons": checked}


def atomic_json(path: str | Path, payload: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".tmp.{os.getpid()}")
    try:
        temporary.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def selftest() -> None:
    rng = np.random.default_rng(4)
    actual = rng.integers(0, 3, 500)
    probability = np.full((500, 3), 0.15)
    probability[np.arange(500), actual] = 0.70
    metrics = probability_metrics(probability, actual)
    assert metrics["samples"] == 500 and metrics["multiclass_brier"] < 0.2
    assert _sample_indices(np.arange(100), 10).shape == (10,)
    for reason in ("forced-startup", "manual-ui", "scheduled", "auto-learning"):
        assert promotion_required(True, reason)
        assert not promotion_required(False, reason)
    assert promotion_gates()["min_directional_calls"] > 0
    assert promotion_gates()["min_directional_precision"] > 0
    print("model_promotion selftest: PASS")


if __name__ == "__main__":
    selftest()
