# The fast BTC tick recorder — `2026-08-09`

`backend/btc_tick_recorder.py`. The smallest remaining piece of work in the whole sweep, and
the only thing standing between the repository and a sub-second cross-venue measurement.

---

## Why it exists, precisely

`research/cross_venue_repricing_lag.py` found no repricing lag at ≥2s and could not look below
it. Chasing that limit produced something more specific than *"we need a recorder"*:

```text
pm_l2_raw_events   272,274 book events   median inter-event gap    32.5ms
microstructure     198,036 rows          median inter-event gap  1,177ms   <- the BTC side
```

**A lead/lag measurement is limited by the slower series.** The Polymarket side was already
fast enough. The BTC reference was not. This closes that gap and nothing else.

## What it records

Two public Binance streams. No credentials, no orders, forward-only.

```text
btcusdt@bookTicker   every best bid/ask change - the price signal a Polymarket quote would
                     respond to, and the right reference for lead/lag
btcusdt@aggTrade     every aggregated trade - direction and size of the flow causing it
```

Schema mirrors `polymarket/l2_recorder.py` exactly — `seq` primary key resumed from `max()` on
restart, `recv_ts_ns` local nanoseconds, `exchange_ts_ms` from the venue — so the two stores
interleave into one ordered history without translation.

## Measured on a 60-second live capture

```text
book ticks 377   trades 97   gaps 0   dropped 0   disk 0.2MB

book cadence ms:  p05 0.0   median 74.2   p95 629.8   max 1,834
transport lag ms: min 0     median 0      max 0
```

```text
pm_l2 book events    median   32.5ms
btc_book_ticker      median   74.2ms     still the slower side, but 16x better
old microstructure   median 1,177.0ms
```

Joint resolution goes from ~1,177ms to ~74ms. That does **not** make BTC the faster side —
`pm_l2` is still ahead — it makes the **50–500ms band where latency arbitrage lives
measurable**, which 1,177ms sampling could not resolve at all.

Storage: ~0.2 MB/minute → roughly **290 MB/day** for both streams.

## The join is only valid because of the clock

`recv_ts_ns` is `time.time_ns()` on the recording host, which is exactly what
`polymarket/l2_recorder.py` writes. Two series can be compared at sub-second resolution **only
because they share that clock**. Recording them on different machines would make the join
meaningless while looking identical, so every run stamps `host`, `platform` and `clock_source`
into `btc_tick_runs`.

## What it refuses to do quietly

Every study in this repository that went wrong went wrong by treating absence as a value.

- **Drops are counted.** The socket reader never blocks on the database; a bounded 20k queue
  absorbs bursts and anything dropped is counted and written. An *uncounted* drop is a silent
  gap.
- **Heartbeats prove liveness** every 30s with the running counters, so "no rows this minute"
  and "the recorder was not running" stay distinguishable.
- **Reconnects are gaps** and are written as such, so the id evidence and the connection
  evidence must agree or something is wrong.

## A defect found by the first live run, and fixed

The first version applied id-continuity gap detection to **both** streams. The 45-second smoke
run reported **366 gaps and 3,549 "lost" messages**.

None of them existed. `bookTicker`'s `u` is the **order-book update id across all price
levels**, not a per-message counter — measured on that capture its step ran **min 1, median 4,
max 66**. Every "gap" was the book changing at a level other than the touch.

```text
bookTicker  u  step   min 1   median 4   max 66     <- NOT a message counter
aggTrade    a  step   min 1   median 1   max  1     <- a true per-message counter
```

So `aggTrade` id-continuity is a valid loss proof and is kept. `bookTicker` has no per-message
counter, so its coverage is measured by **silence** (>2,000ms without a message) instead. The
corrected run reports **0 gaps**.

This is the same defect class the audit series has been chasing: **a detector firing on a
property it does not measure.** It is worse than no detector, because a coverage claim gets
built on it — every later study would have carried a 3,549-message loss that never happened.

## Running it

```bash
python backend/btc_tick_recorder.py --seconds 60
```

```text
--db          destination (default data/btc_ticks.duckdb, or $BTC_TICK_DB)
--seconds     stop after N; omit to run until interrupted
--keep-raw    also persist the raw envelope of every message
--selftest    exercise the store and the gap/drop accounting, no network
```

To collect the data the sub-second study needs, run it **alongside**
`backend/polymarket/l2_recorder.py` on the same host. Neither writes to the other's database;
both are single-writer DuckDB.

## What this does not do

It does not answer the sub-second question — it makes the question answerable. The study
itself needs a capture window overlapping live BTC rounds, and `pm_l2`'s existing capture is
2026-07-02 to 07-04, so **new simultaneous capture is required**; the two existing stores do
not overlap at the needed resolution.

Nor does it change any lane verdict. Six of seven closed on execution economics or on the
absence of information, and none of those closures depended on sampling rate.
