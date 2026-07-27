"""
train_tradability_model.py
Trains the P(Tradable_Move) logistic regression model.
"""

import os
import sys
import pickle
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import roc_auc_score

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from edge_probe import _load_bars, FEATURE_BUILDERS
from probes.probe_tradable_move import make_tradable_labels

def main():
    days = 60
    bars = _load_bars(days)
    if bars is None: return
    
    y, _ = make_tradable_labels(bars)
    
    features = ["range_compression", "realized_vol", "intensity", "vpin"]
    X_list = []
    
    for fname in features:
        X_f, _ = FEATURE_BUILDERS[fname](bars)
        X_list.append(X_f)
        
    X = np.column_stack(X_list)
    valid = np.all(np.isfinite(X), axis=1)
    
    X = X[valid]
    y = y[valid]
    
    tscv = TimeSeriesSplit(n_splits=5)
    aucs = []
    
    for train_i, test_i in tscv.split(X):
        X_train, X_test = X[train_i], X[test_i]
        y_train, y_test = y[train_i], y[test_i]
        
        pipe = Pipeline([
            ('scaler', StandardScaler()),
            ('lr', LogisticRegression(max_iter=1000, class_weight='balanced'))
        ])
        
        pipe.fit(X_train, y_train)
        preds = pipe.predict_proba(X_test)[:, 1]
        
        try:
            auc = roc_auc_score(y_test, preds)
            aucs.append(auc)
        except:
            pass
            
    print("P(Tradable_Move) Model Trained.")
    print(f"Mean Fold AUC: {np.mean(aucs):.3f}")
    
    # Train final model on all data
    final_pipe = Pipeline([
        ('scaler', StandardScaler()),
        ('lr', LogisticRegression(max_iter=1000, class_weight='balanced'))
    ])
    final_pipe.fit(X, y)
    
    out_path = os.path.join(os.path.dirname(__file__), "data", "tradability_model.pkl")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        pickle.dump({"pipeline": final_pipe, "features": features}, f)
        
    print(f"Model saved to {out_path}")

if __name__ == "__main__":
    main()
