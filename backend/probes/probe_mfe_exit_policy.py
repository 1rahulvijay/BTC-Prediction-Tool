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

def simulate_exits(df, df_active, tp_bps, sl_bps, max_horizon=30):
    """
    Simulates a Take-Profit / Stop-Loss / Time-Limit exit policy bar-by-bar to avoid MFE/MAE collision bugs.
    """
    tp_decimal = tp_bps / 10000.0
    sl_decimal = abs(sl_bps) / 10000.0
    
    results = []
    
    for idx in df_active.index:
        entry_idx = df.index.get_loc(idx)
        if entry_idx + max_horizon >= len(df):
            continue
            
        future_slice = df.iloc[entry_idx+1 : entry_idx+1+max_horizon]
        entry_price = df.loc[idx, 'close']
        side = df_active.loc[idx, 'side_signal']
        
        exit_price = future_slice['close'].iloc[-1] # Default to time limit
        exit_reason = f"TIME_{max_horizon}m"
        
        for f_idx, row in future_slice.iterrows():
            if side == 1:
                high_ret = (row['high'] - entry_price) / entry_price
                low_ret = (row['low'] - entry_price) / entry_price
                
                if low_ret <= -sl_decimal:
                    exit_price = entry_price * (1 - sl_decimal)
                    exit_reason = "STOP_LOSS"
                    break
                elif high_ret >= tp_decimal:
                    exit_price = entry_price * (1 + tp_decimal)
                    exit_reason = "TAKE_PROFIT"
                    break
                    
            elif side == -1:
                high_ret = (row['high'] - entry_price) / entry_price
                low_ret = (row['low'] - entry_price) / entry_price
                
                if high_ret >= sl_decimal:
                    exit_price = entry_price * (1 + sl_decimal)
                    exit_reason = "STOP_LOSS"
                    break
                elif low_ret <= -tp_decimal:
                    exit_price = entry_price * (1 - tp_decimal)
                    exit_reason = "TAKE_PROFIT"
                    break
                    
        gross_ret = ((exit_price - entry_price) / entry_price) * side
        results.append({
            'idx': idx,
            'exit_reason': exit_reason,
            'gross_ret': gross_ret
        })
        
    return pd.DataFrame(results)

def main():
    matrix_path = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
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
    
    # Engine
    df_oos = df[df['p_big_move'] > 0].copy()
    sides, tiers, reasons = evaluate_vectorized(
        df_oos['p_big_move'].values, df_oos['p_tradable'].values, df_oos['p_fail_fast'].values,
        df_oos['perp_spot_basis_bps'].values, df_oos['cvd_divergence'].values, df_oos['vpin'].values
    )
    
    df_oos['side_signal'] = sides
    df_oos['tier'] = tiers
    df_active = df_oos[(df_oos['side_signal'] != 0) & (df_oos['tier'].isin(['T1','T2','T3']))].copy()
    
    policies_to_test = [
        (10, -7),
        (15, -7),
        (20, -10),
        (25, -10),
        (30, -15)
    ]
    
    SLIPPAGE_DECIMAL = 14.0 / 10000.0
    
    print("\n--- MFE EXIT POLICY REPORT (14 bps Slippage, 30m Time Limit) ---")
    print(f"Total Signals: {len(df_active)}")
    print(f"{'TP (bps)':<8} | {'SL (bps)':<8} | {'Net EV (bps)':<12} | {'Win Rate':<8} | {'TP Hit%':<8} | {'SL Hit%':<8} | {'Time Hit%':<8}")
    print("-" * 80)
    
    for tp, sl in policies_to_test:
        res_df = simulate_exits(df, df_active, tp_bps=tp, sl_bps=sl, max_horizon=30)
        
        if len(res_df) == 0:
            continue
            
        res_df['net_ret'] = res_df['gross_ret'] - SLIPPAGE_DECIMAL
        net_ev = res_df['net_ret'].mean() * 10000
        win_rate = (res_df['net_ret'] > 0).mean() * 100
        
        tp_hit = (res_df['exit_reason'] == 'TAKE_PROFIT').mean() * 100
        sl_hit = (res_df['exit_reason'] == 'STOP_LOSS').mean() * 100
        time_hit = (res_df['exit_reason'] == 'TIME_30m').mean() * 100
        
        print(f"{tp:<8} | {sl:<8} | {net_ev:>+12.2f} | {win_rate:>7.1f}% | {tp_hit:>7.1f}% | {sl_hit:>7.1f}% | {time_hit:>7.1f}%")

if __name__ == "__main__":
    main()
