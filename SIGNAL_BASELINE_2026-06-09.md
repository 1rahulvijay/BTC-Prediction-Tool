# Signal Baseline — 2026-06-09 (~18:36 local)

First evidence snapshot, ~1-3h after the 30-day model finished training (`evidence-30d-v4`).
**Samples are tiny — treat as a starting point, not a verdict.** Re-run
`python backend/analyze_signals.py` in a few days and compare against this file.

## Context
- Model: 30-day train, `evidence-30d-v4`, completed 15:07 local.
- Market: mostly LOW_VOLATILITY / flat tape during this window.
- Microstructure coverage: ~0% (only just started accruing).
- Reference for the Polymarket mirror at the time of this snapshot: pre-restart logic
  (Chainlink/CoinGecko + spot fallback). The Binance-live-price switch lands on the next restart.

## 1) Polymarket mirror (price_to_beat) — committed UP/DOWN bets only
| Horizon | Bets | Resolved | Correct | Precision | No-bet rounds |
|---|---|---|---|---|---|
| 5m  | 15 | 14 | 9 | **64%** | 17 |
| 15m | 6  | 5  | 1 | **20%** | 6 |

## 2) Combined ensemble — directional leans (raw_direction), strict by move sign
| Horizon | Leans | Resolved | Precision | Committed BUY/SELL |
|---|---|---|---|---|
| 1m  | 2  | 2  | 50% | 0 |
| 3m  | 22 | 21 | 38% | 0 |
| 5m  | 14 | 13 | 62% | 0 |
| 7m  | 10 | 9  | 33% | 0 |
| 10m | 7  | 6  | **83%** | 0 |
| 15m | 5  | 4  | 50% | 0 |
| **TOTAL** | **60** | **55** | **49%** | **0** |

Note: **0 committed BUY/SELL** so far — all leans gated to WAIT (low confidence / flat tape).

## 3) Individual base models (graded vs 3-class neutral band; ~30% = random baseline)
| Model | Votes | Resolved | Correct | Precision |
|---|---|---|---|---|
| cat (CatBoost) | 59 | 54 | 19 | **35%** |
| xgb (XGBoost)  | 58 | 53 | 18 | 34% |
| histgb         | 79 | 74 | 24 | 32% |
| dl (TCN)       | 39 | 34 | 11 | 32% |
| lr (Logistic)  | 105 | 99 | 27 | 27% |
| lgb (LightGBM) | 58 | 53 | 13 | 25% |
| sgd            | 33 | 29 | 6 | **21%** |

Ranking matches OOF training: **cat / xgb / histgb** lead; **lgb / sgd** lag (dynamic weighting down-weights them).

## 4) Kronos + FSR-PPO
- **Kronos** (fallback projection): 52 signals, 50 resolved, 20 correct = **40%** — below the 50% coin-flip. Do not weight it.
- **FSR-PPO challenger**: 119 decisions, **all AVOID** — 0 directional signals (correct, since the ensemble is NEUTRAL).

## What to watch on the next run (in a few days)
1. Does the **5m mirror** hold above ~55%? (encouraging at 64% now, n=14)
2. Do **cat / xgb / histgb** stay above ~32-35% and **sgd / lgb** stay below ~25%? If so, consider dropping sgd/lgb.
3. Do any **committed BUY/SELL** signals start firing (needs higher confidence + a non-flat tape)?
4. Does **10m** (83% now, n=6) survive more samples, or regress to the mean?
5. Target: **>= 500 resolved directional calls per horizon** before any number is trustworthy.
