"""
probe_anchor_behavior.py
Tests whether behavior around a static anchor price (like distance from VWAP or session open) predicts Tradability.
"""
import os
import sys
import numpy as np
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, _roll_sum, _roll_mean
from probes.tradability_helpers import make_tradability_labels

def evaluate_anchor_behavior(bars):
    n = len(bars["close"])
    if n < 100: return
        
    print(f"\n{n} total minute-bars. Testing: ['anchor_behavior']")
    print("\n  feature                    h  tradable_AUC   avoid_AUC")
    
    # Let's proxy an anchor as the 60-minute VWAP
    vol = bars["vol"].clip(1e-9)
    typical_price = (bars["high"] + bars["low"] + bars["close"]) / 3
    vwap_60m = _roll_sum(typical_price * vol, 60) / _roll_sum(vol, 60)
    
    # 1. Distance from anchor
    dist_anchor = np.abs(bars["close"] - vwap_60m)
    hl_60m = _roll_mean(bars["high"] - bars["low"], 60)
    norm_dist = dist_anchor / (hl_60m + 1e-9)
    
    # 2. Cross frequency
    above_anchor = (bars["close"] > vwap_60m).astype(float)
    anchor_cross = (above_anchor != np.roll(above_anchor, 1)).astype(float)
    cross_count_15m = _roll_sum(anchor_cross, 15)
    
    # 3. Time above/below anchor
    time_above_15m = _roll_sum(above_anchor, 15)
    time_below_15m = 15 - time_above_15m
    extreme_sidedness = np.maximum(time_above_15m, time_below_15m) # 15 = fully one-sided
    
    features = {
        "dist_from_anchor": norm_dist,
        "anchor_crosses_15m": cross_count_15m,
        "anchor_sidedness_15m": extreme_sidedness
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
        evaluate_anchor_behavior(bars)
