# BTC Prediction Tool — Institutional Quant Audit, Real-Trading Blueprint, and RL Architecture

**Audit date:** 2026-07-28  
**Repository:** `1rahulvijay/BTC-Prediction-Tool`  
**Audited remote head:** `cca499f66ae4940f397e485a18315284e8baf489`  
**Purpose:** A single execution document for Claude/Codex. It separates verified defects, fixed defects, refuted claims, architecture debt, research ideas, and the exact order required before real money.

> This is an exhaustive **static critical-path audit**, not proof that zero defects remain. Static review cannot prove exchange behavior, real fill quality, database behavior under load, or profitability. The document therefore includes replay, fault-injection, shadow, and live-readiness gates designed to expose unknown defects before capital is at risk.

---

## 1. Executive verdict

The research layer has improved materially, but the runtime that would own money is not yet trustworthy.

Current truth:

- Feature semantics changed to true-duration VWAP and old model artifacts are unprovable.
- The artifact checker can detect stale or unknown models, but authoritative loaders still do not enforce it.
- The P(hold) calibrator is promising but research-only, default-off, and bound only at semantic-version level until exact model manifests exist.
- No event-conditioned strategy has passed promotion.
- The multi-venue archive has historically been empty, so the strongest microstructure, maker-fill, lead-lag, and RL ideas cannot yet be evaluated honestly.
- The legacy Polymarket runtime is a prototype, not an execution-quality system.
- Binance paper execution has duplicated engines and unresolved state-machine/risk defects.
- Real authenticated trading must remain disabled.

The route to profit is not “add more indicators.” It is:

```text
trustworthy data
→ verified model identity
→ exact train/serve parity
→ calibrated conditional probabilities
→ realistic fills and costs
→ selective event-conditioned trading
→ safe state-machine execution
→ shadow evidence
→ tiny controlled live deployment
```

---

## 2. Proposed-error review: fixed, open, or refuted

| Claim or proposed fix | Status | Decision |
|---|---|---|
| VWAP was cumulative and flatlined | **FIXED** | True-duration trailing VWAP; feature semantics v3 |
| WebSocket timestamps should remove `//1000` | **REFUTED** | REST and WS use seconds; removing it creates an orphan candle |
| TCN per-sample weighting was collapsed into class means | **FIXED** | Per-sample weighted unreduced loss |
| TCN moved the whole model to CPU every prediction | **FIXED** | Model stays on configured device |
| `max_taker_ask()` should be linear | **REFUTED / HARMFUL** | Fee is `rate × p × (1-p)`; quadratic inversion is correct |
| Polymarket simulator has fake size, fee, and slippage | **OPEN — LEGITIMATE** | Replace/quarantine; full YES/NO L2 ladder walk required |
| 5m probability should be inserted directly as Black–Scholes drift | **REFUTED AS A FIX** | A probability is not a drift parameter; train exact-target residual/distribution model |
| Heavy feature and inference work is inline on asyncio | **PARTIALLY FIXED** | Feature/inference use executor, but persistence, snapshots, task supervision, and latency budgets remain open |
| Calibrator failed open to raw | **FIXED FOR REQUIRED MODE** | Required mode returns no probability and revokes pricing |
| Calibrator is exactly bound to the source model | **NOT YET** | It is semantic-version bound only; exact artifact hashes require manifests |
| Artifact checker blocks stale models | **NOT YET** | Detection exists; production loader enforcement remains open |
| RL execution already proved cost savings | **REFUTED** | Toy simulator used invented dynamics; latest commit removed synthetic demos |
| Delta-neutral Polymarket guarantees arbitrage | **REFUTED** | Binary delta is dynamic; model error, hedge cost, basis, funding, and leg risk remain |
| Public Binance depth provides true L3 order identities | **REFUTED** | Public level-aggregated depth supports cancellation/replenishment proxies, not exact MBO identity |

---

## 3. Fixed defects that should be protected permanently

### 3.1 True-duration VWAP

**Fixed:** cumulative VWAP and median-delta-derived bar-count windows.  
**Permanent tests:** gaps, irregular bars, seconds/ms parity, duplicates, non-finite timestamps, decreasing timestamps, prefix causality, zero/negative duration.

**Remaining rule:** any further numerical meaning change must bump `FEATURE_SEMANTICS_VERSION` and force a challenger retrain.

### 3.2 Kline timestamp contract

**Fixed:** REST/WS parity and behavioral candle replacement test.

**Permanent invariant:**

```text
historical last candle timestamp T
+ live update for T
= replace T, never append 1000×-unit orphan
```

### 3.3 TCN weighting/device correctness

**Fixed:** per-sample weights reach the gradient; inference remains on `self.device`.

**Permanent invariant:** changes to objective, sample weighting, target encoding, or loss normalization must bump `TRAINING_SEMANTICS_VERSION`.

### 3.4 Calibration failure behavior

**Fixed:**

```text
off       → raw, explicitly uncalibrated
optional  → calibrated when valid; raw only as non-authoritative shadow diagnostic
required  → invalid calibrator returns no probability and revokes pricing/sizing
```

### 3.5 Event-campaign protocol safety

**Fixed or materially improved:** `promotable=False`, canonical WAIT enforcement, day-block horizon lower bound, design-only horizon dataset, relative imports, stream tiers, richer segmentation.

### 3.6 Synthetic-report contamination

Synthetic PPO and “advanced strategy” demonstrations were not evidence. They were removed from the active code path. Keep a hard separation:

```text
docs/research/measured/
docs/research/synthetic/
data/evidence/
data/sandbox/
```

No synthetic result may use words such as “proved,” “edge,” “profitable,” or “promoted.”

---

## 4. P0 stop-ship defects before real money

### P0-1. Artifact identity is detected but not enforced

**Current gap:** checker/verdict logic exists, but direct loaders still deserialize unverified model files.

**Risk:** stale-feature, stale-training, tampered, mixed-bundle, or unknown pickle artifacts can silently generate predictions.

**Required solution:** four isolated commits.

#### Commit A — artifact foundation

Create:

- `backend/model_registry.py`
- `backend/model_artifacts.py`

Implement immutable staging bundles, member hashes, manifest checksum, read-back verification, atomic rename, atomic champion pointer, typed refusal codes, pre-deserialization verification, concurrency and crash tests.

Final status must be `FOUNDATION_ONLY`.

#### Commit B — migrate every authoritative writer

Migrate all measured save paths. No authoritative direct `pickle.dump`, `joblib.dump`, `torch.save`, `save_model`, or ad hoc JSON model write outside the canonical artifact module.

Do **not retrain before this commit**.

#### Commit C — migrate every authoritative loader

Verify identity and bytes before deserialization. Initially keep behind a compatibility flag for integration testing.

#### Commit D — activate verified loading

Default to verified loading and enter `DEGRADED_MODEL_BLOCKED` when artifacts are invalid.

Allowed in degraded mode:

- Recorder operation
- Health and reporting
- Market-data UI
- Reduce-only and emergency position management

Blocked:

- Prediction authority
- Pricing
- Ranking
- Sizing
- New paper/live entries
- Automatic promotion

### P0-2. Ungated model swap path

`relearn_models_background()` contains a path that swaps a newly trained candidate into the active `model` when the special promotion pipeline is not active.

**Risk:** a manually or automatically triggered relearn can become production-active without the full frozen promotion gates.

**Fix:** every model swap must use one promotion state machine:

```text
TRAINED
→ IDENTITY_VERIFIED
→ HOLDOUT_PASSED
→ CALIBRATION_PASSED
→ SHADOW
→ FORWARD_GATE_PASSED
→ OPERATOR_APPROVED
→ ACTIVE
```

No direct `model = candidate` outside the promotion authority. Add an AST/inventory test.

### P0-3. Legacy Polymarket runtime retains authority

Legacy modules remain prototype-grade:

- `polymarket_client.py`
- `polymarket_model.py`
- `polymarket_simulator.py`
- `polymarket_verifier.py`

Defects include naive contract parsing, placeholder residual/calibration logic, synthetic NO pricing, fixed size/fee/slippage, no complete resolution verification, and no execution-capacity model.

**Fix:** mark all four modules `LEGACY_DIAGNOSTIC_ONLY`, remove trading/evidence authority, and create one canonical Polymarket engine.

### P0-4. Two Binance paper engines

Two independent engines use different tables, stores, risk/accounting rules, and overlapping default database paths.

**Risk:** tests can validate one engine while the server runs another; schema collisions and divergent accounting are likely.

**Fix:** one canonical `PaperExecutionEngine`, one schema, one accounting ledger, one risk state machine. The service layer may orchestrate strategies but may not implement independent fill/accounting rules.

### P0-5. Hard-off disables risk management

The current hard gate can return before marking, funding, stop/target, governor exits, or manual close.

**Failure case:** an open position survives restart while entries are disabled and cannot be managed.

**Fix states:**

```text
DISABLED_FLAT
DISABLED_CLOSE_ONLY
PAUSED
RUNNING
EMERGENCY_FLATTEN
```

“Disabled” blocks new exposure, never reduce-only management.

### P0-6. Pending orders are not fully reserved or re-risked

Open risks:

- Multiple pending entries can consume the same cash/exposure.
- No complete per-strategy reservation.
- Arrival processing may not recheck cash, exposure, cooldown, active position, trade count, and loss gate transactionally.
- Equal-time entry/exit ordering can be wrong.

**Fix:** reservations, one active entry intent per strategy, exit-before-entry priority, transactionally re-run the entire risk gate at arrival, idempotency keys, and tests for simultaneous signals/account changes.

### P0-7. Synchronous persistence remains inside feed callbacks

Trade and depth handlers perform ordinary synchronous database/parquet calls.

**Risk:** disk or DuckDB stalls delay WebSocket processing; exceptions can terminate feed callbacks.

**Fix:** callback validates, timestamps, and enqueues an immutable event only. Dedicated serialized writers persist it. Add bounded queue, lag, drop, and DB-latency metrics.

### P0-8. Background tasks are not fully supervised

Multiple `asyncio.create_task()` handles are not centrally owned or awaited. Shutdown can close dependencies while tasks still run.

**Fix:** one task supervisor/TaskGroup. Critical worker failure moves runtime to close-only. Shutdown order:

```text
stop intake
→ cancel/await workers
→ flush queues
→ close DB/network resources
```

### P0-9. Mutating APIs and CORS

Binance paper start/pause/close/update endpoints need the same admin dependency as other protected actions. Wildcard CORS with credentials is unsafe.

**Fix:** authenticate every mutation, explicit origin allowlist, CSRF/replay protections where relevant, audit log, and authorization tests.

### P0-10. Database migrations swallow unexpected failures

Broad exception suppression around schema changes can hide lock, corruption, disk, syntax, and type failures as though a column already exists.

**Fix:** transactional migration registry with `schema_version`, migration ID/hash, code commit, applied timestamp, and explicit expected-error handling. Unexpected failure blocks startup.

### P0-11. Startup mixes serving, training, backfill, cleanup, and recording

One launcher performs too many high-impact actions.

**Fix commands:**

```text
serve
record
backfill
train-challenger
validate-challenger
promote-challenger
report
```

Serving must never train, backfill, clean evidence, or promote.

### P0-12. No exchange-truth reconciliation layer

Before live execution, the system needs an append-only order lifecycle and account reconciliation process.

**Required order states:**

```text
INTENT_CREATED
SUBMITTING
ACKNOWLEDGED
PARTIALLY_FILLED
FILLED
CANCEL_REQUESTED
CANCELLED
REJECTED
EXPIRED
UNKNOWN_RECONCILIATION_REQUIRED
```

On restart, exchange balances/orders/fills are truth. Local state must reconcile without inventing fills or resubmitting duplicate orders.

---

## 5. P1 correctness and evidence gaps

### P1-1. Polymarket execution economics are fictional

Replace fixed 100-share, 1% fee, and 0.5-cent slippage assumptions with:

- Actual YES and NO token books
- Full ladder walk
- Partial/no fills
- Market-specific fee retrieval
- Tick-size and minimum-order validation
- Latency-adjusted arrival book
- Maker fill standards: touch, trade-through, queue-estimated
- Adverse-selection and missed-fill cost
- Entry and exit/settlement PnL

Touch-only maker results must be non-promotable.

### P1-2. Polymarket contract identity and settlement parsing

Do not derive strike, side, or settlement rules only from question text.

Create a frozen `ContractSpec`:

```text
condition_id
market_id
yes_token_id
no_token_id
resolution_source
comparison operator
reference symbol
strike/reference value
start/end timestamps and timezone
settlement rule/version
fee rate/tick size/min size
```

Unknown or ambiguous contract → `NO_DATA`, never 0.5 fair value.

### P1-3. Polymarket fair-value model is a placeholder

Current baseline is a simple lognormal terminal probability with zero drift; residual is inactive; “calibration” may be clipping.

Correct research candidates:

1. Exact-target logit residual on the baseline
2. Conditional return-distribution model
3. First-passage/barrier hazard model
4. Options-implied distribution plus BTC microstructure residual

A 5-minute UP probability is neither an additive long-horizon probability nor a drift parameter.

### P1-4. Feed sequence/gap/session integrity

Canonical event envelope:

```text
source
symbol
source_session_id
exchange_timestamp
receive_timestamp
sequence/update_id
schema_version
gap_status
payload_hash
```

Segment on reconnect, sequence regression, clock regression, schema change, and large gap. Clear or warm feature state after invalid continuity.

### P1-5. Cross-venue prices are not independently fresh

Current logic can infer Coinbase from Binance plus a stored premium and combine venues without per-source age/skew checks.

Fix each field to carry:

```text
value
source
exchange_ts
receive_ts
age_ms
valid
reason
```

Never include inferred or stale values in “independent venue consensus.”

### P1-6. Misnamed oracle source

A CoinGecko-derived price must not be labelled Chainlink or treated as an official settlement oracle.

Rename every field by actual source. Maintain distinct:

- Exchange executable price
- Index price
- Mark price
- Public aggregator price
- Contract resolution oracle

### P1-7. Missing values are converted to real zeros

Examples include meta-context extraction and external data fallbacks.

**Risk:** missing/stale/vector-short becomes indistinguishable from a genuine zero.

**Fix:** nullable values plus explicit masks and age. Required missing input → `NO_DATA`; optional missing must have train/serve parity.

### P1-8. Cross-horizon prediction snapshot is incoherent

Feature build and per-horizon predictions are sequential worker calls while global state continues changing.

**Risk:** 5m and 15m outputs can describe different market instants but appear as one decision batch.

**Fix:** immutable `DecisionSnapshot` containing event time, all feature inputs, source ages, model bundle ID, and a decision deadline. Every horizon consumes the same snapshot. Discard output if the deadline is exceeded.

### P1-9. Default executor contention and latency budget

Feature builds and per-horizon model calls use the default executor. Training, backtests, inference, persistence, and other jobs can contend unpredictably.

**Fix:** dedicated bounded executors/processes per workload and measured service-level objectives:

```text
feed callback p99
snapshot age
feature latency
inference latency
decision age
order-submit latency
```

Do not choose a Rust rewrite before these measurements.

### P1-10. Event-target semantics mismatch

A target described as “worsens within five seconds” must not be labelled only from the endpoint at exactly +5 seconds.

Freeze one versioned contract:

- Endpoint at 5s, or
- First passage within 5s

Never mix labels across versions.

### P1-11. Forecast/evidence ledger integrity

Add deterministic semantic keys, adapter/source run IDs, source cutoffs, outcome-after-forecast validation, orphan checks, duplicate-panel checks, provenance singleton checks, and a hash-chained/rooted ledger.

### P1-12. Adapter hashes are shape-checked, not recomputed

A 64-character string is not proof. Recompute protocol, feature schema, dataset, code, and artifact hashes from frozen files/bundles.

### P1-13. Probability head and censoring defects

- Class-weighted classifier probabilities require chronological recalibration.
- One-class folds must not emit confident 0/1 without uncertainty.
- Missing slippage/fill targets must not be median-fabricated.
- Time-to-profit must model right censoring, not train only on profitable finite cases.
- Quantile monotonicity does not prove coverage.

### P1-14. Incomplete cost attribution

Persist independently:

```text
entry spread
entry slippage
entry fee
exit spread
exit slippage
exit fee
funding
borrow/basis
market impact
unfilled opportunity cost
total cost
```

### P1-15. Funding recovery can miss settlements

Persist a funding-event cursor and fetch every missed event chronologically after downtime. A single latest-event query is insufficient.

### P1-16. Risk model lacks exchange-realistic liquidation mechanics

Before live futures:

- Isolated vs cross margin
- Mark price vs last price
- Maintenance-margin tiers
- Leverage brackets
- Funding
- Liquidation fee
- Reduce-only enforcement
- ADL/insurance uncertainty
- Exchange precision and minimum notional

### P1-17. Model/decision permissions are incomplete

Use one mandatory object:

```text
may_generate
may_display_confidence
may_price
may_rank
may_size
may_support_promotion
```

Calibration success may allow honest confidence display; it does not prove ranking skill or economic sizing authority.

### P1-18. Non-promotion research can still influence UI or candidate order

Every research-only head/result needs typed authority and consumers must reject unauthorized ranking, pricing, sizing, or promotion.

---

## 6. P2 governance, testing, and operational gaps

### 6.1 Direct-to-master workflow

Use one short-lived hardening branch and required CI for high-risk runtime work. Direct master can remain for documentation, but execution/risk changes need reviewable diffs and rollback.

### 6.2 CI is deterministic but does not prove public-feed compatibility

Add scheduled non-promoting smoke jobs for:

- Exchange metadata/schema
- Public WebSocket connect/parse
- Fee/tick-size endpoints
- Recorder write health
- Artifact report generation

No model promotion or order submission in CI.

### 6.3 Runtime state and static documents

Generated runtime state is the correct approach. Extend it with:

- Current queue lag
- Source freshness
- DB write latency
- Model bundle identities
- Decision age
- Open positions and close-only state
- Migration version

### 6.4 No formal incident/runbook layer

Create runbooks for:

- Feed outage
- Clock drift
- DB lock/corruption
- Artifact refusal
- Model crash
- Exchange partial outage
- Unreconciled order
- Position exists after restart
- Kill switch

### 6.5 Secrets and environment management

Before authenticated APIs:

- Secret manager or OS credential store
- No secrets in `.env` committed files or logs
- Key scopes: read-only first, then trading only, no withdrawal
- IP allowlist where supported
- Key rotation and revocation drills

---

## 7. Architecture target for real trading

Do not jump directly to distributed microservices. Start with isolated local processes and explicit contracts.

```text
┌──────────────────────┐
│ Feed Gateway Process │
│ parse/validate/stamp │
└──────────┬───────────┘
           │ canonical immutable events
           ▼
┌──────────────────────┐      ┌──────────────────────┐
│ Recorder/Persistence │      │ Feature State Process│
│ append-only evidence │      │ causal state/windows │
└──────────────────────┘      └──────────┬───────────┘
                                        │ DecisionSnapshot
                                        ▼
                              ┌──────────────────────┐
                              │ Inference Process    │
                              │ verified models only │
                              └──────────┬───────────┘
                                        │ forecast + permissions
                                        ▼
                              ┌──────────────────────┐
                              │ Decision/Portfolio   │
                              │ EV, risk, allocation │
                              └──────────┬───────────┘
                                        │ typed order intent
                                        ▼
                              ┌──────────────────────┐
                              │ Execution/Risk Proc. │
                              │ sole order authority │
                              └──────────┬───────────┘
                                        │ exchange truth/reconciliation
                                        ▼
                              ┌──────────────────────┐
                              │ API/UI               │
                              │ read-mostly control  │
                              └──────────────────────┘
```

Principles:

- Execution/risk is the only process allowed to submit or cancel.
- UI never owns trading state.
- Inference failure cannot prevent closing a position.
- Every decision is reproducible from a snapshot ID.
- Bounded queues fail closed on excessive lag.
- Append-only ledgers, atomic snapshots, and idempotent commands.

---

## 8. Accuracy and prediction blueprint

### 8.1 Stop optimizing raw direction alone

Use a decomposed economic target:

```text
P(move exceeds total cost)
× P(direction correct | move exceeds cost)
× P(fill | action, queue, latency)
× expected payoff
− fees
− slippage
− adverse selection
− funding
− uncertainty reserve
```

Recommended heads:

1. Movement-above-cost
2. Direction conditional on movement
3. Magnitude/quantiles conditional on movement
4. Maker fill probability
5. Post-fill adverse selection
6. Time-to-profit survival
7. ACT/SKIP meta-head
8. OOD/uncertainty head

### 8.2 Selective prediction

Accuracy should be measured as a curve, not one number:

```text
coverage vs precision
coverage vs net expectancy
coverage vs drawdown
```

WAIT is a first-class action. Optimize the lower confidence bound of net expectancy at the selected coverage.

### 8.3 Calibration and uncertainty

After exact model manifests and retraining:

- Four chronological stages: base train, calibrator fit, calibrator selection, untouched evaluation
- Compare Platt, isotonic, beta calibration, and prior shrinkage
- Report tail ECE, slope/intercept, Brier, log-loss, and block intervals
- Per side, horizon, regime, distance, time remaining, and liquidity
- Conformal or distribution-free intervals for movement/magnitude where suitable

### 8.4 OOF stacking and regime affinity

Formal OOF stacking is worth testing only with chronological/purged folds. The meta-model consumes base OOF probabilities plus frozen regime features. It must beat:

- Best single model
- Simple average
- Calibrated linear blend

on untouched Brier/log-loss and net economic utility.

### 8.5 Train/serve parity

For every feature:

```text
name
formula version
source
cadence
window semantics
missing-value rule
freshness limit
unit
normalization
```

Hash the full contract, not merely column names.

### 8.6 OOD and drift

Monitor:

- Feature PSI/Wasserstein distance
- Probability distribution shift
- Calibration slope/ECE drift
- Regime occupancy
- Model disagreement
- Missingness/freshness drift
- Execution-cost drift

OOD can revoke pricing/sizing even if model files are valid.

### 8.7 Multiple-testing control

For every research campaign:

- Preregister hypotheses and trial count
- Nested purged walk-forward for tuning
- Day/week block bootstrap
- Deflated Sharpe and probability of backtest overfitting
- Family-wise or false-discovery control
- One untouched opening
- No same-period retuning after failure

---

## 9. Profit and portfolio blueprint

### 9.1 Optimize net utility, not hit rate

Primary research metric:

```text
net PnL after all costs
+ lower-bound expectancy
+ drawdown
+ tail loss
+ turnover/capacity
```

Accuracy can increase while profit decreases. Every model report must show both.

### 9.2 Position sizing

Do not grant Kelly authority from calibration alone.

Sizing ladder:

```text
fixed tiny probe
→ volatility-scaled probe
→ conservative fractional Kelly using lower-bound probability
→ portfolio-level allocation
```

Requirements:

- Probability uncertainty reserve
- Cost uncertainty reserve
- Correlation between simultaneous bets
- Per-strategy and global exposure caps
- Daily/weekly loss limits
- Drawdown state machine
- No forced “heartbeat” trade floor

### 9.3 Capacity

For Binance and Polymarket, report expected performance by size bucket. Profit that disappears at realistic size is not promotable.

### 9.4 Portfolio allocator

Allocate across strategies using conservative expected returns and covariance, with hard constraints. Avoid treating correlated BTC contracts/horizons as independent bets.

---

## 10. Strategy backlog — Binance

### Tier 1: highest priority after qualifying data exists

1. **Executable cross-venue lead-lag**  
   Predict remaining Binance move after Coinbase/Bybit/perp signal, using receive timestamps and post-latency executable quotes.

2. **Perp–spot OFI divergence**  
   Taker-flow acceleration, microprice, and price impact on perp minus spot; test incremental value after each leg alone.

3. **Liquidation continuation specialist**  
   Predict cascade continuation when depth withdraws and aggressive flow persists.

4. **Liquidation exhaustion/reversion specialist**  
   Separate target and protocol; require replenishment/absorption evidence. Never assume all cascades revert.

5. **Funding/OI/basis unwind**  
   Extreme leverage plus falling price impact/replenishment; longer event horizon.

6. **Cross-venue liquidity withdrawal**  
   Synchronized bid-depth removal and weak replenishment as downside-hazard features.

7. **Absorption and replenishment**  
   Large aggressive volume with limited price response; distinguish genuine absorption from stale walls.

8. **Queue-aware maker conversion**  
   Estimate fill, adverse selection, and missed-fill cost under trade-through and queue-estimated standards.

9. **Hawkes/metaorder persistence**  
   Compare Hawkes to simple EWMA/event-count baselines; promote only incremental after-cost value.

10. **Cross-asset residual leadership**  
    ETH/SOL moves after removing contemporaneous BTC beta; avoid raw correlation.

11. **Volatility breakout after compression**  
    Predict movement-above-cost first, then direction; include depth and options regime.

12. **Time-of-day/session effects**  
    Asia/Europe/US open, funding windows, options expiry, and macro-event exclusions.

### Tier 2: later research

- Deribit ATM IV, term structure, 25-delta risk reversal, butterfly, IV–realized spread
- Change-point and Bayesian online regime detection
- Self-supervised order-book embeddings, tested for economic separation
- Stablecoin issuance/exchange flows for 4h–daily horizons
- Macro liquidity/rates for daily–weekly risk regime
- Cash-and-carry/basis opportunities with complete funding/borrow/capital accounting
- Volatility-risk-premium strategies rather than pure direction

---

## 11. Strategy backlog — Polymarket

### Tier 1

1. **Exact terminal/barrier probability engine**  
   Match the exact contract settlement rule and remaining time.

2. **Cross-strike monotonicity arbitrage**  
   Probabilities must be monotonic across strikes; trade only after full fees, depth, and resolution identity.

3. **YES/NO complement dislocation**  
   Detect executable combinations where YES+NO asks or bids violate no-arbitrage after fees and size.

4. **Options-implied distribution comparison**  
   Compare Polymarket prices with a Deribit-implied terminal distribution, then model residuals.

5. **Polymarket order-flow toxicity**  
   Maker fill followed by adverse price movement; inventory-aware quoting.

6. **Time-decay/stale-quote strategy**  
   Fast fair-value change versus resting quotes, with actual queue/latency and cancellation risk.

7. **Multi-market consistency**  
   Same event across expiries, strikes, and related contracts; enforce logical constraints.

8. **Dynamic hedge research**  
   Offline compare unhedged, static, periodic delta, and threshold delta strategies including all costs and settlement basis risk.

### Tier 2

- Public aggregated wallet-flow features only, without identity claims or blind copying
- Resolution-oracle divergence monitoring
- Market-maker inventory optimization
- Portfolio optimization across correlated contracts

---

## 12. RL execution architecture — correct version

### 12.1 What RL is allowed to do

Initially RL may optimize **execution only**, never decide whether alpha exists.

Alpha system outputs a frozen intent:

```text
side
quantity cap
limit/urgency
latest completion time
maximum all-in cost
```

RL chooses how to execute within that envelope.

### 12.2 Why toy PPO/Q-learning is invalid

An agent trained on invented fill probabilities, queue advancement, rebates, or penalties learns the simulator author. It does not learn the exchange.

No RL work begins until a real replayable L2 archive and credible fill model exist.

### 12.3 State

Binance state candidates:

- Time remaining and inventory remaining
- Spread, microprice, top-N imbalance
- Queue-ahead estimate
- Recent trades at price
- Cancel/replenishment intensity
- Short-horizon volatility and impact
- Cross-venue lead/lag
- Latency and feed health
- Current order state

Polymarket adds:

- Fair value and uncertainty
- Time to settlement
- Contract delta/gamma proxies
- YES and NO books
- Inventory by contract
- Market-specific fee/tick size

### 12.4 Actions

```text
WAIT
PLACE_POST_ONLY(level, size)
KEEP
CANCEL
REPRICE(level)
CROSS_PARTIAL(size)
CROSS_REMAINDER
ABORT
```

Actions are masked by risk, exchange rules, time, and maximum cost.

### 12.5 Reward

Execution reward must benchmark against an arrival decision price and include:

```text
implementation shortfall
fees
spread/impact
adverse selection after fill
missed-fill opportunity cost
inventory/time penalty
rule violation penalty
```

Profit from subsequent alpha movement belongs to the alpha model, not the execution learner, or the policy will confound execution with direction.

### 12.6 Training order

1. Deterministic baselines: immediate taker, passive then cross, TWAP, urgency schedule
2. Supervised imitation of best replay action where observable
3. Contextual bandit
4. Conservative offline RL: CQL/IQL or another no-exploration method
5. Model-based simulator only after calibrated fill/impact validation
6. PPO only after the environment passes sim-to-real tests

### 12.7 Off-policy evaluation

Require multiple estimators:

- IPS/SNIPS where propensities exist
- Doubly robust estimator
- Fitted Q evaluation
- Block bootstrap by day/episode
- Replay against deterministic baselines

No promotion from simulator return alone.

### 12.8 Safety shield

A deterministic shield overrides RL:

- Max order size and exposure
- Price collar
- No action on stale/gapped feed
- Reduce-only when unhealthy
- Forced completion/abort rules
- Rate-limit protection
- No online exploration with real money

### 12.9 RL promotion gates

```text
real-data archive qualifies
fill model calibrated
OPE beats all baselines with positive block lower bound
stress scenarios pass
shadow orders show no reconciliation errors
paper execution shows positive after-cost improvement
operator approval
tiny live size
```

---

## 13. Step-by-step execution roadmap

### Phase 0 — freeze and protect

- Record current commit and protocol hashes
- Keep live orders disabled
- Keep calibration off
- Do not retrain until writers produce manifests
- Start/verify the recorder independently

### Phase 1 — artifact foundation

Canonical registry/bundles, atomicity, verification, fault tests.

### Phase 2 — writer migration

Migrate all authoritative save paths; bypass count zero.

### Phase 3 — loader migration and activation

Migrate all loaders; activate degraded-model-blocked behavior.

### Phase 4 — runtime safety

Unify Binance paper engine, close-only states, reservations, arrival re-risk, task supervision, migrations, API auth, queue-based persistence.

### Phase 5 — canonical market data

Event envelopes, source sessions, sequence/gap logic, clock health, per-field freshness, immutable snapshots, replay/live parity.

### Phase 6 — canonical Polymarket engine

Contract registry, exact settlement rules, YES/NO L2, fee metadata, execution simulator, resolution verifier.

### Phase 7 — retrain verified challengers

Feature v3/training v2 manifests, no automatic promotion, complete model cards.

### Phase 8 — refit and validate calibration

Four-stage chronological calibration, tail/regime reports, exact source-model binding.

### Phase 9 — recorder readiness and campaign data

Minimum 60–90 days, 45 valid days, four continuous qualifying weeks, all-stream overlap, cadence/gap/reconnect report.

### Phase 10 — Tier-1 research campaigns

Separate protocols and untouched tests for each mechanism. No combined “kitchen-sink” campaign.

### Phase 11 — economic decision policy

Calibrated conservative EV, WAIT, portfolio constraints, complete costs.

### Phase 12 — shadow and fault drills

Shadow forecasts/orders, restart/reconciliation, DB lock, network partition, stale feed, exchange rejection, duplicate command.

### Phase 13 — RL execution research

Offline only, after credible fill data and deterministic baselines.

### Phase 14 — controlled live readiness

Read-only account integration → testnet/order validation → shadow intents → tiny reduce-only-capable live deployment → gradual scaling.

---

## 14. Real-trading go/no-go checklist

Real money remains **NO-GO** until all are true:

- Verified artifact loading active; zero bypasses
- Current models retrained and manifested
- Calibration fitted to exact current models and untouched-tested
- One strategy has positive after-cost untouched and forward evidence
- Minimum data duration and valid-day requirements met
- Canonical execution engine and account reconciliation pass
- Close-only/emergency exits work without inference
- Feed gaps, stale data, and clock drift fail closed
- All mutating APIs authenticated
- DB migrations and writers fail visibly
- Restart with open positions tested
- Partial fill, rejection, timeout, and duplicate command tested
- Daily/weekly loss and exposure limits tested
- No unresolved ledger mismatch
- Operator kill switch tested
- Tiny-size deployment plan approved

Profit cannot be guaranteed. The only defensible requirement is that the promoted system has positive conservative expected value after costs, survives stress tests, and can fail safely.

---

## 15. Claude execution instructions

### Immediate prompt: artifact foundation only

```text
Work on current master. Implement ARTIFACT_FOUNDATION_V1 only.

Do not retrain. Do not activate calibration. Do not change thresholds. Do not
promote models. Do not enable paper/live entries. Do not modify frozen campaigns.

Create one canonical model registry and one canonical model-artifact module.
Implement immutable staging bundles, fsync, member hashing, manifest checksum,
read-back verification, atomic publication, atomic champion pointers, typed
refusals, pre-deserialization verification, and crash/concurrency tests.

Do not migrate production save/load paths in this commit. Final status must be
FOUNDATION_ONLY, not enforcement complete.
```

### Following prompt: writer migration

```text
Migrate every authoritative model save path to the canonical artifact writer.
No placeholder provenance. Add an inventory test proving zero unauthorized direct
saves. Do not retrain in this commit.
```

### Following prompt: loader migration

```text
Migrate every authoritative production load path to verification before
deserialization. Add zero-bypass scan. Keep activation behind a temporary flag.
```

### Following prompt: activate and degrade safely

```text
Enable verified loading by default. Old artifacts must be blocked. Recording,
health, reporting, and reduce-only management remain available. Predictions,
pricing, ranking, sizing, promotion, and new entries remain blocked.
```

---

## 16. Audit completeness statement

The repeated static passes covered:

- Model saving/loading and identity
- Feature and timestamp semantics
- Calibration and permissions
- Binance paper execution/risk
- Polymarket client/model/simulator/verifier
- WebSocket callbacks and async tasks
- Persistence and migrations
- Startup behavior
- API security
- Market-data freshness/provenance
- Research labels, ledgers, adapters, and validation
- Costs, funding, slippage, and capacity
- Model accuracy, calibration, uncertainty, and multiple testing
- Strategy backlog for Binance and Polymarket
- Safe offline RL execution architecture
- Real-trading deployment and incident safety

No further **category-level** stop-ship gap was identified after these passes. Unknown implementation bugs can still exist inside each category; the fault-injection, replay, shadow, and reconciliation phases are mandatory precisely because static review cannot prove their absence.
