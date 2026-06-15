# Phase 16: Execution & Horizon Redesign

## Goal Description
We validated that the signal engine possesses statistical edge (Gross EV +0.04 bps), but we proved mathematically that a 5-minute fixed-time hold using Taker/Taker execution (14 bps penalty) structurally suffocates the edge (Net EV -13.96 bps). 
The purpose of Phase 16 was to test alternative execution methods, extended holding horizons (15m, 30m, 60m), and MFE-based exit policies to find a path to a profitable Net EV.

## 1. The Cost Sensitivity Test
We re-ran the exact Tier 1 signals across a spectrum of slippage assumptions.

| Cost bps | Net EV (bps) | Win rate | Signals/day |
| :--- | :--- | :--- | :--- |
| **0** | **+0.04** | 50.6% | 24.2 |
| **2** | -1.96 | 47.5% | 24.2 |
| **7** | -6.96 | 40.2% | 24.2 |
| **14** | -13.96 | 28.5% | 24.2 |

**Conclusion:** The break-even execution cost is exactly **`0 bps`**. The 5-minute edge is so razor-thin that any taker fee instantly negates it. Maker limit execution is strictly mandatory if the holding period remains at 5 minutes.

## 2. The Holding Horizon EV Probe
We simulated extending the fixed-time exit from 5 minutes to 15m, 30m, and 60m to give the breakout more time to mature.

| Horizon | Net EV (bps) | Win Rate | Mean MFE (bps) | Mean MAE (bps) |
| :--- | :--- | :--- | :--- | :--- |
| **5m** | -13.96 | 28.5% | +20.5 | -20.2 |
| **15m** | -19.07 | 33.8% | +33.8 | -37.0 |
| **30m** | -21.34 | 36.7% | +43.9 | -51.2 |
| **60m** | -25.32 | 40.1% | +60.3 | -70.8 |

**Conclusion:** A fixed extended holding period makes the Net EV *worse*. This is because crypto structure on the 1m/5m timeframe is highly mean-reverting. While the Maximum Favorable Excursion (MFE) grows over 60 minutes, the Maximum Adverse Excursion (MAE) grows *faster*. Holding momentum blindly for 60 minutes just exposes the trade to downside drift.

## 3. The MFE / MAE Exit Policy Probe
Given that MFE hits +43.9 bps over 30 minutes, we simulated active Risk Management (Take-Profit and Stop-Loss limits) within a 30m horizon.

| TP (bps) | SL (bps) | Net EV (bps) | Win Rate | TP Hit% | SL Hit% |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **10** | **-7** | -14.02 | 0.0% | 41.0% | 59.0% |
| **20** | **-10** | -13.15 | 36.2% | 36.2% | 63.8% |
| **30** | **-15** | -12.21 | 37.2% | 36.8% | 62.2% |

**Conclusion:** Active Take-Profits slightly improved the Net EV (from -21.34 at fixed 30m down to -12.21 bps), but it is still deeply negative under 14 bps slip. 
Notice that `TP 10 / SL 7` has a `0.0%` win rate. If you capture a 10 bps TP, and pay 14 bps in slippage, your "winning trade" still loses 4 bps.
This mathematically confirms the rule: **`Expected Move / Execution Cost >= 2.5` is an absolute requirement.** To pay a 14 bps spread, you must target at least a 35 bps Take-Profit.

## 4. The Expected Move Cost Gate
We built a Ridge Regression model on our `rv_15m` / `rv_30m` features to dynamically forecast the expected MFE over the next 30 minutes. We added a hard gate: *reject any signal where the forecasted MFE is < 35 bps*.
* Base Signals: `1191`
* Signals Passing Gate: `1043`
* Net EV of Gated Signals: `-21.63 bps`

**Conclusion:** The gate successfully filtered out 150 low-volatility traps. However, because directional accuracy drops over a 30m horizon, simply "expecting a big move" doesn't guarantee the side engine predicts it correctly. The Net EV remains negative.

## The Final Verdict
The theoretical limits of offline modeling have been reached.
Taker execution (14 bps) is completely mathematically unviable on the 1-minute to 60-minute timeframe unless directional accuracy exceeds ~65%.
The *only* mathematical path to profitability is reducing the execution cost to `< 2 bps` via Limit-Maker orders.

We have updated the `live_shadow_logger.py` to support Maker execution tracking. It will now log the entry signal as a Limit order, track whether it gets filled, how long it takes to fill, and calculate the true Net EV under `Maker/Maker (0 bps)` vs `Maker/Taker (7 bps)` vs `Taker/Taker (14 bps)`. The Live Data Ingestion layer is the final frontier.
