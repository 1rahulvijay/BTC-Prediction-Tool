import os
import sys
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import roc_auc_score, accuracy_score
from sklearn.preprocessing import StandardScaler

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data"
)

def wilson_lower_bound(successes, n, z=1.96):
    if n == 0: return 0.0
    p = successes / n
    denominator = 1 + z**2/n
    centre_adjusted_probability = p + z*z / (2*n)
    adjusted_standard_deviation = np.sqrt((p*(1 - p) + z*z / (4*n)) / n)
    return (centre_adjusted_probability - z*adjusted_standard_deviation) / denominator

def evaluate_feature(df, feature_name, target_col="future_direction_5m"):
    """
    Evaluates a single feature for directional edge using TimeSeries CV.
    Target must be binary (-1, 1). We map it to (0, 1) for classification.
    """
    # Drop NaNs
    valid = df[[feature_name, target_col]].dropna()
    X = valid[[feature_name]].values
    y_raw = valid[target_col].values
    
    # Exclude exactly 0 return cases for clear side mapping
    non_zero = y_raw != 0
    X = X[non_zero]
    y_raw = y_raw[non_zero]
    
    y = (y_raw > 0).astype(int)
    n = len(y)
    
    if n < 1000:
        return None, None
        
    tscv = TimeSeriesSplit(n_splits=5)
    
    aucs = []
    accs = []
    
    for train_i, test_i in tscv.split(X):
        if len(np.unique(y[train_i])) < 2 or len(np.unique(y[test_i])) < 2:
            continue
            
        scaler = StandardScaler()
        X_tr = scaler.fit_transform(X[train_i])
        X_te = scaler.transform(X[test_i])
        
        lr = LogisticRegression(class_weight='balanced')
        lr.fit(X_tr, y[train_i])
        
        probs = lr.predict_proba(X_te)[:, 1]
        preds = lr.predict(X_te)
        
        aucs.append(roc_auc_score(y[test_i], probs))
        accs.append(accuracy_score(y[test_i], preds))
        
    mean_auc = np.mean(aucs) if aucs else 0.5
    mean_acc = np.mean(accs) if accs else 0.5
    
    return mean_auc, mean_acc

def evaluate_simple_threshold(df, feature_name, target_col="future_direction_5m", threshold_pct=90):
    """
    Evaluates the accuracy of fading or following the feature when it is at an extreme.
    """
    valid = df[[feature_name, target_col]].dropna()
    valid = valid[valid[target_col] != 0]
    
    feat = valid[feature_name].values
    y_raw = valid[target_col].values
    
    upper_thresh = np.percentile(feat, threshold_pct)
    lower_thresh = np.percentile(feat, 100 - threshold_pct)
    
    # When feature > upper_thresh
    long_hits_upper = np.sum((feat > upper_thresh) & (y_raw > 0))
    short_hits_upper = np.sum((feat > upper_thresh) & (y_raw < 0))
    total_upper = long_hits_upper + short_hits_upper
    
    # When feature < lower_thresh
    long_hits_lower = np.sum((feat < lower_thresh) & (y_raw > 0))
    short_hits_lower = np.sum((feat < lower_thresh) & (y_raw < 0))
    total_lower = long_hits_lower + short_hits_lower
    
    # Analyze direct continuation (Direct) vs mean-reversion (Inverse)
    direct_hits = long_hits_upper + short_hits_lower
    inverse_hits = short_hits_upper + long_hits_lower
    total = total_upper + total_lower
    
    if total == 0:
        return 0, 0, 0, "NONE"
        
    direct_acc = direct_hits / total
    inverse_acc = inverse_hits / total
    
    if direct_acc > inverse_acc:
        return total, direct_acc, wilson_lower_bound(direct_hits, total), "DIRECT"
    else:
        return total, inverse_acc, wilson_lower_bound(inverse_hits, total), "INVERSE"

def main():
    matrix_path = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
    if not os.path.exists(matrix_path):
        print(f"Error: {matrix_path} not found.")
        return
        
    print(f"Loading {matrix_path}...")
    df = pd.read_parquet(matrix_path)
    
    # Features to test
    features = [
        "cvd_spot", "cvd_perp", "cvd_divergence", 
        "perp_spot_basis_bps", "funding_velocity",
        "vpin"
    ]
    
    print("\n" + "="*60)
    print("GLOBAL SIDE-SELECTION (All Regimes)")
    print("="*60)
    print(f"{'Feature':<25} | {'LogReg AUC':<12} | {'Extreme Setup Accuracy (>95th Pct)'}")
    print("-" * 60)
    
    for f in features:
        auc, _ = evaluate_feature(df, f)
        count, acc, wlb, rule = evaluate_simple_threshold(df, f, threshold_pct=95)
        
        auc_str = f"{auc:.3f}" if auc else "N/A"
        print(f"{f:<25} | {auc_str:<12} | {rule} {acc:.1%} (N={count}, WLB={wlb:.1%})")

    # High Volatility Regime (Selectivity Filter)
    # We use realized vol > 85th percentile as a proxy for the 'Top Selectivity' bucket
    vol_thresh = df['rv_15m'].quantile(0.85)
    df_vol = df[df['rv_15m'] > vol_thresh].copy()
    
    print("\n" + "="*60)
    print("CONDITIONAL SIDE-SELECTION (High Volatility Regime > 85th Pct)")
    print("="*60)
    print(f"{'Feature':<25} | {'LogReg AUC':<12} | {'Extreme Setup Accuracy (>95th Pct)'}")
    print("-" * 60)
    
    for f in features:
        auc, _ = evaluate_feature(df_vol, f)
        count, acc, wlb, rule = evaluate_simple_threshold(df_vol, f, threshold_pct=95)
        
        auc_str = f"{auc:.3f}" if auc else "N/A"
        print(f"{f:<25} | {auc_str:<12} | {rule} {acc:.1%} (N={count}, WLB={wlb:.1%})")
        
    print("\n" + "="*60)
    print("TRADABLE BREAKOUT REGIME (tradable_move_label == 1)")
    print("="*60)
    print(f"{'Feature':<25} | {'LogReg AUC':<12} | {'Extreme Setup Accuracy (>95th Pct)'}")
    print("-" * 60)
    
    df_trad = df[df['tradable_move_label'] == 1].copy()
    for f in features:
        auc, _ = evaluate_feature(df_trad, f)
        count, acc, wlb, rule = evaluate_simple_threshold(df_trad, f, threshold_pct=95)
        
        auc_str = f"{auc:.3f}" if auc else "N/A"
        print(f"{f:<25} | {auc_str:<12} | {rule} {acc:.1%} (N={count}, WLB={wlb:.1%})")

if __name__ == "__main__":
    main()
