"""
probe_big_move_remaining.py
P(Move_Remaining) Engine
Evaluates if a move is 'spent' or if enough volatility remains to overcome slippage.
"""

import os
import sys
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, FEATURE_BUILDERS, _roll_sum, _roll_mean

def main():
    days = 60
    bars = _load_bars(days)
    if bars is None: return
    n_bars = len(bars["close"])
    print(f"\n{n_bars} minute-bars loaded. Evaluating P(Move_Remaining).")
    
    # Target: From THIS minute until the next 5 minutes, is the absolute forward move 
    # greater than a cost-adjusted threshold?
    # Say the expected threshold for a "tradable" move was 75th percentile of normal moves.
    # We want future_abs_move > that + buffer.
    
    h = 5
    ret = np.zeros(n_bars)
    for i in range(n_bars - h):
        ret[i] = bars["close"][i+h] - bars["close"][i]
        
    target_abs = np.abs(ret)
    base_threshold = np.percentile(target_abs[target_abs > 0], 75)
    
    # Assume price ~ 65000, 7 bps = ~45 points. We add a buffer of ~45 points.
    cost_adjusted_threshold = base_threshold + 45.0
    
    y = (target_abs > cost_adjusted_threshold).astype(int)
    
    # Features for "how much is already spent?"
    # 1. recent_expansion_1m
    logc = np.log(np.where(bars["close"] > 0, bars["close"], 1.0))
    ret1 = np.zeros(len(logc)); ret1[1:] = np.diff(logc)
    recent_expansion_1m = np.abs(ret1)
    
    # 2. move_spent_ratio
    # current 3m expansion / expected threshold
    recent_expansion_3m = _roll_sum(np.abs(ret1), 3)
    # Using log scale for threshold approximation: target_abs is in dollars, so convert ret1 to dollars
    ret1_dollars = np.zeros(n_bars); ret1_dollars[1:] = np.diff(bars["close"])
    recent_expansion_3m_dollars = _roll_sum(np.abs(ret1_dollars), 3)
    move_spent_ratio = recent_expansion_3m_dollars / (base_threshold + 1e-9)
    
    # 3. distance_from_compression_range
    # Distance of current close from the 15m VWAP
    typ = (bars["high"] + bars["low"] + bars["close"]) / 3.0
    vwap_15m = _roll_sum(typ * bars["vol"], 15) / _roll_sum(bars["vol"], 15).clip(1)
    distance_from_compression = np.abs(bars["close"] - vwap_15m) / (vwap_15m + 1e-9)
    
    # Combine
    X = np.column_stack([recent_expansion_1m, move_spent_ratio, distance_from_compression])
    valid = np.all(np.isfinite(X), axis=1) & np.isfinite(y)
    
    X = X[valid]
    y = y[valid]
    
    n = len(y)
    cut = int(n * 0.70)
    
    pipe = Pipeline([
        ('scaler', StandardScaler()),
        ('lr', LogisticRegression(max_iter=1000, class_weight='balanced'))
    ])
    
    pipe.fit(X[:cut], y[:cut])
    preds = pipe.predict_proba(X[cut:])[:, 1]
    auc = roc_auc_score(y[cut:], preds)
    
    print("\n--- P(Move_Remaining) Results ---")
    print(f"Target: future_abs_move > {cost_adjusted_threshold:.2f} (Base + Cost)")
    print(f"Out of Sample AUC: {auc:.3f}")
    
    # Coefficients
    coefs = pipe.named_steps['lr'].coef_[0]
    names = ["recent_expansion_1m", "move_spent_ratio", "distance_from_vwap"]
    print("\nFeature Coefficients (Positive = implies more move remains):")
    for name, c in zip(names, coefs):
        print(f"  {name:<25}: {c:+.3f}")
        
    print("\nCONCLUSION: If AUC > 0.55, we can predict if a move is exhausted vs just starting.")

if __name__ == "__main__":
    main()
