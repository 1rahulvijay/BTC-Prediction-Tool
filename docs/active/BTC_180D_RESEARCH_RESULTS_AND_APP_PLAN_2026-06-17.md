# BTC 180-Day Research Results And App Plan

Date: 2026-06-17

This document consolidates the research work done around the 180-day BTC forecasting experiments, what we learned, what should be implemented in the app, and what should remain research-only until it proves edge.

This work is separate from the live BTC/Polymarket app. None of these research scripts directly modify live production models, DuckDB live state, app decision logic, or bot execution.

---

## Research Scripts Added

### 1. Multi-Target BTC Forecaster

```text
backend/research/train_360d_multitarget_forecaster.py
```

Launchers:

```text
research\launchers\run_180d_all_models_forecaster.bat
research\launchers\run_180d_remaining_quantile_only.bat
research\launchers\run_180d_sequence_only.bat
research\launchers\run_360d_multitarget_forecaster.bat
```

Purpose:

```text
Train/test BTC 5m and 15m models on Binance 1m data.
Use strict chronological split.
Save model-by-model metrics, predictions, and inventory.
```

### 2. CUDA PyTorch Research Environment

```text
research\launchers\setup_research_cuda_pytorch_env.bat
research\launchers\check_research_cuda_pytorch_env.bat
```

Purpose:

```text
Create a separate CUDA PyTorch environment for sequence models.
Avoid breaking the main app Python environment.
```

Current finding:

```text
RTX 4050 is detected.
XGBoost / LightGBM / CatBoost GPU tests passed.
Default PyTorch is CPU-only: torch 2.12.0+cpu.
```

### 3. Advanced Sequence Research

```text
backend/research/train_180d_advanced_sequence_models.py
research\launchers\run_180d_advanced_sequence_models.bat
docs/active/ADVANCED_SEQUENCE_RESEARCH_RUNBOOK_2026-06-17.md
```

Models implemented:

```text
VLSTM
LPatchTST
PatchTST
iTransformer
```

Optional seats:

```text
Mamba
Mamba2
VSN+Mamba2
```

Current limitation:

```text
mamba_ssm is not installed, so Mamba seats skip cleanly and are logged.
```

Smoke test result:

```text
VLSTM: OK
LPatchTST: OK
Mamba: skipped cleanly because mamba_ssm is missing
```

---

## Data And Split

Research window:

```text
180 days of BTCUSDT 1-minute data
```

Data sources:

```text
Binance spot BTCUSDT 1m
Binance futures BTCUSDT 1m
Binance mark price 1m
Binance premium/index 1m
Binance funding rates
```

Split:

```text
64% train
16% calibration
20% unseen test
```

Completed test set:

```text
test rows: 51,789
```

Feature count:

```text
selected features: 160
```

Feature health:

```text
total feature cells: 41,431,200
zero cells: 727,126
zero rate: 1.76%
null cells: 0
null rate: 0.00%
all-zero columns: 0
all-null columns: 0
95%+ zero columns: 0
95%+ null columns: 0
```

The only columns over 50% zero were normal binary session flags:

```text
session_asia
session_europe
session_us
```

Full feature quality report:

```text
data/research/forecast_180d_feature_zero_stats.csv
```

---

## Targets Predicted

For both 5m and 15m horizons:

```text
future BTC close price
future return in bps
future high in bps
future low in bps
future range in bps
future log volume
UP / DOWN direction
generic big move probability
quantile q10/q50/q90 bands
conformal 80% bands
```

Important distinction:

```text
generic big move = abs(return) is large
```

It detects whether movement is likely, but it does not yet separate:

```text
big UP move
big DOWN move
big drop
```

Those should be added next for Polymarket.

---

## Models Tested

Tabular regression/classification:

```text
Ridge
ElasticNet
HistGradientBoosting
RandomForest
ExtraTrees
LogisticRegression
LightGBM
XGBoost
CatBoost
```

Quantile:

```text
LightGBM quantile q10/q50/q90
split conformal adjusted bands
```

Sequence, base lane:

```text
LSTM
GRU
TCN
Transformer
```

Advanced sequence lane:

```text
VLSTM
LPatchTST
PatchTST
iTransformer
optional Mamba / Mamba2 / VSN+Mamba2
```

---

## Completed Result Summary

### Direction Prediction

Raw UP/DOWN direction was weak.

| Target | Best Model | Accuracy | AUC | Interpretation |
|---|---:|---:|---:|---|
| 5m UP/DOWN | RF | 51.6% | 0.528 | weak |
| 15m UP/DOWN | RF | 51.1% | 0.526 | weak |

Plain-English interpretation:

```text
The system should not aggressively trade every UP/DOWN call.
Raw direction is close to coin flip.
Direction should only matter when other signals agree.
```

### Big-Move Prediction

Big-move prediction was much stronger.

| Target | Best Model | Accuracy | AUC | Interpretation |
|---|---:|---:|---:|---|
| 5m Big Move | CatBoost | 74.3% | 0.745 | strong/useful |
| 15m Big Move | CatBoost | 70.1% | 0.707 | useful |

Plain-English interpretation:

```text
The system is much better at detecting whether a meaningful move may happen
than predicting exact direction every minute.
```

This is the strongest app-level insight from the research.

### Future Price

Exact future close price did not beat a simple current-price baseline.

| Target | Best Result | MAE |
|---|---:|---:|
| 5m future price | current price baseline | $60.71 |
| 15m future price | current price baseline | $104.84 |

Plain-English interpretation:

```text
Do not present exact future price as high-confidence truth.
Future price should be shown as an estimated zone/range, not a precise point target.
```

### Return

Return prediction was not meaningfully better than the zero/median baseline.

| Target | Best Model | MAE |
|---|---:|---:|
| 5m return | ExtraTrees | 8.76 bps |
| 15m return | RF | 15.22 bps |

However, the baseline was very close:

```text
5m baseline MAE: 8.75 bps
15m baseline MAE: 15.12 bps
```

Plain-English interpretation:

```text
Point return prediction is not strong enough alone.
Use it as one ingredient, not as the trade trigger.
```

### High / Low / Range

Future high, low, and range prediction beat simple baselines.

| Target | Best Model | MAE |
|---|---:|---:|
| 5m high | ElasticNet | 5.86 bps |
| 5m low | ElasticNet | 5.96 bps |
| 5m range | ElasticNet | 5.97 bps |
| 15m high | ElasticNet | 10.17 bps |
| 15m low | ElasticNet | 10.61 bps |
| 15m range | ElasticNet | 10.56 bps |

Approximate BTC dollar translation around $60k:

```text
1 bps  ≈ $6
5 bps  ≈ $30
10 bps ≈ $60
15 bps ≈ $90
```

Plain-English interpretation:

```text
The app should emphasize expected high/low/range zones more than exact close price.
```

### Volume

Volume prediction was useful.

| Target | Best Model | Log-Volume MAE |
|---|---:|---:|
| 5m log volume | CatBoost | 0.454 |
| 15m log volume | CatBoost | 0.407 |

Plain-English interpretation:

```text
The app should add a next-window volume forecast or market activity forecast.
```

### Quantile Bands

Quantile bands finished successfully with LightGBM GPU.

| Target | Coverage | Band Width | Interpretation |
|---|---:|---:|---|
| 5m return | 81.26% | 28.69 bps | good |
| 5m high | 82.14% | 19.36 bps | good |
| 5m low | 79.11% | 17.79 bps | good |
| 5m range | 81.03% | 19.40 bps | good |
| 15m return | 79.19% | 51.91 bps | good |
| 15m high | 81.00% | 35.96 bps | good |
| 15m low | 79.56% | 31.43 bps | near target |
| 15m range | 81.37% | 36.75 bps | good |

Plain-English interpretation:

```text
Quantile bands are close to the intended 80% coverage.
This is one of the most app-ready research results.
```

---

## What We Learned

### 1. UP/DOWN Alone Is Not Enough

Raw direction is only slightly above coin flip.

The app should not make aggressive BUY/SELL decisions from direction alone.

### 2. Big-Move Detection Is The Strongest Current Signal

The best predictive signal is:

```text
Will BTC move meaningfully?
```

not:

```text
Will every 5m candle close up or down?
```

### 3. Price Should Be A Zone, Not One Number

Exact future price is weak.

The app should show:

```text
expected low
expected high
expected range
80% band
trade room
```

instead of over-emphasizing a single target price.

### 4. Quantile Bands Are App-Ready

The 80% bands are close to target coverage.

These should be promoted into the app before deep sequence models.

### 5. Volume Forecast Has Signal

Volume/activity forecast can help detect whether a signal has enough market participation.

### 6. Polymarket Needs Directional Big-Move Targets

Current generic big move is useful but incomplete.

For UP/DOWN markets, add:

```text
big_up_5m
big_down_5m
big_up_15m
big_down_15m
touch_up_5m
touch_down_5m
touch_up_15m
touch_down_15m
```

Especially important:

```text
big_drop_probability
```

This can directly help decide whether buying DOWN has edge.

---

## Proposed App Changes

### Priority 1: Add Big-Move Probability As First-Class Signal

New app panel:

```text
Move Risk: LOW / MEDIUM / HIGH
5m big-move probability
15m big-move probability
```

Decision effect:

```text
Low big-move probability -> AVOID
High big-move probability + weak direction -> WAIT / volatility warning
High big-move probability + strong direction -> possible action
```

### Priority 2: Add Big Up / Big Down / Big Drop

New labels:

```text
big_up_5m = future_return_5m_bps >= +10 bps
big_down_5m = future_return_5m_bps <= -10 bps
big_up_15m = future_return_15m_bps >= +15 bps
big_down_15m = future_return_15m_bps <= -15 bps
```

Current research status:

| Label family | Tested? | Notes |
|---|---|---|
| `target_big_move_5m` / `target_big_move_15m` | Yes | Generic absolute move only |
| `big_up_5m` / `big_up_15m` | Yes | Full 180d directional big-move run completed |
| `big_down_5m` / `big_down_15m` | Yes | Full 180d directional big-move run completed |
| `big_drop_probability` | Yes | Path-aware future-low drop label; strongest new result |
| `touch_up_*` / `touch_down_*` | No | Proposed Polymarket path labels |

Important:

```text
The completed big-move tests do not yet prove big-drop prediction.
They only prove the model can detect whether a larger-than-normal move may happen.
The directional research pass is implemented in `backend/research/train_180d_directional_bigmove.py` and completed with `.\research\launchers\run_180d_directional_bigmove.bat`.

Directional big-move results:

| Target | Base Rate | Best Model | AUC | Top 5% Precision | Interpretation |
|---|---:|---|---:|---:|---|
| `target_big_up_5m` | 13.79% | RF | 0.7208 | 35.57% | Useful upside-pressure ranking |
| `target_big_down_5m` | 15.31% | CatBoost | 0.7102 | 33.18% | Useful ranking, but needs threshold tuning |
| `target_big_drop_5m` | 27.49% | CatBoost | 0.7621 | 65.89% | Strongest new signal |
| `target_big_up_15m` | 15.95% | ExtraTrees | 0.6877 | 36.54% | Moderate upside signal |
| `target_big_down_15m` | 18.17% | Logistic | 0.6767 | 38.78% | Moderate downside-close signal |
| `target_big_drop_15m` | 34.81% | Logistic | 0.7377 | 71.34% | Strong downside path-risk signal |

Conclusion:

```text
The new directional big-move lane found real signal, especially for path-aware big_drop.
This should be promoted first as a Big Drop Risk Engine that warns against long signals
and helps identify DOWN-side Polymarket opportunities when other filters agree.
```
```

Optional path labels:

```text
touch_up = future high reaches above anchor/line by X bps
touch_down = future low reaches below anchor/line by X bps
```

Plain app output:

```text
Big drop risk: HIGH
Big pump risk: LOW
Downside room: enough
Action: DOWN favored
```

### Priority 3: Add Quantile High/Low Bands

New app output:

```text
5m expected low
5m expected high
5m 80% range
15m expected low
15m expected high
15m 80% range
```

This should feed:

```text
target realism
expected move
trade room score
risk warning
```

### Priority 4: Replace Exact Price Target Emphasis

Instead of:

```text
Expected price: 63,051
```

Prefer:

```text
Expected 5m zone:
Low: 62,880
High: 63,060
80% band width: $180
```

### Priority 5: Add Trade Room Score

Definition:

```text
trade_room = expected directional edge / expected noise
```

Plain meaning:

```text
Does the predicted move have enough room to justify a trade?
```

Decision:

```text
Trade room too small -> AVOID
Trade room enough + direction aligned -> ACTIONABLE
```

### Priority 6: Add Volume/Activity Forecast

New app output:

```text
Expected next 5m activity: LOW / NORMAL / HIGH
Expected next 15m activity: LOW / NORMAL / HIGH
```

Use it as confirmation:

```text
High move probability + rising volume = stronger signal
High move probability + low volume = warning / lower confidence
```

### Priority 7: Use Direction Only As A Confirmation Signal

New action logic:

```text
IF big_move_probability is low:
    AVOID

IF big_move_probability is high AND direction confidence is high AND quantile band gives enough room:
    BUY UP or BUY DOWN

IF big_move_probability is high BUT direction is weak:
    WAIT / VOLATILITY WARNING

IF expected range is smaller than noise/cost:
    AVOID
```

---

## What Is Already In The App

Already partly present:

```text
BUY / SELL / AVOID
model agreement
confidence gating
price-to-beat display
Kronos projections
support/resistance
RSI
SuperTrend
market regime
order flow indicators
Coinbase premium
Bybit/Binance OI
plain analysis tab
model roster accuracy
target/direction error tracking
```

Needs stronger emphasis:

```text
big-move probability
range/high/low bands
trade room score
big-up vs big-down split
volume forecast
avoid when direction is coin-flip
```

---

## Completed Sequence-Only Results

Completed run:

```text
research\launchers\run_180d_sequence_only.bat
```

Output files:

```text
data/research/forecast_180d_sequence_only_summary.csv
data/research/forecast_180d_sequence_only_model_inventory.csv
data/research/forecast_180d_sequence_only_predictions.csv
data/research/forecast_180d_sequence_only_predictions.parquet
```

Models tested:

```text
LSTM
GRU
TCN
Transformer
```

Environment used:

```text
device=cpu
train rows: 100,000
test rows: 33,333
```

Total fit time:

```text
about 89.4 minutes
```

Fit time by model family:

| Model | Total fit time | Practical read |
|---|---:|---|
| Transformer | 69.4 min | too slow for current benefit |
| GRU | 9.6 min | moderate speed, did not win |
| LSTM | 6.2 min | moderate speed, did not win |
| TCN | 4.2 min | fastest and strongest sequence candidate |

### Sequence Direction Results

Raw UP/DOWN direction stayed weak.

| Target | Best sequence model | Sequence result | Best tabular result | Verdict |
|---|---|---:|---:|---|
| 5m UP/DOWN | Transformer | AUC 0.513 | RF AUC 0.528 | tabular wins |
| 15m UP/DOWN | GRU | AUC 0.520 | RF AUC 0.526 | tabular wins |

Plain-English read:

```text
The sequence models did not solve raw candle direction.
UP/DOWN remains too close to coin-flip to be the main trading trigger.
```

### Sequence Big-Move Results

Big-move detection was the best sequence use case, but still did not beat the best tabular models.

| Target | Best sequence model | Sequence result | Best tabular result | Verdict |
|---|---|---:|---:|---|
| 5m big move | TCN | AUC 0.715 | CatBoost AUC 0.745 | tabular wins |
| 15m big move | TCN | AUC 0.668 | CatBoost AUC 0.707 | tabular wins |

Plain-English read:

```text
TCN can detect movement risk, but CatBoost/RF/ExtraTrees still see the tabular market structure better.
```

### Sequence Return / Move-Size Results

Expected move-size prediction was also worse than tabular baselines.

| Target | Best sequence model | Sequence MAE | Best tabular MAE | Verdict |
|---|---|---:|---:|---|
| 5m return | TCN | 10.20 bps | ExtraTrees/RF about 8.76-8.78 bps | tabular wins |
| 15m return | TCN | 18.93 bps | RF 15.22 bps | tabular wins |

Plain-English read:

```text
For expected price move, the app should keep using tabular/quantile models first.
```

### Sequence Promotion Decision

Do not promote the completed sequence-only models into the live app yet.

Reason:

```text
They did not beat the current tabular models on direction, big-move detection, or return MAE.
```

The only sequence model worth keeping as a candidate is:

```text
TCN
```

Why:

```text
TCN was fastest and strongest overall among the completed sequence-only models.
```

Do not promote yet:

```text
LSTM
GRU
basic Transformer
```

Reason:

```text
They add training/runtime cost without improving the measured unseen-test edge.
```

Advanced sequence models should still be tested separately:

```text
VLSTM
LPatchTST
PatchTST
iTransformer
Mamba/Mamba2/VSN+Mamba2 if mamba_ssm is installed
```

Promotion rule:

```text
Only promote an advanced sequence model if it beats CatBoost on big-move AUC
or beats RF/ExtraTrees on return MAE on the unseen test split.
```

---

## What Should Remain Research-Only For Now

Do not promote yet:

```text
VLSTM
LPatchTST
PatchTST
iTransformer
Mamba/Mamba2/VSN+Mamba2
```

Reason:

```text
They have not yet beaten tabular/quantile baselines on the full unseen 20% test.
```

Promote sequence models only if they beat:

```text
CatBoost big-move AUC
LightGBM quantile coverage/band width
ElasticNet high/low/range MAE
```

---

## Completed 360-Day Advanced Sequence Results

Completed run:

```text
research\launchers\run_180d_advanced_sequence_models.bat
```

Important:

```text
The batch filename still says 180d, but this run used --days 360.
```

Models tested:

```text
VLSTM
LPatchTST
PatchTST
iTransformer
```

Models not tested in this run:

```text
Mamba
Mamba2
VSN+Mamba2
```

Reason:

```text
mamba-ssm did not install cleanly on Windows/Python 3.13.
```

Run shape:

```text
targets: 6
models: 4
total model fits: 24
device: CUDA / RTX 4050 Laptop GPU
train rows per fit: 80,000
test rows per fit: 103,629
epochs: 5
```

Wall-clock run:

```text
about 30 minutes from log start to final metrics
```

Total model fit time:

```text
about 10.7 minutes
```

Fit time by model:

| Model | Total fit time |
|---|---:|
| iTransformer | 331.3 sec |
| PatchTST | 109.0 sec |
| VLSTM | 108.7 sec |
| LPatchTST | 92.7 sec |

Saved model files:

```text
24 .pt files
data/saved_models/research_advanced_sequence/forecast_360d_advanced_sequence/
```

### 360d Advanced Direction Results

Raw UP/DOWN direction remained weak.

| Target | Best advanced model | AUC | Accuracy | Verdict |
|---|---|---:|---:|---|
| 5m UP/DOWN | VLSTM | 0.523 | 51.7% | weak |
| 15m UP/DOWN | LPatchTST | 0.516 | 50.8% | weak |

Plain-English read:

```text
Advanced sequence models still do not make raw UP/DOWN reliable enough
to be the main trading trigger.
```

### 360d Advanced Big-Move Results

Big-move detection was useful but did not beat the existing tabular benchmark.

| Target | Best advanced model | AUC | Accuracy | Existing benchmark | Verdict |
|---|---|---:|---:|---:|---|
| 5m big move | VLSTM | 0.724 | 75.0% | CatBoost AUC 0.745 | tabular still stronger |
| 15m big move | LPatchTST | 0.692 | 71.1% | CatBoost AUC 0.707 | tabular still stronger |

Plain-English read:

```text
The advanced sequence models can detect whether BTC may move,
but CatBoost/RF/ExtraTrees still look better for this job.
```

### 360d Advanced Return / Move-Size Results

At first glance, iTransformer had the best return MAE:

| Target | Best advanced model | MAE |
|---|---|---:|
| 5m return | iTransformer | 8.123 bps |
| 15m return | iTransformer | 14.109 bps |

However, same-test zero-return baselines were:

| Target | Zero-return baseline MAE | Best advanced MAE | Difference |
|---|---:|---:|---:|
| 5m return | 8.121 bps | 8.123 bps | advanced slightly worse |
| 15m return | 14.104 bps | 14.109 bps | advanced slightly worse |

Plain-English read:

```text
The return models mostly learned to predict very small moves near zero.
That lowers MAE, but it does not prove useful price-target skill.
```

Supporting warning signs:

```text
5m iTransformer return Pearson: 0.008
15m iTransformer return Pearson: 0.002
5m sign accuracy: about 49.9%
15m sign accuracy: about 49.8%
```

### 360d Advanced Sequence Promotion Decision

Do not promote these advanced sequence models into the live app yet.

Reason:

```text
They did not beat tabular big-move models.
They did not solve raw UP/DOWN direction.
They did not beat a same-test zero-return baseline for move-size prediction.
```

Best research candidates to keep watching:

```text
VLSTM for 5m big-move probability
LPatchTST for 15m big-move probability
iTransformer only as a research return baseline, not live signal
```

What this teaches:

```text
For this BTC feature set, tabular models remain the stronger live-app base.
The app should still prioritize Big Move Engine, directional big-move labels,
quantile high/low/range bands, and plain-English decision scoring.
```

---

## Files With Results

Main completed files:

```text
data/research/forecast_360d_regression_metrics.csv
data/research/forecast_360d_classification_metrics.csv
data/research/forecast_180d_quantile_only_quantile_metrics.csv
data/research/forecast_180d_quantile_only_predictions.parquet
data/research/forecast_180d_feature_zero_stats.csv
data/research/forecast_180d_sequence_only_summary.csv
data/research/forecast_180d_sequence_only_model_inventory.csv
data/research/forecast_180d_sequence_only_predictions.csv
data/research/forecast_180d_sequence_only_predictions.parquet
data/research/forecast_360d_advanced_sequence_summary.csv
data/research/forecast_360d_advanced_sequence_model_inventory.csv
data/research/forecast_360d_advanced_sequence_classification_metrics.csv
data/research/forecast_360d_advanced_sequence_regression_metrics.csv
data/research/forecast_360d_advanced_sequence_predictions.csv
data/research/forecast_360d_advanced_sequence_analysis_summary.csv
```

Advanced sequence smoke files:

```text
data/research/forecast_advanced_sequence_smoke_regression_metrics.csv
data/research/forecast_advanced_sequence_smoke_classification_metrics.csv
data/research/forecast_advanced_sequence_smoke_model_inventory.csv
```

Runbooks:

```text
docs/active/FORECAST_360D_RESEARCH_RUNBOOK_2026-06-16.md
docs/active/ADVANCED_SEQUENCE_RESEARCH_RUNBOOK_2026-06-17.md
```

---

## Final Practical Recommendation

The next app upgrade should not be “add more UP/DOWN models.”

The next app upgrade should be:

```text
Big Move Engine
Directional Big Move Engine
Quantile Range Engine
Trade Room Score
Plain-English Decision Explainer
```

Best app decision structure:

```text
1. Is a meaningful move likely?
2. Is it likely UP or DOWN?
3. Does the high/low band provide enough room?
4. Is volume/activity confirming?
5. Are support/resistance and regime aligned?
6. If not aligned, AVOID.
```

This is more realistic and more useful than trying to force every 5-minute BTC candle into an UP/DOWN prediction.
