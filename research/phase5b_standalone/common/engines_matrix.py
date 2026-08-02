"""BTC state novelty, robustness, information-clock and path-structure tests."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.mixture import GaussianMixture
from sklearn.neighbors import NearestNeighbors
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from research.phase5_standalone.common.engine_types import EngineContext, EngineResult
from research.phase5_standalone.common.engines_matrix import _binary_economics
from research.phase5_standalone.common.metrics import EMPTY_ECONOMICS, economic_verdict
from research.phase5_standalone.common.modeling import (
    fit_locked_binary_policy,
    score_locked_binary_policy,
)
from research.phase5_standalone.common.temporal_split import chronological_four_way_split

from .data import load_contract


def _load_matrix(context: EngineContext) -> tuple[pd.DataFrame, dict, dict]:
    loaded = load_contract(context.data_dir, context.protocol.payload["data_contract"],
                           context.maximum_rows)
    frame = loaded.frame.copy()
    numeric = [column for column in frame.columns if column not in {"timestamp"}]
    for column in numeric:
        if column != "_ts_ms":
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.replace([np.inf, -np.inf], np.nan).sort_values("_ts_ms").reset_index(drop=True)
    frame["_return"] = frame["ret_5m"] / frame["close"]
    frame["_direction"] = (frame["_return"] > 0).astype(int)
    return frame, loaded.identity, loaded.causal_summary


def _split(context: EngineContext, frame: pd.DataFrame, purge: int = 15):
    return chronological_four_way_split(frame["_ts_ms"], purge_rows=purge,
                                        **context.split_args)


def _feature_list(context: EngineContext, frame: pd.DataFrame) -> list[str]:
    requested = list(context.protocol.payload["method"].get("features", []))
    return [column for column in requested if column in frame]


def _predictive_test(context: EngineContext, frame: pd.DataFrame, *, features: list[str],
                     target: str, split) -> tuple[dict[str, Any], Any, dict[str, Any]]:
    clean = frame[features].replace([np.inf, -np.inf], np.nan)
    if len(np.unique(frame[target].to_numpy(int)[split.train])) < 2:
        raise ValueError("training target has one class")

    def score(actions: np.ndarray, indices: np.ndarray) -> float:
        truth = np.where(frame[target].to_numpy(int)[indices] == 1, 1, -1)
        active = actions != 0
        return float(np.mean(actions[active] == truth[active])) if active.any() else -1.0

    policy, selection = fit_locked_binary_policy(
        frame.assign(**{column: clean[column] for column in features}),
        features=features,
        target=target,
        split=split,
        thresholds=[0.50, 0.55, 0.60, 0.65],
        policy_scorer=score,
        seed=context.seed,
    )
    result = score_locked_binary_policy(policy, frame, target, split.test)
    diagnostics = {
        "locked_model": policy.model_name,
        "locked_threshold": policy.threshold,
        "selection": selection,
        "untouched_test": result["metrics"],
        "features": features,
    }
    return diagnostics, policy, result


def _diagnostic_verdict(diagnostics: dict[str, Any], reason: str) -> tuple[str, list[str]]:
    auc = diagnostics.get("untouched_test", {}).get("auc")
    if auc is None or auc < 0.55:
        return "FAIL_NO_EDGE", [f"untouched AUC {auc} did not clear 0.55"]
    return "FAIL_UNSTABLE", [reason]


def _novelty(context: EngineContext, frame: pd.DataFrame, identity: dict, causal: dict) -> EngineResult:
    features = _feature_list(context, frame)
    frame = frame.dropna(subset=["_return", *features]).reset_index(drop=True)
    split = _split(context, frame, 5)
    base_diag, locked, base_score = _predictive_test(
        context, frame, features=features, target="_direction", split=split)
    scaler = StandardScaler().fit(frame[features].iloc[split.train])
    train_x = scaler.transform(frame[features].iloc[split.train])
    detector = IsolationForest(n_estimators=100, contamination="auto",
                               random_state=context.seed, n_jobs=1).fit(train_x)
    novelty = -detector.score_samples(scaler.transform(frame[features]))
    quantiles = [0.80, 0.90, 0.95]
    policy_values = novelty[split.policy]
    returns = frame["_return"].to_numpy(float)
    cost = 9.0 * context.cost_multiplier / 10_000.0
    # Re-score policy probabilities without changing the frozen direction policy.
    policy_actions = score_locked_binary_policy(locked, frame, "_direction", split.policy)["actions"]
    thresholds = {q: float(np.quantile(novelty[split.train], q)) for q in quantiles}
    scores = {}
    for q, threshold in thresholds.items():
        actions = np.where(policy_values <= threshold, policy_actions, 0)
        active = actions != 0
        scores[q] = float(np.sum(returns[split.policy][active] * actions[active] - cost))
    selected_q = max(scores, key=scores.get)
    selected_threshold = thresholds[selected_q]
    test_actions = base_score["actions"].copy()
    gated_actions = np.where(novelty[split.test] <= selected_threshold, test_actions, 0)
    base_econ, _ = _binary_economics(context, frame, test_actions, split.test, "_return",
                                     hold_seconds=300)
    gated_econ, control = _binary_economics(context, frame, gated_actions, split.test, "_return",
                                            hold_seconds=300)
    status, reasons = economic_verdict(gated_econ, context.protocol.payload["promotion_gates"])
    lift = float((gated_econ.get("net_pnl") or 0) - (base_econ.get("net_pnl") or 0))
    if status == "PASS_CANDIDATE" and lift <= 0:
        status, reasons = "FAIL_NO_EDGE", ["novelty veto did not improve unchanged base strategy"]
    diagnostics = {
        "base_model": base_diag,
        "novelty_model": "IsolationForest",
        "locked_training_quantile": selected_q,
        "locked_novelty_threshold": selected_threshold,
        "policy_net_by_quantile": {str(k): v for k, v in scores.items()},
        "base_untouched_economics": base_econ,
        "gated_minus_base_net_pnl": lift,
        "control": control,
    }
    return EngineResult(status, "Novelty veto against an unchanged direction policy",
                        diagnostics, gated_econ, reasons, identity, causal, split.boundaries)


def _local_support(context: EngineContext, frame: pd.DataFrame, identity: dict,
                   causal: dict) -> EngineResult:
    features = _feature_list(context, frame)
    frame = frame.dropna(subset=["_return", *features]).reset_index(drop=True)
    split = _split(context, frame, 5)
    scaler = StandardScaler().fit(frame[features].iloc[split.train])
    train_x = scaler.transform(frame[features].iloc[split.train])
    test_x = scaler.transform(frame[features].iloc[split.test])
    y_train = frame["_direction"].to_numpy(int)[split.train]
    k = min(51, max(5, int(np.sqrt(len(train_x)))))
    neighbours = NearestNeighbors(n_neighbors=k, algorithm="auto", n_jobs=1).fit(train_x)
    raw_distance, nearest = neighbours.kneighbors(test_x, return_distance=True)
    probability = np.mean(y_train[nearest], axis=1)
    distance = np.mean(raw_distance, axis=1)
    y_test = frame["_direction"].to_numpy(int)[split.test]
    auc = float(roc_auc_score(y_test, probability)) if len(np.unique(y_test)) == 2 else None
    confidence = np.abs(probability - 0.5) * 2
    quartiles = np.quantile(distance, [0.25, 0.5, 0.75])
    buckets = {}
    for index, (low, high) in enumerate(zip([-np.inf, *quartiles], [*quartiles, np.inf]), 1):
        mask = (distance > low) & (distance <= high)
        buckets[f"Q{index}"] = {
            "rows": int(mask.sum()),
            "mean_distance": float(distance[mask].mean()),
            "accuracy": float(np.mean((probability[mask] >= 0.5) == y_test[mask])),
            "mean_confidence": float(confidence[mask].mean()),
        }
    diagnostics = {
        "neighbours": k,
        "untouched_auc": auc,
        "support_distance_buckets": buckets,
        "analogue_count_definition": f"{k} nearest training states",
    }
    status = "FAIL_UNSTABLE" if auc is not None and auc >= 0.55 else "FAIL_NO_EDGE"
    reasons = ["local support is diagnostic until attached to a frozen economic base policy"]
    return EngineResult(status, "Local analogue support on untouched states", diagnostics,
                        dict(EMPTY_ECONOMICS), reasons, identity, causal, split.boundaries)


def _environment_labels(frame: pd.DataFrame, train_idx: np.ndarray) -> dict[str, np.ndarray]:
    timestamp = pd.to_datetime(frame["_ts_ms"], unit="ms", utc=True)
    vol_cut = float(frame["rv_15m"].iloc[train_idx].median())
    trend = frame["close"].pct_change(60).fillna(0).to_numpy()
    return {
        "month": timestamp.dt.to_period("M").astype(str).to_numpy(),
        "volatility": np.where(frame["rv_15m"].to_numpy(float) > vol_cut, "HIGH", "LOW"),
        "weekpart": np.where(timestamp.dt.dayofweek.to_numpy() >= 5, "WEEKEND", "WEEKDAY"),
        "session": np.where(timestamp.dt.hour.to_numpy() < 8, "ASIA",
                            np.where(timestamp.dt.hour.to_numpy() < 16, "EU", "US")),
        "trend": np.where(trend >= 0, "BULL", "BEAR"),
    }


def _feature_sign_stability(context: EngineContext, frame: pd.DataFrame, identity: dict,
                            causal: dict) -> EngineResult:
    features = _feature_list(context, frame)
    frame = frame.dropna(subset=["_return", *features]).reset_index(drop=True)
    split = _split(context, frame, 5)
    environments = _environment_labels(frame, split.train)
    target = np.where(frame["_direction"].to_numpy(int) == 1, 1.0, -1.0)
    results = {}
    reversal_total = 0
    for feature in features:
        effects = []
        x = frame[feature].to_numpy(float)
        for family, labels in environments.items():
            for label in np.unique(labels[split.test]):
                idx = split.test[labels[split.test] == label]
                if len(idx) < 30 or np.std(x[idx]) == 0:
                    continue
                effect = float(np.corrcoef(x[idx], target[idx])[0, 1])
                effects.append((f"{family}:{label}", effect))
        positive = sum(effect > 0.02 for _, effect in effects)
        negative = sum(effect < -0.02 for _, effect in effects)
        neutral = len(effects) - positive - negative
        reversals = min(positive, negative)
        reversal_total += reversals
        results[feature] = {
            "positive_environments": positive,
            "negative_environments": negative,
            "neutral_environments": neutral,
            "sign_reversal_count": reversals,
            "worst_effect": min((effect for _, effect in effects), default=None),
            "best_effect": max((effect for _, effect in effects), default=None),
        }
    diagnostics = {"features": results, "total_sign_reversals": reversal_total,
                   "environment_families": sorted(environments)}
    status = "FAIL_UNSTABLE" if reversal_total else "FAIL_NO_EDGE"
    reasons = ["signs are descriptive on the untouched period and cannot retire features directly"]
    return EngineResult(status, "Feature sign stability across frozen environments", diagnostics,
                        dict(EMPTY_ECONOMICS), reasons, identity, causal, split.boundaries)


def _worst_environment(context: EngineContext, frame: pd.DataFrame, identity: dict,
                       causal: dict) -> EngineResult:
    features = _feature_list(context, frame)
    frame = frame.dropna(subset=["_return", *features]).reset_index(drop=True)
    split = _split(context, frame, 5)
    environments = _environment_labels(frame, split.train)["volatility"]
    x = frame[features]
    y = frame["_direction"].to_numpy(int)
    train_env = environments[split.train]
    counts = pd.Series(train_env).value_counts().to_dict()
    weights = np.array([1.0 / counts[value] for value in train_env], dtype=float)
    weights *= len(weights) / weights.sum()
    models = {}
    for name, weight in [("average_loss", None), ("environment_balanced", weights)]:
        pipe = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(),
                             LogisticRegression(max_iter=500, class_weight="balanced",
                                                random_state=context.seed))
        kwargs = {"logisticregression__sample_weight": weight} if weight is not None else {}
        pipe.fit(x.iloc[split.train], y[split.train], **kwargs)
        probability = pipe.predict_proba(x.iloc[split.test])[:, 1]
        rows = {}
        for env in np.unique(environments[split.test]):
            mask = environments[split.test] == env
            rows[str(env)] = {
                "rows": int(mask.sum()),
                "brier": float(brier_score_loss(y[split.test][mask], probability[mask])),
                "accuracy": float(np.mean((probability[mask] >= 0.5) == y[split.test][mask])),
            }
        models[name] = {
            "environments": rows,
            "worst_accuracy": min(row["accuracy"] for row in rows.values()),
            "worst_brier": max(row["brier"] for row in rows.values()),
        }
    lift = models["environment_balanced"]["worst_accuracy"] - models["average_loss"]["worst_accuracy"]
    status = "FAIL_UNSTABLE" if lift > 0 else "FAIL_NO_EDGE"
    return EngineResult(status, "Worst-environment model-selection diagnostic",
                        {"models": models, "worst_accuracy_lift": float(lift)},
                        dict(EMPTY_ECONOMICS),
                        ["no executable threshold policy was established"], identity, causal,
                        split.boundaries)


def _feature_drift(context: EngineContext, frame: pd.DataFrame, identity: dict,
                   causal: dict) -> EngineResult:
    features = _feature_list(context, frame)
    frame = frame.dropna(subset=["_return", *features]).reset_index(drop=True)
    split = _split(context, frame, 5)
    month = pd.to_datetime(frame["_ts_ms"], unit="ms", utc=True).dt.to_period("M").astype(str)
    target = np.where(frame["_direction"] == 1, 1.0, -1.0)
    rows = {}
    for feature in features:
        monthly = []
        for label in month.unique():
            idx = np.flatnonzero(month.to_numpy() == label)
            if len(idx) < 100 or frame[feature].iloc[idx].std() == 0:
                continue
            monthly.append({"month": label, "effect": float(np.corrcoef(
                frame[feature].to_numpy(float)[idx], target[idx])[0, 1]), "rows": int(len(idx))})
        effects = np.array([item["effect"] for item in monthly], dtype=float)
        rows[feature] = {
            "monthly": monthly,
            "sign_changes": int(np.sum(np.sign(effects[1:]) != np.sign(effects[:-1]))) if len(effects) > 1 else 0,
            "latest_effect": float(effects[-1]) if len(effects) else None,
            "effect_slope": float(np.polyfit(np.arange(len(effects)), effects, 1)[0]) if len(effects) >= 3 else None,
        }
    return EngineResult("FAIL_UNSTABLE", "Frozen monthly feature-value drift diagnostic",
                        {"feature_drift": rows}, dict(EMPTY_ECONOMICS),
                        ["rolling usefulness is monitoring evidence, not a trading policy"],
                        identity, causal, split.boundaries)


def _derived_path_features(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    returns = result["close"].pct_change().fillna(0.0)
    path = returns.abs().rolling(15, min_periods=5).sum()
    net = result["close"].pct_change(15).abs()
    result["path_efficiency"] = (net / path.replace(0, np.nan)).fillna(0.0)
    signs = np.sign(returns.to_numpy())
    turns = np.r_[0, (signs[1:] != signs[:-1]).astype(float)]
    result["turn_count_15"] = pd.Series(turns).rolling(15, min_periods=5).sum().fillna(0.0)
    mean = result["close"].rolling(15, min_periods=5).mean()
    centered = np.sign((result["close"] - mean).fillna(0.0).to_numpy())
    crossings = np.r_[0, (centered[1:] != centered[:-1]).astype(float)]
    result["crossing_count_15"] = pd.Series(crossings).rolling(15, min_periods=5).sum().fillna(0.0)
    result["roughness"] = path / net.replace(0, np.nan)
    result["vol_of_vol"] = result["rv_15m"].diff().rolling(15, min_periods=5).std()
    result["future_path"] = returns.shift(-1).abs().rolling(5).sum().shift(-4)
    result["future_efficiency"] = (result["ret_5m"].abs() / result["close"]) / result["future_path"].replace(0, np.nan)
    return result.replace([np.inf, -np.inf], np.nan)


def _generic_predictive(context: EngineContext, frame: pd.DataFrame, identity: dict,
                        causal: dict, mode: str) -> EngineResult:
    frame = _derived_path_features(frame)
    base = _feature_list(context, frame)
    if mode == "information_clock":
        features = [column for column in ["trade_count", "volume", "count_accel_5m", "vol_accel",
                                          "vpin_15m", "rv_term", "shock_magnitude"] if column in frame]
        target_source = frame["future_abs_move_5m"]
    elif mode == "information_exhaustion":
        features = [column for column in ["trade_count", "volume", "count_accel_5m", "vol_accel",
                                          "vpin_15m", "shock_magnitude", "rv_15m", "rv_term"] if column in frame]
        target_source = -frame["future_abs_move_5m"]
    elif mode == "path_efficiency":
        features = ["path_efficiency", "turn_count_15", "crossing_count_15", "roughness", *base]
        target_source = frame["future_efficiency"]
    elif mode == "path_roughness":
        features = ["path_efficiency", "turn_count_15", "crossing_count_15", "roughness", *base]
        target_source = frame["future_abs_move_5m"]
    elif mode == "volatility_of_volatility":
        features = ["vol_of_vol", "rv_15m", "rv_30m", "rv_60m", "rv_term", "vol_accel"]
        target_source = frame["shock_magnitude"]
    else:
        raise ValueError(f"unknown matrix predictive mode {mode}")
    features = list(dict.fromkeys(column for column in features if column in frame))
    frame = frame.dropna(subset=[*features, target_source.name]).reset_index(drop=True)
    split = _split(context, frame, 15)
    threshold = float(np.nanmedian(target_source.reindex(frame.index).to_numpy()[split.train]))
    # Recompute from the retained frame to avoid using indices from pre-drop data.
    if mode == "information_exhaustion":
        source = -frame["future_abs_move_5m"].to_numpy(float)
    elif mode == "path_efficiency":
        source = frame["future_efficiency"].to_numpy(float)
    elif mode in {"information_clock", "path_roughness"}:
        source = frame["future_abs_move_5m"].to_numpy(float)
    else:
        source = frame["shock_magnitude"].to_numpy(float)
    threshold = float(np.median(source[split.train]))
    frame["_target"] = (source > threshold).astype(int)
    diagnostics, _, _ = _predictive_test(context, frame, features=features,
                                         target="_target", split=split)
    diagnostics["target_threshold_from_train"] = threshold
    status, reasons = _diagnostic_verdict(
        diagnostics, "predictive lift has not been converted to an executable direction policy")
    return EngineResult(status, f"{mode.replace('_', ' ').title()} predictive diagnostic",
                        diagnostics, dict(EMPTY_ECONOMICS), reasons, identity, causal,
                        split.boundaries)


def _regime_discovery(context: EngineContext, frame: pd.DataFrame, identity: dict,
                      causal: dict, mode: str) -> EngineResult:
    features = _feature_list(context, frame)
    frame = frame.dropna(subset=["_return", *features]).reset_index(drop=True)
    split = _split(context, frame, 5)
    scaler = StandardScaler().fit(frame[features].iloc[split.train])
    x_train = scaler.transform(frame[features].iloc[split.train])
    x_all = scaler.transform(frame[features])
    candidates = {}
    assignments = {}
    for name, model in {
        "kmeans": KMeans(n_clusters=4, n_init=10, random_state=context.seed),
        "gaussian_mixture": GaussianMixture(n_components=4, covariance_type="diag",
                                             random_state=context.seed),
    }.items():
        model.fit(x_train)
        labels = model.predict(x_all)
        assignments[name] = labels
        test_labels = labels[split.test]
        returns = frame["_return"].to_numpy(float)[split.test]
        state_rows = {}
        for state in np.unique(test_labels):
            mask = test_labels == state
            state_rows[str(int(state))] = {
                "rows": int(mask.sum()),
                "mean_return": float(returns[mask].mean()),
                "mean_abs_return": float(np.abs(returns[mask]).mean()),
                "persistence": float(np.mean(test_labels[1:][test_labels[:-1] == state] == state))
                if np.any(test_labels[:-1] == state) else None,
            }
        separation = float(np.std([row["mean_abs_return"] for row in state_rows.values()]))
        candidates[name] = {"states": state_rows, "future_outcome_separation": separation}
    selected = max(candidates, key=lambda name: candidates[name]["future_outcome_separation"])
    if mode == "state_transition":
        labels = assignments[selected]
        train_pairs = defaultdict(lambda: defaultdict(int))
        for left, right in zip(labels[split.train][:-1], labels[split.train][1:]):
            train_pairs[int(left)][int(right)] += 1
        transition = {str(left): {str(right): count for right, count in rows.items()}
                      for left, rows in train_pairs.items()}
        test = labels[split.test]
        predicted = []
        for left in test[:-1]:
            choices = train_pairs[int(left)]
            predicted.append(max(choices, key=choices.get) if choices else int(left))
        accuracy = float(np.mean(np.asarray(predicted) == test[1:])) if predicted else None
        diagnostics = {"selected_clustering": selected, "transition_counts": transition,
                       "untouched_next_state_accuracy": accuracy, "models": candidates}
    else:
        diagnostics = {"selected_clustering": selected, "models": candidates}
    return EngineResult("FAIL_UNSTABLE", "Data-derived state diagnostic",
                        diagnostics, dict(EMPTY_ECONOMICS),
                        ["cluster separation alone cannot authorize a strategy"], identity,
                        causal, split.boundaries)


def run_matrix_research(context: EngineContext) -> EngineResult:
    frame, identity, causal = _load_matrix(context)
    mode = str(context.protocol.payload["method"]["mode"])
    if mode == "novelty":
        return _novelty(context, frame, identity, causal)
    if mode == "local_support":
        return _local_support(context, frame, identity, causal)
    if mode == "feature_sign_stability":
        return _feature_sign_stability(context, frame, identity, causal)
    if mode == "worst_environment":
        return _worst_environment(context, frame, identity, causal)
    if mode == "feature_drift":
        return _feature_drift(context, frame, identity, causal)
    if mode in {"information_clock", "information_exhaustion", "path_efficiency",
                "path_roughness", "volatility_of_volatility"}:
        return _generic_predictive(context, frame, identity, causal, mode)
    if mode in {"online_regime", "state_transition"}:
        return _regime_discovery(context, frame, identity, causal, mode)
    raise ValueError(f"unknown matrix-research mode {mode}")
