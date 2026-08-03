# PREREG D — BOUNDED_MAKER_STUDY_V1 (test 165)

**Frozen `2026-08-02`, before the forward window opens.** Any edit invalidates every result
scored under it; the hash in `docs/active/PREREG_HASH.txt` is checked in CI.

## Question

Under realistic fill and adverse-selection bounds, can passive entry retain any of the observed
pre-cost midpoint surplus?

It must **not** answer *"would we have profited if every midpoint order filled?"* — that is the
optimistic ceiling already implied by test 164.

## The precondition, and it currently FAILS

Test 164 measured the surplus a maker fill would be trading on:

```
point estimate            +0.0044 / share
day-clustered 95% CI      [-0.0007, +0.0099]     SPANS ZERO
round-clustered 95% CI    [+0.0063, +0.0161]     excludes zero
```

Day clustering governs — volatility, regime and recorder health cluster within a day. **So the
surplus is not currently distinguishable from zero**, before any adverse selection is charged.

**This protocol may not be scored until the surplus is established on forward data with a
day-clustered lower bound above zero.** It is frozen now so that the design cannot be shaped by
the data that will decide it; it is not authorised to run today.

## One frozen opportunity population

Identical to test 164: same market-side selection, time buckets, eligibility, settlement labels
and fee model. Plus one frozen posting rule as the **primary** protocol:

```
PRIMARY:      POST_AT_BEST_BID
SENSITIVITY:  POST_ONE_TICK_INSIDE, POST_AT_FIXED_PRICE_OFFSET
```

## Fill bounds — all five reported, none selected

| bound | definition | status |
|---|---|---|
| **0 no-fill** | never fills; PnL 0, capital undeployed | measures non-participation |
| **1 immediate** | every post fills instantly, no queue, no adverse selection | `requires_hindsight_or_unrealistic_fill = true` — a **ceiling**, never a result |
| **2 touch** | fills when the market trades through the posted level | still optimistic: a visible touch does not prove *our* queue filled |
| **3 volume-ahead** | traded volume at the level must exceed visible volume ahead + cancellation reserve + our size | reported at several frozen volume-ahead assumptions |
| **4 operational** | submission and cancellation latency, partial fills, recorded maker fee/rebate rule, residual inventory, quote replacement, stale-order exposure | the conservative bound |

**Maker fees are not assumed zero** merely because taker fees disappear; the recorded fee rule
governs.

## Adverse selection — measured, not assumed

Executable markout after every bounded fill at **1s / 5s / 15s / 30s / 60s / settlement**.

Reported: fill rate, conditional markout after fill, **net value per order SUBMITTED**, net value
per order filled, adverse-selection loss, unfilled opportunity cost, partial-fill residual
exposure.

**The key quantity is net value per order submitted.** A strategy can earn on its fills and fill
too rarely to matter.

## Verdicts

```
MAKER_OPTIMISTIC_BOUND_NEGATIVE        even bound 1 is <= 0. Close the lane.
MAKER_ONLY_UNDER_UNREALISTIC_FILL      bound 1 positive, bounds 2-4 not. Close.
MAKER_COLLECTION_JUSTIFIED             conservative bound uncertain but plausibly positive.
MAKER_INFRASTRUCTURE_REVIEW_ELIGIBLE   requires ALL of the below.
```

`MAKER_INFRASTRUCTURE_REVIEW_ELIGIBLE` requires: conservative net value > 0; positive day-block
lower bound; sufficient independent days; positive value per **submitted** order; adverse
selection explicitly charged; partial fills represented; a minimum useful fill rate; capacity
measured.

## Kill rules — close immediately when any holds

```
optimistic (bound 1) net <= 0
required fill rate > feasible upper-bound fill rate
post-fill adverse selection >= spread + taker-fee saving
```

The last one would prove liquidity provision merely exchanges explicit costs for informed-flow
losses.

## Data

The 2-day `multi_venue` archive may test **schema, book reconstruction, fill arithmetic,
partial-fill accounting and latency handling only**. Economic scoring requires the newly
collecting paired Polymarket books and independent forward days.

## Stopping rule

Scored **once**, after its own endpoint-specific power gate passes and the precondition above is
satisfied.
