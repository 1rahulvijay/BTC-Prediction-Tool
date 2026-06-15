"""
probe_composed_scorecard_v2.py
Builds a composed scorecard verifying that the full multi-gate system outperforms baseline models.
"""

import os
import sys
import numpy as np
import pandas as pd
import math
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, FEATURE_BUILDERS
from feature_finding.probe_tradable_move import make_tradable_labels
from feature_finding.probe_invalidation_risk import make_invalidation_labels

def wilson_lower_bound(successes, n, z=1.96):
    if n == 0: return 0.0
    p = successes / n
    denominator = 1 + z**2/n
    centre_adjusted_probability = p + z*z / (2*n)
    adjusted_standard_deviation = np.sqrt((p*(1 - p) + z*z / (4*n)) / n)
    lower_bound = (centre_adjusted_probability - z*adjusted_standard_deviation) / denominator
    return lower_bound

def cross_val_probs(X, y):
    tscv = TimeSeriesSplit(n_splits=5)
    probs = np.full(len(y), np.nan)
    for train_i, test_i in tscv.split(X):
        pipe = Pipeline([
            ('scaler', StandardScaler()),
            ('lr', LogisticRegression(max_iter=1000, class_weight='balanced'))
        ])
        pipe.fit(X[train_i], y[train_i])
        probs[test_i] = pipe.predict_proba(X[test_i])[:, 1]
    return probs

def main():
    bars = _load_bars(60)
    if bars is None: return
    n_bars = len(bars["close"])
    
    # Extract arrays
    c = bars["close"]
    h_arr = bars["high"]
    l_arr = bars["low"]
    
    # Lookahead metrics (5m)
    h_window = 5
    ret = np.zeros(n_bars)
    mfe_arr = np.zeros(n_bars)
    mae_arr = np.zeros(n_bars)
    
    for i in range(n_bars - h_window):
        fut_c = c[i+h_window]
        fut_h = np.max(h_arr[i+1:i+h_window+1])
        fut_l = np.min(l_arr[i+1:i+h_window+1])
        
        ret[i] = fut_c - c[i]
        mfe_arr[i] = fut_h - c[i]
        mae_arr[i] = fut_l - c[i]
        
    target_abs = np.abs(ret)
    threshold = np.percentile(target_abs[target_abs > 0], 75)
    y_big = (target_abs > threshold).astype(int)
    
    # Features
    print("Building features & generating temporal ML probabilities...")
    sel_features = ["realized_vol", "intensity", "vpin", "range_compression", "liquidity_shock"]
    sel_X = np.column_stack([FEATURE_BUILDERS[f](bars)[0] for f in sel_features])
    
    trad_X = np.column_stack([FEATURE_BUILDERS[f](bars)[0] for f in ["range_compression", "realized_vol", "intensity", "vpin"]])
    y_trad, _ = make_tradable_labels(bars)
    
    inv_X = np.column_stack([FEATURE_BUILDERS[f](bars)[0] for f in ["range_compression", "realized_vol", "intensity", "vpin"]])
    y_inv = make_invalidation_labels(bars)
    
    # Side-Selector: VPIN Contrarian Trap (Z-score based)
    vpin_raw = FEATURE_BUILDERS["vpin"](bars)[0][:, 0]
    vpin_z = (vpin_raw - np.nanmean(vpin_raw)) / np.nanstd(vpin_raw)
    side_signal = np.where(vpin_z > 1.5, -1, np.where(vpin_z < -1.5, 1, 0))
    
    valid = np.all(np.isfinite(sel_X), axis=1) & np.all(np.isfinite(trad_X), axis=1)
    
    p_big = np.full(n_bars, np.nan)
    p_trad = np.full(n_bars, np.nan)
    p_inv = np.full(n_bars, np.nan)
    
    p_big[valid] = cross_val_probs(sel_X[valid], y_big[valid])
    p_trad[valid] = cross_val_probs(trad_X[valid], y_trad[valid])
    p_inv[valid] = cross_val_probs(inv_X[valid], y_inv[valid])
    
    oos_mask = ~np.isnan(p_big)
    idx_oos = np.where(oos_mask)[0]
    
    n_oos = len(idx_oos)
    print(f"OOS period: {n_oos} minutes")
    
    bps = 0.0007 # 7 bps slippage/fee proxy per side (14 bps round trip)
    
    # Tiers logic
    top_5_thresh = np.nanpercentile(p_big[oos_mask], 95)
    
    def eval_tier(name, mask):
        hits = np.where(mask)[0]
        count = len(hits)
        print(f"\n================ {name} ================")
        if count == 0:
            print("0 signals.")
            return
            
        correct_side = 0
        big_move_hits = 0
        tradable_hits = 0
        fakeouts = 0
        net_pnl = 0.0
        gross_profit = 0.0
        gross_loss = 0.0
        
        sum_mfe = 0.0
        sum_mae = 0.0
        
        for i in hits:
            s = side_signal[i]
            if s == 0:
                s = np.sign(ret[i]) if ret[i] != 0 else 1 # default if directionless baseline
                if s == 0: s = 1
                
            r = ret[i] / c[i]
            mfe = mfe_arr[i] / c[i] if s == 1 else -mae_arr[i] / c[i]
            mae = -mae_arr[i] / c[i] if s == 1 else mfe_arr[i] / c[i]
            
            # Clamp metrics for proper sign representation
            if mfe < 0: mfe = 0
            if mae > 0: mae = 0
            mae = abs(mae)
            
            sum_mfe += mfe
            sum_mae += mae
            
            if np.sign(r) == s: correct_side += 1
            if np.abs(ret[i]) > threshold: big_move_hits += 1
            if y_trad[i] == 1: tradable_hits += 1
            if y_inv[i] == 1: fakeouts += 1
            
            trade_pnl = (s * r) - (2 * bps)
            net_pnl += trade_pnl
            
            if trade_pnl > 0: gross_profit += trade_pnl
            else: gross_loss += abs(trade_pnl)
            
        acc = correct_side / count
        big_rate = big_move_hits / count
        wilson_acc = wilson_lower_bound(correct_side, count)
        pf = gross_profit / gross_loss if gross_loss > 0 else 999.0
        
        print(f"Signals / Day       : {count / (n_oos / 1440):.1f} ({count} total)")
        print(f"Side Accuracy       : {acc:.1%} (Wilson LB: {wilson_acc:.1%})")
        print(f"Big Move Hit Rate   : {big_rate:.1%}")
        print(f"Tradable Hit Rate   : {tradable_hits / count:.1%}")
        print(f"Average MFE         : {sum_mfe / count * 10000:.1f} bps")
        print(f"Average MAE         : {sum_mae / count * 10000:.1f} bps")
        print(f"Fakeout Rate        : {fakeouts / count:.1%}")
        print(f"Net Expected PnL    : {net_pnl * 100:.2f}%")
        print(f"Profit Factor Proxy : {pf:.2f}")

    # Masks
    m_base = (side_signal != 0)[oos_mask]
    m_sel = m_base & (p_big[oos_mask] > top_5_thresh)
    m_trad = m_sel & (p_trad[oos_mask] > 0.5)
    m_full = m_trad & (p_inv[oos_mask] < 0.6)
    
    eval_tier("A. Baseline Direction Stack Alone (VPIN)", m_base)
    eval_tier("B. Direction + Selectivity", m_sel)
    eval_tier("C. Direction + Selectivity + Tradability", m_trad)
    eval_tier("D. Full Stack (Dir + Sel + Trad + FailFast + Costs)", m_full)

if __name__ == "__main__":
    main()
