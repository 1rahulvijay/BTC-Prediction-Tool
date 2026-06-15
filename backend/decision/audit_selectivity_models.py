"""
audit_selectivity_models.py — leakage/parity audit for the timing heads (V10 PART 5 #1).
=========================================================================================
WHY: `train_selectivity_models.py` produced OOS AUCs ABOVE the research docs
(selectivity 0.739 vs ~0.720, tradability 0.793 vs ~0.739, fail-fast 0.613 vs ~0.537).
Our own "too-good = leakage, audit it" rule says: do NOT trust/serve these until the
excess is explained. This script answers: is the edge real, or fold-boundary leakage?

WHAT IT DOES (read-only on `research_matrix_1m.parquet`, no DB, no ensemble, no writes
except an optional report print):
  1. NAIVE vs PURGED: re-scores each head with plain TimeSeriesSplit (what the trainer
     used) AND a PURGED walk-forward with an embargo = label horizon. If AUC drops under
     purging, the excess was the last `embargo` train rows' labels peeking into val (the
     5m label window straddles the fold boundary). The purged number is the honest one.
  2. SINGLE-FEATURE LEAK SCAN: each feature's standalone purged AUC vs the label. A lone
     feature at ~0.9+ AUC is a red flag (a near-copy of the future-derived label).
  3. CALIBRATION on the purged out-of-fold predictions.
  4. VERDICT per head: SIGNAL (purged AUC clears the research bar) / INFLATED (naive >>
     purged) / LEAK_SUSPECT (a single feature dominates).

Pure offline. Run single-threaded so it never competes with a live retrain/backtest.

Usage:  python backend/decision/audit_selectivity_models.py
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import roc_auc_score

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")

HEADS = {
    "selectivity": (["rv_15m", "rv_30m", "log_count", "vpin_15m", "compression_ratio", "shock_magnitude"],
                    "target_big_move", 0.720),
    "tradability": (["compression_ratio", "rv_60m", "rv_30m"], "tradable_move_label", 0.739),
    "fail_fast":   (["shock_magnitude", "vpin"], "fail_fast_label", 0.537),
}
LABEL_HORIZON = 5   # the 5m forward label → embargo for purging


def _fit_predict(Xtr, ytr, Xte):
    sc = StandardScaler().fit(Xtr)
    clf = LogisticRegression(class_weight="balanced", max_iter=1000)
    clf.fit(sc.transform(Xtr), ytr)
    return clf.predict_proba(sc.transform(Xte))[:, 1]


def _naive_auc(X, y, n_splits=5):
    oof = np.zeros(len(y)); seen = np.zeros(len(y), bool)
    for tr, te in TimeSeriesSplit(n_splits=n_splits).split(X):
        oof[te] = _fit_predict(X[tr], y[tr], X[te]); seen[te] = True
    return roc_auc_score(y[seen], oof[seen]), oof, seen


def _purged_auc(X, y, n_splits=5, embargo=LABEL_HORIZON):
    """Same folds, but drop the last `embargo` train rows whose label window overlaps val."""
    oof = np.zeros(len(y)); seen = np.zeros(len(y), bool)
    for tr, te in TimeSeriesSplit(n_splits=n_splits).split(X):
        cut = te[0] - embargo                      # purge train rows within embargo of val start
        tr = tr[tr < cut]
        if len(tr) < 200 or len(np.unique(y[tr])) < 2:
            continue
        oof[te] = _fit_predict(X[tr], y[tr], X[te]); seen[te] = True
    return roc_auc_score(y[seen], oof[seen]), oof, seen


def main():
    if not os.path.exists(MATRIX):
        print(f"ERROR: {MATRIX} not found."); return
    df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    p75 = df["future_abs_move_5m"].quantile(0.75)
    df["target_big_move"] = (df["future_abs_move_5m"] > p75).astype(int)

    print("=" * 70)
    print("SELECTIVITY MODELS — LEAKAGE / PARITY AUDIT")
    print("=" * 70)

    for name, (feats, target, research_auc) in HEADS.items():
        cols = feats + [target]
        d = df.dropna(subset=cols).copy()
        y = d[target].fillna(0).astype(int).values
        X = d[feats].values
        if len(np.unique(y)) < 2:
            print(f"\n[{name}] only one class — skipped."); continue

        naive, _, _ = _naive_auc(X, y)
        purged, oof, seen = _purged_auc(X, y)
        drop = naive - purged

        # single-feature leak scan (purged)
        worst_feat, worst_auc = None, 0.0
        for j, f in enumerate(feats):
            a, _, _ = _purged_auc(X[:, [j]], y)
            if a > worst_auc:
                worst_auc, worst_feat = a, f

        # calibration on purged OOF (high-confidence bin)
        p = oof[seen]; yt = y[seen]
        hi = p >= 0.7
        hi_real = yt[hi].mean() if hi.sum() >= 50 else float("nan")

        # verdict
        if worst_auc >= 0.85:
            verdict = f"** LEAK_SUSPECT ** (feature '{worst_feat}' alone = {worst_auc:.3f})"
        elif drop >= 0.04:
            verdict = f"** INFLATED ** (naive {naive:.3f} >> purged {purged:.3f}; trust purged)"
        elif purged >= research_auc - 0.03:
            verdict = "SIGNAL (purged ~ research; edge holds)"
        else:
            verdict = "WEAK (purged below research bar)"

        print(f"\n[{name}]  features={feats}")
        print(f"  naive  TimeSeriesSplit AUC : {naive:.3f}   (what the trainer reported)")
        print(f"  PURGED walk-forward  AUC   : {purged:.3f}   (embargo={LABEL_HORIZON}, honest)")
        print(f"  research-doc AUC           : {research_auc:.3f}")
        print(f"  naive-minus-purged         : {drop:+.3f}")
        print(f"  strongest single feature   : '{worst_feat}' = {worst_auc:.3f}")
        print(f"  calibration P>=0.7 realized: {hi_real:.3f} (n={int(hi.sum())})")
        print(f"  VERDICT: {verdict}")

    print("\n" + "=" * 70)
    print("How to read: if PURGED ~= research and no single feature >= 0.85, the edge is")
    print("real and the trainer's higher AUC was fold-boundary leakage — re-fit with a")
    print("purged split before serving. If LEAK_SUSPECT, drop/inspect that feature.")
    print("=" * 70)


if __name__ == "__main__":
    main()
