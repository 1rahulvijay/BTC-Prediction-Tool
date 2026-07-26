# BTC Prediction Tool — Current System, Future Architecture, Research Governance, and Profitability Blueprint

**Status date:** 2026-07-26  
**Repository:** `1rahulvijay/BTC-Prediction-Tool`  
**Latest commit reviewed:** `b7306cb3c46b802e4f0d51669f9f436837ac5037`  
**Mode:** Research, shadow, and paper only  
**Real-money orders:** Disabled  
**Primary objective:** Build an auditable BTC trading-research platform that identifies rare, executable, post-cost opportunities rather than merely predicting direction.

---

## HOW TO READ THIS DOCUMENT — state today vs. state described

This file mixes two things, and a reader cannot always tell which paragraph is which:

| | |
|---|---|
| **IS** — implemented and verified today | §5 (canonical status), §9.1, §11 (mechanism), §15, §16, §17 |
| **SHOULD BE** — target design, not yet true | §11 (`STRICT=1`), §19–21, §22–24, §31 |

**When a section states a setting or a target, it is describing the destination.** Before acting on
any of them, check the destination is reachable. Two live examples as of 2026-07-26:

1. **`BTC_STRICT_ARTIFACT_IDENTITY=1` (§11) would disable every head today.** Zero of twelve
   artifacts carry the required manifest, so strict mode refuses all of them and the app serves
   blind while logging one ERROR per artifact. The launcher therefore ships `0`. See the
   precondition in §11 and run `python backend/verify_artifact_identity.py` — it prints exactly
   which heads would load and tells you when the flag can safely be flipped.
2. **Most forward content is gated on data that does not exist yet.** Binance V1 (§18) needs four
   *continuous* qualifying weeks and the evidence clock has not started; Complete Trade Forecast
   (§19–20) needs eight weeks. Both are correctly marked blocked, but that is the majority of this
   document's forward surface waiting on a collector that is not deployed.

**A note on scale.** This document plans a great deal — eight new heads in §21 alone — on top of a
system whose single alive strategy (§17, late leader) **failed its own promotion gate** and is
statistically indistinguishable from zero. §4's objective (post-cost EV × capacity) argues for
finishing §31.1–31.3 before opening §21. Breadth here is a risk to be managed, not a plan to be
executed in order.

---

# 1. Purpose

This file is the operating memory and future design specification for the BTC Prediction Tool.

It explains:

- What the app does now.
- How it is built.
- What data it uses.
- What every major model and specialist head predicts.
- How training, calibration, full refit, backtesting, forward shadow testing, and promotion work.
- How the app decides between trade, watch, shadow, paper, and no trade.
- What has already been tested.
- What failed and should remain closed.
- What data and experiments are still required.
- How the future complete-trade prediction system should work.
- How the system should adapt to changing markets.
- How Oracle should run continuously without contaminating research.
- What must improve before any claim of durable profitability.

This is not a promise of profit. Profitability must be demonstrated through forward, executable, capacity-aware, post-cost evidence.

---

# 2. Review scope

This document consolidates the latest repository update and the main production and research paths, including:

- Artifact identity.
- Historical data quality.
- True OHLC construction.
- Main direction ensemble.
- Specialist heads.
- P(Hold) calibration.
- Head-health monitoring.
- Decision lockdown.
- Complete Trade Forecast V1.
- Multi-window experts.
- Collector integrity.
- Polymarket research closures.
- Binance V1 preregistration.
- Forward shadow and promotion logic.
- Oracle deployment expectations.

The latest reviewed commit adds or materially changes more than fifty files. The latest diff, core model and serving paths, active documentation, collector, decision logic, and new trade-forecast modules were reviewed. A literal claim that every line in every historical repository file has been formally verified would be inaccurate. A dedicated static-analysis and integration-audit phase is still required.

---

# 3. Product vision

The application should evolve from:

```text
Predict whether BTC goes UP or DOWN.
```

Into:

```text
Observe the market continuously.
Determine whether a real post-cost opportunity exists.
Predict the likely BTC and executable contract-price paths.
Estimate entry, exit, fillability, capacity, risk, and full PnL distribution.
Select the best venue, side, horizon, quantity, and causal exit plan.
Trade only when conservative expected value is positive.
Abstain whenever evidence, data, calibration, execution, or capacity is insufficient.
```

The future application is:

```text
Opportunity detection
+ execution intelligence
+ risk control
+ trade-plan forecasting
+ evidence governance
```

---

# 4. Non-negotiable objective

The objective is:

```text
maximize:
    post-cost expected PnL
    × executable capacity

subject to:
    positive lower confidence bound
    controlled drawdown
    controlled tail risk
    stable weekly performance
    causal data
    reliable execution
```

Do not optimize only:

```text
accuracy
win rate
AUC
confidence
number of trades
model agreement
```

High win rate can coexist with negative profit because of rare large losses, fees, spread, latency, slippage, adverse selection, insufficient depth, and poor calibration.

---

# 5. Current canonical status

```text
Real orders:
    disabled

Champion PAPER_BET:
    disabled by default

Kelly sizing:
    disabled by default

Polymarket late leader:
    failed promotion
    observation only

Polymarket 15m static TP/SL:
    closed

Polymarket maker:
    closed

Conditional stopping:
    closed

Binance volatility momentum V1:
    preregistered
    M0 blocked by data gate

Complete Trade Forecast V1:
    implemented
    pilot estimate only
    NO_TRADE

Multi-window 1265-day experts:
    infrastructure implemented
    true 1265-day run incomplete

Multi-venue evidence clock:
    not started
    Oracle deployment required
```

---

# 6. High-level architecture

```text
1. DATA INGESTION
2. DATA QUALITY AND ADMISSIBILITY
3. FEATURE ENGINEERING
4. BASE MODELS
5. SPECIALIST HEADS
6. CALIBRATION AND HEAD HEALTH
7. CURRENT MARKET STATE
8. EXECUTION STATE
9. POLICY AND GATE ENGINE
10. COMPLETE TRADE FORECAST
11. PAPER/SHADOW LOGGING
12. OUTCOME RESOLUTION
13. BACKTEST AND FORWARD EVALUATION
14. CHAMPION/CHALLENGER PROMOTION
15. UI AND OPERATIONS
```

Each layer must fail closed.

---

# 7. Data currently used

## 7.1 Market sources

- Binance spot aggregate trades.
- Binance spot top-of-book.
- Binance perpetual top-of-book.
- Binance perpetual delayed REST trade flow.
- Binance perpetual premium index.
- Binance perpetual open interest.
- Funding state.
- Bybit perpetual order book.
- Bybit public trades.
- Coinbase ticker.
- Pyth BTC/USD.
- Polymarket UP/DOWN books and settlement.

## 7.2 Historical one-minute data

The matrix contains:

```text
true open
high
low
close
volume
trade count
taker buy
taker sell
```

True aggregate-trade OHLC is:

```text
open   = first trade price in the minute
high   = maximum price
low    = minimum price
close  = last trade price
volume = sum quantity
```

The old `open = close` approximation has been removed.

## 7.3 Feature families

### Price and technical state

- Returns.
- Candle range/body.
- EMA/SMA relationships.
- RSI.
- Stochastic RSI.
- ADX.
- Supertrend.
- Williams %R.
- CCI.
- MFI.
- OBV.
- ATR.
- Momentum and trend efficiency.

### Volatility

- Realized volatility.
- EWMA volatility.
- Volatility acceleration.
- Variance ratio.
- Volatility term structure.
- Compression.
- Range expansion.
- Shock magnitude.

### Flow and participation

- CVD.
- Taker imbalance.
- Trade intensity.
- Volume acceleration.
- VPIN.
- Large-trade flow.
- Spot/perpetual flow divergence.

### Derivatives

- Funding.
- Funding velocity.
- Open interest.
- OI change.
- Basis.
- Premium index.

### Cross venue

- Binance spot/perpetual state.
- Binance/Bybit/Coinbase confirmation.
- Cross-venue disagreement.
- Observer-time arrival relationships.

### Regime and context

- Trend.
- Range.
- Volatile.
- Compression.
- Shock.
- Session and weekend state.

### Polymarket

- Price-to-beat.
- BTC distance from the line.
- Distance in volatility units.
- Seconds left.
- UP/DOWN bid, ask, spread, depth.
- Full ladders.
- Share-price velocity.
- BTC/share sensitivity.
- P(Hold).
- BTC and contract path estimates.

---

# 8. Data-admissibility contract

The central causal rule is:

```text
A feature becomes available at recv_ts.
```

## 8.1 Class A

Real-time push data.

Permitted for:

- Current flow.
- Current market state.
- Compatible timestamp-basis confirmation.

## 8.2 Class B

Delayed observable REST state.

Permitted for:

- Slow-state context.
- One-minute or slower aggregates.
- Funding, OI, delayed basis and delayed flow.

Not permitted for:

- Sub-second lead/lag.
- Who-moved-first claims.
- Restamping historical data into the present.

Frozen age:

```text
CLASS_B_MAX_AGE_S = 60.0
```

## 8.3 Timestamp basis

Every event is one of:

```text
EXCHANGE_TIME
RECEIVE_TIME
RECEIVE_ONLY
POLL_RECEIVE_TIME
```

No mixed-basis lead/lag.

Receive-time findings must be named:

```text
observer_time_lead
collector_arrival_lead
```

---

# 9. Multi-venue collector

Required streams:

```text
1. Binance spot bookTicker
2. Binance spot aggTrade
3. Binance perpetual bookTicker
4. Binance perpetual aggTrade REST
5. Binance perpetual premium index
6. Binance perpetual open interest
7. Bybit perpetual orderbook.1
8. Bybit perpetual publicTrade
9. Coinbase ticker
```

A qualifying episode requires:

```text
9/9 streams
persisted rows
WS age <= 5s
REST age <= 60s
no writer failure
no prolonged silence
valid event identity
valid episode boundary
```

## 9.1 Integrity fixes implemented

- Bybit publicTrade added to health denominator.
- Stale feeds cannot qualify.
- Evidence clock starts after successful persistence.
- Qualification uses persisted rows.
- Deduplication resolves identity globally before lookback filtering.
- REST duplicate and revision identities are distinct.
- `connection_id` and `poll_id` are separate.
- Four continuous weeks are mechanically measured.
- Writer failures propagate to systemd.
- REST calls no longer block the async event loop.
- A single fresh event followed by silence does not qualify.

## 9.2 Continuity

Current frozen meaning:

```text
four uninterrupted qualifying weeks
```

A single excluded episode breaks the run.

Any tolerance requires a new pre-data hashed clarification.

## 9.3 Evidence clock

Starts only when:

```text
the first valid event row is successfully persisted
```

Smoke tests do not start it.

---

# 10. Historical data quality

Monthly gates:

```text
minute coverage >= 98%
trade-feature coverage >= 98%
cross-venue coverage >= 98%
max unexplained gap <= 15m
duplicates = 0
future timestamps = 0
core nulls = 0
invalid OHLC = 0
```

Outputs:

```text
data/research_matrix_monthly_quality.json
data/research_matrix_monthly_quality.csv
```

Official OHLC parity defaults:

```text
median absolute difference <= $0.001
p99 absolute difference <= $0.011
minimum overlap >= 100 minutes
```

Parity failure preserves the previous valid matrix and aborts training.

---

# 11. Artifact identity

Every artifact must record:

```text
requested_days
actual_start_ts
actual_end_ts
actual_span_days
row_count
training_data_hash
source_manifest_hash
feature_schema_hash
code_hash
split timestamps
calibration timestamps
full_refit
artifact_hash
```

Main manifest:

```text
data/saved_models/artifact_manifest.json
```

Standalone heads:

```text
<artifact>.pkl.manifest.json
```

Multi-window runs:

```text
data/research/multiwindow_experts/<run>/artifact_manifest.json
```

Strict loading:

```text
BTC_STRICT_ARTIFACT_IDENTITY=1
```

Serving must reject data, code, feature, label, day-window, and artifact mismatches.

### ⚠ PRECONDITION — do not set this to 1 yet

`STRICT=1` is the correct end state and is **currently unreachable**. Manifests are written only by
the new training path, so every artifact now on disk predates them. Measured 2026-07-26:

```text
0/12 present artifacts would load   (12 lack a manifest)
-> P(hold), path forecaster, fade, signed-quantile, round-state and all four keepers refused
-> the app boots, serves NOTHING, and logs one ERROR per artifact
```

The launcher therefore ships `BTC_STRICT_ARTIFACT_IDENTITY=0`.

**Back-filling manifests is not the fix.** `artifact_compatibility()` compares *every* identity key
against the **current** training identity. A manifest recording these artifacts' real 400-day
provenance is refused anyway; one recording the current identity would be a lie about what trained
them. The honest interim state is "identity is not yet enforced, because no artifact can satisfy
it" — not a fabricated provenance.

**The precondition is a completed full retrain**, which writes real manifests. Then:

```bash
python backend/verify_artifact_identity.py     # prints per-artifact load status + a verdict
```

It reports `READY` only when every artifact proves its training data; only then set the flag to `1`
in `start.bat`. Until that verdict appears, enabling strict mode does not add integrity — it
removes the models.

---

# 12. Main direction ensemble

Models:

- XGBoost.
- LightGBM.
- CatBoost.
- Random Forest.
- HistGradientBoosting.
- Logistic Regression.
- TCN.
- OOF stacker.

Current served horizons:

```text
5m
15m
```

Raw direction remains a low-expectation target and should be confirmation until post-cost forward evidence proves otherwise.

---

# 13. Multi-window experts

Experts:

```text
W90
W400
W1265_RECENCY
W1265_SIMILARITY
```

Purpose:

- W90: current adaptation.
- W400: medium-cycle stability.
- W1265_RECENCY: broad history with recency.
- W1265_SIMILARITY: broad history weighted toward current-like regimes.

Current verified matrix remains:

```text
requested days: 360
rows: 518,400
span: 360 days
```

The code correctly refuses to label it 1265-day evidence.

Current weight:

```text
sample_weight =
    recency
    × regime similarity
    × data quality
```

TCN sampling:

```text
50% recent
25% historical regime-balanced
25% historical tail/change states
```

Promotion requires same-ID OOF, untouched test, forward shadow, calibration, and post-cost economics.

---

# 14. Training and refit

Candidate:

```text
98% fit
2% untouched recent test
```

Validated refit:

```text
preserve candidate metrics
refit production challenger on more/all eligible data
install silently
keep incumbent as decision source
promote only after forward evidence
```

Heads needing isotonic or conformal calibration must reserve fresh calibration rows.

Recommended cadence:

```text
main ensemble:
    every 2–4 weeks

movement/path/tail heads:
    weekly or biweekly

P(Hold ranking:
    weekly

P(Hold calibration:
    nightly challenger

execution heads:
    daily or every 2–3 days
```

---

# 15. Specialist heads

## P(Hold

Predicts whether the current leader holds to settlement.

Raw live output was overconfident.

## P(Hold calibration challenger

Uses:

- One row per round.
- Temporal splits.
- Per-horizon calibration.
- Logistic first.
- Isotonic only with adequate data.
- No automatic serving replacement.

Latest documented 5m result:

```text
raw:
    95.5% predicted
    86.8% realized
    ECE 0.08833
    BSS +0.0268

calibrated:
    87.4% predicted
    86.8% realized
    ECE 0.01361
    BSS +0.1043
```

The challenger wins but is not yet applied.

## Big move

Predicts probability of a meaningful absolute move using basis-point labels.

## Big drop

Downside path-risk veto.

## Directional keeper

Big-up/big-down confirmation.

## Activity

Active versus quiet window.

## Signed quantiles

Future movement ranges and asymmetric uncertainty.

## Path forecaster

Predicts:

- BTC high/low ranges.
- Touch probabilities.
- Round-trip probability.
- Early touch.
- Path style.
- Ride/fade/watch/skip context.

It does not prove a profitable exit.

## Round-state heads

Current round and path state.

## Champion meta

Attempts to rank Champion outcomes, but current action tiers are not monotonic.

## Head health

States:

```text
USABLE
CALIBRATION_ONLY
DRIFTED
DISABLED_NO_SKILL
SHADOW
INSUFFICIENT_DATA
```

Open gap:

```text
all decision paths do not yet mechanically enforce these permissions
```

---

# 16. Decision lockdown

Defaults:

```text
BTC_ENABLE_PAPER_BET=0
BTC_ENABLE_KELLY_SIZING=0
BTC_FREEZE_MODEL=1
```

A setup can be displayed but cannot authorize a paper bet by default.

If manually enabled:

```text
paper quantity = 1
```

Kelly remains disabled.

A valid economic candidate requires:

```text
fair value
- executable ask
- fees
- buffer
> required edge
```

But raw P(Hold fair value remains research-only until calibrated serving wins forward.

Known open items:

- Canonical `DecisionEnvelope`.
- Action relabeling.
- Head-health enforcement.
- Calibrated fair value.
- Removal of misleading confidence semantics.

---

# 17. Tested Polymarket findings

## Late leader

- High win rate.
- Weak PF.
- Negative lower bound.
- Around 0.6–0.8c lost per second.
- No scalable capacity.

Status:

```text
failed promotion
```

## Maker

Adverse selection.

Status:

```text
closed
```

## Static 15m TP/SL

No stable positive configuration.

Status:

```text
closed
```

## Conditional stopping

Profitable exits existed in hindsight, but causal policies failed.

Status:

```text
closed
```

## Complement arbitrage

Apparent edge was stale-book artifact.

Status:

```text
rejected
```

## Next-round drift

Continuation, reversal, and random all lost approximately costs.

Status:

```text
rejected
```

## Complexity selectors

Did not beat simple baseline or shuffled null.

Conclusion:

```text
structural intercept, not conditional alpha
```

---

# 18. Binance Volatility Momentum V1

Question:

```text
Can volatility expansion plus cross-venue state select
5m BTCUSDT perp long/short trades that remain positive after costs?
```

Frozen execution:

```text
one-minute decisions
5m horizon
taker entry/exit
1s primary latency
2s sensitivity
1x exposure
```

Frozen M0:

```text
Q5-Q3 >= 2bps
4 primary cells
BH q <= 0.10
chance-monotonicity control
```

Data gate:

```text
>= 4 continuous weeks
>= 1,000 qualifying episodes
```

If pass:

- Logistic.
- LightGBM.
- CatBoost.

If fail:

```text
close lane
fit no models
```

---

# 19. Complete Trade Forecast V1

Status:

```text
SHADOW_PILOT_ONLY
Champion unchanged
NO_TRADE
```

It estimates for BUY UP and BUY DOWN:

- Post-latency entry VWAP.
- BTC path.
- Share-price path.
- Break-even bid.
- Target bid.
- Invalidation bid.
- Fill probability.
- Capacity.
- Frozen causal plan outcomes.
- PnL quantiles.
- P(profit).
- CVaR.
- Holding time.
- Safe entry ceiling.
- NO_TRADE control.

Pilot:

```text
395 rounds
1 week
24,996 rows
5m and 15m
1/5/10/25/50/100 shares
```

This validates mechanics only.

## Canonical entry

```text
first synchronized book after decision + 500ms
walk ask ladder
charge taker fee
record partial fill
```

## Canonical exit

```text
walk bid ladder
require full exit depth
charge fee
or use official settlement
```

No midpoint or hindsight best exit.

## Share-path model

Predicts executable future bid/ask quantiles, MFE, MAE, first profitable time, and crossing probabilities.

Target:

```text
logit(future price) - logit(current price)
```

## BTC-path model

Predicts BTC quantiles, MFE, MAE, first event, anchor/upper/lower barrier risks.

## Execution heads

Predict:

```text
slippage q50/q80/q95
capacity q50/q80/q95
full-fill probability
quote survival
```

## Validation

```text
70% train
15% selection/calibration
15% untouched test
15-minute purge
```

## Frozen exit plans

```text
HOLD_TO_SETTLEMENT
TAKE_1C
TAKE_3C
TAKE_5C
TAKE_3C_OR_STOP_3C
TIME_EXIT_15S
TIME_EXIT_30S
TIME_EXIT_60S
BREAK_EVEN_LOCK_AFTER_3C
```

## Candidate gates

```text
expected PnL > 0
q10 PnL > 0
P(profit) >= 60%
P(full fill) >= 70%
capacity covers quantity
data healthy
M0 passed
utility beats NO_TRADE
```

Current limitations:

- One-week evidence.
- Marginal quantiles approximate joint paths.
- Passive queue fills not modeled.
- No M0 pass.
- No real causal live action proof.

---

# 20. Complete-trade PnL

Let:

```text
i = entry VWAP
m = exit VWAP
q = quantity
```

```text
PnL =
q × [(m - exit fee) - (i + entry fee)]
```

The app should calculate:

- Break-even bid.
- +1c/+3c/+5c target bids.
- Full-size lockability.
- MFE.
- MAE.
- First-profitable time.
- Settlement PnL.
- Capacity.

Post-entry states:

```text
LOSS
BELOW_BREAK_EVEN
BREAK_EVEN_REACHED
PROFIT_AVAILABLE
PROFIT_LOCKABLE
PROFIT_AT_RISK
EXIT_NOW
```

A profit is lockable only when the entire quantity can exit through the bid ladder after costs.

---

# 21. Future heads

## Net opportunity

```text
P(executable move > all costs + required profit)
```

## Competing risks

```text
P(long TP before SL)
P(short TP before SL)
P(neither)
```

## Execution cost

```text
cost q50/q80/q95
```

## Quote survival

```text
P(quote survives 500ms/1s)
P(arrival worse by >=1/2bp)
```

## Liquidity deterioration

```text
P(spread widens)
P(depth disappears)
P(slippage exceeds threshold)
```

## Regime duration

```text
P(trend/vol expansion/chop persists)
```

## Tail risk

```text
P(MAE > 5/8/12bps)
worst-5% expected loss
```

## Cross-venue confirmation

```text
P(other venues confirm)
P(confirmed move exceeds costs)
P(initial impulse reverses)
```

## Options fair value

Use Deribit IV/skew/term structure to estimate independent Polymarket probability.

---

# 22. L2 expansion

Build sequence-valid books:

```text
Binance spot depth 20
Binance perp depth 20
Bybit perp depth 50
```

Derive:

- Microprice.
- Multi-level OFI.
- Depth slope/convexity.
- Replenishment.
- Cancellation imbalance.
- Sweep impact.
- Recovery half-life.
- Queue depletion.
- Liquidity pull.

Primary use:

```text
execution, fillability, and risk
```

Not generic direction.

---

# 23. Shadow Gate Laboratory

Evaluate frozen policies on identical snapshots:

```text
CURRENT_CONTROL
CONFIDENCE_STRICT
AGREEMENT_STRICT
OPPORTUNITY_STRICT
EXECUTION_STRICT
RISK_STRICT
CALIBRATED_ONLY
ALL_STRICT
ULTRA_SELECTIVE
NO_CONFIDENCE_GATE
```

Log:

- All predictions.
- All gate values.
- Failed gates.
- Accepted and rejected opportunities.
- Near misses.
- Counterfactual outcomes.
- Cost decomposition.
- MFE/MAE.
- Capacity.

Use paired same-ID comparisons.

Gate ablations:

```text
all gates
minus confidence
minus execution
minus risk
minus regime
minus agreement
minus cost
```

Report coverage-profit frontier:

```text
coverage
EV/trade
EV/day
PF
lower bound
CVaR
drawdown
capacity
```

---

# 24. Cross-timeframe allocator

Group simultaneous predictions using:

```text
exposure_group_id
```

Choose at most one overlapping trade.

```text
utility =
    conservative net EV
    × fill probability
    - tail-risk penalty
    - overlap penalty
```

Choose best horizon or NO_TRADE.

---

# 25. Backtesting rules

A valid backtest uses:

- Chronological split.
- Purge.
- One round per partition.
- Receive-time causality.
- First book after latency.
- Ask-ladder entry.
- Bid-ladder exit.
- Both fees.
- Full quantity.
- Partial fills.
- Capacity.
- Official settlement.
- No midpoint.
- No hindsight best exit.
- No final-test threshold optimization.

Controls:

- Always long.
- Always short.
- Random side.
- Volatility-matched random.
- Shuffle labels.
- Momentum.
- Structural baseline.
- HOLD.
- NO_TRADE.

Metrics:

- Independent rounds.
- Calendar weeks.
- Net EV.
- Day-block lower bound.
- PF.
- Drawdown.
- CVaR.
- Positive weeks.
- Hour concentration.
- Regime stability.
- Capacity.
- Latency sensitivity.

---

# 26. Forward evidence and promotion

Every challenger must receive identical live snapshots.

Predictive head promotion requires:

- Positive Brier skill.
- Calibration or ranking-only status.
- Temporal stability.
- Same-ID improvement.
- Enough independent outcomes.

Trading strategy promotion requires:

```text
>= 500–1,000 executions
>= 8 weeks
post-cost EV > 0
day-block lower bound > 0
PF >= 1.20
positive most weeks
matched-control improvement
capacity positive
latency stress pass
forward/replay agreement
no single week/hour dominates
```

Complete Trade Forecast M0 additionally requires:

```text
positive Q5 lower-bound EV
Q5-Q3 >= 0.5c
broad monotonicity
positive Q5 every test week
positive under 1s latency
Q5 beats HOLD
regime stability
valid hashes
```

---

# 27. Market adaptation

## Real time

Update:

- Volatility.
- Regime.
- Spread.
- Depth.
- Quote age.
- Slippage.
- Flow.
- Cross-venue agreement.
- Funding/OI.
- Data quality.

## Nightly or periodic

```text
execution baselines:
    5–15 minutes

regime statistics:
    hourly

rolling percentiles:
    daily

calibration challenger:
    nightly

head health:
    daily

main retraining:
    every 2–4 weeks
```

Never auto-replace Champion after retraining.

---

# 28. Drift monitoring

Monitor:

## Data

- Feature distributions.
- Missingness.
- Volatility.
- Spread/depth.
- Basis/OI/funding.

## Prediction

- Probability distribution.
- Direction balance.
- Entropy.
- Coverage.
- Disagreement.

## Calibration

- Brier.
- Log loss.
- ECE.
- Reliability.

## Execution

- Slippage.
- Fill probability.
- Quote survival.
- Latency.
- Capacity.

## Economic

- EV.
- PF.
- Lower bound.
- Drawdown.
- CVaR.
- Positive weeks.

States:

```text
WARNING
PERSISTENT
RETRAIN_REQUIRED
FAIL_CLOSED
```

---

# 29. Oracle architecture

Services:

```text
btc-backend.service
btc-polymarket-recorder.service
btc-venues-recorder.service
btc-settlement-resolver.service
btc-health-monitor.service
btc-calibration-worker.timer
btc-drift-report.timer
btc-backup.timer
```

Oracle should handle:

- Inference.
- API/UI.
- Recording.
- Settlement.
- Shadow logging.
- Monitoring.
- Backups.
- Light calibration.

Oracle should not perform:

- Multi-day 1265-day training.
- Large backfills while serving.
- Heavy hyperparameter search.
- Parallel sequence training.

Security:

```text
BTC_DEPLOYMENT_ENV=production
BTC_REQUIRE_ADMIN_TOKEN=1
BTC_ADMIN_TOKEN=<secret>
```

---

# 30. What is already strong

- Causality is explicit.
- Collector failures fail visibly.
- Artifacts are hashed.
- True OHLC is built.
- Monthly quality is gated.
- Frozen experiments are documented.
- Closed strategies remain closed.
- Complete-trade mechanics are executable.
- PAPER_BET and Kelly are off.
- Day-window mismatch is rejected.
- NO_TRADE is mandatory.
- Capacity and full depth are modeled.
- Fee logic is canonicalized.

---

# 31. What must improve next

## 31.1 Canonical DecisionEnvelope

Unify Champion, composer, paper rules, and Complete Trade Forecast.

Fields:

```text
venue
strategy ID
action
side
raw/calibrated/lower probability
entry/exit VWAP
gross/net/lower EV
cost quantile
capacity
tail risk
data state
head states
reason codes
hashes
```

## 31.2 Enforce head health

No disabled head may influence fair value, rank, gate, confidence, or action.

> **PARTIALLY BUILT — 2026-07-26.** The **fair value** half is now enforced.
> `backend/head_permissions.py` reads the permissions that `monitoring/head_health.py` was already
> computing (and that nothing consumed), and `decision_champion.py`'s `PAPER_BET` branch now requires
> `may_price("p_hold")` in addition to the operator override.
>
> This closes a specific hole: `BTC_ENABLE_PAPER_BET=1` used to re-enable betting on the very
> probability the live data says cannot price. It can now only act on a head measuring `USABLE`.
> Against today's real report (`p_hold = CALIBRATION_ONLY`, ECE 0.0678 > 0.05) the override is
> inert, and the champion says so in words: *"CANDIDATE UP - p_hold may not price"*.
>
> Behaviour is fail-open-with-a-reason (a missing report cannot take serving down, but never reads
> as a pass), treats a report older than 14 days as `STALE`, and re-opens on its own when a head
> returns to `USABLE`. `BTC_ENFORCE_HEAD_HEALTH=0` gives an explicit observe-only mode.
>
> **Still advisory, not enforced:** `may_rank` and `may_display_confidence`. A `CALIBRATION_ONLY`
> head can still order candidates and drive the displayed tier. Finishing §31.2 means routing those
> two permissions through the same reader — which is best done together with the §31.1 action
> relabelling, since the tiers being displayed are the thing already shown to be non-monotone.
>
> Details and the verification table: `docs/active/DECISION_LOCKDOWN_AND_CALIBRATION_2026-07-26.md` §5b.

## 31.3 Calibrated P(Hold shadow serving

Serve:

```text
raw P(Hold
calibrated P(Hold
lower bound
calibrator hash
```

Do not enable action until downstream forward evidence wins.

## 31.4 More Complete Trade Forecast evidence

Deploy, accumulate eight weeks, freeze M0 score, test ranking, and close if it fails.

## 31.5 Joint path consistency

Marginal quantiles can create inconsistent future paths.

Future options:

- Matched historical residual scenarios.
- Conformal scenario sets.
- Copula.
- Temporal distribution model.

Prove simple ranking first.

## 31.6 Better dynamic microstructure

Add ladder slope, imbalance, cancellation/replenishment, elasticity, update intensity, and latency context.

## 31.7 Recent execution windows

Execution heads should use 7–30 recent days, not the same long window as path and tail heads.

## 31.8 Portfolio risk governor

Add:

- Maximum concurrent BTC exposure.
- Overlap control.
- Daily loss cap.
- Drawdown lock.
- Cooldown.
- Emergency halt.
- Data-health lock.

## 31.9 Independent information

Prioritize Deribit options and relative value over another technical direction model.

## 31.10 Dedicated audit

Audit:

- Every database writer.
- Every model loader.
- Every timestamp conversion.
- Feature/label alignment.
- Silent fallbacks.
- UI/backend semantic parity.
- Environment defaults.
- Direct raw-event queries.
- Research-to-production isolation.

---

# 32. Aspirational future decision card

```text
MARKET
    regime: volatility expansion
    data: healthy
    confirmation: strong
    execution: acceptable

OPPORTUNITY
    net opportunity: 72%
    tail-risk gate: passed

SIDE
    BUY UP expected net: +2.4c/share
    BUY DOWN expected net: -1.1c/share
    NO_TRADE: 0

ENTRY
    best ask: 61.8c
    predicted 500ms VWAP: 62.4c
    cost after fee: 64.0c
    full-fill probability: 84%
    capacity: 25 shares

BTC PATH
    current: $100,040
    price-to-beat: $100,000
    60s q10-q90: $99,990-$100,150

SHARE PATH
    30s bid q10/q50/q90: 61c / 68c / 79c

EXIT
    break-even bid: 65.6c
    +3c target: 68.6c
    invalidation: 60.5c
    target time: 18-42s

RISK
    P(profit): 64%
    q10 PnL: +0.4c
    CVaR: -4.2c
    P(full-size lockable): 59%

DECISION
    SHADOW_CANDIDATE
    evidence gate incomplete
```

---

# 33. Implementation roadmap

## Phase 1

1. DecisionEnvelope.
2. Head-health enforcement.
3. Calibrated P(Hold shadow serving.
4. Action relabeling.
5. Remove misleading confidence semantics.

## Phase 2

1. Deploy Oracle admin token.
2. Deploy Polymarket recorder.
3. Deploy 9-stream venue recorder.
4. Verify 9/9.
5. Start evidence clock.
6. Back up continuously.

## Phase 3

1. Complete 1265-day downloads.
2. Pass OHLC parity and monthly gates.
3. Train W90/W400/W1265.
4. Run budget experiments.
5. Accumulate same-ID forward shadow.

## Phase 4

1. Accumulate Complete Trade Forecast data.
2. Freeze M0 score.
3. Run Q1-Q5 ranking.
4. Test latency, HOLD, and NO_TRADE.
5. Close if M0 fails.

## Phase 5

1. Sequence-valid L2.
2. Cost quantiles.
3. Quote survival.
4. Liquidity deterioration.
5. Capacity.
6. Full-size profit lockability.

## Phase 6

1. Deribit fair value.
2. Spot/perp residual.
3. Binance/Bybit residual.
4. 5m/15m consistency.
5. Binance hedge.

## Phase 7

1. Exposure grouping.
2. Timeframe allocator.
3. Venue allocator.
4. Daily loss and drawdown controls.
5. Emergency halt.

---

# 34. Do not build again

```text
another raw direction model
more technical indicators
another model vote
Transformer/Mamba/RL on the same labels
static 15m TP/SL
dynamic-exit ML without new information
late-leader residual ML
maker late leader
generic L2 direction
post-hoc threshold carving
automatic Champion replacement
win-rate optimization
```

---

# 35. Final profitability thesis

The app is unlikely to reach high profit by predicting every BTC direction more confidently.

The credible path is:

```text
1. Collect new causal execution data.
2. Detect whether enough movement exists.
3. Estimate both long and short economic outcomes.
4. Predict cost, fillability, and capacity.
5. Predict path and tail risk.
6. Choose one venue/horizon/side/plan.
7. Require positive conservative net EV.
8. Abstain aggressively.
9. Promote only after forward evidence.
```

The likely profitable product is:

```text
a sparse opportunity selector
```

not:

```text
a constantly trading predictor
```

---

# 36. Coding-agent instructions

```text
Read this document and active/closed experiment documents before changing code.

Do not:
- enable real orders,
- edit frozen preregistrations,
- remove temporal holdouts,
- restamp REST data,
- reopen closed families without new information,
- allow disabled heads to affect decisions,
- auto-promote retrained models,
- optimize thresholds on final test,
- claim profit from pilot evidence.

Every change requires:
- tests,
- hashes,
- manifests,
- fail-closed behavior,
- migration compatibility,
- explicit acceptance criteria,
- documentation.

Priority:
1. DecisionEnvelope.
2. Head-health enforcement.
3. Calibrated P(Hold shadow.
4. Oracle evidence deployment.
5. 1265-day matrix completion.
6. Complete Trade Forecast M0.
7. L2 execution models.
8. Shadow Gate Laboratory.
9. Independent mechanisms.
10. Portfolio allocator and risk governor.
```

---

# 37. Canonical status block

```text
LATEST COMMIT REVIEWED
    b7306cb3c46b802e4f0d51669f9f436837ac5037

MODE
    SHADOW / PAPER
    REAL ORDERS DISABLED

DIRECTION
    diagnostic / confirmation
    near ceiling

P(HOLD
    raw miscalibrated
    challenger wins
    not applied

CHAMPION
    PAPER_BET off
    Kelly off

COMPLETE TRADE
    implemented
    one-week pilot
    NO_TRADE

LONG WINDOW
    infrastructure implemented
    verified matrix currently 360 days
    full 1265-day run incomplete

BINANCE V1
    frozen
    M0 blocked

COLLECTOR
    fixes implemented
    9/9 required
    deployment next

NEXT
    unify decisions
    enforce head health
    shadow calibrated probability
    deploy collectors
    complete long data
    accumulate forward evidence
    test complete-trade ranking
    improve execution data
```

---

# 38. Maintenance

Append every experiment with:

```text
date
experiment ID
hypothesis
preregistration hash
data window
code hash
feature hash
policy hash
sample size
calendar duration
result
promotion or closure
canonical implication
```

Never rewrite historical failures. They protect the project from repeatedly manufacturing false discoveries.
