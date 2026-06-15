"""
train_selectivity_model_v2.py — The 6-Keeper Selectivity Engine
========================================================================
Combines the 6 proven keepers to predict P(Big_Move):
1. range_compression
2. intensity
3. realized_vol
4. vpin
5. liquidity_shock
6. vpin_transition (slope/accel)

Prints a strict out-of-sample precision ladder.
"""

import os
import sys
import pickle
import numpy as np
from datetime import datetime
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.model_selection import TimeSeriesSplit

sys.path.insert(0, os.path.dirname(__file__))
from edge_probe import _load_bars, FEATURE_BUILDERS, make_labels, _roll_sum, _roll_mean

MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "selectivity_model_v2.pkl")

def compute_vpin_transitions(bars):
    n = len(bars["close"])
    vol = bars["vol"].clip(1)
    ret = np.zeros(n)
    logc = np.log(np.where(bars["close"] > 0, bars["close"], 1.0))
    ret[1:] = np.diff(logc)
    
    dp = np.abs(ret)
    vp = vol * dp
    buy_vol = np.where(ret > 0, vp, 0)
    sell_vol = np.where(ret < 0, vp, 0)
    
    vpin_level = np.abs(_roll_sum(buy_vol, 15) - _roll_sum(sell_vol, 15)) / _roll_sum(vol, 15).clip(1)
    
    vpin_slope_5m = vpin_level - np.roll(vpin_level, 5)
    vpin_accel = vpin_slope_5m - np.roll(vpin_slope_5m, 5)
    
    return np.column_stack([np.abs(vpin_slope_5m), np.abs(vpin_accel)]), ["vpin_slope_abs", "vpin_accel_abs"]

def compute_liquidity_shock(bars):
    hl = bars["high"] - bars["low"]
    hl_mean_60 = _roll_mean(hl, 60)
    shock = hl / (hl_mean_60 + 1e-9)
    return shock.reshape(-1, 1), ["liquidity_shock"]

def main(days=60, horizon=5, percentile=75):
    print(f"Loading {days} days of tick data...")
    bars = _load_bars(days)
    if bars is None or len(bars["close"]) < 500:
        sys.exit("Not enough data to train.")
    
    n = len(bars["close"])
    print(f"Loaded {n} minute-bars. Engineering features...")
    
    # 1. Build Features
    features_to_use = ["range_compression", "realized_vol", "intensity", "vpin"]
    X_list = []
    feature_names = []
    
    # Core Keepers
    for fname in features_to_use:
        X_f, names = FEATURE_BUILDERS[fname](bars)
        X_list.append(X_f)
        feature_names.extend([f"{fname}_{n}" for n in names])
        
    # Phase 3/4 Keepers
    X_shock, names_shock = compute_liquidity_shock(bars)
    X_list.append(X_shock)
    feature_names.extend(names_shock)
    
    X_vpin_t, names_vpin_t = compute_vpin_transitions(bars)
    X_list.append(X_vpin_t)
    feature_names.extend(names_vpin_t)
    
    X = np.column_stack(X_list)
    
    # 2. Build Labels
    _, absm = make_labels(bars["close"], horizon)
    
    # Mask out NaNs and infinities
    mask = np.isfinite(absm) & np.all(np.isfinite(X), axis=1)
    X = X[mask]
    absm = absm[mask]
    
    # 3. Temporal Split
    split_idx = int(len(X) * 0.70)
    X_train, X_test = X[:split_idx], X[split_idx:]
    absm_train, absm_test = absm[:split_idx], absm[split_idx:]
    
    # Define "Big Move" dynamically based on the training set 75th percentile
    threshold = np.percentile(absm_train, percentile)
    print(f"Target: Absolute {horizon}m Move >= ${threshold:.2f} (Top {100-percentile}%)")
    
    y_train = (absm_train >= threshold).astype(int)
    y_test = (absm_test >= threshold).astype(int)
    
    baseline_train = y_train.mean()
    baseline_test = y_test.mean()
    print(f"Train samples: {len(X_train)} (Positive class: {baseline_train:.1%})")
    print(f"Test samples:  {len(X_test)} (Positive class: {baseline_test:.1%})")
    
    # 4. Train Model
    print("\nTraining Logistic Regression v2 pipeline...")
    pipe = Pipeline([
        ('scaler', StandardScaler()),
        ('clf', LogisticRegression(class_weight='balanced', max_iter=1000, C=0.1))
    ])
    
    pipe.fit(X_train, y_train)
    
    # 5. Evaluate Out-of-Sample
    probs = pipe.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, probs)
    print(f"\n--- SELECTIVITY MODEL V2 SCORECARD ---")
    print(f"Out-of-Sample AUC: {auc:.3f}")
    print(f"Baseline Rate:     {baseline_test:.1%}")
    
    print("\n--- Precision by Confidence Bin ---")
    bins = [0.7, 0.8, 0.9, 0.95]
    for b in bins:
        mask_b = probs >= b
        if mask_b.sum() == 0:
            print(f"P(Big_Move) >= {b:.2f} | N =    0 | Precision: N/A")
            continue
        prec = y_test[mask_b].mean()
        lift = prec / baseline_test
        print(f"P(Big_Move) >= {b:.2f} | N = {mask_b.sum():>4} | Precision: {prec:.1%} (Lift: {lift:.1f}x)")
        
    print("\n--- Precision by Top Percentiles ---")
    ranks = np.argsort(probs)[::-1]
    for p_top in [0.20, 0.10, 0.05, 0.01]:
        cutoff_idx = int(len(probs) * p_top)
        top_indices = ranks[:cutoff_idx]
        prec = y_test[top_indices].mean()
        lift = prec / baseline_test
        print(f"Top {p_top:4.0%} signals | Precision: {prec:.1%} (Lift: {lift:.1f}x)")

    print("\n--- Probability Calibration ---")
    cal_bins = [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]
    for low, high in cal_bins:
        mask_c = (probs >= low) & (probs < high)
        if mask_c.sum() > 0:
            actual = y_test[mask_c].mean()
            print(f"Predicted [{low:.2f}-{high:.2f}) | Actual Hit Rate: {actual:.1%} (N={mask_c.sum()})")

    print("\n--- 'Do Nothing' Benchmark ---")
    # Highest realized vol only benchmark
    idx_vol = feature_names.index("realized_vol_vol_5m") if "realized_vol_vol_5m" in feature_names else 0
    vol_feat = X_test[:, idx_vol]
    p90_vol = np.percentile(X_train[:, idx_vol], 90)
    bench_vol_prec = y_test[vol_feat > p90_vol].mean()
    print(f"Benchmark: realized_vol > p90 | Precision: {bench_vol_prec:.1%}")

    print("\n--- Ablation Study ---")
    print(f"Full Model AUC: {auc:.3f}")
    feature_groups = ["range_compression", "realized_vol", "intensity", "vpin", "liquidity_shock", "vpin_slope", "vpin_accel"]
    for group in feature_groups:
        cols_to_keep = [i for i, name in enumerate(feature_names) if group not in name]
        if len(cols_to_keep) == len(feature_names): continue
        X_tr_ab = X_train[:, cols_to_keep]
        X_te_ab = X_test[:, cols_to_keep]
        p_ab = Pipeline([
            ('scaler', StandardScaler()),
            ('clf', LogisticRegression(class_weight='balanced', max_iter=1000, C=0.1))
        ])
        p_ab.fit(X_tr_ab, y_train)
        ab_probs = p_ab.predict_proba(X_te_ab)[:, 1]
        ab_auc = roc_auc_score(y_test, ab_probs)
        drop = auc - ab_auc
        importance = "-> important" if drop > 0.01 else "-> minor" if drop > 0.002 else "-> redundant"
        print(f"Without {group:18s}: {ab_auc:.3f} (Drop: {drop:+.3f}) {importance}")
    
    # Fold Stability Check
    print("\n--- Fold-by-Fold Stability Check (Train set) ---")
    tscv = TimeSeriesSplit(n_splits=5)
    fold_aucs = []
    for f_idx, (train_ix, val_ix) in enumerate(tscv.split(X_train)):
        p_fold = Pipeline([
            ('scaler', StandardScaler()),
            ('clf', LogisticRegression(class_weight='balanced', max_iter=1000, C=0.1))
        ])
        p_fold.fit(X_train[train_ix], y_train[train_ix])
        f_probs = p_fold.predict_proba(X_train[val_ix])[:, 1]
        try:
            f_auc = roc_auc_score(y_train[val_ix], f_probs)
            fold_aucs.append(f_auc)
            print(f" Fold {f_idx+1}: AUC {f_auc:.3f}")
        except:
            pass
    if fold_aucs:
        print(f" Mean Fold AUC: {np.mean(fold_aucs):.3f} (Std: {np.std(fold_aucs):.3f})")

    # 6. Save Model
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({
            "pipeline": pipe,
            "feature_names": feature_names,
            "horizon": horizon,
            "threshold": threshold,
            "trained_at": datetime.now().isoformat()
        }, f)
    print(f"\nSaved model to {MODEL_PATH}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()
    main(days=args.days)
