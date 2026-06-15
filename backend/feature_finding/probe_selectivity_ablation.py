"""
probe_selectivity_ablation.py
Runs ablation testing on Selectivity v2 by removing one feature at a time.
"""

import os
import sys
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score, precision_score
from sklearn.model_selection import TimeSeriesSplit

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, FEATURE_BUILDERS

def compute_lift(y_true, y_prob, percentile=99):
    threshold = np.percentile(y_prob, percentile)
    y_pred_top = (y_prob >= threshold).astype(int)
    
    if y_pred_top.sum() == 0:
        return 0.0, 0.0, 0
        
    prec = precision_score(y_true, y_pred_top)
    baseline = np.mean(y_true)
    lift = prec / baseline if baseline > 0 else 0
    return prec, lift, y_pred_top.sum()

def run_model(X, y):
    tscv = TimeSeriesSplit(n_splits=5)
    aucs = []
    
    for train_i, test_i in tscv.split(X):
        X_train, X_test = X[train_i], X[test_i]
        y_train, y_test = y[train_i], y[test_i]
        
        pipe = Pipeline([
            ('scaler', StandardScaler()),
            ('lr', LogisticRegression(max_iter=1000, class_weight='balanced'))
        ])
        pipe.fit(X_train, y_train)
        preds = pipe.predict_proba(X_test)[:, 1]
        
        try:
            auc = roc_auc_score(y_test, preds)
            aucs.append(auc)
        except:
            pass
            
    # Train full to get lift
    pipe = Pipeline([
        ('scaler', StandardScaler()),
        ('lr', LogisticRegression(max_iter=1000, class_weight='balanced'))
    ])
    pipe.fit(X, y)
    preds = pipe.predict_proba(X)[:, 1]
    prec, lift, n = compute_lift(y, preds, 99)
    
    return np.mean(aucs), prec, lift, n

def main():
    days = 60
    bars = _load_bars(days)
    if bars is None: return
    
    n_bars = len(bars["close"])
    print(f"\n{n_bars} minute-bars loaded. Evaluating Selectivity Ablation.")
    
    # Target
    h = 5
    ret = np.zeros(n_bars)
    for i in range(n_bars - h):
        ret[i] = bars["close"][i+h] - bars["close"][i]
    
    target_abs = np.abs(ret)
    threshold = np.percentile(target_abs[target_abs > 0], 75)
    y = (target_abs > threshold).astype(int)
    
    # Features
    features = [
        "realized_vol",
        "intensity",
        "vpin",
        "range_compression",
        "liquidity_shock"
    ]
    
    X_dict = {}
    for fname in features:
        X_f, _ = FEATURE_BUILDERS[fname](bars)
        X_dict[fname] = X_f
        
    # Build full matrix
    X_full = np.column_stack([X_dict[f] for f in features])
    valid = np.all(np.isfinite(X_full), axis=1)
    
    y = y[valid]
    
    print("\n--- Ablation Results ---")
    baseline_auc, baseline_prec, baseline_lift, top_n = run_model(X_full[valid], y)
    print(f"Full Model AUC: {baseline_auc:.3f} | Top 1% Prec: {baseline_prec:.1%} (Lift: {baseline_lift:.2f}x) | N: {top_n}")
    
    for i, fname in enumerate(features):
        # Create matrix without feature
        ablated_features = [f for f in features if f != fname]
        X_abl = np.column_stack([X_dict[f] for f in ablated_features])
        X_abl = X_abl[valid]
        
        auc, prec, lift, _ = run_model(X_abl, y)
        auc_drop = baseline_auc - auc
        
        print(f"Without {fname:18s}: AUC = {auc:.3f} (Drop: {auc_drop:+.3f}) | Top 1% Prec = {prec:.1%}")

if __name__ == "__main__":
    main()
