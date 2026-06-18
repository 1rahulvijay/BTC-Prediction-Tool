# Directional Big-Move Research Runbook

Date: 2026-06-17

This runbook covers the new research lane for the previously untested directional big-move targets:

- `big_up`
- `big_down`
- `big_drop`

The goal is to answer a very practical Polymarket/BTC question:

```text
Is BTC likely to make a meaningful move?
If yes, is the meaningful move more likely UP, DOWN, or a hard downside DROP?
```

This is research-only. It does not modify the live app, live models, DuckDB, or any trading logic.

---

## Script

```text
backend/research/train_180d_directional_bigmove.py
```

One-click runner:

```powershell
.\run_180d_directional_bigmove.bat
```

Manual command:

```powershell
python backend\research\train_180d_directional_bigmove.py --days 180 --horizons 5 15 --models "logistic,histgb,rf,extra_trees,lightgbm,xgboost,catboost" --device gpu --max-features 160 --n-jobs 2 --threshold-5m-bps 10 --threshold-15m-bps 15 --drop-threshold-5m-bps 10 --drop-threshold-15m-bps 15 --output-prefix forecast_180d_directional_bigmove
```

Smoke test:

```powershell
python backend\research\train_180d_directional_bigmove.py --smoke --no-save-models --output-prefix forecast_directional_bigmove_smoke
```

---

## Labels

The script uses 180 days of Binance BTCUSDT 1-minute data and tests 5-minute and 15-minute horizons.

| Label | Meaning |
|---|---|
| `target_big_up_5m` | Future 5m close return is at least `+10 bps` |
| `target_big_down_5m` | Future 5m close return is at most `-10 bps` |
| `target_big_drop_5m` | Future 5m low trades at least `-10 bps` below current close |
| `target_big_up_15m` | Future 15m close return is at least `+15 bps` |
| `target_big_down_15m` | Future 15m close return is at most `-15 bps` |
| `target_big_drop_15m` | Future 15m low trades at least `-15 bps` below current close |

Important distinction:

```text
big_down = closed meaningfully lower by expiry
big_drop = traded meaningfully lower at any point inside the window
```

That distinction matters for a live decision tool because BTC can dump and bounce before the prediction window closes.

---

## Models Used

| Model | Why It Is Included |
|---|---|
| Logistic Regression | Simple baseline; tells us whether signal exists without complex nonlinear modeling |
| HistGradientBoosting | Fast sklearn tree booster, good CPU baseline |
| RandomForest | Stable nonlinear baseline, useful for noisy feature sets |
| ExtraTrees | Often strong on noisy tabular market features and fast enough for research |
| LightGBM | Strong boosted-tree model, good probability ranking when installed |
| XGBoost | Strong nonlinear tabular model; can use GPU if the environment supports it |
| CatBoost | Best practical next model family from prior research; robust on messy tabular features |

No sequence models are included in this first directional-bigmove pass. Prior 180d and advanced-sequence research showed the sequence models did not beat the best tabular big-move models, so the first test should be fast, interpretable, and directly comparable.

---

## Outputs

| File | Purpose |
|---|---|
| `data/research/forecast_180d_directional_bigmove_classification_metrics.csv` | Full metrics for every target/model |
| `data/research/forecast_180d_directional_bigmove_analysis_summary.csv` | Best model per target |
| `data/research/forecast_180d_directional_bigmove_predictions.csv` | Test-set probabilities and predictions |
| `data/research/forecast_180d_directional_bigmove_model_inventory.csv` | Fit time, status, errors, row counts |
| `data/research/forecast_180d_directional_bigmove_summary.csv` | Ranked model/target summary |
| `data/research/forecast_180d_directional_bigmove_report.md` | Human-readable result report generated after the run |
| `data/saved_models/research_directional_bigmove/forecast_180d_directional_bigmove/` | Saved research models |
| `data/logs/forecast_180d_directional_bigmove.log` | Live run log from the batch file |

---

## Metrics To Trust

Use these in order:

| Metric | Why It Matters |
|---|---|
| AUC | Ranking power; whether the model separates likely vs unlikely events |
| Top 5% precision | Whether the highest-confidence alerts are useful |
| Top 10% precision | Whether the signal can create enough opportunities |
| Brier score | Probability quality; lower is better |
| Base rate | How common the event is; prevents fake excitement |
| Recall | How many true events the model catches |

Do not judge this by raw accuracy alone. If a big drop only happens 20% of the time, a dumb model can be 80% accurate by always saying "no drop."

---

## Promotion Rules

Promote to the live app only if directional big-move beats the current generic big-move logic in a useful way.

Minimum useful evidence:

| Target | Promote If |
|---|---|
| `big_up_5m` | AUC above `0.60` and top 5% precision clearly beats base rate |
| `big_down_5m` | AUC above `0.60` and top 5% precision clearly beats base rate |
| `big_drop_5m` | AUC above `0.62` and top 5% precision is strong enough to warn users |
| `big_up_15m` | AUC above `0.60` and stable Brier score |
| `big_down_15m` | AUC above `0.60` and stable Brier score |
| `big_drop_15m` | AUC above `0.62` and top-confidence precision beats base rate |

Best app use if successful:

```text
1. Big-move probability says whether the window matters.
2. big_up / big_down says which side has the better directional edge.
3. big_drop warns about downside path risk even when close-direction is unclear.
4. Quantile high/low bands estimate likely price range.
5. The UI shows BUY / SELL / WAIT / AVOID with plain-English reasons.
```

---

## Expected Runtime

On the current laptop:

- Smoke test: a few minutes.
- Full 180d run: likely 30 minutes to several hours depending on exchange fetch speed, CatBoost/XGBoost availability, and CPU/RAM pressure.

The runner logs every target/model completion so it should not look frozen.

---

## Current Status

| Item | Status |
|---|---|
| Research script | Created |
| Batch runner | Created |
| 180d full run | Completed |
| Smoke compile/run | Verified successfully |
| Live app integration | Not started; depends on results |

Smoke verification:

```text
python -m py_compile backend\research\train_180d_directional_bigmove.py
python backend\research\train_180d_directional_bigmove.py --smoke --no-save-models --output-prefix forecast_directional_bigmove_smoke
```

The smoke run completed and wrote:

```text
data/research/forecast_directional_bigmove_smoke_classification_metrics.csv
data/research/forecast_directional_bigmove_smoke_analysis_summary.csv
data/research/forecast_directional_bigmove_smoke_predictions.csv
data/research/forecast_directional_bigmove_smoke_report.md
```

---

## 180-Day Full Run Results

Run completed on 2026-06-17.

Configuration:

| Item | Value |
|---|---|
| Data window | 180 days |
| Market rows | 259,200 raw rows |
| Rows after feature/target cleanup | 258,945 |
| Selected features | 160 |
| Train rows | 165,724 |
| Calibration rows | 41,432 |
| Test rows | 51,789 |
| Horizons | 5m and 15m |
| Models | Logistic, HistGB, RF, ExtraTrees, LightGBM, XGBoost, CatBoost |
| Model runs | 48 / 48 completed successfully |
| Total model fit time | About 954 seconds, roughly 15.9 minutes |

Output files:

```text
data/research/forecast_180d_directional_bigmove_classification_metrics.csv
data/research/forecast_180d_directional_bigmove_analysis_summary.csv
data/research/forecast_180d_directional_bigmove_predictions.csv
data/research/forecast_180d_directional_bigmove_model_inventory.csv
data/research/forecast_180d_directional_bigmove_summary.csv
data/research/forecast_180d_directional_bigmove_report.md
```

### Best Model Per Target

| Target | Base Rate | Best Model | AUC | Precision | Recall | Top 5% Precision | Brier |
|---|---:|---|---:|---:|---:|---:|---:|
| `target_big_up_5m` | 13.79% | RF | 0.7208 | 25.44% | 60.66% | 35.57% | 0.1865 |
| `target_big_down_5m` | 15.31% | CatBoost | 0.7102 | 53.97% | 0.43% | 33.18% | 0.1211 |
| `target_big_drop_5m` | 27.49% | CatBoost | 0.7621 | 61.93% | 25.02% | 65.89% | 0.1658 |
| `target_big_up_15m` | 15.95% | ExtraTrees | 0.6877 | 25.29% | 62.56% | 36.54% | 0.2152 |
| `target_big_down_15m` | 18.17% | Logistic | 0.6767 | 28.37% | 58.28% | 38.78% | 0.2174 |
| `target_big_drop_15m` | 34.81% | Logistic | 0.7377 | 55.83% | 60.91% | 71.34% | 0.2025 |

### Plain-English Interpretation

The strongest new signal is not raw up/down direction. It is downside path-risk:

```text
Will BTC trade meaningfully lower at any point inside the next 5m/15m window?
```

The 5m big-drop model is especially useful:

- Base rate: `27.49%`
- Best AUC: `0.7621`
- Top 5% precision: `65.89%`

That means the highest-confidence 5% of 5m big-drop alerts were much richer in actual drops than normal market minutes. This is exactly the kind of signal that can help a decision tool say:

```text
Avoid long.
Downside shock risk is high.
If trading Polymarket, DOWN-side opportunity may be worth checking.
```

The 15m big-drop signal is also strong:

- Base rate: `34.81%`
- Best AUC: `0.7377`
- Top 5% precision: `71.34%`

This does not prove guaranteed profit. It proves the model is finding historical minutes where downside-path risk was much higher than average.

### Model Takeaways

| Finding | Meaning |
|---|---|
| CatBoost won `big_drop_5m` | Best candidate for 5m downside-risk probability |
| Logistic won `big_drop_15m` | Simple linear pressure features were enough for 15m drop-risk ranking |
| RF won `big_up_5m` | RF may help identify short-horizon upward pressure |
| ExtraTrees won `big_up_15m` | Noisy tree ensembles still help on slower upside windows |
| Raw `big_down_5m` recall was tiny for CatBoost at 0.5 threshold | Needs threshold tuning; AUC is useful, default 0.5 classification is not the final action rule |

### App Promotion Recommendation

Promote this research as a new `Big Drop Risk Engine`, not as a direct BUY/SELL button yet.

Recommended live-app wiring:

```text
big_drop_5m_probability
big_drop_15m_probability
big_up_5m_probability
big_down_5m_probability
big_up_15m_probability
big_down_15m_probability
```

Decision logic should use probability thresholds learned from the test set, not a fixed 0.5 cutoff. For example:

```text
If big_drop_5m is in the top-confidence zone:
    warn "hard drop risk"
    downgrade BUY/LONG signals
    require stronger evidence before showing UP
    consider DOWN-side Polymarket opportunity only if direction/range/price-to-beat also agree
```

Next step before production:

```text
Build threshold tables from the predictions CSV:
top 1%, 5%, 10%, 20% probability buckets
event rate per bucket
expected value versus Polymarket price
```
