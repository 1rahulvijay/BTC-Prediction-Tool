# 180-Day BTC Anchor Round-Trip Research

Date: 2026-07-01  
Status: completed, causal v2, paper research only  
Canonical output: `data/research/anchor_roundtrip_180d_30s/`

## Decision

The requested automatic reversal trade is rejected.

- The first eligible fade signal won 41.97% of 3,660 untouched test rounds.
- Its 95% Wilson lower bound was 40.38%.
- A symmetric target/stop requires more than 50% before spread, fees, latency and slippage.
- The BTC-only proxy lost an average $5.41 per signal and $19,790 in total. This is not share PnL, but it is already negative before a Polymarket cost model.

The research did find useful components:

- Conditional current-side hold is a strong late-round ranking signal.
- Activity, side-touch, round-trip and total-range heads predict path shape better than exact direction.
- A narrow 5-minute DOWN subset deserves another independent shadow month, but is not ready to trade.
- Exact closing-price displacement remains effectively unpredictable with this feature set.

No result in this document authorizes live betting.

## What Was Tested

The standalone lane is implemented in:

- `backend/research/test_180d_anchor_roundtrip_strategy.py`
- `run_180d_anchor_roundtrip_strategy.bat`

The experiment uses 180 cached days of Binance one-second trades, resampled to 30-second OHLCV bars. It creates exact clock-aligned 5-minute and 15-minute rounds. The anchor is the first bar open at the round boundary, matching the intended price-to-beat structure as closely as Binance data permits.

The 30-second run was chosen because it is practical on a 16 GB laptop. The script also supports 15-second and one-second modes. A 15-second rerun is useful only as a later sensitivity test for intrabar fade labels; it should not be used to search repeatedly for a better-looking result.

### Data integrity

| Check | Result |
|---|---:|
| Resampled bars | 518,400 |
| Exact rounds | 69,102 |
| 5m rounds | 51,827 |
| 15m rounds | 17,275 |
| Causal non-ambiguous touch events | 73,821 |
| Conditional hold snapshots | 275,622 |
| Gap-skipped rounds | 0 |
| Duplicate round IDs | 0 |
| Missing/infinite model values | 0 |
| Constant open-time features | 0 |
| Runtime | 20.5 minutes |

The split is chronological: 64% train, 16% validation and 20% untouched test. Labels that cross a train/validation or validation/test boundary are purged according to their horizon.

### Conservative causality rules

- All 70 open-time features use completed bars before the anchor.
- Touch models use only completed pre-touch bars plus the known side, barrier and time remaining.
- Entry bars that also cross target or stop are excluded because order is unknowable.
- A later bar crossing both target and stop is counted as a stop-first loss. There were 398 such events.
- Unresolved fades are losses.
- Model choice, isotonic calibration and signal threshold use validation data only. Final metrics use the untouched last 20%.

## Features

The open-time contract contains exactly 70 non-constant features:

| Group | Count | Examples |
|---|---:|---|
| Realized volatility | 8 | 1m through 60m RMS log-return volatility |
| Returns | 9 | 30s through 60m returns |
| Trend | 9 | EMA gaps, acceleration, alignment, efficiency |
| Range | 8 | 1m through 60m ranges, compression, true ATR |
| Volume | 10 | rolling volume, z-score and acceleration |
| Aggressor flow | 5 | taker-buy ratios and acceleration |
| Candle/path state | 8 | body, wicks, close location, alternation, autocorrelation, VWAP stretch |
| Regime state | 6 | volatility ratio, volatility-of-volatility, trend strength, chop, 60m location |
| Time | 7 | hour/day cycles and session flags |

Touch events add barrier, side, time-left and prior-path context. Hold snapshots add current anchor distance, side, path range, efficiency and time-above-anchor. The exact ordered names are in `feature_names.json`.

## Models

Classifiers are trained sequentially to control memory:

1. Logistic Regression
2. Random Forest
3. Extra Trees
4. Histogram Gradient Boosting
5. XGBoost
6. LightGBM
7. CatBoost

Regressors use Ridge, Random Forest, Extra Trees, Histogram Gradient Boosting, LightGBM and CatBoost.

For each target, the three best validation models are averaged. Classification probabilities are then isotonic-calibrated on validation data. This is a research ensemble, not the live app ensemble, and no models were deployed or saved by the default run.

## Untouched-Test Results

### Direction

| Horizon/call | AUC | Selected correct | Signals | Wilson LB | Decision |
|---|---:|---:|---:|---:|---|
| 5m UP | 0.5350 | 53.90% | 538 | 49.68% | Reject |
| 5m DOWN | 0.5385 | 65.28% | 144 | 57.20% | Shadow candidate only |
| 15m UP | 0.5358 | 50.13% | 1,859 | 47.86% | Reject |
| 15m DOWN | 0.5363 | 60.63% | 127 | 51.94% | Too small/unstable |

The 5m DOWN subset produced 94 correct calls out of 144 and was not concentrated in one day. However, it weakened from 70.83% in the first half of test to 59.72% in the second, and the latest partial week was 50%. It needs a frozen threshold and a later independent month before any promotion decision. It also needs actual binary-share ask prices; direction accuracy alone does not imply profit.

### Activity and path

| Target | AUC | Selected precision | Signals | Wilson LB |
|---|---:|---:|---:|---:|
| 5m high activity | 0.7449 | 86.15% | 260 | 81.43% |
| 15m high activity | 0.7340 | 85.71% | 112 | 78.05% |
| 5m touch +$30 | 0.6811 | 80.33% | 1,571 | 78.29% |
| 5m touch -$30 | 0.7017 | 82.85% | 1,079 | 80.49% |
| 5m touches both +/-$30 | 0.8053 | 63.37% | 1,223 | 60.63% |
| 15m touch +$50 | 0.6572 | 77.94% | 843 | 75.01% |
| 15m touch -$50 | 0.6732 | 81.88% | 563 | 78.49% |
| 15m touches both +/-$50 | 0.7626 | 66.76% | 358 | 61.72% |

Small `touch_any` thresholds have very high base rates, so high precision there is mostly trivial. Side-specific touches and round trips carry more information, but still do not define an executable trade.

### Requested fade after a spike

| Horizon/barrier | Action after touch | Selected precision |
|---|---|---:|
| 5m / $10 | Buy UP after down spike | 35.71% |
| 5m / $10 | Buy DOWN after up spike | 44.04% |
| 5m / $20 | Buy UP after down spike | 39.44% |
| 5m / $20 | Buy DOWN after up spike | 40.74% |
| 5m / $30 | Buy UP after down spike | 41.57% |
| 5m / $30 | Buy DOWN after up spike | 38.79% |
| 15m / $20 | Buy UP after down spike | 40.63% |
| 15m / $20 | Buy DOWN after up spike | 39.08% |
| 15m / $30 | Buy UP after down spike | 38.98% |
| 15m / $30 | Buy DOWN after up spike | 45.82% |
| 15m / $50 | Buy UP after down spike | 44.86% |
| 15m / $50 | Buy DOWN after up spike | 45.51% |

None clears 50%. The model has some ranking ability, but the selected trades are not profitable under the symmetric BTC barrier proxy.

In binary-market language, the intended trade is to buy the temporarily losing share at a low price after BTC moves away from the anchor, then sell if BTC and the share price revert. It is not literally “buy high and sell low.” Historical BTC paths cannot reconstruct the executable Polymarket entry ask and exit bid, so a true round-trip share backtest requires recorder quotes.

### Conditional hold

The first selected hold signal per round was correct 99.53% on 4,845 rounds, with a 99.29% Wilson lower bound.

| Horizon | Signals | Correct | Typical absolute anchor lead |
|---|---:|---:|---:|
| 5m | 2,996 | 99.37% | median $121.76 |
| 15m | 1,849 | 99.78% | median $140.52 |

This is not a free 99% trading strategy. The binary market usually prices a large late lead close to $1.00. The decision rule must be `conservative fair probability - executable ask - fee - slippage - safety buffer > 0`. Without the ask, the result only says which side is likely to settle, not whether buying it is profitable.

### Dollar-path regression

| Horizon | Target | MAE | R2 | Decision |
|---|---|---:|---:|---|
| 5m | Maximum up excursion | $46.23 | 0.167 | Context only |
| 5m | Maximum down excursion | $45.93 | 0.158 | Context only |
| 5m | Total high-low path range | $50.74 | 0.415 | Useful range head |
| 5m | Exact close displacement | $64.17 | -0.001 | Reject |
| 15m | Maximum up excursion | $79.89 | 0.162 | Context only |
| 15m | Maximum down excursion | $82.19 | 0.132 | Context only |
| 15m | Total high-low path range | $91.27 | 0.382 | Useful range head |
| 15m | Exact close displacement | $113.37 | 0.000 | Reject |

The tool can estimate how wide the path may be better than it can estimate the exact final price. UI language should therefore show a likely range and uncertainty, not a falsely precise projected close.

## Time And Regime Findings

Hours use Europe/Warsaw local time. They were selected on the first 80% and measured on the untouched final 20%.

- 5m development-selected round-trip hour 16 had a 47.22% test round-trip rate.
- 15m development-selected round-trip hour 16 had a 54.86% test round-trip rate.
- Development-selected high-volume hour 14 remained elevated in test for both horizons.
- High-volatility regimes produced more range and round trips, but did not rescue fade profitability.

These are descriptive windows, not trade permissions. Multiple window comparisons require a later frozen-window test.

## What To Use In The App

Do not wire the fade action into live betting.

Safe candidates for shadow/recorder evaluation are:

1. Activity probability as a market-likely-to-move context head.
2. Side-touch and round-trip probability as path-planning context.
3. Range prediction as a zone, never an exact target.
4. Conditional P(Hold), gated by the real ask and fee.
5. Frozen 5m DOWN threshold `p >= 0.65`, shadow-only for at least one independent month.

The champion must continue to separate these questions:

- Will price move enough?
- Which side is more likely to finish above the anchor?
- Is a reversal likely after a touch?
- Is the current side likely to hold?
- Is the market price cheap enough to have positive expected value?

No single probability answers all five.

## Required Profit Test

For every candidate entry, join the exact market/round, anchor, official settlement, executable entry ask, executable exit bid or payout, available size, fees, latency, slippage, model version and frozen threshold.

Then report one entry per round, net PnL, expectancy, profit factor, drawdown, calibration, retained-call precision and Wilson lower bounds. Promotion requires positive results in a later untouched time period, not this 180-day research sample.

## Artifacts

The result directory contains:

- `REPORT.md`: generated full scorecard
- `classification_metrics.csv`: every classifier and ensemble metric
- `regression_metrics.csv`: every regressor and ensemble metric
- `strategy_metrics.csv`: first-entry fade and hold decisions
- `scenario_metrics.csv`: side/barrier breakdown
- `classification_predictions.csv`: untouched-test probabilities and labels
- `regression_predictions.csv`: untouched-test path estimates
- `fade_test_signals.csv`: every fade candidate
- `hold_test_signals.csv`: every hold snapshot
- `combined_test_signals.csv`: per-round research outputs
- `window_selection.csv`: development-selected/test-measured windows
- `open_rounds.parquet`, `fade_events.parquet`, `hold_snapshots.parquet`: labeled datasets
- `feature_names.json`, `config.json`, `run.log`: reproducibility metadata

## Reproduction

```powershell
.\run_180d_anchor_roundtrip_strategy.bat
```

The default run uses 180 days, 30-second bars, four threads, sequential models and no model persistence. It does not modify live app models or place orders.
