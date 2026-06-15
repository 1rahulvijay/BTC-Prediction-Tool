import os
import sys
import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import roc_auc_score, accuracy_score, log_loss

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data"
)

def main():
    matrix_path = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
    if not os.path.exists(matrix_path):
        print(f"Error: {matrix_path} not found.")
        return
        
    print(f"Loading {matrix_path}...")
    df = pd.read_parquet(matrix_path)
    
    # 1. Define Features
    # Regime / Selectivity Features (helps the tree split on volatility/breakout conditions)
    regime_features = [
        "rv_15m", "rv_30m", "range_15m", "compression_ratio", 
        "log_count", "log_vol", "vpin_15m"
    ]
    
    # Directional Flow / Crossvenue Features (provide the side signal)
    direction_features = [
        "perp_spot_basis_bps", "cvd_divergence", "vpin"
    ]
    
    features = regime_features + direction_features
    target = "future_direction_5m"
    
    # Clean data
    df = df.replace([np.inf, -np.inf], np.nan)
    valid_mask = df[features + [target]].notnull().all(axis=1) & (df[target] != 0)
    df_valid = df[valid_mask].copy()
    
    # Filter to High Volatility Regime ONLY for training (Top 20%)
    vol_thresh = df_valid['rv_15m'].quantile(0.80)
    df_valid = df_valid[df_valid['rv_15m'] >= vol_thresh].copy()
    
    X = df_valid[features].values
    y = (df_valid[target] > 0).astype(int).values
    
    # We will also keep track of original dataframe indices to evaluate conditionally
    df_indices = df_valid.index.values
    
    print(f"\nTraining on {len(y)} valid minutes...")
    
    # 2. Cross Validation Setup
    tscv = TimeSeriesSplit(n_splits=5)
    
    global_aucs = []
    global_accs = []
    
    # Storage for out-of-sample predictions
    oof_preds = np.zeros(len(y))
    oof_probs = np.zeros(len(y))
    
    # Feature importances
    feature_importances = np.zeros(len(features))
    
    # XGBoost Parameters - Heavily regularized
    params = {
        'objective': 'binary:logistic',
        'eval_metric': 'logloss',
        'max_depth': 2,            # Heavily regularize
        'learning_rate': 0.02,
        'n_estimators': 50,
        'subsample': 0.7,
        'colsample_bytree': 0.7,
        'n_jobs': -1,
        'random_state': 42
    }
    
    print(f"Running {tscv.n_splits}-fold TimeSeriesSplit...")
    for fold, (train_idx, test_idx) in enumerate(tscv.split(X)):
        X_train, y_train = X[train_idx], y[train_idx]
        X_test, y_test = X[test_idx], y[test_idx]
        
        clf = xgb.XGBClassifier(**params)
        clf.fit(X_train, y_train, 
                eval_set=[(X_test, y_test)], 
                verbose=False)
        
        preds = clf.predict(X_test)
        probs = clf.predict_proba(X_test)[:, 1]
        
        oof_preds[test_idx] = preds
        oof_probs[test_idx] = probs
        
        fold_auc = roc_auc_score(y_test, probs)
        fold_acc = accuracy_score(y_test, preds)
        
        global_aucs.append(fold_auc)
        global_accs.append(fold_acc)
        
        # Accumulate feature importance (gain)
        feature_importances += clf.feature_importances_ / tscv.n_splits
        
        print(f" Fold {fold+1} | AUC: {fold_auc:.4f} | Acc: {fold_acc:.4f}")

    # 3. Overall Global Performance
    valid_oof_mask = oof_probs > 0  # Only consider test folds
    mean_auc = roc_auc_score(y[valid_oof_mask], oof_probs[valid_oof_mask])
    mean_acc = accuracy_score(y[valid_oof_mask], oof_preds[valid_oof_mask])
    
    print("\n" + "="*50)
    print("GLOBAL PERFORMANCE (Out-of-Sample)")
    print("="*50)
    print(f"Overall OOS AUC : {mean_auc:.4f}")
    print(f"Overall OOS Acc : {mean_acc:.4f}")
    
    # 4. Feature Importance
    print("\n" + "="*50)
    print("XGBOOST FEATURE IMPORTANCE (Average Gain)")
    print("="*50)
    imp_df = pd.DataFrame({'Feature': features, 'Importance': feature_importances})
    imp_df = imp_df.sort_values(by='Importance', ascending=False)
    for _, row in imp_df.iterrows():
        print(f"{row['Feature']:<25} : {row['Importance']:.4f}")

    # 5. Conditional Performance (The key to this probe!)
    print("\n" + "="*50)
    print("CONDITIONAL PERFORMANCE (High Volatility / Breakout)")
    print("="*50)
    
    # Attach predictions back to the dataframe
    df_valid['oof_prob'] = oof_probs
    df_valid['oof_pred'] = oof_preds
    
    # Filter only to bars that were predicted out-of-sample
    df_eval = df_valid[df_valid['oof_prob'] > 0].copy()
    
    # Condition 1: High Realized Volatility (>85th percentile)
    vol_thresh = df_eval['rv_15m'].quantile(0.85)
    df_vol = df_eval[df_eval['rv_15m'] > vol_thresh]
    
    vol_auc = roc_auc_score((df_vol[target] > 0).astype(int), df_vol['oof_prob'])
    vol_acc = accuracy_score((df_vol[target] > 0).astype(int), df_vol['oof_pred'])
    print(f"High Volatility (>85th Pct) Acc: {vol_acc:.4f} (AUC: {vol_auc:.4f}) | N={len(df_vol)}")
    
    # Condition 2: High Tradability Proxy (e.g. tradable_move_label == 1)
    df_trad = df_eval[df_eval['tradable_move_label'] == 1]
    if len(df_trad) > 0:
        trad_auc = roc_auc_score((df_trad[target] > 0).astype(int), df_trad['oof_prob'])
        trad_acc = accuracy_score((df_trad[target] > 0).astype(int), df_trad['oof_pred'])
        print(f"Tradable Breakout Regime Acc   : {trad_acc:.4f} (AUC: {trad_auc:.4f}) | N={len(df_trad)}")
    
    # Condition 3: Extreme Model Confidence Setup
    # Take the top 5% highest confidence long and short predictions WITHIN the high vol regime
    high_conf_thresh_long = df_vol['oof_prob'].quantile(0.95)
    high_conf_thresh_short = df_vol['oof_prob'].quantile(0.05)
    
    df_extreme = df_vol[(df_vol['oof_prob'] > high_conf_thresh_long) | (df_vol['oof_prob'] < high_conf_thresh_short)]
    if len(df_extreme) > 0:
        ext_acc = accuracy_score((df_extreme[target] > 0).astype(int), df_extreme['oof_pred'])
        print(f"Extreme Setup (Top 5% + High Vol): {ext_acc:.4f} | N={len(df_extreme)}")

if __name__ == "__main__":
    main()
