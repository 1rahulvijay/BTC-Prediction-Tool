import os
import sys
import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
RESEARCH_MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")

def standard_kelly(recent_trades):
    wins = [t for t in recent_trades if t > 0]
    losses = [t for t in recent_trades if t < 0]
    
    if not wins or not losses:
        return 0.01
        
    win_rate = len(wins) / len(recent_trades) # Denom includes breakevens
    avg_win = sum(wins) / len(wins)
    avg_loss = abs(sum(losses) / len(losses))
    
    win_loss_ratio = avg_win / avg_loss
    kelly = (win_rate * win_loss_ratio - (1 - win_rate)) / win_loss_ratio
    return max(0.0, kelly / 2.0)

def endogenous_kelly(recent_trades):
    wins = [t for t in recent_trades if t > 0]
    losses = [t for t in recent_trades if t < 0]
    
    if not wins or not losses:
        return 0.01
        
    # Remove breakevens from the denominator
    valid_trades = len(wins) + len(losses)
    win_rate = len(wins) / valid_trades
    avg_win = sum(wins) / len(wins)
    avg_loss = abs(sum(losses) / len(losses))
    
    win_loss_ratio = avg_win / avg_loss
    kelly = (win_rate * win_loss_ratio - (1 - win_rate)) / win_loss_ratio
    return max(0.0, kelly / 2.0)

def main():
    print("--- Blueprint V2: The Kelly Sizing Breakeven Trap Test ---")
    
    # Simulate trade history with high amount of scratches (breakevens)
    # Ranging market: 30 wins, 30 losses, 40 breakevens
    trades = [100.0] * 30 + [-50.0] * 30 + [0.0] * 40
    
    k_standard = standard_kelly(trades)
    k_endo = endogenous_kelly(trades)
    
    print(f"Simulated Trade History: 100 Trades (30 Wins, 30 Losses, 40 Scratches/Breakevens)")
    print(f"Standard Kelly (Bugged): Fraction = {k_standard:.4f}")
    print(f"Endogenous Kelly (Fixed): Fraction = {k_endo:.4f}")
    
    print("\nConclusion: The Standard formula treats 40 breakevens as full $50 losses,")
    print("cratering the Kelly fraction to zero. The Fixed formula correctly sizes the edge.")
    
if __name__ == "__main__":
    main()
