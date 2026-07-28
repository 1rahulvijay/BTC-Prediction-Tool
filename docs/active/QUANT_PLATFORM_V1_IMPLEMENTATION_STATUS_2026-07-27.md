# Quant Platform V1 Implementation Status

Date: 2026-07-27

Canonical branch: `master`

Consolidation date: 2026-07-27

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

`AGENTS.md` establishes one maintained branch:

```text
master
```

It records the actual repository path and prohibits unrequested branch sprawl,
real-order activation, unrelated retraining, force-pushes, and evidence
contamination. The former integration and paper-service branches were merged
without rewriting history, validated together, and retired.

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

The production paper service implements:

- one reused public Binance futures socket carrying `bookTicker`, `aggTrade`
  and liquidations;
- exactly two transparent baselines: Trend Following and Breakout;
- independent per-strategy accounts and one-way positions;
- post-latency executable top-of-book fills, visible-size full-fill
  requirements, adverse slippage, fees, and settled funding;
- stop, take-profit, maximum-hold, opposing-signal reversal, pause, and manual
  close behavior;
- transactional persistence, deterministic signal/order identity, restart
  recovery, idempotent funding, and day-block paper metrics;
- finite candidate validity, LONG maximum-entry and SHORT minimum-entry limits,
  revalidated after simulated latency;
- a validated paper order-event state machine with terminal-state protection;
- truthful uncertainty/economics metadata: the current baseline confidence is
  explicitly uncalibrated and net EV is explicitly unavailable;
- an aggregate capital-preservation governor with `NORMAL`, `REDUCED_SIZE`,
  `NO_NEW_ENTRIES`, `CLOSE_ONLY`, and `EMERGENCY_FLATTEN` modes;
- UI/API inspection of strategies, accounts, positions, orders, fills, trades,
  funding, equity, events, and promotion diagnostics.

The separate low-level accounting harness also verifies:

- signed one-way positions: positive `LONG`, negative `SHORT`;
- `BUY` walks asks;
- `SELL` walks bids;
- full and partial depth fills;
- maximum slippage cap;
- bps-of-filled-notional taker fees;
- reduce-only validation and position-size capping;
- gross realized PnL, fees, funding, cash, unrealized PnL, equity, initial
  margin, and available balance;
- per-fill realized PnL persisted separately from cumulative position PnL;
- rolling 24-hour and 7-day net PnL, including fees, funding, and current
  unrealized PnL, supplied to the daily/weekly loss gates;
- position leverage persisted and replayed, with conservative leverage on
  same-side position increases;
- conservative liquidation-price estimate;
- long funding debits and short funding credits for positive rates;
- immutable order IDs and exact idempotent retry;
- immutable funding IDs: exact retries are ignored and payload collisions fail;
- rejection on stale data, sequence failure, unknown position, unavailable
  model, kill switch, leverage, notional, loss, and correlated-exposure gates;
- atomic order plus position persistence;
- restart recovery;
- replay reconciliation of every fill and funding event;
- fail-closed unknown-position state after a persisted-state mismatch.

There is no import or use of the Polymarket 0-1 contract fee function.

There is no authenticated Binance client in this package.

The production service is disabled by default:

```text
BTC_ENABLE_BINANCE_PAPER=0
```

Setting this to `1` before backend launch only unlocks the UI's paper-engine
start control. Starting the engine generates simulated strategy orders only. It
cannot create a real exchange order.

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

The paper API includes read endpoints:

```text
GET /api/binance-paper/status
GET /api/binance-paper/market
GET /api/binance-paper/strategies
GET /api/binance-paper/accounts
GET /api/binance-paper/positions
GET /api/binance-paper/orders
GET /api/binance-paper/fills
GET /api/binance-paper/funding
GET /api/binance-paper/trades
GET /api/binance-paper/metrics
GET /api/binance-paper/equity
GET /api/binance-paper/events
GET /api/system-health
```

Paper-only controls start/pause the simulator, update a baseline, and close
simulated positions. They cannot reach an exchange:

```text
POST /api/binance-paper/start
POST /api/binance-paper/pause
POST /api/binance-paper/positions/{id}/close
POST /api/binance-paper/close-all
PATCH /api/binance-paper/strategies/{id}
```

`GET /api/runtime-status` now includes both payloads.

The frontend adds:

- `Binance Paper`: engine/feed state, strategy accounts and decisions, risk and
  evidence metrics, positions, orders, executable fills, fees, funding, trades,
  equity, rejection reasons, capital-governor state/causes, calibrated versus
  uncalibrated scores, net-EV availability, and guarded paper controls;
- `System Health`: Binance trade/depth/kline freshness, Coinbase freshness,
  Pyth freshness, own L2 recorder age, database write access, backend code
  identity, and live-execution availability.

The global trust state is `DO_NOT_TRUST` when a required feed is missing/stale
or running backend code differs from disk.

The frontend explicitly identifies the baselines as research-only, the engine
as paper-only, and real orders as disabled. An empty ledger is not presented as
evidence of active or profitable trading.

The frontend also leaves the loading screen after ten seconds when no backend
message has ever arrived. It then exposes the operational tabs with explicit
`unavailable` states. Missing account values render as `--`, never as `$0.00`.

### Final integrity hardening

The final audit found and corrected issues that the first happy-path tests did
not cover:

- audit events now use a monotonic `event_index`; equal timestamps cannot
  reorder and break the hash chain;
- an audit retry that omitted its generated timestamp remains idempotent;
- sequence gaps and invalid ordering remain latched for the recording session
  and clear only when a new session begins;
- non-finite risk state values block orders instead of bypassing comparisons;
- daily and weekly loss limits use persisted paper outcomes instead of constant
  zero placeholders;
- paper-position leverage survives restart and controls margin/liquidation
  reporting;
- funding identifier collisions with different economics raise an error;
- explicit zero/invalid mark prices cannot silently fall back to book mid.

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
- Two baseline Binance paper strategies wired to the isolated simulator.
- Typed paper API, guarded paper controls, and complete paper operations UI.
- Paper candidate expiry/entry bounds, validated order states, truthful
  uncertainty metadata, and aggregate capital-preservation governor.
- Reusable research-validation and promotion-gate library.
- Read-only system-health surface.
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

The Binance paper engine starts with its launch-time hard gate disabled and its
runtime state inactive.

The shared orchestrator also starts fail-closed.

The system-health API is read-only. Binance paper controls can mutate only the
isolated simulator and its dedicated DuckDB database.

No live-execution package exists under `backend/execution_live/`.

No browser code contains private exchange credentials.

## Validation

Focused deterministic commands:

```powershell
python -m backend.quant_platform.test_kernel
python -m backend.binance_paper.test_engine
python -m backend.binance_paper.selftest
python -m backend.binance_paper.api_selftest
python -m backend.quant_platform.test_research_validation
python -m py_compile backend\server.py
npm.cmd run build
npm.cmd audit --audit-level=high
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

On 2026-07-27 the complete deterministic repository matrix passed locally:

- Complete Trade audit, builder, serving, freeze, threshold, evaluator,
  evidence-completion, and Ledger V2 tests;
- 3,600 Ledger V2 forecasts read/resolved across 1,200 rounds;
- head permissions, promotion, and long-window preflight;
- multi-venue recorder, admissibility, and collector-integrity tests;
- shared kernel, Binance paper accounting, and research-validation tests;
- full Binance paper strategy, funding, recovery, API, and control tests;
- all 16 registered paper strategies reconciled across server and UI;
- all seven preregistration hashes intact;
- repository-wide Python compilation and Pyflakes checks;
- Vite production build and npm audit with zero vulnerabilities;
- Windows launcher parsing with a 1,265-day source-backed window, 98/2 gate,
  and full-data refit enabled.

The browser test confirmed that with port 8000 closed the splash exits, the
dashboard is reachable, the connection reads `Disconnected`, and both new
operational views read `unavailable` rather than displaying fabricated values.

The remote GitHub Actions run did not execute repository steps because the
runner stopped before checkout due to the repository/account runner billing
lock. This is an external CI availability blocker, not a passing remote gate.

## Next Valid Sequence

1. Push the consolidated `master`.
2. Restore GitHub Actions runner availability and obtain a run in which the
   actual steps execute and pass.
3. Deploy the existing recorders without altering their frozen evidence schema.
4. Add a canonical-event adapter beside each recorder and compare counts,
   sequence gaps, timestamps, and hashes in shadow.
5. Preregister one Binance mechanism, not a large indicator family.
6. Add it as a disabled challenger beside the two transparent baselines.
7. Collect resolved fills and outcomes.
8. Evaluate with the frozen validation and promotion gates.
9. Keep the result `PAPER_ONLY` unless every predeclared forward gate passes.

## Merge Rule

The engineering branches were consolidated into `master` only after the full
local deterministic matrix passed. Remote GitHub Actions still requires an
available runner to provide independent CI evidence. Strategy profitability
remains a separate forward-evidence question after code consolidation.

## 2026-07-28 Profit Campaign V1

The standalone `PROFIT_CAMPAIGN_V1` research lane is implemented and validated.
It adds no serving or order path. Two campaigns were run on exact Binance
BTCUSDT L2 ladders:

- the q20 cost-aware LONG/SHORT selector emitted zero untouched trades;
- every forced taker baseline lost after fees, spread, depth and impact reserve;
- the dynamic-exit model lost `$481.04` across 398 untouched trades and
  underperformed identical-entry maximum hold by `$2.42`;
- the result validator reconciled 18,116 saved trade rows and all 240 registered
  trials;
- both campaigns remain research-only and not promotable.

See
[PROFIT_CAMPAIGN_V1_IMPLEMENTATION_AND_RESULTS_2026-07-28.md](PROFIT_CAMPAIGN_V1_IMPLEMENTATION_AND_RESULTS_2026-07-28.md)
for the frozen contract, source limits, feature list, model outputs, audit
corrections, exact metrics and commands.
