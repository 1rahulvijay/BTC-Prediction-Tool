# Master Consolidation And Validation

Date: 2026-07-27

Canonical branch: `master`

Mode: research, shadow, and paper only

Real Binance orders: unavailable

Real Polymarket orders: unavailable

## Purpose

This record describes the final repository consolidation, the Binance paper
service integrated during that consolidation, the defects corrected during the
integration audit, and the executable validation performed before retiring the
old branches.

It is an engineering-completeness record. It is not evidence of a profitable
strategy and does not authorize real-money trading.

## Consolidated Components

The canonical branch contains:

- the existing BTC feature, regime, ensemble, calibration, specialist-head,
  Complete Trade Forecast, Polymarket, recorder, and analysis paths;
- immutable serving bundles, forward-evidence isolation, Ledger V2, frozen
  threshold artifacts, and promotion refusal gates;
- the venue-neutral quant-platform event, health, feature-contract, risk,
  audit, allocation, and research-validation kernel;
- the isolated Binance futures accounting test engine;
- the full Binance futures paper service with Trend Following and Breakout
  baselines;
- the complete paper API and frontend operations view;
- system-health and stale-code visibility;
- Linux and Windows deterministic invariant workflows.

No trained artifacts, evidence datasets, or frozen protocols were rebuilt as
part of branch consolidation.

## Production Paper-Service Flow

```text
Public Binance futures WebSocket
  -> BTCUSDT bookTicker + aggTrade + liquidations
  -> typed, freshness-checked MarketSnapshot
  -> Trend Following / Breakout paper decisions
  -> risk gate
  -> simulated latency
  -> executable bid/ask + visible top-size fill
  -> fee, slippage and funding accounting
  -> isolated DuckDB transaction
  -> paper API and UI
```

The service is locked twice:

1. `BTC_ENABLE_BINANCE_PAPER=1` must exist before backend launch.
2. The operator must press `Start paper engine`.

With the default value `0`, no paper strategy can create a simulated order.
There is no authenticated Binance client, credential store, signature path, or
real order endpoint in the package.

## Accounting And Execution Contract

The production paper service:

- uses asks for simulated buys and bids for simulated sells;
- applies configured latency before selecting the executable quote;
- requires the full requested quantity at visible top size;
- rejects stale quotes and missing required inputs;
- charges explicit entry and exit taker fees;
- applies adverse slippage and records spread/slippage costs separately;
- handles LONG and SHORT PnL with the correct sign;
- applies observed settled funding with deterministic idempotent identifiers;
- closes on stop, take-profit, maximum hold, opposing signal, or confirmed
  manual action;
- persists account, order, fill, position, funding, trade, equity, and event
  state transactionally;
- cancels orphan pending orders on restart and reconciles persisted state;
- keeps Trend Following and Breakout accounts isolated.

The service is a top-of-book simulator. It does not claim queue position,
multi-level VWAP, exchange acknowledgement, cancel latency, bankruptcy, or live
fill parity.

## Integration Defects Found And Fixed

### Duplicate service identity and route

The initial branch merge imported the old module-level paper service and the
new `BinancePaperService` instance under the same name. It also exposed two
handlers for `/api/binance-paper/status`.

The old production import and duplicate route were removed. The full
`BinancePaperService` plus its typed router are now the sole server integration.
The older low-level engine remains an isolated test harness.

### Excessive DuckDB work on bookTicker

The initial integration opened a DuckDB marking transaction on every Binance
`bookTicker` update, including while the paper hard gate was disabled. This
could have delayed futures WebSocket processing.

The service now:

- does no portfolio/database work while the hard gate is disabled;
- processes pending latency fills on incoming quotes;
- bounds portfolio marking, funding checks, exit scans, and equity persistence
  to the configured sampling cadence;
- processes a newly observed funding settlement immediately and idempotently.

### Frontend/backend contract collision

The old compact read-only paper panel and the complete operations panel had
overlapping renderer and polling names. The compact renderer was removed.
`System Health` fetches only `/api/system-health`; `Binance Paper` owns its
typed multi-endpoint polling lifecycle and stops polling when its tab is hidden.

## Validation Performed

The complete deterministic repository matrix passed on the consolidated source
state:

- Complete Trade audit regressions and builder integration;
- complete-trade serving and optimizer integration;
- forward-evidence class isolation and frozen threshold validation;
- matched-random, Benjamini-Hochberg, concentration, and M0 gates;
- Ledger V2 end to end: 3,600 predictions, 1,200 independent rounds;
- durable evidence spool, eligibility, and own-L2 outcome tests;
- immutable champion pointer swaps and freeze-guard behavior;
- head permissions, challenger promotion, and long-window preflight;
- multi-venue parser, admissibility, continuity, and collector-integrity tests;
- quant-platform kernel and research-validation tests;
- Binance low-level execution/accounting tests;
- Binance full-service accounting, funding, reversal, latency, stale-data,
  partial-liquidity, pause, idempotency, recovery, rollback, risk, and evidence
  tests;
- Binance typed API and default-off control tests;
- all 16 Polymarket paper-strategy registry mappings;
- all documentation tables;
- all seven frozen preregistration hashes;
- repository-wide Python compilation and Pyflakes static checks;
- Vite production build.

The frontend dependency audit reports no high-severity vulnerability. The
Windows launcher validation prints the resolved paper hard-gate state without
starting the servers.

## Remaining Evidence-Gated Work

The following are intentionally not represented as complete:

- profitable-strategy proof;
- authenticated or real exchange execution;
- exchange acknowledgements and live-order reconciliation;
- full-depth or queue-aware Binance execution;
- trained liquidation, exhaustion, cross-venue lead-lag, basis/OI/funding,
  volatility-expansion, or absorption challengers;
- purged out-of-fold ACT/SKIP policy from resolved paper candidates;
- portfolio promotion or real-capital allocation;
- the required 500 trades and 8-12 weeks of independent forward evidence;
- independent remote CI execution while GitHub runner availability is blocked.

Missing market evidence cannot be replaced by more code, historical refitting,
or a claim that all signals will be accurate.

## Operator State

Default launch behavior:

```text
BTC_ENABLE_BINANCE_PAPER=0
paper service initialized for inspection
paper strategies unable to start
real orders impossible
```

To collect paper-only baseline evidence:

```powershell
$env:BTC_ENABLE_BINANCE_PAPER = "1"
.\start.bat
```

Then open `Binance Paper` and press `Start paper engine`. The UI must continue
to show `PAPER ONLY` and `REAL ORDERS DISABLED`.
