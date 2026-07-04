# Round-State And Stopping Results

Date: 2026-07-01  
Status: completed, causal 180-day historical research, paper only  
Implementation: `backend/research/test_180d_round_state_and_stopping.py`  
Runner: `run_180d_round_state_stopping.bat`  
Output: `data/research/round_state_stopping_180d_30s/`

## Purpose

This lane tested the remaining predictions that can be labeled from the existing 30-second Binance archive without pretending that BTC paths are executable Polymarket trades:

- whether an anchor-side lead flips later in the round;
- whether a touched side remains the settlement winner;
- whether a $20, $50, or $100 shock occurs before settlement;
- when volatility begins;
- anchor recross count and time spent above the anchor;
- high, low, and volume-peak timing;
- current and next-round path archetype;
- how many rounds remain until the next path opportunity;
- validation-selected take-profit, stop-loss, and timed exits;
- previously tested generic model-failure prediction.

No live application model or betting behavior was changed.

## Validation Design

- 180 days of cached Binance 30-second bars.
- Exact non-overlapping 5m and 15m rounds.
- Causal features available at the prediction timestamp.
- Chronological 64% train, 16% validation, and 20% untouched test partitions.
- Outcome-aware purging at split boundaries, including extended next-round labels.
- Seven sequential classifier families and six regressor families.
- Top-three validation ensemble with isotonic probability calibration for binary heads.
- Selection thresholds learned on validation only.
- Stopping policy selected on the first half of the prior test slice and reported on its untouched second half.
- Whole-day block bootstrap confidence intervals, preserving within-day dependence.

## Executive Decisions

| Prediction | 5m result | 15m result | Decision |
|---|---:|---:|---|
| Future side flip from an in-round snapshot | AUC 0.816 | AUC 0.891 | RETAIN as reversal-risk head |
| $50 late shock | AUC 0.851 | AUC 0.845 | RETAIN as path-risk head |
| $100 late shock | AUC 0.891 | AUC 0.888 | RETAIN as rare-tail probability |
| Touched side settles | 98.78%, LB 93.41%, n=82 | 97.56%, LB 93.07%, n=123 | SHADOW; selective and small |
| Opportunity within 3 rounds | AUC 0.837 | AUC 0.812 | RETAIN as scheduling context |
| Rounds to next opportunity | R2 0.404 | R2 0.340 | RETAIN as an estimate, not a timer |
| Volatility-onset bucket | Macro AUC 0.729 | Macro AUC 0.697 | RETAIN probabilities; reject exact bucket |
| Flip known at round open | AUC 0.518 | AUC 0.519 | REJECT |
| Last flip in final 120 seconds | AUC 0.519 | AUC 0.506 | REJECT |
| Exact recross/archetype/extreme timing | Weak or imbalanced | Weak or imbalanced | REJECT exact calls |
| Irreversible-lead time | Worse than median baseline | Worse than median baseline | REJECT |
| Generic model-failure head | Previously failed | Previously failed | REJECT |

## 1. In-Round Reversal Risk

At 120, 90, 60, and 30 seconds before settlement, the model predicts whether price will cross to the other side of the anchor before the round ends.

| Horizon | Test rows | Base rate | AUC | Selected calls | Precision | Wilson lower bound |
|---|---:|---:|---:|---:|---:|---:|
| 5m | 41,464 | 20.21% | 0.816 | 1,956 | 62.27% | 60.10% |
| 15m | 13,820 | 8.65% | 0.891 | 441 | 51.70% | 47.04% |

This is a useful warning head. It answers `is the current side still at meaningful risk of flipping?`; it does not independently tell the bot which share to buy.

The same event could not be predicted usefully at round open. Open-time `has_anchor_flip` AUC was only 0.518/0.519, while `last_flip_in_final_120s` was 0.519/0.506. The information appears during the path, not before it begins.

## 2. Touch-To-Settlement Conversion

Once a primary barrier is touched, this head predicts whether that touched side remains the winning side at settlement.

| Horizon | Test rows | Base rate | AUC | Selected calls | Precision | Wilson lower bound |
|---|---:|---:|---:|---:|---:|---:|
| 5m | 3,664 | 77.97% | 0.605 | 82 | 98.78% | 93.41% |
| 15m | 2,135 | 75.04% | 0.615 | 123 | 97.56% | 93.07% |

This is high precision only because the validation-selected gate is extremely selective. It is a good shadow candidate, but 82 and 123 calls are too few for live promotion and no Polymarket ask, fee, fill, or exit information is present.

## 3. Late-Round Shock Risk

The snapshot heads predict whether the remaining path reaches an absolute move of at least $20, $50, or $100 from the snapshot price.

| Horizon | Shock | AUC | Base rate | Selected precision | Wilson lower bound |
|---|---:|---:|---:|---:|---:|
| 5m | $20 | 0.832 | 63.66% | 97.92% | 97.57% |
| 5m | $50 | 0.851 | 25.21% | 83.61% | 82.40% |
| 5m | $100 | 0.891 | 6.87% | 27.93% | 26.93% |
| 15m | $20 | 0.818 | 58.57% | 96.73% | 95.96% |
| 15m | $50 | 0.845 | 21.06% | 74.02% | 71.60% |
| 15m | $100 | 0.888 | 5.18% | 22.90% | 21.23% |

The $100 models have excellent ranking AUC but intentionally low absolute precision because the event is rare. They should be displayed as tail-risk probabilities, not converted into unconditional action signals.

## 4. Opportunity Drought

An opportunity is defined using the previously labeled path-opportunity event, not executable profit.

| Horizon | Window | AUC | Base rate | Selected precision | Wilson lower bound |
|---|---:|---:|---:|---:|---:|
| 5m | Within 3 rounds | 0.837 | 46.61% | 89.89% | 88.31% |
| 5m | Within 6 rounds | 0.867 | 62.42% | 98.33% | 97.64% |
| 15m | Within 3 rounds | 0.812 | 59.19% | 89.19% | 87.10% |
| 15m | Within 6 rounds | 0.843 | 75.44% | 97.17% | 95.93% |

Continuous rounds-to-next-opportunity forecasts also beat the training-median baseline:

| Horizon | Model MAE | Baseline MAE | R2 |
|---|---:|---:|---:|
| 5m | 2.434 rounds | 3.630 rounds | 0.404 |
| 15m | 2.146 rounds | 2.832 rounds | 0.340 |

These heads can reduce needless participation during dry periods. They do not say that the next event has positive expected value.

## 5. Path State And Timing

Volatility onset contains ranking information, with macro AUC 0.729 at 5m and 0.697 at 15m. Exact class performance is poor because the model mostly distinguishes `EARLY` from `NONE` and misses minority `MIDDLE`/`LATE` states. Retain the probability vector only.

The following exact predictions failed or were too weak:

- anchor recross bucket: macro AUC 0.532/0.522;
- current path archetype: macro AUC 0.637/0.632 but balanced accuracy only 26.91%/24.56%;
- next path archetype: macro AUC 0.632/0.620 with weak balanced accuracy;
- high and low timing buckets: macro AUC about 0.52;
- volume-peak timing bucket: macro AUC 0.556/0.533;
- irreversible lead: MAE 90.51s versus 86.36s baseline at 5m, and 269.92s versus 262.57s at 15m;
- high/low timing fractions: no baseline improvement;
- time above anchor and volume-peak fraction: tiny R2 only, unsuitable for action.

Path archetypes are retrospective labels. Their performance breakdown may diagnose outcomes, but the realized archetype must never be used as an entry filter.

## 6. Validation-Selected Stopping Policy

The policy search compared 30s, 60s, 120s, and settlement exits plus a TP/SL grid of $10/$20/$30/$50. It used the exclusive `base70_plus_flow` first-touch-side signals from the prior causal test. Policy selection used the first half of those old test calls with a $2 cost; all figures below use the untouched second half.

Both horizons selected `take profit $50 / stop loss $10`. If both barriers occur in one 30-second bar, the stop is scored first, which is deliberately conservative.

### Selected policy

| Horizon | Signals | Cost | Mean PnL | Profit factor | Win rate | CVaR 5% | Max drawdown | Day-block 95% mean CI |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 5m | 356 | $0 | $13.80 | 3.46 | 43.54% | -$10 | $100.62 | $10.68 to $16.93 |
| 5m | 356 | $2 | $11.80 | 2.75 | 42.98% | -$12 | $126.62 | $8.68 to $14.93 |
| 5m | 356 | $5 | $8.80 | 2.04 | 42.42% | -$15 | $165.62 | $5.68 to $11.93 |
| 15m | 897 | $0 | $8.02 | 2.15 | 30.10% | -$10 | $150.00 | $6.55 to $10.00 |
| 15m | 897 | $2 | $6.02 | 1.72 | 30.10% | -$12 | $192.00 | $4.55 to $8.00 |
| 15m | 897 | $5 | $3.02 | 1.29 | 30.10% | -$15 | $285.00 | $1.55 to $5.00 |

The median trade is a loss because the policy deliberately accepts frequent small stops for fewer $50 wins. Raw win rate therefore understates the payoff asymmetry.

### Settlement control

- 5m settlement at $2 cost: mean +$10.40, profit factor 1.41, day-block CI +$1.74 to +$19.13, max drawdown $1,485.96.
- 15m settlement at $2 cost: mean -$3.16, profit factor 0.94, day-block CI -$9.36 to +$3.30, max drawdown $5,773.78.

Stopping sharply reduces tail loss and drawdown. It is especially important at 15m, where holding the BTC-direction proxy to settlement loses money.

### Stability diagnostics

- Final evaluation span: 2026-06-08 through 2026-06-27 for 5m; 2026-06-10 through 2026-06-27 for 15m.
- Whole-day block bootstrap includes 20 independent days at 5m and 18 at 15m.
- At $2 cost, every open-time regime had positive mean PnL, but trend-regime samples were small.
- Realized trend-path outcomes generated most profit. This is diagnostic only because realized path archetype is unavailable at entry.

The narrow calendar span is the main robustness limitation. The positive block-bootstrap bounds show consistency across those days, not across multiple market eras.

Follow-up: `TP50_SL10_WALKFORWARD_AUDIT_2026-07-01.md` regenerated the side signals across five expanding
eras. The fixed policy stayed positive in every 5m/15m fold at the $2 proxy cost, with policy-overfitting
estimates of 0.00/0.10. This improves historical confidence but does not remove the Polymarket execution boundary.

## 7. Why This Is Not Yet A Profitable Polymarket Strategy

The stopping results measure signed BTC-dollar movement from a synthetic entry at the round anchor. They do not include:

- the UP/DOWN share ask actually available when the signal fired;
- Polymarket fees and spread;
- fill probability, queue position, partial fills, or latency;
- the nonlinear relationship between BTC distance/time remaining and share price;
- the bid available when the TP/SL condition is reached;
- Chainlink settlement/oracle differences;
- rejected or missed orders.

A $50 favorable BTC move is not automatically a $50 trade profit. The policy must be replayed against recorded quotes and settlements before it can become PAPER, and PAPER must pass an independent forward window before any live capital decision.

## 8. Promotion Order

1. Shadow-log future-side-flip, touch-to-settlement, shock, and drought probabilities.
2. Store the exact prediction timestamp, BTC anchor/distance, UP/DOWN ask and bid, model version, regime, and eventual settlement.
3. Replay TP50/SL10 using executable share bids/asks rather than BTC-dollar proxy PnL.
4. Require positive net expectancy after fees, spread, slippage, and rejected fills.
5. Require at least 100 resolved selected calls per horizon and a positive lower confidence bound in a new non-overlapping period.
6. Run PAPER only; do not auto-bet from these historical results.

## Artifacts

- `REPORT.md`: generated full metric tables.
- `binary_metrics.csv` and `binary_predictions.csv`: calibrated binary head evidence.
- `multiclass_metrics.csv`: path-state classification evidence.
- `regression_metrics.csv`: continuous-target evidence and baselines.
- `stopping_policy_metrics.csv`: policy metrics, costs, drawdown, tail loss, and day-block intervals.
- `stopping_trade_results.csv`: every final-half stopping trade for audit and slicing.
- `round_states.parquet`, `late_snapshots.parquet`, `touch_settlement.parquet`, and `transition_drought.parquet`: reproducible labeled datasets.

## Final Verdict

The test found useful **risk and timing context**, not a universal direction oracle. The strongest new live candidates are in-round side-flip risk, late-shock risk, selective touch-to-settlement conversion, and opportunity-drought forecasting.

The TP50/SL10 BTC-path stopping result is statistically promising and materially better than holding to settlement, particularly at 15m. It remains a research result until Polymarket quote replay proves that the apparent BTC-path edge survives market pricing and execution.
