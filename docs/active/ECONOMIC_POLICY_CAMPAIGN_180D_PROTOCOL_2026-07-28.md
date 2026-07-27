# Economic Policy Campaign 180-Day Protocol

Status: **FROZEN BEFORE THE FULL RUN**

Date: 2026-07-28

Purpose: make one bounded, standalone attempt to produce an economically useful
LONG, SHORT, or ACT/SKIP shadow candidate without retuning the failed 120-day
experiments.

## Important Boundary

The instruction "test until something is promotable" is interpreted as:

```text
run this finite declared family;
score the locked test once;
stop whether the result passes or fails.
```

It does not authorize changing thresholds after results, repeatedly searching
the same history, or presenting a historical pass as live profitability.

## Data

The campaign uses the **earliest complete 180 days** in
`data/research_matrix_1m.parquet`. The recent economic experiments used the
latest 120 days, so this is a distinct older era.

```text
Base model training     first 120 days
ACT/SKIP training       next 15 days
Policy selection        next 15 days
Locked historical test final 30 days
Purge                   one full forecast horizon
Decisions               non-overlapping 5m and 15m timestamps
Round-trip cost         12 bps
```

Base models remain fixed after the first 120 days. This prevents the selected
policy from silently changing before the locked test.

## Declared Models

Direct LONG-profitable and SHORT-profitable classifiers:

```text
Logistic Regression
HistGradientBoosting
ExtraTrees
XGBoost
LightGBM
CatBoost
mean ensemble
```

Side-specific expected-net regressors use Ridge plus the five tree families.
Side-specific 20th-percentile net-return models use HistGB, LightGBM and
CatBoost. ACT/SKIP uses Logistic Regression and HistGB trained only on the
dedicated ACT period.

## Finite Policy Catalog

The selection period evaluates a closed catalog:

```text
direct profitable probability thresholds  0.30 / 0.40 / 0.50 / 0.60
LONG-vs-SHORT probability gaps             0.00 / 0.05 / 0.10
expected-net thresholds                    0 / 2 / 4 / 6 / 8 bps
q20 net thresholds                         0 / 2 / 4 bps
ACT probabilities                          0.50 / 0.60 / 0.70
side modes                                 BOTH / LONG only / SHORT only
```

With all declared seats this is **411 policy configurations per horizon,
822 total**. The locked test still sees only the single selection-period winner
for each horizon.

One policy per horizon is selected by day-block lower bound, then mean net
value and profit factor, subject to minimum selection coverage. Only that
policy is scored on the locked test.

## Locked-Test Shadow Gate

Every condition is required:

```text
mean post-cost return > 0
day-block 95% lower bound > 0
profit factor > 1.10
at least 100 independent trades
at least 1% coverage
at least four positive calendar weeks
final week positive
positive after 50% higher slippage
no week contributes at least 50% of positive profit
Benjamini-Hochberg q <= 0.10 across the two horizon winners
```

A pass creates only a **historical shadow candidate**. Forward paper promotion
still requires at least 500 independent trades and eight weeks under a frozen
artifact.

## Dynamic Exit

HOLD is the champion. For the selected entry population only, one new
challenger predicts remaining signed return at fixed checkpoints:

```text
5m checkpoints   1 / 2 / 3 / 4 minutes
15m checkpoints  3 / 6 / 9 / 12 minutes
models           Ridge + HistGB mean
exit             first checkpoint with predicted remaining return <= -2 bps
```

The challenger uses only information observed through the checkpoint. It must
have positive absolute economics and a positive paired day-block lower bound
versus HOLD. It cannot pass if its entry policy fails.

This does not reopen `CONDITIONAL_STOPPING_V1`, which remains closed for the
Polymarket execution problem. It is a separate Binance close-to-close
historical diagnostic.

## Outputs

```text
manifest.json
model_metrics.csv
selection_catalog.csv
locked_test_predictions.csv/parquet
dynamic_exit_predictions.csv (when testable)
run.log
```

No trained artifact is saved or loaded by the application.
