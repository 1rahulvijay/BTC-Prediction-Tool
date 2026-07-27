"""
probe_compression_ignition.py
Test the "Coil + Spark" hypothesis: Range compression followed by trade count/CVD acceleration.
"""
import os
import sys
import numpy as np
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, make_labels, _roll_mean

def evaluate_ignition(bars):
    n = len(bars["close"])
    if n < 100:
        return
        
    print(f"\n{n} total minute-bars. Testing: ['compression_ignition']")
    print("\n  feature                    h  big_move_AUC")
    
    # Range Compression
    h_15 = _roll_mean(bars["high"], 15)
    l_15 = _roll_mean(bars["low"], 15)
    range_15 = h_15 - l_15
    h_60 = _roll_mean(bars["high"], 60)
    l_60 = _roll_mean(bars["low"], 60)
    range_60 = h_60 - l_60
    compression = range_15 / (range_60 + 1e-9)
    # invert so higher = more compressed
    is_compressed = np.where(compression < 0.5, 1.0, 0.0) 
    
    # Ignition
    count_avg = _roll_mean(bars["count"], 15)
    count_accel = bars["count"] / (count_avg + 1e-9)
    
    cvd = bars["taker_buy"] - bars["taker_sell"]
    cvd_abs_avg = _roll_mean(np.abs(cvd), 15)
    cvd_accel = np.abs(cvd) / (cvd_abs_avg + 1e-9)
    
    # Coil + Spark Features
    features = {
        "ignite_count": is_compressed * count_accel,
        "ignite_cvd": is_compressed * cvd_accel
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
        evaluate_ignition(bars)
