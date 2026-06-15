"""
probe_side_selector_tournament.py
Evaluates multiple side-selectors exclusively during Selectivity v2 top percentiles.
Calculates accuracy and Wilson lower bounds.
"""

import os
import sys
import pickle
import numpy as np
from sklearn.metrics import accuracy_score
import scipy.stats as st

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, FEATURE_BUILDERS, _roll_sum, _roll_mean

def wilson_lower_bound(pos, n, confidence=0.95):
    if n == 0:
        return 0.0
    z = st.norm.ppf(1 - (1 - confidence) / 2)
    phat = pos / n
    return (phat + z*z/(2*n) - z * np.sqrt((phat*(1-phat)+z*z/(4*n))/n)) / (1+z*z/n)

def compute_vpin_transitions(bars):
    n = len(bars["close"])
    vol = bars["vol"].clip(1)
    ret = np.zeros(n)
    logc = np.log(np.where(bars["close"] > 0, bars["close"], 1.0))
    ret[1:] = np.diff(logc)
    dp = np.abs(ret)
    vp = vol * dp
    buy_vol = np.where(ret > 0, vp, 0)
    sell_vol = np.where(ret < 0, vp, 0)
    vpin_level = np.abs(_roll_sum(buy_vol, 15) - _roll_sum(sell_vol, 15)) / _roll_sum(vol, 15).clip(1)
    vpin_slope_5m = vpin_level - np.roll(vpin_level, 5)
    vpin_accel = vpin_slope_5m - np.roll(vpin_slope_5m, 5)
    return np.column_stack([np.abs(vpin_slope_5m), np.abs(vpin_accel)])

def compute_liquidity_shock(bars):
    hl = bars["high"] - bars["low"]
    hl_mean_60 = _roll_mean(hl, 60)
    shock = hl / (hl_mean_60 + 1e-9)
    return shock.reshape(-1, 1)

def evaluate_tournament(bars):
    n = len(bars["close"])
    if n < 500: return
    
    # Load Selectivity Model v2
    model_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "selectivity_model_v2.pkl")
    if not os.path.exists(model_path):
        print("Selectivity v2 model not found.")
        return
        
    with open(model_path, "rb") as f:
        v2_data = pickle.load(f)
    pipe = v2_data["pipeline"]
    
    # Rebuild Selectivity Features
    features_to_use = ["range_compression", "realized_vol", "intensity", "vpin"]
    X_sel_list = []
    for fname in features_to_use:
        X_f, _ = FEATURE_BUILDERS[fname](bars)
        X_sel_list.append(X_f)
    X_sel_list.append(compute_liquidity_shock(bars))
    X_sel_list.append(compute_vpin_transitions(bars))
    X_sel = np.column_stack(X_sel_list)
    
    valid_sel = np.all(np.isfinite(X_sel), axis=1)
    p_big_move = np.zeros(n)
    p_big_move[valid_sel] = pipe.predict_proba(X_sel[valid_sel])[:, 1]
    
    # Target Direction (Oracle 5m return)
    h = 5
    ret = np.zeros(n)
    for i in range(n - h):
        ret[i] = bars["close"][i+h] - bars["close"][i]
    target_dir = np.sign(ret)
    
    # Side Selectors
    # 1. Momentum 5m
    mom_5 = bars["close"] - np.roll(bars["close"], 5)
    dir_mom_5 = np.sign(mom_5)
    
    # 2. VPIN Imbalance (15m)
    vol = bars["vol"].clip(1)
    bar_ret = np.zeros(n)
    logc = np.log(np.where(bars["close"] > 0, bars["close"], 1.0))
    bar_ret[1:] = np.diff(logc)
    vp = vol * np.abs(bar_ret)
    buy_vol = np.where(bar_ret > 0, vp, 0)
    sell_vol = np.where(bar_ret < 0, vp, 0)
    buy_15 = _roll_sum(buy_vol, 15)
    sell_15 = _roll_sum(sell_vol, 15)
    dir_vpin = np.sign(buy_15 - sell_15)
    # INVERSE VPIN (Contrarian Trap)
    dir_vpin_contra = -dir_vpin
    
    # 3. Anchor VWAP 60m
    typ = (bars["high"] + bars["low"] + bars["close"]) / 3
    vwap_60 = _roll_sum(typ * bars["vol"], 60) / (_roll_sum(bars["vol"], 60) + 1e-9)
    dir_anchor = np.sign(bars["close"] - vwap_60)
    
    # 4. Range Breakout Side
    highest_15 = np.zeros(n)
    lowest_15 = np.zeros(n)
    for i in range(15, n):
        highest_15[i] = np.max(bars["high"][i-15:i])
        lowest_15[i] = np.min(bars["low"][i-15:i])
    dir_breakout = np.where(bars["close"] >= highest_15, 1, np.where(bars["close"] <= lowest_15, -1, 0))
    
    predictors = {
        "Simple Momentum (5m)": dir_mom_5,
        "VPIN Contrarian Trap": dir_vpin_contra,
        "Anchor VWAP (60m)": dir_anchor,
        "Range Breakout (15m)": dir_breakout
    }
    
    split = int(n * 0.70)
    p_big_move_test = p_big_move[split:]
    target_dir_test = target_dir[split:]
    ranks = np.argsort(p_big_move_test)[::-1]
    
    print("\n================ SIDE SELECTOR TOURNAMENT ================")
    
    for p_top in [0.10, 0.05, 0.01]:
        cutoff = int(len(p_big_move_test) * p_top)
        idx = ranks[:cutoff]
        y_true = target_dir_test[idx]
        
        print(f"\n--- Top {p_top:.0%} Selectivity Windows (N = {cutoff}) ---")
        print(f"{'Selector':<25} | {'Acc':<6} | {'Wilson LB':<9} | {'Count'}")
        
        for name, pred in predictors.items():
            pred_test = pred[split:][idx]
            mask = (y_true != 0) & (pred_test != 0)
            valid_n = mask.sum()
            
            if valid_n < 10:
                print(f"{name:<25} | {'N/A':<6} | {'N/A':<9} | {valid_n}")
                continue
                
            acc = accuracy_score(y_true[mask], pred_test[mask])
            pos = int(acc * valid_n)
            lb = wilson_lower_bound(pos, valid_n)
            
            print(f"{name:<25} | {acc:.1%}  | {lb:.1%}     | {valid_n}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()
    bars = _load_bars(args.days)
    if bars is not None:
        evaluate_tournament(bars)
