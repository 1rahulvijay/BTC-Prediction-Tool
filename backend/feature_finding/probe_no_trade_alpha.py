"""
probe_no_trade_alpha.py
Test features explicitly designed to predict CHOP or LOW_MOVE (P(Avoid)).
"""
import os
import sys
import numpy as np
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, make_labels, _roll_sum, _roll_mean

def evaluate_no_trade_alpha(bars):
    n = len(bars["close"])
    if n < 100: return
        
    print(f"\n{n} total minute-bars. Testing: ['no_trade_alpha']")
    print("\n  feature                    h  avoid_AUC (P(Chop))")
    
    # Features predicting Chop
    # 1. High frequency of direction flips (chop proxy)
    ret = np.zeros(n)
    logc = np.log(np.where(bars["close"] > 0, bars["close"], 1.0))
    ret[1:] = np.diff(logc)
    
    direction_flips = (np.sign(ret) != np.sign(np.roll(ret, 1))).astype(float)
    flip_rate_15m = _roll_mean(direction_flips, 15)
    
    # 2. Low trade efficiency (Price moved little despite high volume)
    abs_ret_15m = np.abs(_roll_sum(ret, 15))
    vol_15m = _roll_sum(bars["vol"], 15).clip(1)
    flow_efficiency = abs_ret_15m / vol_15m
    
    # 3. Wick pressure asymmetry (lots of long wicks = chop)
    range_m = (bars["high"] - bars["low"]).clip(1e-9)
    open_proxy = np.roll(bars["close"], 1)
    upper_wick = bars["high"] - np.maximum(open_proxy, bars["close"])
    lower_wick = np.minimum(open_proxy, bars["close"]) - bars["low"]
    wick_ratio = (upper_wick + lower_wick) / range_m
    avg_wick_ratio_15m = _roll_mean(wick_ratio, 15)
    
    features = {
        "flip_rate_15m": flip_rate_15m,
        "inefficiency_15m": 1.0 / (flow_efficiency + 1e-9),  # higher = more inefficient = chop
        "avg_wick_ratio_15m": avg_wick_ratio_15m
    }
    
    for h in [3, 5, 10, 15]:
        _, absm = make_labels(bars["close"], h)
        # Target: Is the absolute move in the BOTTOM 25%? (Chop/Avoid)
        chop_threshold = np.nanpercentile(absm, 25)
        chop_target = (absm <= chop_threshold).astype(int)
        
        for name, feat in features.items():
            mask = np.isfinite(absm) & np.isfinite(feat)
            if mask.sum() < 100: continue
            X_clean, y_clean = feat[mask], chop_target[mask]
            
            split = int(len(X_clean) * 0.7)
            X_test, y_test = X_clean[split:], y_clean[split:]
            
            try:
                auc = roc_auc_score(y_test, X_test)
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
        evaluate_no_trade_alpha(bars)
