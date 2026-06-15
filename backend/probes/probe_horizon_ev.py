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
    
    # Dynamic Horizons & Excursions
    # 15m
    df['ret_15m_pct'] = (df['close'].shift(-15) - df['close']) / df['close']
    df['max_high_15m_pct'] = (df['high'].rolling(15).max().shift(-15) - df['close']) / df['close']
    df['min_low_15m_pct'] = (df['low'].rolling(15).min().shift(-15) - df['close']) / df['close']
    
    # 30m
    df['ret_30m_pct'] = (df['close'].shift(-30) - df['close']) / df['close']
    df['max_high_30m_pct'] = (df['high'].rolling(30).max().shift(-30) - df['close']) / df['close']
    df['min_low_30m_pct'] = (df['low'].rolling(30).min().shift(-30) - df['close']) / df['close']
    
    # 60m
    df['ret_60m_pct'] = (df['close'].shift(-60) - df['close']) / df['close']
    df['max_high_60m_pct'] = (df['high'].rolling(60).max().shift(-60) - df['close']) / df['close']
    df['min_low_60m_pct'] = (df['low'].rolling(60).min().shift(-60) - df['close']) / df['close']
    
    # Engine
    df_oos = df[df['p_big_move'] > 0].copy()
    sides, tiers, reasons = evaluate_vectorized(
        df_oos['p_big_move'].values, df_oos['p_tradable'].values, df_oos['p_fail_fast'].values,
        df_oos['perp_spot_basis_bps'].values, df_oos['cvd_divergence'].values, df_oos['vpin'].values
    )
    
    df_oos['side_signal'] = sides
    df_oos['tier'] = tiers
    df_active = df_oos[(df_oos['side_signal'] != 0) & (df_oos['tier'].isin(['T1','T2','T3']))].copy()
    
    df_active['ret_5m_pct'] = df_active['ret_5m'] / df_active['close']
    
    horizons = [5, 15, 30, 60]
    SLIPPAGE_DECIMAL = 14.0 / 10000.0
    
    print("\n--- HOLDING HORIZON EV REPORT (14 bps Slippage) ---")
    print("Horizon | Net EV (bps) | Win Rate | Mean MFE | Mean MAE")
    print("-" * 65)
    
    for h in horizons:
        ret_col = f'ret_{h}m_pct'
        
        # MFE/MAE Calculation
        if h == 5:
            # Approximate 5m MFE/MAE using single bar data because we don't have exactly 5 rolling here
            # But we can compute it on the fly:
            max_h = df['high'].rolling(5).max().shift(-5).loc[df_active.index]
            min_l = df['low'].rolling(5).min().shift(-5).loc[df_active.index]
            mfe_pct = np.where(df_active['side_signal'] == 1, (max_h - df_active['close'])/df_active['close'], (df_active['close'] - min_l)/df_active['close'])
            mae_pct = np.where(df_active['side_signal'] == 1, (min_l - df_active['close'])/df_active['close'], (df_active['close'] - max_h)/df_active['close'])
        else:
            max_h = df_active[f'max_high_{h}m_pct']
            min_l = df_active[f'min_low_{h}m_pct']
            mfe_pct = np.where(df_active['side_signal'] == 1, max_h, -min_l)
            mae_pct = np.where(df_active['side_signal'] == 1, min_l, -max_h)
            
        df_active[f'gross_{h}m'] = df_active[ret_col] * df_active['side_signal']
        df_active[f'net_{h}m'] = df_active[f'gross_{h}m'] - SLIPPAGE_DECIMAL
        
        win_rate = (df_active[f'net_{h}m'] > 0).mean() * 100
        net_ev = df_active[f'net_{h}m'].mean() * 10000
        mean_mfe = np.nanmean(mfe_pct) * 10000
        mean_mae = np.nanmean(mae_pct) * 10000
        
        print(f"{h:>5}m | {net_ev:>+12.2f} | {win_rate:>7.1f}% | {mean_mfe:>+8.1f} | {mean_mae:>+8.1f}")

if __name__ == "__main__":
    main()
