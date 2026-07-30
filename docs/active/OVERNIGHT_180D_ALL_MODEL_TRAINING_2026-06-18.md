# Overnight 180d All-Model Training

Date: 2026-06-18

Purpose: provide one deliberate trigger for a long 180-day training run that trains every major model path sequentially without changing the safe daily `start.bat` behavior.

## Trigger

Run:

```powershell
cd C:\Users\rahul\Documents\BTC-Prediction-Tool
.\research\launchers\train_180d_all_models_overnight.bat
```

This wrapper sets:

```text
BTC_OVERNIGHT_TRAIN_ALL=1
BTC_HISTORICAL_DAYS=180
BTC_BACKFILL_DAYS=180
BTC_FORCE_HEAD_RETRAIN=1
BTC_FORCE_MAIN_RETRAIN=1
BTC_FREEZE_MODEL=0
BTC_RUN_STARTUP_BACKTEST=1
BTC_BACKTEST_MAX_ROWS=12000
BTC_DEV_RELOAD=0
BTC_TRAIN_THREADS=10
```

## Training Order

The run is intentionally sequential:

1. `backfill_trade_features.py --auto --days 180`
2. `build_persistence_dataset.py --auto --days 180`
3. `build_crossvenue_flow.py --auto --days 180`
4. `build_research_matrix.py --days 180`
5. `train_heads.py --force`
6. backend starts without reload
7. backend ignores saved main ensemble because `BTC_FORCE_MAIN_RETRAIN=1`
8. backend trains the main ensemble in the background
9. startup backtest runs after main training completes

## What Gets Trained

Standalone heads:

- selectivity
- signed quantile
- persistence / P(hold)
- bigmove keeper
- bigdrop keeper
- directional big-up / big-down keeper
- activity/range keeper
- champion meta, if enough resolved champion snapshots exist
- legacy heads when forced: beat, magnitude, path, fingerprints

Main ensemble:

- XGBoost
- LightGBM, if installed
- CatBoost, if installed
- Random Forest
- HistGradientBoosting
- Logistic Regression
- Deep sequence model, if PyTorch is installed
- OOF stackers
- move-size priors / magnitude components

## Matrix Safety

`build_research_matrix.py` now writes:

```text
data/research_matrix_1m.manifest.json
data/research_matrix_1m.days.txt
```

The matrix is only marked complete when coverage is believable:

- actual span must be at least 90% of the requested window
- row count must be at least 80% of requested 1-minute rows
- source files must not be newer than the matrix for a skip

If coverage is too low, the matrix step exits non-zero and `start.bat` skips specialist-head training rather than training 180d-tagged heads on stale data.

## Expected Runtime

First 180-day run:

- data download/backfill can take hours
- standalone heads can take 1-2 hours
- main ensemble can take several more hours on a 16 GB laptop
- total 8-11 hours is plausible

Later runs are faster because downloaded archives and saved models are cached.

## Important Notes

This run is for overnight training. For normal daily use, run `start.bat`, which stays frozen unless environment variables override it.

Do not use backend reload mode for this run. The wrapper sets `BTC_DEV_RELOAD=0` so file changes do not restart the backend during training.

