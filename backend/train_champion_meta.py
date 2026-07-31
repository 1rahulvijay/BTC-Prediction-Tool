"""
Train the champion meta-model from live champion snapshots.

This is intentionally data-gated. The rules-first champion can run immediately,
but a learned meta-champion should not be activated until the app has enough
resolved live snapshots to prove which specialist-head combinations hold up.
"""
from __future__ import annotations

import os

import duckdb
import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

import database

# Manifest written in the same step as the artifact: without it the artifact reads as
# UNKNOWN identity, and phold_challenger refuses to deploy any calibrator while a source
# artifact fails identity enforcement - which disables PM_CALIBRATED_FAIR_VALUE_V1.
from verified_io import write_manifest as write_integrity_manifest

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
OUT = os.path.join(DATA_DIR, "saved_models", "champion_meta_model.pkl")
HEAD_VERSION = "2026-06-17-champion-meta-v1"
MIN_ROWS = int(os.environ.get("BTC_CHAMPION_META_MIN_ROWS", "500"))

NUMERIC = [
    "horizon", "seconds_left", "current_move", "p_hold", "p_big_move",
    "p_big_drop", "p_big_up", "p_big_down", "p_activity", "champion_confidence",
]
CATEGORICAL = [
    "current_position", "big_move_tier", "big_drop_risk", "big_up_tier",
    "big_down_tier", "activity_tier", "regime", "champion_action",
]


def _load_training_frame() -> pd.DataFrame:
    if not os.path.exists(database.DB_PATH):
        return pd.DataFrame()
    conn = duckdb.connect(database.DB_PATH, read_only=True)
    try:
        return conn.execute("""
            SELECT
                cs.round_id, cs.ts, cs.horizon, cs.seconds_left,
                cs.current_position, cs.current_move, cs.p_hold, cs.p_big_move,
                cs.big_move_tier, cs.p_big_drop, cs.big_drop_risk,
                cs.p_big_up, cs.big_up_tier, cs.p_big_down, cs.big_down_tier,
                cs.p_activity, cs.activity_tier, cs.regime, cs.champion_action,
                cs.champion_confidence, p.actual_direction
            FROM champion_snapshots cs
            JOIN price_to_beat p ON p.id = cs.round_id
            WHERE p.resolved = TRUE
              AND cs.current_position IN ('UP', 'DOWN')
              AND p.actual_direction IN ('UP', 'DOWN')
        """).df()
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def main():
    df = _load_training_frame()
    if len(df) < MIN_ROWS:
        print(f"[champion-meta] not enough resolved snapshots: {len(df)} < {MIN_ROWS}. Skipping.")
        return

    df = df.replace([np.inf, -np.inf], np.nan)
    y = (df["current_position"].astype(str) == df["actual_direction"].astype(str)).astype(int).values
    if len(np.unique(y)) < 2:
        print("[champion-meta] only one class observed. Skipping.")
        return

    split = max(1, int(len(df) * 0.70))
    train = df.iloc[:split].copy()
    test = df.iloc[split:].copy()
    y_train = y[:split]
    y_test = y[split:]

    pre = ColumnTransformer([
        ("num", Pipeline([
            ("imp", SimpleImputer(strategy="median")),
            ("sc", StandardScaler()),
        ]), NUMERIC),
        ("cat", Pipeline([
            ("imp", SimpleImputer(strategy="most_frequent")),
            ("oh", OneHotEncoder(handle_unknown="ignore")),
        ]), CATEGORICAL),
    ])
    clf = Pipeline([
        ("pre", pre),
        ("clf", LogisticRegression(max_iter=1000, class_weight="balanced")),
    ])
    clf.fit(train[NUMERIC + CATEGORICAL], y_train)

    auc = None
    if len(test) and len(np.unique(y_test)) == 2:
        prob = clf.predict_proba(test[NUMERIC + CATEGORICAL])[:, 1]
        auc = float(roc_auc_score(y_test, prob))

    bundle = {
        "model": clf,
        "numeric_features": NUMERIC,
        "categorical_features": CATEGORICAL,
        "version": HEAD_VERSION,
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "test_auc": auc,
        "target": "P(current price-to-beat side holds to resolution | champion snapshot)",
        "activation_note": "Research/meta layer only until enough live validation proves edge.",
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    joblib.dump(bundle, OUT)
    write_integrity_manifest(OUT)
    print(
        f"[champion-meta] trained rows={len(train):,} test={len(test):,} "
        f"test_auc={auc if auc is not None else 'n/a'} saved={OUT}"
    )


if __name__ == "__main__":
    main()
