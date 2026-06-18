# Profitable Betting Agent Requirements

Date: 2026-06-16

Status: requirements draft, not implementation.

Purpose: define the minimum system needed to turn BTC Quantum Trader from a decision-support tool into a guarded betting/trading agent that can place real bets only when the measured expected value is positive.

Important honesty note: this document cannot promise profit. A bot becomes "profitable" only if live, out-of-sample results prove positive expectancy after spread, fees, slippage, failed fills, latency, and legal venue constraints. The requirement therefore makes profitability a promotion gate, not an assumption.

---

## 1. Current Context

The current app is a research and decision-support platform, not yet a proven production betting bot.

Current useful pieces:

- Main ensemble: v11 pruned-69 model schema.
- Raw app schema: 136 features.
- Model schema: 69 trainable features from `KEEP` + `PARITY-FIX`.
- Direction prediction: repeatedly measured near coin-flip after costs.
- Useful edge candidates:
  - calibrated 80% move band,
  - keeper-enhanced `P(Hold)`,
  - selectivity model,
  - price-to-beat resolution,
  - abstention/AVOID filters,
  - execution simulator,
  - model metrics DuckDB,
  - Polymarket-style implied probability comparison.

Core conclusion:

The bot must not bet because the model says "UP" or "DOWN". It may bet only when:

```text
model fair probability - market implied probability - costs - safety buffer > required edge
```

That is the only defensible path from prediction to profitability.

---

## 2. Non-Negotiable Safety And Compliance Requirements

### 2.1 Legal venue gate

The agent must only connect to venues that the operator is legally allowed to use in their jurisdiction.

Hard requirements:

- No VPN bypass.
- No geofence bypass.
- No offshore workaround.
- No automated betting on venues that prohibit automated access.
- No trading if the venue terms/API permissions are unknown.
- No trading if the local jurisdiction status is unknown.
- The UI must show: `LIVE BETTING DISABLED: legal venue not verified` until manually configured.

The operator appears to be in the United States in this environment. U.S. prediction-market access is legally sensitive and venue-specific. The bot must support a compliance checklist before any live order adapter is enabled.

### 2.2 Human activation gate

Default mode must be paper-only.

Live mode requires all of:

- explicit UI toggle,
- typed confirmation phrase,
- configured bankroll cap,
- configured daily loss cap,
- configured venue,
- completed legal/venue checklist,
- at least one successful paper-trading validation window.

### 2.3 Kill switch

The agent must have a single global kill switch:

```text
BOT_ENABLED=false
```

When false:

- no new orders,
- cancel all open bot orders if venue supports cancellation,
- keep logging market data,
- keep scoring paper decisions.

---

## 3. Bot Objective

The bot's objective is not "more trades".

The objective is:

```text
maximize risk-adjusted realized expectancy after all costs
```

Primary success metrics:

- net profit after fees,
- profit factor,
- expectancy per bet,
- maximum drawdown,
- calibration error,
- fill-adjusted ROI,
- Sharpe-like return stability,
- live-vs-paper degradation.

The bot must prefer no trade over marginal trade.

---

## 4. Supported First Market

First supported market should be BTC binary up/down windows only.

Preferred initial target:

- 5m BTC up/down window,
- 15m BTC up/down window,
- 30m only after enough resolved data.

Do not start with:

- leverage futures auto-trading,
- high-frequency scalping,
- multi-asset betting,
- arbitrary Polymarket events,
- politics/news markets,
- sports markets.

Reason:

The current app already has price-to-beat logic, P(Hold), model metrics, and BTC-specific market data. Keep blast radius narrow.

---

## 5. Betting Math

For a binary contract priced at `c` where payout is `$1` if correct:

```text
p = model fair probability
c = market ask price including spread impact
edge_per_share = p - c
ev_per_share = p * (1 - c) - (1 - p) * c
ev_per_dollar_staked = (p - c) / c
```

The bot may consider an entry only if:

```text
p_model_calibrated
- market_ask
- estimated_fee
- estimated_slippage
- calibration_safety_buffer
>= min_required_edge
```

Initial recommended gates:

```text
min_required_edge = 0.04      # 4 percentage points
min_model_probability = 0.58
max_market_ask = 0.92
max_spread = 0.03
min_liquidity_usd = 100
max_latency_ms = 1500
```

These are starting requirements, not final tuned values.

---

## 6. Position Sizing

Use fractional Kelly, capped hard.

Binary Kelly fraction:

```text
kelly_fraction = (p - c) / (1 - c)
```

Bot stake:

```text
stake = bankroll * clamp(kelly_fraction * kelly_multiplier, 0, max_position_pct)
```

Initial values:

```text
kelly_multiplier = 0.10
max_position_pct = 0.25% of bankroll
max_daily_loss_pct = 1.00% of bankroll
max_open_risk_pct = 0.75% of bankroll
```

Hard rules:

- no martingale,
- no doubling after loss,
- no revenge trade,
- no position increase after feed stale,
- no trade if drawdown circuit breaker is active.

---

## 7. Decision State Machine

The agent must use this state machine:

```text
DISABLED
  -> PAPER_ONLY
  -> SHADOW_LIVE_QUOTES
  -> MICRO_LIVE
  -> GUARDED_LIVE
  -> PAUSED
  -> DISABLED
```

### DISABLED

No decisions, no orders. UI only.

### PAPER_ONLY

Generate hypothetical orders and score them after settlement.

### SHADOW_LIVE_QUOTES

Read live venue quotes and order book. Do not place orders. Score whether the bot would have filled and won.

### MICRO_LIVE

Live orders allowed, but with tiny max stake and strict loss cap.

### GUARDED_LIVE

Higher stake cap allowed only after promotion gates pass.

### PAUSED

Temporary stop from circuit breaker. No new orders.

---

## 8. Promotion Gates

The agent cannot move to the next state unless all gates pass.

### Paper to shadow

Minimum:

```text
settled_paper_bets >= 300
net_expectancy > 0
profit_factor >= 1.10
max_drawdown <= 5%
brier_score stable or improving
calibration_error acceptable
```

### Shadow to micro-live

Minimum:

```text
settled_shadow_bets >= 500
fill_adjusted_expectancy > 0
profit_factor >= 1.20
lower_95_confidence_expectancy >= 0
max_drawdown <= 5%
edge survives spread and partial fill assumptions
```

### Micro-live to guarded-live

Minimum:

```text
settled_live_bets >= 300
net_realized_profit > 0
profit_factor >= 1.20
expectancy_per_bet > 0
max_drawdown <= configured cap
no unresolved system incidents
no compliance warnings
```

No manual override should promote the bot unless the UI shows all failed gates.

---

## 9. Entry Requirements

The bot may place a bet only if every required gate passes.

Required gates:

- legal venue verified,
- bot state allows live orders,
- market is supported,
- market settlement rule matches model target,
- P(Hold)/P(Beat) model loaded,
- calibrated probability available,
- market quote fresh,
- BTC reference price fresh,
- Pyth/Chainlink/venue settlement proxy healthy,
- no feed stale warning,
- no model drift warning,
- no unresolved open incident,
- spread below max,
- liquidity above min,
- edge above min,
- position size above venue minimum,
- bankroll/risk cap allows trade,
- no duplicate exposure in same window.

If any gate fails, log `SKIP` with a plain-English reason.

---

## 10. Exit Requirements

The bot must support these exit paths:

- hold to settlement,
- cancel unfilled order,
- early exit if edge turns negative,
- early exit if feed goes stale,
- early exit if venue quote becomes invalid,
- early exit if market settlement source diverges from expected reference,
- force-close or pause after kill switch.

For binary markets, the default should be hold-to-settlement unless:

```text
current_edge < -exit_edge_threshold
or feed_stale
or spread_explodes
or settlement_proxy_diverges
or risk_circuit_breaker_active
```

---

## 11. Required Data Tables

Create a separate bot database:

```text
data/bot_agent.duckdb
```

Do not write bot execution tables into the main live analytics DB.

Required tables:

### `bot_decisions`

One row per possible decision.

Fields:

- decision_id
- timestamp
- bot_state
- venue
- market_id
- horizon
- side
- action: BET / SKIP / CANCEL / EXIT
- model_probability
- market_bid
- market_ask
- market_mid
- spread
- estimated_fee
- estimated_slippage
- required_edge
- net_edge
- stake_usd
- reason_codes
- plain_english_reason
- model_bundle_id
- feature_schema_hash

### `bot_orders`

One row per submitted order.

Fields:

- order_id
- decision_id
- venue_order_id
- side
- limit_price
- size
- submitted_at
- status
- cancel_reason
- latency_ms

### `bot_fills`

One row per fill.

Fields:

- fill_id
- order_id
- fill_price
- fill_size
- fee
- filled_at
- venue_trade_id

### `bot_positions`

One row per active/resolved position.

Fields:

- position_id
- market_id
- side
- avg_entry_price
- size
- stake_usd
- current_mark
- status
- settlement_result
- realized_pnl
- resolved_at

### `bot_risk_events`

One row per risk block/circuit breaker.

Fields:

- timestamp
- event_type
- severity
- reason
- bot_state_before
- bot_state_after
- action_taken

### `bot_daily_pnl`

One row per day.

Fields:

- date
- starting_bankroll
- ending_bankroll
- realized_pnl
- fees
- trades
- wins
- losses
- profit_factor
- expectancy
- max_intraday_drawdown

---

## 12. Required UI

Add a new tab:

```text
Bot Agent
```

Beginner-friendly panels:

### Top status

- BOT OFF / PAPER / SHADOW / MICRO LIVE / GUARDED LIVE / PAUSED
- current bankroll
- today's P&L
- today's loss cap remaining
- open exposure
- next allowed action

### Current decision

Plain language:

```text
SKIP: model says 64%, market costs 63%, but after spread and safety buffer edge is only 0.5%.
```

or:

```text
PAPER BET: model fair value 72%, market ask 61%, net edge 6.2%, stake $2.50.
```

### Why bet / why skip

Show:

- model probability,
- market price,
- net edge,
- spread,
- liquidity,
- feed freshness,
- risk cap,
- model status,
- legal/venue status.

### Active orders

- open orders,
- filled orders,
- cancelled orders,
- latency,
- fill quality.

### Performance

- paper P&L,
- live P&L,
- profit factor,
- expectancy,
- drawdown,
- win rate,
- Brier score,
- calibration chart,
- performance by horizon.

---

## 13. Execution Adapter Requirements

Every venue adapter must implement:

```python
class BettingVenueAdapter:
    def get_markets(self) -> list[Market]
    def get_order_book(self, market_id: str) -> OrderBook
    def estimate_fill(self, market_id: str, side: str, price: float, size: float) -> FillEstimate
    def place_limit_order(self, market_id: str, side: str, price: float, size: float) -> OrderResult
    def cancel_order(self, venue_order_id: str) -> CancelResult
    def get_open_orders(self) -> list[Order]
    def get_positions(self) -> list[Position]
    def get_balance(self) -> Balance
```

Hard rule:

No market orders in v1.

Only limit orders with:

- max price,
- max size,
- max order age,
- cancel-on-stale-feed.

---

## 14. Security Requirements

API keys:

- environment variables only,
- never stored in DuckDB,
- never printed to terminal,
- never sent to frontend,
- no keys in screenshots,
- no keys in markdown.

Recommended env vars:

```text
BOT_AGENT_ENABLED=0
BOT_MODE=PAPER
BOT_VENUE=NONE
BOT_BANKROLL_USD=100
BOT_MAX_POSITION_PCT=0.0025
BOT_MAX_DAILY_LOSS_PCT=0.01
BOT_MIN_EDGE=0.04
BOT_KILL_SWITCH=1
```

---

## 15. Backtesting And Validation Requirements

Before live betting:

1. Replay historical price-to-beat windows.
2. Replay recorded live quotes if available.
3. Score model fair value vs market implied price.
4. Apply spread, fees, partial-fill assumptions.
5. Apply latency assumptions.
6. Produce full P&L curve.

Minimum reports:

- net P&L,
- gross profit,
- gross loss,
- profit factor,
- expectancy per bet,
- drawdown,
- win rate,
- ROI,
- average edge at entry,
- realized edge,
- calibration by probability bucket,
- performance by horizon,
- performance by time of day,
- performance by regime,
- performance by spread bucket.

---

## 16. Implementation Phases

### Phase 1: Paper Bot

Build:

- `backend/bot_agent.py`
- `backend/bot_risk.py`
- `backend/bot_store.py`
- `backend/bot_math.py`
- `backend/venue_adapters/base.py`
- UI Bot Agent tab

No live orders.

Acceptance:

- 300+ paper decisions logged.
- Every skip has a plain-English reason.
- Bot never places real order.

### Phase 2: Live Quote Shadow

Build:

- live order-book reader for chosen legal venue,
- fill estimator,
- shadow fill simulation,
- quote freshness monitor.

Acceptance:

- 500+ shadow decisions.
- fill-adjusted expectancy report.
- no API key exposed.

### Phase 3: Micro Live

Build:

- real limit-order adapter,
- cancel-on-stale-feed,
- position monitor,
- kill switch,
- risk circuit breaker.

Acceptance:

- tiny max position only,
- real P&L logged,
- no order without all gates passing.

### Phase 4: Guarded Live

Only after Phase 3 passes promotion gates.

Build:

- fractional Kelly sizing,
- horizon-specific sizing,
- regime-specific risk caps,
- automatic pause on drift/drawdown.

Acceptance:

- profit factor >= 1.20 over enough live samples,
- positive realized expectancy,
- max drawdown within cap.

---

## 17. Hard Rejection Rules

The agent must never bet when:

- bot is disabled,
- compliance checklist incomplete,
- market/venue legality unknown,
- model probability missing,
- market ask missing,
- spread too wide,
- liquidity too low,
- net edge below threshold,
- feed stale,
- price source stale,
- model drift significant,
- current day loss cap hit,
- open exposure cap hit,
- model bundle stale,
- settlement rule mismatch,
- UI kill switch active.

---

## 18. Acceptance Criteria For "Profitable Bot"

Do not call it profitable until all are true:

```text
live_settled_bets >= 500
net_realized_pnl > 0
profit_factor >= 1.20
expectancy_per_bet > 0
max_drawdown <= configured cap
calibration_error acceptable
edge remains positive after fees/spread/slippage
no compliance incidents
```

Until then, label it:

```text
paper agent
shadow agent
micro-live experiment
```

not:

```text
profitable bot
```

---

## 19. First Build Recommendation

Build Phase 1 first:

```text
Paper betting agent using P(Hold) fair value vs live/persisted market implied price.
```

Do not start with a full live execution bot. The current evidence says the edge, if it exists, is in mispricing:

```text
P(Hold) - market ask - spread - fees
```

not in raw UP/DOWN prediction.

---

## 20. Regulatory Sources To Recheck Before Live Mode

The regulatory status of prediction markets changes quickly. Recheck current official venue terms and local law before enabling live orders.

Context sources reviewed while drafting this requirement:

- [Barron's: Polymarket cleared to offer prediction markets as U.S. regulator remains quiet on sports betting](https://www.barrons.com/articles/polymarket-kalshi-prediction-markets-sports-betting-a2c4db3a) — reported that Polymarket received CFTC approval to offer event contracts through U.S. brokerage-style access, while direct U.S. access and sports/event-contract treatment remained venue/regulator specific.
- [Axios: CFTC prediction-market sports event contract rules](https://www.axios.com/2026/06/10/cftc-prediction-markets-sports-event-contract-rules) — reported on 2026-06-10 that the CFTC was proposing formal rules for sports-style prediction markets, with a public-comment process still underway.
- [Barron's: Polymarket has problems Congress can't fix](https://www.barrons.com/articles/polymarket-prediction-markets-regulation-congress-afc84169) — reported continuing concerns around offshore access, U.S. users, and contracts that may remain restricted.

This app must treat those as compliance warnings, not permission to trade.
