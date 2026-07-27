# Quant Platform V1 Implementation Status

Date: 2026-07-27

Branch: `quant-platform-v1`

Base: `master` at `a00d613087b30f637926ba6cc2984f48d655b10d`

Mode: research, shadow, and paper only

Real Binance orders: unavailable

Real Polymarket orders: unavailable

## Objective

Build a trustworthy executable-edge research platform. A strategy is useful only
when causal, post-cost, capacity-aware forward evidence supports it. The system
must prefer `NO_TRADE` over unsupported confidence.

This implementation does not claim profitability. It adds infrastructure needed
to measure paper strategies honestly and keep venue economics isolated.

## What Changed

### Repository workflow

`AGENTS.md` establishes one integration branch:

```text
quant-platform-v1
```

It records the actual repository path and prohibits real-order activation,
unrelated retraining, force-pushes, branch sprawl, and evidence contamination.

The serving/evidence work was already merged into `master` before this branch was
created. The integration branch starts from that verified state instead of
replaying or resetting history.

### Shared quant-platform kernel

`backend/quant_platform/` now provides:

- immutable `MarketEvent` identity, source, session, sequence, exchange time,
  receive time, health, payload, and SHA-256;
- injectable system/manual clocks for live and replay parity;
- sequence-aware, stale-aware feed health;
- a thread-safe event bus with explicit handler failures;
- feature contracts with deterministic schema hashes and finite-value checks;
- immutable model-bundle and strategy registries;
- venue-neutral risk limits and auditable block reasons;
- conservative allocation from lower-bound expectancy, liquidity, calibration,
  drawdown, capacity, and correlation;
- Brier score and population stability diagnostics;
- an append-only DuckDB audit chain;
- fail-closed service orchestration and global kill state.

The kernel deliberately does not contain fee formulas, settlement rules, payoff
math, venue fill rules, or position accounting.

### Binance futures paper domain

`backend/binance_paper/` is an isolated USD-M linear-futures paper engine using:

```text
data/binance_paper.duckdb
```

Implemented accounting and execution behavior:

- signed one-way positions: positive `LONG`, negative `SHORT`;
- `BUY` walks asks;
- `SELL` walks bids;
- full and partial depth fills;
- maximum slippage cap;
- bps-of-filled-notional taker fees;
- reduce-only validation and position-size capping;
- gross realized PnL, fees, funding, cash, unrealized PnL, equity, initial
  margin, and available balance;
- conservative liquidation-price estimate;
- long funding debits and short funding credits for positive rates;
- immutable order IDs and exact idempotent retry;
- rejection on stale data, sequence failure, unknown position, unavailable
  model, kill switch, leverage, notional, loss, and correlated-exposure gates;
- atomic order plus position persistence;
- restart recovery;
- replay reconciliation of every fill and funding event;
- fail-closed unknown-position state after a persisted-state mismatch.

There is no import or use of the Polymarket 0-1 contract fee function.

There is no authenticated Binance client in this package.

The service is disabled by default:

```text
BTC_BINANCE_PAPER_ENABLED=0
```

Setting this to `1` only enables the paper engine. It cannot create a real
exchange order.

### Research validation

The shared validation layer now supports:

- immutable experiment protocols with strategy, hypothesis, instrument,
  feature schema, period, entry/exit rules, parameters, number of tried
  configurations, costs, latency, gates, code hash, and dataset hash;
- purged expanding walk-forward splits with an embargo;
- deterministic day/week block bootstrap intervals;
- CSCV-style probability-of-backtest-overfitting diagnostics;
- Deflated Sharpe diagnostics that account for the number and dispersion of
  tried alternatives;
- positive-profit concentration;
- frozen promotion gates covering forward sample size, weeks, trading days,
  post-cost expectancy, block lower bound, profit factor, fee/slippage/latency
  stress, weekly stability, day/regime concentration, drawdown, PBO, Deflated
  Sharpe support, and paper/live divergence.

A gate pass produces:

```text
ELIGIBLE_FOR_LIVE_REVIEW
```

It does not enable live trading. Any failed gate produces:

```text
PAPER_ONLY
```

### Operational surfaces

New read-only endpoints:

```text
GET /api/binance-paper/status
GET /api/system-health
```

`GET /api/runtime-status` now includes both payloads.

The frontend adds:

- `Binance Paper`: position, equity, available balance, reconciliation, ledger
  counts, fills, fees, and rejection reasons;
- `System Health`: Binance trade/depth/kline freshness, Coinbase freshness,
  Pyth freshness, own L2 recorder age, database write access, backend code
  identity, and live-execution availability.

The global trust state is `DO_NOT_TRUST` when a required feed is missing/stale
or running backend code differs from disk.

The frontend explicitly says strategy order generation is not wired. It does
not imply that an empty ledger is active trading.

## Requirement Reconciliation

### Complete

- Canonical integration branch and repository rules.
- Previously merged Polymarket Ledger V2 eligibility and independently
  reconstructed own-L2 outcomes.
- Previously merged restart-safe promoted bundle identity and threshold freeze.
- Previously merged recorder database plus WAL provenance.
- Previously merged required evidence-run selection and mixed-run refusal.
- Previously merged persistent failed-write spool and replay.
- Shared venue-neutral kernel.
- Isolated Binance paper execution and accounting domain.
- Reusable research-validation and promotion-gate library.
- Read-only Binance paper and system-health UI surfaces.
- Deterministic tests registered in Linux and Windows CI and launcher preflight.

### Partial

- Existing live collectors do not yet publish every message as `MarketEvent`.
  Their current recorder schemas remain unchanged to protect active evidence.
- Complete Trade Forecast implements settlement, entry completeness, executable
  entry costs/capacity, ever-profitable, lockable, barrier order, time to first
  profit, MFE/MAE, share path, exit, and plan-PnL distribution heads. The new
  shared registries do not replace its verified bundle/evidence contract.
- Existing strategy research includes liquidation, flow, lead-lag, volatility,
  absorption, mean-reversion, stopping, and Polymarket path probes, but not
  every historical experiment uses the new immutable protocol class.
- Main model, specialist heads, regime detection, and existing meta models
  remain separate from the new allocator. They are not automatically promoted
  into a portfolio.
- The UI has the new paper and health views, but a consolidated Research/Risk
  control room is still a future product phase.

### Not Implemented

- Binance strategy-to-order generation.
- Synchronized Binance paper collection using the canonical event bus.
- Trained Binance liquidation-continuation and liquidation-exhaustion heads.
- Trained cross-venue executable lead-lag head.
- Trained basis/funding/open-interest state head.
- Trained Binance volatility-expansion and absorption strategy heads.
- An out-of-fold ACT/SKIP model built from resolved paper candidates.
- A promoted portfolio allocation policy.
- Authenticated Binance or Polymarket execution adapters.
- Real-order reconciliation, cancel/replace, credential storage, or live
  exchange kill switches.
- The required 500-trade, 8-12-week forward evidence for any new strategy.

These items are not coding omissions to hide. Several require continuous,
sequence-valid, executable market data and resolved paper outcomes before a
model can be trained or promoted honestly.

## Safety State

The new Binance paper engine starts with its kill switch active.

The shared orchestrator also starts fail-closed.

The new UI and APIs are read-only.

No live-execution package exists under `backend/execution_live/`.

No browser code contains private exchange credentials.

## Validation

Focused deterministic commands:

```powershell
python -m backend.quant_platform.test_kernel
python -m backend.binance_paper.test_engine
python -m backend.quant_platform.test_research_validation
python -m py_compile backend\server.py
npm.cmd run build
set BTC_VALIDATE_STARTUP=1
call start.bat
```

The optional multi-gigabyte local Complete Trade pilot fixture is now explicit:

```powershell
set BTC_TEST_COMPLETE_TRADE_PILOT=1
python -m backend.trade_forecast.test_complete_trade_forecast
```

Clean CI does not contain ignored `data/` fixtures. Before this correction, CI
silently skipped that branch while any developer with an old fixture failed the
same invariant suite. With the flag set, a missing or stale fixture fails closed.

The browser build was checked for the two new tabs and their DOM views with the
backend stopped. The expected degraded behavior is a visible unavailable state,
not fake data.

The full existing repository invariant matrix must remain green before this
branch is proposed for merge.

## Next Valid Sequence

1. Run the complete deterministic regression matrix.
2. Push `quant-platform-v1`.
3. Let CI run; inspect logs rather than relying only on a badge.
4. Deploy the existing recorders without altering their frozen evidence schema.
5. Add a canonical-event adapter beside each recorder and compare counts,
   sequence gaps, timestamps, and hashes in shadow.
6. Preregister one Binance mechanism, not a large indicator family.
7. Wire only that strategy to the paper engine.
8. Collect resolved fills and outcomes.
9. Evaluate with the frozen validation and promotion gates.
10. Keep the result `PAPER_ONLY` unless every predeclared forward gate passes.

## Merge Rule

Do not merge this branch into `master` merely because unit tests pass.

The engineering package can be reviewed and merged after CI and integration
tests are green. Strategy profitability remains a separate forward-evidence
question after merge.
