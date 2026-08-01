# BTC Prediction Tool — Trajectory & Counterfactual Engine Audit

**Audit date:** 2026-08-01  
**Repository:** `1rahulvijay/BTC-Prediction-Tool`  
**Audited branch:** `master`  
**Audited head:** `033d9cccb2770df825bef551ed9f63c309230ceb`  
**Source blueprint:** `Pasted markdown(11).md`

---

# Executive verdict

The uploaded blueprint is directionally correct about the next architecture:

> Stop treating BTC direction as the final target. Model the path, execution, and the net value of each feasible action.

However, the repository is **not starting from zero**. It already contains a surprisingly large portion of the proposed trajectory framework:

- future BTC path quantiles;
- BTC MFE/MAE quantiles;
- first-event/competing-risk forecasts;
- Polymarket executable bid and ask path quantiles;
- crossing-event forecasts;
- execution slippage, fill and capacity heads;
- fixed counterfactual exit plans;
- a causal, one-trade-per-round M0 evaluator;
- immutable forward-evidence ledgers;
- a calibrated Polymarket fair-value candidate;
- fail-closed artifact, data and promotion controls.

The central missing piece is not “another 12-head model.”

It is a **promotable Counterfactual Opportunity Engine** that learns direct, executable P&L distributions for each frozen action and policy.

The current scenario engine explicitly admits that its five marginal-quantile paths do not define a valid joint path distribution and that its P&L, CVaR and profit probabilities are diagnostic only. The trade-plan optimizer therefore correctly refuses to use those numbers to authorize trades.

## Correct next leap

```text
Current market state
+ exact executable venue state
+ frozen candidate policy
+ proposed position size
+ inventory/risk state
→ direct distribution of realized net P&L
→ choose the action with a positive conservative lower bound
→ otherwise WAIT
```

## Current production status

- Current app boot and main HTTP surfaces were exercised successfully under safe configuration.
- Local validation reports 99 passing Pytest tests and 76 local CI steps.
- Real orders remain disabled.
- There are no hosted workflow runs/statuses associated with the audited head.
- Only one of the existing saved-model artifacts was confirmed rewritten with an integrity manifest; the remaining artifacts still require their actual trainers to run.
- The calibrated Polymarket fair-value strategy is wired but currently inert because its source-model artifacts are not yet deployment-valid.
- Its entry has promising but incomplete temporal evidence; its dynamic exit has no sufficient historical quote-trajectory evidence.

---

# 1. Uploaded blueprint versus current repository

Status meanings:

- **Implemented:** usable code exists for the stated concept.
- **Partial:** important pieces exist, but target, evidence or serving coverage is incomplete.
- **Research-only:** code exists but has no capital authority.
- **Missing:** no meaningful implementation found.
- **Do not build yet:** blocked by prerequisite evidence or likely to inflate overfitting.

---

## 1.1 The twelve proposed heads

| Blueprint head | Current status | Existing repository implementation | Critical missing work |
|---|---|---|---|
| 1. Tradability/volatility | **Partial** | Main model has magnitude/quantile machinery; complete-trade BTC path head predicts future returns, MFE and MAE. Research found two-sided magnitude under `rv_term_inversion` survives multiplicity. | A direct target for **usable post-cost opportunity**, not raw range. Must predict whether at least one action has positive conservative net value. |
| 2. Regime | **Implemented/Partial** | Causal HMM and regime-specific experts exist elsewhere in the app. | The uploaded taxonomy—breakout, failure, squeeze, exhaustion, whipsaw—and calibrated transition probabilities are not implemented as one authoritative head. Do not expand taxonomy until it improves action P&L over the current HMM. |
| 3. High/low excursion | **Substantially implemented** | `train_btc_path_model.py` trains return quantiles plus BTC MFE/MAE; serving exposes MFE/MAE and future path quantiles. | Separate upper/lower excursion distributions from anchor and from each proposed entry; calibration by horizon/regime; no promotable artifact yet. |
| 4. Barrier ordering | **Partial** | Competing-risk head predicts `ANCHOR`, `LOWER`, `NONE`, `UPPER`; path-shape trainer labels direct/fade archetypes. | Explicit upper-first/lower-first/both-order labels over multiple adaptive barrier pairs; economic proof. Existing research found apparent first-passage advantage can be a structural path artifact. |
| 5. Persistence/survival | **Missing as dedicated head** | `first_event_time` quantiles exist. | Survival curve for impulse life, alpha half-life, trend failure and time until edge becomes nonpositive. |
| 6. Reversal hazard | **Partial heuristic** | Binance paper strategies now have thesis-based dynamic exits; mean reversion and trend strategies can invalidate their premise. | Learned 10s/30s/60s/120s reversal hazards, pullback-versus-new-trend classification and forward economic validation. |
| 7. Path archetype | **Implemented offline, not authoritative** | `build_path_labels.py` defines `CHOP`, `UP_DIRECT`, `UP_THEN_DOWN`, `DOWN_DIRECT`, `DOWN_THEN_UP`; it saves only when future accuracy clears majority baseline by 3 points. | Confirm whether an artifact actually passed and is served. Current research found no directional path signal above matched random in the tested path study. |
| 8. Endpoint/settlement | **Implemented, candidate only** | P(hold)/round-state heads; calibrated fair-value paper strategy; official settlement handling. | Source artifacts need verified manifests; calibrator must become deployable; newest temporal split failed; forward evidence must accrue. |
| 9. Dynamic entry | **Partial** | Fixed entry checkpoints, quantities, ask-walking, latency, candidate validity, entry fill/slippage/capacity heads. | Parameterized entry policies: now, delayed, pullback, breakout, failed breakout, maker/GTD. These should be introduced gradually under a frozen action catalogue. |
| 10. Dynamic exit | **Partial** | Nine fixed Polymarket exit plans; Binance thesis exits; fair-value Polymarket exit hypothesis. | Direct P&L heads per exit plan; repeated Polymarket bid trajectories; dynamic PM exit is currently explicitly unmeasured. |
| 11. Execution quality | **Partial** | Entry slippage, max executable quantity, entry-complete, quote-survival and worse-by-1c/2c heads. | Fill survival over time, partial quantity distribution, post-fill markout, cancel/fill race, maker TTL, missed-fill cost and conservative queue scenarios. |
| 12. ACT/SKIP | **Partial and shadow-only** | M0 exact-plan-profit classifier, causal first-qualifying policy, matched-random control, immutable evidence, `BUY_UP/BUY_DOWN/NO_TRADE` optimizer. | Direct plan-return distributions; current scenario-derived economics are intentionally nonpromotable. A valid champion bundle, threshold artifact and 8-week/1,000-round evidence set are absent. |

---

# 2. What already resembles a Counterfactual Trade Cube

The repository already has a **narrow counterfactual cube** for short-duration Polymarket decisions.

## Existing dimensions

### Market and direction

```text
5-minute and 15-minute markets
UP and DOWN
```

### Decision checkpoints

```text
5m:  240, 180, 120, 90, 60, 30 seconds left
15m: 720, 600, 480, 360, 240, 180, 120, 90, 60, 30 seconds left
```

### Quantity

```text
1, 5, 10, 25, 50, 100 shares
```

### Fixed exits

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

### Execution assumptions

```text
500 ms normal entry latency
1,000 ms stress latency
decision-time ladder
arrival-time ask VWAP
future bid ladders
partial-fill and capacity labels
official settlement only
```

### Existing outputs

```text
future BTC return quantiles
BTC MFE/MAE
first event probabilities and timing
future share-bid quantiles
future share-ask quantiles
crossing-event probabilities
entry arrival slippage
fill/quote-survival probabilities
capacity quantiles
plan-profit labels and plan net PnL
```

This is an excellent base. It is materially closer to the uploaded concept than the blueprint assumes.

---

# 3. What the full Opportunity Surface still lacks

The current system is not yet the proposed function:

```text
O(direction, entry, hold, exit, stop, venue, size)
```

## 3.1 Missing action dimensions

### Entry policy

Current:

```text
fixed checkpoint + immediate taker-style entry after frozen latency
```

Missing:

```text
WAIT_N_SECONDS
PULLBACK_X_BPS
BREAKOUT_X_BPS
FAILED_BREAKOUT
LIQUIDITY_SWEEP_CONFIRMATION
MAKER_BEST
MAKER_INSIDE
MAKER_GTD
MAKER_THEN_TAKER
```

### Binance policy surface

The complete-trade cube is primarily Polymarket-oriented. Binance paper has strategies and dynamic thesis exits, but not one shared counterfactual action surface spanning:

```text
LONG / SHORT / WAIT
entry policy
holding duration
exit policy
size
maker/taker route
```

### Continuous or richer sizing

Current quantities are a small fixed share grid. Missing:

```text
direct EV(size)
marginal EV of the next unit
impact curve
capital-time curve
inventory-dependent capacity
portfolio-dependent size
```

### Position and portfolio state

The current policy input is dominated by market and contract state. A real action-value model also requires:

```text
current inventory
open orders
existing BTC delta
venue balance
capital already reserved
strategy correlation exposure
daily/weekly risk budget
```

---

## 3.2 Missing direct economic outputs

The most important missing artifact is:

```text
P(net PnL | market state, action, size)
```

for every frozen action.

The current scenario engine joins marginal q10/q25/q50/q75/q90 forecasts into five synthetic paths. The file correctly marks these results:

```text
diagnostic_only = true
promotable = false
```

because:

- marginal quantiles do not specify a joint temporal path;
- real paths can cross quantile curves;
- barrier hits between sampled timestamps are missed;
- settlement and pre-settlement movement are not modelled jointly;
- five weighted points do not produce a credible 5% CVaR.

The repository itself identifies the replacement:

> direct per-plan P&L models trained against realized `plan_*_net` labels.

That replacement should be the next major model campaign.

---

# 4. Audit of the uploaded “fresh targets”

| Proposed target | Status | Recommendation |
|---|---|---|
| Alpha half-life | **Missing** | High priority after direct plan-value heads. Label the first causal time the chosen action’s realized or estimated edge becomes nonpositive. |
| Last anchor crossing | **Missing** | High-potential Polymarket target. Build competing risks: final upward crossing, final downward crossing, no more crossing. |
| Settlement fragility | **Partial** | Anchor distance, time, volatility and crossing features exist. Add explicit probability of final flip, close within 1/2/5/10 bps, and local probability sensitivity. |
| Polymarket probability excursion | **Substantially partial** | Future executable bid/ask path quantiles and crossing events exist. Add max future executable bid, min bid, time to extrema and bid-first barrier ordering. |
| Mispricing half-life | **Missing** | Build only after calibrated fair-value and dense per-round quotes accrue forward. |
| Liquidity-vacuum probability | **Missing** | Build after sufficient sequenced L2. Use depth removal/recovery and price impact, not merely “wall disappearance.” |
| Price-response kernel | **Missing** | Good second-stage L2 research: signed-flow shock → 100ms/1s/5s/30s response distribution. |
| Flow-origin classification | **Missing** | Useful but data-hungry. Start with interpretable labels and VAR/Hawkes baselines before GNNs. |
| Path entropy/predictability | **Missing explicitly** | Low-cost ablation: future efficiency ratio, path length/net displacement, turn count and anchor crossings. |
| Recovery-time distribution | **Partial** | `actual_first_profitable_s`, MFE/MAE and first-event time exist. Add underwater probability, recovery-before-expiry and survival distribution. |
| Excursion ordering graph | **Missing** | Defer. Begin with hidden semi-Markov baseline only after simpler archetype and hazard heads show value. |
| Capacity curve EV(q) | **Partial** | q10/q50/q80/q95 executable capacity and endogenous ladder Kelly exist. Add direct net PnL by size and marginal-capital decay. |

---

# 5. Audit of the proposed model stack

## A. Multi-timescale state encoder

**Status: Not implemented as one shared encoder.**

The current approach uses engineered feature sets with HGB/LightGBM/CatBoost and other existing sequence models elsewhere in the project.

### Recommendation

Do not build a giant Mamba/TFT encoder first.

Create a common immutable feature snapshot and compare:

```text
LightGBM baseline
TCN baseline
small state-space encoder
```

Only keep a shared neural encoder if it improves multiple direct economic heads on locked data.

---

## B. Conditional trajectory generator

**Status: Missing.**

The current `scenario_engine.py` is not a learned joint generator and explicitly refuses promotional authority.

### Recommendation

Do not start with diffusion.

Test in this order:

```text
1. Empirical nearest-neighbour path bootstrap
2. Regime-conditioned residual bootstrap
3. Copula/joint quantile baseline
4. Conditional normalizing flow
5. More complex generator only if necessary
```

Validation must include joint coverage of:

```text
terminal return
high
low
time to high/low
barrier order
path length
anchor crossings
plan PnL
```

---

## C. First-passage and survival engine

**Status: Partial.**

The repository has:

- competing-risk first event;
- first-event-time quantiles;
- offset-specific crossing labels.

Missing:

- full cause-specific survival curves;
- alpha expiry;
- reversal hazard;
- final anchor crossing;
- recovery hazard.

### Recommendation

Build one shared discrete-time competing-risk framework before several unrelated survival libraries.

---

## D. Path-signature encoder

**Status: Missing.**

### Recommendation

A valid research ablation, not a priority. Use low-order signatures beside LightGBM only after direct economic targets exist. Reject it unless it improves locked action value over ordinary path statistics.

---

## E. Opportunity Surface Network

**Status: Partial.**

`trade_plan_optimizer.py` already ranks candidates using:

```text
expected PnL
tail-risk penalty
uncertainty penalty
liquidity penalty
q10 capacity
```

But the underlying economic distribution is diagnostic only and the action set is narrow.

### Recommendation

Replace the diagnostic scenario input with direct plan-value heads. Then expand the action catalogue incrementally.

---

## F. Distributional policy optimizer

**Status: Partial research foundation.**

The repository already uses quantile outputs and expected-shortfall-style penalties. It does not have a validated distributional critic or offline-RL policy.

### Recommendation

Use supervised direct plan-value models plus deterministic optimization first. Offline RL remains blocked until:

- logged behavior propensities exist;
- action support is adequate;
- real execution outcomes exist;
- a validated replay environment exists.

---

## G. Conformal abstention

**Status: Missing as conformal inference.**

The app has calibration, data-quality gates, artifact validity and evidence permissioning, but no formal conditional conformal lower bound on action value.

### Recommendation

Apply conformal methods to **plan net PnL residuals**, not merely BTC price. The decision gate should be:

```text
lower conformal bound of net plan PnL > 0
```

within a sufficiently familiar regime and size bucket.

---

# 6. Claims in the uploaded blueprint that require correction

## 6.1 “Most trajectory heads can be trained now, so they can prove the edge”

They can be trained now, but the repository has already tested several core claims:

- directional path information did not beat matched-random controls;
- simple excursion timing was broad and approximately uniform;
- apparent first-passage edge occurred in the zero-information baseline too;
- two-sided magnitude was the one path result that survived multiplicity.

Therefore, training all proposed heads is not automatically valuable.

Every head needs two gates:

```text
Does it beat an appropriate predictive baseline?
Does it improve a frozen executable action after costs?
```

---

## 6.2 “A shared 12-head neural architecture should be the next build”

That is too early.

It would create:

- many targets;
- many losses and weights;
- many architecture choices;
- many chances to cherry-pick apparent improvements;
- difficult attribution when a trade succeeds or fails.

Train simple independent baselines first. Share representations only after several heads independently demonstrate value.

---

## 6.3 “Historical trade data is enough for the core predictive edge”

It is sufficient to test path labels. It is not sufficient to prove:

- maker execution;
- queue survival;
- cancellations;
- exact fill probability;
- Polymarket dynamic exits;
- cross-venue order-book lead/lag.

The uploaded file correctly warns that trades cannot reconstruct exact historical L2. Preserve that distinction.

---

## 6.4 “Exact queue position can later be learned from public L2”

Public aggregate L2 provides price-level quantity, not individual order identity or exact priority. The system can build:

```text
optimistic queue-ahead
base queue-ahead
pessimistic queue-ahead
```

It cannot claim observed exact queue rank.

---

## 6.5 “The dynamic Polymarket exit is already justified by symmetric fair value”

It is a sensible hypothesis, but the repository explicitly says it is not measured. There are too few complete quote trajectories.

Treat:

```text
calibrated entry + hold to settlement
```

and:

```text
calibrated entry + dynamic exit
```

as two separate strategies with separate promotion evidence.

---

## 6.6 “A 100ms deep model should be prioritized”

No.

First measure alpha half-life and end-to-end delay sensitivity. A model that produces a good 15-minute decision but takes 5ms instead of 1ms does not require an FPGA or elaborate encoder.

---

# 7. Exact build-next roadmap for Claude

---

## Phase 0 — Unblock the existing verified platform

### Objective

Make current shadow/paper heads serviceable before creating new model families.

### Tasks

1. Run every authoritative trainer that produces an active artifact.
2. Confirm every saved model has:
   - integrity sidecar;
   - artifact hash;
   - feature schema;
   - training window/cutoff;
   - dataset identity;
   - policy identity where applicable.
3. Re-run the P(hold) calibration challenger against the newly identified source artifacts.
4. Confirm `PM_CALIBRATED_FAIR_VALUE_V1` changes from `CAL_UNAVAILABLE` to visible forward decisions.
5. Build/promote a verified complete-trade champion bundle.
6. Freeze the entry-threshold artifact before evidence begins.
7. Run the recorders continuously.
8. Restore hosted CI and branch protection.
9. Complete at least a seven-day shadow-system soak.

### Required result

```text
all active artifacts verified
calibrator deployable
complete-trade bundle resolved through champion pointer
threshold frozen
no pre-freeze V2 evidence mixed into the run
real orders still disabled
```

---

## Phase 1 — Universal Opportunity Ledger V1

### Objective

Create the common evidence substrate for the uploaded Counterfactual Opportunity Engine.

### New package

```text
backend/opportunity_engine/
    __init__.py
    action_catalog.py
    schema.py
    ledger.py
    counterfactual_resolver.py
    regret.py
    service.py
```

### Initial frozen action catalogue

Keep V1 small.

#### Polymarket

```text
WAIT
BUY_UP_TAKER_HOLD
BUY_DOWN_TAKER_HOLD
BUY_UP_TAKER_TAKE_3C_STOP_3C
BUY_DOWN_TAKER_TAKE_3C_STOP_3C
PM_CALIBRATED_FAIR_VALUE_HOLD
```

#### Binance

```text
WAIT
TAKER_LONG_FIXED_60S
TAKER_SHORT_FIXED_60S
TAKER_LONG_THESIS_EXIT
TAKER_SHORT_THESIS_EXIT
```

Do not add maker, pullback, breakout and reversal actions in the first release.

### Immutable key

```text
opportunity_id
action_id
model_bundle_sha256
policy_sha256
```

### Required fields

```text
decision timestamps and source ages
market/round/instrument
current inventory and risk state
action parameters
expected entry and exit assumptions
counterfactual eligibility
realized entry/fill
realized exit
fees/funding/rebates
residual inventory
capital duration
gross and net PnL
terminal outcome
failure/reconciliation state
```

### Critical rules

- `WAIT` is always an explicit action.
- Every original opportunity remains in the ledger.
- No-fill, partial-fill and invalid-data outcomes remain explicit.
- Missing PnL is never converted to zero.
- One independent unit remains one market round/opportunity, not every checkpoint.

---

## Phase 2 — Direct Plan-Value Heads V1

### Objective

Replace diagnostic synthetic paths with direct economic prediction.

### New trainer

```text
backend/trade_forecast/train_plan_value_heads.py
```

### New serving module

```text
backend/trade_forecast/plan_value_serving.py
```

### For every frozen plan, predict

```text
E[net PnL]
P(net PnL > 0)
q10/q25/q50/q75/q90 net PnL
expected shortfall
expected holding time
probability of no fill
expected filled quantity
capital-time
```

### Inputs

```text
existing market/contract features
action ID and frozen parameters
requested size
current inventory/risk state
```

### Baselines

```text
unconditional plan average
market-price fair-value rule
calibrated P(hold) rule
simple linear/logistic model
```

### Promotion condition

The direct model must beat:

```text
WAIT
the plan’s unconditional average
the calibrated fair-value champion
matched-random actions at the same coverage
```

on untouched chronological and forward evidence.

### Required code change

Only after direct heads pass should `trade_plan_optimizer.py` accept plan economics with:

```text
diagnostic_only = false
promotable = true
```

---

## Phase 3 — Regret Decomposition V1

### Objective

Diagnose where each lost dollar came from.

### Calculate

```text
forecast regret
action regret
entry-route regret
exit-policy regret
latency regret
fill regret
sizing regret
hedge regret
operational regret
```

### Example definitions

```text
forecast regret:
best action under true future - best action under forecast

action regret:
best realizable action - selected action

execution regret:
selected action under idealized arrival - actual execution

sizing regret:
best size on frozen size grid - selected size
```

This prevents retraining the forecast model when the real weakness is execution or sizing.

---

## Phase 4 — Current highest-value forward campaigns

### 4.1 `PHOLD_CALIBRATED_FORWARD_V1`

Use only the measured envelope:

```text
calibrated entry
hold to settlement
```

Required:

```text
8 continuous weeks
1,000 independent rounds
positive day/week lower bound
profit factor > 1.20
stable price-bucket calibration
positive fee and latency stress
no concentrated profit
```

Do not combine dynamic exit evidence with this campaign.

---

### 4.2 `PM_DYNAMIC_EXIT_FORWARD_V1`

Use exactly the same entry population as the hold campaign.

Compare:

```text
hold to settlement
fair-value take-profit
probability-collapse stop
```

Use actual executable bids over the entire round. Report missed recovery and early-exit regret.

---

### 4.3 `USABLE_VOLATILITY_V1`

Do not predict “large move.”

Predict:

```text
P(at least one frozen action has q20 net PnL > 0)
maximum conservative action value
expected number of economically usable impulses
```

This converts the one robust magnitude result into an action-aligned question.

---

### 4.4 `DERIBIT_STRADDLE_MAGNITUDE_V1`

The repository found a two-sided magnitude predictor but could not monetize it through a late Binance breakout bracket.

Next steps:

1. Persist the full BTC option chain:
   - instrument;
   - strike;
   - expiry;
   - bid/ask;
   - mark IV;
   - underlying;
   - timestamps.
2. Test executable straddle/strangle returns.
3. Compare predicted magnitude with implied move.
4. Include spread, fees, theta, skew and exit liquidity.
5. Use longer option horizons that actually match listed contracts.

This is more justified than forcing a direction trade out of directionless magnitude information.

---

### 4.5 `BINANCE_L2_FILL_MARKOUT_V1`

After sufficient gap-free depth coverage:

Predict:

```text
P(fill by 100/250/500/1000ms)
partial quantity distribution
fill-after-cancel probability
100ms/1s/5s post-fill markout
missed-fill opportunity cost
```

Use queue scenarios, not “exact queue rank.”

---

## Phase 5 — New trajectory heads, in order

Only build a new head when the previous phase has a direct economic consumer.

### 5.1 Alpha half-life

Target:

```text
time until a frozen action’s conservative expected edge becomes <= 0
```

Use discrete-time survival or competing risk.

### 5.2 Last anchor crossing and settlement fragility

Targets:

```text
P(no more anchor crossings)
time of final crossing
final-crossing direction
P(final flip)
P(settlement within 1/2/5/10 bps)
```

### 5.3 Recovery survival

Targets:

```text
P(position goes underwater)
P(recovers before expiry/timeout)
time to recovery
P(stop before recovery)
```

### 5.4 Mispricing half-life

After adequate PM quote history:

```text
time until calibrated fair-value wedge closes
P(wedge widens before closing)
executable convergence value
```

### 5.5 Price-response kernel and flow origin

After synchronized trade/L2 history:

```text
flow shock → future return distribution by delay
spot-led/perp-led/liquidation/liquidity-withdrawal classification
```

Begin with simple VAR, logistic and Hawkes baselines.

---

## Phase 6 — Expand the action surface carefully

Once direct plan heads pass, add one action family at a time:

```text
1. delayed entry
2. pullback entry
3. breakout entry
4. maker with fixed TTL
5. maker then taker fallback
6. sequential reversal
7. dynamic hedge
```

Each action-family addition is a new preregistered experiment and adds to the multiple-testing family.

Do not launch a grid of thousands of policies and select the best result.

---

## Phase 7 — Joint scenario generation

Build only if direct plan-value models reveal that their main limitation is inability to generalize across new policies.

Development order:

```text
nearest-neighbour conditional path bootstrap
regime-conditioned residual bootstrap
joint copula/quantile model
normalizing flow
complex neural generator only if simpler methods fail
```

A scenario generator must reproduce joint—not merely marginal—statistics.

---

## Phase 8 — Conformal abstention and robust authority

### Inputs

```text
direct plan-value residuals
regime
market-price bucket
seconds remaining
spread/depth
size
model disagreement
data quality
distribution distance
```

### Gate

```text
lower conformal bound of net PnL > 0
and current state is inside validated support
and execution quality is acceptable
```

The output is still one of:

```text
ACT
WAIT
CLOSE_ONLY
```

---

# 8. Exact tests required

## Opportunity-ledger tests

- Every opportunity contains `WAIT`.
- Every frozen action has exactly one outcome state.
- Counterfactual rows cannot overwrite each other.
- Missing outcome does not become zero.
- Checkpoints inside one round do not inflate independent sample count.
- A policy change creates a different policy hash.
- A model change creates a different evidence run.

## Direct plan-head tests

- Features stop at decision time.
- Action parameters are immutable.
- Size is available at decision time.
- Train/calibration/test are chronological and purged.
- Quantiles are monotonic.
- PnL units are consistent.
- Predictions cannot become promotable without forward evidence.
- Market-price and random baselines are always reported.

## Survival-head tests

- Censoring and terminal settlement are distinct.
- A target after contract expiry is rejected.
- Competing risks sum correctly.
- Calibration is checked by horizon and time bucket.
- Survival probability is monotonic with time.

## Execution tests

- Partial fill then disconnect.
- Cancel request then late fill.
- Fill larger than requested is rejected.
- Order replacement loses priority.
- L2 sequence gap invalidates maker evidence.
- Unknown order causes close-only.
- Missed-fill opportunity is measured.

## Polymarket tests

- Exact YES/NO token identity.
- Actual executable bid/ask, never chart midpoint.
- Tick and fee changes.
- Official settlement source and comparison operator.
- Exact equality/rounding near anchor.
- MATCHED, MINED, CONFIRMED, RETRYING and FAILED lifecycle.
- Dynamic exit evaluated only from forward quote trajectories.

---

# 9. What not to implement next

Do not prioritize:

```text
one giant 12-head neural model
diffusion path generator
offline RL
GNN venue graph
path signatures
hundreds of candidate policy combinations
100ms hardware optimization
real-money execution
```

before direct plan-value heads and a complete opportunity ledger exist.

The repository has already demonstrated that:

- more directional models do not create gross 15-minute edge;
- path direction and simple timing are weak;
- magnitude can be predictable without a profitable instrument;
- execution improvements can reduce losses without turning a bad signal positive.

The next model must answer an economic question directly.

---

# 10. Exact Claude backlog

```text
00 RUN_ALL_ARTIFACT_TRAINERS_AND_VERIFY_MANIFESTS_V1
01 COMPLETE_TRADE_CHAMPION_BUNDLE_AND_THRESHOLD_FREEZE_V1
02 UNIVERSAL_OPPORTUNITY_ACTION_LEDGER_V1
03 DIRECT_PLAN_PNL_HEADS_V1
04 PLAN_VALUE_SERVING_AND_FAIL_CLOSED_PERMISSIONS_V1
05 TOTAL_REGRET_DECOMPOSITION_V1
06 PHOLD_CALIBRATED_HOLD_FORWARD_V1
07 PM_DYNAMIC_EXIT_FORWARD_V1
08 USABLE_VOLATILITY_ACTION_TARGET_V1
09 DERIBIT_FULL_CHAIN_RECORDER_V1
10 DERIBIT_STRADDLE_MAGNITUDE_V1
11 BINANCE_L2_FILL_SURVIVAL_MARKOUT_V1
12 ALPHA_HALF_LIFE_SURVIVAL_V1
13 LAST_CROSSING_SETTLEMENT_FRAGILITY_V1
14 RECOVERY_SURVIVAL_V1
15 MISPRICING_HALF_LIFE_V1
16 FLOW_RESPONSE_ORIGIN_V1
17 EXPANDED_ACTION_CATALOGUE_V2
18 CONDITIONAL_PATH_GENERATOR_ABLATION_V1
19 CONFORMAL_PLAN_VALUE_ABSTENTION_V1
20 CONTROLLED_REAL_ORDER_PLUMBING_CANARY_V1
```

Every commit must:

1. state one hypothesis or one defect;
2. add the failing test first;
3. preserve real-order refusal;
4. record data, code, model and policy hashes;
5. include a matched baseline;
6. report negative results;
7. never auto-promote.

---

# 11. Capital-readiness sequence

The uploaded blueprint is a research architecture, not yet a reason to place $100,000.

Use:

```text
$0 shadow
$250–$500 order-lifecycle plumbing
$1,000 execution canary
$2,500 capacity canary
$5,000 first economic stage
$10,000
$25,000
$50,000
$100,000
```

Before any economic canary:

```text
hosted CI green
all active artifacts verified
startup/reconnect reconciliation
exact fee/filter sync
no unresolved orders
close-only fault tests
one direct plan strategy with positive locked and forward evidence
```

Before scaling:

```text
actual fills match expected fills
actual slippage and markout remain within frozen limits
positive day/week lower bound
profit factor > 1.20
cost/latency/size stress survives
no single week dominates profit
capacity curve remains positive at the next size
```

---

# Final conclusion

The uploaded blueprint identifies the right long-term destination, but the repository has already implemented many of its trajectory and execution primitives.

The strongest missing layer is:

```text
Direct counterfactual plan-value prediction
+ universal action ledger
+ regret decomposition
```

Build that before adding more sophisticated path models.

The most defensible near-term research portfolio is:

```text
calibrated Polymarket hold-to-settlement candidate
+ separate dynamic-exit forward experiment
+ usable-volatility action target
+ Deribit straddle test for the proven magnitude signal
+ L2 fill/markout execution research
```

The correct thesis is not:

> Predict the path perfectly.

It is:

> For a small, frozen set of feasible actions, estimate the direct post-cost return distribution, prove that the conservative value is positive, and abstain everywhere else.
