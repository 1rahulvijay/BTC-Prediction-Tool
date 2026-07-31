"""
probe_invalidation_events.py
Evaluates specific structural invalidation events instead of general fakeouts.

Targets:
1. Anchor Cross-back (Price crosses back through 60m VWAP within 5m)
2. Direction Flips (VPIN side flips rapidly)
3. Early MAE (MAE exceeds 40% of Expected Move before hitting MFE)
"""

import os
import sys
import numpy as np
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, FEATURE_BUILDERS

def calculate_vwap(close, volume, period=60):
    n = len(close)
    vwap = np.full(n, np.nan)
    for i in range(period, n):
        c_win = close[i-period+1:i+1]
        v_win = volume[i-period+1:i+1]
        if np.sum(v_win) > 0:
            vwap[i] = np.sum(c_win * v_win) / np.sum(v_win)
        else:
            vwap[i] = c_win[-1]
    return vwap

def main():
    bars = _load_bars(60)
    if bars is None: return
    n = len(bars["close"])
    print(f"{n} minute-bars loaded. Evaluating Invalidation Events.")
    
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    volume = bars["vol"]
    
    # Pre-compute VWAP and VPIN Side
    vwap_60m = calculate_vwap(close, volume, 60)
    vpin_raw = FEATURE_BUILDERS["vpin"](bars)[0][:, 0]
    vpin_z = (vpin_raw - np.nanmean(vpin_raw)) / np.nanstd(vpin_raw)
    side = np.where(vpin_z > 1.5, -1, np.where(vpin_z < -1.5, 1, 0))
    
    # Calculate Targets (5m forward window)
    h_win = 5
    
    target_anchor_cross = np.zeros(n)
    target_side_flip = np.zeros(n)
    target_early_mae = np.zeros(n)
    
    # For Expected Move, we use realized vol approximation
    rv_60m = FEATURE_BUILDERS["realized_vol"](bars)[0][:, 0]
    
    for i in range(n - h_win):
        c = close[i]
        anchor = vwap_60m[i]
        cur_side = side[i]
        
        # 1. Anchor Cross-back
        # If currently above anchor, does it drop below anchor in next 5m?
        if np.isfinite(anchor):
            min_l = np.min(low[i+1:i+1+h_win])
            max_h = np.max(high[i+1:i+1+h_win])
            if c > anchor and min_l < anchor:
                target_anchor_cross[i] = 1
            elif c < anchor and max_h > anchor:
                target_anchor_cross[i] = 1
                
        # 2. Side flip
        if cur_side != 0:
            fut_sides = side[i+1:i+1+h_win]
            if -cur_side in fut_sides:
                target_side_flip[i] = 1
                
        # 3. Early MAE
        expected_move = c * rv_60m[i] * np.sqrt(5/60) if np.isfinite(rv_60m[i]) else 0
        if expected_move > 0:
            if cur_side == 1:
                mae = c - np.min(low[i+1:i+1+h_win])
            elif cur_side == -1:
                mae = np.max(high[i+1:i+1+h_win]) - c
            else:
                mae = 0
                
            if mae > 0.40 * expected_move:
                target_early_mae[i] = 1
                
    # Features for invalidation
    dist_from_anchor = np.abs(close - vwap_60m) / vwap_60m
    dist_from_anchor_z = (dist_from_anchor - np.nanmean(dist_from_anchor)) / np.nanstd(dist_from_anchor)
    
    # vpin reversal speed
    vpin_accel = np.diff(vpin_raw, prepend=vpin_raw[0])
    
    # Time above/below anchor (simplified proxy using 10m lookback)
    time_above_anchor = np.zeros(n)
    for i in range(10, n):
        if np.isfinite(vwap_60m[i-10:i]).all():
            time_above_anchor[i] = np.sum(close[i-10:i] > vwap_60m[i-10:i])
            
    features_dict = {
        "dist_from_anchor_z": dist_from_anchor_z,
        "vpin_accel": vpin_accel,
        "time_above_anchor_10m": time_above_anchor,
        "intensity": FEATURE_BUILDERS["intensity"](bars)[0][:, 0]
    }
    
    def eval_target(name, y):
        print(f"\n--- AUC against {name} (Hit Rate: {y.mean():.2%}) ---")
        for fname, x in features_dict.items():
            mask = np.isfinite(x) & np.isfinite(y) & (side != 0) # Evaluate only where we have a side
            if mask.sum() < 100: continue
            
            x_col = x[mask]
            y_col = y[mask]
            try:
                auc = roc_auc_score(y_col, x_col)
                dir_str = "DIRECT " if auc >= 0.5 else "INVERSE"
                auc = auc if auc >= 0.5 else 1 - auc
                print(f"{fname:25s} | AUC: {auc:.3f} | {dir_str}")
            except ValueError:
                pass

    eval_target("Anchor Cross-back", target_anchor_cross)
    eval_target("Direction Side Flip", target_side_flip)
    eval_target("Early MAE > 40% EM", target_early_mae)

if __name__ == "__main__":
    main()
