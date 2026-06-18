# BTC Model Research Catalog And App Proposal

Date: 2026-06-17

Purpose: one source of truth for every research model tested so far, what each model predicted, how it performed, the feature set used, and what should change in the live BTC app.

Important conclusion: the strongest evidence is not in raw UP/DOWN direction. The strongest app path is big-move detection, directional big-move labels, high/low/range quantile bands, volume/activity forecast, and a plain-English trade/avoid score.

---

## Data, Split, And Leakage Controls

Research rows in canonical 360d manifest: `258,945`

Split:

- `train`: `165,724` rows
- `calibration`: `41,432` rows
- `test`: `51,789` rows

Leakage controls:

- features use current/past data only
- targets use future bars only
- chronological 64/16/20 split
- scalers/medians fit on train only
- research-only artifacts do not affect live app

Feature health from `forecast_180d_feature_zero_stats.csv`:

- Selected features: `160`
- Null cells: `0`
- All-zero columns: `0`
- All-null columns: `0`
- 95%+ zero/null columns: `0`
- Only high-zero columns were normal session flags like `session_asia`, `session_europe`, and `session_us`.

---

## Prediction Targets Tested

| Target | Horizon | Meaning | Trading use |
| --- | --- | --- | --- |
| `target_big_move_15m` | 15m | Whether absolute movement is large | Strongest classification target; use for action gating |
| `target_big_move_5m` | 5m | Whether absolute movement is large | Strongest classification target; use for action gating |
| `target_direction_15m` | 15m | Whether future close is up or down | Weak; use only as confirmation |
| `target_direction_5m` | 5m | Whether future close is up or down | Weak; use only as confirmation |
| `target_high_15m_bps` | 15m | Future window high above current level | Useful for upside target/risk room |
| `target_high_5m_bps` | 5m | Future window high above current level | Useful for upside target/risk room |
| `target_log_volume_15m` | 15m | Future log volume/activity | Useful as confirmation of move quality |
| `target_log_volume_5m` | 5m | Future log volume/activity | Useful as confirmation of move quality |
| `target_low_15m_bps` | 15m | Future window low below current level | Useful for downside target/risk room |
| `target_low_5m_bps` | 5m | Future window low below current level | Useful for downside target/risk room |
| `target_price_15m` | 15m | Future BTC price | Hard to beat current price baseline |
| `target_price_5m` | 5m | Future BTC price | Hard to beat current price baseline |
| `target_range_15m_bps` | 15m | Expected high-low movement range | Useful for volatility/range planning |
| `target_range_5m_bps` | 5m | Expected high-low movement range | Useful for volatility/range planning |
| `target_return_15m_bps` | 15m | Future return in basis points | Useful only if it beats zero-return baseline |
| `target_return_5m_bps` | 5m | Future return in basis points | Useful only if it beats zero-return baseline |

---

## Models Tested

| Family | Models | Where tested | Current verdict |
| --- | --- | --- | --- |
| Linear/baseline | Ridge, ElasticNet, LogisticRegression | Tabular regression/classification | Surprisingly strong for high/low/range and sanity checks |
| Tree ensembles | RandomForest, ExtraTrees, HistGradientBoosting | Tabular regression/classification | Strong baselines; RF/ExtraTrees often good for move-size and big-move |
| Boosted trees | LightGBM, XGBoost, CatBoost | Tabular classification/regression/quantile | Best practical live-app family, especially CatBoost big-move and LightGBM quantiles |
| Quantile models | LightGBM q10/q50/q90, GBR q10/q50/q90 | Quantile high/low/range/return | Should be promoted for uncertainty bands |
| Base sequence | LSTM, GRU, TCN, basic Transformer | Sequence-only run | TCN best of this group, but did not beat tabular baselines |
| Advanced sequence | VLSTM, LPatchTST, PatchTST, iTransformer | 360d CUDA run | Useful research, but not live-promotion ready |
| Optional sequence | Mamba, Mamba2, VSN+Mamba2 | Smoke path only | Not tested fully because `mamba-ssm` did not install on Windows/Python 3.13 |

---

## Tabular Classification Results

Model-family average across classification targets:

| Model | Targets | Avg AUC | Best AUC | Avg Accuracy | Avg Brier |
| --- | --- | --- | --- | --- | --- |
| rf | 4 | 0.626 | 0.744 | 0.598 | 0.227 |
| catboost | 4 | 0.625 | 0.745 | 0.618 | 0.219 |
| extra_trees | 4 | 0.625 | 0.744 | 0.594 | 0.231 |
| xgboost | 4 | 0.623 | 0.743 | 0.618 | 0.221 |
| logistic | 4 | 0.622 | 0.739 | 0.595 | 0.232 |
| histgb | 4 | 0.621 | 0.741 | 0.617 | 0.223 |
| lightgbm | 4 | 0.620 | 0.739 | 0.615 | 0.225 |

Best model per classification target:

| Target | Horizon | Winner | AUC | Accuracy | Precision | Recall | F1 | Brier |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| target_big_move_15m | 15 | catboost | 0.707 | 0.701 | 0.544 | 0.346 | 0.423 | 0.193 |
| target_big_move_5m | 5 | catboost | 0.745 | 0.743 | 0.555 | 0.334 | 0.417 | 0.171 |
| target_direction_15m | 15 | rf | 0.526 | 0.511 | 0.499 | 0.655 | 0.567 | 0.253 |
| target_direction_5m | 5 | rf | 0.528 | 0.516 | 0.507 | 0.605 | 0.552 | 0.250 |

Plain read:

- Raw `target_direction_5m` and `target_direction_15m` stayed weak, around low-0.52 AUC.
- `target_big_move_5m` and `target_big_move_15m` were much stronger and are the best classification targets for the app.
- CatBoost/RF/ExtraTrees are the strongest practical classification candidates.

---

## Tabular Regression Results

Model-family average across regression targets:

| Model | Targets | Avg MAE | Avg RMSE | Avg Sign Acc |
| --- | --- | --- | --- | --- |
| elasticnet | 12 | 20.064 | 30.400 | 0.504 |
| ridge | 12 | 20.066 | 30.401 | 0.505 |
| extra_trees | 12 | 43.710 | 74.391 | 0.507 |
| rf | 12 | 53.782 | 92.900 | 0.513 |
| histgb | 12 | 86.998 | 141.970 | 0.512 |
| xgboost | 12 | 87.319 | 145.380 | 0.511 |
| lightgbm | 12 | 98.504 | 153.588 | 0.509 |
| catboost | 12 | 100.284 | 160.186 | 0.514 |

Best model per regression target:

| Target | Horizon | Winner | MAE | RMSE | Pearson | Spearman | Sign Acc |
| --- | --- | --- | --- | --- | --- | --- | --- |
| target_high_15m_bps | 15 | elasticnet | 10.175 | 16.644 | 0.451 | 0.400 |  |
| target_high_5m_bps | 5 | elasticnet | 5.864 | 9.805 | 0.463 | 0.414 |  |
| target_log_volume_15m | 15 | catboost | 0.407 | 0.520 | 0.763 | 0.743 |  |
| target_log_volume_5m | 5 | catboost | 0.454 | 0.592 | 0.746 | 0.725 |  |
| target_low_15m_bps | 15 | elasticnet | 10.613 | 15.905 | 0.452 | 0.418 |  |
| target_low_5m_bps | 5 | elasticnet | 5.962 | 9.037 | 0.471 | 0.425 |  |
| target_price_15m | 15 | elasticnet | 105.701 | 156.370 | 1.000 | 0.999 |  |
| target_price_5m | 5 | ridge | 60.986 | 92.258 | 1.000 | 1.000 |  |
| target_range_15m_bps | 15 | elasticnet | 10.562 | 16.999 | 0.700 | 0.746 |  |
| target_range_5m_bps | 5 | elasticnet | 5.965 | 9.834 | 0.712 | 0.755 |  |
| target_return_15m_bps | 15 | rf | 15.221 | 23.170 | 0.038 | 0.006 | 0.509 |
| target_return_5m_bps | 5 | extra_trees | 8.763 | 13.638 | 0.000 | 0.015 | 0.510 |

Plain read:

- Direct future price prediction did not beat the current-price baseline, so exact price should not be treated as the main signal.
- High/low/range targets were more useful than exact price targets.
- Return MAE must always be compared to a zero-return baseline, because models can look good by predicting almost no movement.

---

## Quantile / Uncertainty Results

| Target | Horizon | Model | Raw 80% Coverage | CQR 80% Coverage | Avg Band Width | CQR Band Width |
| --- | --- | --- | --- | --- | --- | --- |
| target_high_5m_bps | 5 | lightgbm | 0.813 | 0.821 | 19.358 | 19.358 |
| target_low_5m_bps | 5 | lightgbm | 0.790 | 0.791 | 17.790 | 17.790 |
| target_range_5m_bps | 5 | lightgbm | 0.800 | 0.810 | 19.138 | 19.402 |
| target_return_5m_bps | 5 | lightgbm | 0.813 | 0.813 | 28.695 | 28.695 |
| target_high_15m_bps | 15 | lightgbm | 0.800 | 0.810 | 35.846 | 35.959 |
| target_low_15m_bps | 15 | lightgbm | 0.796 | 0.796 | 31.432 | 31.432 |
| target_range_15m_bps | 15 | lightgbm | 0.785 | 0.814 | 35.579 | 36.748 |
| target_return_15m_bps | 15 | lightgbm | 0.792 | 0.792 | 51.912 | 51.912 |

Plain read:

- LightGBM quantile bands are one of the most app-ready research outputs.
- These bands are better for non-traders than single-number forecasts because they show likely range and uncertainty.
- Promote high/low/range quantile bands before promoting deep sequence models.

---

## Base Sequence Results

Fit time:

| Model | Fit Minutes |
| --- | --- |
| transformer | 69.451 |
| gru | 9.584 |
| lstm | 6.189 |
| tcn | 4.199 |

Best base sequence result by target:

| Target | Horizon | Model | Metric | Value | Rank |
| --- | --- | --- | --- | --- | --- |
| target_big_move_15m | 15 | tcn | brier | 0.229 | 1 |
| target_big_move_15m | 15 | transformer | brier | 0.233 | 2 |
| target_big_move_15m | 15 | gru | brier | 0.238 | 3 |
| target_big_move_15m | 15 | lstm | brier | 0.252 | 4 |
| target_big_move_5m | 5 | gru | brier | 0.194 | 1 |
| target_big_move_5m | 5 | transformer | brier | 0.194 | 2 |
| target_big_move_5m | 5 | tcn | brier | 0.196 | 3 |
| target_big_move_5m | 5 | lstm | brier | 0.205 | 4 |
| target_direction_15m | 15 | tcn | brier | 0.292 | 1 |
| target_direction_15m | 15 | transformer | brier | 0.301 | 2 |
| target_direction_15m | 15 | gru | brier | 0.324 | 3 |
| target_direction_15m | 15 | lstm | brier | 0.325 | 4 |
| target_direction_5m | 5 | tcn | brier | 0.253 | 1 |
| target_direction_5m | 5 | gru | brier | 0.256 | 2 |
| target_direction_5m | 5 | transformer | brier | 0.258 | 3 |
| target_direction_5m | 5 | lstm | brier | 0.266 | 4 |
| target_return_15m_bps | 15 | tcn | mae | 18.928 | 1 |
| target_return_15m_bps | 15 | transformer | mae | 19.823 | 2 |
| target_return_15m_bps | 15 | lstm | mae | 20.004 | 3 |
| target_return_15m_bps | 15 | gru | mae | 20.944 | 4 |
| target_return_5m_bps | 5 | tcn | mae | 10.195 | 1 |
| target_return_5m_bps | 5 | transformer | mae | 10.310 | 2 |
| target_return_5m_bps | 5 | lstm | mae | 11.095 | 3 |
| target_return_5m_bps | 5 | gru | mae | 11.187 | 4 |

Plain read:

- TCN was the only base sequence model worth watching.
- Basic Transformer was much slower and did not justify the cost.
- LSTM/GRU did not beat tabular baselines.

---

## Advanced Sequence 360-Day Results

Fit time:

| Model | Fit Minutes |
| --- | --- |
| itransformer | 5.521 |
| patchtst | 1.816 |
| vlstm | 1.812 |
| lpatchtst | 1.545 |

Best advanced sequence classification results:

| Target | Horizon | Winner | AUC | Accuracy | Precision | Recall | F1 | Brier |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| target_big_move_15m | 15 | lpatchtst | 0.692 | 0.711 | 0.576 | 0.238 | 0.336 | 0.194 |
| target_big_move_5m | 5 | vlstm | 0.724 | 0.750 | 0.544 | 0.260 | 0.352 | 0.171 |
| target_direction_15m | 15 | lpatchtst | 0.516 | 0.508 | 0.504 | 0.700 | 0.586 | 0.254 |
| target_direction_5m | 5 | vlstm | 0.523 | 0.517 | 0.512 | 0.638 | 0.568 | 0.251 |

Best advanced sequence regression results:

| Target | Horizon | Winner | MAE | RMSE | Pearson | Spearman | Sign Acc |
| --- | --- | --- | --- | --- | --- | --- | --- |
| target_return_15m_bps | 15 | itransformer | 14.109 | 21.227 | 0.002 | -0.002 | 0.498 |
| target_return_5m_bps | 5 | itransformer | 8.123 | 12.463 | 0.008 | 0.012 | 0.498 |

Same-test baseline analysis:

| target | horizon | n | base_rate | always_majority_acc | best_model | best_auc | best_brier | best_acc | best_precision | best_recall | best_f1 | zero_mae | median_mae | best_mae | mae_gain_vs_zero | mae_gain_pct_vs_zero | best_rmse | best_sign_acc | best_pearson |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| target_big_move_15m | 15 | 103629 | 0.308 | 0.692 | lpatchtst | 0.692 | 0.194 | 0.711 | 0.576 | 0.238 | 0.336 |  |  |  |  |  |  |  |  |
| target_big_move_5m | 5 | 103629 | 0.261 | 0.739 | vlstm | 0.724 | 0.171 | 0.750 | 0.544 | 0.260 | 0.352 |  |  |  |  |  |  |  |  |
| target_direction_15m | 15 | 103629 | 0.498 | 0.502 | lpatchtst | 0.516 | 0.254 | 0.508 | 0.504 | 0.700 | 0.586 |  |  |  |  |  |  |  |  |
| target_direction_5m | 5 | 103629 | 0.498 | 0.502 | vlstm | 0.523 | 0.251 | 0.517 | 0.512 | 0.638 | 0.568 |  |  |  |  |  |  |  |  |
| target_return_15m_bps | 15 | 103629 |  |  | itransformer |  |  |  |  |  |  | 14.104 | 14.104 | 14.109 | -0.005 | -0.033 | 21.227 | 0.498 | 0.002 |
| target_return_5m_bps | 5 | 103629 |  |  | itransformer |  |  |  |  |  |  | 8.121 | 8.121 | 8.123 | -0.002 | -0.028 | 12.463 | 0.499 | 0.008 |

Plain read:

- VLSTM won 5m big-move among advanced sequence models, but did not beat CatBoost.
- LPatchTST won 15m big-move among advanced sequence models, but did not beat CatBoost.
- iTransformer had the lowest return MAE, but did not beat the same-test zero-return baseline.
- Do not promote advanced sequence models into live decisions yet.

---

## Features Used

Total selected feature columns: `160`

### Price/candle and returns

Count: `24`

- `low`
- `futures_low`
- `mark_low`
- `open`
- `close`
- `futures_open`
- `futures_close`
- `mark_open`
- `mark_close`
- `high`
- `mark_high`
- `futures_high`
- `range_240m_bps`
- `range_120m_bps`
- `range_60m_bps`
- `range_30m_bps`
- `range_15m_bps`
- `range_10m_bps`
- `range_5m_bps`
- `range_3m_bps`
- `hl_range_bps`
- `oc_body_bps`
- `upper_wick_bps`
- `lower_wick_bps`

### Volume and trade flow

Count: `62`

- `futures_quote_volume`
- `quote_volume`
- `taker_buy_quote`
- `cvd_base`
- `futures_n_trades`
- `n_trades`
- `volume_sum_240m`
- `volume_sum_120m`
- `volume_sum_60m`
- `taker_delta_sum_240m`
- `cvd_change_240m`
- `volume_sum_30m`
- `taker_delta_sum_120m`
- `cvd_change_120m`
- `volume_sum_15m`
- `futures_volume`
- `taker_delta_sum_60m`
- `cvd_change_60m`
- `volume_sum_10m`
- `taker_delta_sum_30m`
- `cvd_change_30m`
- `futures_taker_buy_base`
- `volume_sum_5m`
- `taker_delta_sum_15m`
- `cvd_change_15m`
- `volume_sum_3m`
- `taker_delta_sum_10m`
- `cvd_change_10m`
- `taker_delta_sum_5m`
- `cvd_change_5m`
- `taker_delta_sum_3m`
- `cvd_change_3m`
- `volume`
- `taker_delta_base`
- `taker_buy_base`
- `quote_volume_log`
- `trade_count_z_240m`
- `trade_count_z_120m`
- `volume_z_240m`
- `quote_volume_z_240m`
- `volume_z_120m`
- `quote_volume_z_120m`
- `trade_count_z_60m`
- `volume_z_60m`
- `quote_volume_z_60m`
- `trades_log`
- `trade_count_z_30m`
- `volume_z_30m`
- `quote_volume_z_30m`
- `trade_count_z_15m`
- `volume_z_15m`
- `quote_volume_z_15m`
- `volume_log`
- `trade_count_z_10m`
- `volume_z_10m`
- `quote_volume_z_10m`
- `trade_count_z_5m`
- `volume_z_5m`
- `quote_volume_z_5m`
- `trade_count_z_3m`
- `volume_z_3m`
- `quote_volume_z_3m`

### Futures/derivatives

Count: `26`

- `futures_quote_volume`
- `futures_low`
- `mark_low`
- `futures_open`
- `futures_close`
- `mark_open`
- `mark_close`
- `mark_high`
- `futures_high`
- `futures_n_trades`
- `futures_volume`
- `futures_taker_buy_base`
- `hours_to_funding`
- `mark_basis_bps`
- `basis_z_240m`
- `basis_z_120m`
- `basis_z_60m`
- `basis_z_30m`
- `basis_z_15m`
- `basis_z_10m`
- `basis_z_5m`
- `futures_basis_bps`
- `basis_z_3m`
- `futures_ret_spread_bps`
- `funding_clock_sin`
- `funding_clock_cos`

### Volatility/risk

Count: `12`

- `atr_7_bps`
- `atr_14_bps`
- `atr_30_bps`
- `atr_60_bps`
- `realized_vol_3m_bps`
- `realized_vol_5m_bps`
- `realized_vol_10m_bps`
- `realized_vol_15m_bps`
- `realized_vol_30m_bps`
- `realized_vol_60m_bps`
- `realized_vol_120m_bps`
- `realized_vol_240m_bps`

### Time/session

Count: `8`

- `hours_to_funding`
- `dow_sin`
- `hour_cos`
- `hour_sin`
- `dow_cos`
- `session_europe`
- `session_us`
- `session_asia`

### Other engineered features

Count: `41`

- `ret_sum_240m_bps`
- `bollinger_width_100_bps`
- `ret_sum_120m_bps`
- `donchian_width_100_bps`
- `bollinger_width_50_bps`
- `ret_sum_60m_bps`
- `donchian_width_50_bps`
- `ema_dist_200_bps`
- `ret_sum_30m_bps`
- `bollinger_width_20_bps`
- `ema_dist_100_bps`
- `donchian_width_20_bps`
- `ret_sum_15m_bps`
- `ema_dist_50_bps`
- `ret_sum_10m_bps`
- `rsi_7`
- `ret_sum_5m_bps`
- `ema_dist_20_bps`
- `ret_sum_3m_bps`
- `rsi_14`
- `ema_dist_10_bps`
- `rsi_21`
- `macd_bps`
- `macd_signal_bps`
- `ret_1m_bps`
- `ema_dist_5_bps`
- `abs_ret_1m_bps`
- `ret_mean_3m_bps`
- `ret_mean_5m_bps`
- `ema_slope_5_bps`
- `macd_hist_bps`
- `ema_slope_10_bps`
- `ret_mean_10m_bps`
- `ret_mean_15m_bps`
- `ema_slope_20_bps`
- `ret_mean_30m_bps`
- `ema_slope_50_bps`
- `ret_mean_60m_bps`
- `ema_slope_100_bps`
- `ret_mean_120m_bps`
- `ema_slope_200_bps`

---

## App Changes I Recommend

### 1. Promote Big Move Engine

Use big-move probability as the first question: "Is BTC likely to move enough to matter?" This is stronger than raw UP/DOWN.

Implementation idea:

```text
if big_move_probability < threshold:
    action = AVOID
else:
    continue to direction + range checks
```

### 2. Add Directional Big-Move Labels

Current generic big move only asks whether movement is large. Add labels that separate direction:

```text
big_up_5m
big_down_5m
big_up_15m
big_down_15m
touch_up_price_to_beat
touch_down_price_to_beat
```

This matters for Polymarket because you can trade both UP and DOWN outcomes.

Current research status:

| Label family | Tested? | Notes |
|---|---|---|
| `target_big_move_5m` / `target_big_move_15m` | Yes | Generic absolute move only; does not know UP vs DOWN |
| `big_up_5m` / `big_up_15m` | Yes | Full 180d directional big-move run completed |
| `big_down_5m` / `big_down_15m` | Yes | Full 180d directional big-move run completed |
| `big_drop_probability` | Yes | Path-aware future-low drop label; strongest new result |
| `touch_up_price_to_beat` / `touch_down_price_to_beat` | No | Proposed Polymarket-specific path labels |

Do not interpret the completed `target_big_move_*` results as proof that the app can already predict big drops. They only prove the model has signal for whether a large move may happen, not which side it goes.

The dedicated directional big-move lane has now been run on 180 days of data:

| Target | Base Rate | Best Model | AUC | Top 5% Precision | App Use |
|---|---:|---|---:|---:|---|
| `target_big_up_5m` | 13.79% | RF | 0.7208 | 35.57% | Upside-pressure input |
| `target_big_down_5m` | 15.31% | CatBoost | 0.7102 | 33.18% | Downside-close input |
| `target_big_drop_5m` | 27.49% | CatBoost | 0.7621 | 65.89% | Big Drop Risk Engine |
| `target_big_up_15m` | 15.95% | ExtraTrees | 0.6877 | 36.54% | 15m upside-pressure input |
| `target_big_down_15m` | 18.17% | Logistic | 0.6767 | 38.78% | 15m downside-close input |
| `target_big_drop_15m` | 34.81% | Logistic | 0.7377 | 71.34% | Big Drop Risk Engine |

The strongest promotion candidate is `big_drop_probability`, especially as a warning/avoid filter and DOWN-side opportunity detector.

### 3. Promote Quantile High/Low/Range Bands

Show non-traders:

```text
Likely 5m high
Likely 5m low
Likely 15m high
Likely 15m low
Expected range
Uncertainty band
```

This is more honest than a fake-precise target price.

### 4. Build Trade Room Score

A simple score for users:

```text
Trade Room = expected favorable move - expected adverse move - estimated fees/slippage
```

If Trade Room is negative, show AVOID even if UP/DOWN is leaning one way.

### 5. Add Plain-English Decision Explainer

Every signal should answer:

```text
What is the signal?
Why?
What could go wrong?
What price/range matters?
How confident is the system based on live resolved history?
```

Example:

```text
AVOID: BTC may move, but direction is not reliable. Quantile range is wide and model agreement is weak.
```

### 6. Keep Deep Sequence As Research-Only

Do not add VLSTM/LPatchTST/PatchTST/iTransformer to live decisions until they beat tabular models on the same test window and same target.

### 7. Add Promotion Gates

A model can enter the live app only if it passes:

```text
beats baseline on same test split
beats existing production model
has stable calibration
has acceptable live resolved accuracy
does not increase boot/training time too much
```

---

## Final Ranking For Live App Use

| Rank | Component | Status | Why |
| --- | --- | --- | --- |
| 1 | CatBoost/RF/ExtraTrees big-move classifiers | Promote | Best classification evidence |
| 2 | LightGBM quantile high/low/range bands | Promote | Best uncertainty/range UX |
| 3 | High/low/range regression | Promote carefully | Useful for target/risk room |
| 4 | Volume/activity forecast | Promote as confirmation | Helps distinguish real moves from noise |
| 5 | Raw UP/DOWN direction | Use as confirmation only | Too close to coin-flip |
| 6 | TCN/VLSTM/LPatchTST/PatchTST/iTransformer | Research only | Did not beat tabular baselines yet |
| 7 | Mamba/Mamba2/VSN+Mamba2 | Not tested | Dependency did not install locally |

The best next app upgrade is not more raw direction models. It is a clearer decision engine built around movement probability, directional big move, quantified range, model agreement, and avoid rules.
