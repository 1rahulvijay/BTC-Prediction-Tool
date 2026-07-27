# Conditional EV 120-Day Experiment

Status before run: **FROZEN RESEARCH DESIGN**

## Question

Can the earlier LONG/SHORT profitability ranking be separated into:

1. probability that BTC moves far enough to clear costs;
2. direction conditional on such a move; and
3. a signed return distribution whose conservative bound is profitable?

## Fixed Inputs

```text
Window                    latest 120 days in research_matrix_1m.parquet
Horizons                  5m and 15m
Validation                four expanding 15-day test folds
Purge                     one full forecast horizon
Economic decisions        non-overlapping horizon-aligned timestamps
Fees                      5 bps per side
Slippage                  1 bps per side
Round-trip cost           12 bps
```

## Fixed Models

Magnitude and conditional direction:

```text
Logistic Regression
HistGradientBoosting
ExtraTrees
XGBoost
LightGBM
CatBoost
```

Signed return means:

```text
Ridge
HistGradientBoosting
ExtraTrees
XGBoost
LightGBM
CatBoost
```

Signed return quantiles at 10%, 50% and 90%:

```text
HistGradientBoosting
LightGBM
CatBoost
```

Every family is fitted sequentially and released before the next fit.

## Frozen Primary Policy

```text
P(move clears 12 bps) >= 0.50
P(chosen direction | move) >= 0.55

LONG:
    q10(signed return) > 12 bps

SHORT:
    -q90(signed return) > 12 bps
```

The adverse quantile is deliberate. Median and mean-EV versions are reported
as diagnostics but have no promotion authority.

## Promotion Gate

Every condition must pass:

```text
mean net bps > 0
day-block 95% lower bound > 0
profit factor > 1.10
at least 200 independent trades
at least 1% coverage
at least 3 of 4 folds positive
final fold positive
positive after 50% higher slippage
```

A near miss is a failure. Thresholds are not lowered after results.

## Run

```powershell
.\run_120d_conditional_ev_pipeline.bat
```

Outputs are isolated under `data/research/conditional_ev_120d/`. Nothing is
automatically promoted, and dynamic exit remains closed.
