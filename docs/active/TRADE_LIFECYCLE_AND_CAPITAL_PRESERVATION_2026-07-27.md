# Trade Lifecycle And Capital Preservation

Date: 2026-07-27

Canonical branch: `master`

Mode: research, shadow, and paper only

Real Binance orders: unavailable

Real Polymarket orders: unavailable

## Objective

The target is not to predict every price correctly. The target is to accept
only trades whose expected value remains positive after execution costs and
uncertainty, then preserve capital when data, state, or evidence is unreliable.

The intended lifecycle is:

```text
market state
  -> meaningful-move probability
  -> conditional direction
  -> entry and fill conditions
  -> path and barrier distribution
  -> exit plan
  -> after-cost PnL distribution
  -> ACT or SKIP
  -> risk and capital-preservation gates
  -> paper execution
  -> immutable outcome evidence
```

This document reconciles that architecture with executable code. It does not
claim that any strategy is profitable or authorize real-money trading.

## Implemented In The Complete-Trade Research Lane

`backend/trade_forecast/` already separates the forecast into specialist
outputs instead of one exact-price promise:

- executable entry completeness and entry cost;
- BTC and share-path quantiles;
- target-before-stop, stop-before-target, and neither-barrier outcomes;
- time to first profitable exit;
- maximum favorable and adverse excursion;
- exit opportunity and plan holding duration;
- net-PnL distribution, positive-PnL probability, capacity q10, and safe entry;
- causal trade-plan optimization with fee, slippage, latency, and capacity
  constraints;
- conformal/quantile uncertainty and immutable serving manifests;
- forward-only evidence classes and frozen promotion thresholds.

These heads are research and shadow outputs until their frozen forward gates
pass. A point forecast is never treated as guaranteed.

## Implemented In The Binance Paper Service

The production paper-service path in `backend/binance_paper/` now enforces:

### Candidate lifecycle

- every entry signal has a finite validity window;
- a LONG has a maximum allowed executable entry price;
- a SHORT has a minimum allowed executable entry price;
- the limit includes configured adverse slippage;
- expiry and price bounds are checked when the signal is created and again
  after simulated latency, before any fill is recorded;
- an expired or economically stale candidate is cancelled with an explicit
  reason.

This closes the gap where a valid decision could previously fill after the
market had moved enough to invalidate its original economics.

### Truthful uncertainty

Every paper decision stores:

- whether its probability is calibrated;
- an explicit uncertainty status;
- expected net PnL when a model supplies it;
- a lower-confidence expected net PnL when available.

The current Trend and Breakout baselines intentionally report:

```text
probability_calibrated = false
uncertainty_status = UNMEASURED
expected_net_pnl = unavailable
```

The UI labels their confidence as an uncalibrated research score. It does not
present that score as a proven win probability.

### Order-state integrity

Paper order events use a validated state machine:

```text
NONE -> PENDING -> FILLED
                -> REJECTED
                -> CANCELLED
                -> CANCELLED_RECOVERY

NONE -> RISK_BLOCKED
NONE -> REJECTED
```

Terminal states cannot return to `PENDING`. Event timestamps are monotonic per
order, and recovery cancels only orders with no terminal event. Fill, order,
position, and account mutations remain one DuckDB transaction.

This is a simulator state machine. It is not an exchange acknowledgement state
machine because the repository has no authenticated order adapter.

### Two risk layers

Layer 1 remains the per-strategy entry risk engine:

- engine and strategy enablement;
- fresh required data;
- stop and target validity;
- side permission;
- leverage, notional, exposure, liquidity, and cash;
- daily and weekly loss;
- drawdown, cooldown, trade count, spread, and duplicate signal.

Layer 2 is the new aggregate capital-preservation governor:

```text
NORMAL
REDUCED_SIZE
NO_NEW_ENTRIES
CLOSE_ONLY
EMERGENCY_FLATTEN
```

It evaluates aggregate equity/drawdown, daily and weekly realized loss,
feed health, overdue pending state, persistence integrity, and finite account
state. Its behavior is deterministic:

- half of a capital limit consumed: size new entries at 50%;
- a capital limit reached: block entries and permit closes only;
- a severe limit breach or invalid account state: cancel pending entries and
  request paper-only emergency flattening at a healthy executable quote;
- stale/missing feed, overdue pending state, or unknown persistence integrity:
  block new entries;
- missing or non-finite safety inputs fail closed.

The governor is rechecked immediately before a pending entry fills. Exits remain
available while entries are blocked.

### Exit hierarchy

Implemented exits are:

1. mandatory stop;
2. mandatory take-profit;
3. maximum holding time;
4. opposing strategy signal, with close-before-reverse ordering;
5. capital-governor emergency flatten;
6. confirmed manual close.

An ML dynamic-exit head is not wired. The repository's frozen conditional
stopping experiment did not beat holding after causal execution, so adding one
would manufacture unsupported complexity.

## UI And API

`GET /api/binance-paper/status` now includes `capital_governor`.

The Binance Paper tab shows:

- governor mode;
- whether new entries are allowed;
- active size multiplier;
- portfolio drawdown;
- daily and weekly loss-limit consumption;
- exact block/degradation reasons;
- calibrated versus uncalibrated decision status;
- whether net EV is modelled.

The screen remains explicitly marked `PAPER ONLY` and
`REAL ORDERS DISABLED`.

## Adversarial Tests Added

The deterministic Binance paper selftest now proves:

- schema v1 upgrades to lifecycle-aware schema v3;
- all five governor modes;
- stale feeds block entries;
- non-finite account state fails closed;
- overdue pending state blocks entries;
- expired candidates cannot fill;
- adverse price movement during latency cancels the candidate;
- terminal order state cannot return to pending;
- candidate and uncertainty metadata persist in DuckDB;
- the API returns the governor contract.

Existing tests still prove accounting signs, fees, adverse slippage, funding,
latency causality, liquidity rejection, reversal ordering, pause/disable
cancellation, restart recovery, atomic rollback, and evidence gates.

## Requirement Reconciliation

| Requested capability | Status | Exact boundary |
|---|---|---|
| Full forecast lifecycle | Partial/implemented by specialist lanes | Complete Trade Forecast covers distributions; not every paper baseline consumes every head |
| Calibration/conformal uncertainty | Implemented in research serving | Baseline paper scores remain explicitly uncalibrated |
| Candidate expiry and max entry | Implemented | Enforced before and after latency |
| Dynamic maker/taker/skip | Evidence-gated | Top-of-book has no queue model; taker paper fills only |
| Dynamic exit model | Rejected pending new evidence | Frozen causal stopping research did not beat hold |
| Paper order state machine | Implemented | No authenticated exchange ACK/UNKNOWN states |
| Strategy risk engine | Implemented | Per-strategy |
| Capital-preservation governor | Implemented | Aggregate paper portfolio |
| Emergency flatten | Implemented for paper | Requires a healthy executable quote |
| Chaos/adversarial tests | Implemented for deterministic failure modes | Network partition and exchange ACK chaos require an exchange adapter |
| Market replay digital twin | Partial | Multiple deterministic replay/test lanes exist; no complete live-exchange twin |
| Falsification and champion/challenger | Implemented | Frozen gates, nulls, PBO/Deflated Sharpe, immutable promotion |
| Disagreement safety | Implemented in model/champion lanes | The two transparent paper baselines remain independent |
| Distinct strategy mechanisms | Research-gated | No new strategy is promoted without post-cost forward evidence |
| Polymarket entry/exit economics | Implemented in its isolated domain | Never shares Binance fee/fill assumptions |
| ACT/SKIP meta-model | Not trainable yet | Requires at least 500 resolved, purged OOF paper candidates |
| Authenticated reconciliation | Deliberately unavailable | No API keys, signing, live orders, or exchange-user stream |
| Real-money deployment | Deliberately unavailable | Forward promotion gates are not satisfied |

## What Must Happen Next

1. Collect at least 500 independent paper trades across at least 8 weeks.
2. Measure candidate-to-fill expiry, entry-limit cancellations, slippage,
   adverse selection, and results by governor mode.
3. Train ACT/SKIP only from purged out-of-fold candidate predictions and
   resolved after-cost outcomes.
4. Compare the policy against no-model, shuffled, time-shifted, and
   ask/spread-matched controls.
5. Keep any new policy in shadow until the predeclared forward gate passes.
6. Do not add authenticated execution until a separate exchange-truth state
   machine, user-data reconciliation, and paper/live divergence gate exist.

No amount of code can replace those observations.
