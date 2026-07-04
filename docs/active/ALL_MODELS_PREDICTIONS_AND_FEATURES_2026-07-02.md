# All Models, Predictions And Features

Date: 2026-07-02  
Purpose: canonical inventory of the models currently present in the BTC Prediction Tool, what each
predicts, how it works, which inputs it uses, and whether it is active, gated, disabled or research-only.

This document is derived from the current Python code and saved-model metadata. It supersedes older
descriptions that call the app a four-model or seven-horizon production system.

## Status Legend

| Status | Meaning |
|---|---|
| **ACTIVE** | Loaded by the current app and allowed to affect a displayed prediction or decision |
| **ACTIVE FILTER** | Can reject or downgrade a prediction, but does not originate direction |
| **SHADOW** | Logged or displayed for evaluation; does not authorize a real action |
| **GATED** | Code exists, but activation requires data/evidence or a compatible artifact |
| **RESEARCH** | Standalone experiment; saved outputs are not part of live decisions |
| **DISABLED/RETIRED** | Explicitly excluded because it is stale, unsafe, missing or failed evidence |

## Current Decision Flow

```text
Market feeds
  -> full 136-feature diagnostic vector
  -> 69-feature train/serve-safe model mask
  -> regime-routed direction base models
  -> out-of-fold XGBoost stacker
  -> probability smoothing, calibration and server safety gates
  -> specialist heads: hold, movement, drop, activity, range and path
  -> shadow round-state heads: recross, remaining shock and next opportunity
  -> rules-first Champion plus learned meta veto
  -> live Polymarket ask/depth/fee edge gate
  -> PAPER candidate, WAIT or AVOID
```

Only the 5-minute and 15-minute horizons are active. Old 1m, 3m, 7m, 10m and 30m files remain on disk
for forensic compatibility, but `MultiModelEnsemble.horizons` is currently `[5, 15]` and does not serve them.

## 1. Main Direction Ensemble

**Status:** ACTIVE  
**Code:** `backend/model.py`, `backend/features.py`  
**Horizons:** 5m and 15m  
**Regime buckets:** TREND, RANGE, VOLATILE and GLOBAL fallback  
**Input:** 60-candle lookback of the active 69-feature mask, flattened for tabular models and kept
sequential for the deep model.

### Prediction Target

The ensemble does not directly predict whether the final close is above the current close. It predicts
which adaptive triple barrier is reached first:

| Class | Label rule |
|---|---|
| DOWN | the adaptive lower barrier is touched first |
| NEUTRAL | neither barrier is touched before the horizon expires |
| UP | the adaptive upper barrier is touched first |

The barrier size is based on recent ATR, bounded by cost and stability limits. True future candle highs
and lows are used during training. If both barriers occur in one one-minute candle, close versus entry
resolves the unknown intrabar order. This remaining ambiguity is unavoidable without historical tick order.

### Base Models

| Model | How it predicts | Why included | Output |
|---|---|---|---|
| XGBoost | histogram boosted trees over the flattened sequence; isotonic calibration when viable | nonlinear interactions | P(DOWN), P(NEUTRAL), P(UP) |
| Random Forest | 150 balanced, constrained trees with random feature subsets | decorrelated stable vote | three-class probability |
| LightGBM | multiclass boosted trees, GPU when supported; isotonic calibration when viable | alternative tree geometry | three-class probability |
| CatBoost | multiclass ordered boosting, GPU when supported | robust noisy-tabular learner | three-class probability |
| HistGradientBoosting | regularized sklearn histogram boosting | CPU anchor | three-class probability |
| TCN | temporal convolution over the 60x69 sequence | local temporal patterns | three-class probability |
| Logistic Regression | standardized linear multiclass baseline | sanity check | three-class probability |

`BTC_DL_ARCH=LSTM_GRU` can replace TCN with the older LSTM+GRU network. They are alternatives occupying
the same `dl` seat, not simultaneous extra votes. TCN is the current default.

### Stacking And Regime Routing

For each horizon/regime, purged time-series out-of-fold probabilities train an XGBoost meta-stacker.
This is the preferred combination path. If unavailable, normalized dynamic weights combine base models.

The regime engine fits Gaussian emissions to `[log return, absolute log return, volume ratio]`, estimates
a transition matrix and performs an online HMM-style update. High confidence selects one expert; uncertain
state blends experts. Empty buckets fail over to GLOBAL rather than manufacturing NEUTRAL.

### Stability And Confidence

Raw probabilities pass through smoothing, direction locking, hysteresis, opposing-tick confirmation,
per-regime adjustment, live isotonic recalibration, entropy/feed/confusion/spread/quantile/expectancy gates,
and the learned trust filter.

| Field | Meaning |
|---|---|
| model raw direction | immediate stacker/base result |
| raw direction | stabilized lean before server filters |
| final direction | result after all filters; may be NEUTRAL |
| signal | LONG/SHORT/STRONG or NEUTRAL presentation |

## 2. Main Move-Size Model

**Status:** ACTIVE  
**Model:** HistGradientBoostingRegressor inside `MultiModelEnsemble`  
**Target:** absolute close-to-close move over 5m/15m as a fraction of entry price  
**Features:** the same 60x69 sequence used by the direction ensemble.

It predicts size separately from direction. Held-out residual quartiles produce a conformal range.
Regime/horizon empirical priors are blended lightly. Expected move does not predict settlement side.

## 3. Live Trust Meta-Model

**Status:** ACTIVE FILTER after at least 100 resolved qualifying samples per horizon  
**Model:** XGBoost binary classifier in `backend/meta_model.py`  
**Target:** whether a proposed directional signal would have positive estimated PnL after costs.

Features:

```text
confidence, agreement, encoded regime, EWMA volatility, normalized spread,
wall imbalance, S/R compression, liquidation imbalance,
quantile width/asymmetry/spread, expectancy, walk-forward accuracy/stability,
walk-forward count/age, UTC hour, tradeability, regime score,
liquidity score, expected edge
```

It returns execute/skip and trust probability; it does not choose UP or DOWN. Before enough data exists,
it passes through instead of blocking on nonexistent evidence.

## 4. Precision And Calibration Layer

**Status:** ACTIVE FILTER  
**Code:** `backend/calibration.py`, `backend/calibration_monitor.py`  
**Target:** map stated confidence to observed live precision by horizon/context.

This uses resolved predictions, not market features. It makes probability language more honest; it does
not create directional edge.

## 5. P(Hold) Persistence Head

**Status:** ACTIVE  
**Artifact:** `persistence_model.pkl`  
**Model:** HistGradientBoostingClassifier plus global/per-horizon isotonic calibration  
**Target:** probability the side currently ahead of price-to-beat remains ahead at resolution.

Base features:

```text
absolute distance from anchor (%), seconds left, trailing 60s volatility (%),
horizon, distance/volatility ratio
```

Keeper variant adds:

```text
rv_15m, rv_30m, rv_60m, VPIN, compression ratio, shock magnitude
```

Rounds are split chronologically as whole units to avoid same-round leakage. Saved test AUC is about
0.742. P(Hold) estimates survival of the current side; it does not predict which side gets ahead first.

## 6. Four Keeper Specialist Heads

**Status:** ACTIVE  
**Horizons:** 5m/15m  
**Features:** `rv_15m`, `rv_30m`, `compression_ratio`, `shock_magnitude`  
**Internal ensemble:** soft vote of Logistic Regression, Random Forest, Extra Trees and optional CatBoost;
isotonic calibration from time-series OOF probabilities.

Meaningful/large/extreme boundaries are p75/p90/p97 of the training window and saved for serving parity.

| Head | Label and prediction | Live role | Saved test evidence |
|---|---|---|---|
| Big Move | `abs(close[t+h]-close[t]) >= threshold[h]` | enough movement? | AUC ~0.712 |
| Big Drop | future low <= current close - threshold | downside/avoid-long warning | AUC ~0.737 |
| Big Up/Down | separate signed future-close threshold probabilities | confirmation only | per-head metrics in bundle |
| Activity/Range | future high-low range >= threshold | quiet/elevated/likely activity | AUC ~0.781; top-5% precision ~0.915 |

These predict magnitude/risk more reliably than direction. Their scores are not direction accuracy.

## 7. Signed Quantile Band

**Status:** ACTIVE  
**Artifact:** `signed_quantile_model.pkl`  
**Model:** q10/q50/q90 GradientBoostingRegressors plus conformal CQR  
**Target:** signed future return in bps over 5m/15m  
**Features:** `rv_15m`, `rv_30m`, `rv_60m`, `compression_ratio`.

Outputs are conformal downside, median projected close and conformal upside, targeting about 80% recent
coverage. It is the preferred uncertainty display, not an exact-price promise.

## 8. Standalone Magnitude Quantiles

**Status:** AVAILABLE ARTIFACT; secondary, not the primary live band  
**Artifact:** `magnitude_model.pkl`  
**Model:** q10/q50/q90 GradientBoostingRegressors  
**Target:** absolute window-close move as a fraction of price.

Features:

```text
ret_1, ret_5, ret_15, rv_short, rv_long, variance_ratio,
range_position, normalized ATR, momentum_20, hour_sin, hour_cos
```

A horizon is saved only if q50 pinball loss beats a constant baseline and quantiles remain ordered.

## 9. Path Forecaster

**Status:** ACTIVE path/risk layer  
**Artifact:** `path_forecaster.pkl`  
**Models:** CatBoost, LightGBM and HistGradientBoosting classifier/regressor ensembles with isotonic
calibration for binary heads  
**Features:** `rv_15m`, `rv_30m`, `rv_60m`, `compression_ratio`, `shock_magnitude`.

| Output | Meaning |
|---|---|
| high/low quantiles | maximum likely excursions |
| P(touch $50/$100) | chance a distance barrier is reached |
| P(round trip) | chance both barrier sides are touched |
| touch asymmetry | one-sided versus two-sided path |
| early touch | chance a barrier arrives early |
| net magnitude | expected absolute close displacement |

The plan freezes at round open. It informs CHOP/TREND/QUIET style and risk, not settlement side.

### 9A. Round-State Shadow Heads

**Status:** SHADOW/INFO; explicitly excluded from Champion behavior  
**Artifact:** `round_state_heads.pkl`  
**Models:** best-two validation ensemble from HistGradientBoosting, Extra Trees and standardized Logistic Regression, followed by isotonic calibration  
**Horizons:** 5m and 15m

Final-30-to-120-second features combine the five live path keepers with seconds left, signed/absolute
distance, range already traveled, recross count, side occupancy and current side. Separate next-three-round
heads use the five keepers. Whole rounds are split 70/15/15 and label windows crossing train/calibration
boundaries are purged.

| Output | 5m test AUC | 15m test AUC | Meaning |
|---|---:|---:|---|
| future side flip | 0.8549 | 0.9225 | current side crosses the anchor again before expiry |
| remaining $20 shock | 0.9198 | 0.9175 | another $20 move before expiry |
| remaining $50 shock | 0.9264 | 0.9303 | another $50 move before expiry |
| remaining $100 shock | 0.9485 | 0.9540 | another $100 move before expiry |
| opportunity within three rounds | 0.8158 | 0.7846 | future path event, not profit |

All probabilities fail closed outside their supported feature/time contract. Full Brier, ECE, sample and
model-selection evidence is in `ROUND_STATE_DECISION_PANEL_2026-07-02.md`.

### Older Path-Shape Classifier

**Status:** RESEARCH artifact, not loaded by the live server  
**Artifact:** `path_model.pkl`  
**Model:** HistGradientBoosting multiclass classifier  
**Classes:** CHOP, UP_DIRECT, UP_THEN_DOWN, DOWN_DIRECT, DOWN_THEN_UP.

It uses the same 11 lean beat features as the standalone magnitude/beat lane. Tick paths create the
labels, and a horizon is saved only when temporal test accuracy beats the majority class by at least
three percentage points. The active `path_forecaster.pkl` supersedes it for current UI path/risk output.

## 10. Fade/Reversal Head

**Status:** DISABLED pending compatible causal retraining  
**Code expects:** `2026-07-01-fade-v5-causal-touchbar-30-50`  
**Saved artifact:** older `2026-07-01-fade-v4-multibarrier-30-50`, rejected by the loader.

Intended target: after an observed early $30/$50 touch, probability of reaching anchor before a 2x stop.
Models are CatBoost, LightGBM and HistGradientBoosting plus isotonic calibration.

Features:

```text
rv_15m, rv_30m, rv_60m, compression_ratio, shock_magnitude,
touch fraction, touched side, overshoot bps,
pre-touch opposite excursion bps, pre-touch range bps
```

The version gate is correct: old fade research had timing/label problems. No live fade call is trusted.

## 11. Champion Layers

### Rules-First Champion

**Status:** ACTIVE composer, not ML. It combines hold, movement, drop, directional confirmation,
activity, quantile room, regime, model lean and executable ask. A PAPER candidate requires:

```text
fair value - executable ask - fee - safety buffer > required edge
```

### Learned Champion Meta-Model

**Status:** ACTIVE FILTER when artifact exists  
**Artifact/model:** `champion_meta_model.pkl`, Logistic Regression with scaling/one-hot encoding  
**Target:** probability the current price-to-beat side holds to resolution.

Numeric features are horizon, seconds left, current move, six specialist probabilities and champion
confidence. Categorical features are current position, specialist tiers, regime and proposed action.
Saved test AUC is about 0.742. Below 0.55 it can veto a candidate into WAIT.

## 12. Selectivity/Tradability Bundle

**Status:** RESEARCH/SHADOW; saved but not called by the main server loop  
**Artifact:** `selectivity_models.pkl`

| Head | Model | Target | Features |
|---|---|---|---|
| Selectivity | LogReg + RF soft vote | P(next 5m abs move > p75) | rv_15m, rv_30m, log_count, vpin_15m, compression, shock |
| Tradability | LogReg | favorable move relative to adverse path | compression, rv_60m, rv_30m |
| Fail Fast | LogReg | path invalidates quickly | shock magnitude, VPIN |
| Expected Move | Ridge | absolute move in bps | rv_15m, rv_30m, rv_60m, compression |

It requires purged walk-forward auditing because ordinary folds can overlap forward-label windows.

## 13. Price-To-Beat And Polymarket Models

### Beat Classifier

**Status:** GATED/MISSING ARTIFACT  
**Intended model:** HistGradientBoosting plus isotonic calibration  
**Target:** exact binary `window close >= window open` for 5m/15m.

Features:

```text
ret_1, ret_5, ret_15, short/long RV, variance ratio,
range position, normalized ATR, momentum_20, hour sine/cosine
```

The trainer refuses to save unless temporal test and confident-call gates pass. `beat_model.pkl` is absent.

### Mathematical Polymarket Baseline

**Status:** PLACEHOLDER/UNUSED by current server flow  
**Code:** `backend/polymarket_model.py`

It implements a lognormal binary-option approximation using price, reference, time and volatility. The
intended residual correction/calibrator are placeholders, and the server does not call `predict_fair_value`.

### Exact L2 Execution Layer

**Status:** RECORD-FORWARD RESEARCH  
**Code:** `backend/polymarket/l2_book.py`, `l2_recorder.py`

This is deterministic, not ML: exact ladder VWAP at size and bounded maker queue scenarios. It tests
whether predicted edge survives depth and estimated fees.

## 14. FSR-PPO Challenger

**Status:** SHADOW heuristic; no trained PPO policy loaded  
**Code:** `backend/fsr_ppo_strategy.py`

It derives denoised price, noise ratio, clean momentum, trend strength, Hurst persistence, volume pressure
and signal quality, then emits AVOID or small/medium BUY/SELL under a cost-aware reward. Until policy
weights exist, it is a deterministic warm start, not trained reinforcement learning.

## 15. Research-Only Families

| Family | Models | Targets |
|---|---|---|
| Linear | Ridge, ElasticNet, Logistic Regression | price, return, high, low, range, volume, direction, big move |
| Bagged trees | Random Forest, Extra Trees | tabular classification/regression |
| Boosted trees | XGBoost, LightGBM, CatBoost, HistGB | classification, regression, quantiles |
| Quantile | LightGBM/GBR q10/q50/q90 | return/high/low/range bands |
| Base sequence | LSTM, GRU, TCN, Transformer | direction, big move, return |
| Advanced sequence | VLSTM, LPatchTST, PatchTST, iTransformer | direction, big move, return |
| Optional sequence | Mamba, Mamba2, VSN+Mamba2 | smoke paths only; dependencies blocked full test |

Conclusions: raw direction remained near coin-flip; big movement and range/activity were more predictable;
exact price did not beat the current-price baseline robustly; advanced sequence models did not justify live
promotion; TCN remains only as a diversity seat.

## 16. All 69 Active Main-Model Features

The app computes 136 features, but train/predict uses only `KEEP` and `PARITY-FIX`, schema hash
`7977e0559560`.

### Price, Trend And Oscillators (26)

```text
price_return, rsi, macd_hist, bb_position, vwap_deviation,
stoch_rsi, adx_norm, williams_r_norm, cci_norm, mfi_norm,
price_vs_ema9, price_vs_ema21, price_vs_sma50, roc_5, roc_10,
heikin_ashi_trend, rsi_x_adx, trend_efficiency, signed_streak,
momentum_fast_slow, return_acceleration, variance_ratio,
twap_deviation, exhaustion, orb_position, orb_breakout
```

### Volume And Aggressive Flow (19)

```text
volume_norm, cvd_change, cvd_1m, cvd_5m, trade_intensity,
obv_change, volume_ma_ratio, vol_x_trend,
volume_profile_poc_distance, volume_profile_lvn_distance,
volume_profile_value_area_pos, vpin, cvd_delta_divergence,
delta_ratio, delta_acceleration, flow_efficiency,
cvd_slope_divergence, large_trade_delta, large_trade_imbalance
```

### Volatility And Risk (13)

```text
atr_norm, rv_1m, rv_5m, rv_15m, vol_acceleration, ewma_vol,
vol_forecast_1m, vol_forecast_5m, vol_forecast_15m,
rv_upside, rv_downside, rv_term_structure, mtf_volatility_ratio
```

### Structure, Regime And MTF (7)

```text
dist_to_resistance, dist_to_support, sr_compression,
regime_transition_prob, regime_entropy, mtf_trend_alignment, mtf_support_distance
```

### Calendar (4)

```text
session_asia, session_eu, session_us, is_weekend
```

The exact ordered list is stored in `model_feature_schema.pkl`.

## 17. The Other 67 Features Excluded From Main Training

They may still feed UI/research but lack suitable main-model parity/evidence.

### Order Book And Microstructure

```text
book_imbalance, obi_5, obi_10, obi_20, spread_norm, obi_x_atr,
wall_imbalance, distance_to_bid_wall_norm, distance_to_ask_wall_norm,
spread_expansion_ratio, vacuum_detected, bid_wall_persistence,
ask_wall_persistence, bid_wall_growth, ask_wall_growth, queue_depletion_rate,
liquidity_sweep_bullish, liquidity_sweep_bearish, spoof_score, absorption_ratio,
bid_consume_rate, ask_consume_rate, queue_pressure, order_add_cancel_imbalance,
absorption_persistence, book_replenishment_rate, cross_exchange_lead_lag
```

### Derivatives And Liquidations

```text
funding_rate, funding_velocity, oi_change, long_short_ratio, funding_x_oi,
global_oi_change, oi_divergence_norm, long_liq_volume, short_liq_volume,
liq_imbalance, liq_acceleration, basis_spread, basis_velocity,
funding_oi_interaction, time_to_funding, oi_momentum, price_oi_interaction
```

### External, Options, Cross-Asset And Macro

```text
fear_greed_norm, coinbase_premium_norm, coinbase_premium_velocity_norm,
chainlink_price_norm, fv_deviation, put_call_ratio, options_skew_25d,
max_pain_distance, atm_iv_norm, stablecoin_flow, exchange_netflow,
eth_btc_price_ratio, sol_btc_price_ratio, eth_volume_norm, sol_volume_norm,
eth_imbalance, sol_imbalance, macro_dxy_norm, macro_us10y_norm
```

### Polymarket Record-Forward

```text
polymarket_relevant_event, polymarket_probability_change,
polymarket_liquidity, polymarket_event_shock
```

| Action | Count | Meaning |
|---|---:|---|
| KEEP | 57 | kline-derived with parity |
| PARITY-FIX | 12 | aggTrade-derived; requires populated backfill and live overlay |
| RETIRE | 63 | excluded due source/availability/evidence |
| RECORD-LIVE | 4 | Polymarket frontier requiring forward collection |

## 18. Research Matrix Features

The separate 360-day lane uses about 160 columns: spot/futures/mark OHLC, returns/ranges from 1m-240m,
volume/trade count/taker delta/CVD, basis, ATR/RV, EMA/RSI/MACD/Bollinger/Donchian, z-scores, funding clock
and sessions. The exact list is in `MODEL_RESEARCH_CATALOG_AND_APP_PROPOSAL_2026-06-17.md`. A research
feature cannot become live until timestamp, formula and normalization parity are proven.

## 19. Retired, Missing Or Misleading Names

| Name | Current truth |
|---|---|
| Kronos | `backend/kronos_model.py` does not exist; not active |
| SGD log-loss | retired as anti-signal |
| LSTM/GRU | optional TCN replacement, not extra live seats |
| Transformer/PatchTST/iTransformer/VLSTM | research-only |
| Mamba family | not fully tested due platform/dependencies |
| Polymarket residual model | placeholder only |
| Beat model | trainer exists; artifact absent |
| Fade model | stale artifact; deliberately rejected |
| old seven-horizon pickles | present but only 5m/15m served |
| FSR-PPO | deterministic shadow until weights exist |

## 20. User-Facing Output Ownership

| UI concept | Source |
|---|---|
| UP/DOWN/NEUTRAL | regime-routed stacked direction ensemble |
| confidence | smoothed/calibrated model probability plus safety adjustments |
| expected dollar move | main magnitude regressor plus regime prior |
| low/median/high | signed conformal quantile band |
| P(Hold) | persistence head |
| movement likely | big-move keeper |
| big-drop warning | big-drop keeper |
| big UP/DOWN confirmation | directional keepers |
| activity | activity/range keeper |
| touch/round-trip/style | path forecaster |
| flip risk / remaining shocks / next opportunity | round-state shadow bundle; information only |
| BUY/SELL/WAIT/AVOID | Champion plus meta/trust/expectancy gates |
| fair entry | capped fair value minus ask, fee and buffer |
| exact depth VWAP | deterministic L2 calculation |
| queue fill | bounded simulation, not exact rank |

## Bottom Line

The app is layered, not one giant model: a weak direction ensemble, stronger path/magnitude/activity/hold
specialists, filters that decide when not to trust a call, and execution math that tests whether edge is
tradable. Profit remains unproven until forward quotes, depth, fills, fees and settlement jointly show
positive net expectancy.

## Research Update: Round ORB And Systemic Absorption

The July 2 causal probes did not promote either idea. Round ORB produced a notable `+0.0382` AUC lift
only for 15m line-cross risk, but its P(Hold veto discarded 166 correct calls to avoid seven misses.
PCA systemic absorption reduced AUC for all tested 5m/15m big-move and big-drop models and did not
improve P(Hold retention. Full evidence:
`ROUND_ORB_AND_SYSTEMIC_ABSORPTION_RESULTS_2026-07-02.md`.
