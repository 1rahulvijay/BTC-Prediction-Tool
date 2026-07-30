# BTC Prediction Tool — Real-Money Implementation, Validation, Wiring, and Ceiling-Breaking Blueprint

**Audit date:** 2026-07-29  
**Repository:** `1rahulvijay/BTC-Prediction-Tool`  
**Audited branch:** `master`  
**Audited head:** `5682d31e6b6946ae5bb3a9660b5ecb8020cb0c0f`  
**Primary audience:** Claude/Codex implementing isolated changes; owner deciding whether capital authority may advance  
**Scope:** current repository, recent commits, pasted alternate-chat excerpts, V4/V5/V6 proposal text, official Binance/Polymarket interface requirements, and relevant research literature.

> **Critical conclusion:** this app is a sophisticated research and paper-trading platform, not yet a real-money execution system. Real-money order submission must remain disabled until the P0 authority, reconciliation, fill-truth, and forward-evidence gates in this document pass.

> **Shared-chat limitation:** the supplied ChatGPT share page could not be fetched from this environment. The pasted excerpts were analyzed in full, but any unpasted material from that page was not available.

---

# 1. Executive decision

The project has crossed an important threshold: the next improvement will not come from adding another indicator, classifier, neural network, or dramatic “god-tier” technique.

The remaining ceiling is the missing authority chain:

```text
Verified data and exchange rules
→ verified model release
→ exact target contract
→ calibrated predictive distribution
→ executable action-value distribution
→ conservative sizing
→ authenticated order intent
→ exchange-confirmed lifecycle
→ restart-safe reconciliation
→ live sequential authority revocation
```

The recent commits fixed genuine defects:

- HMM transition-clock mismatch;
- HMM full-history fitting leakage;
- unstable HMM volume normalization;
- simulator slippage double counting;
- fake “day-block” Kelly bootstrap;
- artifact release-identity collision;
- endogenous Kelly calculation on a thin Polymarket ladder.

However, several fixes remain **research-only**, **foundation-only**, or **not wired into the capital path**.

The highest-priority work is therefore:

1. finish artifact authority;
2. implement close-only safety and exchange reconciliation;
3. collect replayable sequenced L2 and owned fill evidence;
4. replace heuristic expectancy/fill/sizing with action-level distributions;
5. validate each strategy forward with independent episodes;
6. only then add execution bandits/offline RL and selected frontier research.

---

# 2. Current implementation truth table

Status definitions:

- **IMPLEMENTED:** code exists.
- **WIRED:** current runtime invokes it in the relevant path.
- **TESTED:** a meaningful regression/invariant test exists.
- **PROMOTABLE:** evidence and authority are sufficient for real-money use.
- **PARTIAL:** one or more of those conditions are missing.

| Component | Implemented | Wired | Tested | Real-money verdict |
|---|:---:|:---:|:---:|---|
| HMM one-transition-per-closed-bar | Yes | Yes | Yes locally | Good foundation |
| HMM train-slice fitting | Yes | Yes | Yes locally | Good foundation |
| Frozen HMM volume scale | Yes | Yes | Yes locally | Good foundation |
| Simulator slippage double-count fix | Yes | Yes in simulator | Yes locally | Simulator remains non-promotable |
| UTC-day Kelly bootstrap | Yes | Yes in simulator | Yes locally | Paper research only |
| Artifact release identity V2 | Yes | No production migration | Selftest | **Foundation only** |
| Strict artifact enforcement | Mechanism exists | No; default off | Partial | **Stop-ship** |
| Endogenous Polymarket Kelly | Yes | **No** | Unit test | Research-only utility |
| Polymarket full-ladder taker economics | Exists in research lanes | Not canonical app authority | Partial | Forward shadow only |
| Fill probability | Heuristic | Used for display/cost call | No calibration | Not usable |
| Applying fill probability to fills | No | No | No | Missing |
| Partial-fill economics | Fragmented | No canonical path | Partial | Missing |
| Binance live liquidation ingestion | Yes | Yes | Basic | Feature only |
| Liquidation cascade forecaster | No | No | No | Not started |
| OI/funding features | Yes | Yes | Partial | Context only |
| Exact liquidation “pain map” | No | No | No | Proposal scientifically overstated |
| Rudimentary wall-disappearance score | Yes | Yes as snapshot feature | Limited | Research diagnostic only |
| V5 cancellation/execution spoof detector | No | No | No | Blocked by data semantics |
| Sequenced Binance local L2 archive | No in multi-venue archive | No | Readiness gate says no | Highest data priority |
| Live spot `depth20` snapshots | Yes | Yes | Limited | Not replayable L2 |
| Fractional differencing | No | No | No | Candidate ablation, not overhaul |
| Causal rolling normalization | No dedicated implementation found | No | No | Build before fracdiff |
| VIX high-frequency feed | No | No | No | Missing/licensing issue |
| DXY/US10Y daily Yahoo polling | Yes | Yes | Basic | Too slow for transmission trading |
| L2 Transformer teacher | No | No | No | Blocked by archive |
| Knowledge-distilled student | No | No | No | Blocked by teacher/evidence |
| TDA iceberg detector | No | No | No | Reject as framed |
| Lyapunov circuit breaker | No | No | No | Null-test only |
| FSR-PPO | Heuristic exists | Off by default | Basic | Not PPO; keep off |
| Execution RL | No | No | Readiness gate refuses | Correctly blocked |
| Binance paper engine | Yes | Yes when enabled | Extensive local tests | Still paper only |
| Central task supervisor | No | No | No | Stop-ship |
| Auth on Binance paper mutations | No | No | No | Stop-ship |
| CORS allowlist | No | Wildcard | No | Stop-ship |
| Reduce-only under kill switch | Incorrect generic behavior | Wired | No full fault matrix | Stop-ship |
| Exchange-truth live reconciliation | No authenticated live adapter | No | No | Stop-ship |
| GitHub Actions evidence | Workflow defined | No runs/status at head | No hosted result | Local tests only |

---

# 3. Corrections to the V5 and V6 claims

## 3.1 Adversarial spoofing

### What exists

`backend/order_flow.py` already computes a `spoof_score` by detecting large walls that disappear after a few `depth20` snapshots without price reaching them.

It also tracks approximate:

- bids/asks added;
- bids/asks cancelled;
- bids/asks executed;
- wall persistence;
- wall migration;
- consumption rates;
- replenishment;
- microprice;
- best-level OFI.

### What does not exist

The V5 detector is not implemented. Current logic does **not** provide:

- per-price-band cancellation velocity;
- sequenced updates;
- exchange update IDs;
- precise cancellation-versus-execution attribution;
- order IDs or queue rank;
- a historical labeled spoofing dataset;
- calibrated spoof probability;
- evidence that fading pulled walls is profitable;
- millisecond execution authority.

### Important correction

A large cancelled order is not automatically spoofing. Spoofing involves deceptive intent, which public L2 cannot prove. The feature should be called something like:

```text
wall_disappearance_hazard
```

not `institutional_spoof_probability`.

### Correct research question

```text
Does a persistent, distance-conditioned wall-disappearance pattern predict
a liquidity vacuum or adverse short-horizon markout after costs?
```

That is testable. “Detect criminal spoofing and short the exact millisecond it is pulled” is not.

---

## 3.2 Liquidation cascade forecasting

### What exists

The app ingests Binance futures `forceOrder`, aggTrades, bookTicker, OI, funding, long/short ratios, and Bybit information.

Features include:

- long liquidation volume;
- short liquidation volume;
- liquidation imbalance;
- liquidation acceleration;
- OI change;
- global OI;
- Binance–Bybit OI divergence;
- funding.

### What does not exist

There is no trained liquidation-cascade forecaster and no verified price-level liquidation map.

### Important correction

```text
total OI / estimated average leverage
```

cannot deduce exact liquidation thresholds.

Liquidation prices also depend on:

- individual entry prices;
- leverage;
- cross versus isolated margin;
- added margin;
- maintenance tiers;
- mark price;
- position side;
- hedge mode;
- exchange rules.

Public aggregate OI cannot invert that hidden distribution uniquely.

### Correct target

Build a **cascade hazard model**, not an exact pain map:

```text
P(forced liquidation notional > threshold within 5/15/30/60 seconds)
```

Condition on:

- OI changes;
- funding/basis;
- long/short account ratios;
- recent liquidations;
- mark–index divergence;
- L2 depth and convexity;
- aggressive flow;
- realized volatility/jumps;
- cross-venue liquidation confirmation.

The action should initially be:

```text
reduce maker exposure / widen quotes / raise uncertainty / skip
```

Directional front-running comes only after an independent economic campaign passes.

---

## 3.3 Fractional differencing

### Status

Not implemented.

### Correction

Fractional differencing does not make neural networks inherently profitable, and neural networks do not universally require every input to be strictly stationary.

The right use is a fold-local feature ablation:

```text
raw normalized level
log return
rolling z-score
fractionally differenced level
```

### Required safeguards

- select `d` inside each training fold only;
- never use test data for ADF optimization;
- use fixed-width truncated weights;
- freeze weights and `d` in the artifact;
- preserve train/serve parity;
- test multiple ADF/KPSS interpretations;
- account for warm-up and effective sample loss;
- compare economic value, not only stationarity.

### Promotion rule

Fractional differencing is retained only if it improves locked post-cost action value beyond simpler transforms and survives feature-dropout tests.

---

## 3.4 Macro volatility transmission

### What exists

A `TradFiMacroClient` polls Yahoo Finance daily DXY and US 10-year values. These are explicitly slow regime context, not a high-frequency feed. No VIX feed is present.

### What is required

Before claiming transmission arbitrage:

- acquire timestamped, legally usable, intraday data;
- use a synchronized clock;
- store source event and local receive times;
- define tradable proxies if official index data is delayed;
- measure lead/lag after latency and market hours;
- model asynchronous clocks and stale periods.

Candidate instruments:

```text
CME/Nasdaq futures or licensed feeds
tradeable ETF/futures proxies
DXY/futures proxy
VIX futures rather than delayed VIX cash, where appropriate
```

This is a research campaign, not a feature added directly to production.

---

## 3.5 TDA for iceberg detection

### Verdict

Reject as framed.

A single monotone price ladder does not magically produce a meaningful Betti-1 “hole” identifying an iceberg. Persistent homology depends completely on the constructed metric/embedding.

A stronger 2026 pre-registered crypto study reported no incremental maker-reversal prediction from several standard persistent-homology constructions over a large microstructure baseline.

### Better replacement

Build hidden-liquidity/replenishment features directly:

- repeated exact-level refill after aggressive trades;
- refill size distribution;
- depletion-to-refill time;
- execution-to-displayed ratio;
- trade-through and recovery;
- markout after refill;
- survival model for displayed size;
- venue comparison.

TDA can remain a capped null test after the direct baseline exists.

---

## 3.6 Largest Lyapunov exponent

### Verdict

Null-test only.

Positive estimated LLE in a noisy stochastic price series is not proof of deterministic chaos. Financial research frequently fails to find robust low-dimensional chaos, and estimates are sensitive to:

- embedding dimension;
- delay selection;
- nonstationarity;
- microstructure noise;
- finite samples;
- stochastic volatility.

### Proper campaign

Compare LLE against simple controls for predicting:

```text
future realized volatility
spread expansion
maker adverse selection
quote-cancel urgency
```

Mandatory controls:

- realized volatility;
- jump score;
- order-flow imbalance;
- regime entropy;
- conformal residual width;
- surrogate/shuffled data.

If it adds no locked incremental value, close the lane.

---

## 3.7 L2 Transformer and knowledge distillation

### Verdict

Potentially valid, currently blocked.

Recent LOB-transformer research shows such architectures can work on suitable L2 datasets. But the project’s multi-venue archive currently has top-of-book only, and the live `depth20` feed is not stored as a sequenced, gap-detectable local book.

### Required order

```text
sequenced archive
→ deterministic replay
→ simple linear/tree/TCN baselines
→ small Transformer teacher
→ proper-score and economic test
→ distillation
→ student latency and parity test
```

Knowledge distillation transfers teacher behavior; it does not guarantee teacher accuracy at student speed.

The student must be tested separately against labels and executable economics, not merely teacher logits.

---

## 3.8 Endogenous Kelly

### What is good

The recent implementation correctly walks the ask ladder and uses average fill VWAP. It demonstrates how exogenous top-of-book Kelly can materially oversize a thin Polymarket book.

### What is missing

It is not used by the app’s live expectancy or simulator sizing path. `calculate_signal_expectancy()` still calls `_compute_kelly_fraction()`.

The endogenous implementation also lacks:

- Polymarket fee curve in the growth function;
- uncertainty around `p_win`;
- partial-fill probability;
- exit-before-settlement liquidity;
- owned inventory and correlated exposure;
- minimum size/tick rules;
- bankroll reserved by other positions;
- drawdown/CVaR constraints;
- forward validation.

### Correct wiring rule

Do not replace `_compute_kelly_fraction()` globally.

Create a separate Polymarket action-sizing module:

```text
fair-value distribution
+ exact token ladder
+ fee schedule
+ uncertainty reserve
+ inventory
+ portfolio limits
→ robust endogenous size
```

Authorized size remains zero until the underlying strategy’s conservative EV has passed forward promotion.

---

# 4. P0 real-money authority phase

Nothing in later phases may submit authenticated real orders until this phase is complete.

## Commit P0-A — Real-money process isolation

### Implement

Create separate executables/processes:

```text
record
research
paper
live-execution
reconcile
report
```

The current general server must not become the authenticated execution process.

### Why

A UI crash, research retrain, notebook, or public endpoint must not share authority with account keys.

### Requirements

- separate environment files;
- separate DBs;
- separate API keys;
- withdrawal disabled;
- IP allowlisting;
- minimal trade permissions;
- no credentials in browser/frontend;
- explicit `REAL_TRADING_NOT_AUTHORIZED` default.

### Tests

- importing research modules cannot initialize live credentials;
- starting UI cannot create an order client;
- missing live authorization blocks boot;
- paper and live databases cannot share a path.

---

## Commit P0-B — Authenticated control plane

### Files

- `backend/binance_paper/routes.py`
- future live routes
- `backend/server.py`
- new `backend/security/control_plane.py`

### Implement

- authentication dependency on every POST/PATCH/DELETE;
- explicit CORS allowlist;
- request idempotency;
- nonce/replay protection;
- actor/action audit ledger;
- configurable read-only UI access;
- no wildcard credentials.

### Tests

- anonymous mutations return 401/403;
- replayed nonce is rejected;
- duplicate idempotency key returns original result;
- read-only routes cannot mutate state;
- audit record exists for every mutation.

### Gate

No live adapter work proceeds before this passes.

---

## Commit P0-C — Close-only risk authority

### Current defect

Generic risk checks can block reduce-only orders when kill switch, stale feed, or sequence faults are active.

### Implement state machine

```text
DISABLED_FLAT
DISABLED_CLOSE_ONLY
RUNNING
PAUSED_CLOSE_ONLY
UNKNOWN_RECONCILIATION
EMERGENCY_FLATTEN
```

### Rules

- faults block new/increasing exposure;
- reduce-only remains available using conservative exchange mark/index rules;
- unknown position blocks local assumptions and starts reconciliation;
- emergency flatten is operator-authorized and idempotent;
- strategy model availability never blocks exposure reduction.

### Tests

Cross-product fault matrix:

```text
kill switch
stale book
sequence gap
model unavailable
DB degraded
position unknown
API timeout
partial fill
restart
```

For each fault:

```text
increase exposure = blocked
reduce exposure = allowed or reconciled
duplicate flatten = impossible
```

---

## Commit P0-D — Exchange-truth order lifecycle

### Implement append-only states

```text
INTENT_CREATED
RISK_APPROVED
SUBMITTING
ACKNOWLEDGED
PARTIALLY_FILLED
FILLED
CANCEL_REQUESTED
CANCELLED
REJECTED
EXPIRED
STATUS_UNKNOWN
RECONCILED
```

### Binance requirements

- signed order adapter;
- user-data stream;
- REST status fallback;
- client order ID/idempotency;
- unknown timeout resolution;
- exchange balances/orders/fills as truth.

### Polymarket requirements

- server-side user channel;
- market order/order lifecycle events;
- matched/confirmed/mined/finalized/reverted states where available;
- tick-size metadata updates;
- token-specific fees;
- outcome-specific inventory.

### Tests

- submit timeout followed by later fill;
- cancel timeout followed by fill;
- partial fill then reconnect;
- duplicate client order ID;
- local DB crash after submission;
- exchange position differs from local;
- Polymarket match later fails/reverts;
- restart with open orders and positions.

---

## Commit P0-E — Central task supervision

### Current defect

Most FastAPI lifespan tasks are detached `asyncio.create_task()` calls.

### Implement

One TaskGroup/supervisor with typed criticality:

```text
CRITICAL_MARKET_DATA
CRITICAL_EXECUTION
CRITICAL_RECONCILIATION
RESEARCH
UI
```

### Failure policy

```text
market-data failure → CLOSE_ONLY
execution failure → CLOSE_ONLY + reconcile
reconciliation failure → block all new orders
research failure → disable only the lane
UI failure → no trading effect
```

### Shutdown order

```text
block new intents
→ cancel/expire resting orders
→ stop intake
→ await strategy and execution workers
→ drain evidence queues
→ reconcile
→ close DB/network resources
```

### Tests

Fault-inject every task and assert runtime state transition.

---

## Commit P0-F — Feed-writer drain correction

### Current defect

The feed-writer sentinel is in the trade lane and can be selected while depth remains pending.

### Implement

- close intake flag;
- no sentinel as a regular job;
- drain until both lanes empty and no job in flight;
- explicit timeout/abandon ledger;
- preserve final depth state;
- add disk-full and locked-file alerts.

### Regression test

Queue one depth item, append stop condition, ensure depth handler executes before worker exit.

---

## Commit P0-G — Production artifact migration

### Current state

`model_artifacts.py` explicitly states `FOUNDATION_ONLY`.

### Implement in isolated commits

1. inventory every model writer;
2. migrate writers to immutable release bundles;
3. inventory every model loader;
4. migrate loaders to verify-before-deserialize;
5. use one champion pointer authority;
6. enable strict identity by default;
7. remove or quarantine direct joblib/pickle authority.

### Degraded behavior

Invalid champion:

```text
recording = allowed
UI/health = allowed
reduce-only = allowed
new forecasts/pricing/ranking/sizing = blocked
```

### Tests

- byte tamper;
- manifest tamper;
- wrong target;
- dataset/protocol collision;
- stale pointer;
- malicious pickle;
- concurrent publish/read;
- crash during publish;
- rollback to known release.

---

# 5. P1 data and replay phase

## Commit P1-A — Sequenced Binance local L2 recorder

### Why first

Spoof analysis, iceberg/replenishment, queue simulation, maker routing, L2 Transformer, and execution RL all depend on replayable L2.

### Implement per venue

For Binance spot and perpetual:

- open diff-depth stream;
- buffer events;
- REST snapshot with `lastUpdateId`;
- apply `[U,u]` sequencing rules;
- detect gaps;
- resync and mark invalid interval;
- record exchange event time and local receive time;
- periodic checkpoint snapshots;
- raw append-only events plus compact derived books.

For Bybit, use the venue’s sequence/snapshot rules.

### Store

```text
venue
symbol
event type
exchange timestamp
receive timestamp
sequence IDs
snapshot/resync ID
bids/asks changes
connection/session ID
data-quality flags
```

### Tests

- correct snapshot bridge;
- duplicate update;
- out-of-order update;
- skipped sequence;
- reconnect;
- 24-hour disconnect;
- checksum/replay parity;
- deterministic final book.

### Gate

`rl_data_readiness.py` remains false until replay tests, not merely stream presence, pass.

---

## Commit P1-B — Durable liquidation/OI event archive

### Current limitation

Live liquidation data is retained in a 60-second in-memory window; signal history stores minute summaries.

### Implement

Append-only raw events:

- force orders;
- OI snapshots;
- funding;
- mark/index;
- long/short ratios;
- perp and spot trades;
- depth state reference.

### Tests

- exact event count;
- restart continuity;
- time alignment;
- no future data in per-event features;
- source outage flags;
- cross-venue deduplication rules.

---

## Commit P1-C — Polymarket canonical book and lifecycle recorder

Use the official market WebSocket:

- `book`;
- `price_change`;
- `last_trade_price`;
- `tick_size_change`;
- `best_bid_ask`;
- `market_resolved`.

Use the authenticated user channel in paper/live execution for owned orders and trades.

### Required state

- token-specific synchronized ladders;
- dynamic tick size;
- minimum size;
- fee rate;
- market/condition/outcome identity;
- local receive and exchange timestamps;
- user order lifecycle;
- resolution.

### Tests

- snapshot then deltas;
- tick-size change invalidates quote;
- market resolution stops new orders;
- stale metadata blocks;
- user event is idempotent;
- UP/DOWN token mapping cannot swap.

---

## Commit P1-D — Unified event replay

Build one event-driven replay kernel used by:

- research;
- paper simulation;
- execution policy evaluation;
- fault injection.

It must not use resampled candles to simulate queue execution.

### Inject

- latency;
- packet loss;
- duplicates;
- sequence gaps;
- REST/WebSocket disagreement;
- partial fills;
- order timeout;
- Polymarket confirmation/revert;
- fee/tick changes;
- disk stalls.

---

# 6. P2 evidence and target-contract phase

## Commit P2-A — Typed target contracts

Every model must declare:

```text
target name
venue
instrument/market family
horizon
decision timestamp
resolution timestamp
outcome domain
official resolution source
comparison operator
fees/cost semantics
allowed actions
```

Examples:

```text
BINANCE_FIRST_PASSAGE_UP_15S
BINANCE_NET_TAKER_RETURN_5M
POLY_SETTLEMENT_UP_5M
POLY_UP_ASK_REPRICE_10S
POLY_MAKER_FILL_500MS
POLY_POST_FILL_MARKOUT_5S
```

A model cannot vote outside its target contract.

---

## Commit P2-B — Independent candidate episodes

One-second samples are not independent trades.

Define event-triggered episodes:

- fair-value residual crosses threshold;
- anchor crossing;
- spread/tick transition;
- queue depletion/refill burst;
- liquidation shock;
- model-set change.

One episode receives one candidate identity until reset/cooldown.

### Reporting clusters

- episode;
- market round;
- UTC day;
- week.

Never count multiple checkpoints in the same round as independent.

---

## Commit P2-C — Honest selection/calibration/locked split

Required chronology:

```text
development
→ model/policy selection
→ calibration
→ locked test never read by selection
→ forward shadow
```

No “final 20% untouched” if the model, checkpoint, or threshold was selected using those rows.

### Required tests

- selection code cannot query locked rows;
- protocol hash freezes thresholds;
- final period begins after selection commit;
- all experiment attempts logged;
- paired baseline comparison by episode/round.

---

## Commit P2-D — Universal outcome accounting

Every candidate must resolve to:

```text
NO_ACTION
NO_FILL
PARTIAL_FILL_MARKED
FULL_FILL_CLOSED
OPEN_INVENTORY_MARKED
INVALID_PREDECLARED_DATA
```

No missing PnL is converted to zero.

Include:

- entry/exit fees;
- spread;
- ladder slippage;
- funding;
- hedge PnL;
- residual liquidation;
- missed opportunity;
- confirmation/revert loss.

---

# 7. P3 economic decision-engine phase

## Separate Binance and Polymarket economics

Do not reuse a single confidence/expected-move formula.

## Binance action values

For each action:

```text
SKIP
TAKER_LONG
TAKER_SHORT
MAKER_LONG_AT_LEVEL
MAKER_SHORT_AT_LEVEL
MAKER_WITH_TTL_THEN_CANCEL
MAKER_THEN_TAKER
REDUCE
```

Predict distributions for:

- net return;
- MFE/MAE;
- time to barriers;
- fill probability;
- post-fill markout;
- signal half-life;
- liquidation/volatility hazard.

## Polymarket action values

```text
WAIT
BUY_UP_TAKER
BUY_DOWN_TAKER
BUY_UP_MAKER
BUY_DOWN_MAKER
EXIT_UP
EXIT_DOWN
BUY_COMPLEMENT_AND_MERGE
HEDGE_BINANCE
HOLD_TO_SETTLEMENT
```

Predict:

- settlement probability;
- executable future token value;
- crossing/re-crossing probabilities;
- fill distribution;
- exit liquidity;
- confirmation/finality risk.

## Authority rule

Use a conservative action-level bound:

```text
q20(net return) > 0
```

or a distributionally robust lower value.

Confidence is not position size.

---

# 8. P4 proper endogenous sizing phase

## Polymarket sizing interface

Input:

```text
settlement probability distribution
model-set uncertainty
exact UP/DOWN ladder
fee schedule
tick/min size
fill distribution
exit/settlement policy
current inventory
correlated Binance exposure
capital reservations
portfolio risk limits
```

Output:

```text
authorized notional
capacity notional
worst-case expected shortfall
binding constraint
```

## Improvements over current endogenous Kelly

- incorporate exact fees;
- optimize shares/notional directly;
- penalize probability estimation error;
- account for partial fill;
- account for remaining exit liquidity;
- use robust log growth/CVaR;
- cap by capacity and portfolio exposure;
- return zero when conservative edge is nonpositive.

## Wiring

Only a promoted Polymarket strategy may call this module.

The general simulator’s `_compute_kelly_fraction()` remains a paper diagnostic or is retired; it must not size live Polymarket trades.

---

# 9. P5 V5 research campaigns

## Campaign V5-A — Wall-disappearance and liquidity-vacuum hazard

### Prerequisite

Sequenced L2 archive.

### Features

Per distance band and side:

- additions;
- cancellations;
- executions;
- cancel/execution ratio;
- lifetime;
- persistence;
- migration;
- refill;
- distance from mid;
- price response;
- cross-venue confirmation.

### Targets

```text
liquidity vacuum in 100/250/500/1000ms
mid-price markout
spread expansion
maker adverse selection
```

### Negative controls

- size-shuffled walls;
- time-shuffled cancellations;
- remove distance;
- remove executions;
- random same-regime events.

### Initial action

Risk veto/quote withdrawal, not directional trade.

---

## Campaign V5-B — Liquidation cascade hazard

### Target

```text
P(liquidation notional > Q within horizon)
```

Horizons: 5s, 15s, 30s, 60s.

### Baselines

- current liquidation acceleration;
- realized vol;
- depth convexity;
- OI/funding;
- aggressive flow.

### Models

- logistic/GAM;
- gradient boosting;
- survival model;
- marked Hawkes only after simpler models pass.

### Promotion

First as a risk/market-making gate. Directional execution is a separate campaign.

---

## Campaign V5-C — Fractional-difference ablation

### Features tested

- price/log price;
- returns;
- rolling normalized level;
- fractional differenced level.

### Protocol

- `d` selected fold-locally;
- fixed-width weights;
- freeze in artifact;
- same raw information cutoff;
- compare proper score and executable EV.

### Stop rule

Close if no consistent incremental economic value across folds/regimes.

---

## Campaign V5-D — Macro transmission

### Data

Timestamped intraday futures/proxy feeds and BTC venue data.

### Tests

- lead-lag by session;
- event-time cross-correlation;
- transfer only after latency;
- Granger/conditional predictive tests;
- adversarial validation;
- trading after all fees.

### Primary use

Change-point/risk context before direction.

---

# 10. P6 V6 research verdicts

## TDA

Do not build an iceberg trading system. At most run a capped, pre-registered incremental null test after direct replenishment baselines.

## LLE

Run a capped null test as a volatility/adverse-selection feature. Do not call it proof of chaos or use it as a circuit breaker without incremental forward evidence.

## Transformer

Build only after data readiness.

Teacher targets should be economically specific:

- next-event mid move;
- first passage;
- maker fill;
- post-fill markout;
- spread transition.

Compare with logistic, LightGBM, TCN, and small MLP.

## Knowledge distillation

Distill only a teacher that beats baselines on locked proper score and economic metrics.

Student gate:

```text
student vs labels
student vs teacher
student calibration
student latency
student economic value
distribution-shift behavior
```

Do not assume teacher performance is preserved.

---

# 11. New ceiling-breaking campaigns beyond V5/V6

## 11.1 Polymarket optimal stopping

Model entry, exit, wait, hedge, and hold using a finite-horizon Snell envelope.

This can create value even if terminal direction alone cannot beat the market enough.

Start with dynamic programming on discretized state before offline RL.

---

## 11.2 Coherent probability-surface projection

Build a joint BTC path distribution and project probabilities for compatible contracts onto a coherent feasible surface.

Trade relative residuals, not another standalone classifier.

---

## 11.3 Conformal model prediction sets

Maintain a statistically calibrated set of plausible best models.

```text
small coherent set → eligible
large disagreement → reduce/WAIT
coverage failure → block
```

---

## 11.4 Sequential e-process authority

Anytime-valid monitoring of:

- net expectancy;
- model-minus-market Brier/log loss;
- fill rate;
- slippage;
- calibration.

It may revoke authority immediately without repeated-testing inflation. It may not grant initial authority.

---

## 11.5 Bayesian change-point control

Detect changes in:

- volatility process;
- liquidity;
- model residuals;
- venue leadership;
- market participation.

On a break:

- shrink weights to prior;
- invalidate old calibration;
- reduce size;
- separate post-break evidence.

---

## 11.6 Polymarket match/finality risk

Track:

```text
MATCHED
CONFIRMED
MINED
FINALIZED
REVERTED
```

Test hedge timing and unwind policy. This matters when a Binance hedge can remain after a Polymarket leg fails.

---

## 11.7 Event-conditioned execution bandit

After a strategy is independently approved, choose among:

```text
SKIP
TAKER
MAKER_BEST
MAKER_INSIDE
MAKER_TTL
MAKER_THEN_TAKER
```

Log behavior propensities and evaluate with IPS/SNIPS/doubly robust estimators.

---

## 11.8 Distributionally robust allocator

Replace point-estimate Kelly with scenario-based robust log growth/expected shortfall.

Size zero if worst-case plausible value is nonpositive.

---

## 11.9 Alpha capacity model

For every strategy estimate:

```text
EV(size)
fill(size)
impact(size)
capital time
maximum profitable size
```

Rank by expected dollars per unit of capital-time and tail risk, not AUC.

---

# 12. RL roadmap

## Stage 0 — Data refusal

Keep `execution_rl_training=False` until sequenced L2 replay and owned fills pass.

## Stage 1 — Contextual bandit

Fixed approved trade thesis; only execution choice changes.

Mandatory behavior-policy propensities.

Evaluation:

- IPS;
- SNIPS;
- doubly robust;
- support/overlap;
- clustered confidence intervals;
- conservative policy improvement.

## Stage 2 — Offline execution RL

State:

- replayable L2;
- owned order state;
- alpha distribution;
- latency;
- inventory;
- remaining horizon;
- risk state.

Actions:

- price offset;
- size;
- TTL;
- cancel/replace;
- take/wait;
- hedge;
- reduce-only.

Algorithms:

- behavior cloning baseline;
- fitted-Q evaluation;
- conservative Q learning;
- implicit Q learning;
- distributional critic.

Constraints:

- no action outside logged support;
- pessimistic value;
- deterministic action mask;
- no real-money exploration;
- lower bound must beat baseline.

## Stage 3 — Minimal live canary

The policy may adjust only bounded execution parameters.

It may never:

- select the trade side;
- override risk;
- modify credentials;
- self-promote;
- exceed deterministic size/leverage limits.

---

# 13. App wiring blueprint

## Backend state

Expose typed, separate blocks:

```text
model_release_status
data_quality
target_forecasts
action_values
execution_quality
risk_state
reconciliation_state
strategy_authority
sequential_evidence
```

## UI

The dashboard must visually separate:

- research score;
- paper score;
- promotable score;
- active authority.

Never show “BUY” based only on model confidence.

For every potential action display:

```text
strategy/target
fair probability or return distribution
executable price
fee/slippage/fill assumptions
conservative EV
authorized size
block reasons
artifact release
data age
```

## Alerts

Critical alerts:

- sequence gap;
- stale market/user stream;
- artifact mismatch;
- reconciliation mismatch;
- unknown order;
- fill-model drift;
- slippage breach;
- sequential alpha invalidation;
- close-only activation;
- writer drops/failures.

## Metrics

Operational:

- event lag;
- queue backlog;
- dropped events;
- reconnects;
- resync duration;
- decision latency;
- order acknowledgement;
- fill latency;
- reconciliation age.

Economic:

- predicted versus realized action value;
- fill calibration;
- markout;
- slippage by size/regime;
- capacity;
- PnL concentration;
- expected shortfall;
- drawdown.

---

# 14. Promotion ladder

```text
RESEARCH
→ LOCKED_HISTORICAL_CANDIDATE
→ FORWARD_SHADOW
→ REALISTIC_PAPER
→ EXCHANGE_TEST ENVIRONMENT where representative
→ MINIMUM LIVE CANARY
→ CONTROLLED SCALE
```

## Strategy gate

- frozen protocol;
- all candidates accounted for;
- exact costs;
- independent episode count;
- positive clustered lower bound;
- positive final untouched period;
- positive both directions where required;
- fee/slippage/latency stress;
- intended-size capacity;
- no single week dominates;
- forward evidence;
- operational replay pass.

## Suggested default evidence gate

For ordinary high-frequency strategies:

```text
≥ 8 continuous forward weeks
≥ 1,000 independent episodes
profit factor > 1.20
clustered lower bound > 0
positive final untouched period
```

These are necessary, not sufficient.

## Live canary

- smallest practical notional;
- zero/minimal leverage;
- one strategy;
- one venue;
- fixed daily loss;
- manual review;
- no automatic scaling.

---

# 15. Exact Claude backlog

Claude should implement in this order:

```text
01 SECURITY_CONTROL_PLANE_V1
02 REAL_PROCESS_ISOLATION_V1
03 CLOSE_ONLY_RISK_STATE_V1
04 EXCHANGE_ORDER_LIFECYCLE_V1
05 TASK_SUPERVISOR_V1
06 FEED_WRITER_DRAIN_V2
07 ARTIFACT_PRODUCTION_MIGRATION_V1
08 BINANCE_SEQUENCED_L2_RECORDER_V1
09 POLYMARKET_CANONICAL_BOOK_USER_LIFECYCLE_V1
10 EVENT_REPLAY_FAULT_INJECTION_V1
11 TARGET_CONTRACTS_V2
12 EPISODE_INDEPENDENCE_V1
13 LOCKED_EVIDENCE_SPLITS_V2
14 UNIVERSAL_EXECUTION_OUTCOMES_V1
15 ACTION_VALUE_ENGINE_V1
16 POLY_ROBUST_ENDOGENOUS_SIZING_V1
17 SEQUENTIAL_ALPHA_AUTHORITY_V1
18 CONFORMAL_MODEL_SET_V1
19 WALL_DISAPPEARANCE_HAZARD_V1
20 LIQUIDATION_CASCADE_HAZARD_V1
21 FRACTIONAL_DIFF_ABLATION_V1
22 MACRO_TRANSMISSION_SHADOW_V1
23 POLY_OPTIMAL_STOPPING_V1
24 EXECUTION_BANDIT_SHADOW_V1
25 OFFLINE_EXECUTION_RL_V1
```

For each commit:

1. reproduce the failure or define the hypothesis;
2. add the failing test first;
3. make the smallest isolated change;
4. preserve existing negative controls;
5. record code/data/protocol hashes;
6. update implementation status;
7. do not change live authority unless the corresponding promotion artifact passes.

---

# 16. Required test suite before real money

## Security

- anonymous mutation;
- replayed request;
- credential leak scan;
- wrong origin;
- permission-scope test.

## Artifacts

- byte and manifest tamper;
- malicious pickle;
- wrong target/data/protocol;
- concurrent publish;
- pointer rollback;
- degraded close-only mode.

## Market data

- sequence gap;
- duplicate/out-of-order;
- stale source;
- reconnect;
- snapshot bridge;
- tick-size change;
- fee change;
- official resolution.

## Orders

- timeout then fill;
- partial fill;
- cancel/fill race;
- duplicate ID;
- restart;
- unknown status;
- reconciliation mismatch;
- Polymarket match/revert.

## Economics

- full ladder;
- partial/no fill;
- residual inventory;
- missed opportunity;
- funding;
- fee stress;
- latency stress;
- capacity.

## Research

- leakage prefix test;
- fold-local transforms;
- locked data inaccessible;
- episode clustering;
- experiment registry;
- negative controls;
- final-period integrity.

## RL

- behavior propensities;
- support overlap;
- IPS/SNIPS/DR consistency;
- pessimistic FQE;
- action masks;
- no online exploration;
- baseline lower-bound improvement.

---

# 17. What not to build next

Do not prioritize:

- another generic classifier on the same labels;
- universal majority voting;
- exact liquidation levels inferred from aggregate OI;
- criminal-spoof attribution from public L2;
- TDA iceberg claims;
- production Lyapunov circuit breakers;
- Transformer training before replayable L2;
- PPO in a candle simulator;
- model confidence mapped directly to leverage;
- threshold tuning after seeing locked results;
- dynamic exits to rescue negative entry EV;
- real orders before reconciliation and close-only behavior pass.

---

# 18. Final success definition

A top-tier personal quant tool will not predict every BTC move.

It will:

1. know exactly what was observable at decision time;
2. know the exact settlement/execution contract;
3. restrict every model to its validated target;
4. report calibrated distributions and uncertainty;
5. calculate executable action value;
6. size against estimation and impact risk;
7. keep `WAIT` dominant;
8. revoke authority when live evidence changes;
9. reconcile exchange truth after faults;
10. preserve an immutable explanation of every action.

The realistic route past the current ceiling is:

```text
several small independent edges
+ superior execution
+ strict abstention
+ robust sizing
+ fault-safe operations
```

—not one spectacular model or “god-tier” mathematical label.

---

# 19. Source map

## Repository evidence

- `backend/regime.py`
- `backend/server.py`
- `backend/trading_simulator.py`
- `backend/model_artifacts.py`
- `backend/quant_platform/risk_engine.py`
- `backend/binance_paper/routes.py`
- `backend/feed_writer.py`
- `backend/data_ingestion.py`
- `backend/order_flow.py`
- `backend/signal_history.py`
- `backend/features.py`
- `backend/venues/rl_data_readiness.py`
- `docs/blueprint_v4_v5_frontier.md`
- `.github/workflows/invariants.yml`
- commit `5682d31e6b6946ae5bb3a9660b5ecb8020cb0c0f`

## External interfaces/research reviewed

- Binance official Spot WebSocket/local-order-book procedure
- Binance official REST reliability and unknown-execution-status guidance
- Polymarket official market and user WebSocket channels
- Polymarket official order lifecycle and dynamic tick-size guidance
- recent fractional-differencing research
- spoofing/order-flow research
- formal Lyapunov/chaos tests in financial time series
- recent LOB Transformer research
- recent pre-registered TDA null evidence on crypto LOBs
