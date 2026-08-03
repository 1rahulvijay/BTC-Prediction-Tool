# PREREG — BYBIT_L2_MAKER_V1

**Frozen `2026-08-03`, before any Bybit L2 result was computed.** Hash checked in CI.

## Admission

DIAGNOSTIC under the Phase 6 freeze, by explicit operator instruction on `2026-08-03`.

## Question

`BINANCE_MAKER_EXECUTION_V1` and `ALTCOIN_MAKER_EXECUTION_V1` both closed passive execution, but
both were limited to **top-of-book**: only the best bid/ask and its size were known, so queue
position beyond level 1 was invisible and orders larger than the visible best size had to be
excluded rather than modelled.

Bybit publishes **200-level sequenced order book** with update IDs.

> With real depth and a verifiable queue position, does passive execution look different from
> what top-of-book data implied?

## Why this source is different, and its acceptance evidence

```
host        quote-saver.bycsi.com/orderbook/linear/BTCUSDT/
coverage    1,293 daily files, 2023-01-18 -> 2026-08-02  (current to yesterday)
format      JSON lines: {topic, type: snapshot|delta, ts, data:{s,b,a,u,seq}}
levels      200 (earlier files 500)
size "0"    means REMOVE that price level
```

Acceptance checks run before this protocol was frozen, on `2026-08-02`:

```
records inspected            400,000
snapshots                    1 (at the start, as required for a replay)
update-id discontinuities    0
timestamp span               00:00:00 -> 11:12:25 UTC, milliseconds
```

Sequence continuity with zero gaps is what makes a faithful replay possible. Without update IDs
or reset markers a replay cannot be guaranteed, and this protocol would not have been written.

## Replay contract

The book is reconstructed from the opening snapshot and applied deltas in `u` order. The
reconstruction must satisfy, at every observation:

```
best bid < best ask
bid prices strictly descending, ask prices strictly ascending
all sizes > 0 after removals are applied
no update applied out of sequence
```

A violated invariant **stops the replay** rather than being repaired. A silently repaired book is
a fabricated book.

## Design — the same method, with real queue depth

Order placement, latency, fees, markouts and the bootstrap are **reused unmodified** from
`BINANCE_MAKER_EXECUTION_V1`, so any difference is attributable to the data, not the method.

```
orders        one per 60s, alternating side, posted at the best bid / best ask
size          0.01 BTC
life          60 seconds, 250 ms submission latency
maker fee     1.0 bps, CHARGED
markouts      1s / 5s / 15s / 30s / 60s
```

**What is new:** queue position is taken from the *reconstructed level's* true size, not from a
top-of-book proxy, and the `VOLUME_AHEAD` bound consumes that real queue.

## Fill bounds — all reported, none selected

```
0  NO_FILL
1  IMMEDIATE       requires_hindsight_or_unrealistic_fill = TRUE. A CEILING, never a result.
2  TOUCH           any trade at or through the posted price
3  VOLUME_AHEAD    traded volume must exceed the REAL queue ahead plus our size
4  OPERATIONAL     VOLUME_AHEAD plus 250 ms latency and cancellation at expiry
```

## Primary endpoint

**Net value per order SUBMITTED under `OPERATIONAL`**, with an hour-block 95% CI.

One day is one day: inference is hour-blocked and cannot see day-to-day variation. No
day-clustered claim is made.

## Secondary endpoints

```
median spread, and depth at the touch
fill rate per bound
adverse selection = gross(IMMEDIATE) - gross(OPERATIONAL)
comparison against the Binance top-of-book result:
    BTC 2026 adverse selection 1.526 bps, spread 0.02 bps
```

## Verdicts — declared before results

```
MAKER_VIABLE_WITH_REAL_DEPTH
    OPERATIONAL net per submitted order positive with an hour-block 95% CI
    excluding zero.

MAKER_LOST_TO_ADVERSE_SELECTION
    Adverse selection >= half the median spread plus the taker-fee saving, or
    the IMMEDIATE ceiling is itself negative.

MAKER_FILL_RATE_INSUFFICIENT
    OPERATIONAL fill rate below 5%.

MAKER_SAVES_BUT_NOT_ENOUGH
    Otherwise.

REPLAY_INVALID
    A book invariant failed. No economic verdict is issued - a broken replay
    must not produce a number.
```

## Kill rule

If real depth produces the same conclusion as top-of-book, passive execution is closed on
perpetual venues on the strongest available evidence, and the remaining hypothesis is a
structurally different venue rather than better data.

## What this may not do

No threshold tuning, no alternative order sizes, no signal-conditioned entry, no day selection
after results. One day may not be presented as a forward claim.

## Stopping rule

Scored **once**.
