# BTC Prediction Tool — Master Model Context and Engineering Roadmap

**Status date:** 2026-07-26  
**Repository:** `1rahulvijay/BTC-Prediction-Tool`  
**Last repository commit reviewed in chat:** `8998d5bf032ac72cfcfcd7c3945446d71e4c926f`  
**Purpose:** Give a coding agent, research agent, or future model the complete project context, accepted evidence, constraints, open work, and implementation order.

> This is a context and execution document, not a claim that any strategy is profitable. Keep all strategies paper/shadow-only until they pass forward, post-cost, capacity-aware promotion gates.

---

# 1. Executive summary

The project began as a BTC short-horizon prediction app and evolved into a multi-venue research and execution platform covering:

- Binance BTCUSDT spot and perpetual.
- Bybit perpetual.
- Coinbase.
- Polymarket BTC 5-minute and 15-minute UP/DOWN markets.
- Historical and live feature collection.
- Direction, movement, activity, path, P(Hold), shock, regime, and decision heads.
- Executable replay, paper ledgers, calibration, drift, and promotion logic.

The main conclusion is:

```text
Raw 5m/15m BTC direction is near an information ceiling.
Adding more models to the same OHLCV and indicator information
is unlikely to create a reliable material edge.
```

The path to higher profit is:

```text
NEW INFORMATION
+ BETTER ECONOMIC TARGETS
+ EXECUTION-AWARE DECISIONS
+ CAPACITY-AWARE SIZING
+ STRONGER ABSTENTION
+ FORWARD SHADOW EXPERIMENTS
```

Immediate priorities:

1. Make every artifact and data window fully identifiable.
2. Preserve causality and event-time admissibility.
3. Run 90d, 400d, and 1265d experts in shadow.
4. Build a Shadow Gate Laboratory for strict-vs-loose policy comparisons on identical prediction IDs.
5. Predict net opportunity, execution cost, quote survival, liquidity deterioration, regime duration, and tail risk.
6. Use current market and execution state in the action layer.
7. Retrain only through champion/challenger workflows.
8. Keep Oracle as a stable serving/recording node, not the heavy training node.
9. Promote only after positive paired forward post-cost evidence.

---

# 2. Non-negotiable research principles

## 2.1 Profit is not accuracy

Primary objective:

```text
maximize:
    post-cost expected profit
    × executable capacity

subject to:
    positive lower confidence bound
    controlled drawdown
    controlled tail risk
    stable weekly performance
```

Do not optimize only:

```text
accuracy
win rate
AUC
confidence
```

A strategy can win 80–90% of trades and still lose because of rare losses, fees, spread, latency, or insufficient capacity.

## 2.2 Every action must be economic

Polymarket:

```text
net_edge_lower =
    probability_lower_bound
    - executable_entry_ask_or_vwap
    - entry_fee
    - latency_cost_q80
    - safety_buffer
```

Binance:

```text
net_edge_lower =
    predicted_return_q20
    - entry_cost_q80
    - exit_cost_q80
    - funding
    - safety_buffer
```

If conservative net edge is not above the frozen required profit, return `NO_TRADE`.

## 2.3 Most windows should produce NO_TRADE

A profitable strategy may act on only 1–5% of evaluated windows. The desired subset is where:

```text
movement opportunity is large
+ side evidence agrees
+ regime will persist
+ execution cost is low
+ liquidity is likely to survive
+ tail risk is acceptable
+ capacity is available
```

## 2.4 Causal information uses receive time

```text
A feature becomes available at recv_ts, not exch_ts.
```

Never inject REST-polled events at their exchange timestamp during replay if the live strategy learned them later.

## 2.5 No silent mutation

A change to thresholds, features, labels, age limits, policies, costs, or promotion rules creates a new version and hash. Never silently overwrite frozen evidence.

## 2.6 Retraining does not equal promotion

```text
CHAMPION
→ CHALLENGER
→ SHADOW
→ CANDIDATE
→ PROMOTED
```

A newly trained model receives the same future snapshot IDs as the champion and cannot control actions until it passes paired forward gates.

---

# 3. Canonical strategy status

```text
Trading mode: PAPER / SHADOW
Real orders: DISABLED

Raw direction:
    diagnostic only

Polymarket late leader:
    failed promotion / observe only

Polymarket 15m static TP/SL:
    CLOSED

Polymarket maker:
    CLOSED

Conditional stopping / dynamic exit:
    CLOSED

Binance volatility momentum V1:
    preregistered
    blocked by forward-data requirement
    M0 not runnable yet

Cross-venue relative value:
    open research lane
```

No real-money strategy is currently proven.

---

# 4. Historical Polymarket evidence

## 4.1 Late-leader rule

Historical rule:

```text
LATE_LEADER_30S_V1
```

Concept:

```text
Market: Polymarket BTC 5m
Entry: current leader near 30 seconds remaining
Requirements: executable ask, ask >= 0.60, ask below conservative fair value
Frequency: one entry per round
Exit: hold to settlement
Mode: paper only
```

Forward evidence did not support promotion.

## 4.2 Live 21-day evidence

Approximate paper ledger:

```text
settled trades: about 2,145
win rate: about 84.8%
EV: about +0.90 cents/share under one ledger method
PF: about 1.08
day-block lower bound: negative
weeks: 2 of 4 negative
```

Independent executable replay approximately:

```text
qty 1:   -0.07 cents/share
qty 5:   -0.09 cents/share
qty 25:  -0.26 cents/share
qty 100: -1.03 cents/share
```

Reconciliation indicated strong latency fragility:

```text
about 0.6–0.8 cents of edge lost per extra second
```

## 4.3 Static 15m entries

```text
2,880 cells tested
zero positive cells
all weeks negative on both sides
```

Status: `CLOSED`.

## 4.4 Maker strategy

Approximate result:

```text
EV around -9.53 cents/share
PF around 0.57
```

Status: `CLOSED` because of adverse selection.

## 4.5 Dynamic stopping

A profitable exit often existed in hindsight, but no causal state captured it.

Canonical statement:

```text
A temporary profitable exit frequently exists in hindsight.
No causally observable stopping state has shown that taking it
improves PnL versus holding.
```

`CONDITIONAL_STOPPING_V1` closed on 2026-07-26.

SHA-256:

```text
5fcae7b6aa069141d0c44f6c54bfac1d87a4e81e2fdba35b7f76f362dfa1a35c
```

Closure record:

```text
docs/archive/CONDITIONAL_STOPPING_V1_CLOSED_2026-07-26.md
```

Status:

```text
M0 strict gate: FAIL
multiple-test-aware gate: FAIL
models fitted: NONE
protocol integrity: PRESERVED
```

Reopen only for fundamentally new information.

---

# 5. Current model inventory

## 5.1 Direction ensemble

- XGBoost.
- LightGBM.
- CatBoost.
- Random Forest.
- HistGradientBoosting.
- Logistic Regression.
- TCN / sequence model.
- OOF stacker.

Reviewed served horizons:

```text
5m
15m
```

Other horizons were pruned because they lacked a relevant market or remained close to coin flip.

## 5.2 Specialist heads

- P(Hold) / persistence.
- Big move.
- Big drop.
- Directional keeper.
- Activity.
- Signed quantiles.
- Path forecaster.
- Touch probability.
- Round-trip probability.
- Asymmetric path.
- Round-state heads.
- Selectivity.
- Champion meta.
- Move size.
- Conformal bands.
- Shock / flip risk.
- Regime classifier.

The app is already model-rich. The missing value is more likely in targets, data, execution, policy evaluation, and capacity than another generic direction model.

---

# 6. Training-window context

Reviewed repository defaults:

```text
BTC_HISTORICAL_DAYS=1265
BTC_BACKFILL_DAYS=1265
BTC_TRAIN_SPLIT_FRAC=0.98
BTC_FULL_REFIT_AFTER_GATE=1
BTC_FREEZE_MODEL=1
```

Source-complete window is documented as starting around:

```text
2023-01-15
```

Do not describe it as a 1,500-day or 2022-bear model.

## 6.1 Correct meaning of “use all data”

Do not fit and evaluate on the same rows.

Correct flow:

```text
1. Train a candidate on the earlier span.
2. Evaluate on an untouched future tail.
3. Preserve candidate metrics.
4. If candidate passes, refit a production challenger on nearly/all valid data.
5. Validate the full-refit model on new forward shadow outcomes.
```

## 6.2 Current caps

Approximate reviewed caps:

```text
Direction models: 40,000 representative samples
TCN: latest 25,000 rows
Move size: 12,000 rows
Quantiles: 12,000 rows
Linear: 12,000 rows
Stacker: 6,000 rows
```

Thus:

```text
Loading 1265 days does not mean every model consumes every row.
```

## 6.3 Multi-window expert design

Train and shadow:

```text
W90:
    recent expert

W400:
    medium expert

W1265_RECENCY:
    long-history expert with recency weighting

W1265_SIMILARITY:
    long-history expert with recency × regime-similarity weighting
```

Same features, labels, hyperparameters, test periods, and cost assumptions.

Log:

```text
p_90
p_400
p_1265_recency
p_1265_similarity
agreement
dispersion
current regime
actual outcome
post-cost result
```

Do not directly replace the 400-day model with the 1265-day model.

---

# 7. Critical code fixes

## 7.1 Artifact identity

Every saved model must contain:

```text
requested_days
actual_start_ts
actual_end_ts
actual_span_days
row_count
training_data_hash
source_manifest_hash
feature_schema_hash
label_schema_hash
code_hash
split timestamps
calibration timestamps
full_refit flag
model family
hyperparameters
artifact hash
```

Retrain or reject load when any identity element differs. Do not rely only on file mtime, version strings, or a completion marker.

## 7.2 True historical OHLC

The reviewed matrix builder used an `open = close` approximation. Replace it with:

```text
open = first trade price in minute
high = maximum trade price
low = minimum trade price
close = last trade price
volume = sum quantity
```

Add parity checks against official Binance klines. Abort training on material mismatch.

## 7.3 Monthly source-quality manifest

For each source and month store:

```text
expected minutes
actual minutes
coverage
maximum gap
duplicate timestamps
NaN percentage
stale/zero percentage
first timestamp
last timestamp
```

Do not allow a high global coverage score to hide a broken month.

## 7.4 Direction sample-budget challengers

Test:

```text
D40K
D100K
D250K
DALL where practical
```

Evaluate on identical rolling future blocks. Do not assume more direction rows improve direction.

## 7.5 TCN sampling

Replace recent-only truncation in a challenger with:

```text
50% recent contiguous rows
25% regime-balanced historical rows
25% rare-event/tail rows
```

Keep TCN shadow-only until it proves economic value.

## 7.6 Stacker evidence

Test:

```text
6K control
25K challenger
50K challenger
```

Use purged temporal OOF predictions only.

## 7.7 Purged rolling cross-fitting

Use expanding-window folds. Persist:

```text
prediction_id
fold_id
train_start
train_end
validation_start
validation_end
model_hash
OOF probability
actual outcome
```

Every eligible historical row can be training data in some folds and validation data in another fold without leakage.

## 7.8 Regime-similarity weighting

Challenger formula:

```text
sample_weight =
    recency_weight
    × regime_similarity_weight
    × data_quality_weight
```

Similarity inputs may include volatility, volatility acceleration, trend efficiency, compression, volume percentile, basis, funding, OI change, spread/depth regime, and cross-venue disagreement.

---

# 8. P(Hold and calibration

Approximate live calibration issue:

```text
predicted P(Hold: 96.1%
realized hold: 89.3%
overconfidence: about 6.7 percentage points
```

Use it as a ranking signal only until recalibrated.

Nightly calibration challenger:

```text
separate 5m and 15m
last 2,000 independent outcomes
minimum 500
logistic/beta primary
isotonic only with adequate sample
no threshold optimization
```

Store raw and calibrated probabilities, calibrator hash, calibration window, Brier, log loss, ECE, and Brier skill.

Head-health states:

```text
USABLE
RANKING_ONLY
RECALIBRATION_REQUIRED
SHADOW
DISABLED_NO_SKILL
INSUFFICIENT_DATA
DRIFTED
```

Rules:

```text
Brier skill <= 0:
    no decision influence

positive skill but poor calibration:
    ranking only

non-monotonic action tiers:
    not confidence labels

insufficient sample:
    shadow only
```

---

# 9. Multi-venue collector and admissibility

## 9.1 Streams

Binance spot:

- `bookTicker` WS.
- `aggTrade` WS.

Binance perpetual:

- `bookTicker` WS.
- `aggTrade` REST polling from the affected location.
- `premiumIndex` REST.
- `openInterest` REST.
- Funding where available.

Bybit perpetual:

- `orderbook.1`.
- `publicTrade`.

Coinbase:

- `ticker`.

## 9.2 Required event fields

```text
exch_ts
recv_ts
seq
source_mode
poll_id
event_key
timestamp_basis
process_start_id
connection_id
queue_delay_ms
processing_delay_ms
```

## 9.3 Feature classes

Class A:

```text
live WebSocket event-time state
```

Class B:

```text
delayed REST observable state
usable as slow aggregate state
not usable for sub-second leadership
```

Class C:

```text
REST events replayed as available at exch_ts
prohibited
```

## 9.4 Class-B age

Frozen clarification:

```text
CLASS_B_MAX_AGE_S = 60.0
```

Record:

```text
docs/active/PREREG_BINANCE_V1_CLARIFICATION_001.md
```

Partial SHA reported in chat:

```text
12bf5e1e5829d320…
```

Use full value from `PREREG_HASH.txt`.

## 9.5 Timestamp basis

```text
EXCHANGE_TIME
RECEIVE_TIME
RECEIVE_ONLY
POLL_RECEIVE_TIME
```

Examples:

```text
Binance spot bookTicker: RECEIVE_ONLY
REST streams: POLL_RECEIVE_TIME
```

Do not mix incompatible bases in lead/lag. Receive-time order means which event reached this collector first, not necessarily true economic price discovery.

Use names:

```text
observer_time_lead
collector_arrival_lead
```

Clarification:

```text
docs/active/PREREG_BINANCE_V1_CLARIFICATION_002.md
```

Partial SHA:

```text
320631b2a83aaaca…
```

## 9.6 Event identity

Separate:

```text
seq = ordering and gap detection
event_key = stable deduplication identity
```

REST event key:

```text
venue + stream + instrument + publication timestamp + canonical payload hash
```

Interpretation:

```text
same timestamp + same payload hash = exact duplicate
same timestamp + different payload hash = revision/conflict
```

## 9.7 Backlog prohibition

```text
REST_POLL and poll_id <= 1
→ audit only
→ never decision-admissible
```

Eligibility:

```text
poll_id >= 2
first observation of event identity
recv_ts <= decision_ts
age <= 60s
```

## 9.8 Episode accounting

Each five-minute episode stores:

```text
episode_start
episode_end
required stream health
received counts
persisted counts
max_ws_age_ms
max_rest_age_ms
reconnect/gap count
insert errors
qualifying flag
exclusion reason
```

Qualification uses persisted rows, not merely parsed rows.

The boot window is `partial_window` and never qualifies.

Evidence clock begins only after the first successful persistent row. Smoke runs cannot start it.

---

# 10. Binance Volatility Momentum V1

Preregistration:

```text
docs/active/PREREG_BINANCE_VOLATILITY_MOMENTUM_V1.md
```

Partial SHA:

```text
0973744b73651e82…
```

Use full value from `PREREG_HASH.txt`.

## 10.1 Question

```text
At fixed one-minute decisions, can a causal volatility-expansion
and cross-venue-flow state select BTCUSDT perpetual long or short
positions whose 5-minute return is positive after executable spread,
fees, latency, and slippage?
```

## 10.2 Execution

```text
instrument: Binance USD-M BTCUSDT perpetual
decision cadence: once per completed minute
horizon: 5 minutes
one position at a time
entry: taker
exit: taker
primary latency: 1s
sensitivity: 2s
exposure: 1x
```

## 10.3 Frozen M0 values

```text
Q5−Q3 minimum spread: 2.0 bps net
primary cells: 2 sides × 2 latencies = 4
multiplicity: BH q <= 0.10
```

A random five-bucket ordering is monotone about 1.7% of the time, so one monotone cell is not a pass.

## 10.4 Forward gate

```text
>= 4 continuous weeks
>= 1,000 qualifying non-overlapping episodes
complete required stream health
```

Backfill is kill-only. It cannot prove promotion.

If M0 fails:

```text
BINANCE_VOLATILITY_MOMENTUM_V1 CLOSED
MODELS FITTED = NONE
```

If M0 passes, allowed models are Logistic, LightGBM, and CatBoost only.

---

# 11. Shadow Gate Laboratory

Evaluate strict and loose policies on the same prediction snapshots.

Never compare different market periods.

## 11.1 Required tables

### `prediction_snapshots`

One immutable row per venue × horizon × decision checkpoint. Store:

- Snapshot ID.
- Decision time.
- Exposure group ID.
- Venue/symbol/horizon.
- Model, feature, code hashes.
- Raw and calibrated probabilities.
- Every specialist-head output.
- Expected move.
- Expected cost q50/q80/q95.
- Expected MAE and tail loss.
- Bid/ask/spread/depth.
- Data quality and stream health.
- Full feature/output JSON.

Log every opportunity, including rejected opportunities.

### `policy_registry`

Store policy ID, version, hash, parent, status, JSON config, preregistration hash, and timestamp.

### `policy_decisions`

One row per snapshot × policy with action, side, accepted flag, quality score, net EV, required edge, intended quantity, and failed gates.

### `policy_gate_results`

One row per gate with observed value, threshold, comparison, pass/fail, and margin.

### `policy_outcomes`

Store exact counterfactual gross and net return, fees, spread, latency, slippage, funding, MFE, MAE, holding time, capacity PnL, and official outcome source.

## 11.2 Initial policies

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

Prior expectation:

```text
execution strictness: highest chance
risk strictness: second
opportunity strictness: useful for Binance
confidence alone: low chance
all strict: may fire too rarely
```

## 11.3 Ablations and near misses

Evaluate all gates, then remove each gate one at a time. Store added winners, added losers, incremental net PnL, and tail-loss change.

Near-miss cohorts:

```text
far below
just below
just above
far above
```

A useful score should show approximately higher score → better economic outcome.

## 11.4 Coverage-profit frontier

For 50%, 25%, 10%, 5%, 2%, and 1% coverage, report:

```text
trades/day
EV/trade
EV/day
PF
day-block lower bound
drawdown
CVaR
capacity
days to n=500
```

The best policy is the best profit/coverage trade-off, not necessarily the strictest.

---

# 12. Cross-timeframe allocator

Group overlapping predictions with `exposure_group_id`.

Log per horizon:

```text
direction
opportunity
expected cost
tail risk
net EV lower bound
regime persistence
```

States:

```text
ALL_AGREE
SHORT_TERM_ONLY
LONG_TERM_ONLY
CONFLICTED
NO_OPPORTUNITY
```

Choose at most one horizon:

```text
utility =
    net_EV_lower_bound
    × fill_probability
    - tail_risk_penalty
    - overlap_penalty
```

Or abstain.

---

# 13. New prediction heads

Each must be a separate experiment.

## 13.1 Net-opportunity head

Predict:

```text
P(executable move > entry costs + exit costs + buffer + required profit)
```

Targets: 3, 5, and 8 bps net opportunity.

## 13.2 Competing-risk heads

```text
P(long TP before long SL)
P(short TP before short SL)
P(neither before timeout)
```

Use volatility-scaled barriers.

## 13.3 Execution-cost quantiles

Predict q50/q80/q95 by intended size ($100, $500, $1,000). Use q80 or q95 in decisions.

## 13.4 Quote survival

Predict whether the current quote survives 500ms/1s and whether arrival price worsens by 1–2 bps.

## 13.5 Liquidity deterioration

Predict spread widening, depth collapse, one-sided book, and slippage threshold breaches.

## 13.6 Regime duration

Predict trend, chop, or volatility expansion persistence over 1/3/5 minutes and whether regime changes before target.

## 13.7 Tail risk

Predict MAE thresholds, adverse-before-favorable barrier, MAE quantiles, and worst-5% loss.

## 13.8 Cross-venue confirmation

After a valid Class-A impulse, predict confirmation by two other venues, continuation beyond costs, or reversal.

## 13.9 Options-implied fair value

Collect Deribit IV, skew, term structure, and implied move. Compute option-implied probability that BTC finishes above the Polymarket anchor and compare it with executable ask before using ML.

---

# 14. L2 expansion

Build sequence-valid local books for:

```text
Binance spot depth 20
Binance perp depth 20
Bybit perp depth 50
```

Derive microprice, multilevel OFI, depth slope/convexity, replenishment, cancellation imbalance, sweep impact, recovery half-life, queue depletion, and liquidity pull.

Use L2 for execution, fill, slippage, quote survival, liquidity deterioration, and adverse selection. Do not reopen generic L2 direction prediction without a new mechanism.

---

# 15. Market adaptation and retraining

## 15.1 Real-time adaptation without retraining

Continuously update current volatility, volatility acceleration, trend/chop, spread, depth, quote age, slippage estimate, trade intensity, cross-venue agreement, funding/OI, and data quality.

Action layer:

```text
frozen model prediction
+ current market state
+ current execution state
+ calibrated probability
+ tail risk
= action or abstention
```

## 15.2 Suggested update cadence

```text
spread/depth/slippage: every 5–15 minutes
volatility/regime baselines: hourly
rolling percentiles: daily
calibration challenger: nightly
head health: daily
strategy economics: daily and weekly
```

## 15.3 Suggested retraining cadence

```text
calibration: daily
execution/slippage: daily or every 2–3 days, last 7–30d
quote survival/liquidity: every 2–3 days, last 7–30d
P(Hold ranking: weekly, 90–400+d
movement/activity/path: weekly or biweekly, 180–1265d
main ensemble: every 2–4 weeks, challenger only
```

## 15.4 Drift monitoring

Create market, model, execution, and strategy drift modules.

Monitor data distributions, prediction distributions, calibration, and economics.

Persistence rule:

```text
WARNING: one detection
PERSISTENT: remains for 3 daily checks
RETRAIN_REQUIRED: persistent + economic/calibration degradation
FAIL_CLOSED: data or execution integrity failure
```

---

# 16. Decision architecture

Use one common result contract:

```python
@dataclass(frozen=True)
class DecisionEnvelope:
    venue: str
    strategy_id: str
    action: str
    side: str | None
    raw_probability: float | None
    calibrated_probability: float | None
    probability_lower_bound: float | None
    intended_quantity: float
    executable_entry_vwap: float | None
    executable_exit_vwap: float | None
    gross_ev: float | None
    net_ev: float | None
    net_ev_lower_bound: float | None
    expected_cost: float | None
    cost_quantile: float | None
    capacity: float | None
    data_status: str
    head_statuses: dict[str, str]
    reason_codes: tuple[str, ...]
    model_hash: str
    strategy_hash: str
    feature_hash: str
```

Separate `PolymarketDecisionPolicy` and `BinanceDecisionPolicy`.

Action labels:

```text
NO_DATA
NO_OPPORTUNITY
NO_EDGE
NO_CAPACITY
WATCH
SHADOW_CANDIDATE
PAPER_CANDIDATE
INVALIDATED
```

---

# 17. Sizing and capacity

Disable Kelly while probability and edge remain unproven.

Safe defaults:

```text
Polymarket paper quantity: 1 share
Binance: paper/testnet, 1x exposure
Real quantity: 0
```

Calculate:

```text
max_positive_ev_quantity
max_depth_safe_quantity
max_risk_safe_quantity

final_quantity = min(all three)
```

Test Polymarket 1/5/10/25/50/100 shares and Binance $100/$500/$1,000 notionals.

---

# 18. Cross-venue relative value

Run deterministic phase zero before ML:

1. Binance spot vs perp.
2. Binance vs Bybit perp.
3. Polymarket 5m vs 15m executable probability consistency.
4. Binance-hedged Polymarket.
5. Options-implied probability vs Polymarket.

For every audit:

```text
raw discrepancy
- entry fees
- exit fees
- spread
- VWAP slippage
- latency
- funding
- legging risk
= executable residual
```

Close the lane before ML if conservative residual is not positive.

---

# 19. Oracle architecture

Recommended services:

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

Oracle responsibilities:

```text
inference
API/UI
recording
settlement
monitoring
backups
light calibration jobs
```

Do not run multi-day 1265-day heavy training on the serving VM.

Model bundles must include artifact, schemas, manifests, metrics, and hashes.

Log every minute:

```text
backend heartbeat
last event per stream
rows written per stream
DB failures
reconnects
clock drift
event-loop lag
prediction latency
disk
memory
active model hash
active calibrator hash
```

Production must fail to start without an admin token.

---

# 20. Coding Agent PR order

## PR 1 — Artifact and data identity

Mandatory manifests, hashes, active-artifact UI, atomic install, rollback, and mismatch refusal.

## PR 2 — Historical data correctness

True OHLC, monthly coverage, gaps, duplicates, source parity, and training abort on invalid data.

## PR 3 — Collector integrity

Required streams, persisted-row qualification, writer failure state, evidence clock after successful insert, global-first-observation dedupe, revisions, connection/poll separation, continuous qualifying-run measurement.

## PR 4 — CI and governance

Add GitHub Actions, Ruff, pytest, selftests, frozen-hash guards, closed-strategy guards, and frontend build.

## PR 5 — Multi-window harness

Train W90, W400, W1265_RECENCY, W1265_SIMILARITY. Generate purged rolling OOF. Do not replace production.

## PR 6 — Sample-budget challengers

Direction 40K/100K/250K, stacker 6K/25K/50K, TCN recent-only vs regime/tail-balanced.

## PR 7 — P(Hold calibration and head health

Raw vs calibrated shadow comparison and automatic demotion of unskilled heads.

## PR 8 — Decision lockdown

Disable Kelly and unproven paper action, fixed paper size, positive lower-bound EV and capacity requirement, common DecisionEnvelope.

## PR 9 — Shadow Gate Laboratory

Immutable snapshots, pure policy evaluator, policies, gate results, counterfactual outcomes, ablations, rejected outcomes, coverage-profit frontier, timeframe allocator.

## PR 10 — Binance episode builder

Use only admissible events. Produce immutable parquet and manifest.

## PR 11 — Frozen Binance M0

Refuse to run before forward gate. Output only PASS or CLOSED.

## PR 12 — M1–M3 only after M0 pass

Logistic, LightGBM, CatBoost. Economic evaluation only.

## PR 13 — New specialist heads

Net opportunity, execution cost, quote survival, liquidity deterioration, regime duration, tail risk, competing risk, confirmation.

## PR 14 — Cross-venue phase zero

Run deterministic residual audits.

## PR 15 — Oracle deployment

Systemd, timers, health, backups, restart tests, and completion record.

---

# 21. Promotion scorecard

Every strategy must report:

```text
independent episodes
calendar weeks
gross EV
fees
spread
slippage
latency cost
funding
net EV
day-block bootstrap interval
PF
max drawdown
CVaR
positive weeks
capacity by size
matched-control difference
latency sensitivity
forward-vs-replay consistency
```

Suggested promotion contract:

```text
>= 500–1,000 independent executions
>= 8 weeks
post-cost EV > 0
day-block lower bound > 0
PF > 1.20
positive most weeks
matched-control improvement
positive capacity at intended size
latency sensitivity passes
forward paper agrees with replay
no single week/hour dominates profit
```

Accuracy alone cannot promote a strategy.

---

# 22. Do not rebuild exhausted ideas

Do not prioritize:

```text
another raw direction model
more technical indicators
more direction ensemble seats
Transformer/Mamba/RL on the same label
dynamic-exit ML
static 15m TP/SL grids
late-leader residual ML
maker late-leader entry
shock fade/momentum re-runs
generic L2 direction prediction
best-bucket threshold carving
per-tick automatic model replacement
win-rate optimization
```

---

# 23. Local retraining notes

Recommended long-window local configuration:

```bat
set BTC_HISTORICAL_DAYS=1265
set BTC_BACKFILL_DAYS=1265
set BTC_FORCE_FULL_RETRAIN=1
set BTC_FORCE_HEAD_RETRAIN=1
set BTC_FULL_REFIT_AFTER_GATE=1
set BTC_HEAD_REFIT_ALL=1
set BTC_TRAIN_SPLIT_FRAC=0.98
call start.bat
```

First long build may require about 300 GB free. Cached resume should retain at least about 80 GB free.

Train locally or on a separate training VM. Serve frozen models on Oracle.

The UI must show the active artifact’s actual manifest, not only environment variables.

---

# 24. Open research questions

1. Does net-opportunity prediction outperform direction?
2. Does execution strictness improve EV/day or only EV/trade?
3. Can tail-risk gating remove rare losses that destroy PF?
4. Does 1265-day history help path and tail heads more than direction?
5. Which window works best in each current regime?
6. Does options-implied probability add independent Polymarket value?
7. Does cross-venue confirmation improve continuation probability?
8. Can spot/perp or cross-perp residuals survive executable costs?
9. Can a Binance hedge improve Polymarket CVaR without destroying EV?
10. Is the best product an allocator across venue and horizon rather than one strategy?

---

# 25. Final target architecture

```text
1. DATA QUALITY
2. CURRENT MARKET STATE
3. OPPORTUNITY
4. SIDE
5. REGIME DURATION
6. EXECUTION
7. TAIL RISK
8. CAPACITY
9. VENUE AND HORIZON
10. POLICY LAB
11. ACTION
12. MONITORING
13. CHAMPION / CHALLENGER
```

Breakthrough hypothesis:

```text
The ceiling will not break because every BTC direction is predicted more confidently.

It may break by finding rare moments where:
    movement opportunity exists,
    side evidence is coherent,
    regime persists,
    execution is cheap,
    liquidity survives,
    tail risk is controlled,
    and capacity is available.
```

---

# 26. Canonical status block

```text
MODE
    PAPER / SHADOW
    REAL ORDERS DISABLED

POLYMARKET
    late leader: failed promotion
    15m static: closed
    maker: closed
    dynamic stopping: closed
    P(Hold: possible ranking value, fair-value calibration untrusted

BINANCE V1
    preregistration frozen
    M0 blocked by forward sample
    backfill kill-only
    models fitted none

COLLECTOR
    admissibility architecture defined
    operator deployment required
    evidence clock starts only after valid persistent rows

TRAINING
    repository default 1265d
    candidate split 98/2
    full refit after gate
    serving frozen
    multi-window shadow harness required

NEXT BUILDS
    artifact/data identity
    true OHLC and coverage
    collector integrity
    multi-window challengers
    P(Hold recalibration
    Shadow Gate Laboratory
    Binance episode builder
    frozen M0
    execution/opportunity/risk heads
```

---

# 27. Coding Agent instruction block

```text
Read BTC_PREDICTION_TOOL_MASTER_CONTEXT_2026-07-26.md before changing code.

Non-negotiable:
- Do not enable real trading.
- Do not edit frozen preregistrations or hash records.
- Do not reopen closed Polymarket strategy families.
- Do not add another raw direction model first.
- Do not remove temporal holdouts.
- Do not evaluate on training rows.
- Do not restamp REST events at exchange time.
- Do not automatically replace the champion after retraining.
- Do not optimize thresholds on the final test.
- Do not use win rate as the promotion metric.

Implement in order:
1. Artifact/data identity.
2. True OHLC and monthly data-quality manifests.
3. Collector persistence and admissibility integrity.
4. CI and governance.
5. W90/W400/W1265 challengers.
6. Purged rolling OOF harness.
7. P(Hold recalibration and head health.
8. DecisionEnvelope and safe sizing.
9. Shadow Gate Laboratory.
10. Binance episode builder and frozen M0.
11. New execution/opportunity/risk heads as separate experiments.
12. Oracle deployment and monitoring.

Every change must include:
- tests,
- hashes,
- manifests,
- fail-closed behavior,
- migration compatibility,
- no silent fallback,
- explicit acceptance criteria.
```

---

# 28. Maintenance rule

For every major result append:

```text
date
experiment ID
preregistration hash
data window
code hash
result
promotion/closure decision
canonical implication
```

Do not rewrite historical conclusions to make later results look cleaner. This file is the high-level project memory; exact evidence should remain in dedicated immutable documents and database records.

---

# 29. Codex long-window implementation update

Date: 2026-07-26

Implemented after the external long-window review:

```text
true 1m OHLC from aggregate trades
official Binance OHLC parity gate
monthly source/continuity/NaN/zero/stale reports
hash-backed matrix and artifact identity
fail-closed main/head artifact loading
pre-write identity validation
W90/W400/W1265_RECENCY/W1265_SIMILARITY harness
purged rolling OOF
40K/100K/250K/all direction experiments
6K/25K/50K stacker experiments
regime-similarity weighting
50/25/25 TCN sampling
target-specific window registry
same-ID Oracle shadow output
```

Canonical implementation record:

```text
docs/active/LONG_WINDOW_1265D_EXPERT_IMPLEMENTATION_2026-07-26.md
```

The current matrix is still 360 days. A smoke run that inherited a 1,265-day
request was quarantined and the code now aborts on that mismatch. Do not claim
1,265-day evidence until the data build, monthly gates, model fits, holdout
gates, full refit, and forward shadow all complete.

The final stability audit also found that LightGBM's Windows OpenCL GPU path
intermittently terminated Python with `0xC0000005`. LightGBM now defaults to CPU;
XGBoost and CUDA-enabled PyTorch retain their GPU paths. Six repeated server
import/exit cycles then completed cleanly.

---

# 30. Complete Trade Forecaster V1 update

Date: 2026-07-26

Implemented a separate, fail-closed `COMPLETE_TRADE_FORECAST_V1` lane. It:

```text
walks post-latency ASK ladders for entry
walks future BID ladders for exits
models BTC and executable share-price distributions
models slippage, fill probability and capacity
evaluates only frozen causal exit plans
keeps NO_TRADE as a control
logs every checkpoint x side x quantity to a dedicated DuckDB
resolves forecasts against official settlement and immutable realized paths
shows a plain-language pilot card in the Polymarket UI
```

The existing Champion is unchanged. The lane cannot place trades and remains
`PILOT ONLY / NO_TRADE` because the available L2 evidence does not meet 500
independent rounds plus eight calendar weeks and the full M0 stability gate.

Canonical implementation record:

```text
docs/active/COMPLETE_TRADE_FORECAST_V1_IMPLEMENTATION_2026-07-26.md
```

Runner:

```text
research\launchers\run_complete_trade_forecast_research.bat
```

Do not call the complete-trade output profitable until the report shows valid
source/artifact hashes, sufficient evidence, M0 pass, latency survival, weekly
and volatility-regime stability, positive lower-bound EV, and superiority to
hold/no-trade controls.
