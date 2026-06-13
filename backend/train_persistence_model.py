"""
train_persistence_model.py — A1 / T3 persistence model (offline, frozen-model-safe).
====================================================================================
Trains a CALIBRATED P(hold) classifier on persistence_dataset.parquet:
"price is `distance` past the line with `seconds_left` left — will it HOLD to close?"

This is a SEPARATE head — it does NOT touch the frozen v6 ensemble, the feature schema,
or any serving code. It only reads the parquet and writes
data/saved_models/persistence_model.pkl. Wiring P(hold) into the live card is a later,
separate step (needs a restart).

Anti-leakage discipline:
  * NEVER use `close` / `actual_direction` / `anchor` (outcome or window-specific) as features.
  * Snapshots within one window share the outcome -> a random split LEAKS. Split TEMPORALLY
    by `window_start_ms` (train=oldest, calib=middle, test=newest) so test windows are
    fully unseen AND it measures generalization to the future.
  * Persistence is up/down SYMMETRIC -> use |distance| (sign carries no extra signal).

Headline metric: at a high P(hold) threshold, the REALIZED hold rate on the held-out test
set (the honest T3-tier precision), with coverage.

Usage:  python backend/train_persistence_model.py
"""
import os
import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import roc_auc_score

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
IN_PATH = os.path.join(DATA_DIR, "persistence_dataset.parquet")
OUT_PATH = os.path.join(DATA_DIR, "saved_models", "persistence_model.pkl")

FEATURES = ["abs_distance_pct", "seconds_left", "vol_60s_pct", "horizon", "dist_vol_ratio"]


def _add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["abs_distance_pct"] = df["distance_pct"].abs()
    # how many trailing-60s-vol units the price is ahead — the core persistence predictor
    df["dist_vol_ratio"] = df["abs_distance_pct"] / (df["vol_60s_pct"] + 1e-6)
    return df


def main():
    if not os.path.exists(IN_PATH):
        raise SystemExit(f"missing {IN_PATH} — run build_persistence_dataset.py first")
    df = _add_features(pd.read_parquet(IN_PATH))
    df = df.dropna(subset=FEATURES + ["label"])
    print(f"loaded {len(df):,} snapshots, base hold-rate {df['label'].mean()*100:.1f}%")

    # TEMPORAL, leak-free split BY WINDOW (snapshots in a window share the outcome).
    wins = np.sort(df["window_start_ms"].unique())
    n = len(wins)
    tr_cut, ca_cut = wins[int(n * 0.70)], wins[int(n * 0.85)]
    tr = df[df["window_start_ms"] < tr_cut]
    ca = df[(df["window_start_ms"] >= tr_cut) & (df["window_start_ms"] < ca_cut)]
    te = df[df["window_start_ms"] >= ca_cut]
    print(f"split (by window, temporal): train={len(tr):,}  calib={len(ca):,}  test={len(te):,}")

    Xtr, ytr = tr[FEATURES].values, tr["label"].values
    clf = HistGradientBoostingClassifier(
        max_iter=300, max_leaf_nodes=31, learning_rate=0.05,
        l2_regularization=0.1, random_state=42, validation_fraction=0.1)
    clf.fit(Xtr, ytr)

    # Isotonic calibration on the (temporally separate) calib slice — leak-free.
    p_ca = clf.predict_proba(ca[FEATURES].values)[:, 1]
    iso = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    iso.fit(p_ca, ca["label"].values)

    # ----- HONEST evaluation on the unseen TEST windows -----
    p_te = iso.predict(clf.predict_proba(te[FEATURES].values)[:, 1])
    yte = te["label"].values
    auc = roc_auc_score(yte, p_te)
    print(f"\nTEST  n={len(te):,}  AUC={auc:.4f}  base={yte.mean()*100:.1f}%")

    print("\nReliability (calibrated P(hold) -> realized):")
    for lo in (0.5, 0.7, 0.8, 0.9, 0.95):
        hi = lo + 0.05 if lo < 0.95 else 1.01
        m = (p_te >= lo) & (p_te < hi)
        if m.sum() >= 50:
            print(f"  P in [{lo:.2f},{hi:.2f}): realized={yte[m].mean()*100:5.1f}%  n={m.sum():,}")

    print("\nT3-tier precision (the headline) — realized hold at each P(hold) bar, with coverage:")
    for thr in (0.85, 0.90, 0.93, 0.95):
        m = p_te >= thr
        if m.sum():
            print(f"  P(hold) >= {thr:.2f}:  realized={yte[m].mean()*100:5.1f}%  "
                  f"coverage={m.mean()*100:5.1f}%  (n={m.sum():,})")

    # Per-horizon at the 0.93 bar (the bettable horizons)
    print("\nPer-horizon @ P(hold)>=0.93:")
    for h in sorted(te["horizon"].unique()):
        sub = te[te["horizon"] == h]
        ph = iso.predict(clf.predict_proba(sub[FEATURES].values)[:, 1])
        m = ph >= 0.93
        ys = sub["label"].values
        if m.sum():
            print(f"  {int(h):>2}m: realized={ys[m].mean()*100:5.1f}%  n={m.sum():,} "
                  f"({m.mean()*100:.0f}% of {len(sub):,})")

    # Baseline the naive heuristic for comparison (the old flat rule)
    naive = (te["seconds_left"] <= 60) & (te["distance"].abs() >= 10)
    if naive.sum():
        print(f"\nNaive heuristic (<=60s & >=$10 ahead): {yte[naive].mean()*100:.1f}%  "
              f"coverage={naive.mean()*100:.1f}% — the model should match this AND extend "
              f"high-precision coverage to more setups.")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    joblib.dump({"clf": clf, "iso": iso, "features": FEATURES,
                 "trained_rows": int(len(tr)), "test_auc": float(auc),
                 "note": "P(hold|abs_distance_pct,seconds_left,vol_60s_pct,horizon,dist_vol_ratio)"},
                OUT_PATH)
    print(f"\nSaved -> {OUT_PATH}")


if __name__ == "__main__":
    main()
