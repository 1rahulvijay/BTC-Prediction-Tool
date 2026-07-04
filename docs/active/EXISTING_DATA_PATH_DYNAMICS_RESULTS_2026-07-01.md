# Existing-Data Path Dynamics Results

Date: 2026-07-01  
Status: completed, causal 180-day test, paper only  
Implementation: `backend/research/test_180d_path_dynamics.py`  
Runner: `run_180d_path_dynamics.bat`  
Output: `data/research/path_dynamics_180d_30s/`

## Purpose

This experiment tested everything materially new that could be labeled from historical files already present on disk, without using the forward Polymarket recorder:

- which primary barrier is touched first;
- when the first touch occurs;
- joint touch-side/time classes;
- continuation after the touch;
- partial and full retracement depth;
- whether the next round contains a movement/round-trip/activity opportunity;
- future aggressive taker-flow bursts;
- continuous continuation, retracement and flow magnitude;
- whether Binance perpetual flow leads Binance spot flow;
- whether the existing spot/perpetual/VPIN/funding bundle improves candle-only models.

No live model or application behavior was changed.

## Data And Validation

- 69,100 exact 5m/15m rounds after next-round label alignment.
- 28,991 causal primary-touch events.
- 70 validated candle/trade features.
- 13 additional historical flow features: CVD, VPIN, large trades, funding, spot/perpetual CVD, basis and volumes.
- Complete flow-feature coverage: 98.34%.
- Chronological 64% train, 16% validation, 20% untouched test.
- Horizon-aware purge at partition boundaries.
- Seven classifier families and six regressor families, trained sequentially.
- Candle-only and candle-plus-flow variants use identical rows.
- Full runtime: 25.0 minutes, plus a clean normalized multiclass-only rebuild.

## Executive Decisions

| Prediction | Result | Decision |
|---|---|---|
| 5m first-touch side | Exclusive calls 58.37%, Wilson LB 54.71%, n=711 | SHADOW candidate |
| 15m first-touch side | Exclusive calls 52.93%, Wilson LB 50.61%, n=1,793 | Weak/context only |
| First-touch time bucket | Macro AUC 0.732 / 0.759 | RETAIN as probability distribution |
| Joint side+time class | Accuracy 25.0% / 23.3% | REJECT exact class |
| Flow burst | AUC about 0.70-0.73 | RETAIN as activity context |
| Next-round round trip | AUC 0.795 / 0.753 | RETAIN as opportunity context |
| Post-touch continuation | AUC 0.51-0.56; small or no lift | REJECT |
| Retracement depth | AUC 0.53-0.61; weak precision | Context only |
| Continuous continuation/retrace size | Worse MAE than median baseline | REJECT |
| Future taker imbalance magnitude | R2 about 0.05 | Weak context only |
| Spot/perpetual flow leadership | Worse than follower's own persistence | REJECT leadership claim |
| Extra historical flow feature bundle | AUC lifts mostly 0.0-0.6 percentage points | Do not expand ensemble |

## 1. Competing First-Touch Side

The primary barrier is +/-$30 for 5m and +/-$50 for 15m. Ambiguous same-bar UP/DOWN touches are excluded from the three-class model.

### Independent binary side heads

| Horizon | Side | Candle AUC | +Flow AUC | +Flow selected precision | Wilson LB |
|---|---|---:|---:|---:|---:|
| 5m | UP first | 0.576 | 0.581 | 57.85% | 53.60% |
| 5m | DOWN first | 0.598 | 0.600 | 59.89% | 52.64% |
| 15m | UP first | 0.542 | 0.543 | 51.49% | 48.04% |
| 15m | DOWN first | 0.562 | 0.567 | 52.99% | 50.29% |

After requiring exactly one side signal:

- 5m candle-plus-flow: 711 calls, 58.37% correct, Wilson LB 54.71%.
- 15m candle-plus-flow: 1,793 calls, 52.93% correct, Wilson LB 50.61%.
- 15m produced 167 contradictory dual-side signals before exclusivity filtering.

The 5m result is a legitimate shadow candidate for **first path direction**, not settlement direction. It does not prove that buying a Polymarket UP/DOWN share is profitable.

### Multiclass side model

| Horizon | Variant | Macro AUC | Accuracy | Balanced accuracy |
|---|---|---:|---:|---:|
| 5m | Candle only | 0.680 | 46.24% | 53.00% |
| 5m | + historical flow | 0.686 | 48.53% | 52.55% |
| 15m | Candle only | 0.658 | 43.04% | 51.68% |
| 15m | + historical flow | 0.661 | 44.40% | 52.21% |

The model ranks the three outcomes better than chance but is not accurate enough to emit a simple deterministic side on every round.

## 2. First-Touch Timing

Classes are `30S`, `60S`, `120S`, `LATE`, and `NONE`.

| Horizon | Macro AUC | Accuracy | Balanced accuracy |
|---|---:|---:|---:|
| 5m | 0.732 | 43.61% | 38.41% |
| 15m | 0.759 | 49.44% | 40.88% |

This is useful as a probability distribution for path speed. Exact bucket classification is still weak, especially for the 60-second class. Use it to describe fast/slow movement risk, not as a precise timer.

## 3. Joint Side And Time

The nine-class joint model combines UP/DOWN with 30s/60s/120s/late, plus no touch.

- 5m macro AUC: 0.691; accuracy: 24.95%.
- 15m macro AUC: 0.713; accuracy: 23.33%.

Decision: reject exact joint-class calls. Side and timing should remain separate probabilistic heads.

## 4. Post-Touch Continuation

Continuation asks whether price extends another $10 or $20 in the touched direction before returning to the anchor or reaching the equal-distance stop. Same-bar ordering is scored conservatively as not successful.

| Horizon/target | AUC | Selected precision | Base rate | Decision |
|---|---:|---:|---:|---|
| 5m continue $10 | 0.526 | 50.3% | 48.3% | Reject |
| 5m continue $20 | 0.515 | 25.9% | 24.9% | Reject |
| 15m continue $10 | 0.562 | 71.8% | 67.4% | Weak lift only |
| 15m continue $20 | 0.531 | 56.9% | 55.2% | Reject |

The 15m $10 continuation pocket improves only about 4.4 percentage points over a high base rate. It is insufficient for a trading rule.

## 5. Retracement Depth

Retracement labels measure whether price returns 25%, 50%, 75% or 100% of the primary barrier before the adverse stop.

- 5m AUC range: approximately 0.56-0.60.
- 15m AUC range: approximately 0.53-0.61.
- Full 5m retracement selected precision: about 35.9% versus a 29.0% base rate.
- Full 15m retracement selected precision: about 44.9% versus a 36.9% base rate.

These heads contain modest ranking information but not enough to revive the rejected fade strategy.

## 6. Next-Round Opportunity Arrival

This predicts path conditions in the following same-horizon round. Labels span both rounds and are purged correctly at split boundaries.

### Round-trip opportunity

| Horizon | AUC | Selected precision | Wilson LB | Base rate |
|---|---:|---:|---:|---:|
| 5m | 0.795 | 63.18% | 59.81% | 22.87% |
| 15m | 0.753 | 56.23% | 52.55% | 29.99% |

This is the strongest genuinely new path-opportunity head. It predicts a choppy/two-sided next round, not a profitable trade. It can help choose between trend and range strategy templates.

### Primary touch and activity

- Next primary touch has AUC 0.780/0.815, but base rates are already 85.8%/92.7%; headline precision is mostly trivial.
- Next high activity has AUC only 0.57-0.58 and is weak.

## 7. Aggressive Flow Bursts

A burst requires future volume at least 1.25x normal and taker imbalance beyond +/-8%.

| Horizon | Target | AUC | Selected precision | Base rate |
|---|---|---:|---:|---:|
| 5m | Any burst | 0.725 | 79.78% | 29.78% |
| 5m | Up burst | 0.716 | 39.73% | 14.59% |
| 5m | Down burst | 0.719 | 37.56% | 15.19% |
| 15m | Any burst | 0.704 | 62.18% | 27.37% |
| 15m | Up burst | 0.698 | 32.71% | 12.98% |
| 15m | Down burst | 0.687 | 35.37% | 14.39% |

This is useful for anticipated market heat and execution risk. It does not determine settlement direction reliably enough on its own.

## 8. Continuous Forecasts

| Target | Result |
|---|---|
| 5m continuation distance | MAE $29.39 versus $27.58 median baseline; reject |
| 15m continuation distance | MAE $63.98 versus $59.72 baseline; reject |
| 5m retracement fraction | MAE 0.903 versus 0.841 baseline; reject |
| 15m retracement fraction | MAE 1.178 versus 1.174 baseline; reject |
| 5m future taker imbalance | R2 0.053, MAE 0.262 versus 0.271 baseline; weak |
| 15m future taker imbalance | R2 0.053, MAE 0.195 versus 0.201 baseline; weak |

Positive R2 did not make the path-distance forecasts useful: their absolute-error MAE is worse than predicting the training median.

## 9. Spot/Perpetual Flow Leadership

Large flow shocks use a threshold learned on the first 70%. Test events are spaced to avoid overlapping outcome windows.

| Proposed leader | Follower | Horizon | Follow rate | Follower own persistence | Incremental value |
|---|---|---:|---:|---:|---:|
| Perpetual CVD | Spot CVD | 1m | 55.83% | 56.64% | -0.81 pp |
| Perpetual CVD | Spot CVD | 3m | 56.83% | 57.81% | -0.98 pp |
| Perpetual CVD | Spot CVD | 5m | 57.20% | 58.24% | -1.04 pp |
| Spot CVD | Perpetual CVD | 1m | 52.47% | 54.51% | -2.04 pp |
| Spot CVD | Perpetual CVD | 3m | 53.30% | 54.71% | -1.40 pp |
| Spot CVD | Perpetual CVD | 5m | 53.38% | 54.49% | -1.12 pp |

The apparent lead-lag disappears under the proper own-flow control. Flow is persistent, but neither venue provides incremental leadership over the follower's current state.

## 10. Does Historical Flow Improve The Models?

Mostly no.

- First-touch side AUC improves only about 0.1-0.6 percentage points.
- Next-round round-trip AUC improves less than 0.1 percentage point.
- Flow-burst AUC is unchanged or slightly worse because candle/volume features already capture most activity.
- Post-touch continuation and retracement are generally unchanged or worse.
- The largest continuous improvement is 15m continuation R2 +0.015, but MAE remains worse than the median baseline.

Decision: do not enlarge the live ensemble with these 13 historical flow columns based on this evidence.

## Data That Still Cannot Be Tested Historically

- Coinbase/Bybit/OKX price leadership: no synchronized bulk ticker archive.
- Pyth/Chainlink settlement-oracle divergence: no historical tick archive.
- True liquidation cascades: no liquidation-event history.
- Passive fill and queue position: no order/trade matching.
- Polymarket quote lag, fair-value edge and executable exits: require the forward recorder.

Already concluded and intentionally not duplicated:

- Binance futures depth direction: seven cached days produced AUC 0.51-0.54.
- Generic candle-plus-flow settlement direction: previous tests found no useful lift.

## Recommended Use

Shadow only:

1. 5m first-touch side, with mutual-exclusion and abstention.
2. 5m/15m first-touch timing probabilities.
3. Next-round round-trip probability as strategy-mode context.
4. Aggressive-flow burst probability as market-heat context.

Do not deploy:

1. Post-touch continuation trade.
2. Exact retracement/continuation size.
3. Spot/perpetual leadership rule.
4. Historical-flow feature expansion.

## Artifacts

- `REPORT.md`
- `binary_metrics.csv`
- `binary_predictions.csv`
- `multiclass_metrics.csv`
- `regression_metrics.csv`
- `spot_perp_flow_propagation.csv`
- `round_dynamics.parquet`
- `touch_dynamics.parquet`
- `config.json`
- `run.log`

## Reproduction

```powershell
.\run_180d_path_dynamics.bat
```

The command uses existing historical files only and does not replace live models.
