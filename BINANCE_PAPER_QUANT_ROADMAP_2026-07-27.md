# Binance Paper Quant Roadmap

Date: 2026-07-27

## Objective

The objective is not guaranteed monthly profit. The measurable objective is:

> Positive after-cost expectancy, controlled drawdown, repeatability across
> regimes, and survival when latency, fees and slippage are stressed.

The economic decision is:

```text
trade edge
  = P(fill) * expected PnL conditional on fill
  - entry fee
  - exit fee
  - slippage
  - funding
  - adverse-selection cost
  - uncertainty buffer
```

No strategy may be promoted because its raw direction accuracy or point
estimate is attractive.

## Current Implemented State

Phase 1 is implemented on the canonical `master` branch.

- Public BTCUSDT USD-M perpetual market data only.
- The existing Binance futures WebSocket carries `bookTicker`, `aggTrade` and
  liquidation events. The paper service does not open another socket.
- Exactly two transparent baselines: Trend Following and Breakout.
- Breakout fails closed when perpetual `aggTrade` intensity is unavailable.
- Independent paper accounts, one-way LONG/SHORT positions and isolated PnL.
- Executable bid/ask fills with latency, visible top size, adverse slippage and
  Binance-specific fees.
- Signed funding cash flows are applied from observed settled public funding
  events. They are position-linked and idempotent.
- Transactional state changes, deterministic signal/order IDs and restart
  recovery.
- Candidate expiry plus side-aware executable entry-price limits, checked again
  after latency.
- A validated paper order-state machine and aggregate capital-preservation
  governor.
- Baseline uncertainty is explicit: confidence is uncalibrated and net EV is
  unavailable until a tested model supplies it.
- Hard default-off environment gate and no authenticated Binance code.
- Day-block evidence metrics and a separate, stricter promotion diagnostic.

Production activation is intentionally two-step:

```text
BTC_ENABLE_BINANCE_PAPER=1 before backend launch
then Start paper engine in the UI
```

This only starts simulated orders. The repository contains no authenticated
Binance order client.

The current strategies are research baselines. They have not established an
after-cost edge.

## Current Execution Limits

- Only top-of-book liquidity is available to the in-process paper engine.
- A full requested quantity is required; insufficient visible size is rejected.
- The simulator cannot estimate queue position.
- Funding uses the latest settled funding event observed by the existing public
  REST poller. Events missed while the application is offline are not
  backfilled.
- There is no one-second alternate fill path for latency-stress scoring.
- There is no exchange/API acknowledgement or cancel-delay simulation.
- There is no liquidation or bankruptcy engine.
- Paper results cannot prove live fill quality.
- No authenticated exchange acknowledgement/reconciliation state exists.
- The current baselines do not have a trained net-EV or dynamic-exit head.

These limitations must remain visible in evaluation and promotion decisions.

## Phase 2: Three Independent Mechanisms

Do not add correlated EMA variants. Each mechanism needs a preregistered
hypothesis, protocol hash, dataset hash, frozen costs and independent result.

### 1. Liquidation Continuation And Exhaustion

Build two separate hypotheses:

- Continuation: forced liquidation, aggressive imbalance, book depletion and
  volatility expansion predict a short-lived continuation after latency.
- Exhaustion: forced liquidation, stalled impact, opposite-side replenishment
  and weakening aggressive flow predict reversal after latency.

The models must not share one target. Compare executable entry and exit prices,
not candle direction.

### 2. Basis, Funding And Open-Interest State

Classify at least:

- Price up, OI up, basis expanding: leveraged long expansion.
- Price up, OI down: short covering.
- Price down, OI up: leveraged short expansion.
- Price down, OI down: long liquidation.

Inputs require timestamped basis, funding, OI, price and liquidation data with
freshness checks. Missing derivatives data means no decision.

### 3. Cross-Venue Lead-Lag

Synchronize Binance perpetual, Binance spot, Coinbase spot and Bybit perpetual.
Test:

```text
leader event
  -> first executable quote on the lagging venue after simulated latency
  -> after-cost exit distribution
```

Price correlation alone is not evidence of executable lead-lag.

## Phase 3: Complete Trade Forecast

Create separate targets rather than one large direction target:

- Fill probability and fill ratio.
- Entry VWAP.
- Adverse move after 100, 250, 500 and 1,000 milliseconds.
- MFE and MAE quantiles.
- Target-before-stop and stop-before-target probabilities.
- Probability neither barrier is reached.
- Time to target, stop and first profitability.
- Exit VWAP and holding duration.
- Net-PnL 10th, 50th and 90th percentiles.
- Probability net PnL is positive.
- Probability edge survives fee, slippage and latency stress.
- Probability the active regime changes before exit.

Every prediction must store expected versus simulated outcome and its error.
Start with logistic regression, gradient boosting, quantile regression and
survival models. Sequence models are challengers only after tabular incremental
value is proven. Reinforcement learning remains out of scope.

## Phase 4: ACT/SKIP Meta-Model

The meta-model target is:

```text
P(net PnL > 0 | proposed trade, regime, execution state)
```

Candidate inputs:

- Strategy ID, score and cross-strategy agreement.
- Spread, quote age, depth, expected fill and slippage.
- Volatility, funding, basis, OI and liquidation state.
- Regime and time of day.
- Recent calibration, expectancy and adverse-selection error.

Allowed outputs:

- `ACT`
- `SKIP_LOW_EDGE`
- `SKIP_BAD_LIQUIDITY`
- `SKIP_REGIME_MISMATCH`
- `SKIP_MODEL_DEGRADED`
- `SKIP_EXCESS_CORRELATION`

Training must use purged out-of-fold base-strategy predictions. In-sample
strategy predictions are prohibited.

## Frozen Promotion Contract

The paper metrics expose what is measurable now and mark unavailable checks as
unmeasured. A real-capital build remains prohibited.

Minimum research gates:

```text
independent forward trades             >= 500
forward observation                    >= 56 days
observed trading days                  >= 30
after-cost expectancy                  > 0
day-block 95% lower bound              > 0
profit factor                          > 1.20
positive with fees +50%                required
positive with slippage +50%            required
positive under one-second latency      required
positive weeks                         majority
single-day profit concentration        < 20%
single-regime profit concentration     < 50%
deflated/probabilistic Sharpe          supports skill
backtest-overfit probability           acceptable
```

An unavailable or failed gate blocks promotion. Gates must not be lowered after
results are observed.

## Validation And Governance

Each research protocol must record:

- Hypothesis and causal mechanism.
- Features and target definitions.
- Parameters and number of tried variants.
- Entry, exit and cost assumptions.
- Train, embargo and test periods.
- Protocol, code and dataset hashes.
- Purged walk-forward results.
- Day/week-block uncertainty.
- Matched-random, shuffled, time-shift and latency controls.
- Fee, slippage and venue stress.
- Regime and calendar-period decomposition.

Champion models remain frozen. Challengers run in shadow and may replace a
champion only after the complete forward gate passes.

## Next Build Order

1. Accumulate synchronized perpetual book, trade, liquidation, OI, basis and
   cross-venue observations with sequence and freshness integrity.
2. Add a multi-level depth recorder and deterministic replay; do not fabricate
   depth in the live service.
3. Preregister and test the three Phase-2 mechanisms independently.
4. Add complete-trade labels and distributional baselines.
5. Add latency, fee and slippage stress replays.
6. Train the ACT/SKIP meta-model only from purged out-of-fold predictions.
7. Continue paper-only forward collection until every frozen gate is measured.

Real Binance order submission, API keys, signing and automatic live deployment
are intentionally absent.
