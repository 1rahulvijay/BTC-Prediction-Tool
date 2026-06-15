"""
probe_compression_duration.py
Test if the duration of range compression predicts Tradability better than depth alone.
"""
import os
import sys
import numpy as np
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, _roll_sum, _roll_mean
from probes.tradability_helpers import make_tradability_labels

def evaluate_compression_duration(bars):
    n = len(bars["close"])
    if n < 100: return
        
    print(f"\n{n} total minute-bars. Testing: ['compression_duration']")
    print("\n  feature                    h  tradable_AUC   avoid_AUC")
    
    h_15 = _roll_mean(bars["high"], 15)
    l_15 = _roll_mean(bars["low"], 15)
    range_15 = h_15 - l_15
    h_60 = _roll_mean(bars["high"], 60)
    l_60 = _roll_mean(bars["low"], 60)
    range_60 = h_60 - l_60
    
    compression = range_15 / (range_60 + 1e-9)
    is_compressed = (compression < 0.5).astype(float)
    
    # Measure consecutive minutes compressed
    # Since we can't easily vectorize a cumulative sum with reset without pandas or loops,
    # we'll approximate duration by smoothing the binary flag over large windows.
    # If the window has been compressed 90% of the time over the last 30 minutes:
    duration_30m = _roll_sum(is_compressed, 30)
    duration_60m = _roll_sum(is_compressed, 60)
    
    features = {
        "duration_30m": duration_30m,
        "duration_60m": duration_60m
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
        evaluate_compression_duration(bars)
