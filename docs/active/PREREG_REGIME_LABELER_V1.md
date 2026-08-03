# PREREG — REGIME_LABELER_V1

**Frozen `2026-08-03`, before any regime result was computed.** Any edit invalidates every
result scored under it; the hash in `docs/active/PREREG_HASH.txt` is checked in CI.

## Admission

Phase 6 freezes new model families. This is admitted by explicit operator instruction on
`2026-08-03` as a **DIAGNOSTIC**: it may produce a verdict about separability and may not
promote a strategy, tune a threshold, or authorise capital.

## Question

Do the six declared market regimes have **distinguishable forward behaviour**, and can a model
assign them out of sample?

A Strategy Router routes between strategies conditional on regime. If regimes are not separable,
the router has nothing to route and must not be built. This test exists to be able to say no.

## Why the rules are frozen before results

Six classes, each with a rule and a threshold, is a large design space. Choosing regime
definitions after seeing which ones separate is a search, and it is how this repository
produced two retracted results. The definitions below are fixed now, in a hashed file, and the
implementation reads them exactly as written.

## The six regimes, in strict priority order — first match wins

Evaluated per 1-minute bar. Every input is available at the bar's close; nothing reads forward.

```
1  LIQUIDATION_SHOCK      abs(ret_5m) >= P99(abs ret_5m)  AND  rv_15m >= P90(rv_15m)
2  SHOCK_EXHAUSTION       a LIQUIDATION_SHOCK occurred in the previous 30 bars
                          AND abs(ret_5m) <= P50(abs ret_5m)
                          AND rv_15m >= P75(rv_15m)
3  THIN_LIQUIDITY         volume_60m <= P10(volume_60m)
4  COMPRESSION_EXPANDING  compression_ratio <= P25(compression_ratio)
                          AND rv_15m / rv_60m >= 1.20
5  TRENDING               efficiency >= 1.00  AND  rv_60m >= P50(rv_60m)
                          where efficiency = abs(sum of last 60 one-minute returns)
                                             / (rv_60m * sqrt(60))
6  RANGE                  everything else
```

Priority order is part of the specification. `LIQUIDATION_SHOCK` precedes everything because a
shock that is also thin or also trending is a shock. `RANGE` is the residual class, never a
positive assertion.

## Every percentile is estimated on TRAIN only

`P99`, `P90`, `P75`, `P50`, `P25`, `P10` are computed **on the training window and applied
unchanged to the test window**. Computing them over the full sample lets a test-set observation
influence its own label — the exact defect found in `edge_probe.py`, where a full-sample
percentile leaked test information into a "large trade" threshold.

## Split

```
chronological         train 70% / test 30%, no shuffling
purge                 60 bars between them, at least one feature window
test window           scored ONCE
```

## Primary endpoint

**Forward 15-minute absolute move, in basis points, by regime.**

Reported per regime as a mean with a **day-block bootstrap 95% CI**, because bars within a day
share regime and volatility and are not independent draws.

## Verdicts — declared before results

```
REGIME_SEPARABLE
    At least one PAIR of regimes has non-overlapping day-block 95% CIs on forward
    absolute move, AND the ordering of regime means is preserved between train and test.

REGIME_WEAKLY_SEPARABLE
    A pair separates on the point estimate but every CI overlaps. Not sufficient to
    build a router on.

REGIME_NOT_SEPARABLE
    No pair of regimes has non-overlapping CIs. The taxonomy does not describe
    distinguishable market states, and the Strategy Router is NOT built.
```

## Secondary endpoints, reported always

```
assignment stability   out-of-sample agreement between a LightGBM multiclass model
                       trained on the rule labels and the rules themselves
persistence            median dwell time per regime, in bars
population             share of bars per regime, train and test
```

A regime holding under 1% of bars is reported as **UNDERPOPULATED** and excluded from the
separability verdict: a class with a handful of bars cannot support a confidence interval, and
including it would let noise decide the verdict.

## Kill rules

```
no pair separates                         -> REGIME_NOT_SEPARABLE, router not built
regime ordering inverts train -> test     -> the taxonomy is unstable; router not built
a regime holds >80% of bars               -> the taxonomy is not partitioning anything
```

## What this test may not do

It may not tune the thresholds, add or remove a regime, change the priority order, or select
among alternative taxonomies. Any of those requires a new preregistration with its own hash.

It produces no trading signal, no direction forecast and no position. Separability is a
necessary condition for a router, not evidence that a router would be profitable.

## Data

`data/research_matrix_1m.parquet` — 360 days of 1-minute BTC bars. Regime rules use
`ret_5m`, `rv_15m`, `rv_60m`, `compression_ratio`, `volume` and `close` only. Open interest,
funding and book depth are deliberately excluded: they are absent from this archive, and a
regime taxonomy that cannot be computed from it would not be testable here.

## Stopping rule

Scored **once**, on the frozen split. A second look requires a new preregistration.
