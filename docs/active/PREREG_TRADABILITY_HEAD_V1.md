# PREREG — TRADABILITY_HEAD_V1

**Frozen `2026-08-03`, before the head was trained.** Hash checked in CI.

## Admission

DIAGNOSTIC under the Phase 6 freeze, by explicit operator instruction on `2026-08-03`. May not
promote a strategy, tune a threshold, or authorise capital.

## Question

> Is there enough predicted movement in the next 15 minutes to be worth trading at all?

This is the movement gate that precedes direction. Phase 5 measured that **sign is predictable
and magnitude is not** (direction AUC 0.87 against magnitude AUC 0.58), and that every fixed
rule tested lost to `WAIT` after costs. A head that says *when not to trade* attacks that
directly.

## The baseline is the incumbent, not zero

`REGIME_VOLATILITY_CONTROL_V1` retired a taxonomy that looked separable until it was compared
against current realised volatility, which explained 84% of it. That lesson is built into this
protocol: the head is scored against **`rv_60m` alone**, not against a constant.

```
BASELINE_CONSTANT   the unconditional base rate
BASELINE_VOLATILITY a single-feature model on rv_60m - the incumbent this must beat
CANDIDATE           LightGBM on the frozen feature set below
```

A head that matches `BASELINE_VOLATILITY` has added nothing, however good its AUC looks.

## Target

`fwd_abs_bps` = absolute return over the next **15 one-minute bars**, in basis points.

## Cost hurdles — both reported

```
BINANCE_HURDLE      14 bps    taker 5.5 + slippage 1.5, both legs
POLYMARKET_HURDLE  149 bps    the measured spread + fee floor for a 5m round
```

Neither is tuned. Both come from measurements already in this repository.

## Frozen feature set

Backward-looking only. Everything is available at the decision bar's close.

```
volatility     rv_15m rv_30m rv_60m rv_term
structure      compression_ratio range_15m micro_range_15m shock_magnitude
flow           cvd_1m cvd_5m cvd_change delta large_trade_imbalance
toxicity       vpin_15m vpin_30m vpin_50m
activity       log_vol vol_accel count_accel_5m log_count
cross-venue    perp_spot_basis_bps funding_velocity cvd_divergence
```

**Explicitly excluded**, and asserted in the selftest: every `future_*` column,
`tradable_move_label`, `fail_fast_label`, `ret_5m`-derived forward quantities, and the target
itself. A feature set that can see the label is the defect this repository has retracted twice.

## Split

```
chronological     train 70% / test 30%, no shuffling
purge             60 bars, at least one feature window
test              scored ONCE
```

## Primary endpoint

**Top-decile hit rate.** Rank test bars by predicted movement, take the top 10%, and measure
the share whose realised move exceeds the hurdle.

Reported for the candidate, both baselines, and — the quantity that decides the verdict — the
**difference between candidate and `BASELINE_VOLATILITY`**, with a day-block bootstrap 95% CI
on that difference.

## Secondary endpoints

```
AUC                     candidate and volatility baseline, on P(move > hurdle)
decile monotonicity     realised hit rate by predicted decile
quantile calibration    q10 / q50 / q90 coverage of realised move
coverage-value curve    hit rate at 5%, 10%, 25%, 50% coverage
```

## Verdicts — declared before results

```
TRADABILITY_HEAD_ADDS
    Top-decile hit rate beats BASELINE_VOLATILITY by >= 2.0 percentage points,
    AND the day-block 95% CI on that difference excludes zero.

TRADABILITY_IS_VOLATILITY
    The CI on the difference spans zero, or the gain is < 2.0 points.
    The head restates the volatility model and is not built.

TRADABILITY_NOT_PREDICTABLE
    Neither the candidate nor BASELINE_VOLATILITY beats BASELINE_CONSTANT
    at the top decile. Movement is not predictable at this horizon.
```

`2.0` points is the declared materiality bar, set before any result. Significance on a hundred
thousand bars is cheap; the bar that matters is whether the gain is large enough to change a
decision.

## Kill rule

If `TRADABILITY_IS_VOLATILITY`, the head is not built as a separate model. The existing
volatility forecast already carries the information, and adding a second model that agrees with
it would create a false impression of independent confirmation.

## What this may not do

No threshold tuning, no feature search, no alternative horizons, no resampling of the split.
Any of those requires a new preregistration. It produces no direction forecast and no position.

Predicting movement is a **necessary** condition for a tradable round, never a sufficient one:
a large expected move with unpredictable sign is not an opportunity.

## Stopping rule

Scored **once**.
