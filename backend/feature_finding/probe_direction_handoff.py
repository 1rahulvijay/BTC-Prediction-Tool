"""
probe_direction_handoff.py
Conditional Directional Handoff Testing.
Tests directional predictors *exclusively* when the Selectivity v2 model predicts a high P(Big_Move).
"""

import os
import sys
import pickle
import numpy as np
from sklearn.metrics import accuracy_score

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, FEATURE_BUILDERS, _roll_sum, _roll_mean

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

def evaluate_direction_handoff(bars):
    n = len(bars["close"])
    if n < 500: return
    print(f"\n{n} minute-bars loaded. Evaluating Directional Handoff.")
    
    # 1. Load Selectivity Model v2
    model_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data", "selectivity_model_v2.pkl")
    if not os.path.exists(model_path):
        print("Selectivity v2 model not found. Run train_selectivity_model_v2.py first.")
        return
        
    with open(model_path, "rb") as f:
        v2_data = pickle.load(f)
    pipe = v2_data["pipeline"]
    
    # 2. Rebuild Selectivity Features
    features_to_use = ["range_compression", "realized_vol", "intensity", "vpin"]
    X_sel_list = []
    for fname in features_to_use:
        X_f, _ = FEATURE_BUILDERS[fname](bars)
        X_sel_list.append(X_f)
        
    X_shock = compute_liquidity_shock(bars)
    X_sel_list.append(X_shock)
    
    X_vpin_t = compute_vpin_transitions(bars)
    X_sel_list.append(X_vpin_t)
    
    X_sel = np.column_stack(X_sel_list)
    
    # Predict Selectivity
    # Mask out NaNs
    valid_sel = np.all(np.isfinite(X_sel), axis=1)
    p_big_move = np.zeros(n)
    p_big_move[valid_sel] = pipe.predict_proba(X_sel[valid_sel])[:, 1]
    
    # 3. Build Target Direction
    h = 5
    ret = np.zeros(n)
    for i in range(n - h):
        ret[i] = bars["close"][i+h] - bars["close"][i]
    target_dir = np.sign(ret)
    
    # 4. Build Directional Predictors
    # A. Momentum (5m Return)
    mom_5 = bars["close"] - np.roll(bars["close"], 5)
    dir_mom_5 = np.sign(mom_5)
    
    # B. VPIN Direction
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
    
    # C. VWAP Anchor Sidedness
    typ = (bars["high"] + bars["low"] + bars["close"]) / 3
    vwap_60 = _roll_sum(typ * bars["vol"], 60) / (_roll_sum(bars["vol"], 60) + 1e-9)
    dir_anchor = np.sign(bars["close"] - vwap_60)
    
    predictors = {
        "Simple Momentum (5m)": dir_mom_5,
        "VPIN Flow Imbalance (15m)": dir_vpin,
        "Anchor VWAP (60m) Sidedness": dir_anchor
    }
    
    # 5. Evaluate Conditionally
    # Only evaluate on the OOS portion (last 30%)
    split = int(n * 0.70)
    p_big_move_test = p_big_move[split:]
    target_dir_test = target_dir[split:]
    
    ranks = np.argsort(p_big_move_test)[::-1]
    
    print("\n  Direction Selector            Top 10% Acc    Top 5% Acc    Top 1% Acc")
    
    for name, pred in predictors.items():
        pred_test = pred[split:]
        
        accs = []
        for p_top in [0.10, 0.05, 0.01]:
            cutoff = int(len(p_big_move_test) * p_top)
            idx = ranks[:cutoff]
            
            y_true = target_dir_test[idx]
            y_pred = pred_test[idx]
            
            # Mask out cases where return is 0 or prediction is 0
            mask = (y_true != 0) & (y_pred != 0)
            if mask.sum() < 10:
                accs.append(0.0)
            else:
                accs.append(accuracy_score(y_true[mask], y_pred[mask]))
                
        print(f"  {name:28s} {accs[0]:.1%}          {accs[1]:.1%}         {accs[2]:.1%}")
        
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()
    bars = _load_bars(args.days)
    if bars is not None:
        evaluate_direction_handoff(bars)
