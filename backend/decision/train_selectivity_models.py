"""
train_selectivity_models.py — persist the timing/side models the composer needs.
=================================================================================
The Phase 8-15 probes compute Selectivity / Tradability / Invalidation INLINE
(out-of-sample LogReg) but never SAVE a servable model. The live decision composer
needs real P(big_move) / P(tradable) / P(fail_fast) + an expected-move estimate, so
this script fits and PERSISTS them once.

IMPORTANT — this is NOT the 136-feature ensemble retrain:
  • It reads `data/research_matrix_1m.parquet` (a flat file), never the live DuckDB.
  • It fits tiny LogReg/Ridge models (86k rows x <=6 features) in seconds, single-
    threaded, no GPU. It is safe to run WHILE the ensemble retrain is going.
  • It only WRITES `data/saved_models/selectivity_models.pkl` — it touches nothing
    the running app or train uses.

It also persists the microstructure extreme thresholds (basis/CVD/VPIN 5/95 pct and
the selectivity tier cutoffs) from 60 days of history so the live side engine has
grounded absolute thresholds instead of recomputing them per-tick.

Honesty: it prints the OOS (TimeSeriesSplit) AUC for each head so we confirm the
saved model matches the research (~0.72 selectivity, ~0.74 tradability, ~0.54 weak
fail-fast). The final saved model is fit on ALL rows (production), but the reported
metric is strictly out-of-sample.

Usage:  python backend/decision/train_selectivity_models.py
"""
from __future__ import annotations

import os

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
import joblib

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
OUT = os.path.join(DATA_DIR, "saved_models", "selectivity_models.pkl")
HEAD_VERSION = "2026-06-15-lr+rf-ensemble"   # train_heads.py retrains when this changes

# Feature sets — IDENTICAL to probe_rule_composed_scorecard.py (do not drift).
SELECTIVITY_FEATURES = ["rv_15m", "rv_30m", "log_count", "vpin_15m", "compression_ratio", "shock_magnitude"]
TRADABILITY_FEATURES = ["compression_ratio", "rv_60m", "rv_30m"]
FAILFAST_FEATURES = ["shock_magnitude", "vpin"]
EXPECTED_MOVE_FEATURES = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio"]

NEEDED = sorted(set(
    SELECTIVITY_FEATURES + TRADABILITY_FEATURES + FAILFAST_FEATURES + EXPECTED_MOVE_FEATURES
    + ["future_abs_move_5m", "tradable_move_label", "fail_fast_label",
       "perp_spot_basis_bps", "cvd_divergence", "vpin", "close"]))


def _oos_auc(X: np.ndarray, y: np.ndarray) -> float:
    """Out-of-sample AUC via 5-fold TimeSeriesSplit (train past → test future)."""
    if len(np.unique(y)) < 2:
        return float("nan")
    oof = np.zeros(len(y))
    seen = np.zeros(len(y), dtype=bool)
    for tr, te in TimeSeriesSplit(n_splits=5).split(X):
        sc = StandardScaler().fit(X[tr])
        clf = LogisticRegression(class_weight="balanced", max_iter=1000)
        clf.fit(sc.transform(X[tr]), y[tr])
        oof[te] = clf.predict_proba(sc.transform(X[te]))[:, 1]
        seen[te] = True
    return roc_auc_score(y[seen], oof[seen])


def _fit_classifier(df: pd.DataFrame, feats: list[str], y: np.ndarray) -> tuple[Pipeline, float]:
    X = df[feats].values
    auc = _oos_auc(X, y)
    pipe = Pipeline([("scaler", StandardScaler()),
                     ("clf", LogisticRegression(class_weight="balanced", max_iter=1000))])
    pipe.fit(X, y)
    return pipe, auc


def _make_selectivity_ensemble(n_jobs: int = 2):
    """Soft-voting LogReg + RandomForest for the TIMING head. RF is the ONLY decorrelated lift
    measured for selectivity (+~0.003 OOS AUC vs LogReg; operator-requested). Drop-in:
    VotingClassifier exposes predict_proba, so every consumer of models['selectivity']['pipe']
    keeps working unchanged (LogReg keeps its own StandardScaler; RF is scale-invariant)."""
    from sklearn.ensemble import RandomForestClassifier, VotingClassifier
    lr = Pipeline([("scaler", StandardScaler()),
                   ("clf", LogisticRegression(class_weight="balanced", max_iter=1000))])
    rf = RandomForestClassifier(n_estimators=300, max_depth=8, min_samples_leaf=50,
                                class_weight="balanced", n_jobs=n_jobs, random_state=0)
    return VotingClassifier([("lr", lr), ("rf", rf)], voting="soft", n_jobs=1)


def _oos_auc_ensemble(X: np.ndarray, y: np.ndarray) -> float:
    """OOS AUC for the LogReg+RF soft-voting ensemble (TimeSeriesSplit, refit per fold)."""
    if len(np.unique(y)) < 2:
        return float("nan")
    oof = np.zeros(len(y)); seen = np.zeros(len(y), dtype=bool)
    for tr, te in TimeSeriesSplit(n_splits=5).split(X):
        clf = _make_selectivity_ensemble()
        clf.fit(X[tr], y[tr])
        oof[te] = clf.predict_proba(X[te])[:, 1]; seen[te] = True
    return roc_auc_score(y[seen], oof[seen])


def _fit_selectivity_ensemble(df: pd.DataFrame, feats: list[str], y: np.ndarray):
    """Fit the LogReg+RF ensemble on all rows; return (clf, ens_oos_auc, lr_oos_auc) so the lift
    is visible in the log (the honest +0.003 vs LogReg-only)."""
    X = df[feats].values
    ens_auc, lr_auc = _oos_auc_ensemble(X, y), _oos_auc(X, y)
    clf = _make_selectivity_ensemble()
    clf.fit(X, y)
    return clf, ens_auc, lr_auc


def main():
    if not os.path.exists(MATRIX):
        print(f"ERROR: {MATRIX} not found — build it first (build_research_matrix.py).")
        return
    print(f"Loading {MATRIX} ...")
    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    missing = [c for c in NEEDED if c not in df.columns]
    if missing:
        print(f"ERROR: research matrix missing columns: {missing}")
        return
    df = df.dropna(subset=NEEDED).copy()
    print(f"Rows after NaN-drop: {len(df):,}")

    # Targets
    p75 = df["future_abs_move_5m"].quantile(0.75)
    y_big = (df["future_abs_move_5m"] > p75).astype(int).values
    y_trad = df["tradable_move_label"].fillna(0).astype(int).values
    y_fail = df["fail_fast_label"].fillna(0).astype(int).values
    # Expected move in bps (absolute), the cost-gate input.
    move_bps = (df["future_abs_move_5m"] / df["close"] * 10000.0).values

    print("\nFitting heads (OOS AUC reported, final fit on all rows):")
    sel_pipe, sel_auc, sel_lr_auc = _fit_selectivity_ensemble(df, SELECTIVITY_FEATURES, y_big)
    print(f"  selectivity P(big_move) : OOS AUC {sel_auc:.3f}  (LogReg+RF ensemble; "
          f"LogReg-only {sel_lr_auc:.3f}, lift {sel_auc - sel_lr_auc:+.3f}; research ~0.720)")
    trad_pipe, trad_auc = _fit_classifier(df, TRADABILITY_FEATURES, y_trad)
    print(f"  tradability P(tradable) : OOS AUC {trad_auc:.3f}  (research ~0.739)")
    fail_pipe, fail_auc = _fit_classifier(df, FAILFAST_FEATURES, y_fail)
    print(f"  invalidation P(fail)    : OOS AUC {fail_auc:.3f}  (research ~0.537, weak/soft)")

    # Expected-move regressor (Ridge) — OOS R-equivalent via simple holdout split.
    Xm = df[EXPECTED_MOVE_FEATURES].values
    split = int(len(Xm) * 0.8)
    msc = StandardScaler().fit(Xm[:split])
    rdg = Ridge(alpha=1.0).fit(msc.transform(Xm[:split]), move_bps[:split])
    pred = rdg.predict(msc.transform(Xm[split:]))
    mae = float(np.mean(np.abs(pred - move_bps[split:])))
    move_pipe = Pipeline([("scaler", StandardScaler()), ("reg", Ridge(alpha=1.0))]).fit(Xm, move_bps)
    print(f"  expected_move_bps (Ridge): holdout MAE {mae:.1f} bps  (mean |move| {move_bps.mean():.1f} bps)")

    # Microstructure extreme thresholds + selectivity tier cutoffs from 60d history.
    def pct(col, q):
        v = df[col].values
        return float(np.percentile(v[np.isfinite(v)], q))
    sel_scores = sel_pipe.predict_proba(df[SELECTIVITY_FEATURES].values)[:, 1]
    side_thresholds = {
        "basis_up": pct("perp_spot_basis_bps", 95), "basis_dn": pct("perp_spot_basis_bps", 5),
        "cvd_up": pct("cvd_divergence", 95), "cvd_dn": pct("cvd_divergence", 5),
        "vpin_up": pct("vpin", 95), "vpin_dn": pct("vpin", 5),
        "sel_t1": float(np.percentile(sel_scores, 95)),
        "sel_t2": float(np.percentile(sel_scores, 99)),
        "sel_t3": float(np.percentile(sel_scores, 99.75)),
    }
    print("\nPersisted side thresholds (60d):")
    for k, v in side_thresholds.items():
        print(f"  {k:>10}: {v:+.4f}")

    bundle = {
        "models": {
            "selectivity": {"pipe": sel_pipe, "features": SELECTIVITY_FEATURES, "oos_auc": sel_auc,
                            "model_type": "voting(lr+rf)", "oos_auc_lr": sel_lr_auc},
            "tradability": {"pipe": trad_pipe, "features": TRADABILITY_FEATURES, "oos_auc": trad_auc},
            "fail_fast": {"pipe": fail_pipe, "features": FAILFAST_FEATURES, "oos_auc": fail_auc},
            "expected_move_bps": {"pipe": move_pipe, "features": EXPECTED_MOVE_FEATURES, "holdout_mae": mae},
        },
        "side_thresholds": side_thresholds,
        "n_rows": int(len(df)),
        "big_move_p75": float(p75),
        "version": HEAD_VERSION,   # bump to force a retrain on next start.bat (train_heads.py)
        "note": "Stage-2 timing/side models for the live decision composer. NOT the "
                "136-feature ensemble. Serving requires live parity for the input features.",
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    joblib.dump(bundle, OUT)
    print(f"\nSaved {OUT}")
    print("NOTE: serving these live requires computing the SAME features at serve time "
          "(rv_*/vpin_15m/compression_ratio/shock_magnitude/basis/cvd) with parity — see V10.")


if __name__ == "__main__":
    main()
