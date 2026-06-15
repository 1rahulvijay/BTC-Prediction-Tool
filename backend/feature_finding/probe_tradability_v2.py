"""
probe_tradability_v2.py
Tests the upgraded tradability targets:
- P(Clean_Big_Move)
- P(Continuation_Clean)
- P(Reversal_Clean)
"""

import os
import sys
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, FEATURE_BUILDERS

def make_clean_path_labels(bars, h=5):
    n = len(bars["close"])
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    
    clean_big = np.zeros(n)
    cont_clean = np.zeros(n)
    rev_clean = np.zeros(n)
    
    # Base target threshold
    ret = np.zeros(n)
    for i in range(n - h):
        ret[i] = close[i+h] - close[i]
        
    target_abs = np.abs(ret)
    threshold = np.percentile(target_abs[target_abs > 0], 75)
    
    # Compute MAE/MFE and splits
    for i in range(n - h):
        if target_abs[i] <= threshold:
            continue
            
        r = ret[i]
        direction = np.sign(r)
        
        # Path over next h minutes
        path_h = high[i:i+h+1]
        path_l = low[i:i+h+1]
        c0 = close[i]
        
        mfe = (np.max(path_h) - c0) if direction > 0 else (c0 - np.min(path_l))
        mae = (c0 - np.min(path_l)) if direction > 0 else (np.max(path_h) - c0)
        
        if mfe == 0: continue
        
        mae_mfe_ratio = mae / mfe
        
        # "Clean" if MAE is less than 30% of MFE
        if mae_mfe_ratio < 0.30:
            clean_big[i] = 1
            
            # Was it a continuation or reversal of the immediate prior 5m trend?
            if i >= 5:
                prior_ret = close[i] - close[i-5]
                if np.sign(prior_ret) == direction:
                    cont_clean[i] = 1
                else:
                    rev_clean[i] = 1
                    
    return clean_big, cont_clean, rev_clean

def run_model(X, y):
    mask = np.isfinite(y) & np.all(np.isfinite(X), axis=1)
    X, y = X[mask], y[mask]
    n = len(y)
    if n < 400: return 0.5
    
    cut = int(n * 0.7)
    
    if len(np.unique(y[:cut])) < 2 or len(np.unique(y[cut:])) < 2:
        return 0.5
        
    pipe = Pipeline([
        ('scaler', StandardScaler()),
        ('lr', LogisticRegression(max_iter=1000, class_weight='balanced'))
    ])
    
    pipe.fit(X[:cut], y[:cut])
    preds = pipe.predict_proba(X[cut:])[:, 1]
    return roc_auc_score(y[cut:], preds)

def main():
    days = 60
    bars = _load_bars(days)
    if bars is None: return
    n_bars = len(bars["close"])
    print(f"\n{n_bars} minute-bars loaded. Tradability Targets v2.")
    
    y_clean, y_cont, y_rev = make_clean_path_labels(bars)
    
    print(f"Total Big Moves: {np.sum((y_clean==1) | (y_cont==1) | (y_rev==1))}")
    print(f"Clean Big Moves: {np.sum(y_clean)}")
    print(f"Clean Continuations: {np.sum(y_cont)}")
    print(f"Clean Reversals: {np.sum(y_rev)}")
    
    # Features
    features = ["range_compression", "realized_vol", "intensity", "vpin"]
    X = np.column_stack([FEATURE_BUILDERS[f](bars)[0] for f in features])
    
    auc_clean = run_model(X, y_clean)
    auc_cont = run_model(X, y_cont)
    auc_rev = run_model(X, y_rev)
    
    print("\n--- Out-of-Sample AUC ---")
    print(f"P(Clean_Big_Move)   : {auc_clean:.3f}")
    print(f"P(Continuation_Clean) : {auc_cont:.3f}")
    print(f"P(Reversal_Clean)     : {auc_rev:.3f}")

if __name__ == "__main__":
    main()
