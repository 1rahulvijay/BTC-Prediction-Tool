# PREREG — CROSSING_HEADS_V1

**Frozen `2026-08-03`, before any crossing head was trained.** Hash checked in CI.

## Admission

DIAGNOSTIC under the Phase 6 freeze, by explicit operator instruction on `2026-08-03`. May not
promote a strategy, tune a threshold, or authorise capital.

## Question

`polymarket_crossing_recorder` produced **15,428 labelled anchor crossings** over 5,738 rounds,
with measured base rates:

```
final crossing        37.2%
reverted within 30s   18.7%
reverted within 60s   30.1%
```

> Can a model predict whether a crossing is **final** or **reverts**, beyond what the time
> remaining in the round already tells you?

## The incumbent is TIME REMAINING, not a constant

A crossing with 10 seconds left is more likely to be final than one with 4 minutes left, for a
purely mechanical reason: there is less time in which to undo it. Any head that merely
rediscovers this has added nothing.

`REGIME_VOLATILITY_CONTROL_V1` retired a taxonomy that looked separable until 84% of it turned
out to be current volatility. The same discipline applies here in a different costume.

```
BASELINE_CONSTANT     the base rate
BASELINE_TIME         a model on seconds_left ALONE - the incumbent this must beat
CANDIDATE             round state + causally joined market state
```

## Targets — three, all reported

```
is_final_crossing   base rate 37.2%   n = 15,428
reverted_30s        base rate 18.7%   n = 14,844
reverted_60s        base rate 30.1%   n = 14,273
```

The 5s and 15s horizons are **excluded**: `round_state_snapshots` samples every ~15 seconds, so
those labels are almost never resolvable (6 cases at 15s, none at 5s). Training on 6 rows would
be theatre.

## Features — frozen

```
round state    seconds_left, horizon_min, crossing_index, move_at_crossing,
               from_side, elapsed_fraction
market state   rv_15m, rv_60m, compression_ratio, vpin_15m, cvd_5m, delta,
               large_trade_imbalance, shock_magnitude
```

Market state is joined from `research_matrix_1m` **as of the last 1-minute bar that had CLOSED
before the crossing** — the same causal join rule fixed in `train_round_state_heads`, where the
containing bar leaked 44 seconds of future into every row.

Labels may see the future. Features may not. Asserted in the selftest.

## Split

```
chronological by DAY, train 70% / test 30% of days, no shuffling
no purge needed: a crossing's label resolves within 60s, and days do not overlap
test scored ONCE
```

Splitting by **day** rather than by row keeps all crossings from one round on one side. Rows
from the same round are not independent, and a row split would leak.

## Null floor

Per target, 200 replications with labels shuffled in whole-day blocks. On ~4,500 test rows an
AUC of 0.52 may or may not be meaningful, and the floor is what decides.

## Primary endpoint

**Test AUC of `CANDIDATE` on `is_final_crossing`**, versus `BASELINE_TIME`, with a day-block
bootstrap 95% CI on the **difference**.

## Verdicts — declared before results, per target

```
CROSSING_HEAD_ADDS
    CANDIDATE beats BASELINE_TIME by >= 0.02 AUC with a day-block CI on the
    difference excluding zero.

CROSSING_IS_TIME_REMAINING
    The gain is statistically present but below 0.02 AUC, or its CI spans zero.
    The head restates the clock.

CROSSING_NOT_PREDICTABLE
    CANDIDATE does not exceed the null floor.
```

`0.02` is the declared materiality bar, set before any result. Significance on 4,500 rows is
cheap; the question is whether the gain could change a decision.

## Kill rule

If all three targets return `CROSSING_IS_TIME_REMAINING` or worse, crossing prediction on this
feature set is closed, and the remaining hypothesis is finer-cadence forward data — which is
also what the 5s and 15s horizons need.

## What this may not do

No threshold tuning, no feature search, no alternative horizons, no resampling of the split, and
no promotion of any head to a trading signal. A base rate is a prior, not a result.

This produces **no position and no action**. A well-calibrated crossing probability is an input
to a decision, and every decision lane measured in this repository is currently closed on cost.

## Stopping rule

Scored **once**.
