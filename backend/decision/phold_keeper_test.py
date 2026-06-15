"""
phold_keeper_test.py — does adding the volatility KEEPERS lift P(Hold)?
======================================================================
The live P(Hold) model uses ~5 intra-round features (abs_distance_pct, seconds_left,
vol_60s_pct, horizon, dist_vol_ratio) and scores test_auc≈0.747. Open question
(PRICE_TO_BEAT_MODEL_ANALYSIS §12): does broader volatility-regime context — the
keepers (rv_15m/30m/60m, vpin, compression_ratio, shock_magnitude) — improve the hold
prediction beyond trailing-60s vol?

Method (honest):
  • Join each intra-round row to the research-matrix KEEPERS at its CURRENT minute
    (window_start_ms + seconds_elapsed).
  • ROUND-LEVEL temporal split (train = earliest 70% of rounds, test = latest 30%) so
    no same-round leakage (a round's rows never straddle the split).
  • Same model (HistGB) for BASE vs BASE+KEEPERS → compare test AUC.
  • Also report on the T3 high-precision subset (late + already ahead) — that's where a
    lift would actually matter for the product.

Pure offline read of two parquet files. Writes nothing. Run with the app stopped.
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data")
PERS = os.path.join(DATA_DIR, "persistence_dataset.parquet")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
KEEPERS = ["rv_15m", "rv_30m", "rv_60m", "vpin", "compression_ratio", "shock_magnitude"]


def main():
    p = pd.read_parquet(PERS)
    m = pd.read_parquet(MATRIX, columns=["ts_ms"] + KEEPERS).replace([np.inf, -np.inf], np.nan)
    print(f"persistence rows: {len(p):,}   matrix rows: {len(m):,}")

    # Current minute of each intra-round state -> join key to the keeper matrix.
    p["cur_min_ms"] = (((p["window_start_ms"] + p["seconds_elapsed"] * 1000) // 60000) * 60000).astype("int64")
    df = p.merge(m.rename(columns={"ts_ms": "cur_min_ms"}), on="cur_min_ms", how="inner")
    df = df.dropna(subset=KEEPERS).reset_index(drop=True)
    print(f"joined rows (keepers present): {len(df):,}")

    # Base features (mirror the live P(Hold) model).
    df["abs_distance_pct"] = df["distance_pct"].abs()
    df["dist_vol_ratio"] = df["abs_distance_pct"] / df["vol_60s_pct"].replace(0, np.nan)
    df["dist_vol_ratio"] = df["dist_vol_ratio"].fillna(0.0)
    BASE = ["abs_distance_pct", "seconds_left", "vol_60s_pct", "horizon", "dist_vol_ratio"]
    y = df["label"].astype(int).values

    # ROUND-level temporal split (no same-round leakage).
    rounds = np.sort(df["window_start_ms"].unique())
    cut = rounds[int(len(rounds) * 0.70)]
    tr = df["window_start_ms"].values < cut
    te = ~tr
    print(f"rounds: {len(rounds):,}  train rows={tr.sum():,}  test rows={te.sum():,}  "
          f"test hold-rate={y[te].mean():.3f}")

    def fit_auc(cols):
        X = df[cols].values
        clf = HistGradientBoostingClassifier(max_iter=250, max_depth=4, learning_rate=0.06,
                                             l2_regularization=1.0, random_state=0)
        clf.fit(X[tr], y[tr])
        pr = clf.predict_proba(X[te])[:, 1]
        return roc_auc_score(y[te], pr), pr

    auc_base, p_base = fit_auc(BASE)
    auc_keep, p_keep = fit_auc(BASE + KEEPERS)
    print("\n=== P(Hold) AUC (round-level temporal test) ===")
    print(f"  BASE (5 feat)        : {auc_base:.4f}")
    print(f"  BASE + KEEPERS (11)  : {auc_keep:.4f}")
    print(f"  lift                 : {auc_keep - auc_base:+.4f}")

    # T3 high-precision subset: late rows (seconds_left<=120) — the part that matters.
    # p_base/p_keep are aligned to the TEST rows; index the late-within-test subset.
    late_in_test = (df["seconds_left"].values[te] <= 120)
    y_te = y[te]
    if late_in_test.sum() > 500:
        print("\n=== T3 subset (test rows, seconds_left<=120) ===")
        print(f"  n={late_in_test.sum():,}  hold-rate={y_te[late_in_test].mean():.3f}")
        print(f"  BASE AUC        : {roc_auc_score(y_te[late_in_test], p_base[late_in_test]):.4f}")
        print(f"  BASE+KEEP AUC   : {roc_auc_score(y_te[late_in_test], p_keep[late_in_test]):.4f}")

    print("\nVerdict: keepers are worth adding to P(Hold) ONLY if the lift is meaningful "
          "(>~0.01 AUC) AND it holds on the T3 late subset. Otherwise keep the lean 5-feature model.")


if __name__ == "__main__":
    main()
