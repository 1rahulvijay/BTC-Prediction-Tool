# PREREG — CONDITIONAL_DIRECTION_V1

**Frozen `2026-08-03`, before any conditional direction result.** Hash checked in CI.

## Admission

DIAGNOSTIC under the Phase 6 freeze, by explicit operator instruction on `2026-08-03`. May not
promote a strategy, tune a threshold, or authorise capital.

## Question

`TRADABILITY_HEAD_V1` found a movement gate that beats the volatility incumbent by +2.67 points
at the top decile on the 14 bps Binance hurdle.

> Inside the windows that gate selects, does a direction model produce **post-cost value** —
> and does it do better there than it does unconditionally?

Phase 5 measured direction AUC 0.87 and magnitude AUC 0.58, and no fixed rule beat `WAIT` after
costs. That combination is the whole reason this test exists: **sign predictability that does
not convert into profit is the repository's most repeated finding.** The endpoint here is
therefore realised value, not accuracy.

## Accuracy is a diagnostic, never the verdict

AUC and hit rate are reported. They cannot decide anything. A model can be right 60% of the time
and lose money if it is right on small moves and wrong on large ones, and that specific failure
is what an accuracy-based verdict would hide.

## Design — frozen

```
gate            TRADABILITY_HEAD_V1, retrained on train only, top decile of predicted movement
direction       LightGBM binary classifier, sign of the forward 15-bar return
features        the same 23 frozen backward-looking columns
split           chronological 70/30, purge 60 bars, test scored ONCE
```

### Trades do not overlap

The forward horizon is 15 bars, so consecutive selected bars describe the same move. One
position at a time: after an entry, the next 15 bars are skipped.

Counting overlapping windows as separate trades would inflate the sample several-fold and make
one lucky move look like fifteen. This is declared here because it changes the result, not
because it is a detail.

### Execution

```
entry     the NEXT bar's open after the signal bar
exit      15 bars later, at that bar's open
cost      14 bps round trip (taker 5.5 + slippage 1.5, both legs)
net_bps   side * forward_return_bps - 14
```

## Arms — all reported

```
GATED_DIRECTION        direction model, traded only in gated windows
UNCONDITIONAL          direction model, traded on every bar (non-overlapping)
GATED_RANDOM           random side, matched count, in the same gated windows
ALWAYS_FLAT            zero by construction
```

`GATED_RANDOM` is the control that matters: if gated windows are simply drifting, a random side
would also profit, and the direction model would be taking credit for a property of the window.

## Primary endpoint

**Mean net basis points per trade for `GATED_DIRECTION`,** with a day-block bootstrap 95% CI.

## Secondary endpoints

```
difference vs UNCONDITIONAL   day-block CI on the paired difference - does gating help?
difference vs GATED_RANDOM    day-block CI - is it the model or the window?
direction AUC and hit rate    gated and unconditional, DIAGNOSTIC ONLY
trade count and coverage      both arms
```

## Verdicts — declared before results

```
GATED_DIRECTION_PROFITABLE
    GATED_DIRECTION mean net bps has a day-block 95% CI whose LOWER bound
    exceeds zero, AND it beats GATED_RANDOM with a CI on the difference
    excluding zero.

GATING_HELPS_BUT_UNPROFITABLE
    GATED_DIRECTION beats UNCONDITIONAL with a CI on the difference excluding
    zero, but its own CI includes or lies below zero. The gate works; the
    system still does not pay.

GATING_ADDS_NOTHING
    No CI on any difference excludes zero. Gating changes nothing measurable.

DIRECTION_NOT_PREDICTABLE
    Direction AUC is within 0.02 of 0.50 in both arms.
```

Only `GATED_DIRECTION_PROFITABLE` would justify further work on this lane, and even then it
would establish a historical result, not a forward one.

## Kill rule

If `GATED_DIRECTION` does not beat `GATED_RANDOM`, the direction model contributes nothing and
the lane closes regardless of the sign of its raw return.

## What this may not do

No threshold tuning, no alternative gate coverage, no feature search, no alternative horizons,
no resampling of the split. Any of those requires a new preregistration.

Costs are not negotiable downward to make a result work. If 14 bps is too high for the lane, the
lane is too thin for the venue.

## Stopping rule

Scored **once**.
