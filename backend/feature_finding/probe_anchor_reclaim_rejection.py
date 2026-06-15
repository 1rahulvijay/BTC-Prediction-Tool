"""
probe_anchor_reclaim_rejection.py
Tests Anchor VWAP Reclaim/Rejection specifically inside high selectivity buckets.
"""

import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, FEATURE_BUILDERS

def wilson_lower_bound(successes, n, z=1.96):
    if n == 0: return 0.0
    p = successes / n
    denominator = 1 + z**2/n
    centre_adjusted_probability = p + z*z / (2*n)
    adjusted_standard_deviation = np.sqrt((p*(1 - p) + z*z / (4*n)) / n)
    lower_bound = (centre_adjusted_probability - z*adjusted_standard_deviation) / denominator
    return lower_bound

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
    print(f"{n} minute-bars loaded. Evaluating Conditional Anchor Reclaim/Rejection.")
    
    close = bars["close"]
    volume = bars["vol"]
    
    vwap_60m = calculate_vwap(close, volume, 60)
    dist_anchor = close - vwap_60m
    
    # 5m Forward returns
    h = 5
    ret = np.zeros(n)
    for i in range(n - h):
        ret[i] = close[i+h] - close[i]
        
    # Get Selectivity Score (using simplified realized vol as proxy for offline fast probe, or full pipeline if available)
    # Since we don't have the cross-val probabilities globally stored, we will use a naive proxy for Top 5% Selectivity:
    # realized_vol > 90th AND range_compression > 90th
    rv = FEATURE_BUILDERS["realized_vol"](bars)[0][:, 0]
    rc = FEATURE_BUILDERS["range_compression"](bars)[0][:, 0]
    
    rv_90 = np.nanpercentile(rv, 90)
    rc_90 = np.nanpercentile(rc, 90)
    
    is_high_selectivity = (rv > rv_90) & (rc > rc_90)
    
    # Define Anchor Conditions
    # 1. Extreme Distance Reversion (Price is far from VWAP, fade back to VWAP)
    dist_z = (dist_anchor - np.nanmean(dist_anchor)) / np.nanstd(dist_anchor)
    signal_revert = np.where(dist_z > 2.0, -1, np.where(dist_z < -2.0, 1, 0))
    
    # 2. Anchor Bounce (Price is touching VWAP, bet on continuation of prior trend)
    # Proxied by: distance is very small (abs(z) < 0.2), assume rejection away from VWAP
    # We need a trend proxy, e.g., 24h VWAP or just the sign of 60m return
    ret_60m = np.zeros(n)
    for i in range(60, n):
        ret_60m[i] = close[i] - close[i-60]
    
    signal_bounce = np.where((np.abs(dist_z) < 0.2) & (ret_60m > 0), 1, 
                    np.where((np.abs(dist_z) < 0.2) & (ret_60m < 0), -1, 0))
    
    def evaluate(name, signal_arr, condition_mask):
        idx = np.where((signal_arr != 0) & condition_mask)[0]
        n_sig = len(idx)
        print(f"\n--- {name} ---")
        if n_sig == 0:
            print("0 signals.")
            return
            
        correct = 0
        for i in idx:
            if np.sign(ret[i]) == signal_arr[i]:
                correct += 1
                
        acc = correct / n_sig
        lb = wilson_lower_bound(correct, n_sig)
        print(f"Signals: {n_sig}")
        print(f"Accuracy: {acc:.1%}")
        print(f"Wilson LB: {lb:.1%}")

    print("\n=== GLOBAL PERFORMANCE (Baseline) ===")
    evaluate("Extreme Reversion (Global)", signal_revert, np.ones(n, dtype=bool))
    evaluate("Anchor Bounce (Global)", signal_bounce, np.ones(n, dtype=bool))
    
    print("\n=== CONDITIONAL PERFORMANCE (Top Selectivity Bucket) ===")
    evaluate("Extreme Reversion (High Selectivity)", signal_revert, is_high_selectivity)
    evaluate("Anchor Bounce (High Selectivity)", signal_bounce, is_high_selectivity)

if __name__ == "__main__":
    main()
