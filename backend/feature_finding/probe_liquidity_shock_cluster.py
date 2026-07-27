"""
probe_liquidity_shock_cluster.py
Test if clustered liquidity shocks predict tradable volatility better than single shocks.
"""
import os
import sys
import numpy as np
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, _roll_sum, _roll_mean
from probes.tradability_helpers import make_tradability_labels

def evaluate_shock_cluster(bars):
    n = len(bars["close"])
    if n < 100: return
        
    print(f"\n{n} total minute-bars. Testing: ['liquidity_shock_cluster']")
    print("\n  feature                    h  tradable_AUC   avoid_AUC")
    
    hl = bars["high"] - bars["low"]
    hl_mean_60 = _roll_mean(hl, 60)
    
    shock = hl / (hl_mean_60 + 1e-9)
    is_shock = (shock > 2.5).astype(int)  # 2.5x variance spike
    
    shock_count_5m = _roll_sum(is_shock, 5)
    shock_count_15m = _roll_sum(is_shock, 15)
    
    features = {
        "shock_count_5m": shock_count_5m,
        "shock_count_15m": shock_count_15m
    }
    
    for h in [5, 10, 15]:
        is_tradable, is_chop = make_tradability_labels(bars, h)
        
        for name, feat in features.items():
            mask = np.isfinite(feat) & ~np.isnan(is_tradable)
            if mask.sum() < 100: continue
            
            X_clean = feat[mask]
            y_tradable = is_tradable[mask]
            y_chop = is_chop[mask]
            
            split = int(len(X_clean) * 0.7)
            X_test, y_t_test, y_c_test = X_clean[split:], y_tradable[split:], y_chop[split:]
            
            try:
                auc_t = roc_auc_score(y_t_test, X_test)
                if auc_t < 0.5: auc_t = 1 - auc_t
                auc_c = roc_auc_score(y_c_test, X_test)
                if auc_c < 0.5: auc_c = 1 - auc_c
            except:
                auc_t, auc_c = 0.5, 0.5
                
            print(f"  {name:22s} {h:2d}m    {auc_t:.3f}          {auc_c:.3f}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=21)
    args = ap.parse_args()
    bars = _load_bars(args.days)
    if bars is not None:
        evaluate_shock_cluster(bars)
