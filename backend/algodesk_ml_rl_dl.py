"""
algodesk_ml_rl_dl.py -- ML/DL/RL Evolved Models for the 17 AlgoDesk Agents
===========================================================================

This script evolves the 17 rule-based AlgoDesk agents into predictive models using:
1. Machine Learning (XGBoost): Predicts probability of trade success using agent signals as features.
2. Deep Learning (PyTorch MLP): Direct classification of market context into Long/Short/Skip.
3. Reinforcement Learning (SB3 PPO): Trading agent optimizing for P&L in a simulated environment.

Usage:
  python backend/algodesk_ml_rl_dl.py --train-days 30 --test-days 7
"""
from __future__ import annotations

import argparse
import time
import numpy as np
import pandas as pd
import requests
import warnings
warnings.filterwarnings("ignore")

import xgboost as xgb
import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset
import gymnasium as gym
from gymnasium import spaces
from stable_baselines3 import PPO

# ===========================================================================
#  Constants & Shared Logic (From previous script)
# ===========================================================================
BYBIT_BASE = "https://api.bybit.com"
LEV = 5
STOP_PCT = 0.015
TGT_PCT = 0.03
CANDLES_PER_DAY = 288
KLINE_INTERVAL = "5"
MIN_VOL_24H = 50_000_000
FUNDING_LONG_BLOCK = 0.005
FUNDING_SHORT_BLOCK = -0.005

def apply_global_guards(direction: str, vol_24h: float, fr: float) -> str:
    if vol_24h < MIN_VOL_24H: return "SKIP"
    if direction == "LONG" and fr > FUNDING_LONG_BLOCK: return "SKIP"
    if direction == "SHORT" and fr < FUNDING_SHORT_BLOCK: return "SKIP"
    return direction

# --- 17 Agent Logic (Condensed) ---
def sig_trend(c) -> str:
    if abs(c["change_24h_pct"]) > 5.0 and c["vol_24h"] >= 100_000_000:
        if c["change_24h_pct"] > 5.0 and c["pos"] > 0.65: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
        if c["change_24h_pct"] < -5.0 and c["pos"] < 0.35: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
    return "SKIP"

def sig_momo(c) -> str:
    if abs(c["change_24h_pct"]) > 8.0 and c["vol_24h"] >= 100_000_000:
        if c["change_24h_pct"] > 8.0 and c["pos"] > 0.75: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
        if c["change_24h_pct"] < -8.0 and c["pos"] < 0.25: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
    return "SKIP"

def sig_break(c) -> str:
    if c["vol_24h"] >= 80_000_000 and c["high_24h"] - c["low_24h"] > 0:
        dist_high = (c["high_24h"] - c["close"]) / c["high_24h"] * 100
        dist_low = (c["close"] - c["low_24h"]) / c["low_24h"] * 100
        if dist_high < 3.0 and c["pos"] > 0.90: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
        if dist_low < 3.0 and c["pos"] < 0.10: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
    return "SKIP"

def sig_mean(c) -> str:
    if abs(c["change_24h_pct"]) > 15.0:
        if c["change_24h_pct"] < -15.0 and c["pos"] < 0.15: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
        if c["change_24h_pct"] > 15.0 and c["pos"] > 0.85: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
    return "SKIP"

def sig_fund(c) -> str:
    fr = c["funding_rate"]
    if abs(fr) >= 0.0015:
        if fr > 0.0015: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
        if fr < -0.0015: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
    return "SKIP"

def sig_vol(c) -> str:
    if c["vol_24h"] >= 150_000_000 and abs(c["change_24h_pct"]) > 3.0:
        if c["change_24h_pct"] > 3.0: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
        if c["change_24h_pct"] < -3.0: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
    return "SKIP"

def sig_oi(c) -> str:
    if c["open_interest"] >= 2_000_000_000 and abs(c["change_24h_pct"]) > 2.0:
        if c["change_24h_pct"] > 2.0: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
        if c["change_24h_pct"] < -2.0: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
    return "SKIP"

def sig_contra(c) -> str:
    if abs(c["change_24h_pct"]) > 18.0 and abs(c["funding_rate"]) > 0.001:
        if c["change_24h_pct"] > 18.0 and c["funding_rate"] > 0.001 and c["pos"] > 0.85: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
        if c["change_24h_pct"] < -18.0 and c["funding_rate"] < -0.001 and c["pos"] < 0.15: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
    return "SKIP"

def sig_scalp(c) -> str:
    if c["vol_24h"] >= 200_000_000:
        if 60 < c["rsi"] < 80: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
        if 20 < c["rsi"] < 40: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
    return "SKIP"

def sig_liq(c) -> str:
    if abs(c["change_24h_pct"]) > 8.0 and c["vol_24h"] >= 200_000_000:
        if c["change_24h_pct"] > 8.0 and c["pos"] > 0.85: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
        if c["change_24h_pct"] < -8.0 and c["pos"] < 0.15: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
    return "SKIP"

def sig_pat(c) -> str:
    range_pct = (c["high_24h"] - c["low_24h"]) / c["low_24h"] * 100 if c["low_24h"] > 0 else 0
    if 0.5 <= range_pct <= 5.0 and abs(c["change_24h_pct"]) > 1.5:
        if c["change_24h_pct"] > 1.5 and c["pos"] > 0.70: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
        if c["change_24h_pct"] < -1.5 and c["pos"] < 0.30: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
    return "SKIP"

def sig_range(c) -> str:
    range_pct = (c["high_24h"] - c["low_24h"]) / c["low_24h"] * 100 if c["low_24h"] > 0 else 0
    if range_pct <= 8.0 and abs(c["change_24h_pct"]) <= 5.0:
        if c["pos"] < 0.15: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
        if c["pos"] > 0.85: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
    return "SKIP"

def sig_stat(c) -> str:
    if c["open_interest"] > 1_500_000_000:
        if c["change_24h_pct"] > 3.0 and c["funding_rate"] < 0: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
        if c["change_24h_pct"] < -3.0 and c["funding_rate"] > 0: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
    return "SKIP"

def sig_sent(c) -> str:
    if abs(c["funding_rate"]) >= 0.002:
        if c["funding_rate"] > 0.002 and c["change_24h_pct"] < -2.0: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
        if c["funding_rate"] < -0.002 and c["change_24h_pct"] > 2.0: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
    return "SKIP"

def sig_flow(c) -> str:
    if c["vol_24h"] >= 300_000_000 and abs(c["change_24h_pct"]) > 2.0 and c["open_interest"] > 1_000_000_000:
        if c["change_24h_pct"] > 2.0: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
        if c["change_24h_pct"] < -2.0: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
    return "SKIP"

def sig_regime(c) -> str:
    range_pct = (c["high_24h"] - c["low_24h"]) / c["low_24h"] * 100 if c["low_24h"] > 0 else 0
    if abs(c["change_24h_pct"]) > 4.0 and range_pct > 3.0:
        if c["change_24h_pct"] > 4.0: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
        else: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
    return "SKIP"

def sig_oidiv(c) -> str:
    if c["open_interest"] >= 1_000_000_000:
        if c["change_24h_pct"] > 3.0 and c["funding_rate"] < -0.001: return apply_global_guards("SHORT", c["vol_24h"], c["funding_rate"])
        if c["change_24h_pct"] < -3.0 and c["funding_rate"] > 0.001: return apply_global_guards("LONG", c["vol_24h"], c["funding_rate"])
    return "SKIP"

AGENT_FUNCS = [
    sig_trend, sig_momo, sig_break, sig_mean, sig_fund, sig_vol, sig_oi, sig_contra,
    sig_scalp, sig_liq, sig_pat, sig_range, sig_stat, sig_sent, sig_flow, sig_regime, sig_oidiv
]

def map_signal(sig: str) -> int:
    return 1 if sig == "LONG" else (-1 if sig == "SHORT" else 0)

# ===========================================================================
#  Data Fetching & Preprocessing
# ===========================================================================
def fetch_klines(symbol: str, days: int) -> pd.DataFrame:
    total_candles = days * CANDLES_PER_DAY
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (days * 86_400_000)
    cursor_end = now_ms
    all_data = []

    print(f"Fetching {symbol} for {days} days...")
    while len(all_data) < total_candles:
        limit = min(1000, total_candles - len(all_data))
        url = f"{BYBIT_BASE}/v5/market/kline?category=linear&symbol={symbol}&interval={KLINE_INTERVAL}&limit={limit}&end={cursor_end}"
        try:
            resp = requests.get(url, timeout=10).json()
            rows = resp.get("result", {}).get("list", [])
            if not rows: break
            for r in rows:
                if int(r[0]) >= start_ms:
                    all_data.append([int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[5])])
            cursor_end = int(rows[-1][0]) - 1
            time.sleep(0.1)
        except Exception:
            time.sleep(1)

    df = pd.DataFrame(all_data, columns=["ts", "open", "high", "low", "close", "volume"])
    df = df.sort_values("ts").drop_duplicates(subset=["ts"]).reset_index(drop=True)
    return df

def create_dataset(symbol: str, days: int) -> pd.DataFrame:
    df = fetch_klines(symbol, days)
    if len(df) < CANDLES_PER_DAY: return pd.DataFrame()
    
    # 24h rolling stats
    window = CANDLES_PER_DAY
    df["high_24h"] = df["high"].rolling(window, min_periods=1).max()
    df["low_24h"] = df["low"].rolling(window, min_periods=1).min()
    df["vol_24h"] = df["volume"].rolling(window, min_periods=1).sum()
    df["change_24h_pct"] = (df["close"] - df["close"].shift(window)) / df["close"].shift(window) * 100
    df["change_24h_pct"].fillna(0, inplace=True)
    
    range_24h = df["high_24h"] - df["low_24h"]
    df["pos"] = np.where(range_24h > 0, (df["close"] - df["low_24h"]) / range_24h, 0.5)
    df["rsi"] = (df["pos"] * 100).round()
    
    # Simulated FR & OI
    df["funding_rate"] = 0.0
    if len(df) > 96:
        change_8h = (df["close"] - df["close"].shift(96)) / df["close"].shift(96)
        df["funding_rate"] = (change_8h * 0.05).fillna(0)
    df["open_interest"] = df["vol_24h"] * 3.5

    # Target logic: Does it hit 3% TP before 1.5% SL in the next 288 candles (24h)?
    lookahead = 100
    df["target_long"] = 0
    df["target_short"] = 0
    
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    
    print("Computing forward targets and agent signals...")
    for i in range(len(df) - lookahead):
        entry = closes[i]
        tp_long = entry * (1 + TGT_PCT)
        sl_long = entry * (1 - STOP_PCT)
        tp_short = entry * (1 - TGT_PCT)
        sl_short = entry * (1 + STOP_PCT)
        
        # Check forward path
        for j in range(1, lookahead):
            idx = i + j
            if lows[idx] <= sl_long:
                break
            if highs[idx] >= tp_long:
                df.at[i, "target_long"] = 1
                break
                
        for j in range(1, lookahead):
            idx = i + j
            if highs[idx] >= sl_short:
                break
            if lows[idx] <= tp_short:
                df.at[i, "target_short"] = 1
                break

    # Generate 17 agent signals
    records = df.to_dict("records")
    for a_idx, fn in enumerate(AGENT_FUNCS):
        sigs = [map_signal(fn(r)) for r in records]
        df[f"agent_{a_idx}"] = sigs
        
    df = df.dropna().reset_index(drop=True)
    # Drop first 24h warmup and last lookahead buffer
    df = df.iloc[CANDLES_PER_DAY : -lookahead].reset_index(drop=True)
    return df

# ===========================================================================
#  1. ML Model (XGBoost)
# ===========================================================================
def train_xgboost(train_df: pd.DataFrame, test_df: pd.DataFrame, features: list[str]):
    print("\n--- Training ML Model (XGBoost) ---")
    X_train = train_df[features]
    y_train = train_df["target_long"]
    X_test = test_df[features]
    y_test = test_df["target_long"]
    
    model = xgb.XGBClassifier(n_estimators=100, max_depth=4, learning_rate=0.05, eval_metric="logloss")
    model.fit(X_train, y_train)
    
    preds = model.predict(X_test)
    
    accuracy = (preds == y_test).mean()
    print(f"XGBoost Long Prediction Accuracy: {accuracy*100:.2f}%")
    print("Top Features:")
    imp = pd.Series(model.feature_importances_, index=features).sort_values(ascending=False)
    print(imp.head(5))
    return model

# ===========================================================================
#  2. DL Model (PyTorch MLP)
# ===========================================================================
class PyTorchOracle(nn.Module):
    def __init__(self, input_dim):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(input_dim, 64),
            nn.ReLU(),
            nn.Dropout(0.2),
            nn.Linear(64, 32),
            nn.ReLU(),
            nn.Linear(32, 1),
            nn.Sigmoid()
        )
        
    def forward(self, x):
        return self.net(x).squeeze(-1)

def train_pytorch(train_df: pd.DataFrame, test_df: pd.DataFrame, features: list[str]):
    print("\n--- Training DL Model (PyTorch MLP) ---")
    X_tr = torch.tensor(train_df[features].values, dtype=torch.float32)
    y_tr = torch.tensor(train_df["target_long"].values, dtype=torch.float32)
    X_te = torch.tensor(test_df[features].values, dtype=torch.float32)
    y_te = torch.tensor(test_df["target_long"].values, dtype=torch.float32)
    
    dataset = TensorDataset(X_tr, y_tr)
    loader = DataLoader(dataset, batch_size=256, shuffle=True)
    
    model = PyTorchOracle(len(features))
    optimizer = optim.Adam(model.parameters(), lr=0.001)
    criterion = nn.BCELoss()
    
    epochs = 5
    for epoch in range(epochs):
        model.train()
        total_loss = 0
        for bx, by in loader:
            optimizer.zero_grad()
            out = model(bx)
            loss = criterion(out, by)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
            
    model.eval()
    with torch.no_grad():
        out = model(X_te)
        preds = (out >= 0.5).float()
        acc = (preds == y_te).float().mean().item()
    print(f"PyTorch Epoch {epochs} Loss: {total_loss/len(loader):.4f} | Test Accuracy: {acc*100:.2f}%")
    return model

# ===========================================================================
#  3. RL Model (Gym Environment & SB3 PPO)
# ===========================================================================
class AlgoDeskTradingEnv(gym.Env):
    def __init__(self, df: pd.DataFrame, features: list[str]):
        super().__init__()
        self.df = df.reset_index(drop=True)
        self.features = features
        # Actions: 0=Skip, 1=Long, 2=Short
        self.action_space = spaces.Discrete(3)
        self.observation_space = spaces.Box(low=-np.inf, high=np.inf, shape=(len(features),), dtype=np.float32)
        self.current_step = 0
        
    def reset(self, seed=None, options=None):
        super().reset(seed=seed)
        self.current_step = 0
        return self._get_obs(), {}
        
    def _get_obs(self):
        return self.df.loc[self.current_step, self.features].values.astype(np.float32)
        
    def step(self, action):
        row = self.df.iloc[self.current_step]
        reward = 0.0
        
        # Simplified reward function based on pre-calculated targets
        if action == 1: # Long
            reward = 1.0 if row["target_long"] == 1 else -0.5
        elif action == 2: # Short
            reward = 1.0 if row["target_short"] == 1 else -0.5
        else: # Skip
            reward = 0.0
            
        self.current_step += 1
        terminated = self.current_step >= len(self.df) - 1
        return self._get_obs(), reward, terminated, False, {}

def train_rl(train_df: pd.DataFrame, test_df: pd.DataFrame, features: list[str]):
    print("\n--- Training RL Model (SB3 PPO) ---")
    train_env = AlgoDeskTradingEnv(train_df, features)
    test_env = AlgoDeskTradingEnv(test_df, features)
    
    # Train PPO agent
    model = PPO("MlpPolicy", train_env, verbose=0, n_steps=2048)
    model.learn(total_timesteps=10000)
    
    # Evaluate
    obs, _ = test_env.reset()
    total_reward = 0
    actions = {0: 0, 1: 0, 2: 0}
    done = False
    
    while not done:
        action, _ = model.predict(obs, deterministic=True)
        obs, reward, done, _, _ = test_env.step(action)
        total_reward += reward
        actions[int(action)] += 1
        
    print(f"RL Agent Test Reward: {total_reward:.2f}")
    print(f"Actions Taken: Skips={actions[0]}, Longs={actions[1]}, Shorts={actions[2]}")
    return model

# ===========================================================================
#  Main
# ===========================================================================
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--train-days", type=int, default=30)
    parser.add_argument("--test-days", type=int, default=7)
    args = parser.parse_args()
    
    symbol = "BTCUSDT"
    total_days = args.train_days + args.test_days
    
    print("=" * 70)
    print(" ALGODESK ML/DL/RL MODEL PIPELINE")
    print("=" * 70)
    
    df = create_dataset(symbol, total_days)
    if df.empty:
        print("Failed to create dataset.")
        return
        
    # Split Train/Test sequentially
    split_idx = int(len(df) * (args.train_days / total_days))
    train_df = df.iloc[:split_idx].reset_index(drop=True)
    test_df = df.iloc[split_idx:].reset_index(drop=True)
    
    print(f"\nDataset Ready: {len(train_df)} train rows, {len(test_df)} test rows.")
    
    # Define features
    agent_cols = [f"agent_{i}" for i in range(17)]
    context_cols = ["change_24h_pct", "vol_24h", "funding_rate", "pos", "rsi"]
    features = context_cols + agent_cols
    
    # Normalize context features
    for col in context_cols:
        mean = train_df[col].mean()
        std = train_df[col].std() + 1e-9
        train_df[col] = (train_df[col] - mean) / std
        test_df[col] = (test_df[col] - mean) / std
        
    # Run pipelines
    train_xgboost(train_df, test_df, features)
    train_pytorch(train_df, test_df, features)
    train_rl(train_df, test_df, features)

if __name__ == "__main__":
    main()
