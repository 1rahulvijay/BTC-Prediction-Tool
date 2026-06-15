import os
import sys
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit

# Add backend to path for imports
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from rules.microstructure_side_engine import evaluate_vectorized

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data"
)

def build_oos_predictions(df, features, target):
    X = df[features].values
    y = df[target].values
    oof_probs = np.zeros(len(y))
    tscv = TimeSeriesSplit(n_splits=5)
    for train_idx, test_idx in tscv.split(X):
        X_train, y_train = X[train_idx], y[train_idx]
        X_test, y_test = X[test_idx], y[test_idx]
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)
        clf = LogisticRegression(class_weight='balanced', max_iter=1000)
        clf.fit(X_train_scaled, y_train)
        oof_probs[test_idx] = clf.predict_proba(X_test_scaled)[:, 1]
    return oof_probs

def main():
    matrix_path = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
    if not os.path.exists(matrix_path):
        print(f"Error: {matrix_path} not found.")
        return
        
    df = pd.read_parquet(matrix_path)
    
    needed_cols = [
        "rv_15m", "rv_30m", "rv_60m", "log_count", "compression_ratio", "shock_magnitude", 
        "vpin_15m", "vpin", "perp_spot_basis_bps", "cvd_divergence", "ret_5m", "future_abs_move_5m",
        "tradable_move_label", "fail_fast_label"
    ]
    df = df.replace([np.inf, -np.inf], np.nan).dropna(subset=needed_cols).copy()
    
    # Base targets
    p75_vol = df['future_abs_move_5m'].quantile(0.75)
    df['target_big_move'] = (df['future_abs_move_5m'] > p75_vol).astype(int)
    df['target_tradable'] = df['tradable_move_label'].fillna(0).astype(int)
    df['target_fail_fast'] = df['fail_fast_label'].fillna(0).astype(int)
    
    # OOS
    selectivity_features = ["rv_15m", "rv_30m", "log_count", "vpin_15m", "compression_ratio", "shock_magnitude"]
    tradability_features = ["compression_ratio", "rv_60m", "rv_30m"]
    failfast_features = ["shock_magnitude", "vpin"]
    
    df['p_big_move'] = build_oos_predictions(df, selectivity_features, 'target_big_move')
    df['p_tradable'] = build_oos_predictions(df, tradability_features, 'target_tradable')
    df['p_fail_fast'] = build_oos_predictions(df, failfast_features, 'target_fail_fast')
    
    df_oos = df[df['p_big_move'] > 0].copy()
    
    # Engine
    sides, tiers, reasons = evaluate_vectorized(
        df_oos['p_big_move'].values, df_oos['p_tradable'].values, df_oos['p_fail_fast'].values,
        df_oos['perp_spot_basis_bps'].values, df_oos['cvd_divergence'].values, df_oos['vpin'].values
    )
    
    df_oos['side_signal'] = sides
    df_oos['tier'] = tiers
    df_active = df_oos[df_oos['side_signal'] != 0].copy()
    df_active['ret_5m_pct'] = df_active['ret_5m'] / df_active['close']
    df_active['gross_ret_pct'] = df_active['ret_5m_pct'] * df_active['side_signal']
    
    costs_to_test = [0, 2, 4, 7, 10, 14]
    
    print("\n--- COST SENSITIVITY REPORT ---")
    print("Cost bps | Net EV (bps) | Win rate | Signals/day")
    print("-" * 55)
    
    # Only test Tier 1+ signals
    df_t1 = df_active[df_active['tier'].isin(['T1', 'T2', 'T3'])].copy()
    signals_per_day = len(df_t1) / (len(df_oos) / 1440)
    
    for cost in costs_to_test:
        slip_decimal = cost / 10000.0
        df_t1[f'net_{cost}'] = df_t1['gross_ret_pct'] - slip_decimal
        win_rate = (df_t1[f'net_{cost}'] > 0).mean() * 100
        net_ev = df_t1[f'net_{cost}'].mean() * 10000
        
        print(f"{cost:<8} | {net_ev:>+11.2f}  | {win_rate:>7.1f}% | {signals_per_day:.1f}")

if __name__ == "__main__":
    main()
