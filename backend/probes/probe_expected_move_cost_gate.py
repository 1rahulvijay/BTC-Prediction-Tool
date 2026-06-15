import os
import sys
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, RidgeCV
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit

# Add backend to path for imports
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from rules.microstructure_side_engine import evaluate_vectorized

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data"
)

def build_oos_predictions(df, features, target, is_regression=False):
    X = df[features].values
    y = df[target].values
    oof_preds = np.zeros(len(y))
    tscv = TimeSeriesSplit(n_splits=5)
    for train_idx, test_idx in tscv.split(X):
        X_train, y_train = X[train_idx], y[train_idx]
        X_test, y_test = X[test_idx], y[test_idx]
        scaler = StandardScaler()
        X_train_scaled = scaler.fit_transform(X_train)
        X_test_scaled = scaler.transform(X_test)
        
        if is_regression:
            model = RidgeCV()
            model.fit(X_train_scaled, y_train)
            oof_preds[test_idx] = model.predict(X_test_scaled)
        else:
            model = LogisticRegression(class_weight='balanced', max_iter=1000)
            model.fit(X_train_scaled, y_train)
            oof_preds[test_idx] = model.predict_proba(X_test_scaled)[:, 1]
            
    return oof_preds

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
    
    # Calculate MFE 30m target for Regression
    # We want to predict the expected maximum excursion (bps) over the next 30m
    max_h = (df['high'].rolling(30).max().shift(-30) - df['close']) / df['close'] * 10000
    min_l = (df['close'] - df['low'].rolling(30).min().shift(-30)) / df['close'] * 10000
    # Average of the two side excursions roughly represents expected volatility magnitude
    df['target_expected_move_bps'] = (max_h + min_l) / 2.0
    # Drop NaNs at the end
    df = df.dropna(subset=['target_expected_move_bps']).copy()
    
    # OOS Generation
    selectivity_features = ["rv_15m", "rv_30m", "log_count", "vpin_15m", "compression_ratio", "shock_magnitude"]
    tradability_features = ["compression_ratio", "rv_60m", "rv_30m"]
    failfast_features = ["shock_magnitude", "vpin"]
    
    df['p_big_move'] = build_oos_predictions(df, selectivity_features, 'target_big_move')
    df['p_tradable'] = build_oos_predictions(df, tradability_features, 'target_tradable')
    df['p_fail_fast'] = build_oos_predictions(df, failfast_features, 'target_fail_fast')
    df['expected_move_bps'] = build_oos_predictions(df, selectivity_features, 'target_expected_move_bps', is_regression=True)
    
    # Engine
    df_oos = df[df['p_big_move'] > 0].copy()
    sides, tiers, reasons = evaluate_vectorized(
        df_oos['p_big_move'].values, df_oos['p_tradable'].values, df_oos['p_fail_fast'].values,
        df_oos['perp_spot_basis_bps'].values, df_oos['cvd_divergence'].values, df_oos['vpin'].values
    )
    
    df_oos['side_signal'] = sides
    df_oos['tier'] = tiers
    df_active = df_oos[(df_oos['side_signal'] != 0) & (df_oos['tier'].isin(['T1','T2','T3']))].copy()
    
    # Evaluate Cost Gate
    EXECUTION_COST_BPS = 14.0
    REQUIRED_RATIO = 2.5
    MIN_EXPECTED_MOVE = EXECUTION_COST_BPS * REQUIRED_RATIO
    
    print(f"\n--- EXPECTED MOVE COST GATE (Cost = {EXECUTION_COST_BPS} bps | Target = {MIN_EXPECTED_MOVE} bps) ---")
    print(f"Total Base Signals (All Expected Moves): {len(df_active)}")
    
    gated_df = df_active[df_active['expected_move_bps'] >= MIN_EXPECTED_MOVE].copy()
    
    print(f"Signals passing Expected Move >= 2.5x Cost: {len(gated_df)}")
    
    if len(gated_df) == 0:
        print("NO SIGNALS PASSED THE GATE. The expected volatility is rarely 35+ bps.")
    else:
        # Check Net EV of gated signals under 30m hold
        ret_30m_pct = (df['close'].shift(-30) - df['close']) / df['close']
        gated_df['ret_30m_pct'] = ret_30m_pct.loc[gated_df.index]
        gated_df['gross_ret_30m'] = gated_df['ret_30m_pct'] * gated_df['side_signal']
        gated_df['net_ret_30m'] = gated_df['gross_ret_30m'] - (EXECUTION_COST_BPS / 10000.0)
        
        net_ev = gated_df['net_ret_30m'].mean() * 10000
        win_rate = (gated_df['net_ret_30m'] > 0).mean() * 100
        
        print(f"\n[GATED TIER PERFORMANCE (30m Hold, 14 bps slip)]")
        print(f"  Signals : {len(gated_df)}")
        print(f"  Net EV  : {net_ev:>+8.2f} bps")
        print(f"  Win %   : {win_rate:>8.1f}%")

if __name__ == "__main__":
    main()
