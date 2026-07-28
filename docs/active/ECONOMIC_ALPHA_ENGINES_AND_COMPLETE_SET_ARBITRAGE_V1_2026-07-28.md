# Economic Alpha Engines And Complete-Set Arbitrage V1

Date: 2026-07-28

Status: **research-only implementation; no edge discovered; no order path**

## Decision

The app's next ceiling is economic, not another UP/DOWN classifier. Existing
research has repeatedly found information about movement, path, volatility and
very short event-time direction, but a predictive score is not a trade unless
its conservative expected value survives:

```text
spread
+ current venue fees
+ depth slippage
+ latency
+ partial-fill / failed-leg risk
+ operating cost
+ uncertainty buffer
```

The target architecture is a portfolio of independent economic engines:

1. structural arbitrage;
2. market making and execution;
3. delta-neutral carry;
4. cross-market relative value;
5. sparse directional specialists;
6. central portfolio and risk control.

`POLY_COMPLETE_SET_ARBITRAGE_V1` is the first structural engine. It is an
evidence collector, not a promoted strategy.

## Existing Evidence Is Preserved

The historical test in
`STRUCTURAL_EDGE_HUNT_2026-07-25.md` scanned 3,780,501 executable top-of-book
ticks from 14,226 settled BTC 5m rounds:

- only 21 raw complement crossings;
- only three affected rounds;
- mean apparent locked profit was an implausible 36.5 cents;
- 20 of 21 crossings exceeded two cents.

That shape is consistent with stale/collapsed book artifacts, not a fillable
arbitrage population. The verdict remains **NO TRADEABLE EDGE**.

The new engine does not reinterpret those artifacts as profit. It answers a
narrower forward question with current market mechanics:

```text
Do current synchronized full-depth books ever contain a fee-adjusted,
same-size complete-set gap that survives realistic execution delay?
```

## Contract Identities

For a binary market, one complete set is one UP token plus one DOWN token.

### Buy both, then merge

For quantity `q`:

```text
raw net =
    q
  - exact UP ask-ladder cash
  - exact DOWN ask-ladder cash
```

The shadow marks an opportunity only when:

```text
raw net
- q * safety margin
- fixed operating cost
> 0
```

### Split collateral, then sell both

For quantity `q`:

```text
raw net =
    exact UP bid-ladder proceeds
  + exact DOWN bid-ladder proceeds
  - q
```

This side requires prefunded complete-set inventory before both sells. Until
inventory and split/merge costs are measured, it is labeled
`BOOK_EXECUTABLE_INVENTORY_REQUIRED`, never promotion-eligible.

## Exact Execution Accounting

Implementation:

- `backend/research/poly_complete_set_arbitrage_v1/economics.py`
- reuses the deterministic Decimal-based `L2Book`;
- consumes each UP/DOWN level in price priority;
- requires exactly equal filled quantity on both outcomes;
- calculates the fee separately at every consumed level;
- reports gross notional, fee, VWAP, worst price and levels consumed per leg;
- calculates `5`, `10` and `50` share cases;
- walks all combined depth breakpoints to find:
  - available equal quantity;
  - quantity producing maximum conservative dollar profit;
  - maximum quantity that remains conservatively profitable.

No top-of-book price is multiplied by an unsupported size.

## Dynamic Market Rules

The shadow queries, caches and persists for each token:

```text
GET /fee-rate/{token_id} -> base_fee in basis points
GET /book?token_id=...   -> minimum order size, tick size, neg-risk flag
```

It converts:

```text
fee_rate = base_fee_bps / 10,000
fee/share = round(fee_rate * p * (1-p), 5)
```

Missing or stale fee/rule metadata fails closed. The 2026-07-28 live smoke
returned:

```text
base_fee_bps = 1000 on both BTC outcomes
fee_rate     = 0.10
min size     = 5 shares
tick size    = 0.01
neg risk     = false
```

These values are observations, not constants. This is materially different
from older research scripts that assumed `0.07`.

## Cross-Book Synchronization

A raw price gap is not an executable opportunity. Qualification also requires:

```text
both books valid and two-sided
both exchange timestamps present
receive age <= 500 ms
receive timestamp skew <= 250 ms
exchange timestamp skew <= 250 ms
fresh token-specific fee metadata
active round window
ordinary binary market (not neg-risk)
quantity >= current market minimum
```

Every raw snapshot is stored with its rejection reason. A stale/skewed pair can
never open an opportunity.

## Delay And Failed-Leg Stress

Every positive gap episode is followed after:

```text
250 ms
500 ms
1000 ms
```

The ledger records:

- actual observation delay;
- whether the same-size pair is still positive;
- current full-pair net;
- UP-first result using the original UP fill and delayed DOWN fill;
- DOWN-first result using the original DOWN fill and delayed UP fill;
- worst failed-leg result.

An episode that closes before 250 ms remains tracked until all three delayed
grades are written. A later reopening becomes a new independent episode.

This is still a **book-survival proxy**, not observed fill probability. Public
book availability cannot prove that two submitted orders both filled. The gate
therefore remains blocked until real shadow-order or exchange-confirmed fill
evidence exists.

## DuckDB Evidence

Default database:

```text
data/research/poly_complete_set_arbitrage_v1/shadow.duckdb
```

Tables:

| Table | Purpose |
|---|---|
| `complete_set_meta` | frozen protocol and fee provenance |
| `complete_set_markets` | tokens, horizons, dynamic fees and market rules |
| `complete_set_snapshots` | pair timing, raw gaps, size economics and capacity |
| `complete_set_opportunities` | independent positive-gap episodes and duration |
| `complete_set_delay_stress` | 250/500/1000 ms pair and failed-leg outcomes |

The database has a frozen 2 GB safety cap. It is owned only by this standalone
process.

## Launchers

Continuous forward shadow:

```powershell
.\run_poly_complete_set_arbitrage_shadow.bat
```

Bounded diagnostic run:

```powershell
.\run_poly_complete_set_arbitrage_shadow.bat --duration 300
```

Report:

```powershell
.\report_poly_complete_set_arbitrage_shadow.bat
```

The launcher reads public data only. The package has no credential import and no
order method.

## Validation Completed

```text
Python compileall                                  PASS
Ruff package scan                                 PASS
Exact buy-both / sell-both economics              PASS
Current fee conversion (1000 bps -> 0.10)         PASS
Depth capacity and insufficient-depth handling    PASS
DuckDB market upsert                              PASS
Positive-gap open/close lifecycle                 PASS
Short-gap delayed tracking (3 sizes x 3 delays)  PASS
Research-only boundary checks                     PASS
Current public WebSocket smoke                    PASS
```

Second live smoke:

```text
duration                     10 seconds after connection
assets                       4 (UP/DOWN for active 5m and 15m)
markets                      2
full pair snapshots          79
opportunities                0
delay records                0
```

The first smoke recorded 95 snapshots, 94 synchronization-qualified, also with
zero raw buy or sell gaps. This is not enough evidence for a frequency estimate,
but it is directionally consistent with the historical no-edge result.

## Frozen Promotion Standard

The report fails closed unless all required evidence clears:

- at least 500 independent opportunities;
- at least eight continuous forward weeks;
- at least 75% positive weeks;
- positive day-block 95% lower bound;
- profit factor at least 1.20;
- no week contributes more than 35% of positive profit;
- complete 250/500/1000 ms stress coverage;
- positive net at every required delay;
- profitability with rebates set to zero;
- measured split/merge and operating cost;
- failed-leg loss limit declared and passed;
- real two-leg fill evidence;
- positive final untouched period.

Current promotion is deliberately impossible because measured operating cost,
real fill evidence, failed-leg risk tolerance and a final untouched period do
not yet exist.

## What Was Not Implemented

The broader economic-engine proposal is not falsely marked complete:

| Campaign | Status |
|---|---|
| `POLY_COMPLETE_SET_ARBITRAGE_V1` | implemented as forward research shadow |
| `POLYMARKET_REPRICING_SHADOW_V1` | implemented separately; still forward-gated |
| negative-risk multi-outcome arbitrage | not implemented |
| Polymarket maker inventory engine | not implemented |
| option-implied binary relative value | not implemented |
| Polymarket/Binance delta hedge | not implemented |
| `BINANCE_MAKER_CONVERSION_V1` | not implemented |
| funding/basis carry | not implemented |
| liquidity/liquidation specialists | not implemented |
| cross-market graph residual | not implemented |
| multi-alpha portfolio allocator | intentionally blocked |

## Next Implementation Order

1. Run this forward scanner continuously without changing its protocol.
2. Keep `POLYMARKET_REPRICING_SHADOW_V1` collecting independent evidence.
3. Build `BINANCE_MAKER_CONVERSION_V1` around fill, queue, toxicity and markout,
   counting every original signal including unfilled orders.
4. Research funding/basis carry and option-implied Polymarket residuals.
5. Build a portfolio allocator only after at least two engines independently
   pass locked promotion gates.

## Bottom Line

This implementation improves evidence quality, not forecast accuracy. It cannot
guarantee profit and does not establish a current complete-set edge. Its value is
that a future claim must survive current fees, exact depth, equal-size execution,
cross-book timing, delayed repricing and failed-leg risk before the app can call
it an opportunity.

