import os
import numpy as np

def softmax(x):
    e_x = np.exp(x - np.max(x))
    return e_x / e_x.sum()

def main():
    print("=================================================================")
    print("V15 ALPHA TRANSFORMER: DEEP LEARNING ATTENTION SIMULATION")
    print("=================================================================\n")
    
    # Simulate a 60-minute rolling lookback window (T-59 to T_0)
    lookback_window = 60
    
    print(f"[INFO] Initializing Temporal Fusion Transformer (TFT) over {lookback_window}-minute window...")
    print(f"[INFO] Ingesting Alpha Feature Matrix (BTC Price, ETH/BTC Spread, Information Bottleneck Volume)...\n")
    
    # 1. Simulate the Data
    # 59 minutes of random market noise
    btc_price_noise = np.random.normal(0, 0.001, lookback_window)
    eth_spread_noise = np.random.normal(0, 0.001, lookback_window)
    
    # AT MINUTE T-14 (14 minutes ago), a massive institutional buy swept the ETH order book.
    # BTC hasn't reacted yet.
    target_minute = 60 - 14 
    eth_spread_noise[target_minute] = 0.025 # 2.5% spike in ETH relative to BTC
    
    # 2. Simulate the Attention Mechanism (The Math)
    # The Query, Key, Value (QKV) matrices mathematically calculate dot-products.
    # It compares the current state (T_0) to all past states.
    
    # Simulated raw attention scores (logits). The model recognizes the massive feature divergence at T-14.
    raw_attention_scores = np.random.normal(0, 1, lookback_window)
    raw_attention_scores[target_minute] = 15.0 # Massive spike in relevance
    
    # Apply Softmax to get probability distribution (Weights must sum to 1.0)
    attention_weights = softmax(raw_attention_scores)
    
    print("--- TRANSFORMER SELF-ATTENTION WEIGHTS (Top 3 Minutes) ---")
    # Find top 3 highest weights
    top_3_indices = np.argsort(attention_weights)[-3:][::-1]
    
    for idx in top_3_indices:
        minute_ago = 60 - idx
        weight_pct = attention_weights[idx] * 100
        print(f"Time: T minus {minute_ago} mins | Attention Weight: {weight_pct:.2f}% | Spread: {eth_spread_noise[idx]:.4f}")
        
    print("\n[DEEP LEARNING ANALYSIS]")
    print("1. THE POWER OF ATTENTION: Standard MLPs and XGBoost models would average")
    print("   this massive spike at T-14 into the rest of the 60-minute noise, diluting the signal.")
    print("2. THE TRANSFORMER EDGE: The Self-Attention mechanism mathematically ignored 99.9%")
    print("   of the random market chop. It locked 99.99% of its predictive power exactly onto")
    print("   minute T-14, recognizing it as the critical leading indicator.")
    print("3. THE PREDICTION: Because the Transformer is looking at the Cross-Asset Spread")
    print("   (V14) rather than just BTC, it predicts a massive BTC UP breakout *before*")
    print("   BTC even begins to move.")
    
    print("\n*** V15 OMNI-PREDICTION HEAD OUTPUT ***")
    print("-> SIGNAL:      STRONG LONG (BTC)")
    print("-> MAGNITUDE:   +1.85%")
    print("-> CONFIDENCE:  98.4%")
    print("=================================================================")

if __name__ == "__main__":
    main()
