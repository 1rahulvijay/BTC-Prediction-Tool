# Binance Maker Conversion V1

Date: 2026-07-28

Status: implemented, validated, forward evidence not yet sufficient

Mode: research shadow only

Orders: impossible in this package

## Executive Verdict

The event-time direction research found repeatable gross short-horizon ranking:

- 5-second direction AUC was approximately 0.77 in two non-overlapping eras.
- 15-second direction AUC was approximately 0.69.
- Frozen E09/E10 candidates had positive gross direction economics.
- A two-basis-point round-trip cost assumption turned the gross result negative.

That means the next question is economic, not predictive:

> Can passive entry convert the same frozen candidate stream into positive
> after-cost expectancy without creating unacceptable missed moves or adverse
> selection?

`BINANCE_MAKER_CONVERSION_V1` now records the evidence required to answer that
question. It does not alter the source models, train a new direction model,
submit orders, or claim profitability.

## Why This Is Forward-Only

The repository does not contain historical data capable of an honest maker-fill
backtest for the frozen May 2026 candidates:

- `data/depth_cache` contains aggregate percentage-depth bands, not a sequenced
  best-price queue.
- `data/microstructure.duckdb` contains one-second summaries, not exact
  update-ID continuity, order-level queue state, or trade-through sequencing.
- the historical candidate period and available depth windows do not overlap.

Fabricating maker fills from those files would create false precision. This lane
therefore starts a new evidence clock and reconstructs the public Binance
USD-M futures book prospectively.

## Frozen Inputs

Protocol:

`backend/research/binance_maker_conversion_v1/frozen_protocol.json`

Source model:

`data/research/polymarket_repricing_shadow_v1/event_model_bundle.joblib`

The source bundle remains research-only and contains 86 causal event-time
features for spot/perpetual aggregate-trade state. This campaign verifies:

- bundle SHA-256;
- bundle protocol SHA-256;
- feature-schema SHA-256;
- source E09/E10 protocol SHA-256;
- source training-dataset SHA-256 and chronological calibration cutoff when
  embedded by the event-bundle trainer;
- current Git commit plus a dirty-worktree marker;
- a hash of this package's Python source.

Every new candidate stores campaign and source protocol hashes, model artifact,
training dataset, cutoff, feature schema, code commit and dirty state. An older
event bundle without the newly embedded dataset/cutoff fields may continue in
this isolated research shadow, but its universal-ledger adapter fails closed
until the bundle is explicitly retrained. No automatic retraining occurs.

## Exact Candidate Rule

For each completed second and each horizon independently:

```text
horizon in {5 seconds, 15 seconds}
p_movement >= 0.50
abs(p_direction_up - 0.50) >= 0.10
side = LONG when p_direction_up >= 0.50, otherwise SHORT
```

After one candidate is accepted at a horizon, the next candidate at that same
horizon cannot occur until the prior horizon expires. This matches the frozen
E09/E10 `greedy_nonoverlap` rule.

The live decision is necessarily made after the feature second closes. The
recorded `decision_ts_ms` is the actual local decision time, not the historical
bar timestamp. Baseline decision latency is frozen at zero additional simulated
milliseconds; measured receive and exchange timestamps remain in the ledger.

## Execution Instrument

The execution question is tested on Binance USD-M `BTCUSDT` perpetual futures:

- spot aggregate trades feed the spot half of the event model;
- perpetual aggregate trades feed the perpetual half and queue consumption;
- perpetual diff depth plus a REST snapshot builds the executable local book.

The 2026 Binance base-URL split is respected: diff depth uses the `/public`
route and aggregate trades use `/market`. The legacy unrouted aggregate-trade
URL can connect without publishing events and is therefore not accepted.

The local-book algorithm follows Binance's published sequence contract:

1. open the diff-depth stream and buffer events;
2. fetch a 1,000-level REST snapshot;
3. discard obsolete events;
4. require the first retained event to overlap the snapshot update ID;
5. require every subsequent `pu` to equal the previous `u`;
6. rebuild immediately after any gap;
7. treat quantities as absolute and delete zero-quantity levels.

References:

- https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly
- https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect
- https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account#user-commission-rate

## Compared Policies

Every original candidate receives all five policy rows.

### A. Taker / Taker

Walk visible depth immediately to enter, then walk the opposite side at the
signal horizon to exit.

Purpose: executable cost baseline.

### B. Maker / Taker

Join the current best bid for LONG or best ask for SHORT for up to 1,000 ms. If
filled, exit as a taker at the signal horizon. If not filled, retain the
candidate with zero execution PnL and a missed-fill reason.

Purpose: measure whether spread saving survives missed opportunities and
adverse selection.

### C. Maker TTL, Then Taker Fallback / Taker

Join the best price for 1,000 ms. At expiry, cross the remaining quantity,
including after a partial maker fill. Exit as a taker at the original signal
horizon.

Purpose: the primary conversion candidate. It guarantees that the denominator
does not improve by silently dropping missed maker entries.

### D. Maker / Maker

Use the same passive entry, then attempt a passive exit for 1,000 ms at the
signal horizon. An unfilled exit is explicitly `UNWOUND_INCOMPLETE`; it receives
no fabricated PnL and blocks promotion.

Purpose: measure the optimistic spread-saving ceiling while exposing inventory
risk honestly.

### E. Toxicity-Gated Maker / Taker

This route is present for every candidate but is currently `SKIPPED` with
`labels_required_fail_closed`.

Purpose: reserve the predeclared comparison without training on invented fill
or toxicity labels. It can be enabled only in a new protocol after defensible
forward labels exist.

## Conservative Queue Model

Public market data does not reveal the exact queue position of a hypothetical
order. V1 therefore uses a deliberately conservative rule:

- displayed quantity at the selected level is placed ahead of the order;
- cancellations never count as fills;
- only correctly sided public aggregate trades at or through the order price
  consume queue;
- only trade quantity remaining after displayed queue consumption fills the
  hypothetical order;
- partial quantities remain partial;
- duplicate aggregate-trade IDs are ignored;
- aggregate-trade ID gaps are persisted as health events.

This is suitable for rejecting weak execution hypotheses. It is not proof that
a real account would receive the reconstructed fills.

## Evidence Ledger

Default database:

`data/research/binance_maker_conversion_v1/shadow.duckdb`

Tables:

| Table | Purpose |
|---|---|
| `campaign_meta` | frozen protocol and artifact identities |
| `health_events` | connect, disconnect, heartbeat, staleness, sequence gaps and clock drift |
| `candidates` | every original 5s/15s candidate and exact decision book |
| `routes` | same-denominator A-E execution state and economics |
| `queue_checkpoints` | 100/250/500/1000 ms queue progress |
| `post_fill_marks` | 100/250/500/1000 ms signed movement after fills |
| `candidate_book_checkpoints` | 0/250/500 ms and horizon depth/VWAP for latency and 2x-size stress |

Candidate identity is unique on `(decision_second, horizon_seconds)`. A restart
cannot duplicate it. Unresolved rows become `INTERRUPTED`, never silently
disappear.

The process emits a heartbeat every five seconds, records feed outages and
sequence gaps, reconnects each feed independently, logs clock drift and book
age, stops at the 10 GB database cap, and closes DuckDB gracefully.

## Fee Treatment

The protocol currently carries explicit research assumptions:

```text
maker fee: 2 bps per side
taker fee: 5 bps per side
```

These are not asserted to be the user's fees. Binance futures commission rates
are account-specific. `account_fee_verified` is frozen `false`, so promotion is
impossible until a new version records the verified account rate.

Mixed maker/fallback entries use exact component notionals and the corresponding
maker/taker fee rates.
Unfilled candidates contribute zero, rather than disappearing from expectancy.

## Reporting and Stress

Run:

```powershell
.\report_binance_maker_conversion_shadow.bat
```

The report calculates:

- original candidates and calendar coverage;
- LONG and SHORT counts;
- fill rate;
- expectancy per original candidate;
- profit factor;
- LONG and SHORT expectancy separately;
- day-block bootstrap lower 95% expectancy;
- best-week positive-profit concentration;
- final untouched 20% expectancy;
- incomplete/unresolved exposure;
- +250 ms and +500 ms taker latency;
- one-tick-worse primary-route economics;
- 2x taker size from exact visible-depth VWAP;
- 2x fee stress.

These are diagnostics until the frozen sample and duration gates are met.

## Promotion Gate

No policy is eligible unless all applicable checks pass:

```text
at least 1,000 original candidates
at least 56 calendar days
account-specific fees verified
all original candidates preserved
day-block expectancy lower 95% > 0
profit factor > 1.2
one-tick-worse result > 0
+250 ms latency result > 0
+500 ms latency result > 0
LONG expectancy > 0
SHORT expectancy > 0
best week <= 35% of positive profit
final untouched window > 0
no unresolved inventory exposure
```

The report always returns `NOT_ELIGIBLE` in V1. Promotion requires a separate
review and protocol version; it is never automatic.

## Commands

Validate without network:

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  backend\research\binance_maker_conversion_v1\live_shadow.py --selftest

& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  -m pytest backend\research\test_binance_maker_conversion.py -q
```

Start the long-running shadow:

```powershell
.\run_binance_maker_conversion_shadow.bat
```

The launcher restarts after an unexpected non-zero exit and exits normally
after a deliberate `Ctrl+C` or a bounded `--duration` run.

## Validation Completed

On 2026-07-28:

- Python compile: pass;
- Pyflakes: pass;
- deterministic tests: 4 passed;
- source artifact/protocol/schema self-test: pass;
- the first public-feed smoke exposed Binance's 2026 routed-URL migration:
  legacy perpetual aggregate-trade sockets connected but emitted no events;
- routed `/market` aggregate trades and `/public` depth were then enforced in
  code and protocol;
- final 75-second smoke: spot and perpetual events healthy, depth snapshot plus
  buffered diff bootstrap succeeded, sequence gaps `0`, feature score advanced,
  and health changed from warm-up `STALE` to `OK`;
- final smoke database: zero candidates, which is valid because the frozen
  probability gate did not fire during the short window;
- no source model, application ensemble, Polymarket campaign or trading route
  was modified.

## Interpretation

A positive gross 5s/15s direction result is not enough. The useful result from
this campaign is one of:

1. **Pass:** passive conversion remains positive after verified fees, missed
   fills, adverse selection, latency, size, both sides and time stability.
2. **Fail:** the direction signal is real but not economically tradable on
   Binance at this scale.
3. **Inconclusive:** data health, sample size, queue evidence or fee provenance
   is insufficient.

Until the locked gate says otherwise, the correct status is:

> Research-only execution evidence, not a profitable strategy and not a trade
> instruction.

## Related Frozen-Campaign Operational Note

`polymarket_repricing_shadow_v1/live_shadow.py` still declares the legacy
unrouted Binance futures aggregate-trade URL. Under Binance's July 2026 route
split, that socket may connect without publishing aggregate trades. V1 is
frozen, so this implementation deliberately did not edit it in place. Any
restart of repricing evidence collection must use an explicit V2 protocol/code
identity, the routed `/market` endpoint, a fresh evidence clock, and the
provenance fields required by this document.

## Ordered Next Work

1. Run this shadow continuously until both the 1,000-candidate and eight-week
   gates are met.
2. Record the user's verified Binance maker/taker commission rates in a new
   protocol version; do not overwrite V1.
3. Train fill and toxicity heads only after queue/fill labels are defensible,
   using a final untouched time window.
4. Create repricing V2 only when its evidence clock can be reset explicitly.
5. Keep complete-set arbitrage passive; zero-opportunity days remain valid
   evidence.
6. Test options-implied relative value as a separately preregistered campaign.
7. Reconsider deep execution models only after a simple execution policy passes
   after costs.
