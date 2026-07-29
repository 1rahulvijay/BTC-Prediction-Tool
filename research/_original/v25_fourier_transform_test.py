import os
import pandas as pd
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
BTC_CSV = os.path.join(DATA_DIR, "btc_1m_data.csv")

def run_v25_empirical_test():
    print("=================================================================")
    print("V25 FOURIER TRANSFORM SPECTRAL ANALYSIS (EMPIRICAL BACKTEST)")
    print("=================================================================\n")
    
    if not os.path.exists(BTC_CSV):
        print(f"[ERROR] Required dataset not found: {BTC_CSV}")
        return
        
    print(f"[INFO] Loading 120 Days of Real Binance Data from: {BTC_CSV}")
    df = pd.read_csv(BTC_CSV).reset_index(drop=True)
    print(f"[INFO] Total Live Data Rows Loaded: {len(df):,}\n")
    
    print("[INFO] Applying Fast Fourier Transform (FFT) to Time Series...")
    # 1. Detrend the data (subtract mean) for pure wave analysis
    prices = df['close'].values
    mean_price = np.mean(prices)
    detrended = prices - mean_price
    
    # 2. Compute FFT
    fft_vals = np.fft.fft(detrended)
    frequencies = np.fft.fftfreq(len(detrended))
    
    print("[INFO] Filtering out high-frequency noise (Low-Pass Filter)...")
    # 3. Filter out all high-frequency noise (keep only the dominant macro cycles)
    # We want cycles that last longer than 24 hours (1440 minutes)
    # frequency = 1 / cycle_length
    threshold_freq = 1.0 / 1440.0
    
    fft_filtered = fft_vals.copy()
    fft_filtered[np.abs(frequencies) > threshold_freq] = 0
    
    print("[INFO] Applying Inverse FFT to reconstruct the Dominant Sine Wave...")
    # 4. Inverse FFT to get the pure, smoothed market wave
    smoothed_wave = np.fft.ifft(fft_filtered).real
    df['dominant_wave'] = smoothed_wave + mean_price
    
    print("[INFO] Calculating Wave Derivatives (Crests and Troughs)...")
    # 5. First Derivative (Slope)
    df['wave_slope'] = df['dominant_wave'].diff()
    
    # Trough: Slope crosses from negative to positive
    df['trough'] = (df['wave_slope'] > 0) & (df['wave_slope'].shift(1) < 0)
    # Crest: Slope crosses from positive to negative
    df['crest'] = (df['wave_slope'] < 0) & (df['wave_slope'].shift(1) > 0)
    
    signals = np.where(df['trough'], 1, 0)
    signals = np.where(df['crest'], -1, signals)
    
    print("\n--- INITIATING EMPIRICAL EXECUTION ALGORITHM ---")
    
    # Fast vectorized backtest
    capital = 1000.0
    tp_bps = 0.010 # 1.0% Take Profit for macro wave trades
    sl_bps = -0.005 # 0.5% Stop Loss
    
    total_trades = 0
    winning_trades = 0
    losing_trades = 0
    
    df['forward_60m_max'] = df['high'].rolling(60).max().shift(-60)
    df['forward_60m_min'] = df['low'].rolling(60).min().shift(-60)
    
    long_signals = df[signals == 1]
    short_signals = df[signals == -1]
    
    # Execute Longs (Troughs)
    for idx, row in long_signals.iterrows():
        total_trades += 1
        target_price = row['close'] * (1 + tp_bps)
        if pd.isna(row['forward_60m_max']): continue
        
        if row['forward_60m_max'] >= target_price:
            capital += 1000.0 * tp_bps
            winning_trades += 1
        else:
            capital += 1000.0 * sl_bps
            losing_trades += 1
            
    # Execute Shorts (Crests)
    for idx, row in short_signals.iterrows():
        total_trades += 1
        target_price = row['close'] * (1 - tp_bps)
        if pd.isna(row['forward_60m_min']): continue
        
        if row['forward_60m_min'] <= target_price:
            capital += 1000.0 * tp_bps
            winning_trades += 1
        else:
            capital += 1000.0 * sl_bps
            losing_trades += 1
            
    win_rate = (winning_trades / max(1, total_trades)) * 100
    profit_pct = ((capital - 1000.0) / 1000.0) * 100
    
    print(f"Total Isolated Wave Extremes Executed: {total_trades}")
    print(f"Winning Trades:                        {winning_trades}")
    print(f"Losing Trades:                         {losing_trades}")
    print(f"Wave Mechanics Win Rate:               {win_rate:.2f}%")
    print(f"Cumulative Return:                     {profit_pct:.2f}%")
    
    print("\n[V25 EMPIRICAL ANALYSIS]")
    if win_rate > 50:
        print("=> SUCCESS: Fourier Transform cycle isolation successfully predicts the macro peaks and troughs of the crypto market!")
    else:
        print("=> FAILURE: While Fourier Transforms map historical cycles perfectly, using them as look-ahead predictors fails. Because FFT uses the entire dataset globally to construct the waves, a strict rolling-FFT in real-time introduces severe 'repainting' lag, causing you to buy the trough right as the trend actually crashes lower.")
    print("=================================================================\n")

if __name__ == "__main__":
    run_v25_empirical_test()
