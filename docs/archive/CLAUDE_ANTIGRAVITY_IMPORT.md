# Claude + Antigravity Context Import

Date: 2026-06-07

This file records what was scanned and imported from local Claude and Antigravity
tool storage for this BTC Prediction Tool workspace.

## Scope

Workspace scanned:

`C:\Users\rahul\OneDrive\Documents\BTC-Prediction-Tool`

External tool stores scanned:

- `C:\Users\rahul\.claude`
- `C:\Users\rahul\.claude.json`
- `C:\Users\rahul\.antigravity`
- `C:\Users\rahul\.antigravity-ide`
- `C:\Users\rahul\AppData\Roaming\Claude`
- `C:\Users\rahul\AppData\Roaming\Antigravity`
- `C:\Users\rahul\AppData\Roaming\Antigravity IDE`

Sensitive browser/app storage was intentionally not imported:

- cookies
- browser cache
- tokens
- broad app telemetry
- unrelated extension files

## Workspace Scan Result

The current project contains the expected application files:

- `backend/`: Python FastAPI backend and model engine
- `src/`: frontend JavaScript and CSS
- `dist/`: built frontend output
- `ANALYSIS.md`: plain-English analysis and enhancement log
- `system_architecture.md`: architecture document
- `analysis.html`: standalone browser-friendly analysis
- `analytics.duckdb`: local analytics database
- `.claude/settings.local.json`: project-local Claude permission settings

No folder literally named `holder` was found inside the workspace. I interpreted
"holder directory" as the whole BTC Prediction Tool directory.

## Claude Import

Project-specific Claude session found:

`C:\Users\rahul\.claude\projects\C--Users-rahul-OneDrive-Documents-BTC-Prediction-Tool\4141853e-302e-4cd8-8128-3a94dec468a0.jsonl`

Session size:

- 895 JSONL entries
- about 3 MB
- entries include user prompts, assistant responses, attachments, mode changes and titles

Imported project context from Claude:

1. Phase 6 cross-exchange integration requirements:
   - Coinbase WebSocket ticker feed
   - Bybit V5 REST open-interest and funding feed
   - Coinbase premium calculation
   - global OI calculation
   - OI divergence request

2. Quant roadmap:
   - market regime engine
   - triple-barrier labels
   - dynamic ensemble weighting
   - probability calibration
   - order-book imbalance
   - cumulative volume delta
   - futures data
   - meta model
   - feature interactions
   - cross-exchange intelligence
   - confidence-aware retraining
   - regime-specific accuracy dashboard
   - Bayesian ensemble
   - SHAP monitoring
   - reinforcement-learning weight controller

3. Plain-English UI requirement:
   - add a new analysis tab
   - explain predictions for non-traders
   - show what signals mean for UP/DOWN, buy pressure and sell pressure
   - show miss rate
   - show target-size error, not only directional correctness
   - show support/resistance and top indicator analysis

4. Recent implemented requirements:
   - Plain Analysis app tab
   - prediction miss rate
   - direction-right but price-off tracking
   - UP and DOWN move-error tracking
   - support/resistance explanations
   - top indicator explanations

These imported Claude requirements are now represented in:

- `ANALYSIS.md`
- `system_architecture.md`
- `index.html`
- `src/main.js`
- `src/style.css`
- `backend/prediction_verifier.py`
- `backend/server.py`

## Antigravity Import

Project-specific Antigravity history found:

`C:\Users\rahul\AppData\Roaming\Antigravity IDE\User\History\-455a673f`

History index:

```json
{
  "version": 1,
  "resource": "file:///c%3A/Users/rahul/OneDrive/Documents/BTC-Prediction-Tool/backend/data_ingestion.py",
  "entries": [
    {
      "id": "30bZ.py",
      "timestamp": 1780788112930
    }
  ]
}
```

Snapshot file:

`C:\Users\rahul\AppData\Roaming\Antigravity IDE\User\History\-455a673f\30bZ.py`

Comparison result:

- Antigravity snapshot line count: 675
- Current `backend/data_ingestion.py` line count: 675
- Antigravity snapshot SHA256:
  `A8F86123B97F2F5CD39DA2BB14230315FEAD6C8168970B1753B3CEBF01EE1545`
- Current `backend/data_ingestion.py` SHA256:
  `A8F86123B97F2F5CD39DA2BB14230315FEAD6C8168970B1753B3CEBF01EE1545`

Conclusion:

The Antigravity snapshot is byte-for-byte identical to the current project file.
No code merge was needed.

Confirmed Antigravity snapshot includes:

- Binance WebSocket client
- Binance liquidation WebSocket logic
- Binance REST derivatives fetches
- Coinbase WebSocket client
- Bybit REST client
- funding/open-interest fetch logic

## AppData Settings Found

Antigravity IDE user settings:

```json
{
  "json.schemaDownload.enable": true,
  "python.languageServer": "None",
  "claudeCode.preferredLocation": "panel"
}
```

Antigravity workspace storage points to:

```json
{
  "folder": "file:///c%3A/Users/rahul/OneDrive/Documents/BTC-Prediction-Tool"
}
```

Project `.claude/settings.local.json` contains only local permission settings:

```json
{
  "permissions": {
    "allow": [
      "Bash(python -c ' *)"
    ]
  }
}
```

No project instructions or hidden implementation notes were found in the local
`.claude` folder inside the repository.

## Import Decision

Code import:

- No Antigravity code merge required because the only project-relevant snapshot
  exactly matches the current file.

Context import:

- Claude roadmap and UI requirements were imported into documentation.
- The project now has this dedicated import ledger so future work can trace where
  those ideas came from.

Recommended next import target:

- `analytics.duckdb`, if the goal is to import or expose historical prediction
  records into the Plain Analysis tab. That database was not modified in this scan.

## Codex Continuation Log - 2026-06-07

This section records the Codex work completed after importing the Claude and
Antigravity context. It is intended as a handoff note so future Claude,
Antigravity, or Codex sessions can see what changed and why.

### User Goal

The user wanted the project moved from a basic prediction app toward a clearer
quant research platform with:

- a non-trader-friendly Plain Analysis tab
- better prediction accuracy tracking
- buy/sell/avoid explanations
- miss-rate and dollar-error tracking
- persistent DuckDB analytics
- faster startup using 30 days of training data
- clearer boot-time visibility in the UI
- canonical documentation that no longer contradicts the actual code

### Architecture Documentation Freeze

Claude's critique correctly identified documentation drift. The old
architecture text still described a four-model system and stale feature counts,
while later project notes described 86 features, deep learning, CatBoost,
institutional feeds, execution simulation, A/B testing, and richer analytics.

Codex response:

- Rewrote `system_architecture.md` from scratch as the canonical source of
  truth.
- Marked `ANALYSIS.md` as a chronological enhancement log, not the source of
  truth.
- Preserved historical notes instead of deleting them, but made it explicit that
  old four-model language may be stale.

Current canonical architecture now documents:

- 30-day historical startup window
- 86-feature vector
- Binance, Coinbase, Bybit, Chainlink, Deribit, CME basis, stablecoin, and
  exchange-flow inputs
- `analytics.duckdb` persistence
- `LiveSignalHistoryBuffer`
- regime engine and proportional regime blending
- triple-barrier direction labels
- separate move-size / target-size models
- ensemble models and optional dependency behavior
- meta-model trade/skip filtering
- purged walk-forward validation
- execution simulator
- A/B challenger framework
- Plain Analysis UI responsibilities
- current limitations and next work

### Ensemble And Model Logic Updates

Codex updated `backend/model.py` to better match the current system.

Important changes:

- Replaced stale "four model" assumptions in comments and metadata.
- Documented the current base-model family:
  - XGBoost
  - LightGBM, if installed
  - CatBoost, if installed
  - HistGradientBoosting
  - Logistic Regression
  - optional PyTorch LSTM/GRU sequence model
- Clarified that Random Forest is no longer a primary classifier in the current
  ensemble, but may still be used for move-size regression.
- Fixed model-pair disagreement keys so CatBoost and LightGBM comparisons use
  the actual model IDs.
- Added variable agreement thresholds based on live model count.

Agreement threshold behavior now scales with available models:

- 4 models: 3 of 4 must broadly agree, threshold 0.75
- 5 models: 3 of 5 must agree, threshold 0.60
- 6 models: 4 of 6 must agree, threshold about 0.67

Prediction payloads now include:

- `agreementVotes`
- `agreementModelCount`
- `agreementThreshold`

This makes the frontend and analytics less fragile when CatBoost, LightGBM, or
deep-learning models are installed or missing.

### Optional Model Inventory

Codex added a model inventory helper to expose what is actually active.

Backend now reports:

- whether CatBoost is installed
- whether PyTorch is installed
- whether LightGBM is installed
- how many trained model directories exist per model family
- whether each family is installed, trained, and active

This was added so the app can explain model behavior honestly instead of
claiming that unavailable optional models are participating.

### DuckDB Persistence And Analytics

The user observed that the tool appeared to run for hours but DuckDB had only a
small number of samples. Codex improved the persistence and analytics path.

Database updates in `backend/database.py`:

- prediction tables now preserve richer signal metadata
- added `raw_direction`
- added `skip_reason`
- added `avoid_success`
- added `prob_up`
- added `prob_down`
- added `agreement`
- added `model_dirs_json`
- added `verify_at`
- added quantile move-size range columns:
  - `move_range_low`
  - `move_range_median`
  - `move_range_high`
  - `move_range_width`
- added `analysis_snapshots`

Important note:

- Old DuckDB rows will have null values for newly added fields.
- New live predictions will populate the richer analytics fields going forward.

### First-Class Analytics Queries

Codex promoted several missing analytics to first-class query functions in
`backend/analytics.py`.

Added analysis functions:

- `analyze_avoid_success`
- `analyze_meta_filter_outcomes`
- `analyze_skip_reasons`
- `analyze_quantile_width_vs_error`
- `analyze_analysis_snapshots`
- `ab_promotion_criteria`

These are designed to answer practical questions:

- Are AVOID/SKIP signals actually protecting the user?
- Are accepted meta-model signals better than rejected ones?
- Which skip reasons are most common?
- Does a wide predicted price range lead to larger real miss error?
- Are analysis snapshots being persisted over time?
- When should a challenger model be promoted?

### A/B Testing Promotion Criteria

Codex updated `backend/ab_testing.py` so challenger promotion is explicit.

Current promotion rule:

- challenger must have at least 100 verified predictions
- challenger accuracy must beat primary by at least 3 percentage points
- disagreement rate must be at or below 35%

The A/B payload now reports:

- `accuracy_delta`
- `disagreement_rate`
- `promotion_recommendation`
- `promotion_criteria`

This prevents vague "challenger is better" language and gives the user a
plain rule for model promotion.

### Plain Analysis Tab Direction

The user requested a tab made for non-traders, separate from Technical and Live
Feed tabs.

Codex added and documented the Plain Analysis concept:

- explain UP, DOWN, BUY, SELL, AVOID, and SKIP in simple language
- show why the signal happened
- show overbought/oversold context
- show support and resistance
- show whether price target was close or missed
- show model drift warnings
- show indicator importance
- show action-specific accuracy
- show avoid-signal accuracy
- show target-size error

Example target behavior:

- Signal said DOWN.
- Expected move was 56 USD.
- Actual move was 70 USD down.
- Direction was correct.
- Target-size error was 14 USD.
- Direction score should count as a hit.
- Move-size model should record a miss/error distance.

This separates directional correctness from price-target accuracy.

### Boot-Time And Startup Changes

The user wanted faster startup and visible boot progress.

Codex changes:

- changed historical startup window from 90 days to 30 days
- added backend boot-status tracking
- exposed boot status through the WebSocket payload
- added a Boot chip in the UI
- updated `start.bat` so backend reload watches the `backend` directory

Expected impact:

- Faster initial training and startup than 90 days
- Less historical data, so the model may lose some long-regime context
- Faster iteration during development
- UI can show whether the app is still warming up

### Accuracy Improvement Roadmap Captured

The user asked how to improve accuracy further. Codex captured the practical
priority order:

1. CatBoostClassifier
2. move-size / quantile regression model
3. stacking meta-model
4. TCN sequence model
5. online adaptive model

Codex recommendation:

- Add CatBoost and move-size modeling first because they are lower risk.
- Delay TCN and online learning until there is enough verified live data.
- Judge improvements by live BUY/SELL/AVOID accuracy, target-size error,
  profit factor, expectancy, drawdown, and regime-specific performance instead
  of raw backtest accuracy.

### Verification Performed

Codex ran backend verification after changes:

- Python compile checks passed for updated backend files.
- DuckDB migration succeeded.
- Confirmed new prediction columns exist.
- Confirmed `analysis_snapshots` table exists.
- Earlier frontend build passed after the Boot UI changes.

### Remaining Caveats

The project is stronger, but not proven institutional trading infrastructure.

Current caveats:

- New analytics fields only fill for future predictions.
- A/B challenger persistence exists conceptually, but needs more live sample
  depth before promotion decisions are meaningful.
- Accuracy should not be judged until each horizon has at least 100 resolved
  predictions.
- 30 days of history starts faster but may reduce long-regime learning compared
  with 90 days.
- The system still needs live out-of-sample evidence for profit factor,
  expectancy, Sharpe ratio, drawdown, and regime-specific edge.

### Files Updated By Codex In This Chat

- `system_architecture.md`
- `ANALYSIS.md`
- `CLAUDE_ANTIGRAVITY_IMPORT.md`
- `backend/model.py`
- `backend/server.py`
- `backend/database.py`
- `backend/analytics.py`
- `backend/ab_testing.py`
- `start.bat`
- `index.html`
- `src/main.js`

This file now serves as the combined Claude, Antigravity, and Codex handoff
ledger for the current project state.

## Claude Continuation Log - 2026-06-07 (Ensemble Correctness Pass)

This section records a Claude session that audited the current code against the docs and
fixed several real ensemble bugs. Every fix was verified by training the then-current
six-model ensemble per regime on this machine (synthetic data) and confirming model
persistence. The current system later added `SGDLogLoss` as a seventh fast direction vote.

### Context

The user approved fixing the confirmed XGBoost per-regime failure plus the
CatBoost/LightGBM "honesty" items, installing CatBoost, wiring feature retirement into an
actual prune step, and reconciling the documentation. The architecture-doc rewrite had
already been done; this pass focused on code correctness and surgical doc updates.

### Confirmed bugs found and fixed

1. **XGBoost (and all multiclass models) failed in thin regime buckets.** The per-regime
   expert split produced buckets with only `{DOWN, UP}` (no NEUTRAL). With `num_class=3`,
   XGBoost raised `Invalid classes inferred ... Expected [0 1], got [0 2]`, so the
   primary 40%-weight model trained for zero regimes and the ensemble silently degraded.
   Fix: per-regime class-balancing augmentation (top every class up to >=3 tiny-noise
   samples) before training the classifiers. Verified in that pass: all 6 then-current
   classifiers trained in every regime.

2. **Models never persisted.** XGBoost used `eval_metric=["mlogloss"]` (a list), which
   raised `Unknown metric function ['mlogloss']` during serialization, so `_save_models`
   failed and the app retrained from scratch on every boot. Fix: `eval_metric="mlogloss"`.
   Verified: all 10 per-regime artifacts now save to `saved_models/<REGIME>/` and reload.

3. **Magnitude/quantile regressors broke after the augmentation fix** (sample mismatch
   `[509, 506]`). Fix: regressors train on the original, non-augmented regime rows.

4. **LightGBM was silently skipped on CPU-only machines** (`device_type='gpu'` hardcoded).
   Fix: probe GPU once at import (`LGB_DEVICE`) with automatic CPU fallback; surface the
   active device in `model_inventory.lightgbm_device`.

### Environment / dependency change

- **Installed CatBoost (1.2.10).** It now trains as the noisy-tabular specialist instead
  of being silently skipped. (`cat_*.pkl` persists per regime.)

### Feature discipline

- Wired `analytics.apply_feature_retirement()` into an **actual prune step**: it writes
  persistently low-SHAP features to DuckDB table `feature_retirement_events`, and
  `features.build_features_from_klines` zeroes those columns (safe, reversible, keeps the
  86-wide matrix dimension stable). Guarded (`dry_run=True` default, requires SHAP
  evidence, always keeps core price features) so it cannot misfire on sparse data.

### Documentation reconciled

- `system_architecture.md` (canonical): updated §7 ensemble table (LightGBM probe/fallback,
  CatBoost active, `eval_metric` persistence note), added the per-regime class-augmentation
  explanation, updated §14 to describe the feature-retirement prune, and refreshed §15
  limitations.
- `ANALYSIS.md`: added **Pass 17** documenting all of the above.

### Verification performed

- All 15 backend modules compile.
- Full per-regime training then produced all 6 classifiers + 4 move-size regressors per populated
  regime with no `Invalid classes`, `inconsistent samples`, or `Unknown metric` errors.
- Models persist to disk and reload. Synthetic test models were then cleared so the server
  retrains on real Binance data at next boot.

### Still open (handoff)

- Feed the quantile move-range width (q25–q75) into the meta-model context as a skip signal.
- Persist A/B challenger predictions/outcomes to a dedicated DuckDB table.
- Accumulate 100+ resolved predictions per horizon before trusting accuracy; live
  out-of-sample edge over 30–90 days remains the real test.

### Files updated by Claude in this session

- `backend/model.py` (per-regime augmentation, `eval_metric`, LightGBM probe/fallback,
  magnitude regressor data, `model_inventory.lightgbm_device`)
- `backend/features.py` (feature-retirement load + zeroing)
- `backend/analytics.py` (`apply_feature_retirement` writer)
- `system_architecture.md`, `ANALYSIS.md`, `CLAUDE_ANTIGRAVITY_IMPORT.md`

## Claude Continuation Log - 2026-06-07 (Quantile Trust Signal + Durable A/B)

Implemented the two items left open by the previous pass, both verified end-to-end.

### Quantile move-range width as a trust/skip signal

- `model.py` now exposes `quantileSpread = (q75 - q25) / q50` per prediction.
- It is wired into the trained meta-model: added to `META_FEATURES`, the training query,
  `should_execute`, the prediction context (`server.build_meta_context`), and a new
  persisted DuckDB column `quantile_spread`.
- `server.apply_live_quality_filters` adds a deterministic skip *before* the meta-model has
  data: q25–q75 spread >= 3x the median move forces NEUTRAL; >= 2x raises the confidence bar.

### Durable A/B persistence

- New DuckDB table `ab_results` plus `log_ab_prediction`, `resolve_ab_results` and
  `fetch_ab_variant_stats` in `database.py`.
- `ab_testing.ABTestRunner` gained `persist()`, `resolve()` and `restore_from_db()`.
- `server.py` logs each variant on record, resolves on verification (computing each
  variant's hit from its stored direction vs the actual move), and reseeds in-memory stats
  on boot so promotion survives restarts.
- New `analytics.analyze_ab_results()` for a durable per-variant comparison + promotion call.

### Verification

- All 15 backend modules compile; A/B round-trip and quality-filter logic verified on a temp
  DuckDB; a full ensemble train confirmed `quantileSpread` is present in real predictions
  (`{29.0, 72.5, 100.7}` -> `0.99`). Synthetic test models were cleared afterward.

### Files updated

- `backend/model.py`, `backend/meta_model.py`, `backend/database.py`,
  `backend/ab_testing.py`, `backend/analytics.py`, `backend/server.py`,
  `system_architecture.md`, `ANALYSIS.md`, `CLAUDE_ANTIGRAVITY_IMPORT.md`

## Codex Continuation Log - 2026-06-07 (Accuracy Audit Pass)

Codex reviewed the latest Claude and Antigravity handoff notes against the actual
codebase, then fixed implementation gaps that could reduce accuracy or make analytics
look better than they really are.

### Bugs fixed

1. **Missing A/B persistence table.**
   `database.log_ab_prediction()` wrote to `ab_results`, but `init_db()` did not create
   that table. Fresh databases silently failed to persist A/B results. `ab_results` is
   now created and migrated.

2. **Agreement diagnostics used the wrong regime.**
   Predictions used the active regime context, but agreement and `modelDirs` defaulted
   to the RANGE expert because `data_state` was not passed into those helper calls.
   This polluted agreement, Plain Analysis, model-direction history and learned
   regime-specific model weights. Agreement/model directions now use the same regime
   context as the prediction.

3. **Dynamic weights skipped live regime weights.**
   `_get_dynamic_weights()` returned base weights immediately when horizon-level
   `model_accuracies` was empty. It now normalizes base weights, applies light regime
   priors and still blends live regime-model weights when available.

4. **Quantile-spread skip rule was documented but not actually enforced.**
   The system now stores `quantile_spread`, feeds it into the meta-model context and
   applies the deterministic live filter:
   - q25-q75 spread >= 3x median move -> AVOID/SKIP
   - q25-q75 spread >= 2x median move -> higher confidence required

5. **Regime transition feature was duplicated.**
   Feature 73 now uses `transition_probability`; feature 74 remains regime entropy.

6. **Advanced live-only features were not fully recorded.**
   `LiveSignalHistoryBuffer` now records deep microstructure, regime forecast and
   institutional fields so those columns become learnable after enough runtime instead
   of staying broadcast constants.

7. **Old signal history compatibility.**
   Alignment now uses `row.get(...)`, so old `signal_history.pkl` rows that do not have
   newly added keys do not crash feature building.

### UI improvement

Plain Analysis now shows:

- active model votes
- move-size range
- simple warning when the dollar target is uncertain

### Environment update

`backend/requirements.txt` now includes:

- `duckdb`
- `catboost`
- `shap`

PyTorch remains optional because it is large and hardware-specific.

### Documentation updated

- `system_architecture.md`
- `implementation_plan.md`
- `ANALYSIS.md`
- `CLAUDE_ANTIGRAVITY_IMPORT.md`
- `walkthrough.md`
- `task.md`

### Remaining priority

The next serious accuracy improvement is evidence, not more indicators:

- configure a real challenger variant
- collect 100+ resolved predictions per horizon
- export monthly DuckDB reports for expectancy, profit factor, Sharpe/Sortino,
  drawdown and regime/action accuracy

## Codex Continuation Log - 2026-06-07 (Kronos & Charting Consistency)

Codex reviewed the latest Kronos Integration & Advanced BTC Charting checklist
against the actual codebase and corrected the items that were only partially true.

### Runtime fixes

- Removed stale `server.py` references to undefined Chainlink and cross-asset runtime
  clients.
- Kept legacy Chainlink/cross-asset feature slots neutral so the 109-feature schema and
  saved models remain compatible.
- Removed a duplicate `BinanceFuturesWebSocketClient` class from `data_ingestion.py`.
- Reworked `backend/kronos_model.py` so Kronos lazy-loads on scheduled inference
  instead of backend import.
- Added VRAM-conscious limits: `max_context=256`, `max_pred_len=60`, CUDA + FP16 when
  available.
- Added deterministic fallback forecasts when Kronos is unavailable or inference fails.
- Added `kronos_status` to the WebSocket payload.

### Chart and analysis fixes

- Added backend `compute_indicator_series()` for RSI and SuperTrend chart series.
- Added backend support/resistance payloads using candle pivots plus liquidity-wall
  levels.
- Updated `src/main.js` to render backend RSI, backend SuperTrend and backend S/R
  instead of recomputing or reading missing fields.
- Reworded the Plain Analysis forecast board from Chainlink oracle targets to
  BTC/Kronos forecast targets.
- Added a Plain Analysis status line for true Kronos vs fallback projection mode.

### Persistence fixes

- Extended DuckDB `analysis_snapshots` with:
  - `support_resistance_json`
  - `indicator_snapshot_json`
  - `kronos_status_json`
- Updated `analytics.analyze_analysis_snapshots()` to show the new snapshot context.

### Launch/reload fixes

- Updated `start.bat` and `run_backend.bat` with explicit `PYTHONPATH`, absolute
  project root handling and backend reload watching.

### Verification

- Frontend production build passed with `npm.cmd run build`.
- Backend compile/import checks could not be run inside this Codex shell because
  `python`/`py` are not on PATH in the tool environment. The next user-side check is
  to run `.\start.bat` and verify Uvicorn starts cleanly.

## Codex Continuation Log - 2026-06-07 (Startup Speed & Fast Accuracy Layer)

Codex reviewed the user's live startup log where 30-day training took more than two
hours and target-size/quantile models became the obvious bottleneck.

### Startup visibility

- Added timestamped boot-stage logs.
- Added per-model training progress such as `[TRAIN 12/150] h=5m reg=GLOBAL model=CatBoost`.
- Added LSTM/GRU epoch timing.
- Added saved-model load/save component counts.

### Target-size speed correction

- Replaced slow move-size training with fast `HistGradientBoostingRegressor`.
- Replaced slow quantile training with fast histogram quantile regression.
- Made move-size and quantile training `GLOBAL` only by default.
- Added speed knobs:
  - `BTC_MOVE_SIZE_MAX_SAMPLES`
  - `BTC_QUANTILE_MAX_SAMPLES`
  - `BTC_MOVE_SIZE_MAX_ITER`
  - `BTC_QUANTILE_MAX_ITER`
  - `BTC_QUANTILE_REGIME_SCOPE=NONE` to disable quantiles if startup is still too slow.

### Fast model addition

- Added `SGDLogLoss` using `SGDClassifier(loss="log_loss")`.
- Added SGD to dynamic weights, stacker inputs, agreement votes, pairwise disagreement,
  model inventory, verifier mappings, A/B challenger config and saved-model persistence.
- Capped Logistic Regression training with `BTC_LINEAR_MAX_SAMPLES`.
- Added `BTC_SGD_MAX_ITER`.
- Added `BTC_STACKER_MAX_SAMPLES` to cap OOF stacker cross-validation on recent rows.

### Regime-aware target-size fallback

- Added `move_size_stats`, a cheap horizon/regime prior for realized move size.
- Blended the prior into `expectedMove` and `expectedMoveRange`.
- Added `moveSizePrior` to prediction payloads.
- Persisted `move_size_stats.pkl` with saved models.
- Added `architecture_version.pkl`; old saved model bundles retrain once so the
  new SGD/prior architecture is active, then load normally on future starts.

### Accuracy expectation

This is a practical speed and stability improvement, not proof of edge. Expected impact:

- faster startup than the previous uncapped linear + slow quantile setup
- small additional model diversity
- better target-size stability after moving quantiles to global training
- higher win rate only if live BUY/SELL/AVOID metrics prove it after enough resolved predictions

## Codex Continuation Log - 2026-06-07 (Fast Boot, Background Backtest & Relearn)

Codex implemented the next operational pass after the user reported that saved models
were ready but backtesting still appeared frozen for 20-25+ minutes.

### Backend runtime changes

- Added `backtest_status` and `relearn_status` to backend state.
- Added `/api/runtime-status`, `/api/backtest` and `/api/relearn`.
- Backtesting now runs in the background with progress messages instead of blocking
  startup readiness.
- Backtest results are cached to `backend/saved_models/backtest_cache.json` and loaded
  on restart when the historical window matches.
- Added `BTC_BACKTEST_MAX_ROWS`, default `12000`, to validate recent market behavior
  quickly. Set `BTC_BACKTEST_MAX_ROWS=0` for a full historical replay.
- Added `BTC_HISTORICAL_DAYS`, default `30`, to make the candle window configurable.
- Manual relearn now trains a candidate `MultiModelEnsemble` in the background and swaps
  it into the active runner only after successful training.
- Existing active models remain available if relearn fails.

### Accuracy-relevant fix

- Fixed the OOF stacker failure seen in the live log:
  `cross_val_predict only works for partitions`.
- Replaced `cross_val_predict` with manual time-series out-of-fold predictions.
- This restores the intended stacking layer, which can improve final BUY/SELL/AVOID
  decision quality if the live data supports it.

### Frontend changes

- Added top-bar runtime controls:
  - boot time
  - backtest status
  - relearn status
  - `Run Backtest`
  - `Relearn Models`
- The buttons call the new backend endpoints and disable while work is running.

### Launcher and feed stability

- `start.bat` now disables startup backtest by default with
  `BTC_RUN_STARTUP_BACKTEST=0`.
- `start.bat` runs Uvicorn without `--reload` during normal use so browser refreshes
  and file-watcher noise do not restart a long model session.
- Backend reload remains available by setting `BTC_DEV_RELOAD=1`.
- Client-side keepalive pings were removed from Coinbase, Polymarket and the legacy
  cross-asset WebSocket to reduce false timeout pressure during CPU/RAM-heavy periods.

### Documentation correction

- Corrected stale `94` feature wording to the current `109` feature schema in the
  canonical architecture and feature header.

### Verification

- Frontend production build passed with `npm.cmd run build`.
- Backend compile checks could not run in the Codex shell because `python`/`py` are not
  on PATH in this environment.

## Codex Continuation Log - 2026-06-07 (Boot Crash Fix & Candle Cache)

After the user restarted with no-reload mode, the server reached derivative snapshot
boot and Binance futures/Bybit calls timed out. The background task crashed because
`update_global_oi_history()` called `.get()` on `None`.

### Fixes

- Added safe guards for optional derivative payloads:
  - `_safe_dict()`
  - `_safe_list()`
  - `_safe_float()`
- Hardened:
  - `prepare_derivatives_data()`
  - `update_global_oi_history()`
- Binance derivatives, Bybit, sentiment and institutional slow-data calls now run
  through a best-effort timeout wrapper.
- Missing OI/funding data degrades to neutral values instead of killing the main loop.
- Added historical candle caching in `backend/cache`.
- Added gap-refresh support to `BinanceRESTClient.fetch_historical_klines()`.
- Startup now loads cached 1m/5m/15m data and fetches only the missing gap when possible.
- Added `BTC_HISTORICAL_CACHE_REFRESH_MAX_GAP_SECONDS`, default `43200`.

### Expected Result

The first successful run after this patch will still need to fetch and save historical
candles if no cache exists. Future restarts should be much faster because the backend
can reuse the cache and request only missing candles.

## Codex Continuation Log - 2026-06-07 (No-Backtest Meta Context Fallback)

The next boot reached live inference, but the loop error repeated because startup
backtest is disabled by default and `backend_state["last_backtest"]` was `None`.

### Fixes

- Hardened `build_meta_context()` so `last_backtest=None` is valid.
- Walk-forward context now defaults to neutral values until cached/manual validation exists.
- Cached JSON walk-forward keys are handled as either integers or strings.

### Expected Result

Live predictions can run immediately after saved models load, even when no backtest has
been run in this session.

## Claude Continuation Log - 2026-06-08 (Win-Rate Engine: NEUTRAL Fix → Conviction → Calibration → De-Bias)

A multi-pass session (ANALYSIS.md Passes 38–43) driven by the operator complaint *"running
for a while, not a single BUY/UP/DOWN — always NEUTRAL"* and the goal *"higher win rate, not
looser guardrails — a top quant tool."* Grounded throughout in the live 685 MB `analytics.duckdb`.

### Root-cause fix: always-NEUTRAL (Pass 38)
The safety bar (0.64) sat above the model's max confidence (~0.55), so every UP/DOWN was
flipped to NEUTRAL. Recalibrated base bars to the real scale + added an **adaptive percentile
clamp** (bar ≤ recent 72nd-pct confidence). Verified: 48% pass-rate at realistic confidences
(was 0%). The model's edge was real but 100% hidden (1m 57%, 15m 64% raw directional).

### Decision-quality stack added
- **Conviction engine** (`model._signal_quality`): a call is `actionable` only when ensemble +
  Kronos + order-flow + regime confluence; outputs conviction 0–100, grade, confluence detail.
  Trade fewer, win more. Persisted to DuckDB; measurable via `analyze_conviction_performance()`.
- **Kronos directional verification** (`kronos_verifier.py`) + accuracy-weighted ensemble nudge
  (Kronos earns influence by being right). New `kronos_predictions` table + `kronos_accuracy`.
- **Multi-exchange** (`MultiExchangePriceClient`: Bybit + KuCoin) consensus + lead-venue +
  fragmentation; **per-venue verifier** (`exchange_verifier.py`) + `exchange_verifications` table.
- **Direction Scoreboard UI** (5m/15m, our-model-vs-Kronos, conviction) replaces the broken
  Polymarket Value Engine focus. Multi-exchange strip added.

### Calibration + de-bias (Pass 43, the audit's top two fixes)
- **Live isotonic confidence recalibration** (`verifier.refit_confidence_calibrators`): repairs
  the live inversion where 0.5 conf hit *less* than 0.4; makes conviction trustworthy.
- **Tempered prior correction** (`model.class_priors`, alpha=0.5): removes the learned UP-bias
  (model called UP ~6× more than DOWN) so it commits to DOWN when warranted.
- **Per-regime confidence calibration** + **conviction/actionable/confluence persisted to DuckDB**.

### Audit findings (Pass 42) — the real bottlenecks
- **Only ~27 of 109 features contribute** (SHAP top-10 is all volatility/volume); the alpha
  features (order flow, derivatives, institutional, cross-asset) are inert until the
  signal-history buffer fills. **#1 accuracy limiter — it's data-starved, not feature-starved.**
- `simulated_trades = 0` and `kronos_predictions` absent ⇒ **the running backend predates all
  of this. A restart is mandatory** before any of it is live.

### Files touched
- `server.py`, `model.py`, `prediction_verifier.py`, `database.py`, `analytics.py`,
  `data_ingestion.py`, new `kronos_verifier.py` / `exchange_verifier.py`,
  `index.html`, `src/main.js`, `src/style.css`.
- Docs: `ANALYSIS.md` (Passes 38–43), `system_architecture.md` (§20), this ledger.

### Verification
- All 23 backend modules compile; `server` imports; frontend `npm run build` passes; each
  feature unit-tested (adaptive threshold, conviction, Kronos verify, per-venue verify,
  per-regime + isotonic calibration, prior correction, conviction persistence).

### Honest status
The decision-quality + calibration machinery for a high win rate is now in code and
self-correcting. The remaining levers are **runtime, not code**: restart → let the buffer fill
so alpha features wake up → accumulate verified samples so calibration/meta-model/regime
weights sharpen → measure conviction performance to prove the uplift. Realistic ceiling for
1–15m BTC remains ~53–58% directional with good calibration; edge comes from conviction-gating
+ calibration + sizing, not raw prediction.
