# PREREG — LIQUIDITY_VACUUM_CONTINUATION_V1

**Frozen `2026-08-04`, before any additional Bybit day is downloaded.** Hash checked in CI.

## Admission

DIAGNOSTIC under the Phase 6 freeze, by explicit operator instruction on `2026-08-04`. Zero
action authority. This protocol may not price, rank, size, or gate any live or paper decision.

## Why this is being frozen now

A single-day exploratory pass (`BYBIT_L2_DEPTH_HEADS_V1`, BTCUSDT 2026-08-02, 84,260 one-second
anchors) found that after top-of-book depth halves, price **continues** rather than reverting:

```text
P(continue | vacuum, replenished)   80.89%   (n = 12,021)
P(continue | unconditional)         50.04%   (n =  5,286)
efficiency ratio |net| / path       0.0176   near-pure chop, no trend to borrow from
```

That result is exploratory. It was measured before any hypothesis was written down, on one day,
with thresholds chosen while looking at the data. **It is a reason to preregister, not a finding.**
This document fixes the question and every parameter *before* more days exist, so the additional
data is a test rather than a search.

## Question

> After a liquidity vacuum at the top of the book, does the price move that occurred *during* the
> vacuum continue, at a rate materially above the unconditional base rate, on days not used to
> form the hypothesis?

## Frozen definitions

```text
venue / symbol      Bybit linear perpetual, BTCUSDT
book                200-level, sequenced, via research/bybit_l2_maker_v1.Book
anchor grid         one anchor per 1000 ms; sub-second anchors are not independent
top depth           min(best_bid_size, best_ask_size)
VACUUM              top depth at t+5s <= 0.50 * top depth at t
REPLENISHED         top depth returns to >= its value at t, within 60s of the vacuum
move_during         mid(t+5s) - mid(t)
move_after          mid(t+35s) - mid(t+5s)
CONTINUE            move_during * move_after > 0
anchors with move_during == 0 are EXCLUDED (sign undefined, not a coin flip)
```

## Primary endpoint

```text
lift = P(CONTINUE | vacuum, replenished) - P(CONTINUE | unconditional, same 5s->30s test)
```

Computed **per UTC day**, then aggregated by **day-block bootstrap**, 2000 resamples, seed
`20260804`. The unconditional baseline is computed on the same day, same grid, same exclusion rule
— an incumbent baseline, never an assumed 50%.

## Pre-declared thresholds

```text
PASS      day-block 95% lower bound on lift  >  +0.15   (15 percentage points)
          AND at least 5 qualifying days
          AND no single day contributes > 40% of vacuum episodes
FAIL      anything else
```

`+0.15` is chosen as roughly half the exploratory lift (30.85 pp). A result that survives at half
the discovered magnitude is not an artifact of the day it was found on. **A near miss is a miss.**

## Materiality, declared in advance

Statistical continuation is not economic value. Alongside the endpoint, report:

```text
median |move_after| in USD and bps
P(|move_after| >= $10 / $25 / $50)
the same, unconditional
```

The exploratory day gave `P($10 in 30s) = 37.84%` after a vacuum. At $63k, $10 is **1.6 bps**
against a ~2 bps Bybit maker round trip and a measured **-0.56 bps** adverse-selection cost on
passive fills (`BYBIT_L2_MAKER_V2_TRADE_DRIVEN`). **This protocol is expected to be statistically
positive and economically insufficient.** Recording that expectation now is the point: if it comes
out positive AND economically sufficient, that is a surprise requiring explanation, not a
vindication.

## Data

```text
hypothesis-forming day  2026-08-02   DESIGN_ONLY, excluded from every reported endpoint
test days               all other days downloaded after this file is frozen
source                  https://public.bybit.com/trading/BTCUSDT/  (trades)
                        Bybit historical OrderBook archive           (200-level L2)
```

The 2026-08-02 file may be replayed for code paths but **may not contribute a single row** to the
primary endpoint. It formed the hypothesis; it cannot also test it.

## Invariants

Reuses `Book` / `replay` / `ReplayInvalid` unchanged. A violated invariant — crossed book,
backwards update ID — produces **no number** for that segment. Segments are not repaired.

## Prohibitions

```text
no threshold search after seeing test days
no horizon search (5s vacuum window and 30s continuation window are FROZEN)
no swapping the 0.50 depth ratio
no dropping days that fail
no re-running with a different grid until the endpoint reads positive
no promotion to any action authority regardless of outcome
```

Changing any frozen parameter requires a new protocol name and a new hash. This one is then
recorded as answered.

## Scoring

Scored **once**, when at least 5 qualifying test days exist. The result is recorded whether it
passes or fails.
