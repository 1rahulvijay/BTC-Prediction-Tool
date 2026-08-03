# PREREG — REGIME_VOLATILITY_CONTROL_V1

**Frozen `2026-08-03`, before any conditional result was computed.** The hash in
`docs/active/PREREG_HASH.txt` is checked in CI.

## Admission

DIAGNOSTIC under the Phase 6 freeze, by explicit operator instruction on `2026-08-03`. It may
not promote a strategy, tune a threshold, or authorise capital.

## Question

`REGIME_LABELER_V1` found `THIN_LIQUIDITY < RANGE < TRENDING` on forward 15-minute absolute
move, stably across a chronological split.

But `TRENDING` requires `rv_60m ≥ P50`, `THIN_LIQUIDITY` selects low volume which correlates
with low volatility, and the endpoint **is** forward volatility. So the finding may restate
volatility clustering, already measured in Phase 5C (AR(1) half-life ≈ 34 minutes).

> **Does regime separate forward absolute move after conditioning on current realised
> volatility?**

If it does not, the Strategy Router would be routing on a quantity the system already predicts,
and it must not be built on this basis.

## Design — frozen

Bars are stratified by **current `rv_60m`**, using **decile edges fitted on the TRAIN window
only** and applied unchanged to test. Fitting edges on the full sample would let a test bar help
define the stratum that contains it.

Within each decile, the mean forward absolute move is computed per regime. The quantity of
interest is the **within-stratum gap**:

```
gap_d(A, B) = mean(fwd_abs | regime = A, decile = d)
            - mean(fwd_abs | regime = B, decile = d)

pooled gap  = equal-weighted mean of gap_d over deciles where BOTH regimes
              have at least MIN_CELL = 200 test bars
```

Equal decile weighting is declared, not chosen afterwards: opportunity weighting would let the
most populated volatility band dominate, and that band is the one the regimes least distinguish.

Uncertainty is a **day-block bootstrap**: whole days are resampled and the entire stratified
statistic is recomputed each iteration. Bars within a day share both regime and volatility, so a
per-bar interval would be far too narrow.

## Pairs tested

Only the pairs that separated unconditionally, so this is a control on an existing finding
rather than a fresh search:

```
TRENDING       vs  RANGE            unconditional gap  +6.9 bps
RANGE          vs  THIN_LIQUIDITY   unconditional gap  +5.7 bps
TRENDING       vs  THIN_LIQUIDITY   unconditional gap +12.6 bps
```

No other pair may be examined under this protocol.

## Primary endpoint

The **pooled within-stratum gap** and its day-block 95% CI, per pair.

## Shrinkage

```
shrinkage = pooled within-stratum gap / unconditional gap
```

A shrinkage near 1.0 means volatility explained little. Near 0.0 means the unconditional
separation was volatility.

## Verdicts — declared before results

```
REGIME_ADDS_BEYOND_VOLATILITY
    At least one pair has a pooled within-stratum gap whose day-block 95% CI
    excludes zero, AND shrinkage >= 0.25.

REGIME_ADDS_WEAKLY
    A pair's CI excludes zero but shrinkage < 0.25. Statistically present,
    materially mostly volatility.

REGIME_IS_VOLATILITY_RESTATED
    Every pair's pooled CI spans zero. The unconditional separation does not
    survive conditioning, and the router is not built on regime.

REGIME_CONTROL_UNDERPOWERED
    Fewer than 4 deciles have MIN_CELL bars in both regimes for every pair.
    No verdict is issued - an underpowered control must not read as a pass.
```

`0.25` is the declared materiality bar: below it, three quarters of the apparent regime effect
was volatility, and calling that "regime information" would be a description of nothing.

## Kill rule

If `REGIME_IS_VOLATILITY_RESTATED`, the Strategy Router is not built on this taxonomy. The
correct next step would be a regime definition that does **not** use volatility thresholds, with
its own preregistration.

## Data and reuse

Same archive, same regime labels, same chronological split and purge as
`PREREG_REGIME_LABELER_V1`. The regime labels are consumed exactly as that protocol produced
them; no threshold, priority order or class definition may be altered.

This is a second look at the same test window, and it is declared as such: it asks a different
question of the same data, with its endpoint and verdicts fixed in advance. It does not re-score
the original separability claim.

## Stopping rule

Scored **once**.
