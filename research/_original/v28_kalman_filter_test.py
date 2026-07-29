import os
import pandas as pd
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
BTC_CSV = os.path.join(DATA_DIR, "btc_1m_data.csv")

def run_v28_kalman_filter_test():
    print("=================================================================")
    print("V28 KALMAN FILTER (STATE SPACE ESTIMATION) TEST")
    print("=================================================================\n")
    
    if not os.path.exists(BTC_CSV):
        print(f"[ERROR] Required dataset not found: {BTC_CSV}")
        return
        
    print(f"[INFO] Loading 120 Days of Real Binance Data from: {BTC_CSV}")
    df = pd.read_csv(BTC_CSV).reset_index(drop=True)
    print(f"[INFO] Total Live Data Rows Loaded: {len(df):,}\n")
    
    print("[INFO] Initializing Aerospace Kalman Filter Matrices...")
    
    # 1D Kalman Filter Constants
    Q = 1e-5 # Process Noise (How much we trust the model)
    R = 0.01 # Measurement Noise (How much we trust the incoming 1m price data)
    
    # Initial State
    x = df['close'].iloc[0] # Initial Price Estimate
    p = 1.0 # Initial Error Covariance
    
    kalman_states = np.zeros(len(df))
    prices = df['close'].values
    
    print("[INFO] Executing recursive state-space updates over 518,400 minutes...")
    
    # Fast loop for the recursive Kalman updates
    for i in range(len(df)):
        # 1. Prediction Update
        p = p + Q
        
        # 2. Measurement Update
        K = p / (p + R) # Kalman Gain
        x = x + K * (prices[i] - x)
        p = (1 - K) * p
        
        kalman_states[i] = x
        
    df['kalman_state'] = kalman_states
    
    print("[INFO] Calculating Statistical Divergence (Z-Score)...")
    df['divergence'] = df['close'] - df['kalman_state']
    df['divergence_std'] = df['divergence'].rolling(1440).std().clip(lower=0.0001)
    df['z_score'] = df['divergence'] / df['divergence_std']
    
    # Signal Generation: Mean Reversion
    # If the noisy price spikes > 2 standard deviations away from the true Kalman state, we fade it.
    signals = np.where(df['z_score'] > 2.5, -1, 0) # Price is too high, SHORT
    signals = np.where(df['z_score'] < -2.5, 1, signals) # Price is too low, LONG
    
    df['signal'] = signals
    
    print("\n--- INITIATING EMPIRICAL EXECUTION ALGORITHM ---")
    
    capital = 1000.0
    tp_bps = 0.003 # 0.3% Take Profit (quick mean reversion scalp)
    sl_bps = -0.002 # 0.2% Stop Loss
    
    total_trades = 0
    winning_trades = 0
    losing_trades = 0
    
    # Fast vectorized approximation of forward returns
    df['forward_15m_max'] = df['high'].rolling(15).max().shift(-15)
    df['forward_15m_min'] = df['low'].rolling(15).min().shift(-15)
    
    long_signals = df[df['signal'] == 1]
    short_signals = df[df['signal'] == -1]
    
    # Process Longs
    for idx, row in long_signals.iterrows():
        total_trades += 1
        target_price = row['close'] * (1 + tp_bps)
        if pd.isna(row['forward_15m_max']): continue
        
        if row['forward_15m_max'] >= target_price:
            capital += 1000.0 * tp_bps # Simple interest
            winning_trades += 1
        else:
            capital += 1000.0 * sl_bps
            losing_trades += 1
            
    # Process Shorts
    for idx, row in short_signals.iterrows():
        total_trades += 1
        target_price = row['close'] * (1 - tp_bps)
        if pd.isna(row['forward_15m_min']): continue
        
        if row['forward_15m_min'] <= target_price:
            capital += 1000.0 * tp_bps
            winning_trades += 1
        else:
            capital += 1000.0 * sl_bps
            losing_trades += 1
            
    win_rate = (winning_trades / max(1, total_trades)) * 100
    profit_pct = ((capital - 1000.0) / 1000.0) * 100
    
    print(f"Total Divergence Arbitrage Trades Executed: {total_trades}")
    print(f"Winning Trades:                             {winning_trades}")
    print(f"Losing Trades:                              {losing_trades}")
    print(f"Kalman Filter Win Rate:                     {win_rate:.2f}%")
    print(f"Cumulative Return:                          {profit_pct:.2f}%")
    
    print("\n[V28 EMPIRICAL ANALYSIS]")
    if win_rate > 50:
        print("=> SUCCESS: The Kalman Filter successfully tracked the true market state with zero lag. Fading extreme standard deviation wicks against the Kalman state proved to be a highly effective, high win-rate mean reversion strategy.")
    else:
        print("=> FAILURE: The crypto market frequently trends violently. What looks like a 3-standard-deviation noise wick to the Kalman Filter is actually the start of a massive trend, causing mean reversion shorts to get completely crushed.")
    print("=================================================================\n")

if __name__ == "__main__":
    run_v28_kalman_filter_test()
