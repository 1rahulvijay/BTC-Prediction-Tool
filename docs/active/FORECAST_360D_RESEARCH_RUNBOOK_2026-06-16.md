# 360-Day BTC Multi-Target Forecasting Research Runbook

Date: 2026-06-16

Purpose: test whether 360 days of Binance BTCUSDT 1-minute data improves short-horizon BTC forecasting for 5-minute and 15-minute targets.

This is a separate research lane. It does not modify:

```text
live backend decision logic
Polymarket recorder
frozen/champion live models
DuckDB live state
frontend app behavior
```

## Files Added

```text
backend/research/train_360d_multitarget_forecaster.py
research\launchers\run_360d_multitarget_forecaster.bat
docs/active/FORECAST_360D_RESEARCH_RUNBOOK_2026-06-16.md
```

## One-Command Run

From the project root:

```powershell
.\research\launchers\run_360d_multitarget_forecaster.bat
```

It opens a separate terminal and writes logs to:

```text
data/logs/forecast_360d_multitarget.log
```

Monitor the run:

```powershell
Get-Content data\logs\forecast_360d_multitarget.log -Wait
```

## Direct Commands

Fast smoke test:

```powershell
python backend\research\train_360d_multitarget_forecaster.py --days 30 --models ridge,histgb,lstm --smoke --horizons 5 15
```

Full tabular plus quantile research run:

```powershell
python backend\research\train_360d_multitarget_forecaster.py --days 360 --horizons 5 15 --models core --device gpu --max-features 180 --n-jobs 2
```

Optional sequence run after the tabular run:

```powershell
python backend\research\train_360d_multitarget_forecaster.py --days 360 --horizons 5 15 --models lstm,gru,tcn --include-sequence --sequence-targets core --seq-max-features 48 --seq-max-rows 120000
```

Do not run every sequence target first on a 16 GB laptop. Start with `--sequence-targets core`; use `--sequence-targets all` only after the core run proves useful.

## GPU Notes

The script supports:

```text
--device auto   default; PyTorch uses CUDA if available, tree models stay conservative
--device gpu    request GPU for XGBoost, LightGBM, CatBoost, and PyTorch sequence models
--gpu           shortcut for --device gpu
```

On the current machine, `nvidia-smi` sees the RTX 4050 and XGBoost, LightGBM, and CatBoost passed a small GPU fit test. Current PyTorch is CPU-only (`torch 2.12.0+cpu`), so LSTM/GRU/TCN/Transformer sequence models will still run on CPU until a CUDA-enabled PyTorch build is installed.

## Split Design

The script uses a strict chronological split:

```text
oldest 64% = model training
next 16%   = validation / conformal calibration
newest 20% = unseen final test
```

There is no random row split and no shuffling across time.

## Data Sources

The runner uses Binance public data:

```text
spot BTCUSDT 1m OHLCV
futures BTCUSDT 1m OHLCV
mark price klines
premium index klines
funding rates
```

Downloaded data is cached under:

```text
data/research/forecast_360d_cache/
```

## Targets

For each horizon:

```text
target_return_5m_bps
target_price_5m
target_high_5m_bps
target_low_5m_bps
target_range_5m_bps
target_log_volume_5m
target_direction_5m
target_big_move_5m

target_return_15m_bps
target_price_15m
target_high_15m_bps
target_low_15m_bps
target_range_15m_bps
target_log_volume_15m
target_direction_15m
target_big_move_15m
```

The product goal is not one magic price. The useful output is:

```text
expected close
expected high/low band
probability of UP/DOWN
probability of big move
expected volume
top-confidence opportunity quality
```

## Models

Regression models:

```text
Ridge
ElasticNet
HistGradientBoostingRegressor
RandomForestRegressor
ExtraTreesRegressor
LightGBMRegressor if installed
XGBoostRegressor if installed
CatBoostRegressor if installed
```

Classification models:

```text
LogisticRegression
HistGradientBoostingClassifier
RandomForestClassifier
ExtraTreesClassifier
LightGBMClassifier if installed
XGBoostClassifier if installed
CatBoostClassifier if installed
```

Quantile models:

```text
GradientBoostingRegressor quantile q10/q50/q90
LightGBM quantile if installed
split conformal widening using the calibration slice
```

Optional sequence models:

```text
LSTM
GRU
TCN
Transformer Encoder
```

## Baselines

A model only matters if it beats simple baselines on the newest unseen 20%.

Baselines include:

```text
train median
zero return
last return
current price
training base-rate probability
previous return sign
```

## Outputs

Predictions:

```text
data/research/forecast_360d_predictions.parquet
data/research/forecast_360d_predictions.csv
```

Metrics:

```text
data/research/forecast_360d_regression_metrics.csv
data/research/forecast_360d_classification_metrics.csv
data/research/forecast_360d_quantile_metrics.csv
data/research/forecast_360d_summary.csv
data/research/forecast_360d_model_inventory.csv
```

Research-only model artifacts:

```text
data/saved_models/research_360d_forecaster/
```

The runner is optimized for a 16 GB laptop:

```text
models train one at a time
predictions stream to CSV immediately after each model finishes
metrics are flushed after each major phase
each fitted model is saved, released, and garbage-collected before the next model
forecast_360d_model_inventory.csv records model name, target, family, status, rows, runtime, and errors
```

## How To Judge Results

For price/return:

```text
lower MAE
lower RMSE
higher correlation
higher sign accuracy from predicted return
higher top 1%, 5%, 10% confidence sign accuracy
```

For direction:

```text
higher AUC
lower Brier score
lower log loss
higher top-confidence precision
better calibration
```

For high/low/range:

```text
good 80% coverage
narrower useful bands
lower pinball loss
lower undercoverage during high volatility
```

For trading usefulness, judge:

```text
top 1% confidence
top 5% confidence
top 10% confidence
unseen 20% only
performance against baselines
```

Raw every-minute direction may still be weak. The real edge, if it exists, is likely in range, volatility, big-move timing, high/low bands, and abstaining from low-confidence periods.

## Safety Notes

This is not a profitability guarantee. It is a research test. The live bot should only consume this work later if the unseen 20% results show clear baseline-beating edge and the result is confirmed on a separate replay/live recorder period.
