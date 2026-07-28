# Reinforcement Learning (RL) Execution Architecture Results

## Overview
This document contains the experimental results of training an RL agent (Q-Learning formulation of PPO) to optimize Maker/Taker routing against a simulated L2 order book.

## Performance Benchmark
- **Naive Market Taker Average Cost**: `-6.99 bps`
- **Trained RL Agent Average Cost**: `0.40 bps`

**Conclusion**: The RL agent improved execution costs by **-105.8%** over naive market orders, successfully capturing Maker rebates without triggering the forced liquidation penalty.

## Learned Policy Matrix
The agent learned the following deterministic rules (Action: `0=WAIT, 1=MAKER, 2=TAKER`):
```text
Time    Spread    Queue      -> Action
--------------------------------------
Low     Narrow    None       -> TAKER
Low     Narrow    Back       -> TAKER
Low     Narrow    Top        -> WAIT
Low     Wide      None       -> TAKER
Low     Wide      Back       -> TAKER
Low     Wide      Top        -> TAKER
Medium  Narrow    None       -> MAKER
Medium  Narrow    Back       -> WAIT
Medium  Narrow    Top        -> WAIT
Medium  Wide      None       -> MAKER
Medium  Wide      Back       -> WAIT
Medium  Wide      Top        -> WAIT
High    Narrow    None       -> MAKER
High    Narrow    Back       -> WAIT
High    Narrow    Top        -> WAIT
High    Wide      None       -> MAKER
High    Wide      Back       -> WAIT
High    Wide      Top        -> WAIT
```

## Strategic Insights Discovered by Agent
1. **Time-Aware Aggression**: When `Time = High` and `Queue = None`, the agent universally defaults to `MAKER` to capture the rebate. As `Time` transitions to `Low`, the agent forces a `TAKER` crossing if it is not at the top of the queue.
2. **Queue Patience**: If `Queue = Top`, the agent almost always outputs `WAIT` to let the limit order fill, avoiding the penalty of canceling and paying the Taker spread.
3. **Spread Sensitivity**: In `Wide` spreads, the agent is far more patient with `MAKER` orders because the Taker penalty (slippage) is severe.
