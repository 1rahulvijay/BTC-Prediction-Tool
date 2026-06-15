import os
import sys
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score

# Add backend to path for imports
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from rules.microstructure_side_engine import evaluate_vectorized

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "data"
)

def build_oos_predictions(df, features, target):
    """Generates Out-of-Sample predictions using TimeSeriesSplit and Logistic Regression."""
    X = df[features].values
    y = df[target].values
    
    oof_probs = np.zeros(len(y))
    tscv = TimeSeriesSplit(n_splits=5)
    
    for train_idx, test_idx in tscv.split(X):
        X_train, y_train = X[train_idx], y[train_idx]
        X_test, y_test = X[test_idx], y[test_idx]
        
        # Scale
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
        
    print(f"Loading {matrix_path}...")
    df = pd.read_parquet(matrix_path)
    
    # Drop rows with NaNs in necessary columns
    needed_cols = [
        "rv_15m", "rv_30m", "rv_60m", "log_count", "compression_ratio", "shock_magnitude", 
        "vpin_15m", "vpin", "perp_spot_basis_bps", "cvd_divergence", "ret_5m", "future_abs_move_5m",
        "tradable_move_label", "fail_fast_label"
    ]
    df = df.replace([np.inf, -np.inf], np.nan)
    df = df.dropna(subset=needed_cols).copy()
    
    df['abs_ret_5m'] = df['ret_5m'].abs()
    
    # Targets for timing models
    p75_vol = df['future_abs_move_5m'].quantile(0.75)
    df['target_big_move'] = (df['future_abs_move_5m'] > p75_vol).astype(int)
    df['target_tradable'] = df['tradable_move_label'].fillna(0).astype(int)
    df['target_fail_fast'] = df['fail_fast_label'].fillna(0).astype(int)
    
    print("\n--- 1. Generating Out-of-Sample ML Timing Scores ---")
    selectivity_features = ["rv_15m", "rv_30m", "log_count", "vpin_15m", "compression_ratio", "shock_magnitude"]
    tradability_features = ["compression_ratio", "rv_60m", "rv_30m"]
    failfast_features = ["shock_magnitude", "vpin"]
    
    df['p_big_move'] = build_oos_predictions(df, selectivity_features, 'target_big_move')
    df['p_tradable'] = build_oos_predictions(df, tradability_features, 'target_tradable')
    df['p_fail_fast'] = build_oos_predictions(df, failfast_features, 'target_fail_fast')
    
    # Only keep out-of-sample data (the first fold test set starts at 1/6th of data)
    valid_mask = df['p_big_move'] > 0
    df_oos = df[valid_mask].copy()
    print(f"OOS Validated Bars: {len(df_oos)}")
    
    print("\n--- 2. Applying Deterministic Side Engine ---")
    sides, tiers, reasons = evaluate_vectorized(
        df_oos['p_big_move'].values,
        df_oos['p_tradable'].values,
        df_oos['p_fail_fast'].values,
        df_oos['perp_spot_basis_bps'].values,
        df_oos['cvd_divergence'].values,
        df_oos['vpin'].values
    )
    
    df_oos['side_signal'] = sides
    df_oos['tier'] = tiers
    df_oos['reason'] = reasons
    
    # Only evaluate active signals
    active_mask = df_oos['side_signal'] != 0
    df_active = df_oos[active_mask].copy()
    
    print(f"\nTotal Active Signals Generated: {len(df_active)} ({(len(df_active)/len(df_oos))*100:.2f}% of market time)")
    
    print("\n--- 3. Rule-Composed Scorecard (EV Evaluation) ---")
    
    # Slippage / Cost Assumption
    # The user mandated a brutal 14 bps round-trip slip constraint
    SLIPPAGE_BPS = 14.0
    SLIPPAGE_DECIMAL = SLIPPAGE_BPS / 10000.0
    
    # Calculate returns (ret_5m in parquet is dollar move, not pct)
    df_active['ret_5m_pct'] = df_active['ret_5m'] / df_active['close']
    df_active['gross_ret_pct'] = df_active['ret_5m_pct'] * df_active['side_signal']
    df_active['net_ret_pct'] = df_active['gross_ret_pct'] - SLIPPAGE_DECIMAL
    df_active['is_win'] = df_active['net_ret_pct'] > 0
    
    # We will evaluate different compositions requested by the user
    # A. Selectivity only (Ignore side logic, just assume 50/50 side for benchmark)
    # B. Selectivity + Side Engine (Tier 1)
    # C. Selectivity + Tradability + Side Engine (Tier 2)
    # D. Full Stack Top Tier (Tier 3)
    
    def evaluate_tier(name, df_tier):
        n = len(df_tier)
        if n == 0:
            print(f"{name:.<40} N=0")
            return
            
        win_rate = df_tier['is_win'].mean() * 100
        gross_ev_bps = df_tier['gross_ret_pct'].mean() * 10000
        net_ev_bps = df_tier['net_ret_pct'].mean() * 10000
        total_pnl = df_tier['net_ret_pct'].sum() * 100
        signals_per_day = n / (len(df_oos) / 1440) # Approximate days
        
        print(f"[{name}]")
        print(f"  Signals   : {n} ({signals_per_day:.1f} / day)")
        print(f"  Win Rate  : {win_rate:.1f}% (after 14 bps slip)")
        print(f"  Gross EV  : {gross_ev_bps:+.2f} bps/trade")
        print(f"  Net EV    : {net_ev_bps:+.2f} bps/trade")
        print(f"  Total PnL : {total_pnl:+.2f}%")
        print("")

    # A. Baseline Selectivity Only (Assume random direction, gross EV)
    top_5_sel = df_oos[df_oos['p_big_move'] >= np.percentile(df_oos['p_big_move'], 95)]
    top_5_abs_pct = (top_5_sel['abs_ret_5m'] / top_5_sel['close']).mean() * 10000
    print(f"[A. Baseline Selectivity (Top 5%)]")
    print(f"  Signals   : {len(top_5_sel)}")
    print(f"  Avg Abs Move : {top_5_abs_pct:.1f} bps")
    print(f"  Directional Side Accuracy: N/A (Needs side logic)")
    print("")

    # Evaluate Tiers
    evaluate_tier("B. T1 (Top 5% Selectivity + Side Rules)", df_active[df_active['tier'].isin(['T1', 'T2', 'T3'])])
    evaluate_tier("C. T2 (Top 1% Selectivity + Tradability)", df_active[df_active['tier'].isin(['T2', 'T3'])])
    evaluate_tier("D. T3 (Top 0.25% Selectivity + Full Stack)", df_active[df_active['tier'] == 'T3'])
    
    # Specific Rule Performance
    print("--- Performance by Specific Microstructure Rule (All Tiers) ---")
    rules = df_active['reason'].unique()
    for r in rules:
        df_r = df_active[df_active['reason'] == r]
        net_ev = df_r['net_ret_pct'].mean() * 10000
        win_rate = df_r['is_win'].mean() * 100
        print(f"{r:<35} | Net EV: {net_ev:+.2f} bps | Win%: {win_rate:.1f}% | N={len(df_r)}")


if __name__ == "__main__":
    main()
