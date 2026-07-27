"""
probe_composed_scorecard.py
Builds a unified Composed Paper Scorecard that gates all live entry conditions.
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
from feature_finding.probe_tradable_move import make_tradable_labels
from feature_finding.probe_invalidation_risk import make_invalidation_labels

# Helper for out-of-sample cross validation predictions
def cross_val_probs(X, y):
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
    print(f"\n{n_bars} minute-bars loaded. Building Composed Scorecard.")
    
    # Target values (forward looking metrics)
    h = 5
    ret = np.zeros(n_bars)
    for i in range(n_bars - h):
        ret[i] = bars["close"][i+h] - bars["close"][i]
    
    target_abs = np.abs(ret)
    threshold = np.percentile(target_abs[target_abs > 0], 75)
    y_big = (target_abs > threshold).astype(int)
    
    # 1. Selectivity v2
    sel_features = ["realized_vol", "intensity", "vpin", "range_compression", "liquidity_shock"]
    sel_X = np.column_stack([FEATURE_BUILDERS[f](bars)[0] for f in sel_features])
    
    # 2. Tradability
    trad_features = ["range_compression", "realized_vol", "intensity", "vpin"]
    trad_X = np.column_stack([FEATURE_BUILDERS[f](bars)[0] for f in trad_features])
    y_trad, _ = make_tradable_labels(bars)
    
    # 3. Fail Fast
    inv_features = ["range_compression", "realized_vol", "intensity", "vpin"]
    inv_X = np.column_stack([FEATURE_BUILDERS[f](bars)[0] for f in inv_features])
    y_inv = make_invalidation_labels(bars)
    
    # 4. Side-Selector (VPIN Contrarian Trap)
    # VPIN Contrarian rule: if VPIN is high, short. If VPIN is low, long.
    # We will use the VPIN 15m.
    vpin_raw = FEATURE_BUILDERS["vpin"](bars)[0][:, 0]
    # Simple global z-score for the offline probe
    vpin_z = (vpin_raw - np.mean(vpin_raw)) / np.std(vpin_raw)
    side_signal = np.where(vpin_z > 1.5, -1, np.where(vpin_z < -1.5, 1, 0))
    
    # Valid rows
    valid = np.all(np.isfinite(sel_X), axis=1) & np.all(np.isfinite(trad_X), axis=1)
    
    print("Generating temporal Out-Of-Sample probabilities...")
    prob_big = cross_val_probs(sel_X[valid], y_big[valid])
    prob_trad = cross_val_probs(trad_X[valid], y_trad[valid])
    prob_inv = cross_val_probs(inv_X[valid], y_inv[valid])
    
    # Realign with original arrays
    p_big = np.full(n_bars, np.nan)
    p_trad = np.full(n_bars, np.nan)
    p_inv = np.full(n_bars, np.nan)
    
    p_big[valid] = prob_big
    p_trad[valid] = prob_trad
    p_inv[valid] = prob_inv
    
    # Extract only the testable out-of-sample region (where probs are not nan)
    oos_mask = ~np.isnan(p_big)
    
    p_big = p_big[oos_mask]
    p_trad = p_trad[oos_mask]
    p_inv = p_inv[oos_mask]
    side = side_signal[oos_mask]
    ret_actual = ret[oos_mask]
    prices = bars["close"][oos_mask]
    
    n_oos = len(p_big)
    print(f"Out of Sample evaluation period: {n_oos} minutes")
    
    # Cost metrics (7 bps)
    bps = 0.0007
    
    # Create Gates
    top_1_thresh = np.percentile(p_big, 99)
    top_5_thresh = np.percentile(p_big, 95)
    
    # T3: Selectivity > 95% + Side present
    is_t3 = (p_big > top_5_thresh) & (side != 0)
    
    # T2: T3 + Tradability > 0.5
    is_t2 = is_t3 & (p_trad > 0.50)
    
    # T1: T2 + Fail Fast < 0.60 + Selectivity > 99%
    is_t1 = is_t2 & (p_inv < 0.60) & (p_big > top_1_thresh)
    
    def evaluate_tier(tier_name, is_tier):
        idx = np.where(is_tier)[0]
        signals = len(idx)
        print(f"\n================ COMPOSE SCORECARD ({tier_name}) ================")
        if signals == 0:
            print("0 signals passed.")
            return
            
        correct_dir = 0
        big_hits = 0
        pnl_net = 0.0
        
        for i in idx:
            s = side[i]
            r = ret_actual[i] / prices[i] # fractional return
            if np.sign(r) == s: correct_dir += 1
            if np.abs(ret_actual[i]) > threshold: big_hits += 1
            trade_pnl = (s * r) - (2 * bps)
            pnl_net += trade_pnl
            
        print(f"Total Signals       : {signals}")
        print(f"Signals / Day       : {signals / (n_oos / 1440):.1f}")
        print(f"Side Accuracy       : {correct_dir / signals:.1%}")
        print(f"Big Move Hit Rate   : {big_hits / signals:.1%}")
        print(f"Net Expected PnL    : {pnl_net * 100:.2f}% (After 14 bps round-trip slippage)")
        print("=============================================================")

    print("\nFunnel Analysis:")
    print(f"Base Top 5% Selectivity : {np.sum(p_big > top_5_thresh)}")
    print(f"Base Top 1% Selectivity : {np.sum(p_big > top_1_thresh)}")
    print(f"Base VPIN Side != 0     : {np.sum(side != 0)}")
    
    evaluate_tier("T3 (Top 5% + Side)", is_t3)
    evaluate_tier("T2 (T3 + Tradability)", is_t2)
    evaluate_tier("T1 (Top 1% + All Gates)", is_t1)

if __name__ == "__main__":
    main()
