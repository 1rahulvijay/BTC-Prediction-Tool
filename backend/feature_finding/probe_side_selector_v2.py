"""
probe_side_selector_v2.py
Side-Selector Tournament v2. Maps side selectors conditionally inside Top Selectivity Buckets.
"""

import os
import sys
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, FEATURE_BUILDERS

# Same cross_val_probs as the scorecard to get purely out of sample Selectivity rankings
def get_selectivity_oos(X, y):
    tscv = TimeSeriesSplit(n_splits=5)
    probs = np.full(len(y), np.nan)
    
    for train_i, test_i in tscv.split(X):
        X_train, X_test = X[train_i], X[test_i]
        y_train = y[train_i]
        
        pipe = Pipeline([
            ('scaler', StandardScaler()),
            ('lr', LogisticRegression(max_iter=1000, class_weight='balanced'))
        ])
        pipe.fit(X_train, y_train)
        probs[test_i] = pipe.predict_proba(X_test)[:, 1]
        
    return probs

def main():
    days = 60
    bars = _load_bars(days)
    if bars is None: return
    n_bars = len(bars["close"])
    print(f"\n{n_bars} minute-bars loaded. Side-Selector Tournament v2.")
    
    # 1. Target Data
    h = 5
    ret = np.zeros(n_bars)
    for i in range(n_bars - h):
        ret[i] = bars["close"][i+h] - bars["close"][i]
        
    target_abs = np.abs(ret)
    threshold = np.percentile(target_abs[target_abs > 0], 75)
    y_big = (target_abs > threshold).astype(int)
    
    # 2. Compute Selectivity
    sel_features = ["realized_vol", "intensity", "vpin", "range_compression", "liquidity_shock"]
    sel_X = np.column_stack([FEATURE_BUILDERS[f](bars)[0] for f in sel_features])
    
    valid = np.all(np.isfinite(sel_X), axis=1)
    p_big = np.full(n_bars, np.nan)
    print("Computing out-of-sample Selectivity rankings...")
    p_big[valid] = get_selectivity_oos(sel_X[valid], y_big[valid])
    
    # 3. Generate side-selector strategies
    # a. VPIN Contrarian: if vpin 15m z-score > 1.5 -> short (-1), < -1.5 -> long (1)
    vpin_raw = FEATURE_BUILDERS["vpin"](bars)[0][:, 0]
    vpin_z = (vpin_raw - np.nanmean(vpin_raw)) / np.nanstd(vpin_raw)
    side_vpin = np.where(vpin_z > 1.5, -1, np.where(vpin_z < -1.5, 1, 0))
    
    # b. CVD Continuation: follow 15m CVD direction if strong
    cvd_raw = FEATURE_BUILDERS["cvd"](bars)[0][:, 2] # 15m ratio
    cvd_z = (cvd_raw - np.nanmean(cvd_raw)) / np.nanstd(cvd_raw)
    side_cvd_cont = np.where(cvd_z > 1.5, 1, np.where(cvd_z < -1.5, -1, 0))
    
    # c. CVD Contrarian/Exhaustion: fade 15m CVD
    side_cvd_fade = -side_cvd_cont
    
    # d. Momentum Continuation: follow recent 5m return
    momo_raw = FEATURE_BUILDERS["autocorr"](bars)[0][:, 2] # recent ret normalized
    side_momo = np.where(momo_raw > 1.0, 1, np.where(momo_raw < -1.0, -1, 0))
    
    # e. Absorption Fade: fade the aggressive flow if absorption flag is high
    abs_mag = FEATURE_BUILDERS["absorption"](bars)[0][:, 0]
    abs_dir = FEATURE_BUILDERS["absorption"](bars)[0][:, 1]
    side_abs = np.where((abs_mag > 0.05) & (abs_dir > 0), -1, 
                        np.where((abs_mag > 0.05) & (abs_dir < 0), 1, 0))
                        
    # Session masks
    sess_x = FEATURE_BUILDERS["session"](bars)[0]
    is_asia = sess_x[:, 2] == 1
    is_us = sess_x[:, 3] == 1
    is_eu = ~(is_asia | is_us) # proxy
    
    # 4. Evaluate conditional mapping
    oos_mask = ~np.isnan(p_big)
    
    top_1_thresh = np.percentile(p_big[oos_mask], 99)
    top_5_thresh = np.percentile(p_big[oos_mask], 95)
    top_10_thresh = np.percentile(p_big[oos_mask], 90)
    
    strategies = {
        "VPIN Contrarian": side_vpin,
        "CVD Continuation": side_cvd_cont,
        "CVD Exhaustion (Fade)": side_cvd_fade,
        "Momentum Continuation": side_momo,
        "Absorption Fade": side_abs
    }
    
    def evaluate_side_selector(strat_name, side_array, mask_name, mask_idx):
        # We only care when the side_array actually triggers a signal (side != 0)
        eval_mask = mask_idx & (side_array != 0) & oos_mask
        idx = np.where(eval_mask)[0]
        n_signals = len(idx)
        
        if n_signals == 0:
            return None
            
        correct = 0
        for i in idx:
            if np.sign(ret[i]) == side_array[i]:
                correct += 1
                
        acc = correct / n_signals
        
        # Wilson lower bound approximation (95% confidence)
        z = 1.96
        phat = acc
        wilson = (phat + z*z/(2*n_signals) - z * np.sqrt((phat*(1-phat)+z*z/(4*n_signals))/n_signals)) / (1 + z*z/n_signals)
        
        return acc, wilson, n_signals

    print("\n================ SIDE-SELECTOR TOURNAMENT v2 ================")
    
    buckets = {
        "Top 1% Selectivity": p_big > top_1_thresh,
        "Top 5% Selectivity": p_big > top_5_thresh,
        "Top 10% Selectivity": p_big > top_10_thresh,
        "Top 5% (Asia Only)": (p_big > top_5_thresh) & is_asia,
        "Top 5% (US Only)": (p_big > top_5_thresh) & is_us,
        "Top 5% (Europe Only)": (p_big > top_5_thresh) & is_eu
    }
    
    for bucket_name, bucket_mask in buckets.items():
        print(f"\n--- {bucket_name} ---")
        results = []
        for strat_name, strat_side in strategies.items():
            res = evaluate_side_selector(strat_name, strat_side, bucket_name, bucket_mask)
            if res is not None:
                acc, wilson, n_sig = res
                results.append((acc, wilson, n_sig, strat_name))
        
        # Sort by Wilson lower bound
        results.sort(key=lambda x: x[1], reverse=True)
        
        for acc, wilson, n_sig, strat_name in results:
            print(f"  {strat_name:<25} | Acc: {acc:.1%} | Wilson LB: {wilson:.1%} | N: {n_sig}")

if __name__ == "__main__":
    main()
