# PREREG — SIDE_SPECIFIC_VACUUM_V1

**Frozen `2026-08-04`, before any event was counted.** Hash checked in CI.

## Admission

DIAGNOSTIC under the Phase 6 freeze, by explicit operator instruction. May not promote a
strategy, tune a threshold, or authorise capital.

## Question

The earlier vacuum study used `min(bid_depth, ask_depth)`, which discards direction. It reported
that a $10 move within 30s rose from 27.70% to 37.84% after a vacuum.

> When depth is withdrawn from **one side**, does price subsequently move **in that side's
> direction**, by enough to clear the cost of trading it?

## Two things this protocol must not do, both of which the prior result did

**1. Report a move that cannot pay for itself.** BTC in this archive trades near **$63,000**.
Bybit linear taker is 5.5 bps per side, so a round trip costs **11 bps ~= $70**. Therefore:

```
$10  = 1.59 bps    far below cost
$25  = 3.97 bps    far below cost
$50  = 7.94 bps    below cost
$70  = 11.1 bps    ~= COST-CLEARING
$100 = 15.9 bps    clears cost
```

The 27.70% -> 37.84% lift was measured on a move roughly **7x too small to pay for the trade**.
The primary endpoint here is therefore declared at the cost-clearing threshold, not at $10.

**2. Predict a move that has already happened.** Depth in a band collapses partly *because* price
moved through it. Measuring "vacuum" after a move and then predicting continuation is circular,
and it is the most likely explanation of any naive positive result. Handled by `VACUUM_QUIET`
below.

## Definitions — frozen

Book rebuilt from snapshot + deltas in `u` order, invariants enforced (`ReplayInvalid`), sampled
on a **100 ms grid** (the feed updates ~10.7x/s, so 100 ms neither aliases nor oversamples).

```
mid                 (best_bid + best_ask) / 2
depth_bid(t, band)  cumulative size resting at prices within `band` bps BELOW the
                    anchor price, measured at time t
depth_ask(t, band)  cumulative size within `band` bps ABOVE the anchor price
band                PRIMARY 5 bps; robustness at 1, 2, 10, 20 bps
```

**The anchor is `mid(t - w)`, the mid at the START of the window, held fixed.** A band anchored at
the moving mid would report a "vacuum" whenever price simply walked away from resting liquidity.
Fixing the anchor makes the measurement "liquidity left this price region", which is the claim.

### Vacuum events

```
w                    lookback window: PRIMARY 5s; robustness 1s, 15s
VACUUM_ANY   (side)  depth_side(t, 5bps) <= 0.50 * depth_side(t-w, 5bps)
VACUUM_QUIET (side)  VACUUM_ANY AND |mid(t) - mid(t-w)| <= $5
```

**`VACUUM_QUIET` is the primary event.** The $5 cap is 0.8 bps — one seventh of the smallest
burst threshold — so the depth is gone while price has *not yet* moved. `VACUUM_ANY` is scored
alongside it precisely to expose how much of any effect is the move that already happened.

### Burst

Signed **maximum excursion** from `mid(t)` over the next `H` seconds:

```
down_exc = mid(t) - min(mid over (t, t+H])
up_exc   = max(mid over (t, t+H]) - mid(t)
H        PRIMARY 30s; robustness 5s, 15s
```

Max excursion is a statistic of maxima and inflates absolute probabilities. That is tolerated
here **only because the primary endpoint is a difference of two probabilities computed
identically**, so the inflation cancels. No absolute hazard is given a verdict.

### Independence

Vacuum events cluster. Per side, an event is admitted only if no admitted event of that side
occurred within the preceding `H` seconds, so **test episodes never overlap** and each contributes
one observation.

## Primary endpoint — ONE cell, declared

```
band 5bps    w 5s    H 30s    threshold X = $70 (cost-clearing)

ASYM = 0.5 * [ P(down_exc >= X | BID_VACUUM_QUIET) - P(down_exc >= X | ASK_VACUUM_QUIET)
             + P(up_exc   >= X | ASK_VACUUM_QUIET) - P(up_exc   >= X | BID_VACUUM_QUIET) ]
```

`ASYM = 0` is the null **"vacuums precede volatility but carry no direction"** — which is what the
prior unsigned study could not distinguish from a directional effect. `ASYM > 0` means the burst
follows the side that emptied.

Comparing the two vacuum sides against each other, rather than against an unconditional baseline,
also removes any shared time-of-day, volatility, or regime confound: both arms are drawn from
vacuum states.

**Confidence interval: day-block bootstrap, resampling whole days.**

> **Declared power limitation.** The archive holds **6 days** (2026-07-28 .. 2026-08-02). Six
> blocks is a weak basis for a bootstrap interval and this is stated before any result is seen. A
> day-block interval that excludes zero on 6 blocks is *suggestive, not established*; the standard
> the operator set is several weeks, and this protocol does not meet it. Hour-block intervals are
> reported as a secondary and are known to understate autocorrelation.

## Secondary endpoints — no verdict attaches

```
ASYM over the full grid X in {10, 25, 50, 70, 100} x H in {5, 15, 30}s, Bonferroni-corrected
    across the 15 cells (99.67% intervals). Reported to show shape, not to find a winner.
the same grid under VACUUM_ANY, to quantify the reverse-causality contamination
unsigned hazard P(|move| >= X | vacuum) vs unconditional, comparable to the prior study
cancellation attribution: share of episodes where traded volume inside the band during the
    window is < 20% of the depth lost - i.e. liquidity CANCELLED rather than consumed
event counts per side per day, and the band/window robustness sweep
```

## Null floor

The whole procedure is re-run with each day's **price path circularly shifted** relative to its
event times, 200 draws. This preserves the event count, the clustering and the volatility
profile, and destroys only the alignment. The observed `ASYM` must lie outside the null band. A
null floor that is not centred near zero means the estimator is biased and the study is void.

## Verdicts — declared before results

```
SIDE_SPECIFIC_AND_COST_CLEARING
    Primary ASYM day-block CI excludes 0 at X = $70, H = 30s, and the null floor is clear.

SIDE_SPECIFIC_BUT_SUB_COST
    ASYM CI excludes 0 at some X < $70 but NOT at $70. Direction is real and too small to
    trade. This is the outcome the prior $10 result would have produced.

UNSIGNED_ONLY
    Vacuum raises the unsigned hazard but the ASYM CI includes 0 at every X. Depth withdrawal
    marks volatility, not direction.

NO_EFFECT
    Neither the unsigned hazard nor ASYM is distinguishable from baseline.

VOID
    Book invariants fail, the null floor is not centred near zero, or fewer than 200 admitted
    episodes on either side.
```

## What this may not do

No threshold may be moved after seeing a result. No calibration, no feature search, no model
fitting - this is a conditional-frequency measurement, and adding a learner would convert a
declared endpoint into a search. The cost model (11 bps round-trip taker) is declared here and
may not be revised downward to make a result clear it.

A positive result would establish a **conditional directional tendency on Bybit**, and nothing
about Binance or Polymarket. Tick size, fee schedule, participant mix and matching rules differ;
transfer requires its own forward test.

## Stopping rule

Scored **once**.
