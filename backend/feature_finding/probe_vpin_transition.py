"""
probe_vpin_transition.py
Test if the *derivative* (slope/acceleration) of VPIN predicts big moves or chop better than level.
"""
import os
import sys
import numpy as np
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, make_labels, _roll_sum

def evaluate_vpin_transition(bars):
    n = len(bars["close"])
    if n < 100: return
        
    print(f"\n{n} total minute-bars. Testing: ['vpin_transition']")
    print("\n  feature                    h  big_move_AUC")
    
    # 1. Base VPIN
    vol = bars["vol"].clip(1)
    ret = np.zeros(n)
    logc = np.log(np.where(bars["close"] > 0, bars["close"], 1.0))
    ret[1:] = np.diff(logc)
    
    dp = np.abs(ret)
    vp = vol * dp
    buy_vol = np.where(ret > 0, vp, 0)
    sell_vol = np.where(ret < 0, vp, 0)
    
    vpin_level = np.abs(_roll_sum(buy_vol, 15) - _roll_sum(sell_vol, 15)) / _roll_sum(vol, 15).clip(1)
    
    # 2. VPIN Transitions
    vpin_slope_5m = vpin_level - np.roll(vpin_level, 5)
    vpin_slope_15m = vpin_level - np.roll(vpin_level, 15)
    vpin_accel = vpin_slope_5m - np.roll(vpin_slope_5m, 5)
    
    features = {
        "vpin_slope_5m": vpin_slope_5m,
        "vpin_slope_15m": vpin_slope_15m,
        "vpin_accel": vpin_accel
    }
    
    for h in [3, 5, 10, 15]:
        _, absm = make_labels(bars["close"], h)
        bm_threshold = np.nanpercentile(absm, 75)
        bm_target = (absm > bm_threshold).astype(int)
        
        for name, feat in features.items():
            mask = np.isfinite(absm) & np.isfinite(feat)
            if mask.sum() < 100: continue
            X_clean, y_clean = feat[mask], bm_target[mask]
            
            split = int(len(X_clean) * 0.7)
            X_test, y_test = X_clean[split:], y_clean[split:]
            
            try:
                # Testing if absolute slope/acceleration predicts volatility
                auc = roc_auc_score(y_test, np.abs(X_test)) 
            except:
                auc = 0.5
            print(f"  {name:22s} {h:2d}m    {auc:.3f}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=21)
    args = ap.parse_args()
    bars = _load_bars(args.days)
    if bars is not None:
        evaluate_vpin_transition(bars)
