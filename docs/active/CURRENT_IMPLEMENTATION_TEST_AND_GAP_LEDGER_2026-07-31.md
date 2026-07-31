# Current Implementation, Test And Gap Ledger

Date: 2026-07-31

Reconciled through commit: `0085498`

Purpose: canonical answer to what is implemented, what is tested, what the results mean, what is
blocked, and how each blocked item can be completed.

This document supersedes point-in-time completion/status claims in older roadmap, architecture and
model-catalog documents. Executable contracts still take precedence over prose:

- `backend/features.py`
- `backend/model_contract.py`
- `backend/model_registry.py`
- `backend/production_readiness.py`
- `backend/check_feature_contract.py`
- `.github/workflows/invariants.yml`
- `start.bat`

## 1. Honest Verdict

The source code, Windows launcher, paper accounting, safety boundaries, frontend build and
deterministic validation suite are implemented and passing.

The application is **not currently model-serving ready**:

- the saved main ensemble is incompatible with the current v14 contract;
- all 11 standalone serving artifacts lack current identity manifests;
- calibration is inactive while compatible source models are unavailable;
- the 1,265-day retrain and explicit challenger promotion have not completed.

The application is **not real-money production ready**:

- production preflight currently reports 17 unmet prerequisites;
- no real Binance or Polymarket order adapter is implemented or authorized;
- no strategy has established robust forward profitability.

"Implemented" below means executable code exists and its declared tests pass. It never means a
forecast is guaranteed correct or a strategy is profitable.

## 2. Status Legend

| status | meaning |
|---|---|
| IMPLEMENTED_TESTED | Code exists and its executable invariant/test passes. |
| IMPLEMENTED_BLOCKED | Code exists but cannot serve because its model, data or deployment gate is unmet. |
| SHADOW_PAPER | Records or simulates decisions without real orders. |
| RESEARCH_REJECTED | Tested and failed its frozen promotion/economic gate. |
| NOT_IMPLEMENTED | Required capability does not exist yet. |
| NOT_READY_DATA | Code exists but the minimum forward evidence has not accrued. |

## 3. Current Executable Contracts

| contract | current value |
|---|---|
| raw feature count | 136 |
| main-model feature count | 63 |
| main-model feature hash | `864622d65e85` |
| feature semantics | v4 |
| training semantics | v3 |
| lookback | 60 one-minute observations |
| live direction horizons | 5m and 15m |
| default deep seat | TCN |
| main architecture | `2026-07-31-v14-pruned63-864622d65e85-2horizon-5-15-rf-persist-split98-classbal-simw-tcnbal-purged-vrts-session-136-tcn` |
| configured historical window | 1,265 days |
| evaluation split | recent 2%, after purging |
| production refit | all accepted data after the untouched-tail gate |
| current serving status | `DEGRADED_MODEL_BLOCKED` |
| real orders | unavailable and unauthorized |

## 4. Application Capability Matrix

### Data And State

| capability | implementation | status | evidence/result | remaining work |
|---|---|---|---|---|
| Binance spot candles/trades/book | `data_ingestion.py` | IMPLEMENTED_TESTED | Parser, timestamp and protocol tests pass. | Maintain reconnect/content-health monitoring. |
| Binance futures state/liquidations | `data_ingestion.py` | IMPLEMENTED_TESTED | Parser and nullable-state paths pass. | Host currently records no `forceOrder` rows in the research archive. |
| Coinbase BTC-USD ticker | `data_ingestion.py` | IMPLEMENTED_TESTED | Feed health and callback tests pass. | Forward uptime is evidence, not a code completion issue. |
| Bybit OI/funding/book/trades | REST/live recorders | IMPLEMENTED_TESTED | Archive contains order book and public trades. | Long continuous qualifying coverage is still absent. |
| Polymarket canonical CLOB | `polymarket_client.py`, `polymarket/l2_book.py` | IMPLEMENTED_TESTED | Snapshot/increment, identity and stale-content invariants pass. | Profitability still needs forward quote, fill and settlement evidence. |
| Pyth/round anchor | Price-to-Beat tracker | IMPLEMENTED_TESTED | Boundary, late-anchor invalidation and settlement tests pass. | Continue recorder evidence; do not mix Binance settlement with Pyth rounds. |
| DuckDB analytics/predictions | `database.py` | IMPLEMENTED_TESTED | Persistence, restore and ledger tests pass. | Backups/service supervision remain deployment work. |
| Multi-venue event archive | `venues/multi_venue_recorder.py` | IMPLEMENTED_BLOCKED | 20,085,631 rows over 0.95d. | Need at least 60d for event families and four continuous qualifying weeks for the preregistered lane. |
| Sequenced Binance L2 archive | `venues/binance_l2_recorder.py` | NOT_READY_DATA | Recorder/replay invariants pass; local archive absent. | Start the recorder and collect sequenced snapshot/diff/gap history. |
| Deribit option chain archive | `venues/deribit_option_chain_recorder.py` | NOT_READY_DATA | 2,650 rows; latest batch stale. | Run continuously and join implied prices to subsequent realized movement. |

### Features And Models

| capability | implementation | status | evidence/result | remaining work |
|---|---|---|---|---|
| 136-column diagnostic feature engine | `features.py` | IMPLEMENTED_TESTED | Schema hash and train/serve checks pass. | Many external/live-only columns remain excluded from main training. |
| 63-column parity-safe model mask | `model_contract.py` | IMPLEMENTED_TESTED | v14 contract and retirement assertions pass. | Complete the compatible retrain. |
| Main direction ensemble | `model.py` | IMPLEMENTED_BLOCKED | Training, stacking and save/load invariants pass. | Current saved v11 bundle is incompatible; retrain v14. |
| Move-size/conformal output | `model.py` | IMPLEMENTED_BLOCKED | Target/alignment and conformal tests pass. | Requires the compatible main bundle. |
| XGBoost OOF stacker | `model.py` | IMPLEMENTED_BLOCKED | Persistence and OOF invariants pass. | Retrain and evaluate; no current trusted stacker artifact. |
| Regime routing | `regime.py`, model routing | IMPLEMENTED_TESTED | Causal HMM forward-filter test passes. | Promotion still depends on model and live evidence. |
| P(Hold) persistence | `train_persistence_model.py` and Price-to-Beat serving | IMPLEMENTED_BLOCKED | Loader and calibration contracts pass. | Current artifact has unknown identity; retrain with manifest. |
| Path, quantile and keeper heads | standalone trainers/serving | IMPLEMENTED_BLOCKED | Head self-tests and permissions pass. | All current artifacts are refused for missing identity manifests. |
| Round-state flip/shock heads | `round_state_panel.py` | IMPLEMENTED_BLOCKED | Fail-closed corrupt-reload regression passes. | Retrain; current artifact is unavailable. |
| Learned trust/meta filters | meta/champion modules | IMPLEMENTED_BLOCKED | Permissions and promotion tests pass. | Need compatible artifact plus enough independent outcomes. |
| P(Hold) calibration | `phold_calibrator.py` | IMPLEMENTED_BLOCKED | Exact round-trip and fail-closed tests pass. | Mode remains off until compatible source models and new OOF predictions exist. |
| FSR-PPO | `fsr_ppo_strategy.py` | SHADOW_PAPER | Deterministic heuristic path only. | A trained PPO policy is NOT implemented; requires a validated simulator and forward fill model. |
| Kronos | wrapper/fallback forecast path | IMPLEMENTED_BLOCKED | UI/persistence paths exist. | A loaded, identity-bound Kronos production artifact is not currently available. Fallback projection must not be described as Kronos. |

### Decisions, Execution And Risk

| capability | implementation | status | evidence/result | remaining work |
|---|---|---|---|---|
| BUY/SELL/WAIT/AVOID explanation | decision gate/champion/UI | IMPLEMENTED_TESTED | Synchronization and reason-code tests pass. | Accuracy depends on retrained models and resolved live samples. |
| Price-to-Beat 5m/15m rounds | `price_to_beat.py` | IMPLEMENTED_TESTED | Sign-truth, settlement and persistence tests pass. | Continue forward settlement collection. |
| 16 Polymarket paper strategies | tracker, database, UI | SHADOW_PAPER | Registry confirms all 16 logged, exposed and named. | None is authorized for real money; evaluate exact bid/ask/fee results. |
| Binance futures paper engine | `binance_paper/` | SHADOW_PAPER | Execution, partial fills, funding, recovery, risk and accounting suites pass. | Forward economic evidence is still required. |
| Exact Polymarket ladder VWAP | `polymarket/l2_book.py` | IMPLEMENTED_TESTED | Deterministic depth/VWAP tests pass. | Passive queue priority cannot be exact from public aggregate L2. |
| Control-plane authentication | `control_auth.py` | IMPLEMENTED_TESTED | Real HTTP auth tests pass. | Production tokens/origins are not configured on this machine. |
| Order lifecycle and reservations | `order_lifecycle.py` | IMPLEMENTED_TESTED | Timeout=UNKNOWN, transition and recovery tests pass. | No real venue adapter consumes it. |
| Real trading authority | `trading_authority.py` | NOT_IMPLEMENTED | Tests prove requests are refused. | Implement only after explicit authorization, promoted edge and canary controls. |
| Dynamic exit model | research campaigns | RESEARCH_REJECTED | Challenger underperformed identical-entry HOLD. | Reopen only with new causal state or a new instrument, not parameter tuning. |

### User Interface

| view | status | purpose |
|---|---|---|
| Polymarket | IMPLEMENTED_TESTED | 5m/15m round, leader, P(Hold), model heads, book edge and plain verdict. |
| Trades | IMPLEMENTED_TESTED | Paper blotter, exact stored P/L components and per-rule scoreboards. |
| Bitcoin | IMPLEMENTED_TESTED | Binance predictions, projected bands and native Price-to-Beat mirror. |
| Binance Paper | IMPLEMENTED_TESTED | Isolated accounts, positions, orders, fills, funding, equity and controls. |
| Analysis | IMPLEMENTED_TESTED | Plain-language technical/order-flow context; not an independent profit signal. |
| System Health | IMPLEMENTED_TESTED | Feed, database, artifact, task, code and execution-readiness status. |
| How to bet | IMPLEMENTED_TESTED | Operator explanation page; paper-only guidance, not financial advice. |

Frontend production build and high-severity dependency audit pass. Hidden historical DOM views are
not separate active navigation tabs.

## 5. Main Ensemble Models And Targets

The current code defines seven base seats for each supported horizon/regime:

| seat | target | role |
|---|---|---|
| XGBoost | DOWN/NEUTRAL/UP triple-barrier class | nonlinear tree learner |
| LightGBM | same | alternate boosting geometry |
| CatBoost | same, optional dependency | noisy-tabular diversity |
| Random Forest | same | lower-variance bagged diversity |
| HistGradientBoosting | same | CPU regularized anchor |
| Logistic Regression | same | linear sanity baseline |
| TCN | same sequence target | temporal diversity seat |

An XGBoost OOF stacker combines available base probabilities. A separate move-size regressor predicts
movement magnitude. Neither target is an exact future candle promise.

Current measured research conclusions:

- raw 5m/15m settlement direction has generally remained near coin-flip;
- movement magnitude, range and path-risk heads rank better than exact direction;
- a high classification score is not promotion evidence without executable post-cost EV;
- current artifacts are unavailable, so historical model metrics must not be presented as live
  production accuracy.

## 6. Complete Feature Catalog

### Raw 136-Feature App Schema

The ordered executable source of truth is `backend/features.py::FEATURE_NAMES`:

```text
price_return, volume_norm, rsi, macd_hist, bb_position, atr_norm, vwap_deviation,
cvd_change, cvd_1m, cvd_5m, book_imbalance, obi_5, obi_10, obi_20,
trade_intensity, spread_norm, funding_rate, funding_velocity, oi_change,
long_short_ratio, fear_greed_norm, stoch_rsi, adx_norm, obv_change,
williams_r_norm, cci_norm, mfi_norm, price_vs_ema9, price_vs_ema21,
price_vs_sma50, volume_ma_ratio, roc_5, roc_10, heikin_ashi_trend,
rsi_x_adx, vol_x_trend, obi_x_atr, funding_x_oi, coinbase_premium_norm,
global_oi_change, coinbase_premium_velocity_norm, oi_divergence_norm,
long_liq_volume, short_liq_volume, liq_imbalance, liq_acceleration,
rv_1m, rv_5m, rv_15m, vol_acceleration, ewma_vol, chainlink_price_norm,
wall_imbalance, distance_to_bid_wall_norm, distance_to_ask_wall_norm,
spread_expansion_ratio, vacuum_detected, dist_to_resistance, dist_to_support,
sr_compression, fv_deviation, bid_wall_persistence, ask_wall_persistence,
bid_wall_growth, ask_wall_growth, queue_depletion_rate,
liquidity_sweep_bullish, liquidity_sweep_bearish, spoof_score,
absorption_ratio, bid_consume_rate, ask_consume_rate, queue_pressure,
regime_transition_prob, regime_entropy, vol_forecast_1m, vol_forecast_5m,
vol_forecast_15m, put_call_ratio, options_skew_25d, max_pain_distance,
atm_iv_norm, basis_spread, basis_velocity, stablecoin_flow, exchange_netflow,
eth_btc_price_ratio, sol_btc_price_ratio, eth_volume_norm, sol_volume_norm,
eth_imbalance, sol_imbalance, macro_dxy_norm, macro_us10y_norm,
mtf_trend_alignment, mtf_volatility_ratio, mtf_support_distance,
order_add_cancel_imbalance, absorption_persistence, book_replenishment_rate,
cross_exchange_lead_lag, volume_profile_poc_distance,
volume_profile_lvn_distance, funding_oi_interaction, time_to_funding,
polymarket_relevant_event, polymarket_probability_change,
polymarket_liquidity, polymarket_event_shock, twap_deviation, exhaustion,
volume_profile_value_area_pos, vpin, cvd_delta_divergence, oi_momentum,
orb_position, orb_breakout, delta_ratio, delta_acceleration, flow_efficiency,
cvd_slope_divergence, rv_upside, rv_downside, price_oi_interaction,
large_trade_delta, large_trade_imbalance, trend_efficiency, signed_streak,
momentum_fast_slow, return_acceleration, variance_ratio, rv_term_structure,
session_asia, session_eu, session_us, is_weekend
```

### Current 63-Feature Main-Model Mask

The ordered executable source of truth is `backend/model_contract.py::MODEL_FEATURE_NAMES`:

```text
price_return, volume_norm, rsi, macd_hist, bb_position, atr_norm,
vwap_deviation, cvd_change, cvd_1m, cvd_5m, trade_intensity, stoch_rsi,
adx_norm, obv_change, williams_r_norm, cci_norm, mfi_norm, price_vs_ema9,
price_vs_ema21, price_vs_sma50, volume_ma_ratio, roc_5, roc_10,
heikin_ashi_trend, rsi_x_adx, vol_x_trend, rv_1m, rv_5m, rv_15m,
vol_acceleration, ewma_vol, dist_to_resistance, dist_to_support,
sr_compression, mtf_trend_alignment, mtf_volatility_ratio,
volume_profile_poc_distance, volume_profile_lvn_distance, twap_deviation,
exhaustion, volume_profile_value_area_pos, vpin, cvd_delta_divergence,
orb_position, orb_breakout, delta_ratio, delta_acceleration, flow_efficiency,
cvd_slope_divergence, rv_upside, rv_downside, large_trade_delta,
large_trade_imbalance, trend_efficiency, signed_streak, momentum_fast_slow,
return_acceleration, variance_ratio, rv_term_structure, session_asia,
session_eu, session_us, is_weekend
```

The other 73 raw columns remain available for UI, diagnostics or isolated research but are excluded
from current main-model training because they do not meet the default `KEEP,PARITY-FIX` contract.

## 7. Test Inventory And Results

### Passing Gates

| gate | result |
|---|---|
| canonical local workflow | 71/71 passed |
| exact `start.bat` self-test-only path | passed |
| pytest-compatible suites | 5 passed |
| Python compile | passed |
| backend/test Pyflakes | passed |
| frontend production build | passed |
| npm high-severity audit | 0 vulnerabilities |
| documentation tables | passed |
| launcher integrity | 60 invoked paths valid |
| repository layout | 42 research launchers, 4 test launchers |

The canonical workflow covers labels, target units, OOF isolation, immutable evidence, artifact
hash-before-deserialize, atomic promotion, paper fills/accounting, fee formulas, funding, risk,
recovery, HTTP/WebSocket boundaries, feed content health, task supervision, close-only authority,
regime causality, feature weighting, research controls, documentation and frontend build integrity.

The exact Windows launcher also exposed an ambiguous failure label: three research checks shared one
message, so a transient research-coverage failure was reported as promotion-statistics failure.
`start.bat` now has a separate guarded label for each command; the corrected full launcher gate passes.

### Expected Failing Readiness Checks

These failures are correct behavior, not test failures:

| check | measured result | why |
|---|---|---|
| specialist serving contract | 0/11 serviceable | old artifacts have no current manifests |
| main ensemble compatibility | incompatible | saved v11 versus required v14 |
| paper-production readiness | 17 blockers | deployment env, tokens, verified artifacts and feed requirements absent |
| event-conditional families | 0 results | archive span 0.95d versus 60d minimum; liquidation stream absent |
| preregistered venue episodes | 0 qualifying | continuous health contract not met |
| Binance sequenced L2 | no local archive | recorder has not run |
| Deribit research readiness | false | only three stale batches; no forward realized-vol join |

## 8. Research Results That Must Remain Honest

| lane | result | deployment status |
|---|---|---|
| raw direction families | generally near 0.50 AUC/coin-flip | not proof of edge |
| movement magnitude/path | predictive ranking exists in several tests | information/risk only until an executable instrument clears costs |
| conditional LONG/SHORT/ACT-SKIP | selected policies lost after costs | RESEARCH_REJECTED |
| dynamic exit | underperformed identical-entry HOLD | RESEARCH_REJECTED |
| Binance breakout bracket | all tested configurations lost after costs | RESEARCH_REJECTED |
| complete-set Polymarket arbitrage | real but tiny/rare at measured size | no production strategy |
| cross-market coherence | no robust inconsistency after timestamp correction | RESEARCH_REJECTED |
| funding carry | sample lacked useful variation; estimated retail hurdle unattractive | not promoted |
| Polymarket market-prior residual | did not beat the market/economic gates | RESEARCH_REJECTED |
| event-time repricing | research candidate only | forward shadow, no promotion |
| liquidity provision | unresolved | requires sequenced L2 and conservative queue evidence |
| options surface | 0/2,079 executable no-arbitrage violations; 15m magnitude fails the straddle spread upper bound | no production strategy |
| options physical-versus-implied vol | genuinely unresolved | requires continuous Deribit history and a regime-matched forward comparison |

The detailed protocols and metrics remain in the linked result documents under `docs/active/` and
`docs/PATH_INFORMATION_RESULTS.md`. A standalone research script is not evidence merely because it
runs; `audit_research_claims.py` currently flags 10 of 31 legacy scripts for explicit defects.

## 9. Not Implemented Or Not Ready: Why And How

### Compatible Model Serving

**Why not ready:** saved artifacts predate v4/v3 semantics and current manifests.

**How:** run the 1,265-day retrain, evaluate the purged recent 2%, generate OOF calibration, refit
accepted production models on all data, write staged verified bundles, smoke-test, then use
`promote_challenger.py` explicitly.

### Strict Artifact Identity Everywhere

**Why not complete:** active serving loaders are enforced, but 39 raw research save sites and 14 raw
research/offline load sites remain.

**How:** migrate remaining research writers/loaders through `model_artifacts`/`verified_io`, reduce
the ratcheted counts to zero, retrain artifacts, then set `BTC_STRICT_ARTIFACT_IDENTITY=1`.

### Production Deployment Environment

**Why not complete:** tokens, explicit origins, dedicated environment and verified models are absent.

**How:** create `.venv-prod`, configure deployment variables/secrets, HTTPS/reverse proxy, service
supervision, backups and alerts, then require `production_readiness.py --mode paper` to pass.

### Forward Event Research

**Why not ready:** only 0.95d is recorded and zero five-minute episodes qualify.

**How:** run the multi-venue recorder continuously on an always-on host, repair the missing
liquidation stream, monitor clock/content health and wait for the frozen duration/sample gates.

### Exact Passive Fill/Queue Position

**Why not implemented:** public aggregate L2 has price-level size but no stable per-order identity.

**How:** use sequenced L2 with conservative queue-ahead assumptions, cancellation/adverse-selection
stress and forward maker outcomes. Call it a queue model, never exact queue truth.

### Trained PPO/RL Execution

**Why not implemented:** no validated execution simulator or sufficient forward depth/fill history.

**How:** first establish simulator-to-forward agreement, define a costed reward and hard risk
constraints, compare with deterministic baselines, and keep any learned policy shadow-only until
independent promotion gates pass.

### Options Volatility Strategy

**Why not ready:** the Deribit recorder has only three stale batches. Predicting realized movement is
not enough; the system must beat implied volatility after spread and fees.

**How:** continuously record full chains, construct expiry/strike-consistent straddles, join to
subsequent realized variance, test physical-minus-implied residual out of sample, and include
executable bid/ask and vega/expiry risk.

### Real-Money Trading

**Why not implemented:** no promoted edge and no real adapter; explicit repository policy forbids it.

**How:** only after forward gates pass, build one venue/strategy canary adapter with manual arming,
small notional, kill switch, reconciliation, unknown-order handling, reduce-only safety and an
independent live-versus-paper audit. This requires separate user authorization.

### Guaranteed Accuracy Or Exact Candles

**Why not implementable:** markets are stochastic and regime-dependent; exact future OHLC/volume is
not deterministically knowable.

**How:** forecast calibrated distributions and ranges, measure Brier/ECE/coverage and abstain when
evidence is weak. Never label a projected candle or target as guaranteed.

## 10. Operator Path

Current manual launch:

```powershell
cd C:\Users\rahul\Documents\BTC-Prediction-Tool
.\start.bat
```

After training completes, do not auto-promote merely because files exist:

```powershell
python backend\check_model_compatibility.py
python backend\check_feature_contract.py --enforce-serving
python backend\promote_challenger.py --challenger data\saved_models_challenger_1265d --days 1265
```

Apply only if the dry run passes every frozen gate:

```powershell
python backend\promote_challenger.py --challenger data\saved_models_challenger_1265d --days 1265 --apply
```

Then rerun:

```powershell
python backend\production_readiness.py --mode paper
```

The 1,265-day training run is a prerequisite, not proof of accuracy or profit.
