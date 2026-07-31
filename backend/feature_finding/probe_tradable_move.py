"""
probe_tradable_move.py
Evaluates features against the P(Tradable_Move) strict target.
Target:
- future_abs_move > p75
- MAE < 0.35 * MFE
- path_efficiency > 0.55
- move_remaining > 3 bps
"""

import os
import sys
import numpy as np
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, FEATURE_BUILDERS

def make_tradable_labels(bars, horizon=5, p_threshold=75):
    n = len(bars["close"])
    mfe = np.zeros(n)
    mae = np.zeros(n)
    path_eff = np.zeros(n)
    
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    
    for i in range(n - horizon):
        c = close[i]
        window_h = high[i+1:i+1+horizon]
        window_l = low[i+1:i+1+horizon]
        window_c = close[i:i+1+horizon]
        
        max_up = np.max(window_h) - c
        max_down = c - np.min(window_l)
        
        if max_up > max_down:
            # Oracle says LONG
            mfe[i] = max_up
            mae[i] = c - np.min(window_l)
        else:
            # Oracle says SHORT
            mfe[i] = max_down
            mae[i] = np.max(window_h) - c
            
        path_len = np.sum(np.abs(np.diff(window_c)))
        if path_len > 0:
            path_eff[i] = mfe[i] / path_len
        else:
            path_eff[i] = 0

    threshold_mfe = np.percentile(mfe[mfe > 0], p_threshold)
    cost_buffer = close * 0.0003  # 3 bps
    
    tradable = (
        (mfe > threshold_mfe) & 
        (mae < 0.35 * mfe) & 
        (path_eff > 0.55) & 
        (mfe > cost_buffer)
    )
    
    return tradable.astype(int), mfe

def main():
    days = 60
    bars = _load_bars(days)
    if bars is None: return
    
    n = len(bars["close"])
    print(f"\n{n} minute-bars loaded. Evaluating P(Tradable_Move).")
    
    y_tradable, mfe = make_tradable_labels(bars, horizon=5, p_threshold=75)
    print(f"Target Hit Rate: {y_tradable.mean():.2%} (Tradable Moves)")
    
    features = ["range_compression", "realized_vol", "intensity", "vpin"]
    
    print("\n--- AUC against P(Tradable_Move) ---")
    for fname in features:
        X, names = FEATURE_BUILDERS[fname](bars)
        mask = np.isfinite(X[:, 0]) & (mfe > 0)
        
        for i, col_name in enumerate(names):
            x_col = X[:, i][mask]
            y_col = y_tradable[mask]
            
            try:
                auc = roc_auc_score(y_col, x_col)
                if auc < 0.5:
                    auc = 1 - auc
                    dir_str = "INVERSE"
                else:
                    dir_str = "DIRECT "
                print(f"{fname}_{col_name:15s} | AUC: {auc:.3f} | {dir_str}")
            except ValueError:
                pass
                
if __name__ == "__main__":
    main()
