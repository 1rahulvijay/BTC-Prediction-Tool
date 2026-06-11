# BTC Quantum Trader: High-Accuracy Implementation Plan

Date: 2026-06-07

This file replaces the old Phase 6-only plan. The canonical architecture is
`system_architecture.md`; this document is the current execution roadmap.

## Current Audit Result

The latest Claude, Antigravity and Codex handoff notes were reviewed against the
actual codebase. Several items were documented as complete, but the code still
had gaps that could reduce accuracy or make analytics unreliable.

Fixed in this pass:

- Created/migrated the missing DuckDB `ab_results` table used by durable A/B
  logging.
- Added `quantile_spread` as an explicit meta-model/DuckDB field.
- Made the live quality filter enforce the documented quantile-spread rule:
  very wide move ranges are forced to AVOID, moderately wide ranges require
  higher confidence.
- Made agreement and per-model direction diagnostics use the same active regime
  expert as the prediction, instead of defaulting to RANGE.
- Normalized dynamic model weights even when no backtest accuracy dictionary is
  populated.
- Allowed live regime-specific model weights to work even when backtest weights
  are unavailable.
- Fixed regime feature 73 so it uses transition probability instead of duplicating
  regime entropy.
- Expanded `LiveSignalHistoryBuffer` so deep microstructure, regime forecast and
  institutional fields become learnable over time instead of broadcast constants.
- Made signal-history alignment backward compatible with older saved rows.
- Updated Plain Analysis with active model votes and move-size uncertainty.
- Updated backend requirements with mandatory analytics/model packages:
  `duckdb`, `catboost`, and `shap`.
- Removed stale `server.py` Chainlink/cross-asset runtime references that could
  break backend startup.
- Removed a duplicate Binance futures WebSocket class definition.
- Kept legacy Chainlink/cross-asset feature columns neutral so saved 109-feature
  models remain compatible.
- Made Kronos lazy-loaded, VRAM-limited and status-aware, with a deterministic
  fallback projection when the package/model is unavailable.
- Added backend-computed RSI/SuperTrend chart series, support/resistance payloads
  and Kronos status to WebSocket updates.
- Extended DuckDB `analysis_snapshots` with support/resistance, indicator and
  Kronos status JSON.
- Updated `start.bat` and `run_backend.bat` to use explicit Python paths/reload
  directories so backend file changes are watched more reliably.
- Added timestamped boot/training/backtest terminal logs, including saved-model
  counts and per-model training progress.
- Optimized slow target-size training:
  - point move-size model now uses `HistGradientBoostingRegressor`
  - quantile q25/q50/q75 models now use fast histogram quantile regression
  - move-size and quantile models train on the `GLOBAL` bucket only by default
  - sample caps are configurable through `BTC_MOVE_SIZE_MAX_SAMPLES` and
    `BTC_QUANTILE_MAX_SAMPLES`
  - quantiles can be disabled with `BTC_QUANTILE_REGIME_SCOPE=NONE`
- Added fast linear ensemble diversity:
  - `SGDClassifier(loss="log_loss")` now trains as `SGDLogLoss`
  - Logistic Regression is still kept as a sanity baseline, but uses a recent
    sample cap so it does not dominate startup time
  - `BTC_LINEAR_MAX_SAMPLES`, default `12000`
  - `BTC_SGD_MAX_ITER`, default `350`
  - `BTC_STACKER_MAX_SAMPLES`, default `6000`, caps expensive OOF stacker
    cross-validation to recent rows
  - SGD participates in model weights, agreement votes, pairwise disagreement,
    stacker inputs, verifier mappings and saved-model persistence
- Added near-zero-cost regime move-size priors:
  - median/q25/q75 realized move size by horizon/regime
  - lightly blended into `expectedMove` and `expectedMoveRange`
  - helps keep target-size/error analysis regime-aware while the expensive
    quantile models train globally for speed
- Added a saved-model architecture marker:
  - old bundles without SGD/priors retrain once
  - after the new bundle is saved, normal startup should load saved models again
- Fixed the OOF stacker training failure from the live log:
  - replaced `cross_val_predict` with manual purged/time-series OOF predictions
  - avoids the `cross_val_predict only works for partitions` error
  - allows the stacker to train instead of silently losing the model-combination layer
- Made startup/backtest/relearn operationally safe:
  - startup backtest is disabled by default in `start.bat`
  - cached backtest results still load when available
  - old backtest caches are versioned out so stale validation is not shown after this stacker/runtime change
  - backtests now run in the background with visible progress
  - validation uses the latest `BTC_BACKTEST_MAX_ROWS` rows by default (`12000`)
  - full replay remains available with `BTC_BACKTEST_MAX_ROWS=0`
  - manual `Run Backtest` and `Relearn Models` controls were added to the top bar
  - model relearn trains a candidate ensemble first, then swaps it in only after success
  - `start.bat` no longer runs Uvicorn with reload mode unless `BTC_DEV_RELOAD=1`
- Reduced live-feed disconnect pressure:
  - removed client-side keepalive pings from Coinbase, Polymarket and the legacy cross-asset WebSocket
  - heavy validation/relearn work is moved away from blocking startup readiness

## Current Runtime Defaults

| Setting | Default | Meaning |
|---|---:|---|
| `BTC_HISTORICAL_DAYS` | `30` | Historical candle window used for startup model training/loading context. |
| `BTC_RUN_STARTUP_BACKTEST` in `start.bat` | `0` | Do not start expensive validation automatically during normal launch. |
| `BTC_BACKTEST_MAX_ROWS` | `12000` | Validate on recent rows for faster feedback; set `0` for full replay. |
| `BTC_DEV_RELOAD` | unset | Backend reload disabled during normal long runs; set `1` only while coding. |

## Highest-ROI Accuracy Work

### 0. Measure FSR-PPO Before Promotion

The FSR-PPO inspired challenger is now implemented as a paper-policy layer.

Current behavior:

- Builds denoised BTC signal state.
- Suggests `AVOID`, `BUY_SMALL`, `BUY_MEDIUM`, `SELL_SMALL`, or `SELL_MEDIUM`.
- Scores expected reward after costs/noise/overtrade penalty.
- Logs and resolves actions in DuckDB `fsr_ppo_decisions`.
- Shows the challenger in the Decision Center.

Promotion gate before it can affect final calls:

- 500+ resolved PPO actions.
- Positive average reward after cost.
- BUY/SELL actions outperform AVOID-only baseline.
- No large drawdown cluster in high-volatility regimes.
- PPO improvement remains visible out-of-sample, not just in one short window.

### 1. Prove Live Edge Before Adding Heavy Models

Do not judge the system by a single backtest. The next accuracy improvement must
come from live, resolved outcomes.

Minimum gates:

- 100+ resolved predictions per horizon: early read.
- 500+ resolved predictions per horizon: more useful.
- 30-90 live days: real out-of-sample validation window.

Track:

- BUY accuracy
- SELL accuracy
- AVOID success
- target-size error
- profit factor
- expectancy
- Sharpe/Sortino
- max drawdown
- regime/action breakdown

### 2. Configure A Real Challenger Variant

The A/B framework is ready, but only `baseline_v9` is configured by default.

Recommended challenger:

- Same feature set.
- Different ensemble configuration:
  - CatBoost weight slightly higher.
  - 1m confidence threshold stricter.
  - quantile-spread filter active.
  - no cascade bias unless live cascade impact is positive.

Promotion rule:

```text
challenger_verified >= 300
challenger_accuracy - primary_accuracy >= 0.02
bootstrap_lower_bound > 0
```

Pros:

- Stops emotional/manual promotion.
- Makes model changes measurable.
- Survives backend restarts through DuckDB.

Cons:

- Needs enough resolved predictions.
- A weak challenger still costs CPU during inference.

### 3. Build A DuckDB Analysis Report View

Create either a backend endpoint or static report generator that exposes:

- confidence calibration
- avoid success
- skip reasons
- meta accepted vs rejected
- quantile spread vs realized move error
- A/B variant results
- analysis snapshots
- regime/action accuracy

Pros:

- Prevents guessing from UI impressions.
- Makes documentation and live results auditable.

Cons:

- Needs careful wording for non-traders.
- Early small samples must be labeled clearly.

### 4. Improve Target-Size Modeling

Direction and size should remain separate.

Next model improvements:

- keep current fast point move model
- keep q25/q50/q75 quantile models global by default for startup speed
- use regime move-size priors to preserve TREND/RANGE/VOLATILE context cheaply
- add quantile calibration report:
  - how often actual move falls inside q25-q75
  - average error when range is narrow
  - average error when range is wide

Pros:

- Directly improves the user's requested "$56 expected, $70 actual" tracking.
- Helps avoid signals where direction may be right but target is too uncertain.

Cons:

- Needs many resolved examples.
- Magnitude can be noisier than direction.

### 5. Add A Conservative Online Model

Only after enough live outcomes exist, add a lightweight online learner.

Recommended role:

- advisory vote only
- small ensemble weight
- resettable if drift gets worse

Pros:

- Adapts faster to changing market regimes.
- Useful when old training data gets stale.

Cons:

- Easy to overfit recent noise.
- Should never replace the main ensemble.

### 6. Delay TCN/Transformer Until Evidence Justifies It

TCN is a reasonable future upgrade, but not the next best move unless current
models show persistent underfitting.

Use TCN only when:

- tabular ensemble has enough live evidence
- drift reports show sequence patterns are being missed
- GPU/CPU runtime remains acceptable

Avoid heavy Transformer work for now.

## Non-Trader Plain Analysis Goals

Plain Analysis should answer:

- What is the app saying: BUY/UP, SELL/DOWN, AVOID/SKIP?
- Why is it saying that?
- What could make it wrong?
- Was direction right but price target wrong?
- Is this timeframe mature enough to trust?
- Are safety filters blocking a risky signal?
- Are models agreeing or split?
- Is the expected move range tight or too wide?

Recent UI additions:

- active model votes
- move-size range explanation
- explicit AVOID reason when quantile spread is too wide

Next UI additions:

- `analysis_snapshots` history/export
- model inventory card
- A/B primary vs challenger card once a challenger is configured

## Current Bugs/Risks To Watch

- Live institutional/deep microstructure history only improves after runtime
  accumulates new snapshots.
- Old prediction rows have nulls for newer fields.
- Fresh installs need the updated `backend/requirements.txt`.
- Kronos projections are fallback-only unless the local Kronos package/model is
  installed and compatible.
- Chainlink and ETH/SOL cross-asset feeds are not currently active in `server.py`;
  their feature columns are neutral for compatibility.
- Bybit open-interest units should be validated against exchange documentation
  before treating Binance+Bybit OI as exact global OI.
- Background relearn keeps the active model available, but candidate training can
  still consume CPU/RAM. Do not run manual backtest and manual relearn at the same
  time on a 16 GB laptop unless necessary.
- `BTC_BACKTEST_MAX_ROWS=12000` is a faster recent-window validation. Use
  `BTC_BACKTEST_MAX_ROWS=0` only when the laptop has enough headroom for a full replay.

## Crash Fix And Boot Cache Follow-Up

The first no-reload boot exposed two additional runtime issues:

- Binance futures derivative endpoints can timeout during boot.
- When `open_interest` is `None`, `update_global_oi_history()` previously called
  `.get()` on `None` and killed the main background loop.

Fixed:

- Added safe dict/list/float guards around derivative and Bybit fields.
- `prepare_derivatives_data()` and `update_global_oi_history()` now degrade missing
  OI/funding data to neutral values instead of crashing.
- Boot derivative, Bybit and sentiment snapshots are best-effort with short timeout caps.
- Periodic slow-data polling uses the same best-effort timeout wrapper.
- Historical candles are cached under `backend/cache`.
- On restart, the app loads cached 1m/5m/15m candles and fetches only the missing gap
  when the cache is recent enough.
- `BTC_HISTORICAL_CACHE_REFRESH_MAX_GAP_SECONDS`, default `43200`, controls how far
  back gap-refresh is allowed before a full historical refetch is used.

Follow-up fix:

- `build_meta_context()` now handles `last_backtest=None` safely.
- When startup backtest is disabled and no cache exists, walk-forward context defaults
  to neutral values until the user runs validation.
- Cached JSON walk-forward results are read with either integer or string horizon keys.

## Definition Of "Good Enough"

The app should not be called proven until it can show, over 30-90 live days:

- positive expectancy after simulated costs
- profit factor above 1.2
- no catastrophic drawdown pattern
- BUY and SELL accuracy above chance with enough samples
- AVOID success that actually prevents poor signals
- calibration curve close to stated confidence
- stable performance across at least two distinct regimes

Engineering can improve the tool, but the market decides whether the signal has
edge. The next phase is disciplined measurement plus targeted fixes, not endless
indicator additions.

## How To Push Accuracy Higher (evidence-grounded)

Ordered by ROI, grounded in what the live DuckDB actually shows (not speculation):

1. **Fill the live signal-history buffer — the #1 limiter.** Audited reality: only ~27 of 109
   features contribute (SHAP top-10 is all volatility/volume); order-flow, derivatives,
   institutional and cross-asset features stay inert until `LiveSignalHistoryBuffer` accrues
   *days* of per-candle coverage. Until then the model is a volatility-momentum model. The single
   highest-ROI action is simply **keeping the backend running** so the buffer fills — then the
   richer microstructure features become learnable and accuracy can move past chance.

2. **Trade fewer, on conviction.** Aggregate resolved direction accuracy is ~42–48% (near chance),
   but `actionable=1` (conviction-gated) 1m predictions hit ~83% (19/23) in the live DB. The edge
   is concentrated in the high-conviction bucket. Do **not** loosen the gate to get more signals —
   widen the *sample* of actionable calls first (needs more live days), then measure
   `analytics.analyze_conviction_performance()` before tuning thresholds.

3. **Use the new per-model accuracy to prune/upweight with evidence.** `model_accuracy`
   (DuckDB `model_predictions`) now gives each base model a live hit rate per horizon. Once each
   model has enough resolved votes, drop or down-weight persistently sub-chance models and
   upweight leaders — replacing guesswork with measured contribution (complements the L1 OOF
   ablation already in `MultiModelEnsemble`).

4. **Watch calibration, not just accuracy.** The live isotonic recalibration + per-regime
   calibration repair overconfidence. Track the calibration curve (`analytics` calibration query)
   and the price-to-beat / Kronos head-to-head; demote regimes/horizons whose stated confidence
   keeps overshooting realized hit rate.

5. **Regime-aware routing.** Resolved accuracy varies by regime (e.g. LOW_VOLATILITY/RANGE carry
   most samples). Keep auto-overriding historically weak horizon×regime combos to NEUTRAL, and let
   regime-specific live weights do the routing — don't add a global model to "fix" one bad regime.

6. **Only then consider heavier models (TCN/Transformer).** Justified only once the tabular
   ensemble has enough live evidence *and* drift reports show sequence patterns being missed.
   Premature heavy models overfit recent noise.

7. **Gate everything on samples.** 100+ resolved/horizon = early read, 500+ = useful, 30–90 days =
   real validation. Judge by BUY/SELL/AVOID accuracy, expectancy after costs, profit factor,
   Sharpe/Sortino, drawdown, and per-regime breakdown — surfaced in the Models & Signals tab and
   `analytics.py`.
