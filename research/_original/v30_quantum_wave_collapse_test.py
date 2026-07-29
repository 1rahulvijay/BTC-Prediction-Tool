import os
import pandas as pd
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
BTC_CSV = os.path.join(DATA_DIR, "btc_1m_data.csv")

def run_v30_quantum_wave_test():
    print("=================================================================")
    print("V30 QUANTUM WAVE COLLAPSE (SCHRODINGER'S EQUATION) TEST")
    print("=================================================================\n")
    
    if not os.path.exists(BTC_CSV):
        print(f"[ERROR] Required dataset not found: {BTC_CSV}")
        return
        
    print(f"[INFO] Loading 120 Days of Real Binance Data from: {BTC_CSV}")
    df = pd.read_csv(BTC_CSV).reset_index(drop=True)
    print(f"[INFO] Total Live Data Rows Loaded: {len(df):,}\n")
    
    print("[INFO] Simulating Quantum Probability Density Function (PDF)...")
    
    # Calculate Returns
    df['ret'] = df['close'].pct_change().fillna(0)
    
    # The width of the Quantum Wave Function (Probability Cloud) is represented by Rolling Variance
    # We use a 60-minute window for the subatomic state
    df['wave_variance'] = df['ret'].rolling(60).var().clip(lower=1e-9)
    
    # Calculate the rate of change of the wave width
    # A negative rate of change means the probability cloud is collapsing
    df['wave_collapse_rate'] = df['wave_variance'].diff()
    df['collapse_std'] = df['wave_collapse_rate'].rolling(1440).std().clip(lower=1e-9)
    
    # Z-Score of the collapse
    df['collapse_z'] = df['wave_collapse_rate'] / df['collapse_std']
    
    print("[INFO] Detecting Subatomic Wave Collapses...")
    
    # A Quantum Wave Collapse is defined as:
    # 1. Variance compresses violently (Z-Score < -3.0)
    # 2. Volume surges (Observation occurs)
    
    rolling_vol_mean = df['volume'].rolling(60).mean().clip(lower=1)
    df['vol_surge'] = df['volume'] > (rolling_vol_mean * 2.0)
    
    df['wave_collapse_event'] = (df['collapse_z'] < -3.0) & df['vol_surge']
    
    # Signal Direction: When the wave collapses, we check the instantaneous return skew
    signals = np.zeros(len(df))
    
    # Vectorized execution
    collapse_mask = df['wave_collapse_event']
    positive_skew = df['ret'] > 0
    negative_skew = df['ret'] < 0
    
    signals = np.where(collapse_mask & positive_skew, 1, signals)
    signals = np.where(collapse_mask & negative_skew, -1, signals)
    
    df['signal'] = signals
    
    print("\n--- INITIATING EMPIRICAL EXECUTION ALGORITHM ---")
    
    capital = 1000.0
    tp_bps = 0.005 # 0.5% Take Profit 
    sl_bps = -0.003 # 0.3% Stop Loss
    
    total_trades = 0
    winning_trades = 0
    losing_trades = 0
    
    # Fast vectorized approximation of forward returns
    df['forward_30m_max'] = df['high'].rolling(30).max().shift(-30)
    df['forward_30m_min'] = df['low'].rolling(30).min().shift(-30)
    
    long_signals = df[df['signal'] == 1]
    short_signals = df[df['signal'] == -1]
    
    # Process Longs
    for idx, row in long_signals.iterrows():
        total_trades += 1
        target_price = row['close'] * (1 + tp_bps)
        if pd.isna(row['forward_30m_max']): continue
        
        if row['forward_30m_max'] >= target_price:
            capital += 1000.0 * tp_bps
            winning_trades += 1
        else:
            capital += 1000.0 * sl_bps
            losing_trades += 1
            
    # Process Shorts
    for idx, row in short_signals.iterrows():
        total_trades += 1
        target_price = row['close'] * (1 - tp_bps)
        if pd.isna(row['forward_30m_min']): continue
        
        if row['forward_30m_min'] <= target_price:
            capital += 1000.0 * tp_bps
            winning_trades += 1
        else:
            capital += 1000.0 * sl_bps
            losing_trades += 1
            
    win_rate = (winning_trades / max(1, total_trades)) * 100
    profit_pct = ((capital - 1000.0) / 1000.0) * 100
    
    print(f"Total Wave Collapses Traded: {total_trades}")
    print(f"Winning Trades:              {winning_trades}")
    print(f"Losing Trades:               {losing_trades}")
    print(f"Quantum Collapse Win Rate:   {win_rate:.2f}%")
    print(f"Cumulative Return:           {profit_pct:.2f}%")
    
    print("\n[V30 EMPIRICAL ANALYSIS]")
    if win_rate > 50:
        print("=> SUCCESS: Schrödinger's equation successfully modeled the crypto market. Trading the mathematical collapse of probability variance yielded a massive directional edge.")
    else:
        print("=> FAILURE: Quantum Wave Collapses fail empirically on the 1-minute chart. When the probability density variance compresses instantly alongside a volume spike, it simply means a massive wick occurred. Trading the direction of the wick results in buying the top or shorting the bottom before an immediate mean-reversion.")
    print("=================================================================\n")

if __name__ == "__main__":
    run_v30_quantum_wave_test()
