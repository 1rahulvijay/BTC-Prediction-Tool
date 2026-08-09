# Polymarket vs Binance $500 Paper Competition V1

Date: 2026-08-09  
Status: implemented; paper-only forward experiment  
Real order authority: absent and disabled

## Purpose

Run one model-driven paper account on each venue with the same starting capital and compare
their realized after-cost results. The experiment answers a narrow question:

> Which current model account preserves or grows a simulated $500 bankroll better under its
> venue's own execution and settlement rules?

It does not claim that either model is profitable, suitable for real capital, or capable of
producing sustainable income.

## Competitors

| Venue | Paper model | Role |
|---|---|---|
| Polymarket | `CHAMPION_DYNAMIC_PAPER_V1` | Model/head-gated binary-contract paper entries and exits |
| Binance USD-M perpetual | `model_consensus` | Existing ensemble-driven LONG/SHORT paper strategy |
| Binance control | `random_control` | Zero-information diagnostic account; not ranked as a competitor |

Only the two model rows are ranked. The random control remains active because an apparent
Binance edge that does not beat a matched zero-information baseline has established little.

## Capital And Risk Contract

Each ranked model receives a separate simulated bankroll of `$500`:

| Limit | Value per model |
|---|---:|
| Starting bankroll | $500 |
| Maximum new position | 10%, or $50 |
| Maximum simultaneous exposure | 20%, or $100 |
| Daily loss stop | 5%, or $25 |
| Weekly loss stop | 12%, or $60 |
| Maximum drawdown stop | 10%, or $50 |

Polymarket replay sizing is additionally capped by the recorded top-ask depth. A signal with no
recorded executable depth is rejected. Binance uses the native risk engine, executable book,
latency, fee, slippage and funding accounting. The competition refuses to compare the accounts
when the Binance persisted limits, starting cash or database identity differ from this contract.

## Isolated State

The two venues retain separate economics and ledgers:

- Polymarket source ledger: `data/analytics.duckdb`, table `rule_paper_trades`.
- Binance race ledger: `data/binance_paper_competition_500.duckdb`.
- Persistent race identity and start epoch: `data/paper_competition_500.json`.

The state file is created atomically on the first successful backend startup. It records whether
the dedicated Binance account was clean: no prior trades, no open position and no prior realized
P/L, fees or funding. Subsequent restarts continue the same race. Removing only the state file
while retaining a used Binance database produces `BLOCKED_CONFIGURATION_MISMATCH`; it cannot
silently reuse old P/L as a new `$500` account. Changing the bankroll, model IDs or risk contract
without deliberately starting a new state is also blocked.

## Accounting And Ranking

The provisional leader is determined only by:

```text
sum(realized net P/L after recorded costs since the race epoch)
```

The system intentionally does not compare incompatible unrealized marks:

- Binance marked equity is shown as a diagnostic because the perpetual position can be marked
  against the current executable market.
- Open Polymarket positions remain at entry cost until an executable early exit or official
  settlement. The app does not fabricate a current value when a trustworthy paired book is not
  available.

Displayed metrics include settled equity, realized return, closed trades, win rate, profit
factor, average net P/L, realized drawdown, open exposure and recorded costs. A database read
failure is `ACCOUNTING_UNAVAILABLE`, never an empty but apparently healthy account.

## Evidence Rule

The UI labels all results provisional. A basic comparison requires at least 30 closed trades from
each model. Thirty trades is only an initial sample threshold, not proof of edge. Any promotion to
real capital would still require independent forward evidence, day-clustered uncertainty,
live-versus-paper fill agreement, positive expectancy after all costs and explicit human approval.

## UI And API

- App tab: `$500 Race`
- Read-only endpoint: `GET /api/paper-competition`
- Refresh cadence while the tab is visible: five seconds

The endpoint has no start, order, funding, withdrawal, reset or live-routing operation. The UI
shows `PAPER ONLY` and `REAL ORDERS DISABLED` at all times.

## Launcher Defaults

`start.bat` sets the race defaults when the operator has not explicitly supplied an override:

```text
BTC_PAPER_COMPETITION_BANKROLL_USD=500
BTC_PAPER_COMPETITION_POSITION_FRACTION=0.10
BTC_PAPER_COMPETITION_EXPOSURE_FRACTION=0.20
BTC_PAPER_COMPETITION_POLY_RULE=CHAMPION_DYNAMIC_PAPER_V1
BTC_PAPER_COMPETITION_BINANCE_STRATEGY=model_consensus
BTC_BINANCE_PAPER_STARTING_CASH=500
BTC_BINANCE_PAPER_DB=data\binance_paper_competition_500.duckdb
BTC_BINANCE_COMPETITION_ONLY=1
BTC_ENABLE_BINANCE_PAPER=1
BTC_BINANCE_PAPER_AUTO_START=1
```

Competition mode enables `model_consensus` and `random_control`; the older Binance strategy
accounts stay disabled by default in the dedicated race database. Auto-start only starts this
paper engine; it does not create or authorize any real exchange order route.

## Code Ownership

| Component | Responsibility |
|---|---|
| `backend/paper_competition.py` | Persistent race identity, normalized accounting and comparison |
| `backend/database.py` | Complete chronological Polymarket competition rows |
| `backend/binance_paper/persistence.py` | Uncapped chronological Binance race trades |
| `backend/binance_paper/strategy_registry.py` | Competition-only strategy defaults |
| `backend/server.py` | Startup initialization and read-only API |
| `index.html`, `src/main.js`, `src/style.css` | Plain-language race dashboard |

## Validation Contract

The executable race selftest covers capital sizing, recorded depth, wins/losses, realized P/L and
settled equity. It is wired into both `start.bat` and `.github/workflows/invariants.yml`.

Required validation after any change:

```powershell
python backend\paper_competition.py
python -m backend.binance_paper.test_engine
python backend\polymarket\model_dynamic_paper.py
python backend\test_paper_trading_integrity.py
python -m pytest -q
python -m compileall -q backend
python -m pyflakes backend
npm.cmd run build
```

## Interpretation

Use the race as an evidence collector, not as an income promise. A positive balance can result
from chance, a favorable short regime, unrealistically optimistic paper fills, or too few
independent trades. Sustainable income is not established until the economics remain positive
across enough unseen days and real executable conditions.
