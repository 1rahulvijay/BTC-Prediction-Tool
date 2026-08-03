# PREREG — MULTIHORIZON_DIRECTION_V1

**Frozen `2026-08-03`, before any analysis of the multi-horizon dataset.** Hash checked in CI.

## Admission

DIAGNOSTIC under the Phase 6 freeze, by explicit operator instruction on `2026-08-03`. May not
promote a strategy, tune a threshold, or authorise capital.

`DIRECTION_ENSEMBLE_V1` closed the **model-family** search and named the two surviving
hypotheses: a **different horizon** and **different information**. This protocol tests both at
once, which is the only remaining move that is not a repetition.

## Question

> At 60, 240, 300 and 600 minutes, across seven pairs and two exchanges, with open interest,
> funding and cross-exchange features — is direction predictable enough to pay costs?

## What is new relative to every previous test

```
horizon        60 / 240 / 300 / 600 minutes   (previous work: 15 minutes only)
pairs          BTC ETH SOL BNB XRP AVAX LINK  (previous: BTC only)
exchanges      Bybit + Binance                (previous: one)
perpetual      real open interest, real funding (previous: absent from the archive)
cross-section  relative strength vs BTC, cross-pair dispersion (previous: none)
window         180 days                       (previous: 40)
```

## Power, computed before the result

Independent (non-overlapping) windows available across seven pairs over 180 days:

```
 60m   30,240        ample
240m    7,560        adequate
300m    6,048        adequate
600m    3,024        the binding constraint
```

The 600-minute arm is the weakest and is expected to have the widest interval. It is reported
regardless of what it shows; a horizon is not dropped for being inconvenient.

## Multiplicity, declared

Four horizons are tested. The primary endpoint is evaluated with a **Bonferroni-corrected
alpha of 0.05 / 4 = 0.0125**, implemented as a **98.75% day-block interval** rather than 95%.

Testing four horizons and reporting the best at 95% would be a search over horizons. Nine model
arms per horizon are reported as diagnostics and do not multiply the correction, because only
`SOFT_VOTE` decides.

## Feature families — frozen

```
price/vol       returns and realised volatility at 1/4/16/40 bars, ATR, range position
structure       compression, high/low distance, consecutive-direction runs
flow            volume, turnover, volume acceleration, taker imbalance proxy
perpetual       open interest level, OI change 1h/24h, OI-price divergence sign,
                funding level, funding change, funding-vs-return sign
cross-exchange  Bybit-Binance basis in bps, basis change, relative volume
cross-section   return rank vs the other six pairs, dispersion, distance from BTC return
calendar        hour of day, day of week
```

Every feature is backward-looking at the bar's close. The label may see the future; features may
not. Forbidden columns are asserted absent in the selftest.

## Models — the same seven families and voting

Identical to `DIRECTION_ENSEMBLE_V1` so results are comparable: LightGBM, XGBoost,
RandomForest, ExtraTrees, LogisticRegression, MLP, GaussianNB, plus `HARD_VOTE` and
`SOFT_VOTE`. `SOFT_VOTE` is the declared primary.

## Split

```
chronological     train 30 days / test the remainder, walking forward in 30-day blocks
purge             one full horizon between train and test in every block
test              scored ONCE
```

Walk-forward is used rather than a single split because 180 days spans multiple regimes, and a
single 70/30 cut would score one regime and call it out of sample.

## Primary endpoint

**Post-cost net basis points per non-overlapping trade for `SOFT_VOTE`, per horizon**, with a
day-block bootstrap **98.75%** CI.

```
cost   14 bps round trip, charged on every trade at every horizon
```

Cost does not scale with horizon; a 600-minute trade pays the same round trip as a 60-minute
one. This is the mechanism by which longer horizons could succeed where 15 minutes failed, and
it is the reason to test them.

## Secondary endpoints

```
AUC per head and per vote, per horizon
null floor per horizon, labels shuffled in whole-day blocks, 200 replications
per-pair breakdown of SOFT_VOTE
implied gross edge = net + cost
```

## Verdicts — declared before results, per horizon

```
HORIZON_PROFITABLE
    SOFT_VOTE net bps has a 98.75% day-block CI whose lower bound exceeds zero.

HORIZON_SIGNAL_ONLY
    AUC above the null floor, but the net CI includes or lies below zero.

HORIZON_NO_SIGNAL
    AUC inside the null floor.
```

An overall verdict of `MULTIHORIZON_PROFITABLE` requires at least one horizon at
`HORIZON_PROFITABLE`. Otherwise `MULTIHORIZON_NO_TRADABLE_EDGE`.

## Kill rule

If no horizon reaches `HORIZON_PROFITABLE`, direction trading on this information set is closed
across all tested horizons, and no further horizon or model search may be run against it. The
remaining hypothesis would be order-book microstructure, which this archive does not contain.

## On refining the strategy while testing

Thresholds, features, horizons, pairs and models are fixed by this document. **Refinement after
seeing these results is a different study and needs a new hash.** Adjusting any of them now and
re-reporting would convert a frozen test into a search, which is precisely the failure this
repository has spent its governance preventing.

Everything measured is reported, including per-pair and per-horizon detail, so that a later
refinement can be designed from evidence — but it must be declared before it is scored.

## Stopping rule

Scored **once**.
