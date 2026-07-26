# Complete Trade Forecaster V1

Date: 2026-07-26  
Mode: `SHADOW_PILOT_ONLY`  
Production Champion: unchanged  
Real orders: disabled

## Purpose

`COMPLETE_TRADE_FORECAST_V1` estimates a distribution for a complete executable
Polymarket trade instead of claiming one exact BTC price or guaranteed profit.
For both `BUY UP` and `BUY DOWN`, it evaluates:

- the post-latency ask-ladder entry VWAP;
- future BTC and executable contract-price paths;
- break-even, target and invalidation bids after both taker fees;
- fill probability and size capacity;
- frozen causal exit plans;
- expected PnL, five PnL quantiles, profit probability and worst-tail loss;
- `NO_TRADE` as the mandatory control.

This lane cannot place a trade and does not feed any result into the existing
Champion, direction, tier, advice, or paper strategy.

## Evidence State

The current L2 snapshot contains fewer than the frozen evidence requirements.
The small real-data validation slice used for fast end-to-end checks contains:

```text
110 independent matched rounds
1 calendar week
6,972 side/checkpoint/quantity rows
5m and 15m
quantities 1 / 5 / 10 / 25 / 50 / 100
```

The full stable snapshot was then rebuilt and trained at the production artifact
paths:

```text
395 independent matched rounds
1 calendar week
24,996 side/checkpoint/quantity rows
5m and 15m
quantities 1 / 5 / 10 / 25 / 50 / 100
share-path artifact: valid, HGB pilot
BTC-path artifact: valid, HGB pilot
execution artifact: valid, HGB pilot
all serving statuses: PILOT_ESTIMATE_NOT_ACTIONABLE
```

This is enough to validate mechanics, training, artifact integrity and serving.
It is not enough to approve a trading rule because the sample still spans only
one calendar week. Promotion still requires at least:

```text
500 independent rounds
8 calendar weeks
positive Q5 day-block lower-bound EV
Q5 minus Q3 EV >= 0.5c/share
broad bucket monotonicity
positive Q5 EV in every test week
positive Q5 EV under 1,000ms latency
Q5 beats hold-to-settlement
profit factor >= 1.20
stability across volatility regimes
no single UTC hour above 50% of Q5
valid source, dataset, policy, feature and artifact hashes
```

The app therefore displays `PILOT ONLY` and returns `NO_TRADE`.

## Canonical Snapshot

At frozen checkpoints for 5m and 15m rounds, the dataset creates one immutable
row for each side and quantity. Features are taken from the last synchronized
book at or before the decision. Entry uses the first book at or after:

```text
decision timestamp + 500ms
```

The snapshot contains:

- exact round, side, quantity, horizon and seconds remaining;
- Pyth/BTC price, anchor, side-relative distance and recent BTC returns;
- P(Hold), volatility, contract velocity and BTC/share sensitivity;
- both contracts' bid, ask, spread, top size and full visible depth;
- quote age, book hash provenance, feature hash and model hash.

Malformed, stale, crossed, unsorted, empty, or round-mismatched ladders fail
closed.

## Executable Labels

Entry:

```text
first synchronized book after 500ms
walk ASK ladder for requested quantity
record partial fill honestly
charge rounded taker fee
```

Exit:

```text
walk BID ladder for the complete filled quantity
skip books without full exit depth
charge rounded taker fee
use official settlement for hold outcomes
```

The canonical fee helper is now:

```text
backend/polymarket_fee.py
fee = round(0.07 * price * (1 - price), 5)
```

Champion, recorder, labels, optimizer and resolver use the same function.

The dataset records:

- entry VWAP, fee, latency, slippage, fill fraction and capacity;
- future executable bid and ask VWAP at 5/10/15/30/60/120 seconds;
- ever-profitable, stays-profitable, full-size lockable and settlement labels;
- cumulative break-even, +3c and -3c crossings by future offset;
- first-profitable time, MFE, MAE and first target/stop;
- all frozen causal-plan outcomes;
- a 1,000ms latency-stress outcome for M0.

No midpoint, decision-book fill, partial-fill promotion, or historical best exit
is permitted.

## Models

### Share-Price Path

`train_share_path_model.py` predicts:

- executable future bid quantiles q10/q25/q50/q75/q90;
- executable future ask quantiles q10/q50/q90;
- MFE, MAE and first-profitable-time quantiles;
- calibrated complete-trade event probabilities;
- calibrated cumulative crossing probabilities by future offset.

Bid and ask targets use:

```text
logit(future executable price) - logit(current executable price)
```

### BTC Path

`train_btc_path_model.py` predicts:

- BTC q10/q25/q50/q75/q90 at every future offset and settlement;
- BTC MFE, MAE and first-event time;
- competing risks: upper barrier, lower barrier, anchor first, or none.

The BTC head uses one canonical UP/one-share row per checkpoint so side and
quantity duplicates do not create pseudo-sample size.

### Execution

`train_execution_heads.py` predicts:

- entry arrival slippage q50/q80/q95;
- maximum executable quantity q50/q80/q95;
- full-fill probability;
- post-latency quote-survival probability.

### Laptop-Safe Family Selection

Each target tries HGB, LightGBM and CatBoost sequentially. The best family is
selected on the calibration partition, only that model is retained, and final
metrics are computed on the untouched chronological test partition. This avoids
holding hundreds of losing model objects in 16 GB RAM and avoids choosing a
family on the final test set.

## Temporal Validation

Every horizon is split independently by round start:

```text
70% train
15% model-family selection and calibration
15% untouched test
15-minute purge between partitions
```

All rows from one round remain in one partition. Classifier family selection
uses the first half of calibration; isotonic calibration uses the later half
when at least 100 eligible rows and both classes exist.

Every artifact records and verifies:

- exact dataset SHA-256;
- source paths and source SHA-256 values;
- min/max data span and independent-round count;
- policy and feature-schema hashes;
- trainer, serving, label, fee and shared-model code hash;
- artifact SHA-256.

Serving rejects missing or changed data, code, policy, features, manifest or
artifact bytes.

## Frozen Plans And Optimizer

The scenario engine evaluates only:

```text
HOLD_TO_SETTLEMENT
TAKE_1C
TAKE_3C
TAKE_5C
TAKE_3C_OR_STOP_3C
TIME_EXIT_15S
TIME_EXIT_30S
TIME_EXIT_60S
BREAK_EVEN_LOCK_AFTER_3C
```

The break-even-lock plan arms after +3c and can only trigger on a later
executable observation. It no longer duplicates `TAKE_3C`.

The optimizer calculates expected PnL, q10/q25/q50/q75/q90, P(profit), CVaR,
profit factor, expected holding time and maximum model-safe entry ask.
It chooses `BUY_UP`, `BUY_DOWN`, or `NO_TRADE`.

A candidate is blocked unless:

```text
expected PnL > 0
q10 PnL > 0
P(profit) >= 60%
P(full fill) >= 70%
median predicted capacity covers requested quantity
data is fresh and healthy
all evidence and M0 gates pass
robust utility beats NO_TRADE
```

## Persistence

Dedicated database:

```text
data/complete_trade_forecast.duckdb
```

Tables:

- `complete_trade_forecasts`
- `complete_trade_path_predictions`
- `complete_trade_checkpoints`
- `complete_trade_outcomes`

At frozen checkpoints, live logging records both sides and all six quantities.
The resolver attaches immutable entry, path, exit, fee, holding-time and official
settlement outcomes. This database is separate from recorder-owned
`execution_layer.duckdb`, preventing DuckDB writer contention.

## UI

Each live Polymarket round now includes a distinct Complete Trade Forecast
section showing:

- current full-size ask and fillability;
- predicted post-latency entry;
- break-even and +3c target bids;
- P(ever profitable), P(stays profitable), lock and target probabilities;
- expected MFE/MAE and first-profitable time;
- plan EV, q10, P(profit), expected hold, profit factor and safe-entry ceiling;
- exact tested capacity;
- BTC 60-second q10-q90 range;
- explicit `PILOT ONLY`, `NO_TRADE`, and `Champion unchanged` language.

## Commands

Run the complete research lane only when the recorder/export files are closed
and stable:

```bat
run_complete_trade_forecast_research.bat
```

The runner performs dataset build, the three trainers, outcome resolution and
the final integrity report sequentially. It does not start or stop the app.

Report only:

```bat
C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe -m backend.trade_forecast.report_complete_trade_forecast
```

## Validation Completed

```text
full backend compileall: PASS
focused Pyflakes on changed runtime files: PASS
Vite production build: PASS
git diff --check: PASS
paper-trading integrity: PASS
collector integrity: PASS, 26 checks
executable fill engine: PASS, 640 randomized barrier comparisons
canonical fee self-test: PASS
schema/label/scenario/optimizer/logger/serving self-tests: PASS
real L2 dataset mechanics: PASS
partial-fill and quantity-capacity behavior: PASS
artifact fail-closed checks: PASS
full pilot dataset: PASS, 24,996 rows / 395 rounds / 1 week
full share/BTC/execution artifact training: PASS
full production-path serving smoke test: PASS
all full-pilot artifacts remain non-actionable: PASS
```

## Honest Boundary

This implementation makes the experiment executable and auditable. It does not
prove a profitable edge. The available sample is below the evidence gate, exact
joint future paths are approximated from marginal quantiles, passive queue fills
are not claimed, and post-entry live actions remain shadow records until a
causal entry is actually logged. No real-money promotion is justified.
