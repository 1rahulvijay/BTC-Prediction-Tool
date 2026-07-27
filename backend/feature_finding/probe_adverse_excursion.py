"""
probe_adverse_excursion.py
Tests features designed to predict High Adverse Excursion (MAE) and fakeouts before the target is reached.
"""
import os
import sys
import numpy as np
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, _roll_sum

def evaluate_adverse_excursion(bars):
    n = len(bars["close"])
    if n < 100: return
        
    print(f"\n{n} total minute-bars. Testing: ['adverse_excursion']")
    print("\n  feature                    h  high_MAE_AUC")
    
    ret = np.zeros(n)
    logc = np.log(np.where(bars["close"] > 0, bars["close"], 1.0))
    ret[1:] = np.diff(logc)
    
    # Feature 1: VPIN Exhaustion (High flow toxicity, but price is not moving)
    vol = bars["vol"].clip(1)
    dp = np.abs(ret)
    vp = vol * dp
    buy_vol = np.where(ret > 0, vp, 0)
    sell_vol = np.where(ret < 0, vp, 0)
    vpin_level = np.abs(_roll_sum(buy_vol, 15) - _roll_sum(sell_vol, 15)) / _roll_sum(vol, 15).clip(1)
    
    abs_ret_15m = np.abs(_roll_sum(ret, 15))
    vpin_exhaustion = vpin_level / (abs_ret_15m + 1e-9)
    
    # Feature 2: Noise to Trend Ratio
    sum_abs_ret_15m = _roll_sum(np.abs(ret), 15)
    noise_ratio = sum_abs_ret_15m / (abs_ret_15m + 1e-9)
    
    features = {
        "vpin_exhaustion": vpin_exhaustion,
        "noise_to_trend": noise_ratio
    }
    
    # Build Target: MAE / MFE Ratio > 0.50 (Fakeout)
    c = bars["close"]
    high = bars["high"]
    low = bars["low"]
    
    for h in [5, 10, 15]:
        target = np.full(n, np.nan)
        for i in range(n - h):
            start_price = c[i]
            if start_price <= 0: continue
            end_price = c[i+h]
            move = end_price - start_price
            
            h_window = high[i+1:i+h+1]
            l_window = low[i+1:i+h+1]
            if len(h_window) == 0: continue
            
            max_high = np.max(h_window)
            min_low = np.min(l_window)
            
            if move > 0:
                mfe = max_high - start_price
                mae = start_price - min_low
            else:
                mfe = start_price - min_low
                mae = max_high - start_price
                
            mfe = max(0, mfe)
            mae = max(0, mae)
            
            ratio = mae / (mfe + 1e-9)
            # High MAE/MFE ratio means the trade went severely against you before/during the move
            target[i] = (ratio > 0.50).astype(int)
            
        for name, feat in features.items():
            mask = np.isfinite(feat) & ~np.isnan(target)
            if mask.sum() < 100: continue
            
            X_clean = feat[mask]
            y_target = target[mask]
            
            split = int(len(X_clean) * 0.7)
            X_test, y_test = X_clean[split:], y_target[split:]
            
            try:
                auc = roc_auc_score(y_test, X_test)
                if auc < 0.5: auc = 1 - auc
            except:
                auc = 0.5
                
            print(f"  {name:22s} {h:2d}m    {auc:.3f}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()
    bars = _load_bars(args.days)
    if bars is not None:
        evaluate_adverse_excursion(bars)
