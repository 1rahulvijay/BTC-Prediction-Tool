# BTC Quantum Trader — UI Guide

This is the canonical reference for **what the user sees on screen**, what every card/metric means,
and where each value comes from (payload key → backend builder). It complements
`system_architecture.md` (the engine) by documenting the presentation layer.

The frontend is a single-page app: `index.html` (structure) + `src/main.js` (render logic) +
`src/style.css` (styling). It receives one JSON `update` payload per backend tick over the
WebSocket `/ws`, and additionally calls `GET /api/action-log` for the Action Log.

---

## Current Latest UI Addition

- **FSR-PPO Strategy Challenger** appears in the Decision Center after the primary
  decision cockpit. It shows a separate reinforcement-learning style paper-policy
  recommendation: `AVOID`, `BUY_SMALL`, `BUY_MEDIUM`, `SELL_SMALL`, or
  `SELL_MEDIUM`.
- It reads `payload.fsr_ppo` and `payload.fsr_ppo_summary`.
- It is persisted in DuckDB table `fsr_ppo_decisions`.
- It is a measured challenger only. It does not override the ensemble decision.

---

## 1. Top Bar (always visible)

| Element | Meaning | Source |
|---|---|---|
| Price + 24h change | Live BTC spot | `payload.price`, `payload.ticker_24h` |
| Volume / High / Low | 24h ticker stats | `payload.ticker_24h` |
| Model health badge | Overall system health grade | `payload.health` |
| Boot chip | Backend startup-to-ready time | `payload.boot_status` |
| Backtest chip + **Run Backtest** | Background validation status/trigger | `payload.backtest_status`, `POST /api/backtest` |
| Relearn chip + **Relearn Models** | Candidate retrain status/trigger | `payload.relearn_status`, `POST /api/relearn` |

## 2. Tabs

Three tabs (`.app-tab`, switched in `main.js` tab handler):

1. **Technical + Live Feed** — chart, indicators, predictions, order flow, tape, derivatives, verification, backtest.
2. **Decision Center** — beginner-friendly decision cockpit with action, trust, risk,
   confirmation and evidence gates first.
3. **Models & Signals** — *(new)* per-model accuracy, price-to-beat rounds, action log, inventory.

---

## 3. Decision Center tab

Reorganized to read **top-down**: the first screen answers "what should I do now?",
"why?", "what can go wrong?", and "which evidence gates passed?". Dense detail remains
below in collapsible groups (`<details class="pa-group">`).

### Always visible
- **Decision Center cockpit** (`#decision-cockpit`, `renderDecisionCockpit`) — primary human
  decision surface. It shows the selected timeframe, final action (`BUY SETUP`,
  `SELL SETUP`, or `WAIT / NO TRADE`), rating (`A`, `B`, `C`, or `WATCH`), expected
  price zone, trust score, risk rule, reason, invalidation condition, next
  confirmation, and six evidence gates: models, Kronos, flow, regime, live record,
  and data freshness. Source: `payload.predictions[*]`, `payload.kronos_forecasts`,
  `payload.scoreboard`, `payload.verification`, `payload.feed_health`,
  `payload.derivatives`, `payload.order_flow`, `payload.regime`, and recent candles.
- **Live Market Pulse** (`#global-pulse-grid`, `renderGlobalPulse`) — one card per horizon (1/3/5/7/10/15m). Each card shows the final **ensemble** decision, the risk-gated **action** (BUY/SELL/AVOID), confidence, and the model's expected price zone. Source: `payload.predictions[*]` + `payload.price`.
- **Kronos Forecast Targets** (`#forecast-pulse-grid`, `renderForecastPulse`) — separate Kronos/fallback path target per horizon. Source: `payload.kronos_forecasts` + `payload.kronos_status`. Treat as zones, not exact prices.
- **Signal Flow** (`#signal-flow-grid`, `renderSignalFlow`) — selected-timeframe flow: ensemble lean, safety action, Kronos cross-check, and scoring rule.
- **Timeframe sub-tabs** (`.tf-tab`) — placed inside the cockpit so the whole page reads from the selected decision horizon.
- **Deep Analysis hero** — one-line verdict (`#analysis-verdict`), plain meaning, VRP quantile bell curve (`#quantile-curve-container`), and timeframe confidence.
- **Decision Guide** — Action read / Main reason / Risk note / Next check.
- **Can I Trust This?** — single trust score blended from confidence, agreement, sample size, price-error and skip filters.

### Collapsible group: "Performance & accuracy"
- **Why This Action?** — plain reasons for BUY/SELL/AVOID.
- **Prediction Rates** — Signal Expectancy (USD), miss rate, direction-right, avg price error, UP/DOWN error. Source: `payload.verification` + `payload.execution_simulator`.
- **Action Accuracy** — separate scorecards for ALL / BUY / SELL / AVOID.

### Collapsible group: "Risk & capital preservation"
- **Capital Preservation (Avoid Success)** — dollars preserved by the AVOID layer, trades avoided, good-avoid rate.
- **Regime Health & Profit Factor** — current regime, profit factor (target > 1.2), max drawdown.
- **Challenger Lab (A/B)** — primary vs challenger accuracy + significance. Source: `payload.ab_test`.

### Collapsible group: "Signals & indicators"
- **Live Signals** — what the machine sees right now (buy/sell pressure cards).
- **Error Examples** + **Support / Resistance** — direction-right-but-target-wrong examples; nearest S/R.
- **Top Indicator Analysis** — readable translation of the strongest live inputs.

### Bottom of tab (always visible)
- **BTC Direction Scoreboard — 5m, 15m & 30m** (`#scoreboard-grid`, `renderScoreboard`) — our model, conviction-gated. Only high-conviction calls are "actionable". Source: `payload.scoreboard` (`build_scoreboard`).
- **Multi-Exchange Consensus** (`#exchange-strip`, `renderExchanges`) — median consensus price + per-venue bps deviation across Binance, Coinbase, Bybit, KuCoin, **Chainlink** (now live again). Source: `payload.exchanges` (`build_exchanges_block`).

---

## 4. Models & Signals tab (new)

Renders from the WS payload (`renderModelsView`) plus a REST fetch for the log.

### Price to Beat — 5m, 15m & 30m (`#ptb-grid`, `renderPriceToBeat`)
The self-contained replacement for the removed Polymarket "Value Engine". Each round:
- **Price to beat** — the reference price locked at round start.
- **Our call + action** — UP/DOWN and STRONG BUY / STRONG SELL / WAIT.
- **Kronos** call and **conviction**.
- **Result** — ⏳ open (with resolve time), or ✓ correct / ✗ wrong once the horizon elapses, with the actual close price.
- **Live accuracy** per horizon (hits/resolved).
- **Recent resolved rounds** strip below.

Source: `payload.price_to_beat = { latest, accuracy, recent }`, produced by `PriceToBeatTracker`
(`backend/price_to_beat.py`), persisted to DuckDB `price_to_beat`.

### Price-to-Beat Signal Rating (`#ptb-confluence-grid`, `renderPriceToBeatConfluence`)
The confluence scoreboard for the same 5m/15m price-to-beat rounds. It reconciles:

- ensemble final direction
- Kronos direction
- live order-flow agreement
- regime agreement

The card gives a plain signal rating: `BUY / BEAT`, `SELL / NOT BEAT`, `WAIT`, or lean-only,
plus the grade, conviction, expected beat/not-beat outcome, raw lean, Kronos direction, and
sample counts. Source: `payload.scoreboard`, `payload.price_to_beat`, `payload.kronos_accuracy`.

### Model Roster & Live Accuracy (`#model-roster`, `renderModelRoster`)
Top rows reconcile **Ensemble final** and **Kronos path** with the base models. Base model rows
then show XGBoost, LightGBM, CatBoost, HistGradientBoosting, TCN/Sequence, Logistic Regression and
SGD. Each horizon cell shows the current vote/path and live accuracy (hits/resolved).

Sources:

- Ensemble final: `payload.predictions` + `payload.verification.accuracy`
- Kronos path: `payload.kronos_accuracy`
- Base models: `payload.model_accuracy` (`PerModelVerifier` in `backend/model_verifier.py`,
  fed by each prediction's `modelDirs`), persisted to DuckDB `model_predictions`.

### Action & Trade Log (`#action-log`, `renderActionLog`)
Timestamped feed (latest first) of recorded predictions across all horizons: time, timeframe,
**action** (BUY/SELL/AVOID), **expected** move, **reference** price, and **result**
(✓ hit / ✗ miss / ⏳ pending with the realized move). Auto-refreshes every 15s while the tab is open.

Source: `GET /api/action-log?limit=N` → `database.fetch_action_log` (unions `predictions_{h}m`).

### Model Inventory (`#model-inventory-grid`, `renderModelInventory`)
Per-model availability (installed/not), trained-head count, and the LightGBM execution device.
Also shows the active deep sequence architecture (`deep_model_arch`, default `TCN`).
Source: `payload.model_inventory` (`model.get_model_inventory`).

---

## 5. Technical + Live Feed tab
Unchanged by this pass: chart (candles + RSI + MACD + Kronos overlay + S/R), directional consensus,
predictions grid, alerts, verification log with per-horizon tabs, tape, order book, derivatives,
backtest metrics. Driven by the same `update` payload.

---

## 6. Payload key → UI map (quick index)

| Payload key | Rendered by | Where |
|---|---|---|
| `price`, `ticker_24h` | `renderPrice` | top bar |
| `predictions` | `renderPredictions`, `renderGlobalPulse` | Technical + Plain |
| `kronos_forecasts` | `renderForecastPulse` | Plain |
| `scoreboard` | `renderScoreboard` | Plain (Direction Scoreboard) |
| `exchanges` / `chainlink_price` | `renderExchanges` | Plain (Multi-Exchange Consensus) |
| `model_accuracy` | `renderModelRoster` | Models & Signals |
| `price_to_beat` | `renderPriceToBeat` | Models & Signals |
| `model_inventory` | `renderModelInventory` | Models & Signals |
| `verification`, `execution_simulator`, `ab_test` | Decision Center cards | Decision Center |
| `signal_policy`, `verification.neutral_summary` | `renderDecisionCockpit`, `renderActionReasons` | Decision Center |
| `kronos_status`, `kronos_accuracy` | forecast + scoreboard | Decision Center |
| (REST) `/api/action-log` | `renderActionLog` | Models & Signals |

---

See `system_architecture.md` → **Document Sync Map** for how this guide relates to the other docs
and which file to update for which kind of change.

---

## 7. Signal Flow Clarification

Current UI source-of-truth:

- **Decision Center cockpit** is the first thing to read. It is the final plain-English
  action board for the selected timeframe.
- **Live Market Pulse** is the final ensemble/action view from `payload.predictions`.
- **Kronos Forecast Targets** is the separate Kronos/fallback path from `payload.kronos_forecasts`.
- **Signal Flow** explains the selected timeframe in four steps: ensemble lean, final safety action, Kronos cross-check, and result-scoring rule.
- **AVOID/NEUTRAL** outcomes are scored as skip/avoid outcomes, not as normal UP/DOWN directional bets.
