"""Small baseline hierarchy with train/calibration/policy/test separation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .temporal_split import FourWaySplit


@dataclass(slots=True)
class LockedBinaryPolicy:
    model_name: str
    model: object
    calibrator: IsotonicRegression
    threshold: float
    feature_columns: list[str]
    policy_score: float


def _models(seed: int) -> dict[str, object]:
    return {
        "logistic": make_pipeline(
            SimpleImputer(strategy="median"),
            StandardScaler(),
            LogisticRegression(max_iter=500, class_weight="balanced", random_state=seed),
        ),
        "hist_gradient_boosting": make_pipeline(
            SimpleImputer(strategy="median"),
            HistGradientBoostingClassifier(max_iter=100, max_leaf_nodes=15,
                                           learning_rate=0.06, l2_regularization=1.0,
                                           random_state=seed),
        ),
    }


def _positive_probability(model: object, x: pd.DataFrame) -> np.ndarray:
    probability = np.asarray(model.predict_proba(x), dtype=float)
    classes = np.asarray(model.classes_)
    matches = np.flatnonzero(classes == 1)
    if not len(matches):
        raise ValueError("binary model has no positive class")
    return probability[:, int(matches[0])]


def _fit_calibrator(raw: np.ndarray, y: np.ndarray) -> IsotonicRegression:
    if len(np.unique(y)) < 2 or len(np.unique(raw)) < 2:
        # Isotonic needs variation. A constant mapping is still honest and deterministic.
        raw = np.r_[raw, 0.0, 1.0]
        y = np.r_[y, float(np.mean(y)), float(np.mean(y))]
    return IsotonicRegression(out_of_bounds="clip").fit(raw, y)


def fit_locked_binary_policy(
    frame: pd.DataFrame,
    *,
    features: list[str],
    target: str,
    split: FourWaySplit,
    thresholds: list[float],
    policy_scorer: Callable[[np.ndarray, np.ndarray], float],
    seed: int,
) -> tuple[LockedBinaryPolicy, dict[str, dict[str, float]]]:
    x = frame[features].replace([np.inf, -np.inf], np.nan)
    y = pd.to_numeric(frame[target], errors="coerce").astype(int).to_numpy()
    if len(np.unique(y[split.train])) < 2:
        raise ValueError("training target has fewer than two classes")
    candidates: list[LockedBinaryPolicy] = []
    diagnostics: dict[str, dict[str, float]] = {}
    for name, model in _models(seed).items():
        model.fit(x.iloc[split.train], y[split.train])
        raw_cal = _positive_probability(model, x.iloc[split.calibration])
        calibrator = _fit_calibrator(raw_cal, y[split.calibration])
        raw_policy = _positive_probability(model, x.iloc[split.policy])
        p_policy = np.asarray(calibrator.predict(raw_policy), dtype=float)
        best_threshold = float(thresholds[0])
        best_score = -float("inf")
        for threshold in thresholds:
            actions = np.where(p_policy >= threshold, 1,
                               np.where(p_policy <= 1.0 - threshold, -1, 0))
            score = float(policy_scorer(actions, split.policy))
            if score > best_score:
                best_score = score
                best_threshold = float(threshold)
        diagnostics[name] = {
            "calibration_brier": float(brier_score_loss(y[split.calibration],
                                                        calibrator.predict(raw_cal))),
            "locked_threshold": best_threshold,
            "policy_score": best_score,
        }
        candidates.append(LockedBinaryPolicy(name, model, calibrator, best_threshold,
                                             features, best_score))
    selected = max(candidates, key=lambda candidate: candidate.policy_score)
    return selected, diagnostics


def score_locked_binary_policy(
    policy: LockedBinaryPolicy,
    frame: pd.DataFrame,
    target: str,
    indices: np.ndarray,
) -> dict[str, object]:
    x = frame[policy.feature_columns].replace([np.inf, -np.inf], np.nan).iloc[indices]
    y = pd.to_numeric(frame[target], errors="coerce").astype(int).to_numpy()[indices]
    raw = _positive_probability(policy.model, x)
    probability = np.asarray(policy.calibrator.predict(raw), dtype=float)
    actions = np.where(probability >= policy.threshold, 1,
                       np.where(probability <= 1.0 - policy.threshold, -1, 0))
    metrics = {
        "rows": int(len(indices)),
        "auc": float(roc_auc_score(y, probability)) if len(np.unique(y)) == 2 else None,
        "brier": float(brier_score_loss(y, probability)),
        "log_loss": float(log_loss(y, np.c_[1.0 - probability, probability], labels=[0, 1])),
        "positive_rate": float(y.mean()),
        "action_rate": float(np.mean(actions != 0)),
    }
    return {"probability": probability, "actions": actions, "target": y, "metrics": metrics}


def discriminator_auc(frame: pd.DataFrame, features: list[str], label: np.ndarray,
                      split_at: int, seed: int) -> dict[str, float | int | None]:
    x = frame[features].replace([np.inf, -np.inf], np.nan)
    y = np.asarray(label, dtype=int)
    train = np.arange(0, split_at)
    test = np.arange(split_at, len(frame))
    if len(np.unique(y[train])) < 2 or len(np.unique(y[test])) < 2:
        return {"auc": None, "train_rows": len(train), "test_rows": len(test)}
    model = _models(seed)["logistic"]
    model.fit(x.iloc[train], y[train])
    probability = _positive_probability(model, x.iloc[test])
    return {"auc": float(roc_auc_score(y[test], probability)),
            "train_rows": int(len(train)), "test_rows": int(len(test))}


def selftest() -> None:
    from .temporal_split import chronological_four_way_split

    rng = np.random.default_rng(1)
    frame = pd.DataFrame({"x": rng.normal(size=400)})
    frame["y"] = (frame["x"] + rng.normal(scale=0.3, size=400) > 0).astype(int)
    split = chronological_four_way_split(np.arange(400), purge_rows=1)
    policy, _ = fit_locked_binary_policy(
        frame, features=["x"], target="y", split=split, thresholds=[0.55, 0.65],
        policy_scorer=lambda actions, idx: float(np.mean(actions == np.where(frame["y"].to_numpy()[idx], 1, -1))),
        seed=1,
    )
    result = score_locked_binary_policy(policy, frame, "y", split.test)
    assert result["metrics"]["auc"] > 0.8

