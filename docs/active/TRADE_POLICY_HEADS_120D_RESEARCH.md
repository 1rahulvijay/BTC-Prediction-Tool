# 120-Day LONG, SHORT, and ACT/SKIP Research

## Purpose

`backend/research/train_120d_trade_policy_heads.py` is an isolated economic
research lane. It tests whether the available causal BTC features can identify:

1. LONG entries that finish profitable after costs.
2. SHORT entries that finish profitable after costs.
3. Which base-model candidate trades should be acted on or skipped.

It does not replace live models or enable real orders.

## Labels

For each 5-minute and 15-minute observation:

```text
long_net_bps  = future_return_bps - round_trip_cost_bps
short_net_bps = -future_return_bps - round_trip_cost_bps

LONG profitable  = long_net_bps > 0
SHORT profitable = short_net_bps > 0
ACT profitable   = selected candidate net_bps > 0
```

The default cost assumption matches the conservative Binance paper engine:

```text
5 bps fee per side
1 bps slippage per side
12 bps total round-trip cost
```

Funding can be supplied separately with `--funding-bps-per-trade`. It defaults
to zero because 5-minute and 15-minute positions rarely cross a funding event.

## Validation Design

The default 120-day run has four expanding folds:

```text
first 60 days -> next 15 days
first 75 days -> next 15 days
first 90 days -> next 15 days
first 105 days -> final 15 days
```

Every fold has an embargo equal to the forecast horizon. Base models are fitted
only on earlier rows. Economic scoring uses one aligned decision per horizon,
so 5-minute and 15-minute trades do not overlap.

The first fold seeds out-of-fold base predictions. ACT/SKIP models train only on
earlier out-of-fold candidate records and are evaluated on later folds. They
never train on in-sample base-model probabilities.

## Models

Base LONG and SHORT families:

```text
Logistic Regression
HistGradientBoosting
ExtraTrees
XGBoost
LightGBM
CatBoost
```

ACT/SKIP challengers:

```text
Logistic Regression
HistGradientBoosting
```

Models are fitted and released sequentially to stay within a 16 GB RAM budget.
The launcher uses all available rows in the 120-day slice; `--max-train-rows`
can be set explicitly only for a smaller diagnostic run.
The final research artifacts are stored inside the run directory and are marked
`research_only`; no production loader reads them.

## Outputs

Each timestamped run under `data/research/trade_policy_heads_120d/` contains:

| File | Meaning |
|---|---|
| `manifest.json` | Configuration, costs, source hash, features and skipped models |
| `metrics.csv` | Per-fold and pooled classification/economic metrics |
| `summary.csv` | Pooled headline metrics |
| `oof_predictions.csv` | Auditable out-of-fold predictions and realized labels |
| `oof_predictions.parquet` | Compact equivalent of the CSV when Parquet is available |
| `models/*.joblib` | Research-only full-window shadow artifacts |
| `run.log` | Timestamped progress |

## Interpretation

Promotion requires more than classification accuracy. A useful ACT/SKIP head
must improve post-cost mean net bps, profit factor and drawdown versus
`always_act`, retain adequate coverage, and remain stable across folds.
Because profitable short-horizon entries can be rare after costs, average
precision, balanced accuracy, Brier score and calibration error are reported;
raw accuracy alone is not a valid promotion metric.

The saved full-window models no longer have an untouched test set. They are
therefore shadow candidates only and require forward paper verification.

## Dynamic Exit Exclusion

No dynamic-exit model is trained. `CONDITIONAL_STOPPING_V1` tested seven causal
policies, all of which underperformed holding to settlement. Reopening that lane
requires fundamentally new causal execution information, such as sub-second L2
book state or a materially different fee/maker-cost structure.

## Commands

Full experiment:

```powershell
.\research\launchers\run_120d_trade_policy_heads.bat
```

Focused unit tests:

```powershell
python backend\research\test_120d_trade_policy_heads.py
```

Small real-data smoke run:

```powershell
python backend\research\train_120d_trade_policy_heads.py `
  --days 14 --horizons 5 --models logreg,histgb `
  --meta-models logreg --folds 2 --test-days 2 `
  --max-train-rows 5000 --no-save-models --run-name smoke
```

## Validation Recorded 2026-07-27

Deterministic validation:

```text
Python compilation                         PASS
Ruff static checks                         PASS
Label, purge and economic unit tests       3/3 PASS
Real-matrix end-to-end smoke               PASS
Research artifact save/reload              3/3 PASS
Existing Python platform suites            27/27 PASS
Windows start.bat validation               PASS
Vite production build                      PASS
npm high-severity audit                    0 vulnerabilities
```

The 14-day smoke used 20,161 contiguous one-minute rows and generated 1,151
non-overlapping 5-minute decisions. Under the conservative 12 bps round-trip
cost:

```text
LONG mean-ensemble AUC                     0.621
SHORT mean-ensemble AUC                    0.647
ACT/SKIP Logistic Regression AUC           0.601
Always-ACT mean net                        -12.06 bps/trade
Always-ACT profit factor                   0.033
ACT trades at frozen threshold 0.58        0
```

This is a plumbing and honesty check, not a performance claim. The correct
behavior on that short sample was to reject every candidate rather than lower
the threshold until trades appeared. Only the full 120-day run can provide the
requested research result, and even a positive historical result remains
shadow-only until independent forward paper evidence confirms it.
