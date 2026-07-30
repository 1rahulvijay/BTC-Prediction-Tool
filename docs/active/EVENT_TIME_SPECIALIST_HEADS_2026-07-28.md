# Event-Time Direction And Specialist Heads

Date: 2026-07-28

Status: **RESEARCH COMPLETE - REPEATABLE INFORMATION FOUND, NO TRADING PROMOTION**

Follow-up: the frozen accumulator test is documented in
`EVENT_EVIDENCE_ACCUMULATOR_RESULTS_2026-07-28.md`. It formed 1,188 independent
5m/15m candidates, but accumulated event direction did not improve the causal
anchor-distance/time baseline and is not eligible for promotion.

## Why This Experiment Exists

The earlier candle-based economic-policy campaigns established two different facts:

1. BTC movement intensity and profitable-event probability can be ranked.
2. Signed 5m/15m expected return remains negative or unstable after costs.

Adding more model families to the same one-minute matrix did not solve the second problem.
This experiment changes the information set and target instead:

- raw spot and perpetual trades are retained in event order;
- observations are aggregated to one second rather than one minute;
- the target is which relative price barrier is reached first;
- movement, two-sided path structure, and direction reliability are separate heads.

Nothing in this lane imports into serving code or writes to `data/saved_models`.

## Implementation

Standalone trainer:

`backend/research/train_event_time_specialists.py`

Convenience launcher:

`research\launchers\run_event_time_specialists.bat`

Outputs:

`data/research/event_time_specialists/<UTC_RUN_ID>/`

Each run writes:

- `metrics.csv`
- `locked_predictions.parquet`
- `direction_by_day.csv`
- `label_stats.json`
- `feature_manifest.json`
- `run_manifest.json`
- `RESULTS.md`
- `run.log`

## Historical Data

The repository contains 1,286 overlapping days of raw Binance spot and Binance USD-M
perpetual `aggTrades` files. The experiment reads one day at a time and releases the raw
arrays before loading the next venue/day, keeping memory bounded on a 16 GB laptop.

This source provides:

- venue trade timestamp;
- trade price and quantity;
- aggressive-buy/aggressive-sell classification;
- spot/perpetual flow divergence;
- spot/perpetual price and basis history.

It does **not** reconstruct:

- limit-order additions and cancellations;
- queue position;
- multi-level order-book depth;
- historical Coinbase/Bybit event-time lead/lag;
- executable bid/ask, fees, slippage, or fill probability.

Those omissions prevent any direct profitability or production claim.

## Causal Feature Set

The script builds 86 features using information available at or before the anchor:

| Group | Count | Examples |
|---|---:|---|
| Time context | 4 | hour and weekday sine/cosine |
| Current basis | 1 | perpetual-minus-spot basis in bps |
| Price/lead history | 24 | spot return, perp return, perp lead, basis change over 1/3/5/10/30/60s |
| Flow/activity history | 48 | signed flow, divergence, agreement, volume and trade intensity over 1/3/5/10/30/60s |
| Volatility/range | 9 | spot/perp RMS returns and spot range over 10/30/60s |

The self-test perturbs future prices and verifies that earlier feature rows remain unchanged.
Leading seconds without an observed trade are never backfilled from the future.

## Changed Targets

Targets are calculated from the sequence of future one-second spot prices:

| Horizon | Relative barrier |
|---:|---:|
| 5 seconds | +/-1.0 bps |
| 15 seconds | +/-1.5 bps |
| 30 seconds | +/-2.0 bps |
| 60 seconds | +/-3.0 bps |

Specialist heads:

1. **Direction**: upper barrier is reached before lower barrier, conditioned on a barrier
   being reached. Same-second ties are excluded.
2. **Movement**: either barrier is reached.
3. **Round-trip**: both barriers are reached.
4. **ACT/SKIP**: the frozen direction ensemble is both resolved and correct.

Direction is intentionally not defined as the sign of a future 5m/15m candle close.

## Models And Validation

Every base head trains four sequential model families:

- Logistic Regression
- HistGradientBoosting
- LightGBM
- CatBoost

Each model is freed before the next fit. Their calibrated probabilities are mean-ensembled.

Chronological split:

```text
60%  base-head training
10%  isotonic probability calibration
 5%  ACT/SKIP training (first half of the meta period)
 5%  ACT/SKIP calibration (second half of the meta period)
20%  locked historical test
```

Anchors are spaced by their target horizon. A 30-second target is evaluated every 30
seconds, so its forward label windows do not overlap. Labels cannot cross a split boundary.
The ACT threshold was frozen at `0.65` before reading locked-test outcomes.

## Executed Runs

### Era A: Recent

```text
Run              20260728T050036Z
Period           2026-06-25 through 2026-07-24
Raw seconds       2,592,000
Feature anchors   518,364
Runtime           143.7 seconds
```

### Era B: Non-Overlapping Replication

```text
Run              20260728T050314Z
Period           2026-05-26 through 2026-06-24
Raw seconds       2,592,000
Feature anchors   518,364
Runtime           161.2 seconds
```

## Locked-Test Results

### Mean-Ensemble AUC

| Horizon | Direction A | Direction B | Movement A | Movement B | Round-trip A | Round-trip B |
|---:|---:|---:|---:|---:|---:|---:|
| 5s | **0.7716** | **0.7702** | 0.7384 | 0.7610 | 0.9073 | 0.9360 |
| 15s | **0.6884** | **0.6924** | 0.7394 | 0.7585 | 0.8888 | 0.9125 |
| 30s | **0.6650** | **0.6473** | 0.7376 | 0.7656 | 0.8691 | 0.8927 |
| 60s | **0.6251** | **0.5997** | 0.7399 | 0.7549 | 0.8580 | 0.8597 |

The signed information is strongest at five seconds and decays as the target extends.
Movement predictability remains near 0.74-0.77 across the tested horizons.

Round-trip AUC is high, but the event is rare. Average precision is the relevant caution:

```text
Era A round-trip average precision: 0.034 / 0.083 / 0.115 / 0.140
Era B round-trip average precision: 0.100 / 0.226 / 0.281 / 0.302
```

The round-trip head ranks rare events but is not a high-recall action trigger.

### Direction Day Stability

Every locked-test day had direction AUC above 0.50:

| Horizon | Era A daily AUC range | Era B daily AUC range | Positive days |
|---:|---:|---:|---:|
| 5s | 0.7590-0.8031 | 0.7326-0.8111 | 12/12 |
| 15s | 0.6691-0.7189 | 0.6651-0.7438 | 12/12 |
| 30s | 0.6430-0.6981 | 0.6147-0.6836 | 12/12 |
| 60s | 0.6142-0.6414 | 0.5739-0.6422 | 12/12 |

This replication is the strongest evidence from the experiment. It shows repeatable
short-horizon signed information rather than a single lucky locked-test aggregate.

## ACT/SKIP Result

The calibrated `0.65` ACT threshold did not produce enough independent calls:

| Era | Horizon | ACT count | Coverage | Correct when ACT | Wilson lower bound |
|---|---:|---:|---:|---:|---:|
| A | 5s | 21 | 0.020% | 66.7% | 45.4% |
| A | 15s | 24 | 0.069% | 54.2% | 35.1% |
| A | 30s | 4 | 0.023% | 25.0% | 4.6% |
| A | 60s | 4 | 0.046% | 50.0% | 15.0% |
| B | 5s | 5 | 0.005% | 80.0% | 37.6% |
| B | 15s | 4 | 0.012% | 75.0% | 30.1% |
| B | 30s | 5 | 0.029% | 100.0% | 56.6% |
| B | 60s | 21 | 0.243% | 57.1% | 36.6% |

No row has enough sample size for promotion. The one lower bound above 50% contains only
five calls and is not evidence of a deployable filter.

## Correct Interpretation

What the test establishes:

- event-time trade flow contains repeatable signed information over 5-60 seconds;
- the first-barrier target is substantially more predictable than the old candle-close target;
- movement and two-sided path structure should remain separate from direction;
- the useful information decays rapidly and should not be extrapolated directly to 5m/15m.

What the test does not establish:

- 70% accuracy on all seconds: direction accuracy is conditional on a barrier resolving;
- a profitable BTC or Polymarket strategy;
- valid execution after spread, fees, latency, slippage, and fills;
- a production-ready ACT/SKIP rule;
- a direct 5m/15m close prediction.

## Proposed Serving Architecture

The evidence supports this future shadow-only path:

```text
synchronized event recorder
    -> one-second causal state
    -> P(first barrier UP)
    -> P(any movement)
    -> P(two-sided round-trip)
    -> calibrated direction-reliability head
    -> persistence across repeated 5s/15s forecasts
    -> 5m/15m shadow state
    -> separate economic/execution gate
    -> paper decision only
```

The 5m/15m layer must aggregate repeated short forecasts. A single 5-second forecast must
not be stretched into a 15-minute call.

## Requirements Before Live Shadow Wiring

1. Run `backend/venues/multi_venue_recorder.py` continuously. Its persistent database had
   zero rows at the time of this experiment, although a 25-second smoke test confirmed all
   nine expected streams were healthy.
2. Collect at least eight weeks of synchronized Binance/Coinbase/Bybit events and qualified
   five-minute episodes.
3. Add causal book features: OFI, microprice, queue imbalance, depth depletion, spread,
   additions/cancellations, and sequence-gap quality.
4. Replay the frozen heads on forward events without retuning thresholds.
5. Accumulate at least 500 independently resolved paper candidates.
6. Model executable spread, fees, latency, slippage, and fill probability.
7. Require positive post-cost mean, positive day-block lower bound, profit factor above the
   preregistered threshold, weekly stability, and latency/slippage stress survival.
8. Only then consider an atomic shadow artifact; production promotion remains a separate gate.

## Commands

Self-test:

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  backend\research\train_event_time_specialists.py --selftest
```

Latest 30 paired days:

```powershell
.\research\launchers\run_event_time_specialists.bat
```

Explicit non-overlapping era:

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  backend\research\train_event_time_specialists.py `
  --start 2026-05-26 --end 2026-06-24 --days 30 `
  --horizons 5 15 30 60 --threads 4
```

## Final Decision

```text
Event-time direction information       FOUND and replicated
Movement specialist                    FOUND and replicated
Round-trip ranker                      FOUND, rare-event caution required
Calibrated ACT/SKIP                    NOT promotable
Executable economic edge              NOT tested by available history
Serving artifacts changed             NO
Live model behavior changed            NO
Production promotion                   REFUSED
```

The next legitimate accuracy improvement is forward synchronized order-book evidence, not
another model family or another threshold search on the same historical trade-only matrix.
