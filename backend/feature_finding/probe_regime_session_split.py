"""
probe_regime_session_split.py
Cross-tabulates the VPIN Contrarian Side-Selector across Global Regimes and Sessions.
"""

import os
import sys
import pickle
import numpy as np
import pandas as pd
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

def evaluate_regimes(bars):
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
    
    # Target Direction
    h = 5
    ret = np.zeros(n)
    for i in range(n - h):
        ret[i] = bars["close"][i+h] - bars["close"][i]
    target_dir = np.sign(ret)
    
    # VPIN Contrarian Trap
    vol = bars["vol"].clip(1)
    bar_ret = np.zeros(n)
    logc = np.log(np.where(bars["close"] > 0, bars["close"], 1.0))
    bar_ret[1:] = np.diff(logc)
    vp = vol * np.abs(bar_ret)
    buy_vol = np.where(bar_ret > 0, vp, 0)
    sell_vol = np.where(bar_ret < 0, vp, 0)
    dir_vpin = np.sign(_roll_sum(buy_vol, 15) - _roll_sum(sell_vol, 15))
    dir_vpin_contra = -dir_vpin
    
    # Regimes
    hl = bars["high"] - bars["low"]
    hl_1440 = _roll_mean(hl, 1440)
    regime = np.where(hl > hl_1440 * 1.5, "VOLATILE", 
              np.where(hl < hl_1440 * 0.5, "LOW_VOL", "CHOP"))
              
    # Sessions
    # 0=Asia (00-08 UTC), 1=Europe (08-16 UTC), 2=US (16-00 UTC)
    hours = (np.arange(n) / 60) % 24
    session = np.where(hours < 8, "Asia", 
                np.where(hours < 16, "Europe", "US"))
                
    split = int(n * 0.70)
    p_test = p_big_move[split:]
    target_test = target_dir[split:]
    pred_test = dir_vpin_contra[split:]
    regime_test = regime[split:]
    session_test = session[split:]
    
    ranks = np.argsort(p_test)[::-1]
    cutoff = int(len(p_test) * 0.01) # Top 1% only
    idx = ranks[:cutoff]
    
    y_true = target_test[idx]
    y_pred = pred_test[idx]
    reg = regime_test[idx]
    sess = session_test[idx]
    
    mask = (y_true != 0) & (y_pred != 0)
    
    df = pd.DataFrame({
        "y_true": y_true[mask],
        "y_pred": y_pred[mask],
        "regime": reg[mask],
        "session": sess[mask],
        "correct": (y_true[mask] == y_pred[mask]).astype(int)
    })
    
    print("\n================ VPIN CONTRARIAN TRAP: REGIME/SESSION SPLIT (TOP 1%) ================")
    
    print("\n--- By Session ---")
    sess_agg = df.groupby("session").agg(
        Count=("correct", "count"),
        Acc=("correct", "mean")
    )
    for s in ["Asia", "Europe", "US"]:
        if s in sess_agg.index:
            row = sess_agg.loc[s]
            lb = wilson_lower_bound(int(row["Acc"] * row["Count"]), int(row["Count"]))
            print(f"{s:<10} | Acc: {row['Acc']:.1%} | Wilson LB: {lb:.1%} | N: {int(row['Count'])}")
            
    print("\n--- By Regime ---")
    reg_agg = df.groupby("regime").agg(
        Count=("correct", "count"),
        Acc=("correct", "mean")
    )
    for r in ["LOW_VOL", "CHOP", "VOLATILE"]:
        if r in reg_agg.index:
            row = reg_agg.loc[r]
            lb = wilson_lower_bound(int(row["Acc"] * row["Count"]), int(row["Count"]))
            print(f"{r:<10} | Acc: {row['Acc']:.1%} | Wilson LB: {lb:.1%} | N: {int(row['Count'])}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=60)
    args = ap.parse_args()
    bars = _load_bars(args.days)
    if bars is not None:
        evaluate_regimes(bars)
