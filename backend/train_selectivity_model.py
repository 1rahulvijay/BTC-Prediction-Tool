"""
train_selectivity_model.py — Offline P(Big_Move) Selectivity Meta-Model
========================================================================
Combines the 4 validated timing/microstructure features to predict whether
the upcoming 5-minute window will exceed the 75th percentile of normal
volatility.

This script is fully INDEPENDENT of the main server/model, adhering to the
discipline of testing offline before wiring anything into the live loop.

Features:
- range_compression
- realized_vol
- intensity
- vpin
"""

import os
import sys
import pickle
import numpy as np
from datetime import datetime
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import Pipeline

sys.path.insert(0, os.path.dirname(__file__))
from edge_probe import _load_bars, FEATURE_BUILDERS, make_labels

# Manifest written in the same step as the artifact: without it the artifact reads as
# UNKNOWN identity, and phold_challenger refuses to deploy a calibrator while any source
# artifact fails identity enforcement - which disables
# PM_CALIBRATED_FAIR_VALUE_FORWARD_BENCHMARK_V1.
from verified_io import write_manifest as write_integrity_manifest

MODEL_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "selectivity_model_v1.pkl")

def main(days=30, horizon=5, percentile=75):
    print(f"Loading {days} days of tick data...")
    bars = _load_bars(days)
    if bars is None or len(bars["close"]) < 500:
        sys.exit("Not enough data to train.")
    
    n = len(bars["close"])
    print(f"Loaded {n} minute-bars. Engineering features...")
    
    # 1. Build Features
    features_to_use = ["range_compression", "realized_vol", "intensity", "vpin"]
    X_list = []
    feature_names = []
    
    for fname in features_to_use:
        X_f, names = FEATURE_BUILDERS[fname](bars)
        X_list.append(X_f)
        feature_names.extend([f"{fname}_{n}" for n in names])
        
    X = np.column_stack(X_list)
    
    # 2. Build Labels
    _, absm = make_labels(bars["close"], horizon)
    
    # Mask out NaNs and infinities
    mask = np.isfinite(absm) & np.all(np.isfinite(X), axis=1)
    X = X[mask]
    absm = absm[mask]
    
    # 3. Temporal Split
    split_idx = int(len(X) * 0.70)
    X_train, X_test = X[:split_idx], X[split_idx:]
    absm_train, absm_test = absm[:split_idx], absm[split_idx:]
    
    # Define "Big Move" dynamically based on the training set 75th percentile
    threshold = np.percentile(absm_train, percentile)
    print(f"Target: Absolute {horizon}m Move >= ${threshold:.2f} (Top {100-percentile}%)")
    
    y_train = (absm_train >= threshold).astype(int)
    y_test = (absm_test >= threshold).astype(int)
    
    print(f"Train samples: {len(X_train)} (Positive class: {y_train.mean():.1%})")
    print(f"Test samples:  {len(X_test)} (Positive class: {y_test.mean():.1%})")
    
    # 4. Train Model
    print("Training Logistic Regression pipeline...")
    pipe = Pipeline([
        ('scaler', StandardScaler()),
        ('clf', LogisticRegression(class_weight='balanced', max_iter=1000, C=0.1))
    ])
    
    pipe.fit(X_train, y_train)
    
    # 5. Evaluate Out-of-Sample
    probs = pipe.predict_proba(X_test)[:, 1]
    auc = roc_auc_score(y_test, probs)
    print(f"\nOut-of-Sample AUC: {auc:.3f}")
    
    print("\n--- SCORECARD: Precision by Confidence Bin ---")
    bins = [0.5, 0.6, 0.7, 0.8, 0.9]
    for b in bins:
        mask_b = probs >= b
        if mask_b.sum() == 0:
            continue
        prec = y_test[mask_b].mean()
        lift = prec / y_test.mean()
        print(f"P(Big_Move) >= {b:.1f} | N = {mask_b.sum():>4} | Precision: {prec:.1%} (Lift: {lift:.1f}x)")
    
    # 6. Save Model
    os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
    with open(MODEL_PATH, "wb") as f:
        pickle.dump({
            "pipeline": pipe,
            "feature_names": feature_names,
            "horizon": horizon,
            "threshold": threshold,
            "trained_at": datetime.now().isoformat()
        }, f)
    write_integrity_manifest(MODEL_PATH)
    print(f"\nSaved model to {MODEL_PATH}")

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    main(days=args.days)
