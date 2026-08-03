# PREREG — BINANCE_MAKER_EXECUTION_V1

**Frozen `2026-08-03`, before any maker result was computed.** Hash checked in CI.

## Admission

DIAGNOSTIC under the Phase 6 freeze, by explicit operator instruction on `2026-08-03`. May not
promote a strategy, tune a threshold, or authorise capital.

## Question

`MULTIHORIZON_DIRECTION_V1` measured gross edges of **+0.97 to +1.97 bps** against a **14 bps**
taker round trip, across four horizons, seven pairs and two exchanges. Every horizon was
cost-dominated, and the conclusion was that refinement must attack **cost**, not signal.

> Does passive (maker) execution change that arithmetic — and how much of the saving survives
> adverse selection?

A maker fill avoids crossing the spread and pays a lower fee. It also fills precisely when
someone informed wants the other side. This test measures both halves against real book and
trade data.

## Data, and its hard limit

```
source     data/multi_venue.duckdb, venue_events
venue      binance_perp   symbol BTCUSDT
quotes     11,389,147 bookTicker events (bid, bid_size, ask, ask_size)
trades       775,982 aggTrade events (price, size, side)
span       2026-07-28 20:26 -> 2026-07-29 19:18 UTC  = 22.9 HOURS
```

**This is a single day.** Day-clustered inference is therefore impossible, and none is claimed.
Uncertainty is reported as an **hour-block bootstrap over 22 hourly blocks**, which is strictly
weaker: it cannot see day-to-day regime variation, and a one-day result can be unrepresentative
in either direction.

`recv_ts` is in **seconds**, not milliseconds. The loader must convert explicitly; the same unit
confusion previously sent 56,467 rows to 1970 in this repository.

Only **top-of-book** is recorded, so queue position is known only at level 1. Any order larger
than the visible best-level size has unknown standing, and such orders are excluded rather than
assumed to fill.

## Design — frozen

Passive orders are posted at the **best bid** (to buy) and **best ask** (to sell), one order per
decision instant, on a fixed grid of **one order every 60 seconds**, alternating side, so the
population is exogenous and not selected by any signal. This is an *execution* study, not a
strategy: entry timing carries no information by construction.

```
order life   60 seconds, then cancelled if unfilled
size         0.01 BTC, always below the visible best-level size (larger orders excluded)
```

## Five fill bounds — all reported, none selected

```
0  NO_FILL          never fills. Measures the cost of non-participation.
1  IMMEDIATE        fills instantly at the posted price, no queue, no adverse selection.
                    requires_hindsight_or_unrealistic_fill = TRUE. A CEILING, never a result.
2  TOUCH            fills if any trade prints at or through the posted price.
                    Still optimistic: a touch does not prove OUR order filled.
3  VOLUME_AHEAD     fills only when traded volume at or through the level exceeds the
                    visible size ahead at posting time, plus our own size.
4  OPERATIONAL      VOLUME_AHEAD plus a 250 ms submission latency (the book may move
                    before the order rests) and cancellation at expiry.
```

`OPERATIONAL` is the conservative bound and the only one that may inform a decision.

## Fees

```
maker fee   +1.0 bps   (charged; NOT assumed zero merely because taker fees disappear)
taker fee   +5.5 bps   for the comparison arm
```

## Adverse selection — measured, not assumed

Executable markout after every bounded fill at **1s / 5s / 15s / 30s / 60s**, signed by side,
using the mid at that offset.

## Primary endpoint

**Net value per order SUBMITTED**, in basis points, under bound 4 (`OPERATIONAL`), with an
hour-block bootstrap 95% CI.

Per *submitted*, not per filled. A strategy can earn on its fills and fill too rarely to matter,
and reporting per-fill would hide exactly that.

## Secondary endpoints

```
fill rate per bound
conditional markout after fill, per horizon
net value per order FILLED
adverse selection loss = markout at 60s minus markout at 1s
spread distribution and time-at-touch
implied cost saving vs the 14 bps taker round trip
```

## Verdicts — declared before results

```
MAKER_CHANGES_THE_ARITHMETIC
    OPERATIONAL net value per submitted order is positive with an hour-block
    95% CI excluding zero, AND the implied round-trip cost falls below the
    +1.97 bps best measured gross edge.

MAKER_SAVES_BUT_NOT_ENOUGH
    OPERATIONAL cost saving is real, but the implied round trip still exceeds
    the measured gross edge. Passive execution helps and does not close the gap.

MAKER_LOST_TO_ADVERSE_SELECTION
    Post-fill adverse selection is at least as large as the spread plus the
    taker-fee saving. Liquidity provision merely exchanges explicit costs for
    informed-flow losses.

MAKER_FILL_RATE_INSUFFICIENT
    OPERATIONAL fill rate is below 5%, so net value per submitted order is
    dominated by non-participation regardless of fill quality.
```

## Kill rules

```
bound 1 (IMMEDIATE) net <= 0                            close the lane outright
adverse selection >= spread + taker-fee saving          close the lane
OPERATIONAL fill rate < 5%                              insufficient participation
```

If the optimistic ceiling itself is unprofitable, no realistic fill model can rescue it.

## What this may not do

No threshold tuning, no alternative order sizes, no signal-conditioned entry, no re-posting
policy, and no reporting of bound 1 or 2 as achievable. Scoring on a single day may not be
presented as a forward result. Any change requires a new preregistration.

## Relationship to PREREG D

`PREREG_FORWARD_D_BOUNDED_MAKER_STUDY_V1` covers **Polymarket** and remains unscorable — its
precondition is a day-clustered surplus lower bound above zero, which is not met. This protocol
is a different venue, a different instrument and a different data source, and does not touch it.

## Stopping rule

Scored **once**.
