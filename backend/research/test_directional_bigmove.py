"""
test_directional_bigmove.py — does splitting big-move into UP / DOWN / DROP carry signal?
==========================================================================================
The 180d research only tested GENERIC big_move (|return| large) + direction. This tests the
PROPOSED directional labels (180d doc Priority 2) that were never actually run:
    big_up_X    = round expiry_return_bps >= +T
    big_down_X  = round expiry_return_bps <= -T
    big_drop_X  = round low dipped <= -T2 below anchor (intra-round hard drop)
HONEST setup: one row per round at the EARLIEST snapshot (round start) -> no realized-path leak;
round-grouped temporal 70/30 split. Models: ExtraTrees, RandomForest, CatBoost, HistGB, LogReg.

Reads data/research/binance_updown_features.parquet. Prints AUC per (horizon, target, model).
"""
from __future__ import annotations

import sys
import warnings

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

FEAT_PARQUET = "data/research/binance_updown_features.parquet"
MANIFEST = "data/research/binance_updown_feature_manifest.json"


def classifiers():
    from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier, HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    m = {
        "extra_trees": ExtraTreesClassifier(n_estimators=300, max_depth=10, min_samples_leaf=50, n_jobs=2, random_state=0),
        "random_forest": RandomForestClassifier(n_estimators=300, max_depth=8, min_samples_leaf=50, n_jobs=2, random_state=0),
        "histgb": HistGradientBoostingClassifier(max_iter=250, max_depth=4, learning_rate=0.05),
        "logreg": Pipeline([("sc", StandardScaler()), ("clf", LogisticRegression(max_iter=1000, class_weight="balanced"))]),
    }
    try:
        from catboost import CatBoostClassifier
        m["catboost"] = CatBoostClassifier(iterations=300, depth=5, learning_rate=0.05, verbose=0)
    except Exception:
        pass
    return m


def main():
    import json
    import os
    from sklearn.metrics import roc_auc_score
    if not os.path.exists(FEAT_PARQUET):
        raise SystemExit(f"{FEAT_PARQUET} not found — run build_binance_updown_feature_dataset.py first.")
    df = pd.read_parquet(FEAT_PARQUET)
    feats = json.load(open(MANIFEST))["feature_cols"]
    feats = [c for c in feats if c in df.columns]
    # one row per round = the EARLIEST snapshot (round start) -> honest from-anchor features, no path leak
    df = df.sort_values(["round_id", "seconds_elapsed"]).groupby("round_id", as_index=False).first()
    print(f"[dir-bigmove] rounds={len(df):,}  features={len(feats)}  horizons={sorted(df.horizon_min.unique())}")
    T1, DROP_T = 10.0, 15.0
    df["big_up"] = (df["expiry_return_bps"] >= T1).astype(int)
    df["big_down"] = (df["expiry_return_bps"] <= -T1).astype(int)
    df["big_drop"] = (df["max_down_bps"] <= -DROP_T).astype(int)
    print(f"[dir-bigmove] labels: big_up>=+{T1}bps  big_down<=-{T1}bps  big_drop low<=-{DROP_T}bps\n")
    print(f"{'Hz':>3}{'target':>10}{'base%':>7}{'best_model':>15}{'AUC':>7}   (all models AUC)")
    for hz in sorted(df.horizon_min.unique()):
        d = df[df.horizon_min == hz].sort_values("round_start")
        cut = int(len(d) * 0.7)
        tr, te = d.iloc[:cut], d.iloc[cut:]
        train_values = tr[feats].replace([np.inf, -np.inf], np.nan)
        medians = train_values.median(numeric_only=True)
        Xtr = train_values.fillna(medians).fillna(0.0).values
        Xte = (
            te[feats]
            .replace([np.inf, -np.inf], np.nan)
            .fillna(medians)
            .fillna(0.0)
            .values
        )
        for tgt in ("big_up", "big_down", "big_drop"):
            ytr, yte = tr[tgt].values, te[tgt].values
            if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
                print(f"{hz:>3}{tgt:>10}{yte.mean()*100:>6.1f}%  (one class)"); continue
            aucs = {}
            for name, mdl in classifiers().items():
                try:
                    mdl.fit(Xtr, ytr)
                    aucs[name] = roc_auc_score(yte, mdl.predict_proba(Xte)[:, 1])
                except Exception:
                    pass
            best = max(aucs, key=aucs.get)
            alls = " ".join(f"{k}={v:.3f}" for k, v in sorted(aucs.items(), key=lambda x: -x[1]))
            print(f"{hz:>3}{tgt:>10}{yte.mean()*100:>6.1f}%{best:>15}{aucs[best]:>7.3f}   {alls}")
    print("\nRead: 0.50=coin-flip, ~0.74=the generic big-move (magnitude) ceiling. The gap between a")
    print("target's AUC and 0.50 is how much of the magnitude signal survives the direction split.")


if __name__ == "__main__":
    main()
