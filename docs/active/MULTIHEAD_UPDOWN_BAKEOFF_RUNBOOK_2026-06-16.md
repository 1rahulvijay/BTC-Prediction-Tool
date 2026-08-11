# Multi-Head Binance Up/Down Bakeoff Runbook

Date: 2026-06-16

Purpose: test model families by target on 120 days of Binance BTCUSDT 1m data using a leak-safe chronological split:

```text
oldest 70% rounds = train
newest 30% rounds = test
```

This is research-only. It does not modify live app models, saved models, DuckDB state, or the running backend.

---

## Files Added

```text
backend/build_binance_updown_feature_dataset.py
backend/research/standalone/run_updown_multihead_bakeoff.py
research\launchers\run_120d_updown_bakeoff.bat
docs/active/MULTIHEAD_UPDOWN_BAKEOFF_RUNBOOK_2026-06-16.md
```

---

## One-Command Run

From the project root:

```powershell
.\research\launchers\run_120d_updown_bakeoff.bat
```

It launches a separate terminal window and writes logs to:

```text
data/logs/updown_bakeoff_120d.log
```

Monitor from another terminal:

```powershell
Get-Content data\logs\updown_bakeoff_120d.log -Wait
```

---

## Direct Python Command

```powershell
python backend\research\standalone\run_updown_multihead_bakeoff.py `
  --days 120 `
  --horizons 5 15 30 `
  --split 0.70 `
  --rebuild `
  --calibrate `
  --save-predictions `
  --prediction-limit-per-model 5000
```

Use this lighter version for a faster smoke run:

```powershell
python backend\research\standalone\run_updown_multihead_bakeoff.py --days 14 --horizons 5 15 --split 0.70 --rebuild --max-train-rows 20000 --max-features 120
```

---

## Outputs

Dataset:

```text
data/research/binance_updown_features.parquet
data/research/binance_updown_rounds.parquet
data/research/binance_updown_feature_manifest.json
```

Results:

```text
data/research/updown_bakeoff_metrics.csv
data/research/updown_bakeoff_predictions.csv
data/research/updown_bakeoff_run_summary.json
```

---

## Targets Tested

Classification:

```text
target_up_win
target_current_side_hold
target_line_cross
target_big_move_10bps
target_big_move_20bps
```

Regression:

```text
target_expiry_return_bps
target_max_up_bps
target_max_down_bps
target_range_bps
target_log_quote_volume
target_log_volume
target_log_trades
```

Quantile range models:

```text
target_expiry_return_bps
target_max_up_bps
target_max_down_bps
target_range_bps
```

---

## Models Tested

Classification:

```text
majority baseline
analytic P(Hold) baseline
analytic P(UP) baseline
last-side persistence baseline
LogisticRegression
HistGradientBoostingClassifier
LightGBMClassifier
XGBoostClassifier
CatBoostClassifier
RandomForestClassifier
ExtraTreesClassifier
```

Regression:

```text
Ridge
ElasticNet
HistGradientBoostingRegressor
LightGBMRegressor
XGBoostRegressor
CatBoostRegressor
RandomForestRegressor
ExtraTreesRegressor
```

Quantile:

```text
GradientBoostingRegressor loss=quantile alpha=0.10/0.50/0.90
```

Optional libraries are skipped if unavailable.

---

## Metrics In CSV

Classification rows include:

```text
accuracy
auc
brier
log_loss
ece_10bin
precision
recall
f1
precision_top5_pct
precision_top10_pct
lift_top5
lift_top10
p_ge_90_realized
p_ge_93_realized
p_ge_95_realized
```

Line-cross rows additionally include:

```text
line_cross_recall_at_025
line_cross_precision_at_025
false_safe_rate_at_025
safe_allowed_n_at_025
```

Regression rows include:

```text
mae
rmse
r2
spearman
direction_from_prediction_acc
```

Quantile rows include:

```text
pinball_q10
pinball_q50
pinball_q90
interval_80_coverage
interval_80_avg_width
median_mae
```

---

## How To Read Results

### `target_current_side_hold`

This is the most important head. Look for:

```text
low brier
high AUC
low ECE
P>=0.93 realized rate close to or above 0.93
enough coverage at P>=0.93
```

This can become fair value for a binary up/down market.

### `target_line_cross`

This is a danger head. Look for:

```text
high recall at threshold 0.25
low false_safe_rate_at_025
```

It should block trades, not create trades.

### `target_big_move_10bps`

This is a tradability/timing head. Look for:

```text
high AUC
high precision_top10_pct
lift_top10 > 1
```

Use percentile thresholds first.

### `target_up_win`

This is raw UP/DOWN. Expect it to be weak. Reject unless:

```text
AUC >= 0.56
accuracy stable above 54-55%
Brier/calibration acceptable
```

### Range/high/low/volume

Use these for projected price/risk bands and volume/tradability, not direct betting side.

---

## Important Limitations

This bakeoff does not prove profit because it does not include live market ask prices.

Profit requires:

```text
model fair probability
- market ask
- spread
- fees
- slippage
- safety buffer
> 0
```

The next stage after this bakeoff is to join these predictions with live Polymarket/order-book snapshots.

