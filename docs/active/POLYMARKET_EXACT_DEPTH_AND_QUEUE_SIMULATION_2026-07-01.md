# Polymarket Exact Depth VWAP And Queue Simulation

Date: 2026-07-01  
Status: implemented, tested, record-forward, paper research only

## Purpose

This layer answers the execution question the prediction models cannot answer:

> If the model sees an edge at the displayed best price, can the intended order size actually execute
> at a profitable average price, and how likely is a passive order to fill before the opportunity ends?

It does not change the main BTC ensemble, place orders, or claim profit. It records public Polymarket
L2 data into a separate database and evaluates execution economics after depth, fees and latency.

Protocol references: [market WebSocket](https://docs.polymarket.com/market-data/websocket/market-channel),
[WebSocket heartbeat/subscriptions](https://docs.polymarket.com/market-data/websocket/overview),
[authenticated user channel](https://docs.polymarket.com/market-data/websocket/user-channel), and
[fee formula](https://docs.polymarket.com/trading/fees).

## Implemented Files

| File | Responsibility |
|---|---|
| `backend/polymarket/l2_book.py` | Decimal L2 reconstruction, book-integrity gates, exact taker VWAP and queue scenarios |
| `backend/polymarket/l2_recorder.py` | Public WebSocket recorder for current/next BTC 5m and 15m UP/DOWN tokens |
| `backend/research/test_polymarket_l2_execution.py` | Deterministic tests plus recorded-data VWAP and queue report |
| `run_polymarket_l2_recorder.bat` | Standalone continuous recorder |
| `tests\launchers\run_polymarket_l2_execution_test.bat` | Read-only report runner |

The recorder writes only `data/polymarket_l2.duckdb`. It does not contend with
`analytics.duckdb`, `execution_layer.duckdb`, or saved model artifacts.

## Exact Taker VWAP

For a BUY of quantity `Q`, asks are consumed from lowest to highest price. For a SELL, bids are
consumed from highest to lowest. At each level:

```text
take_i = min(remaining_quantity, level_size_i)
gross_notional = sum(price_i * take_i)
VWAP = gross_notional / filled_quantity
```

The result records requested, filled and unfilled shares; completion; best/worst consumed prices;
VWAP; gross notional; estimated crypto taker fee; all-in unit price; slippage; and levels consumed.

The fee function is configurable and defaults to the documented crypto formula
`0.07 * p * (1-p)` per share. The protocol rounds actual fees at match time, while public L2 does not
expose each maker match composing a sweep, so the recorded fee is an estimate even though ladder VWAP
is exact. Before live use, query the market fee schedule. Fee uncertainty must not be hidden in model
confidence.

### Plain-Language Example

```text
Displayed UP ask:        $0.55
Model fair probability:  $0.62
Requested size:          500 shares
Depth VWAP:              $0.59
Fee and safety buffer:   $0.02
Executable edge:         $0.01/share
```

The apparent seven-cent top-of-book edge is only one cent at the requested size. Decisions should use
executable edge at size, not the displayed best ask.

## Queue Simulation

Public L2 exposes price-level quantity, not individual order IDs or exact exchange queue rank.
Consequently, queue output is deliberately reported in three modes:

| Mode | Cancellation assumption | Interpretation |
|---|---|---|
| conservative | cancellations do not move this order forward | lower estimate; only opposite-side trades consume queue ahead |
| base | 50% of removed level size was ahead | research midpoint, to be calibrated |
| optimistic | all removed size was ahead | upper estimate, never a sole trading justification |

For a maker BUY, only SELL-aggressor trades at the exact order price consume queue. For a maker SELL,
only BUY-aggressor trades do so. The simulator supports partial fills, configurable submit latency,
time to first/full fill, a fixed replay window and expiry.

WebSocket connection boundaries are persisted. A simulated order is truncated at the next reconnect;
it never crosses an unobserved data gap. The book is also cleared on reconnect and cannot calculate
VWAP until a new full snapshot arrives.

## Persisted Schema

| Table | Contents |
|---|---|
| `pm_l2_markets` | asset-to-round, horizon and UP/DOWN mapping |
| `pm_l2_raw_events` | full book, trade, control and unsupported raw messages |
| `pm_l2_book_levels` | every level from each full `book` snapshot |
| `pm_l2_level_updates` | every parsed `price_change`, previous/new size and applied state |
| `pm_l2_trades` | public trade price, size and aggressor side |
| `pm_l2_book_summaries` | sampled validity, best prices, spread and total depth |
| `pm_l2_execution_snapshots` | compact JSON with exact BUY/SELL VWAP at configured sizes |

Updates and trades remain event-by-event for queue replay. Calculated states are sampled once per
second per token to control growth. Default sizes are 1, 10, 50, 100 and 500 shares.

## Commands

```powershell
# Continuous forward recorder
.\run_polymarket_l2_recorder.bat

# Custom sizes and one-second calculated-state sampling
.\run_polymarket_l2_recorder.bat --sizes 1,10,25,50,100 --sample-ms 1000

# Network-free deterministic tests
.\tests\launchers\run_polymarket_l2_execution_test.bat --selftest

# Report after briefly stopping the standalone recorder
.\tests\launchers\run_polymarket_l2_execution_test.bat --order-size 10 --window-seconds 30 --submit-latency-ms 250
```

Outputs:

```text
data/research/polymarket_l2_execution/latest_exact_depth_vwap.csv
data/research/polymarket_l2_execution/maker_queue_scenarios.csv
data/research/polymarket_l2_execution/summary.json
```

## Verification Completed

The deterministic suite verifies multi-level BUY/SELL VWAP, fee direction, partial fills,
insufficient depth, level removal, crossed-book rejection, ordered queue bounds, submission latency,
and DuckDB persistence.

A live public-feed smoke test also completed successfully:

- four current/next BTC outcome tokens subscribed;
- more than 1,300 messages processed in approximately 12 seconds;
- 2,696 incremental level updates and three trades persisted;
- 48 sampled execution states produced;
- all sampled states passed the book-integrity checks;
- the read-only report produced exact-depth and queue-scenario CSVs.

This proves protocol handling and calculation mechanics. It does not prove profitable execution.

## Resource And Storage Notes

RAM is bounded because only current ladders are held in memory. CPU use is small; there is no model
training. Disk is the main cost. During active periods the feed can exceed 200 level-update rows per
second, so the database can grow by several gigabytes over a multi-week collection. Monitor
`data/polymarket_l2.duckdb` and archive it only while the recorder is stopped.

Do not run two L2 recorders against the same DuckDB. The prediction app can run normally because it
uses different databases.

## What Is Still Not Exact

Exact queue position is unavailable from public L2. It requires order IDs or authenticated evidence
from orders submitted by this account. The next calibration phase is:

1. connect the authenticated user channel with backend-only environment credentials;
2. record placement, update, cancellation and trade lifecycle timestamps;
3. use paper/sandbox orders if available, otherwise only tiny explicitly approved probes;
4. compare actual partial/full fills with all three queue modes;
5. learn cancellation credit and latency by horizon, side, price and seconds left;
6. promote queue probability only after a separate forward sample.

Credentials must never be stored in source code, DuckDB payload JSON, frontend JavaScript or docs.

## Promotion Gates

- At least 14 days of varied forward L2 coverage.
- No unexplained stream gap inside evaluated order windows.
- Positive taker expectancy using exact size-specific VWAP and verified fees.
- Positive maker expectancy under the conservative queue mode.
- Actual authenticated fills calibrate predicted fill probability.
- Latency, partial-fill and adverse-selection costs are included.
- Results survive a later, non-overlapping forward window.
- Explicit position limits and kill switch exist.

The correct near-term use is a research and decision veto: reject opportunities whose edge disappears
after depth, and treat maker fill output as a bounded range rather than a promise.
