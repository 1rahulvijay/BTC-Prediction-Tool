# BTC Quantum Trader: Architecture Reference

> **Historical reference, not a deployment checklist.** This file contains architecture history
> from several model eras and must not decide whether the current process or artifacts are
> production-ready. The canonical deployment gate is
> `docs/active/PRODUCTION_READINESS_AUDIT_2026-07-30.md`; executable contracts in
> `model_contract.py`, `model_registry.py`, `production_readiness.py` and serving loaders take
> precedence over narrative documentation. Current implementation/test status is in
> `docs/active/CURRENT_IMPLEMENTATION_TEST_AND_GAP_LEDGER_2026-07-31.md`.

**Current executable correction, 2026-07-31:** the raw app feature schema is **136** columns and
the v14 main-model mask contains **63** `KEEP`/`PARITY-FIX` features with hash
`864622d65e85`. The current saved v11 main bundle and all unmanifested standalone artifacts are
blocked pending the 1,000-day retrain. Later 69-feature/v11 statements in this historical reference
describe the prior model era and are not current serving claims.

Status: research and decision-support platform, not a proven production trading system. It uses serious quant concepts, but live edge still has to be proven over enough out-of-sample predictions.

---

## 1. Current Maturity Scorecard

| Area | Current Score | Reason |
|---|---:|---|
| Feature engineering | 8/10 | 136 raw app features with technical, microstructure, derivatives, cross-exchange, institutional, multi-timeframe, Polymarket/event and interaction features. The main ensemble consumes a 63-feature pruned model schema for speed/RAM hygiene. Live-only feature coverage improves as the signal-history buffer accumulates. |
| Ensemble models | 8.7/10 | XGBoost, LightGBM, optional CatBoost, Random Forest, HistGradientBoosting, optional PyTorch TCN/sequence model, Logistic Regression, plus separate move-size regressors and regime move-size priors. |
| Verification system | 9/10 | Per-horizon prediction recording, direction accuracy, miss rate, price-match rate, target-size error, BUY/SELL/AVOID action accuracy, and avoid-success tracking. |
| Auto learning | 8/10 | Live verification feeds confidence thresholds, retraining flags, regime-specific model weights, and meta-model training data. |
| Reinforcement policy layer | 5/10 | New FSR-PPO inspired challenger computes denoised-signal state, flexible BUY/SELL/AVOID sizing and paper rewards. It is measured in DuckDB but does not override the ensemble yet. |
| UI / monitoring | 9.2/10 | Technical dashboard plus Decision Center cockpit with action, trust, risk, confirmation gates, why BUY/SELL/AVOID explanations, action accuracy, support/resistance, drift and boot-time visibility. |
| Market microstructure | 7/10 | CVD, order-book imbalance, liquidity walls, spread/vacuum checks, spoofing/absorption concepts, queue depletion, liquidations. Not low-latency/proprietary. |
| Quant research framework | 8/10 | DuckDB analytics, analysis snapshots, purged walk-forward validation, feature importance, A/B comparison framework, simulator metrics. |
| Institutional data sources | 6.5/10 | Binance, Coinbase, Bybit, Deribit options, CME basis proxy, stablecoin flow and exchange flow. Mostly public/derived feeds. Chainlink/cross-asset runtime wiring is currently disabled in `server.py`. |
| Regime intelligence | 8/10 | HMM/GMM-style regime detection, regime memory, confidence vector, proportional expert blending, regime-specific accuracy tracking. |
| Labeling methodology | 8/10 | Triple-barrier direction labels plus separate magnitude and quantile move-size models. |
| Proven trading edge | Unknown | Requires 30-90 days of live, out-of-sample BUY/SELL/AVOID accuracy, expectancy, profit factor, Sharpe and drawdown. |

---

## 2. Data Sources

The backend ingests and broadcasts live BTC market context through `backend/server.py`.

Primary feeds:

- Binance spot candles and 24h ticker.
- Binance spot WebSocket trades, depth and klines for BTCUSDT.
- Binance futures liquidation stream.
- Coinbase BTC-USD ticker for Coinbase premium.
- Bybit V5 open interest and funding.
- Fear & Greed sentiment.
- Deribit options summary.
- CME basis proxy.
- Stablecoin flow and exchange flow proxies.
- TradFi macro proxies (DXY, US10Y).

Re-enabled feed:

- **Chainlink BTC/USD** (`ChainlinkRESTClient`, CoinGecko proxy) is now instantiated and
  polled (~every 30s, best-effort) in `server.py`; the value flows into
  `data_state["chainlink_price"]` → `current_venue_prices()` → the Multi-Exchange
  Consensus strip. It is an oracle-reference spot price, not a low-latency exchange feed.

Re-enabled cross-asset feed:

- **ETH/SOL cross-asset** (`CrossAssetWebSocketClient`) is now instantiated and connected.
  The WS handlers write flat keys (`data_state["eth_price"]`, `eth_imbalance`, `eth_volume`,
  and the SOL equivalents); the live-signal snapshot reads those same flat keys so the
  `eth_*`/`sol_*` feature columns finally carry real lead-lag variance.
  *(Bug fixed 2026-06-08: the snapshot previously read a nested `cross_asset["ETH"]` dict
  that nothing populated, so these features were silently dead.)*

Polling and streaming:

- WebSockets are used for live Binance/Coinbase/futures data.
- Slower institutional and derivatives data are polled periodically.
- Historical note: this model era used a 30-day startup window. The current launcher is configured
  for 1,000 days; see the current implementation ledger and `start.bat`.

---

## 3. Feature Engine

`backend/features.py` currently exposes:

```text
NUM_FEATURES = 136
LOOKBACK = 60
```

The app builds full sequences shaped approximately:

```text
[samples, 60 candles, 136 raw features]
```

The main ensemble then applies a model-local feature mask before flattening:

```text
MODEL_NUM_FEATURES = 63
MODEL_FEATURE_ACTIONS = KEEP,PARITY-FIX
flattened learner row = 60 * 63 = 3780 values
```

This is deliberate: UI, replay, feed-health and future live-only research still need the full
136-column vector, while the train/predict path should not spend hours fitting columns that
historical training cannot learn from.

Feature groups:

- Price and volume returns.
- RSI, MACD, Bollinger position, ROC and EMA relationships.
- ATR, ADX, realized volatility, EWMA volatility and volatility acceleration.
- CVD, order-book imbalance, spread, trade intensity and liquidity state.
- Support/resistance distances and compression.
- Coinbase premium, premium velocity and fair-value deviation.
- Binance/Bybit/global OI changes and OI divergence.
- Funding, long/short positioning and liquidation imbalance.
- Regime features and volatility forecasts.
- Options and institutional proxy features: put/call ratio, options skew, max-pain distance, ATM IV, basis spread/velocity, stablecoin flow, exchange netflow.
- Legacy cross-asset and Chainlink feature slots remain in the 136-column schema but
  are neutral unless those runtime feeds are deliberately re-enabled.
- Macroeconomic features (DXY, US 10Y yield) are now fed from a live, range-validated
  Yahoo Finance poll (`TradFiMacroClient`) with fallback to the last good value; they move
  slowly so they matter mainly for longer-horizon/regime context, not 1m scalps.
- Feature interactions such as RSI x ADX, volume x trend, OBI x ATR and funding x OI.

Important implementation detail:

- Historical candles do not naturally contain live order-flow snapshots.
- `LiveSignalHistoryBuffer` stores live signal snapshots at candle close.
- `build_features_from_klines(..., signal_history=...)` aligns those snapshots with candles.
- On fresh installs, deep historical live-signal coverage starts low and improves as the app runs.

---

## 4. Persistence And DuckDB

Persistent local database:

```text
analytics.duckdb
```

Prediction tables:

```text
predictions_1m
predictions_3m
predictions_5m
predictions_7m
predictions_10m
predictions_15m
```

Important persisted fields:

- prediction id, timestamp, horizon
- Binance price, target price and legacy reference-price columns
- expected move
- confidence
- final signal
- raw model direction
- skip reason
- avoid success
- probability up/down
- agreement
- model directions JSON
- verify deadline
- actual price/move
- hit, price match and move error
- regime
- meta-model context columns

Other tables:

- `feature_importance`: SHAP top-feature records.
- `simulated_trades`: execution simulator outputs.
- `analysis_snapshots`: periodic dashboard state snapshots written about once per minute.
  Current snapshots include support/resistance JSON, indicator snapshot JSON and
  Kronos status JSON.

Why this matters:

- Pending predictions are restored from DuckDB on backend startup.
- Longer-horizon predictions are no longer lost just because Uvicorn reloads.
- Avoid/skip quality can be audited after restarts.

---

## 5. Labeling

The direction model uses triple-barrier labels (`[DOWN, NEUTRAL, UP]` = classes `[0,1,2]`):

- `DOWN`: lower stop/loss barrier hit first.
- `NEUTRAL`: neither barrier hit before timeout.
- `UP`: upper take-profit barrier hit first.

**Target construction (v2 — corrected, must stay consistent across the pipeline):**

- **Time alignment:** the entry is the close of candle `i` — the same candle the last
  feature row is built from — and barriers scan `i+1 … i+h`. This matches live inference
  exactly (the sequence ends at the latest candle and the move is predicted from its close),
  so there is no train/serve bar skew.
- **Cost-floored neutral band:** the barrier `threshold = max(cost_floor, ATR%×0.15)`
  (floor ≈ 0.08%, `BTC_LABEL_COST_FLOOR`). The model is never trained to call UP/DOWN on a
  move smaller than round-trip cost.
- **Verification uses the same band:** each prediction carries `neutralBand` (the same
  cost-floored adaptive value); `PredictionVerifier` grades direction against it, so reported
  accuracy and the auto-learning loop measure the exact target the model was trained on
  (not a hardcoded 0.01%).

The model also trains separate move-size estimators:

- `mag`: fast `HistGradientBoostingRegressor` point estimate of realized absolute move.
- `mag_q25`: lower quantile estimate.
- `mag_q50`: median quantile estimate.
- `mag_q75`: upper quantile estimate.
- `move_size_stats`: near-zero-cost horizon/regime priors used as a light blend so
  fast global quantile models still remain sensitive to TREND/RANGE/VOLATILE behavior.

Prediction output can include:

```text
expectedMove
expectedMoveRange.low
expectedMoveRange.median
expectedMoveRange.high
```

This separates two different questions:

- Direction: should BTC move UP, DOWN or be avoided?
- Magnitude: if it moves, how far is reasonable?

---

## 6. Regime Engine

`backend/regime.py` classifies the market into:

- `TRENDING_UP`
- `TRENDING_DOWN`
- `RANGE`
- `HIGH_VOLATILITY`
- `LOW_VOLATILITY`

The system uses HMM/GMM-style temporal regime handling and regime memory. The model layer maps those fine-grained regimes into expert buckets:

- `TREND`
- `RANGE`
- `VOLATILE`

If one regime has high confidence, inference hard-routes to that expert bucket. If regime confidence is ambiguous, predictions are proportionally blended across experts.

DuckDB and the verifier track performance by regime so weak horizon/regime combinations can be skipped.

---

## 7. Current Ensemble

The classifier ensemble is not four models anymore.

Current base direction models:

| Model | Status | Notes |
|---|---|---|
| XGBoost | Always attempted | CPU `hist` mode; `eval_metric="mlogloss"` (string, so models serialize correctly). |
| LightGBM | Optional dependency | GPU support **probed once at import** (`LGB_DEVICE`); automatic CPU fallback if the build/hardware lacks GPU, so LightGBM is never silently skipped on CPU-only machines. |
| CatBoost | Optional dependency | Noisy-tabular specialist. **Installed in this environment** and active; if missing elsewhere, the ensemble skips it safely (weight 0). |
| Random Forest | Always attempted | Decorrelated tabular seat. It is trained, persisted, loaded, included in OOF stacker inputs, dynamic weights, inventory and per-model live accuracy. |
| HistGradientBoosting | Always attempted | CPU baseline anchor. |
| PyTorch TCN / sequence model | Optional dependency | Defaults to `BTC_DL_ARCH=TCN`; uses CUDA if `torch.cuda.is_available()`, else CPU. `BTC_DL_ARCH=LSTM_GRU` keeps the older recurrent stack available. |
| Logistic Regression | Always attempted | Linear sanity-check baseline; recent sample cap keeps startup bounded. |

SGD log-loss is retired from the main roster. Move-size modeling uses faster histogram
regressors plus cheap regime priors.

**Per-regime class-balancing.** Thin regime buckets often contain only {DOWN, UP} and no
NEUTRAL, which made multiclass models (`num_class=3`) fail on the non-contiguous label set
(`Expected [0 1], got [0 2]`) and silently drop that regime's primary model. Each regime
bucket now tops every class up to ≥3 tiny-noise samples before training, so XGBoost,
LightGBM and CatBoost train reliably in **every** regime. The move-size regressors train on
the *original* (non-augmented) rows so the dummies don't skew magnitude.

`model_inventory` (payload) now also reports `lightgbm_device`, `deep_model_arch`,
`raw_feature_count`, `model_feature_count`, `retired_from_model_count`,
`feature_pruning` and `model_feature_schema_hash`, so the real execution mode and
pruned schema are observable.

Saved model keys:

```text
xgb
lgb
cat
histgb
dl
lr
rf
mag
stackers.pkl
feature_reference.pkl
feature_reference_names.pkl
model_feature_schema.pkl
```

The WebSocket payload exposes `model_inventory`, including optional dependency availability and trained model counts.
Saved bundles include `architecture_version.pkl`; stale bundles retrain once when the
ensemble structure changes.

---

## 8. Fusion, Agreement And Dynamic Weights

The model no longer uses manual sub-signal probability boosting. Market signals are fed as features and learned by the models.

Fusion process:

1. Select or blend regime experts.
2. Predict class probabilities from trained models.
3. Weight models using base weights, backtest/live feedback and regime-specific live accuracy.
4. Normalize weights over available models.
5. Smooth probabilities to reduce jitter.
6. Apply direction lock and hysteresis.
7. Apply confidence and agreement safety filters.
8. Apply live learned signal policy from resolved raw UP/DOWN leans:
   - per horizon
   - per current regime when enough samples exist
   - precision-first threshold selection
   - action-rate tie-breaker so the app can allow more calls where evidence supports it

The OOF stacker remains active, but its cross-validation is capped by
`BTC_STACKER_MAX_SAMPLES` (default `6000`) so startup does not retrain cloned base
models across the full historical matrix.

Agreement is calculated over the models actually available for the current horizon/regime.

Example:

```text
4 available models -> majority threshold = 3/4 = 0.75
5 available models -> majority threshold = 3/5 = 0.60
6 available models -> majority threshold = 4/6 = 0.667
7 available models -> minimum agreement floor = 0.60
```

Prediction payload includes:

```text
agreement
agreementVotes
agreementModelCount
agreementThreshold
pairwise
modelDirs
```

This avoids the stale four-model assumption that previously made agreement interpretation misleading.

---

## 9. Meta-Model And Quality Filters

`backend/meta_model.py` implements a trained trust filter.

It trains from DuckDB once a horizon has enough resolved examples. Until then, it is pass-through.

Meta-model inputs include:

- confidence
- agreement
- regime
- EWMA volatility
- spread
- wall imbalance
- support/resistance compression
- liquidation imbalance
- hour of day
- tradeability
- regime score
- liquidity score
- expected edge

Additional live quality filters in `server.py`:

- warn below 100 resolved predictions per horizon
- raise confidence thresholds for weak direction accuracy
- raise thresholds for weak price-match accuracy
- skip poor horizon/regime combinations
- auto-override (`NEUTRAL` force) for regimes with historically poor accuracy (<50%) based on DuckDB data
- record skip reason and avoid-success outcome

The Decision Center treats BUY/SELL/AVOID separately.

---

## 10. Backtesting And Validation

The app currently runs two validation styles.

Main backtest:

- Uses a recent validation window capped by `BTC_BACKTEST_MAX_ROWS` (default `12000` rows).
- Set `BTC_BACKTEST_MAX_ROWS=0` for a full historical replay.
- Tests on the last 20% of features.
- Reports accuracy, directional accuracy, UP/DOWN accuracy, precision, recall, F1, win rate, profit factor, max drawdown, total trades and confusion matrix.
- Emits progress logs for each horizon and updates `backtest_status` in the WebSocket payload.

Purged walk-forward validation:

- Uses temporal train-past / validate-future splits.
- Uses an embargo gap of `LOOKBACK + horizon`.
- Does not shuffle.
- **`is_below_chance` is judged on directional _precision_, not recall (Phase 15).** Each fold
  reports `directional_accuracy` (recall — of the bars that actually moved, did we catch the
  direction; structurally low on NEUTRAL-heavy horizons like 1m and INFORMATIONAL only) and
  `directional_precision` (of the UP/DOWN calls the model committed to, how many were right — a
  proper 0.50 coin-flip baseline). The below-chance flag fires only when folds make ≥10 directional
  calls AND precision < 0.50, so a model that selectively abstains on noise is not mislabeled as
  "worse than chance." Output adds `mean_directional_precision` and `directional_calls`.
  (Note: this layer trains a proxy balanced RandomForest, so it is an "is there any edge in the
  data" check, not the live ensemble's exact score.)
- Stores walk-forward outputs for all horizons:

```text
walk_forward
walk_forward_1m
walk_forward_3m
walk_forward_5m
walk_forward_7m
walk_forward_10m
walk_forward_15m
```

Limitation:

- This is still a research validation layer, not proof of live tradable edge.
- True proof requires sustained live out-of-sample results, execution costs and drawdown analysis.

---

## 11. Runtime Operations

Startup now separates model availability from expensive research work.

- `HISTORICAL_DAYS` defaults to `30` and can be overridden with `BTC_HISTORICAL_DAYS`.
- Historical 1m/5m/15m candles are cached under `backend/cache`.
- On restart, the app uses cached candles and fetches only the missing gap when the
  cache is recent enough.
- `BTC_HISTORICAL_CACHE_REFRESH_MAX_GAP_SECONDS` defaults to `43200`; older caches
  trigger a full historical refetch.
- `start.bat` sets `BTC_RUN_STARTUP_BACKTEST=0` by default so the app boots from saved models quickly.
- If version-compatible cached backtest results exist in `backend/saved_models/backtest_cache.json`, they are loaded into the UI.
- If startup backtest is enabled, validation is scheduled in the background instead of blocking readiness.
- `start.bat` runs Uvicorn without `--reload` by default, so browser refreshes and long runs do not restart the backend.
- Development reload can be enabled intentionally with `BTC_DEV_RELOAD=1`.

Manual controls:

- `POST /api/relearn` starts a background full model relearn.
- The active model remains in memory while the candidate model trains.
- When the candidate finishes successfully, the server swaps the new ensemble into `ab_runner.primary`.
- If relearning fails, the old model remains active.

Runtime safety & concurrency (Phase 15):

- **Relearn cadence:** auto-learning AND the periodic scheduled relearn share a cooldown
  (`BTC_SCHEDULED_RELEARN_SEC`, default 6h) so the box is not perpetually retraining (a full
  retrain is tens of minutes; firing one every ~30 min starved live predictions and stopped
  signal-history coverage from accumulating). Cheap meta-model / poor-regime refreshes still run.
- **Feature-building runs in a worker thread** (`run_in_executor`) for the live tick, training,
  and backtest paths, so the heavy synchronous numpy build no longer stalls WebSocket pings
  (which had surfaced as stale-feed / ping-timeout disconnects). Each threaded build operates on a
  one-shot **snapshot copy** of `data_state["klines"]` (the feed appends/pops that list in place),
  preventing `list changed size during iteration` and a features-vs-labels row desync.
- **NaN/inf-safe JSON everywhere it leaves the process.** The WS `broadcast()` dumps with
  `allow_nan=False` plus a recursive `_sanitize_nonfinite` fallback (Python's default emits a
  literal `NaN` token, which the browser's `JSON.parse` rejects — silently dropping live updates);
  DuckDB read helpers (`database._f`) map NaN/inf → `None`; `/api/action-log` also degrades to an
  empty feed on error instead of 500ing.
- `POST /api/backtest` starts background validation with live progress.
- `GET /api/runtime-status` exposes boot, backtest, relearn and model-inventory state.

Resilience:

- Derivatives, Bybit, sentiment and institutional slow-data polls are best-effort.
- Missing OI/funding payloads are converted to neutral values instead of crashing the
  prediction loop.

Frontend controls:

- The top bar shows boot time, backtest status and relearn status.
- `Run Backtest` triggers background validation.
- `Relearn Models` trains a candidate model in the background and swaps it in after completion.

---

## 12. Execution Simulator

`backend/trading_simulator.py` simulates trade execution from live signals and serves as the core Expectancy engine.

It includes:

- **Signal Expectancy (USD):** Strict calculation of `(Prob(Win) * Expected_Net_Win) - (Prob(Loss) * Expected_Net_Loss)`.
- Kelly-style position sizing.
- Dynamic slippage based on volume and spread expansion.
- Fill probability from order-book depth.
- TWAP-style slicing via `AlgorithmicExecutionRouter`.
- Maker/taker fee modeling based on `fill_prob`.
- Uncertainty Penalty (VRP Approximation) based on quantile width.
- Rolling Sharpe, Sortino, VaR and drawdown metrics.

**Expectancy Gate:** `server.py` intercepts predictions and uses the simulator to calculate `signal_expectancy_usd`. If it is <= 0, the trade is rejected and forced to `NEUTRAL` (AVOID) to protect capital.

Simulator output is durably logged alongside the predictions in DuckDB (`expectancy_usd`, `expected_slippage_usd`).

---

## 13. A/B Testing

`backend/ab_testing.py` supports primary/challenger model comparison.

Current behavior:

- Primary variant drives dashboard output.
- Challenger can run silently.
- Comparison tracks agreement and accuracy.
- Promotion criteria are exposed in `get_comparison()`.
- **Automated Promotion:** During outcome resolution, if the challenger meets the promotion criteria, the system automatically promotes it to primary, resets the challenger, and logs a critical event.
- Variant predictions and outcomes are persisted in DuckDB table `ab_results`; `restore_from_db()` reseeds in-memory stats after restart.

Current promotion rule:

```text
challenger_verified >= 300
challenger_accuracy - primary_accuracy >= 0.02
bootstrap_lower_bound > 0
```

If those are met, the comparison reports:

```text
promotion_recommendation = promote_challenger
```

Active Variants:

- **Primary (`baseline_v9`)**: Drives the dashboard output and execution simulator.
- **Challenger (`challenger_cat_v1`)**: Runs silently. Configured with a heavy CatBoost weighting for tabular microstructure data, an elevated 1m confidence threshold (0.68), and strict variance avoidance (quantile spread $\ge$ 3.0 skips trade).

---

## 14. Kronos Forecast Layer

`backend/kronos_model.py` provides the BTC chart projection layer.

Current behavior:

- Lazy-loads Kronos on the first scheduled inference instead of during backend import.
- Limits context to 256 candles and prediction length to 60 candles.
- Uses CUDA + FP16 when the local Kronos package/model and GPU support are available.
- Falls back to a deterministic volatility projection when Kronos is not installed or inference fails.
- Exposes `kronos_status` in the WebSocket payload so the UI can show whether the path is true Kronos or fallback.

Important interpretation:

- Kronos projected candles are forecast zones for visual context.
- Direction, BUY/SELL/AVOID, confidence, verification and target-error metrics still come from the main ensemble/verifier pipeline.

---

## 15. Frontend

The app has two main tabs.

Technical + Live Feed:

- chart
- indicators
- predictions
- order flow
- tape
- derivatives
- verification
- backtest metrics

Plain Analysis (beginner-friendly decision dashboard):

- **Signal Expectancy (USD)** hero metric
- global pulse by timeframe
- Kronos/BTC forecast targets
- decision guide
- trust score
- why BUY/SELL/AVOID
- active model votes
- **Volatility Risk Premium (VRP) Quantile Bell Curve**
- **Capital Preservation (Avoid Success) Dashboard**
- action accuracy
- prediction rates and error examples
- support/resistance
- top indicator explanations
- **Regime Health & Profit Factor**
- **Challenger Lab (A/B Testing)**

The top bar includes a Boot chip showing backend startup-to-ready time.

---

## 16. Analytics Queries

`backend/analytics.py` exposes first-class queries for:

- confidence buckets
- calibration
- regime accuracy
- cascade impact
- time-of-day performance
- feature importance
- feature retirement candidates
- simulated expectancy
- avoid/skip success
- meta/quality filter accepted vs rejected outcomes
- skip reasons
- quantile move-range width vs realized miss rate
- analysis snapshots
- A/B promotion criteria

These queries are the main way to check whether the system is improving with live evidence.

**Current main-ensemble pruning path (2026-06-15):** training speed comes from the
model-local mask in `backend/model.py`, not from DuckDB zeroing. `dead_feature_classifier.py`
classifies all 136 raw features; `model.py` passes only 69 `KEEP`/`PARITY-FIX` columns into
the direction, move-size, stacker, SHAP, PSI and live prediction paths. The older DuckDB
feature-retirement flow below is still a reversible hygiene layer, but it is not the main
speed lever.

**Feature retirement is now an actual prune step**, not just a report.
`analytics.apply_feature_retirement(dry_run=False)` finds features that never reach any
horizon's SHAP top-10 over a window and writes them to DuckDB's `feature_retirement_events` table;
`features.build_features_from_klines` then **zeroes those columns** — a safe, reversible
prune that keeps the dynamic-width matrix dimension stable (saved models stay loadable) while
removing the feature's influence. It is guarded (requires enough SHAP evidence, always
keeps core price features, `dry_run=True` by default) so it cannot misfire on sparse data.

---

## 17. Current Known Limitations

The main operational risks are now:

1. Not enough live resolved predictions yet.
2. Live signal-history coverage is only as old as the app's persisted runtime.
3. Optional model availability changes the ensemble; use `model_inventory` (now also reports `lightgbm_device`).
4. CatBoost is installed and active here, but remains an optional dependency elsewhere (skipped safely if absent).
5. LightGBM GPU is probed once with automatic CPU fallback, so it is no longer silently skipped — but GPU speedups still depend on the installed build.
6. The simulator is not live exchange execution.
7. Profitability is unproven until 30-90 days of out-of-sample live evidence exists.
8. Kronos is lazy-loaded and falls back to a deterministic volatility projection unless the local Kronos package/model is installed and compatible.
9. Chainlink and ETH/SOL cross-asset runtime feeds may be disabled in some runtime configs; their raw
   feature columns remain in the 136-feature app schema, while the current main ensemble consumes the
   63-feature pruned model schema.

Recently resolved (no longer limitations):

- **A/B challenger outcomes are now durably persisted** in the `ab_results` DuckDB table.
  Each variant's prediction is logged at record time and resolved (hit/miss) at outcome
  time from its stored direction vs the actual move, so promotion decisions survive
  restarts (`ab_runner.restore_from_db()` reseeds in-memory stats on boot).
  See `analytics.analyze_ab_results()`.
- **Quantile move-range width now feeds the meta-model** (`quantile_spread` is a meta
  feature, persisted per prediction) *and* drives an immediate deterministic skip in the
  live quality filter: q25–q75 spread ≥3× the median move → NEUTRAL; ≥2× → raised
  confidence bar. This gives the benefit before the meta-model has enough data to learn it.

- **Agreement and model-direction diagnostics now use the same regime expert as the
  prediction.** This prevents RANGE-model diagnostics from polluting TREND/VOLATILE
  predictions, live regime-model weights and Decision Center explanations.
- **Advanced live-signal history now records deep microstructure, regime forecast and
  institutional fields.** Those features no longer have to be broadcast as one constant
  snapshot across the whole training matrix once enough live history has accumulated.
- **Kronos/chart payloads are now consistent.** `server.py` sends backend-computed
  RSI/SuperTrend series, support/resistance levels and Kronos status to the frontend.
  The Decision Center shows BTC forecast targets instead of stale Chainlink wording.
- **Backend startup breakers from stale Chainlink/cross-asset references are removed.**
  `server.py` no longer instantiates undefined clients, and the batch launchers use an
  explicit backend reload directory.

Minimum trust targets:

```text
100+ resolved predictions per horizon: early read
500+ resolved predictions per horizon: more useful
30-90 days: real live validation window
```

Judge progress by:

- BUY accuracy
- SELL accuracy
- AVOID accuracy
- price-match rate
- average target-size error
- profit factor
- expectancy
- Sharpe/Sortino
- max drawdown
- regime-specific performance

---

## 18. Institutional Architecture Updates (Phases 1-5 Completed)

The prediction tool has been upgraded into a rigorous quantitative platform capable of sustaining an active execution pipeline. The following architectural systems are now strictly active:

### 1. Data Lineage & Institutional Reporting
- **Persistence:** Every prediction logged to DuckDB `predictions_{h}m` now carries `model_bundle_id` and `feature_schema_hash`. The exact model snapshot is always traceable.
- **Reporting:** `analytics.py` now autonomously generates Institutional Monthly Reports (Expectancy, Win Rate, Profit Factor, Gross Profit/Loss segmented entirely by market regime).

### 2. OOF Stacking & L1 Feature Ablation
- The legacy hardcoded model-weight system is gone. `MultiModelEnsemble` generates Purged Out-of-Fold (OOF) probabilities via `TimeSeriesSplit`.
- A `LogisticRegression` meta-learner with L1 Sparsity (`penalty='l1'`) is trained over the OOF outputs, mathematically setting redundant models to zero weight (automated ablation).

### 3. Institutional Quality Filters (EV > 0)
- The system evaluates purely on **Expected Value (EV)**, no longer raw directional accuracy.
- **Safety Blocks:** The platform aggressively overrides the ML ensemble with an `AVOID` tag if:
  - The feed is stale (`freshness_ms` > 5000ms).
  - The ensemble is heavily confused (Shannon Entropy > 1.05).
  - The Hidden Markov Model detects transition chop (top two regime probabilities are within 5% of each other).
  - 1m/3m fast-horizons see the spread expand > 2.0x normal.
  - 10m/15m slow-horizons see target-quantile variance expand > 2.5x normal.

### 4. High-Frequency Microstructure
- The `OrderFlowAnalyzer` explicitly tracks multi-timeframe burst events (1s/5s/15s) for CVD and OBI.
- **Queue Dynamics:** Removals are mathematically subtracted against trade execution volume to explicitly detect **Cancels**.
- **Wall Migration:** Limit-order aggression is tracked as massive resting walls are pulled and placed closer to the mid-price.
- **Limit-Order Slippage:** The `TradingSimulator` enforces a severe `queue_position_btc` penalty. Limits are never filled unless market volume explicitly trades through the exact depth of the limit-order queue.

### 5. Automated Promotion Gates
- `ABTestRunner` manages live A/B deployments. A challenger model is mathematically blocked from production promotion unless it satisfies all four institutional gates:
  1. `min_verified >= 500` actionable live predictions.
  2. `min_live_days >= 30` active market days.
  3. Positive Expected Value (EV > $0.00).
  4. Profit Factor > 1.20.

**Next steps:** Simply stream live data into the platform for 30 to 90 days and allow the automated machine learning stack to compile a rigorous sample of high-quality verified predictions.

## 19. Institutional Architecture Implementation

In this pass, the system was upgraded into a high-win-rate, institutional-grade quantitative platform retaining the full existing model ensemble.

### ✅ Deep Order Flow (1m/5m Horizons)
- Updated `OrderFlowAnalyzer` to track distinct liquidity events: new, cancelled, and executed liquidity (bid/ask).
- Computed multi-scale order flow imbalances and acceleration.
- Added `book_replenishment_rate` and explicit `absorption_persistence`.
- Exposed these features in `features.py` FEATURE_NAMES.

### ✅ Multi-Timeframe (MTF) Context
- Implemented `mtf_trend_alignment` using 1m, 5m, 15m closed candles.
- Implemented `mtf_volatility_ratio` and `mtf_support_distance`.

### ✅ Rolling Volume Profiles
- Computed point of control (POC) proxies, value area metrics, and nearest LVN.

### ✅ Contextual & Cross-Asset Modifiers
- Enabled ETH/SOL cross-exchange lead-lag features (`eth_btc_lead_lag`).
- Enhanced funding intersections (`funding_oi_interaction`, `time_to_funding`).
- Exposed Polymarket event context stubs.

### ✅ Conformal Residuals & Meta-Model Upgrades
- Refactored move-size training to Conformal Residual Ranges.
- Updated Meta-Model target to positive net PnL prediction rather than pure direction correctness.

### ✅ Data Infrastructure & UI Analysis Cards
- Set up PyArrow Parquet partition writers for orderbook/trade ticks in `database.py`.
- Finalized Plain Analysis UI wiring for the institutional cards.

---

## 20. Decision-Quality & Calibration Stack (Passes 38–43)

This section is the current source-of-truth for how a raw model probability becomes a
**trustworthy, conviction-graded, calibrated, de-biased** signal. It supersedes any earlier
threshold/confidence description.

### 20.1 Adaptive signal thresholding (fixes "always NEUTRAL")
A 3-class direction model's confidence structurally tops out near ~0.55, but the old safety
bar was 0.60–0.70 — above the reachable maximum — so every UP/DOWN was flipped to NEUTRAL.
Fixed in `server.apply_live_quality_filters`:
- Base bars recalibrated to the real scale (`{1:0.50, 3:0.48, 5:0.47, 7:0.46, 10:0.45, 15:0.45}`).
- **Adaptive clamp**: the bar can never exceed the recent 72nd percentile of confidence per
  horizon (so the most-confident ~28% always pass) nor drop below a 0.40 floor. Self-corrects
  to the live confidence distribution. Tracked in a rolling per-horizon buffer (`_recent_conf`).

### 20.2 Conviction engine (the win-rate lever, `model._signal_quality`)
A directional call is only `actionable` when independent sources **confluence**:
ensemble agreement + **Kronos** forecast direction (`_kronos_direction`) + **order flow**
(`_flow_direction`) + regime favorability. Outputs `conviction` (0–100), `convictionGrade`
(A+/A/B/C/WATCH), `confluence`, `confluenceDetail`, `actionable`. Actionable requires
conviction ≥ 62, confluence ≥ 0.5, and **no contradiction**. Persisted to DuckDB and
measurable via `analytics.analyze_conviction_performance()`.

### 20.3 Confidence honesty (two layers)
1. **Per-regime calibration** (`verifier.get_regime_calibration`): scales conviction-confidence
   by `realized_hit_rate / stated_confidence` per regime (clamped 0.6–1.4), demoting
   overconfident regimes.
2. **Live isotonic recalibration** (`verifier.refit_confidence_calibrators`): per-horizon
   isotonic map raw-confidence → realized hit rate, refit every ~12 verified outcomes,
   shrunk toward raw until ~120 samples. Repairs the live inversion where 0.5 conf was hitting
   *less* than 0.4. Applied in `model.generate_ensemble_prediction`.

### 20.4 Direction de-bias (tempered prior correction)
The model learned an UP bias from the up-drifting training window (called UP ~6× more than
DOWN). `train()` stores per-horizon class priors `[DOWN, NEUTRAL, UP]` (persisted); inference
divides each class prob by its base rate tempered by `alpha=0.5`, so the model commits to DOWN
when warranted without flattening real asymmetry.

### 20.5 Kronos as forecaster AND verified signal
- **`kronos_verifier.py`**: records Kronos's implied direction at 5m/15m, resolves hit/miss,
  tracks accuracy, persists to `kronos_predictions` DuckDB table; surfaced as `kronos_accuracy`.
- **Accuracy-weighted ensemble nudge**: the ensemble nudges its probabilities toward Kronos's
  direction ONLY when Kronos has proven >53% live accuracy over ≥20 samples (self-correcting).

### 20.6 Multi-exchange consensus + per-venue verification
- `MultiExchangePriceClient` adds Bybit + KuCoin spot (Binance/Coinbase/Chainlink already
  flow). `build_exchanges_block` → median consensus, per-venue bps deviation, `lead_venue`,
  `fragmentation_bps`.
- **`exchange_verifier.py`** (`PerVenueVerifier`): snapshots each venue's price on a 5m/15m
  call and checks per-venue confirmation; high cross-venue confirmation = clean move,
  divergence = risk flag. Persists to `exchange_verifications`.

### 20.7 UI: Direction Scoreboard
The Plain Analysis tab now leads with a **"BTC Direction Scoreboard — 5m, 15m & 30m"** (conviction
bar + grade + confluence chips + our-model-vs-Kronos with live accuracy) and a
**Multi-Exchange Consensus** strip. The Polymarket "Value Engine" (0 logged predictions,
broken long-dated fair value) is demoted to experimental. `build_scoreboard` feeds the payload.

### 20.8 Audited reality (Pass 42 — read before trusting headline accuracy)
- **Only a minority of the raw 136 features contribute materially** (SHAP top-10 is mostly
  volatility/volume). The main ensemble now trains on a 63-feature pruned schema; live-only
  order-flow / derivatives / institutional / cross-asset research still needs live buffer coverage
  before it can be trusted. This remains a primary limiter of directional accuracy.
- Raw directional accuracy (small n): 1m 57%, 3m 53%, 5m 46%, 7m 41%, 10m 59%, 15m 64%.
- Unanimous model agreement underperforms (contrarian tell) — a future conviction refinement.
- **Everything above activates only on backend restart**; it then sharpens as verified
  samples accumulate. The honest path to win rate is restart → calibration/de-bias (now live)
  → buffer fills → measure conviction performance.

---

## 21. UI Rework, Per-Model Evidence, Price-to-Beat & Chainlink (current pass)

This pass focused on observability and fixing a misleading panel, not new models.

### 21.1 Polymarket "Value Engine" removed
Polymarket's API only lists **long-dated** BTC markets (e.g. "$150k by Dec 31"). The
fair-value model could not price these, producing absurd ~99% edges. The whole fair-value/edge
block was removed from `server.py` and the UI section deleted. `PolymarketClient` discovery/WS
remains harmless; `polymarket_*` tables are untouched.

### 21.2 Price-to-Beat 5m/15m tracker (`backend/price_to_beat.py`)
A self-contained directional game replacing the above. Each 5m/15m round locks a **price to
beat** (current price), records our ensemble call + action + conviction + Kronos call, then
resolves UP/DOWN vs the price to beat once the horizon elapses. Mirrors the Kronos verifier;
persists to DuckDB `price_to_beat`; surfaced as `payload.price_to_beat = {latest, accuracy, recent}`.

**Path-plan shadow log (added 2026-06-30, record-forward only).** When the Layer-2 path forecaster
freezes its plan at window open (`_compute_specialist_heads`), the plan is persisted onto that round's
`price_to_beat` row via `database.log_path_plan(round_id, plan)` — additive columns `path_play`,
`path_style`, `path_p_move_50/100`, `path_p_roundtrip`, `path_p_early`, `path_p_touch_asym`,
`path_pred_high/low`, `path_net_move`. **This does NOT gate any decision** — it is a pure shadow log so
the path head can be graded on *live* rounds (not just the backtest matrix) and so `PATH_CHAMPION_LIFT`
gets a real out-of-sample holdout. Migration is additive (`ALTER TABLE … ADD COLUMN`), applied on boot;
written once per round (guarded by `rnd["_plan_logged"]`). Rationale + the WATCH lift result:
`docs/active/PATH_CHAMPION_LIFT_2026-06-30.md`.

**Two-sided round-trip fade research (corrected 2026-07-01).** `_refresh_live` tracks first and opposite-side
touches around the anchor, but fade events are **paper telemetry only**. The former v4 model used the completed
one-minute touch candle, leaking post-entry high/low information; most touch candles also contained an
unordered TP/stop event. Serving therefore rejects v4 and requires causal v5, which uses completed pre-touch
bars, exact zero-overshoot barrier entry, and excludes ambiguous touch candles. `_trade_signal` emits
`PAPER ONLY`, never an executable fade instruction. The old AUC/top-bucket/proxy-profit claims are retracted.

The only candidate entry path is now the late P(Hold) champion gate. It requires the already-ahead side,
P(Hold)>=0.93, at least $10 distance, 15-120 seconds left, an exact-round quote no more than five seconds old,
spread<=3c, displayed ask depth, and:

`min(P(Hold), 0.91) - executable_ask - crypto_taker_fee - 0.03 > 0`

The 91c cap is the rounded 95% lower bound from one-first-entry-per-round calibration. It avoids pricing
snapshot-level overconfidence as certainty. `PAPER_BET` remains simulation only until at least 500 independent
officially settled entries prove positive after-cost expectancy. Details:
`docs/active/PROFITABILITY_AND_BETTING_VALIDATION_2026-07-01.md`.

### 21.3 Per-model live accuracy (`backend/model_verifier.py`)
Each recorded prediction already carries every base model's argmax vote (`p["modelDirs"]`).
`PerModelVerifier` records one row per model per prediction and resolves each vs the realized
move, so XGBoost/LightGBM/CatBoost/etc. get head-to-head **live** accuracy. Persists to DuckDB
`model_predictions`; surfaced as `payload.model_accuracy = {model: {horizon: {...}}}`.

### 21.4 Chainlink re-enabled
`ChainlinkRESTClient` (CoinGecko proxy) is instantiated and polled best-effort (~30s). The
Multi-Exchange Consensus strip shows a live Chainlink price again (see §2).

### 21.5 Action/Trade log endpoint
`GET /api/action-log?limit=N` → `database.fetch_action_log` unions `predictions_{h}m` newest-first
and returns what was advised, what was expected, and how it resolved (hit/miss/pending).

### 21.6 New "Models & Signals" UI tab
Consolidates price-to-beat cards, the model roster (votes + live accuracy), the action/trade log,
and model inventory. See `UI_GUIDE.md`.

### 21.7 Live Market Pulse + Plain Analysis readability
Pulse cards now show direction + action + confidence + **expected price**. Plain Analysis is
reorganized top-down with the dense sections moved into three collapsible groups.

### 21.8 New DuckDB tables (additive, `CREATE TABLE IF NOT EXISTS`)
`model_predictions`, `price_to_beat`. No destructive migrations; saved models unaffected.

### Polymarket Execution Research Layer

The standalone `backend/polymarket/l2_recorder.py` maintains full public books for active BTC 5m/15m
outcome tokens and writes to `data/polymarket_l2.duckdb`. `l2_book.py` calculates exact taker VWAP at
requested size and bounded maker queue scenarios. This layer is not part of the direction ensemble and
does not place orders. It can veto opportunities whose apparent edge disappears after depth, fee and
latency assumptions. See `docs/active/POLYMARKET_EXACT_DEPTH_AND_QUEUE_SIMULATION_2026-07-01.md`.

### Binance Maker-Conversion Research Layer

`backend/research/binance_maker_conversion_v1/` is an isolated, forward-only
execution experiment for the frozen E09/E10 5s/15s event-time candidates. It
uses public Binance USD-M diff depth and aggregate trades to build a
sequence-validated local book, compares the same original candidates under
taker/taker, maker/taker, maker-fallback/taker and maker/maker routes, and
records conservative queue, partial-fill, adverse-selection, latency and
visible-depth evidence in
`data/research/binance_maker_conversion_v1/shadow.duckdb`.

The layer has no API-key support and no order-submission path. Its source event
bundle, protocol, feature schema and code identities are hashed into every
candidate. Fees are explicit but remain unverified for the user's account, so
promotion is hard-blocked. See
`docs/active/BINANCE_MAKER_CONVERSION_V1_2026-07-28.md`.

### Canonical Model Inventory

The authoritative model-by-model map, including activation status, targets, algorithms, features,
stale artifacts and research-only families, is
`docs/active/ALL_MODELS_PREDICTIONS_AND_FEATURES_2026-07-02.md`. Use it instead of older four-model,
38/61/86-feature or seven-live-horizon descriptions.

---

## 22. FSR-PPO Strategy Challenger

The app now includes an FSR-PPO inspired policy layer adapted from Wang and Wang's
2024 Engineering Applications of Artificial Intelligence paper, "An adaptive financial
trading strategy based on proximal policy optimization and financial signal representation."

Implementation files:

- `backend/fsr_ppo_strategy.py`
- `backend/server.py`
- `backend/database.py`
- `index.html`
- `src/main.js`
- `src/style.css`

### What It Does

The layer is a **measured challenger**, not the primary trading brain.

1. Builds a financial signal representation from recent BTC candles:
   - denoised price path via multi-scale EMA blend
   - noise ratio
   - clean momentum
   - trend strength
   - Hurst/rescaled-range persistence proxy
   - volume pressure
   - signal-quality score
2. Reads the existing ensemble prediction, order flow, Coinbase premium, regime and live accuracy.
3. Produces a PPO-style flexible action:
   - `AVOID`
   - `BUY_SMALL`
   - `BUY_MEDIUM`
   - `SELL_SMALL`
   - `SELL_MEDIUM`
4. Scores expected reward after estimated cost, spread/noise penalty and overtrade penalty.
5. Logs each action to DuckDB table `fsr_ppo_decisions`.
6. Resolves rewards when the parent prediction resolves.
7. Broadcasts:
   - `payload.fsr_ppo`
   - `payload.fsr_ppo_summary`
8. Stores periodic JSON context in `analysis_snapshots.fsr_ppo_json`.

### What It Does Not Do Yet

- It does **not** override the ensemble decision.
- It does **not** place trades.
- It does **not** claim a proven edge.
- It does **not** auto-train PPO during startup.

This is intentional. Reinforcement learning can overfit market data easily. The challenger
must first prove positive live reward, acceptable drawdown and useful skip behavior before
it is promoted into the final decision gate.

---

## 23. Document Sync Map

These markdown files cover different layers. Keep them consistent by updating the right one(s)
for each kind of change:

| Document | Role | Update it when… |
|---|---|---|
| `system_architecture.md` | **Canonical** description of the engine (data, features, models, verification, runtime). | The backend behavior, data flow, models, or persistence changes. |
| `UI_GUIDE.md` | Reference for the **presentation layer** — tabs, cards, metrics, payload→UI map. | Any UI element, payload key, or metric shown to the user changes. |
| `implementation_plan.md` | The **execution roadmap** + highest-ROI accuracy work. | Planning the next changes or recording an accuracy strategy. |
| `task.md` | Flat **checklist** of phases/items (done vs pending). | Completing or adding a concrete work item. |
| `ANALYSIS.md`, `CLAUDE_ANTIGRAVITY_IMPORT.md` | Point-in-time **handoff snapshots** from prior agents. | Generally append-only history; don't treat as current truth. |

**Sync rule of thumb:** a single change usually touches **two** files — the layer doc
(`system_architecture.md` for engine, `UI_GUIDE.md` for UI) **and** the checklist (`task.md`).
Cross-link rather than duplicate: the UI guide points back here for the sync map; this file points
to `UI_GUIDE.md` for screen details.

**Data flow (one line):** live feeds → `features.py` → `model.py` ensemble → quality filters in
`server.py` → DuckDB persistence + WS `payload` → `main.js` render → `UI_GUIDE.md`-documented UI;
verifiers (`*_verifier.py`, `price_to_beat.py`) resolve outcomes back into DuckDB → `analytics.py`.

## 24. Model-Driven Paper Execution (2026-07-31)

The Binance paper engine has five isolated strategies: trend following, breakout, mean reversion,
the deterministic random control and `model_consensus`. The last strategy consumes the final
post-filter 5m ensemble decision only when live calibration, model identity, agreement, meta trust
and conservative post-cost EV all pass. Model decay, confidence collapse, direction reversal,
fixed stop/target and maximum hold all route through the same latency/depth/fee/accounting engine.

The Polymarket Price-to-Beat tracker exposes 17 paper strategies. The newest,
`CHAMPION_DYNAMIC_PAPER_V1`, cannot authorize its own entry: it requires the existing Champion
`PAPER_BET` and then measures ask-to-bid exits after both taker fees. Its dynamic target, stop,
model-invalidation, edge-decay and last-chance exits are forward paper experiments. The Champion
calibration lockdown remains default-off.

Neither venue has a real-order adapter or authority. These strategies measure executable economics;
they are not promoted or claimed profitable. See
`docs/active/MODEL_DRIVEN_PAPER_STRATEGIES_2026-07-31.md`.

## Production Boundary (2026-07-30)

The deployable boundary is a **paper/shadow decision-support service**, not a real-order service.
`start_production.bat` serves the immutable frontend and FastAPI API from one Uvicorn worker after
`backend/production_readiness.py` verifies environment, artifact, feature-contract and champion
gates. `/healthz` is liveness; `/readyz` fails closed on missing models, unhealthy feeds, writers,
tasks or required complete-trade heads. Real Binance and Polymarket execution adapters remain
unavailable and unauthorized.

See
[Production Readiness Audit](../active/PRODUCTION_READINESS_AUDIT_2026-07-30.md)
for the current blockers and operator sequence.
