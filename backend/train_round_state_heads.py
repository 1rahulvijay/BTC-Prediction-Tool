"""Train compact, deployable round-state SHADOW heads.

The original 180-day experiment proved several labels were predictable but did
not save serving artifacts. This trainer rebuilds those targets with a small
feature contract the live price-to-beat tracker can reproduce. Models that fail
their predeclared held-out AUC gate remain in the bundle for audit but are not
served.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesClassifier, HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("BTC_DATA_DIR", ROOT / "data"))
STATE_DIR = DATA / "research" / "round_state_stopping_180d_30s"
MATRIX = DATA / "research_matrix_1m.parquet"
OUT = DATA / "saved_models" / "round_state_heads.pkl"
METRICS_OUT = DATA / "research" / "round_state_live" / "metrics.csv"
TRAIN_DAYS_TAG = (os.environ.get("BTC_HISTORICAL_DAYS")
                  or os.environ.get("BTC_BACKFILL_DAYS") or "na")

VERSION = f"2026-07-02-round-state-shadow-v1-{TRAIN_DAYS_TAG}d"
HEAD_VERSION = VERSION

KEEPERS = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio", "shock_magnitude"]
SNAPSHOT_FEATURES = KEEPERS + [
    "seconds_left",
    "distance_usd",
    "abs_distance_usd",
    "range_so_far_usd",
    "recrosses_so_far",
    "time_above_so_far",
    "current_side_up",
]
OPPORTUNITY_FEATURES = KEEPERS
SNAPSHOT_TARGETS = ("future_side_flip", "late_shock_20", "late_shock_50", "late_shock_100")
OPPORTUNITY_TARGET = "next_opportunity_within_3_rounds"
AUC_GATES = {
    "future_side_flip": 0.75,
    "late_shock_20": 0.75,
    "late_shock_50": 0.78,
    "late_shock_100": 0.82,
    OPPORTUNITY_TARGET: 0.75,
}
REFERENCE_AUC = {
    5: {"future_side_flip": 0.8163, "late_shock_20": 0.8316, "late_shock_50": 0.8507,
        "late_shock_100": 0.8912, OPPORTUNITY_TARGET: 0.8369},
    15: {"future_side_flip": 0.8909, "late_shock_20": 0.8180, "late_shock_50": 0.8453,
         "late_shock_100": 0.8884, OPPORTUNITY_TARGET: 0.8123},
}


def _factories() -> dict[str, object]:
    return {
        "histgb": HistGradientBoostingClassifier(
            max_iter=180, learning_rate=0.05, max_leaf_nodes=15,
            min_samples_leaf=50, l2_regularization=2.0, class_weight="balanced",
            random_state=42,
        ),
        "extra_trees": ExtraTreesClassifier(
            n_estimators=180, max_depth=12, min_samples_leaf=30,
            max_features="sqrt", class_weight="balanced", n_jobs=4, random_state=42,
        ),
        "logreg": Pipeline([
            ("scale", StandardScaler()),
            ("model", LogisticRegression(class_weight="balanced", C=0.2, max_iter=1000)),
        ]),
    }


def _ece(probability: np.ndarray, actual: np.ndarray, bins: int = 10) -> float:
    probability = np.asarray(probability, float)
    actual = np.asarray(actual, int)
    edges = np.linspace(0.0, 1.0, bins + 1)
    value = 0.0
    for index in range(bins):
        mask = (probability >= edges[index]) & (
            probability < edges[index + 1] if index < bins - 1 else probability <= edges[index + 1]
        )
        if mask.any():
            value += float(mask.mean()) * abs(float(probability[mask].mean()) - float(actual[mask].mean()))
    return value


def _temporal_masks(frame: pd.DataFrame, outcome_end_column: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    rounds = np.sort(frame["round_start"].astype("int64").unique())
    if len(rounds) < 100:
        raise ValueError("not enough independent rounds")
    train_cut = rounds[int(len(rounds) * 0.70)]
    calibration_cut = rounds[int(len(rounds) * 0.85)]
    starts = frame["round_start"].to_numpy(np.int64)
    outcome_end = frame[outcome_end_column].to_numpy(np.int64)
    return (
        (starts < train_cut) & (outcome_end <= train_cut),
        (starts >= train_cut) & (starts < calibration_cut) & (outcome_end <= calibration_cut),
        starts >= calibration_cut,
    )


def _ensemble_probability(members: list[tuple[str, object]], values: np.ndarray) -> np.ndarray:
    return np.mean([model.predict_proba(values)[:, 1] for _, model in members], axis=0)


def _fit_head(frame: pd.DataFrame, features: list[str], target: str, horizon: int,
              outcome_end_column: str) -> tuple[dict, dict]:
    clean = frame.dropna(subset=features + [target, "round_start", outcome_end_column]).sort_values(
        "round_start").reset_index(drop=True)
    train, calibration, test = _temporal_masks(clean, outcome_end_column)
    x = clean[features].to_numpy(np.float32)
    y = clean[target].to_numpy(int)
    if any(len(np.unique(y[mask])) < 2 for mask in (train, calibration, test)):
        raise ValueError(f"{horizon}m/{target} lacks both classes in a temporal split")

    candidates: list[tuple[str, object, float]] = []
    for name, model in _factories().items():
        started = time.time()
        model.fit(x[train], y[train])
        probability = model.predict_proba(x[calibration])[:, 1]
        auc = float(roc_auc_score(y[calibration], probability))
        print(f"[round-state] {horizon}m {target} {name} validation_auc={auc:.4f} "
              f"elapsed={time.time() - started:.1f}s", flush=True)
        candidates.append((name, model, auc))
    candidates.sort(key=lambda value: value[2], reverse=True)
    members = [(name, model) for name, model, _ in candidates[:2]]
    raw_calibration = _ensemble_probability(members, x[calibration])
    calibrator = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    calibrator.fit(raw_calibration, y[calibration])
    raw_test = _ensemble_probability(members, x[test])
    probability = calibrator.predict(raw_test)
    auc = float(roc_auc_score(y[test], probability))
    gate = float(AUC_GATES[target])
    metrics = {
        "horizon": int(horizon),
        "target": target,
        "train_n": int(train.sum()),
        "calibration_n": int(calibration.sum()),
        "test_n": int(test.sum()),
        "test_base_rate": float(y[test].mean()),
        "test_auc": auc,
        "test_brier": float(brier_score_loss(y[test], probability)),
        "test_ece": float(_ece(probability, y[test])),
        "auc_gate": gate,
        "supported": bool(auc >= gate),
        "members": "+".join(name for name, _ in members),
        "reference_full_feature_auc": REFERENCE_AUC[horizon][target],
    }
    head = {
        "features": list(features),
        "members": members,
        "calibrator": calibrator,
        "metrics": metrics,
        "supported": metrics["supported"],
    }
    print(f"[round-state] {horizon}m {target} TEST auc={auc:.4f} gate={gate:.2f} "
          f"supported={metrics['supported']}", flush=True)
    return head, metrics


def _join_keepers(frame: pd.DataFrame, timestamp_column: str, matrix: pd.DataFrame) -> pd.DataFrame:
    # The 30-second research lane carries older features with some of the same
    # names. Serving uses live_keepers.py, whose contract matches the current
    # 1-minute research matrix, so matrix values must win without suffixes.
    work = frame.drop(columns=KEEPERS, errors="ignore").copy()
    work["feature_ts_ms"] = (
        work[timestamp_column].astype("int64") // 60_000 * 60_000
    )
    joined = work.merge(
        matrix.rename(columns={"ts_ms": "feature_ts_ms"}),
        on="feature_ts_ms",
        how="inner",
        validate="many_to_one",
    )
    return joined.replace([np.inf, -np.inf], np.nan)


def train(output: Path = OUT) -> dict:
    snapshots_path = STATE_DIR / "late_snapshots.parquet"
    transitions_path = STATE_DIR / "transition_drought.parquet"
    for path in (snapshots_path, transitions_path, MATRIX):
        if not path.exists():
            raise FileNotFoundError(path)
    matrix = pd.read_parquet(MATRIX, columns=["ts_ms"] + KEEPERS).drop_duplicates("ts_ms", keep="last")
    snapshots = _join_keepers(pd.read_parquet(snapshots_path), "snapshot_ts", matrix)
    transitions = _join_keepers(pd.read_parquet(transitions_path), "round_start", matrix)
    snapshots["outcome_end_ms"] = (
        snapshots["round_start"].astype("int64") + snapshots["horizon"].astype("int64") * 60_000
    )
    print(f"[round-state] joined snapshots={len(snapshots):,} transitions={len(transitions):,}", flush=True)

    bundle = {
        "version": VERSION,
        "trained_at": time.time(),
        "snapshot_features": SNAPSHOT_FEATURES,
        "opportunity_features": OPPORTUNITY_FEATURES,
        "snapshot_seconds_supported": [30, 120],
        "heads": {},
        "research_boundary": "shadow/info only; no Champion behavior",
    }
    metric_rows = []
    for horizon in (5, 15):
        bundle["heads"][horizon] = {}
        snapshot_h = snapshots[snapshots["horizon"] == horizon]
        for target in SNAPSHOT_TARGETS:
            head, metrics = _fit_head(
                snapshot_h, SNAPSHOT_FEATURES, target, horizon, "outcome_end_ms")
            bundle["heads"][horizon][target] = head
            metric_rows.append(metrics)
        transition_h = transitions[transitions["horizon"] == horizon]
        head, metrics = _fit_head(
            transition_h, OPPORTUNITY_FEATURES, OPPORTUNITY_TARGET, horizon,
            f"{OPPORTUNITY_TARGET}_outcome_end")
        bundle["heads"][horizon][OPPORTUNITY_TARGET] = head
        metric_rows.append(metrics)

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f"{output.name}.tmp.{os.getpid()}")
    joblib.dump(bundle, temporary)
    os.replace(temporary, output)
    METRICS_OUT.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(metric_rows).to_csv(METRICS_OUT, index=False)
    (METRICS_OUT.parent / "summary.json").write_text(json.dumps({
        "version": VERSION,
        "artifact": str(output),
        "metrics": metric_rows,
    }, indent=2), encoding="utf-8")
    print(f"[round-state] saved {output} ({output.stat().st_size / 1024 / 1024:.1f} MB)", flush=True)
    return bundle


def selftest() -> None:
    assert set(SNAPSHOT_TARGETS).issubset(AUC_GATES)
    assert set(KEEPERS).issubset(SNAPSHOT_FEATURES)
    assert AUC_GATES[OPPORTUNITY_TARGET] >= 0.5
    print("ROUND STATE TRAINER SELFTEST PASS")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    selftest()
    if not args.selftest:
        try:
            train(args.output)
        except FileNotFoundError as exc:
            # Optional shadow head: a clean clone may not carry the large 180-day
            # research lane. Startup must continue without inventing an artifact.
            print(f"[round-state] optional training skipped; missing source: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
