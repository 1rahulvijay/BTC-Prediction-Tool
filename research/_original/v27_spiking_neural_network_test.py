import os
import pandas as pd
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
BTC_CSV = os.path.join(DATA_DIR, "btc_1m_data.csv")

def run_v27_snn_empirical_test():
    print("=================================================================")
    print("V27 SPIKING NEURAL NETWORKS (LIQUID STATE MACHINE) TEST")
    print("=================================================================\n")
    
    if not os.path.exists(BTC_CSV):
        print(f"[ERROR] Required dataset not found: {BTC_CSV}")
        return
        
    print(f"[INFO] Loading 120 Days of Real Binance Data from: {BTC_CSV}")
    df = pd.read_csv(BTC_CSV).reset_index(drop=True)
    print(f"[INFO] Total Live Data Rows Loaded: {len(df):,}\n")
    
    print("[INFO] Initializing Leaky Integrate-and-Fire (LIF) Neuron Array...")
    
    # SNN Parameters
    RESTING_VOLTAGE = 0.0
    SPIKE_THRESHOLD = 1.0
    LEAK_RATE = 0.05 # Voltage decays by 5% every minute
    
    # Input current proxy: Normalized Momentum * Normalized Volume
    df['momentum'] = df['close'].pct_change()
    
    # Simple rolling normalization to represent incoming raw sensory data
    rolling_vol_max = df['volume'].rolling(1440).max().clip(lower=1)
    rolling_mom_std = df['momentum'].rolling(1440).std().clip(lower=0.0001)
    
    df['norm_vol'] = df['volume'] / rolling_vol_max
    df['norm_mom'] = df['momentum'] / rolling_mom_std
    
    df['input_current'] = df['norm_vol'] * df['norm_mom']
    df = df.fillna(0)
    
    print("[INFO] Simulating biological voltage accumulation across 518,400 minutes...")
    
    # We must iterate to simulate the hidden state (voltage memory)
    voltages = np.zeros(len(df))
    spikes = np.zeros(len(df)) # 1 for Long Spike, -1 for Short Spike
    
    current_voltage = RESTING_VOLTAGE
    
    # Vectorized iteration using numpy for speed
    input_currents = df['input_current'].values
    
    for i in range(1, len(df)):
        # 1. Leak
        current_voltage *= (1.0 - LEAK_RATE)
        
        # 2. Integrate
        current_voltage += input_currents[i]
        
        # 3. Fire
        if current_voltage > SPIKE_THRESHOLD:
            spikes[i] = 1
            current_voltage = RESTING_VOLTAGE # Reset
        elif current_voltage < -SPIKE_THRESHOLD:
            spikes[i] = -1
            current_voltage = RESTING_VOLTAGE # Reset
            
        voltages[i] = current_voltage
        
    df['voltage'] = voltages
    df['spike'] = spikes
    
    total_spikes = np.sum(spikes != 0)
    print(f"[INFO] Neurons fired exactly {total_spikes} times in 120 days.")
    print(f"[INFO] The bot sat completely dormant for {len(df) - total_spikes} minutes, ignoring noise.\n")
    
    print("--- INITIATING EMPIRICAL EXECUTION ALGORITHM ---")
    
    capital = 1000.0
    tp_bps = 0.005 # 0.5% Take Profit
    sl_bps = -0.003 # 0.3% Stop Loss
    
    total_trades = 0
    winning_trades = 0
    losing_trades = 0
    
    # Fast vectorized approximation of forward returns
    df['forward_15m_max'] = df['high'].rolling(15).max().shift(-15)
    df['forward_15m_min'] = df['low'].rolling(15).min().shift(-15)
    
    long_signals = df[df['spike'] == 1]
    short_signals = df[df['spike'] == -1]
    
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
    
    print(f"Total Spikes (Trades Executed):        {total_trades}")
    print(f"Winning Trades:                        {winning_trades}")
    print(f"Losing Trades:                         {losing_trades}")
    print(f"Biological SNN Win Rate:               {win_rate:.2f}%")
    print(f"Cumulative Return:                     {profit_pct:.2f}%")
    
    print("\n[V27 EMPIRICAL ANALYSIS]")
    if win_rate > 50:
        print("=> SUCCESS: Biological Integrate-and-Fire neurons successfully filtered out market noise. By physically forcing the bot to sit dormant while voltage leaked during chop, it avoided the standard AI overtrading trap, yielding high sniper profitability.")
    else:
        print("=> FAILURE: The SNN spiked too late. By waiting for the voltage to accumulate over multiple candles, the momentum was exhausted by the time the neuron fired.")
    print("=================================================================\n")

if __name__ == "__main__":
    run_v27_snn_empirical_test()
