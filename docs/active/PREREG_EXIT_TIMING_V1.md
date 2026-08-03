# PREREG — EXIT_TIMING_V1

**Frozen `2026-08-03`, before any exit-timing result.** Hash checked in CI.

## Admission

DIAGNOSTIC under the Phase 6 freeze, by explicit operator instruction on `2026-08-03`. May not
promote a strategy, tune a threshold, or authorise capital.

## Question

> Given a position **already open**, does a learned exit policy beat holding to a fixed horizon,
> after costs — and does it beat exiting at random?

This is the last lane in this repository with a large *measured* ceiling. Earlier work found
perfect-exit ceilings of +0.1005/share on Polymarket and +19/+30 bps on Binance at 60/120
minutes, and two pre-declared rules captured none of it. Every other lane has been closed:
entry direction is unpredictable (AUC 0.498), the movement gate has nothing to gate, and regime
separability was 84% volatility.

## Entries are RANDOM, and that is the point

Positions are opened at **random bars with random sides**. This test may not choose when or
which way to enter.

`CONDITIONAL_DIRECTION_V1` measured entry direction at AUC 0.498 — a coin flip. If entries were
model-chosen here, any result would confound exit skill with entry skill, and entry skill is
already known to be absent. Randomising entry isolates the only question being asked: **once
capital is committed, is there value in when it is released?**

A consequence, stated in advance: the expected value of a random entry is negative by the cost.
The candidate is therefore not expected to be profitable in absolute terms, and profitability is
**not** the verdict. The verdict is whether exit timing improves on the alternatives.

## Design — frozen

```
entries          2,000 random (bar, side) pairs in the test window, seed 71
                 no two entries overlap; a position closes before the next opens
max hold         240 bars (4 hours), matching the 60-240m opportunity work
decision         every bar while open: HOLD or EXIT
cost             14 bps round trip, charged once per position in every arm
split            chronological 70/30, purge 60 bars, test scored ONCE
```

Cost is identical across arms by construction — every arm enters and exits exactly once — so
the comparison is purely about *where* the exit lands, not about how often it trades.

## Arms

```
CANDIDATE           learned policy: exit when P(exit now beats holding) >= 0.5
HOLD_TO_HORIZON     hold all 240 bars, always. The champion to beat.
RANDOM_EXIT         exit at a uniformly random bar, matched count
TRAILING_STOP       exit on a 50 bps retracement from peak unrealised
ORACLE_BEST_EXIT    the best exit available in hindsight - a CEILING, never selectable
```

`ORACLE_BEST_EXIT` carries `requires_hindsight = True`. It is reported to size the opportunity
and may never be presented as an achievable result.

## Training target

For each open-position bar, the label is whether **exiting now beats holding to the horizon**:

```
label = 1 if unrealised_now > unrealised_at_horizon else 0
```

Labels may use the future — they are labels. **Features may not.** The feature set is the 23
frozen backward-looking columns plus position state known at that instant:

```
unrealised_bps    bars_held    side    mfe_bps    mae_bps
```

## Primary endpoint

**Mean net basis points per position for `CANDIDATE`**, with a day-block bootstrap 95% CI, and
the paired differences against `HOLD_TO_HORIZON` and `RANDOM_EXIT`.

## Verdicts — declared before results

```
EXIT_TIMING_ADDS
    CANDIDATE beats HOLD_TO_HORIZON with a day-block CI on the difference
    excluding zero, AND beats RANDOM_EXIT on the same basis.

EXIT_TIMING_IS_RANDOM
    CANDIDATE beats HOLD_TO_HORIZON but not RANDOM_EXIT. Exiting early helps;
    choosing WHEN does not. The value is in the horizon, not the policy.

EXIT_TIMING_ADDS_NOTHING
    CANDIDATE does not beat HOLD_TO_HORIZON.

EXIT_CEILING_UNREACHABLE
    ORACLE_BEST_EXIT is large but no non-hindsight arm captures a
    statistically distinguishable share of it.
```

`EXIT_TIMING_IS_RANDOM` exists because it is the most likely way to be fooled here: shortening
average holding time changes the risk profile, and that alone can move the mean without any
timing skill. `RANDOM_EXIT` is matched on count precisely to detect it.

## Kill rule

If `CANDIDATE` does not beat `RANDOM_EXIT`, the learned policy contributes nothing and the lane
closes, regardless of how it compares to holding.

## What this may not do

No threshold tuning, no alternative horizons, no feature search, no entry selection, no
resampling of the split, and no reporting of `ORACLE_BEST_EXIT` as achievable. Any of those
requires a new preregistration.

## Stopping rule

Scored **once**.
