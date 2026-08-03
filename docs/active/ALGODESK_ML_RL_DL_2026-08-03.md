# AlgoDesk ML / DL / RL pipeline — built to spec, and what it measures

**Date** `2026-08-03` · **Script** `research/algodesk_ml_rl_dl.py` · **Status** DIAGNOSTIC ONLY

Built to the AlgoDesk system prompt: Bybit 5-minute klines, 7 symbols, 40 days, the 17 rule
agents used as **features** rather than executors, a 22-dimensional vector (17 agent signals +
5 context features), and XGBoost / PyTorch MLP / Stable-Baselines3 PPO. All four Part 4
optimizations implemented exactly as written.

```
data     80,647 bars, 7 symbols, 5m       sha256 e6f8ab50f57152c6...
         OI 100% coverage, funding 100% coverage (both REAL, both also simulated per Part 2)
split    30d train / 288-bar purge / 10d test        test scored once
barriers TP 0.5% / SL 0.5% (Part 4.1)     cost 14 bps round trip
```

## Result

### REAL funding and open interest

| model | trades | cover% | win% | net bps | day-block 95% CI |
|---|---:|---:|---:|---:|---|
| always-long | 3,814 | 100.0 | 41.8 | −22.2 | [−31.4, −11.1] |
| agent vote | 3,491 | 91.5 | 40.3 | −23.7 | [−32.4, −17.2] |
| XGBoost | 1,635 | 42.9 | 41.5 | −22.5 | [−32.4, −7.0] |
| PyTorch MLP | 2,593 | 68.0 | 43.7 | −20.3 | [−33.2, −7.0] |
| PPO (RL) | 3,814 | 100.0 | 41.8 | −22.2 | [−31.4, −11.1] |

### SIMULATED funding and OI (the spec's Part 2)

| model | trades | cover% | win% | net bps | day-block 95% CI |
|---|---:|---:|---:|---:|---|
| always-long | 5,001 | 100.0 | 43.8 | −20.2 | [−31.0, −7.5] |
| agent vote | 4,169 | 83.4 | 44.0 | −20.0 | [−26.1, −14.7] |
| XGBoost | 2,456 | 49.1 | 42.3 | −21.7 | [−33.6, −4.0] |
| PyTorch MLP | 2,592 | 51.8 | 39.9 | −24.1 | [−32.4, −9.7] |
| PPO (RL) | 4,861 | 97.2 | 45.3 | −18.7 | [−29.8, −6.2] |

**Every model, both data variants, every day-block CI entirely below zero.** These are not
underpowered results — 3,814 test trades is ample. The negative is established.

## Part 4.1 makes the problem harder, and this is arithmetic

Cost is paid on every trade, win or lose. The break-even win rate for a barrier pair is

```
p* = (SL + cost) / (TP + SL)

original   3.0% / 1.5%   ->  (150 + 14) / 450  =  36.4%
tightened  0.5% / 0.5%   ->  ( 50 + 14) / 100  =  64.0%
```

Tightening the barriers does produce more "win" labels, exactly as Part 4 predicts. It also
raises the bar the model must clear from **36.4% to 64.0%**. At a 50 bps target, the 14 bps
round trip is **28% of the target**.

Observed win rates across every model and both variants: **39.9% – 45.3%**. The gap to
break-even is roughly **20 percentage points**, and nothing in the pipeline moves it.

## What the three optimizations actually did

They did what they were asked. None of them produced a positive expectancy.

- **`scale_pos_weight` (XGBoost)** — coverage 42.9%, win rate 41.5%. The model was pushed to
  trade a large subset, and that subset wins at the same rate as trading everything.
- **Weighted `BCELoss` (MLP)** — coverage 68.0%, win rate 43.7%. Best win rate on real data,
  still 20 points short.
- **SKIP penalty −0.01 (PPO)** — coverage **100.0%**. The agent stopped skipping entirely and
  **collapsed to always-long**: identical trades, identical 41.8% win rate, identical −22.2 bps.
  Penalising SKIP removed the option that the reward function had been selecting.

The diagnosis in the spec — "the models predict Failure 100% and the RL agent SKIPs 100%" —
was not a class-imbalance artifact. It was the models correctly reporting that a 64% win rate
is not attainable from these features. Forcing them to trade did not change the win rate; it
only changed how many losing trades they took.

**The win rate is the invariant here.** Across five models, two data variants and coverage
ranging from 42.9% to 100%, it stays inside 39.9%–45.3%. No subset of the 22-dimensional
feature space separates winners from losers. That is a statement about the features, and no
amount of class weighting or reward shaping addresses it.

## Real versus simulated funding and OI

Both were computed so the difference is measurable rather than argued.

| | train samples | test samples |
|---|---:|---:|
| REAL funding/OI | 15,118 | 3,814 |
| SIMULATED (Part 2) | 18,207 | 5,001 |

The simulation yields **~20–30% more signals**, because `funding = change_8h × 0.05` reaches
the agents' 0.0015–0.003 thresholds that real funding never approaches. Measured over these 7
symbols and 40 days, real funding ranged **−0.000256 to +0.000100** — six times below the
smallest threshold any funding agent requires.

So the simulated variant is not a neutral stand-in: it manufactures signals for FUND, CONTRA
and SENT that cannot exist, and those signals encode 8-hour momentum. The economic conclusion
happens to be the same here, because the barrier arithmetic dominates — but the sample
inflation is real and would matter for any per-agent claim.

## Causality and accounting

- Entry is the **next bar's open**, never the close that produced the signal.
- Every 24h aggregate is `.shift(1)`-ed; funding and OI are joined as-of and shifted.
- Barriers resolve forward with a 288-bar (24h) horizon; unresolved samples are dropped rather
  than counted as either outcome.
- Costs charged on both legs of every trade.
- A **288-bar purge** separates train from test, so no training sample resolves inside test.
- Day-block bootstrap CIs: samples within a day share regime and are not independent.
- Agents whose real inputs are absent emit **0**, never a proxy — asserted in the selftest.

Selftest: 16 checks, including that the tightened barriers raise the break-even rate.

## Verdict

The pipeline is built as specified and runs end to end: XGBoost, a PyTorch MLP, and a PPO agent
in a Gymnasium environment, over 22-dimensional features, with all four Part 4 optimizations.

On 3,814 out-of-sample trades with real funding and open interest, every model loses, every
confidence interval excludes zero, and the win rate is invariant to model class, to coverage
forcing, and to whether funding is real or simulated.

The binding constraint is not the model, the class balance, or the reward shape. It is that a
0.5%/0.5% barrier costs 14 bps to attempt and requires 64% accuracy, and these features deliver
about 42%.
