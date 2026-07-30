# Ceiling-Breaking Multi-Engine Architecture

Date: 2026-07-28

Status: **first 1h campaign implemented as forward research; no trading edge
proved; no order path**

The shared target-specific combination layer is now implemented as
research-only infrastructure:

```text
docs/active/HIERARCHICAL_TARGET_SPECIFIC_ENSEMBLE_V1_2026-07-28.md
```

It enforces model-role ownership, logs immutable OOF/forward forecasts, fits
only same-target constrained ensembles, selects actions on conservative
post-cost return distributions and leaves the multi-alpha portfolio empty
until at least two engines independently pass promotion. It does not activate
new trading behavior.

## Decision

The proposal is directionally correct: the app should not add another generic
UP/DOWN model and call it progress. It should maintain independent,
cost-aware engines and activate none of them unless conservative forward
expected value survives fees, executable depth, latency, uncertainty and
regime change.

The first implementation is:

```text
POLY_1H_DIGITAL_FAIR_VALUE_V1
```

The path features required by:

```text
POLY_1H_PATH_AND_CROSSING_V1
```

are recorded in the same evidence lane. A trained residual or path model is
intentionally deferred until the forward ledger has at least 500 settled
hourly rounds.

## Why The One-Hour Contract Is Different

Polymarket's rule is exact:

```text
UP   when finalized Binance BTCUSDT 1h close >= finalized 1h open
DOWN otherwise
```

The relevant start is Gamma's `eventStartTime`, not the event creation time or
the displayed Polymarket market-open time. The relevant finish is Gamma's
`endDate`. The implementation rejects any candidate whose interval is not
exactly 3,600 seconds or whose rule/source text does not explicitly identify
the Binance BTC/USDT 1-hour candle.

Primary references:

- [Polymarket hourly BTC market rule](https://polymarket.com/event/bitcoin-up-or-down-july-25-2026-7am-et)
- [Polymarket fee documentation](https://docs.polymarket.com/trading/fees)

## Implemented Package

Code:

```text
backend/research/poly_1h_digital_fair_value_v1/
    core.py
    frozen_protocol.json
    live_shadow.py
    report.py
    selftest.py
    store.py
```

Launchers:

```powershell
.\research\launchers\run_poly_1h_fair_value_shadow.bat
.\research\launchers\run_poly_1h_fair_value_shadow.bat --duration 300
.\research\launchers\report_poly_1h_fair_value_shadow.bat
```

Default evidence:

```text
data/research/poly_1h_digital_fair_value_v1/shadow.duckdb
data/research/poly_1h_digital_fair_value_v1/report/summary.json
data/research/poly_1h_digital_fair_value_v1/report/calibration_buckets.csv
data/research/poly_1h_digital_fair_value_v1/report/path_targets.csv
data/research/poly_1h_digital_fair_value_v1/report/executable_candidates.csv
```

The process uses public endpoints only. It has no credential import, API-key
field, order constructor or order submission method.

## Exact Data Contract

### Polymarket

The recorder discovers only Gamma series `10114`,
`btc-up-or-down-hourly`, then verifies:

- exactly one market;
- outcomes mapped by names `Up` and `Down`, never token-array position;
- exact 3,600-second `eventStartTime` to `endDate`;
- rule contains `close >= open`, `BTC/USDT 1 hour candle` and finalized
  `1H` candle language;
- resolution source is Binance BTC/USDT;
- dynamic CLOB fee rate exists for both tokens;
- both books are two-sided and ordinary binary books;
- both full books were fetched recently and within the pair-skew limit.

The CLOB book timestamp is retained as the last venue mutation timestamp. It
is not incorrectly treated as HTTP response age: a quiet but freshly fetched
book can legitimately have an old mutation timestamp.

### Binance

The recorder uses:

- exact `BTCUSDT` 1h kline open for the anchor;
- public spot aggregate trades for current price and source timestamp;
- trailing 1m closes for fast and slow realized volatility;
- the finalized 1h kline for open/high/low/close/volume and settlement.

Tie settlement is explicitly tested as UP.

## Probability Baselines

V1 compares three frozen, model-free baselines:

### A: Market only

```text
p_A = UP midpoint / (UP midpoint + DOWN midpoint)
```

Normalizing both midpoints avoids assuming they sum to one inside the spread.

### B: Distance and time

```text
x = log(current Binance price / exact Binance 1h open)
p_B = Phi(x / (slow sigma per second * sqrt(seconds left)))
```

There is no fitted drift. Slow realized volatility uses the previous 240
one-minute closes.

### C: Volatility mixture

```text
p_C =
    0.60 * fast-vol diffusion
  + 0.25 * slow-vol diffusion
  + 0.15 * jump-vol diffusion
```

Fast volatility uses 30 one-minute closes. Jump volatility is 2.5 times fast
volatility, capped at the frozen maximum. This is a conservative analytic
baseline, not a trained regime model.

No residual correction is active:

```text
residual_lambda = 0
residual_models_enabled = false
```

The future residual model must predict information beyond the market price
using purged, time-ordered training and a final untouched period. It cannot be
trained or promoted from the initial sparse ledger.

## Path State Recorded Causally

Every valid observation stores:

- fraction of elapsed samples above and below the open;
- crossing count and crossing rate;
- seconds since the last crossing;
- average and longest residence above/below;
- maximum distance above and below;
- drawdown from the current side's extreme;
- 15-second and 60-second velocity toward/away from the anchor.

At reporting checkpoints, resolved paths export:

- current side held;
- crossed anchor before settlement;
- recrossed after the first future crossing;
- remained on the current side;
- number of future crossings;
- terminal distance from open;
- terminal move from the observation price.

These labels support future settlement, crossing-hazard, recross, residence
and terminal-distance models without reconstructing labels from a different
price source.

## Executable Economics

The recorder stores complete ladders plus exact 5/10/50-share calculations:

- buy and sell VWAP;
- exact filled quantity;
- level-weighted fee per share.

The report never multiplies the top ask by unsupported size. At each frozen
checkpoint it selects at most one side for a baseline and requires:

```text
fair probability
- executable 5-share ask VWAP
- exact level-weighted taker fee
- 3c uncertainty buffer
>= 1c minimum net edge
```

Realized PnL is:

```text
settlement payout - entry VWAP - entry fee
```

Confidence bounds are bootstrapped by round, not by correlated one-second
snapshots. The final 20% check is chronological.

## DuckDB Tables

| Table | Purpose |
|---|---|
| `campaign_meta` | frozen protocol and source/code hashes |
| `health_events` | discovery, source, latency and rejection health |
| `hourly_markets` | rule, tokens, exact candle times and dynamic fees |
| `hourly_snapshots` | Binance state, books, probabilities, path and VWAP |
| `hourly_resolutions` | finalized Binance outcome and Polymarket reconciliation |

The database is append-preserving, duplicate-safe by market/observed-second
and capped at 16 GB. Full ladders are roughly 2 KB per one-second observation
in the current live market, so the cap leaves room for the frozen eight-week
campaign while still failing safely before unbounded disk growth.

## Frozen Promotion Gate

Promotion remains impossible until all checks pass:

- at least 500 finalized hourly rounds;
- at least eight continuous weeks;
- at least 200 rounds at each reporting checkpoint;
- probability score improves over the market-only baseline;
- executable EV round-clustered lower 95% bound is positive;
- UP and DOWN expectancy are independently positive;
- final chronological 20% is positive;
- every Binance resolution is reconciled to Polymarket;
- zero outcome mismatches.

Passing these gates would authorize a separate shadow candidate, not live
money. A full-data refit still remains in live shadow because it has no
untouched test period after refitting.

## Validation Performed

```text
Python compileall                              PASS
Ruff package scan                             PASS
Research-only boundary                        PASS
Outcome-name token mapping                    PASS
Exact 3,600-second market validation          PASS
Tie resolves UP                               PASS
Digital probability symmetry/monotonicity     PASS
Volatility-mixture bounds                     PASS
Full-ladder VWAP                              PASS
Exact level-weighted fee                      PASS
Path crossing/residence state                 PASS
DuckDB duplicate protection                   PASS
Resolution persistence                        PASS
Checkpoint/report/export path                 PASS
Current public Gamma/CLOB/Binance smoke       PASS
```

Live smoke observation:

```text
eligible hourly markets       1
one-second snapshots          4
valid snapshots               4
dynamic fee                   1000 bps per outcome
quantities graded             5 / 10 / 50 shares
orders submitted              0
```

Four snapshots prove connectivity and schema correctness, not edge.

## Full Proposal Status

| Priority | Campaign | Status after this change |
|---:|---|---|
| 1 | `POLY_1H_DIGITAL_FAIR_VALUE_V1` | implemented, forward research only |
| 2 | `POLY_1H_PATH_AND_CROSSING_V1` | causal inputs/labels implemented; trained model deferred |
| 3 | `POLY_BINANCE_DELTA_HEDGE_V1` | not implemented; needs fair-value evidence and hedge-cost replay |
| 4 | `POLY_1H_MARKET_MAKER_V1` | not implemented; needs queue/fill/toxicity evidence |
| 5 | `BINANCE_MAKER_CONVERSION_V1` | implemented separately as forward shadow |
| 6 | `BINANCE_COST_AWARE_BARRIER_V1` | research components exist; no promoted engine |
| 7 | `BINANCE_MAGNITUDE_BREAKOUT_V1` | proposed; not implemented as an economic campaign |
| 8 | `BINANCE_LIQUIDATION_PHASE_V1` | not implemented |
| 9 | `BINANCE_FUNDING_BASIS_V1` | not implemented |
| 10 | `CROSS_HORIZON_PROBABILITY_SURFACE_V1` | not implemented |
| 11 | complete-set inventory recycling | scanner exists; inventory optimizer not implemented |
| 12 | on-chain informed flow | not implemented |
| 13 | extreme-price calibration | buckets implemented in 1h report; no strategy |
| 14 | failed-breakout specialist | not implemented as economic engine |
| 15 | regime-specific trend/reversion | partial research only |
| 16 | Hawkes order-arrival engine | not implemented |
| 17 | order-flow image model | not implemented |
| 18 | Polymarket sentiment residual for Binance | not implemented |
| 19 | three-leg relative value | not implemented |
| 20 | leverage/risk allocator | intentionally blocked before proven engines |

## Next Correct Actions

1. Run the 1h shadow continuously without changing the frozen protocol.
2. Generate the report weekly; do not tune from the first few rounds.
   Stop the standalone writer before opening its DuckDB from the separate
   report process; DuckDB intentionally holds a single-writer file lock.
3. After 500 resolved rounds, create a separate purged residual/path training
   campaign and compare it against A/B/C.
4. Research delta hedging only after the fair-value residual survives
   executable Polymarket costs.
5. Research market making only with defensible queue/fill labels.
6. Build the cross-horizon surface only after 5m/15m/1h observations are
   synchronized to their exact independent anchors.

## Bottom Line

This change creates a valid experiment around a genuinely exact settlement
mapping. It does not prove that Polymarket is mispriced, that an analytic
probability beats its market price, or that delta hedging/market making is
profitable. Those claims now have a forward ledger and frozen rejection gates
instead of assumptions.
