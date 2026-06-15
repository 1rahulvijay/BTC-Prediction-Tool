"""
probe_markov_entropy.py
Evaluates Order Flow Markov Entropy as a Tradability / Timing Gate.
"""

import os
import sys
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars

def compute_entropy(bars, window=60):
    n = len(bars["close"])
    entropy = np.zeros(n)
    
    # States: 0 = Down, 1 = Flat, 2 = Up
    ret = np.zeros(n)
    ret[1:] = np.diff(bars["close"])
    
    # Threshold for flat
    std_ret = np.std(ret)
    state = np.where(ret > 0.2 * std_ret, 2, np.where(ret < -0.2 * std_ret, 0, 1))
    
    # Calculate transition probabilities dynamically
    for i in range(window, n):
        # Count transitions in the window
        transitions = np.zeros((3, 3))
        window_states = state[i-window:i]
        for j in range(1, window):
            s_prev = window_states[j-1]
            s_curr = window_states[j]
            transitions[s_prev, s_curr] += 1
            
        # Add small smoothing to avoid log(0)
        transitions += 1e-6
        # Normalize to probabilities
        row_sums = transitions.sum(axis=1, keepdims=True)
        p_trans = transitions / row_sums
        
        # Calculate entropy of the transition matrix
        # H = - sum(P(i) * sum(P(j|i) log P(j|i)))
        # Here we approximate by unweighted average of row entropies
        row_entropies = -np.sum(p_trans * np.log2(p_trans), axis=1)
        entropy[i] = np.mean(row_entropies)
        
    return entropy

def main():
    days = 60
    bars = _load_bars(days)
    if bars is None: return
    
    n = len(bars["close"])
    print(f"\n{n} minute-bars loaded. Evaluating Markov Entropy.")
    
    entropy_60 = compute_entropy(bars, 60)
    
    # Target: P(Big_Move)
    h = 5
    ret = np.zeros(n)
    for i in range(n - h):
        ret[i] = bars["close"][i+h] - bars["close"][i]
    
    target_abs = np.abs(ret)
    threshold = np.percentile(target_abs[target_abs > 0], 75)
    y_big = (target_abs > threshold).astype(int)
    
    mask = (entropy_60 > 0)
    if mask.sum() > 0:
        auc = roc_auc_score(y_big[mask], entropy_60[mask])
        if auc < 0.5:
            auc = 1 - auc
            dir_str = "INVERSE (Low Entropy -> Big Move)"
        else:
            dir_str = "DIRECT (High Entropy -> Big Move)"
        print(f"Markov Entropy 60m vs P(Big_Move) | AUC: {auc:.3f} | {dir_str}")

if __name__ == "__main__":
    main()
