import os
import pandas as pd
import numpy as np

import sys
sys.path.insert(0, os.path.dirname(__file__))
from edge_probe import _load_bars, FEATURE_BUILDERS

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data"
)

def export_base_csv():
    """
    Exports the base OHLCV from edge_probe._load_bars into data/btc_1m_data.csv.
    """
    csv_path = os.path.join(DATA_DIR, "btc_1m_data.csv")
    print("Loading base OHLCV from cache via _load_bars(60)...")
    bars = _load_bars(60)
    
    if bars is not None and len(bars["close"]) > 0:
        # Build pandas dataframe from bars
        df = pd.DataFrame({
            "ts_ms": bars["minute"] * 60000,
            "open": bars["close"], # Approximation: close is carried over
            "high": bars["high"],
            "low": bars["low"],
            "close": bars["close"],
            "volume": bars["vol"],
            "trade_count": bars["count"],
            "taker_buy": bars["taker_buy"],
            "taker_sell": bars["taker_sell"]
        })
        
        # Merge some basic features from FEATURE_BUILDERS since the user specifically requested them
        for feat in ["realized_vol", "intensity", "vpin", "range_compression", "liquidity_shock"]:
            if feat in FEATURE_BUILDERS:
                X, cols = FEATURE_BUILDERS[feat](bars)
                for i, col in enumerate(cols):
                    df[col] = X[:, i]
        
        df.to_csv(csv_path, index=False)
        print(f"Exported base OHLCV and base features to {csv_path} ({len(df)} rows)")
        return df
    else:
        print("Failed to load base data from cache.")
        return pd.DataFrame()

def make_labels(df):
    """
    Computes the labels for future prediction targets.
    """
    df['future_close_5m'] = df['close'].shift(-5)
    df['future_high_5m'] = df['high'].rolling(window=5).max().shift(-5)
    df['future_low_5m'] = df['low'].rolling(window=5).min().shift(-5)
    
    df['ret_5m'] = df['future_close_5m'] - df['close']
    df['future_abs_move_5m'] = df['ret_5m'].abs()
    df['future_direction_5m'] = np.sign(df['ret_5m'])
    
    expected_move = df['close'] * 0.002 # 20 bps move
    mfe_long = df['future_high_5m'] - df['close']
    mae_long = df['close'] - df['future_low_5m']
    
    mfe_short = df['close'] - df['future_low_5m']
    mae_short = df['future_high_5m'] - df['close']
    
    is_big_long = (df['ret_5m'] > expected_move) & (mae_long < 0.4 * expected_move)
    is_big_short = (df['ret_5m'] < -expected_move) & (mae_short < 0.4 * expected_move)
    df['tradable_move_label'] = (is_big_long | is_big_short).astype(int)
    
    df['fail_fast_label'] = ((df['future_abs_move_5m'] > expected_move) & (df['tradable_move_label'] == 0)).astype(int)
    
    return df

def main():
    print("Building unified research matrix...")
    
    # 1. Base Data
    csv_path = os.path.join(DATA_DIR, "btc_1m_data.csv")
    if os.path.exists(csv_path):
        base_df = pd.read_csv(csv_path)
    else:
        base_df = export_base_csv()
        
    if base_df.empty:
        print("No base data found.")
        return

    # Ensure timestamp is datetime and ts_ms is present
    if 'timestamp' in base_df.columns and 'ts_ms' not in base_df.columns:
        base_df['timestamp'] = pd.to_datetime(base_df['timestamp'])
        base_df['ts_ms'] = base_df['timestamp'].astype(np.int64) // 10**6
    elif 'ts_ms' in base_df.columns and 'timestamp' not in base_df.columns:
        base_df['timestamp'] = pd.to_datetime(base_df['ts_ms'], unit='ms')

    # 2. Trade Features (includes spot & funding)
    trade_path = os.path.join(DATA_DIR, "trade_features_backfill.parquet")
    if os.path.exists(trade_path):
        trade_df = pd.read_parquet(trade_path)
        if 'candle_ts' in trade_df.columns:
            trade_df.rename(columns={"candle_ts": "ts_ms"}, inplace=True)
    else:
        print(f"Missing {trade_path}")
        trade_df = pd.DataFrame(columns=["ts_ms"])

    # 3. Crossvenue Flow
    cross_path = os.path.join(DATA_DIR, "crossvenue_flow.parquet")
    if os.path.exists(cross_path):
        cross_df = pd.read_parquet(cross_path)
    else:
        print(f"Missing {cross_path}")
        cross_df = pd.DataFrame(columns=["ts_ms"])

    # Merge
    print(f"Base OHLCV rows: {len(base_df)}")
    print(f"Trade features rows: {len(trade_df)}")
    print(f"Crossvenue rows: {len(cross_df)}")
    
    merged = pd.merge(base_df, trade_df, on="ts_ms", how="left")
    merged = pd.merge(merged, cross_df, on="ts_ms", how="left")
    
    # Sort
    merged.sort_values("ts_ms", inplace=True)
    
    # Build targets
    merged = make_labels(merged)
    
    # Cleanup and Save
    out_path = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
    merged.to_parquet(out_path, index=False)
    
    print(f"\nFinal Research Matrix saved to {out_path}")
    print(f"Rows: {len(merged)}")
    print(f"Columns: {list(merged.columns)}")

if __name__ == "__main__":
    main()
