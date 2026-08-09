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
# artifact fails identity enforcement - which disables
# PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1.
from verified_io import write_manifest as write_integrity_manifest

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)
OUT = os.path.join(
    os.environ.get("BTC_MODEL_OUTPUT_DIR") or os.path.join(DATA_DIR, "saved_models"),
    "champion_meta_model.pkl",
)
HEAD_VERSION = "2026-06-17-champion-meta-v1"
#: Gates count INDEPENDENT RESOLUTIONS, not snapshot rows. ~28 snapshots share one round_id
#: and therefore one outcome, so the old `MIN_ROWS = 500` snapshot gate was satisfied by about
#: 18 independent rounds - measured, not estimated, on the live store (186,955 eligible
#: snapshots over 6,727 rounds and 21 days). A row count is not a sample size here.
MIN_ROWS = int(os.environ.get("BTC_CHAMPION_META_MIN_ROWS", "500"))
MIN_ROUNDS = int(os.environ.get("BTC_CHAMPION_META_MIN_ROUNDS", "500"))
MIN_DAYS = int(os.environ.get("BTC_CHAMPION_META_MIN_DAYS", "10"))

#: Dropped from the END of TRAIN so its feature windows cannot overlap the first test round.
PURGE_MINUTES = int(os.environ.get("BTC_CHAMPION_META_PURGE_MINUTES", "60"))

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
            ORDER BY cs.ts, cs.round_id, cs.seconds_left DESC
        """).df()
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def round_grouped_split(df: pd.DataFrame, frac: float = 0.70,
                        purge_ms: int | None = None):
    """Split by ROUND in time order, with a purge. Never by snapshot row.

    About 28 snapshots share one round_id, hence one resolution and one actual_direction. A
    positional row split therefore puts the SAME outcome on both sides and the model is
    tested on answers it was trained on. On the live store 6 rounds straddled the old split -
    small only because DuckDB happened to return near-insertion order, which it never
    promised and which the query did not request.

    Returns (train_mask, test_mask, meta) or None when no honest split exists. TRAIN is what
    shrinks at the boundary; the test set is never trimmed to improve a metric.
    """
    if purge_ms is None:
        purge_ms = PURGE_MINUTES * 60_000
    first_ts = df.groupby("round_id")["ts"].min().sort_values()
    last_ts = df.groupby("round_id")["ts"].max()
    ordered = list(first_ts.index)
    if len(ordered) < 2:
        return None
    cut = max(1, min(len(ordered) - 1, int(len(ordered) * frac)))
    train_rounds, test_rounds = ordered[:cut], ordered[cut:]
    boundary = int(first_ts.loc[test_rounds[0]])
    kept = [r for r in train_rounds if int(last_ts.loc[r]) <= boundary - purge_ms]
    if not kept or not test_rounds:
        return None
    train_mask = df["round_id"].isin(set(kept)).values
    test_mask = df["round_id"].isin(set(test_rounds)).values
    day = pd.to_datetime(df["ts"], unit="ms").dt.date
    meta = {
        "independent_rounds_total": int(len(ordered)),
        "independent_rounds_train": int(len(kept)),
        "independent_rounds_test": int(len(test_rounds)),
        "rounds_purged": int(len(train_rounds) - len(kept)),
        "purge_minutes": int(purge_ms // 60_000),
        "independent_days_train": int(day[train_mask].nunique()),
        "independent_days_test": int(day[test_mask].nunique()),
        "rounds_in_both_sides": 0,  # structurally impossible: masks partition round_id
    }
    return train_mask, test_mask, meta


def grouped_auc_lcb(y_true, prob, groups, n_boot: int = 400, seed: int = 0):
    """AUC with a 5th-percentile lower bound from resampling ROUNDS, not rows.

    A row bootstrap over ~56k snapshots drawn from ~2k rounds reports the precision of a
    sample size the data does not have. The independent unit is the resolution."""
    rng = np.random.default_rng(seed)
    groups = np.asarray(groups)
    uniq = np.unique(groups)
    idx_by_group = {g: np.flatnonzero(groups == g) for g in uniq}
    stats = []
    for _ in range(n_boot):
        pick = rng.choice(uniq, size=len(uniq), replace=True)
        idx = np.concatenate([idx_by_group[g] for g in pick])
        if len(np.unique(y_true[idx])) < 2:
            continue
        stats.append(roc_auc_score(y_true[idx], prob[idx]))
    if not stats:
        return None
    return float(np.percentile(stats, 5))


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

    n_rounds = int(df["round_id"].nunique())
    n_days = int(pd.to_datetime(df["ts"], unit="ms").dt.date.nunique())
    if n_rounds < MIN_ROUNDS or n_days < MIN_DAYS:
        print(f"[champion-meta] not enough INDEPENDENT evidence: {n_rounds} rounds over "
              f"{n_days} days (need {MIN_ROUNDS} rounds and {MIN_DAYS} days) from "
              f"{len(df):,} snapshots. Snapshots are not independent observations. Skipping.")
        return

    split_out = round_grouped_split(df)
    if split_out is None:
        print("[champion-meta] no purged round-grouped split is possible. Skipping.")
        return
    train_mask, test_mask, split_meta = split_out
    train = df.loc[train_mask].copy()
    test = df.loc[test_mask].copy()
    y_train = y[train_mask]
    y_test = y[test_mask]
    if len(np.unique(y_train)) < 2:
        print("[champion-meta] training side is single-class after the round split. Skipping.")
        return

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
    auc_lcb = None
    if len(test) and len(np.unique(y_test)) == 2:
        prob = clf.predict_proba(test[NUMERIC + CATEGORICAL])[:, 1]
        auc = float(roc_auc_score(y_test, prob))
        auc_lcb = grouped_auc_lcb(y_test, prob, test["round_id"].values)

    bundle = {
        "model": clf,
        "numeric_features": NUMERIC,
        "categorical_features": CATEGORICAL,
        "version": HEAD_VERSION,
        "train_rows": int(len(train)),
        "test_rows": int(len(test)),
        "test_auc": auc,
        # The number that should be read instead of test_auc: resampled over ROUNDS, so it
        # reflects the ~28x dependence between snapshots of one resolution.
        "test_auc_round_bootstrap_lcb95": auc_lcb,
        "split": "round_grouped_purged_chronological",
        **split_meta,
        "target": "P(current price-to-beat side holds to resolution | champion snapshot)",
        "activation_note": "Research/meta layer only until enough live validation proves edge.",
        # champion_snapshots carries NO release or artifact identity (verified: 21 columns,
        # none naming a release/sha/hash/policy). So these rows pool every head generation
        # that has ever run, while the live inputs come from the CURRENT stack. This head
        # therefore cannot claim release compatibility, and says so rather than implying it.
        # Fixing it requires the snapshot writer to record main_release_id, the per-head
        # artifact hashes and the champion policy hash at write time.
        "release_pooling": "UNMITIGATED_NO_IDENTITY_COLUMNS",
        "release_scoped": False,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    joblib.dump(bundle, OUT)
    write_integrity_manifest(OUT)
    print(
        f"[champion-meta] trained rows={len(train):,} test={len(test):,} "
        f"test_auc={auc if auc is not None else 'n/a'} "
        f"round_lcb95={auc_lcb if auc_lcb is not None else 'n/a'} saved={OUT}"
    )
    print(
        f"[champion-meta] INDEPENDENT rounds train={split_meta['independent_rounds_train']:,} "
        f"test={split_meta['independent_rounds_test']:,} "
        f"(purged {split_meta['rounds_purged']} at a "
        f"{split_meta['purge_minutes']}m boundary); days train="
        f"{split_meta['independent_days_train']} test={split_meta['independent_days_test']}. "
        f"Snapshot counts above are ~28x these and are NOT the sample size."
    )


if __name__ == "__main__":
    main()
