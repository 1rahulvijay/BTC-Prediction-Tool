# Accuracy-Focused Execution Roadmap

The architecture is structurally mature. The focus is now evidence, validation,
and careful upgrades that improve live decision quality.

## Phase 1 - Clean Evidence Layer
- [x] Reconcile 94 vs 86 feature count.
- [x] Generate feature schema automatically.
- [x] Add model and data lineage.
- [x] Configure one challenger variant.
- [x] Add sample counts and confidence intervals.
- [x] Build DuckDB report/export primitives.
- [x] Validate feed freshness in live payloads.
- [x] Add simulator disclaimer and conservative cost assumptions.

## Phase 2 - Model Selection
- [x] Generate purged out-of-fold predictions.
- [x] Train logistic stacker.
- [x] Run group feature ablations.
- [x] Add feature retirement table and safe zeroing.
- [x] Cap LSTM/GRU contribution.
- [x] Add global fallback for thin regime experts.
- [x] Calibrate per horizon and regime.

## Phase 3 - Better Quality Filter
- [x] Change meta-target toward positive net outcome.
- [x] Add walk-forward quality and age.
- [x] Add feed freshness checks.
- [x] Add model dispersion and entropy.
- [x] Add transition risk.
- [x] Raise/skip thresholds from expectancy and target uncertainty.
- [x] Train different policies for fast and slow horizons.

## Phase 4 - Microstructure
- [x] Add 1s/5s/15s event windows.
- [x] Split adds/cancels/executions.
- [x] Add cross-exchange lead-lag.
- [x] Add absorption persistence.
- [x] Add wall migration.
- [x] Calibrate fills and slippage from paper-trading assumptions.

## Phase 5 - Proof Gates
- [x] Implement promotion-gate logic.
- [ ] Collect >= 500 resolved actionable predictions.
- [ ] Collect >= 30-90 live days.
- [ ] Prove positive expectancy after costs.
- [ ] Prove profit factor > 1.2.
- [ ] Prove stable calibration.
- [ ] Prove no catastrophic drawdown.
- [ ] Prove positive performance in at least two regimes.
- [ ] Prove AVOID improves outcomes.

## Phase 6 - Kronos & Advanced Charting
- [x] Add `backend/kronos_model.py`.
- [x] Lazy-load Kronos with `max_context=256` and `max_pred_len=60`.
- [x] Provide deterministic fallback forecasts when Kronos is unavailable.
- [x] Export backend RSI and SuperTrend series for charting.
- [x] Send support/resistance levels and Kronos status in WebSocket payloads.
- [x] Render Kronos projected candles, SuperTrend, RSI and S/R overlays.
- [x] Replace stale Chainlink wording in Plain Analysis with BTC/Kronos wording.
- [x] Persist support/resistance, indicators and Kronos status in DuckDB snapshots.
- [x] Surface Kronos status message directly in the Plain Analysis UI.

## Phase 7 - Fast Accuracy Layer
- [x] Add `SGDLogLoss` as a fast linear ensemble vote.
- [x] Add SGD to dynamic weights, agreement votes, stacker inputs, verifier mappings
  and persistence.
- [x] Cap Logistic Regression training with `BTC_LINEAR_MAX_SAMPLES` for faster startup.
- [x] Add regime move-size priors for expected-dollar-move stability.
- [x] Persist move-size priors with saved models.
- [x] Add saved-model architecture marker so old bundles retrain once after ensemble changes.

## Phase 8 - Fast Boot, Background Backtest And Relearn
- [x] Disable automatic startup backtest in normal `start.bat` launches.
- [x] Keep cached backtest results available after restart.
- [x] Add background backtest scheduling with terminal/UI progress.
- [x] Add `Run Backtest` control in the UI.
- [x] Add background candidate-model relearn with active-model swap after success.
- [x] Add `Relearn Models` control in the UI.
- [x] Add `/api/runtime-status`, `/api/backtest` and `/api/relearn`.
- [x] Run Uvicorn without reload by default to protect long sessions.
- [x] Keep development reload available through `BTC_DEV_RELOAD=1`.
- [x] Fix OOF stacker training so the stacking layer can train on temporal folds.
- [x] Cap validation replay with `BTC_BACKTEST_MAX_ROWS`.
- [x] Correct docs to the current 109-feature schema.

## Phase 9 - Boot Crash Resilience And Candle Cache
- [x] Guard derivative and Bybit payloads when REST endpoints return `None`.
- [x] Prevent missing OI/funding data from killing the main background loop.
- [x] Make boot slow-feed snapshots best-effort with timeout caps.
- [x] Make periodic slow-feed polling best-effort with timeout caps.
- [x] Add historical 1m/5m/15m candle cache files.
- [x] Add gap-refresh support for cached historical candles.
- [x] Document `BTC_HISTORICAL_CACHE_REFRESH_MAX_GAP_SECONDS`.
- [x] Make meta-context safe when startup backtest is disabled and `last_backtest` is `None`.
- [x] Support string/int horizon keys for cached walk-forward results.

## Phase 10 - Broad Runtime Safety Scan
- [x] Scan backend for remaining optional nested `.get()` crash patterns.
- [x] Harden feature-engine parsing for missing OI, long/short and sentiment data.
- [x] Harden model/regime/sentiment/stacker lookups for partial or missing state.
- [x] Harden signal-history snapshots for missing cross-asset and macro state.
- [x] Harden server expectancy/simulator and regime-quality lookups.
- [x] Harden CoinGecko, Bybit and Deribit parsing against malformed rows.
- [x] Harden verifier maturity summary against null cache rows.
- [x] Harden multi-exchange Bybit ticker and signal-history long/short row parsing.
- [x] Re-run frontend production build.
- [x] Document what backtest auto-learning currently does and does not control.

## Phase 11 - UI Rework & Per-Model Evidence
- [x] Remove the misleading Polymarket "Value Engine" (long-dated markets it could not price).
- [x] Add self-contained 5m/15m Price-to-Beat tracker (`backend/price_to_beat.py`) + DuckDB `price_to_beat`.
- [x] Add per-model live accuracy verifier (`backend/model_verifier.py`) + DuckDB `model_predictions`.
- [x] Re-enable a real Chainlink BTC/USD feed (CoinGecko proxy) into the consensus strip.
- [x] Add `GET /api/action-log` + `database.fetch_action_log` (what advised / expected / result).
- [x] Add "Models & Signals" UI tab (price-to-beat, model roster, action log, inventory).
- [x] Show expected price + action on the Live Market Pulse cards.
- [x] Reorganize Plain Analysis into top-down collapsible groups for readability.
- [x] Add `UI_GUIDE.md`, Document Sync Map, and the accuracy roadmap.
- [x] Separate Live Market Pulse ensemble/action rendering from Kronos forecast-path rendering.
- [x] Add selected-timeframe Signal Flow explanation.
- [x] Make AVOID/NEUTRAL error examples read as skip outcomes instead of direction bets.
- [x] Add 5m/15m Price-to-Beat tabs with expected/met/correct result language.
- [x] Add feed-stale explanation for signal-history snapshot lag.
- [x] Add Ensemble final and Kronos path rows to Model Roster & Live Accuracy.
- [x] Add Price-to-Beat Signal Rating cards using model/Kronos/flow/regime confluence.
- [x] Guard BTC Direction Scoreboard so final NEUTRAL cannot display as STRONG SELL.
- [x] Rename Plain Analysis to Decision Center.
- [x] Add decision-first cockpit with action, target, trust, risk and confirmation flow.
- [x] Add six-gate decision checklist: models, Kronos, flow, regime, live record and data freshness.
- [x] Add TCN sequence architecture in the existing deep-learning ensemble slot.
- [x] Add adaptive precision-first signal policy from resolved raw UP/DOWN leans.
- [x] Track neutral/wait reason codes and expose neutral reason summary to the UI.
- [x] Show learned signal bar and WAIT reason context in Decision Center.
- [ ] Restart backend so new tables/endpoints/feeds load, then let evidence accrue.

## Phase 12 - Pre-Freeze Feed Fixes (final changes before the live observation window)
- [x] Fix dead liquidation feed: slow-data polls were overwriting the WebSocket-accumulated
  `derivatives.liquidations` (and breaking `handle_liquidation`); now preserved via
  `refresh_derivatives_from_rest()` + a dict guard.
- [x] Replace the DXY/US10Y stub with a live, range-validated Yahoo Finance macro poll
  (`TradFiMacroClient`) + fallback to last good value (best-effort, can't break the loop).
- [x] Verify clean-start: `init_db` creates every table; frozen models + DuckDB can be wiped.
- [x] Error sweep after multi-tool edits (Claude + Gemini + Codex). All backend files compile
  and `import server` succeeds; no duplicate frontend functions; adaptive-threshold policy uses
  consistent int horizon keys and degrades safely; Kronos non-block logic is sound.
- [x] **Bug found & fixed:** ETH/SOL cross-asset was half-wired — handlers write flat keys but
  the snapshot read a nested `cross_asset["ETH"]` dict, so the features were silently dead.
  Snapshot now reads the flat keys → eth/sol features carry real variance.
- [x] **Hardened:** `broadcast()` now iterates a snapshot of `clients` (main_loop and the new
  fast-price broadcaster both call it concurrently, so the list can mutate mid-iteration).
- [x] **Critical bug fixed:** XGBoost failed to train in EVERY bucket with
  `UnboundLocalError: local variable 'xgb'` — a function-local `import xgboost as xgb` in the
  OOF-stacker (model.py ~859) shadowed the module-level import, so the base-model
  `xgb.XGBClassifier` (~604) was unbound. Removed the local import. (Other models were
  unaffected — their imports are module-level.) Requires a restart to retrain WITH XGBoost.
- [x] **OOF stacker class-remap fix:** XGBoost OOF folds can contain only `DOWN` and `UP`
  labels encoded as `{0, 2}`. XGBoost treats that as invalid binary labels, so fold labels are
  now remapped to contiguous local classes and prediction columns are mapped back to
  `[DOWN, NEUTRAL, UP]`. This keeps XGBoost inside the stacker instead of dropping it from thin
  horizon/regime buckets.
- [x] **Reliability:** gated the per-tick trade/depth Parquet archive behind
  `BTC_LOG_TICKS_PARQUET` (default off) — the read-whole-file-then-rewrite-per-tick pattern was
  O(n^2), a corruption source, and unused by the prediction pipeline.

## Phase 13 - "Make it actually work" (DB resilience + stop self-degradation)
Root causes found in the live log: DuckDB was being locked by OneDrive + the Antigravity IDE
language server (so ~half of all prediction/outcome writes were lost), and the auto-learning
loop was thrashing during an in-flight relearn (re-flagging a retrain + raising smoothing every
10s on a half-baked, zero-coverage model → everything collapsed to NEUTRAL).
- [x] **DB off OneDrive:** `BTC_DB_PATH` env override (default in `start.bat` →
  `%LOCALAPPDATA%\BTCQuantTrader\analytics.duckdb`, which OneDrive never syncs).
- [x] **DB lock resilience:** all 25 `duckdb.connect` sites now go through `_connect()` which
  retries transient lock errors with backoff — writes survive contention instead of dropping.
- [x] **Stop auto-learn thrashing:** the feedback loop is skipped entirely while a (re)train is
  in progress (`not is_training`).
- [x] **Relearn cooldown:** auto-relearn won't re-fire within `BTC_AUTO_RELEARN_COOLDOWN_SEC`
  (default 3600s) so a fresh model can accumulate its own verified samples and STABILIZE instead
  of relearning in an endless loop.
- [ ] User action (not code): close the Antigravity IDE's hold on the DB / pause OneDrive,
  let training FINISH before judging, and keep `signal_history.pkl` so coverage accrues.
- [ ] FREEZE: no further model/feature/threshold changes. Run live ~2-3 days and observe
  the Feed Health panel (liquidations, macro, AND eth/sol should move dead → alive) and the
  actionable high-conviction bucket.

## Phase 12 - Tier-1 Accuracy Upgrades
- [x] Upgrade Meta-Model Trust Filter to non-linear XGBoost/LightGBM.
- [x] Implement Continuous AutoML Challenger (Optuna).
- [x] Re-enable ETH/SOL Cross-Asset Lead-Lag features.
- [x] Add FSR-PPO inspired strategy challenger:
  denoised financial signal representation, PPO-style BUY/SELL/AVOID sizing,
  expected reward, DuckDB `fsr_ppo_decisions`, payload fields and Decision Center UI.
- [ ] Let FSR-PPO challenger collect resolved live rewards before allowing it to affect
  final ensemble decisions.
- [ ] Implement Historical Microstructure Backfill (if API available).
- [ ] Implement Live Paper-Trading Integration (if API available).

## Phase 13 - Correctness Audit & Target-Construction Fixes
A line-by-line audit of the prediction/training pipeline found a cluster of bugs, all in
how "a correct UP/DOWN/NEUTRAL" is defined. Fixed together so labeling, inference and
verification share ONE consistent, cost-aware, time-aligned target (bumped
`MODEL_ARCH_VERSION` -> `target-align-v2` to force a one-time retrain).
- [x] **Label/serve alignment** (`features.py`): entry is now `closes[i]` (the candle the
  last feature row is built from), barriers scan `i+1..i+h` - matching inference's 0-bar gap.
  Removed the 1-bar train/serve skew (was `closes[i+1]`). Verified end-to-end + leak-free.
- [x] **Cost-floored label threshold** (`features.py` `compute_adaptive_threshold`): neutral
  band = `max(cost_floor, ATR%*0.15)`, floor 0.08% (env `BTC_LABEL_COST_FLOOR`) so the model
  never trains on sub-cost moves.
- [x] **Verification grades on the same band** (`model.py` -> `prediction_verifier.py`): each
  prediction carries `neutralBand` (same cost-floored adaptive formula); the verifier uses it
  instead of the hardcoded 0.0001, so accuracy measures the target the model trained on.
- [x] **Hysteresis margin** `0.05 -> 0.015` (`model.py`): sized to this model's ~0.5 confidence
  cap so genuine reversals can flip the locked direction instead of holding a stale one.
- [x] Audited & verified CORRECT (no change): class convention (0/1/2 = DOWN/NEUTRAL/UP),
  target-price sign, NaN guards, OOF-stacker class remap, divide-by-zero guards, FSR-PPO
  challenger isolation (cannot touch the live signal), de-bias prior math.
- [ ] Lower-confidence items to revisit later (subtle/by-design, not fixed):
  (a) stacked live calibration (regime-cal then isotonic) has a mild input-chain mismatch;
  (b) confidence-scaling uses hardcoded feature indices `seq[-1, 15/49/50]` (fragile if the
  feature order ever changes).
- [ ] Validate the four fixes with a 24h debug launch (fast retrain), then treat as a new
  baseline - do NOT mix with old DB data.

## Phase 14 - Consistency fixes (external audit follow-up)
Two of these were regressions from the data/ move and the neutralBand fix.
- [x] **DB path split (regression):** `analytics.py` and `automl.py` were still pointing at the
  root `analytics.duckdb` while the server writes to `data/`. Both now `from database import
  DB_PATH` (single source of truth); `automl` artifacts also moved under `data/saved_models`.
- [x] **Neutral-band consistency (regression):** main verifier now uses ~0.0008 (cost floor),
  so the satellite verifiers (`kronos_verifier`, `model_verifier`, `price_to_beat`) were bumped
  0.0002 -> 0.0008 so ensemble / Kronos / per-model / price-to-beat accuracies are comparable.
- [x] **Stale default horizons:** `build_sequences` default was `[1,5,10,15]` (dropped 3m/7m);
  now `[1,3,5,7,10,15]`. (Real callers pass explicit horizons, so impact was latent.)
- [ ] DEFERRED (the "canonical prediction object" refactor): one source of truth for
  raw_direction -> ensemble_direction -> final_action -> verification_result, used identically by
  UI, DuckDB (`signal` column currently stores `direction`), verifier (grades `direction` not
  the final action), agreement display, and target-error (currently blends NEUTRAL). Right
  architectural direction, but a large cross-cutting refactor — do it AFTER the target-alignment
  fixes validate, not on an unproven build.

## Phase 15 — Codex-plan completion, runtime safety & metric honesty (2026-06-09)
Bumped `MODEL_ARCH_VERSION` → `2026-06-09-consistency-v3` (one automatic retrain on next launch).

**CODEX_FIX_PLAN items closed**
- [x] **P3.1 dynamic weighting now has data** (`model.py`): per-model **OOF directional
  accuracy** (leak-free, purged CV) is computed in the stacker block and written to
  `model_accuracies` — flat-by-horizon (GLOBAL = the runtime fallback) and nested-by-regime.
  Short OOF names remapped to canonical (`xgb→xgboost`, `lgb→lightgbm`, `cat→catboost`).
  Logs `[OOF ACC] h=.. reg=.. per-model: ...`. Weighting was previously inert (nothing filled it).
- [x] **P3.2 honest A/B card** (`src/main.js`): backend already reported
  `enabled:false / reason:challenger_not_trained`; the UI now shows "Not active — challenger
  not trained" instead of a misleading `0.0% (0 runs)`. (Challenger is never `.train()`d.)
- [x] **P4.2 Chainlink feature** (`server.py`): confirmed `der["chainlink_price"]` carries the
  real oracle value (features.py col 51 = bounded normalized deviation vs Binance, col 60 =
  fair-value); fixed the stale "kept neutral for compatibility" comment + hardened the cast.

**Stability / churn**
- [x] **Scheduled-relearn 6h cooldown** (`server.py`): the 30-min scheduled relearn fired
  unconditionally on top of auto-learn, so the box retrained ~constantly (~45 min each) and
  coverage never accumulated. Heavy ensemble relearn is now gated behind
  `BTC_SCHEDULED_RELEARN_SEC` (default 21600s); cheap meta-model + poor-regime refreshes still
  run every cycle.

**Runtime crash/serialization safety**
- [x] **Action-log NaN → 500 fixed** (`database.py` `_f`): unresolved rows carry NaN
  `move_error`/`actual_move`; `_f` now maps NaN/inf → None so FastAPI (allow_nan=False) no
  longer 500s on `/api/action-log`. Endpoint also degrades to `{items:[]}` on any error.
- [x] **WebSocket broadcast NaN → null** (`server.py` `broadcast`): `json.dumps` defaulted to
  `allow_nan=True` and emitted literal `NaN`, which the browser's `JSON.parse` REJECTS →
  silently dropped the whole `update` (a likely cause of the stuck loading splash). Now
  `allow_nan=False` fast path + recursive `_sanitize_nonfinite` fallback (handles numpy arrays).
- [x] **Splash can't trap the user** (`src/main.js`): added `clearSplash()` with a 20s
  connected-but-no-update safety timeout + wrapped `renderDashboard` in try/catch so one bad
  payload can't wedge the socket handler.

**Concurrency (introduced by, and fixed alongside, the executor offload)**
- [x] **Feature-building moved off the event loop** (`server.py`, 4 sites — live tick + train +
  2 backtest): heavy synchronous numpy build was stalling WebSocket pings (the
  stale-feed/ping-timeout disconnects). Now via `run_in_executor`.
- [x] **Kline-snapshot race fix** (`server.py`): with the build now threaded, the train/backtest
  prep sites passed `data_state["klines"]` by reference while the feed mutates it in place
  (`.append`/`.pop`) → risk of `list changed size during iteration` AND features (N rows)
  desyncing from labels (N+1). Each site now snapshots the list ONCE and uses that copy for the
  build, timestamps, and closes/highs/lows. (Live build already used a `[-1500:]` slice copy.)

**Measurement honesty**
- [x] **1m "below chance" was a metric artifact** (`backtester.py`, `server.py`): walk-forward
  scored directional *recall* (every actual move must be caught) and flagged `<0.50` — impossible
  to pass on a 61%-NEUTRAL horizon, since the model correctly predicting NEUTRAL counts as a miss.
  Now also computes directional **precision** (of the UP/DOWN calls the model commits to, how
  many are right — proper 0.50 baseline), and `is_below_chance` is based on precision with a
  ≥10-calls-per-fold guard (a model that abstains on noise is selective, not broken). Recall kept
  as informational. Log line now shows `precision=.. (calls=..) recall=.. below_chance=..`.

**Audit follow-up (data integrity)**
- [x] **#4 `chainlink_price` DB column** (`server.py`): normal-prediction logs stored Binance
  `current_price` in the `chainlink_price` column. Now stores the real oracle price (Binance
  close only as cold-start fallback) — reconciles with price-to-beat, which already uses it.

**Boot UX**
- [x] **Startup training no longer blocks the dashboard** (`server.py` lifespan): boot did
  `await train_model()` BEFORE setting `ready` and starting the main update loop, so on any
  fresh train (e.g. right after an arch-version bump, when saved models are stale) the UI showed
  only the live price tick and an EMPTY chart/feeds for the entire ~45 min train. Training is now
  a background task; `ready` is set immediately and the main loop streams klines/feeds/chart at
  once (predictions remain gated on `model.is_trained` and begin when training lands). The startup
  backtest is deferred to inside that task so it never runs against an untrained model.

**🔴 CRITICAL — the "always NEUTRAL" collapse (root-caused from DuckDB + standalone model test)**
- [x] **Regime routing dropped the model's entire output** (`model.py` `_predict_from_regime`):
  DuckDB showed 100% NEUTRAL, zero UP/DOWN, `prob_up=prob_down=0.000` across every bundle; a
  standalone `predict_base` test returned `[0,0,0]` for ALL inputs. Cause: training fits only the
  **GLOBAL** bucket (regime buckets get too few samples), but inference hard-routes by **HMM
  regime label** (TRENDING_UP/LOW_VOLATILITY/…) to `models_by_regime[label]`, which is **empty**
  → returns `[0,0,0]` → the zero-sum safety net in `generate_ensemble_prediction` forced
  `[0,1,0]` NEUTRAL on EVERY prediction. The trained model was never consulted. **Fix:** route to
  GLOBAL whenever the chosen regime has no usable models for the horizon. Verified: `predict_base`
  now returns real varied distributions (5/5 directional at 1/5/15m). **No retrain needed** — this
  is an inference-routing fix; existing saved models work once the code reloads (just restart).
  (This was the deferred P4.3, but far more severe than a "mismatch".)
- [x] **Directional-calls log added** (`database.py` action-log query + `raw_direction`;
  `index.html` + `src/main.js` `renderDirectionalLog`): a "Directional Calls — UP/DOWN" panel that
  filters the feed to moments the model actually leaned UP/DOWN, showing lean → committed action
  (BUY/SELL vs WAIT) → result. Empty-state explains NEUTRAL holds. (Populates once the routing fix
  is live and the model makes calls.)

**External (Gemini) audit follow-up — verified against code, fixed**
- [x] **#1 Sharpe inflation** (`backtester.py` `_sharpe_from_returns`): annualized per-TRADE
  returns by BAR frequency (√(525600/h) ≈ 324× for 5m) → impossible Sharpes (300+). Now reports
  an honest per-trade Sharpe (mean/std), no bogus annualization.
- [x] **#4 direction-blind move_error** (`prediction_verifier.py`): `abs(|actual|−|expected|)`
  scored a right-size/wrong-direction call (predict +$500, market −$500) as $0 (perfect). Now
  compares SIGNED moves → that case scores $1000.
- [x] **#2 sweep flags read-reset → dead features** (`order_flow.py`): `get_summary()` zeroed
  `liquidity_sweeps` on every (~2s) call, so the per-candle snapshot almost always saw False.
  Now stores last-sweep timestamps and reports 1.0 while within 60s (≈1 candle), no consume-on-read.
- [x] **#3 Kelly $0-trade freeze** (`trading_simulator.py`, paper-sim only): Kelly→0 opened
  $0-size trades whose $0 PnL (neither win>0 nor loss<0) diluted win_rate downward, pinning Kelly
  at 0 forever. Now skips opening sub-$1 positions (respects Kelly=0, breaks the spiral).
- [ ] #5 multi-truth (verifier grades lean not action) = the canonical-object refactor, still deferred.

**Polymarket BTC up/down MIRROR** (`price_to_beat.py` + `server.py`)
- [x] Reworked the price-to-beat tracker into a faithful Polymarket mirror: **strict** above/below
  resolution (`neutral_band=0.0`, so +$50 on $63k = UP, not "near reference"), anchored on
  **Binance spot** (not the CoinGecko-Chainlink proxy), windows already UTC-clock-aligned
  (:00/:05/:15…). Grades the model's **lean** (`rawDirection` — the bet you'd place on a binary
  market) and excludes NEUTRAL "no-bet" rounds from the win-rate. Verified: +$50 → UP, hit=True.

- [ ] Still DEFERRED until after the validation run (audit P5 + related): canonical prediction
  object (raw_direction/final_action/verification unified; DB `signal`=direction; verifier grades
  direction not action), NEUTRAL/avoid scoring reconciliation between the main verifier and
  price-to-beat, triple-barrier parity in the optimistic in-sample backtest, and a
  Kronos "real model vs fallback projection" UI badge.
