"""
probe_anchor_reclaim.py
Evaluates Anchor VWAP reclaim failures and distance-from-anchor mean reversion.
"""

import os
import sys
import numpy as np
from sklearn.metrics import accuracy_score

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, _roll_sum, _roll_mean

def main():
    days = 60
    bars = _load_bars(days)
    if bars is None: return
    
    n = len(bars["close"])
    print(f"\n{n} minute-bars loaded. Evaluating Anchor Reclaim.")
    
    typ = (bars["high"] + bars["low"] + bars["close"]) / 3
    vwap_60 = _roll_sum(typ * bars["vol"], 60) / (_roll_sum(bars["vol"], 60) + 1e-9)
    
    dist_anchor = bars["close"] - vwap_60
    dist_anchor_z = (dist_anchor - _roll_mean(dist_anchor, 60)) / (np.std(dist_anchor) + 1e-9)
    
    # Target: 5m move
    h = 5
    ret = np.zeros(n)
    for i in range(n - h):
        ret[i] = bars["close"][i+h] - bars["close"][i]
    target_dir = np.sign(ret)
    
    # Simple strategy: Reversion from extreme anchor distance
    # If price is extremely far above anchor (z > 2), predict short (-1)
    # If price is extremely far below anchor (z < -2), predict long (+1)
    pred_dir = np.where(dist_anchor_z > 2.0, -1, 
                  np.where(dist_anchor_z < -2.0, 1, 0))
                  
    split = int(n * 0.70)
    target_test = target_dir[split:]
    pred_test = pred_dir[split:]
    
    mask = (target_test != 0) & (pred_test != 0)
    valid_n = mask.sum()
    
    if valid_n > 0:
        acc = accuracy_score(target_test[mask], pred_test[mask])
        print("Mean Reversion from Extreme Anchor Distance (Z > 2.0):")
        print(f"Accuracy: {acc:.2%} | Sample Size: {valid_n}")
    else:
        print("No signals found.")

if __name__ == "__main__":
    main()
