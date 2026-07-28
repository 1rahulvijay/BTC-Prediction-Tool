# Documentation Index

## Latest canonical implementation
- **[BINANCE_MAKER_CONVERSION_V1_2026-07-28.md](active/BINANCE_MAKER_CONVERSION_V1_2026-07-28.md)** -
  forward-only execution campaign for the frozen 5s/15s event-time candidates.
  It reconstructs a sequenced Binance USD-M futures book, compares taker/taker,
  maker/taker, maker-fallback/taker and maker/maker routes on one preserved
  denominator, records conservative queue and adverse-selection evidence,
  enforces artifact provenance and frozen promotion gates, and contains no
  API-key or order-submission path. The initial live smoke was operationally
  clean but is not profitability evidence.
- **[POLYMARKET_REPRICING_SHADOW_V1_2026-07-28.md](active/POLYMARKET_REPRICING_SHADOW_V1_2026-07-28.md)** -
  isolated execution-routing forward shadow for the surviving UP/DOWN ask-worsening
  heads; four same-denominator policies, baseline-versus-evidence calibration,
  complete-depth and delay stress, explicit missed-fill accounting, side-specific
  frozen gates, fail-closed artifacts, and no production or order path.
- **[EVENT_EXECUTION_AND_ANCHOR_CROSSING_RESULTS_2026-07-28.md](active/EVENT_EXECUTION_AND_ANCHOR_CROSSING_RESULTS_2026-07-28.md)** -
  frozen ten-experiment campaign that tests 5s/15s event predictions as execution,
  contract-repricing, anchor-crossing, and matched-horizon BTC evidence. Event veto/delay,
  crossing augmentation, and BTC microtrades were rejected. UP/DOWN 5-second contract
  repricing passed the research gate and remains forward-shadow only.
- **[EVENT_EVIDENCE_ACCUMULATOR_RESULTS_2026-07-28.md](active/EVENT_EVIDENCE_ACCUMULATOR_RESULTS_2026-07-28.md)** -
  frozen nine-configuration replay of persistent 5s/15s/30s/60s evidence into
  independent 5m/15m candidates. Episode construction worked, but its 64.56%
  side accuracy trailed the 74.83% distance/time baseline and worsened Brier/log
  loss, so the incremental direction hypothesis and all promotion gates failed.
- **[ECONOMIC_V2_BLUEPRINT_RESULTS_2026-07-28.md](active/ECONOMIC_V2_BLUEPRINT_RESULTS_2026-07-28.md)** -
  frozen tests of the proposed LONG/SHORT common-factor decomposition and Polymarket
  market-price residual. Magnitude was repeatable, residual direction lost after costs,
  the market residual underperformed the market baseline, all promotion gates failed,
  and no serving or paper-policy artifact changed.
- **[EVENT_TIME_SPECIALIST_HEADS_2026-07-28.md](active/EVENT_TIME_SPECIALIST_HEADS_2026-07-28.md)** -
  two independent one-second event-time experiments covering direction, movement,
  round-trip and ACT/SKIP heads. Short-horizon ranking was repeatable, but no economic
  selector cleared the frozen support and cost requirements.
- **[ECONOMIC_POLICY_CAMPAIGN_180D_PROTOCOL_2026-07-28.md](active/ECONOMIC_POLICY_CAMPAIGN_180D_PROTOCOL_2026-07-28.md)** -
  frozen standalone test of direct economic LONG/SHORT heads, expected-net and
  q20 specialists, ACT/SKIP, and one causal dynamic-exit challenger. It declares
  822 finite policy configurations, selects before a locked 30-day test, saves
  no serving artifacts, and permits at most a historical shadow candidate.
- **[ECONOMIC_POLICY_CAMPAIGN_180D_RESULTS_2026-07-28.md](active/ECONOMIC_POLICY_CAMPAIGN_180D_RESULTS_2026-07-28.md)** -
  complete older-era locked test: all 822 selection configurations failed,
  both selected SHORT policies lost about 13 bps per trade, expected-net models
  had no signed skill, q20 correctly abstained, ACT/SKIP did not create edge,
  and dynamic exit failed its paired comparison with HOLD.
- **[CONDITIONAL_EV_120D_EXPERIMENT_2026-07-27.md](active/CONDITIONAL_EV_120D_EXPERIMENT_2026-07-27.md)** -
  frozen, research-only decomposition of trade selection into magnitude probability,
  conditional direction, and conservative signed-return quantiles. The untouched
  120-day result must clear every predeclared economic gate before any deployment;
  the experiment does not save or promote serving artifacts.
- **[CONDITIONAL_EV_120D_RESULTS_2026-07-27.md](active/CONDITIONAL_EV_120D_RESULTS_2026-07-27.md)** -
  completed 23,038-decision run: magnitude ranking and return quantile calibration
  were useful, conditional direction remained coin-flip, the conservative policy
  correctly emitted zero trades, every promotion gate failed, and no artifact was
  deployed.
- **[TRADE_POLICY_HEADS_120D_RESEARCH.md](active/TRADE_POLICY_HEADS_120D_RESEARCH.md)** -
  isolated purged walk-forward LONG, SHORT, and ACT/SKIP research lane; post-cost
  basis-point labels, non-overlapping economic evaluation, sequential laptop-safe
  fitting, research-only artifacts, commands, and measured smoke validation.
  Dynamic exit remains excluded under its frozen closure record.
- **[TRADE_POLICY_HEADS_120D_RESULTS_2026-07-27.md](active/TRADE_POLICY_HEADS_120D_RESULTS_2026-07-27.md)** -
  completed 172,801-row run: moderate side-head ranking AUC but coin-flip selected
  direction, negative post-cost EV in every fold and every top-score bucket, failed
  ACT/SKIP promotion, and explicit refusal to wire the research artifacts live.
- **[TRADE_LIFECYCLE_AND_CAPITAL_PRESERVATION_2026-07-27.md](active/TRADE_LIFECYCLE_AND_CAPITAL_PRESERVATION_2026-07-27.md)** -
  lifecycle requirement reconciliation; candidate expiry and entry bounds,
  truthful uncertainty/economics metadata, validated paper order states,
  aggregate capital-preservation governor, emergency paper flattening,
  adversarial validation, and exact evidence-gated/live-execution boundaries.
- **[QUANT_PLATFORM_V1_IMPLEMENTATION_STATUS_2026-07-27.md](active/QUANT_PLATFORM_V1_IMPLEMENTATION_STATUS_2026-07-27.md)** -
  canonical `master` state, shared event/health/risk/governance kernel, isolated Binance
  futures paper accounting and baseline strategy service, reusable research promotion gates,
  paper controls, system-health views, exact completed/partial/not-implemented status, safety
  state, and validation commands.
  This is engineering infrastructure, not evidence of a profitable strategy.
- **[MASTER_CONSOLIDATION_AND_VALIDATION_2026-07-27.md](active/MASTER_CONSOLIDATION_AND_VALIDATION_2026-07-27.md)** -
  branch-consolidation record, production paper-service wiring, safety boundaries, deterministic
  validation matrix, and the evidence-gated items that remain intentionally unimplemented.

## Latest serving and evidence completion
- **[SERVING_EVIDENCE_COMPLETION_2026-07-27.md](active/SERVING_EVIDENCE_COMPLETION_2026-07-27.md)** -
  immutable Clarification-001 candidate eligibility, direct execution-capacity q10, durable
  Ledger V2 spool/replay, own-recorder L2 outcome reconstruction, explicit evidence-run scoring,
  frozen clarification verification, deterministic bundle manifests, self-contained verified
  serving, operator commands, compatibility impact, and remaining evidence/research limits.
  This completes the code path; it does not claim a profitable strategy.

## Audit remediation — COMPLETE_TRADE_FORECAST_V1 (rebuild required before any M0)
- **[AUDIT_REMEDIATION_2026-07-26.md](active/AUDIT_REMEDIATION_2026-07-26.md)** — external audit of
  `b7306cb` found 14 defects letting the Complete Trade Forecaster manufacture apparent
  selectivity. **12 fixed, 1 contained, 1 explicitly deferred**, with a regression test per defect
  (`python -m backend.trade_forecast.test_audit_fixes`, 60 checks). Headline fixes: future targets
  no longer run past contract expiry (a 30s-left decision was carrying BTC prices from 90s AFTER
  settlement); `exposure_id` makes M0 score one deployable action per market moment instead of
  every side x quantity candidate (395 rounds vs 24,996 rows); the M0 ranking label and realized
  column now describe the SAME plan; quote survival is size-aware; missing live features yield NO
  FORECAST instead of a neutral value; evidence logging is append-only and monitored; loaders pin
  their artifact under `BTC_FREEZE_MODEL`; sizing gates on q10 capacity, not the median.
  **One correction to the audit:** its proposed `resolution_source LIKE 'official:%'` filter would
  have matched ZERO rows (the export stores bare venue values; the prefix is added downstream), so
  the frozen-allowlist alternative was used plus a fail-loud guard. **The dataset must be rebuilt
  before any number from this lane means anything** - `load_verified_dataset` already refuses the
  stale one.

- [Complete Trade Forecaster V1 (2026-07-26)](active/COMPLETE_TRADE_FORECAST_V1_IMPLEMENTATION_2026-07-26.md) - executable entry, BTC/share path distributions, full-depth capacity, causal plan optimizer, strict M0 gates, dedicated DuckDB ledger, shadow UI, commands and validation evidence.
- [1,265-Day Multi-Window Expert Implementation (2026-07-26)](active/LONG_WINDOW_1265D_EXPERT_IMPLEMENTATION_2026-07-26.md) - true OHLC, monthly data gates, artifact identity, W90/W400/W1265 experts, purged OOF, sample-budget experiments, TCN sampling, forward shadow scoring, run order, and remaining evidence-gated work.

## Frozen preregistration — Binance lane (M0 not yet runnable)
- **[PREREG_BINANCE_VOLATILITY_MOMENTUM_V1.md](active/PREREG_BINANCE_VOLATILITY_MOMENTUM_V1.md)** —
  frozen 2026-07-26, sha256 `0973744b7365…` (in `PREREG_HASH.txt`). One instrument, one cadence,
  one horizon: can a causal volatility-expansion + cross-venue-flow state select 5-minute BTCUSDT
  perp longs/shorts that are positive **after** spread, fees, latency and slippage?
  **Section 0 is a binding data-admissibility contract: a feature is available at `recv_ts`, never
  `exch_ts`.** Binance perp WS serves only `bookTicker` from this host — `aggTrade`/`markPrice`
  deliver nothing — so perp trade flow, basis, OI and funding arrive by REST with ~54s lag and are
  **Class B (slow state only, never lead-lag)**. M0 is a single predeclared composite mechanism with
  frozen thresholds (Q5−Q3 ≥ 2.0 bps, 4-cell BH family, explicit chance-monotonicity control
  inherited from the conditional-stopping closure). **M0 requires ≥4 weeks of collector uptime and
  cannot run today** (0 rows collected); an optional backfill pre-screen has **kill-only** authority
  and can never be cited as a pass.
- **Collector:** `backend/venues/multi_venue_recorder.py` — synchronized event-time capture across
  Binance spot/perp, Bybit, Coinbase with `exch_ts`/`recv_ts`/`seq`, per-venue reconnect isolation,
  measured clock drift (push streams only), 9/9 stream-health check, and an offline `--selftest`
  (26 checks). Enforces the admissibility contract **in the data, not by convention**: every row
  carries `source_mode` (`WS`/`REST_POLL`) and REST rows carry `poll_id`, so Class A and Class B are
  separable in SQL alone. Per-5-minute-episode health is persisted to `venue_episodes`
  (`stream_counts`, `streams_live`, `max_ws_age_ms`, `max_rest_age_ms`, `reconnects`, `qualifying`,
  `exclusion_reason`); stalls **materialise as excluded rows** rather than vanishing, and WS/REST
  feature ages are tracked separately so a ~54s poll lag cannot mask a healthy 20ms feed.
  `--report` states **uptime and qualifying coverage as different numbers** — only the second
  advances the promotion contract. Two measured venue facts are locked in: perp `aggTrade`/
  `markPrice` are REST-only (Class B), and Binance **spot** `bookTicker` carries no exchange
  timestamp at all, so its `exch_ts` is NULL and `recv_ts` is its only honest time.
- **Admissibility gate:** `backend/venues/venue_admissibility.py` — **the only sanctioned path from
  `venue_events` to a decision feature** (`--selftest`: 22 checks). Enforces two invariants that
  prose cannot: (1) **REST backlog is prohibited, not merely filterable** — `poll_id <= 1` is a
  reconnect backfill of up to 1,000 trades with measured ages of **255–334s**, which aggregated
  naively would collapse minutes of history into the first live decision window as a fictitious
  flow impulse; a REST event is eligible only at `poll_id >= 2`, on its *first* observation of that
  `seq`, with `recv_ts <= decision_ts` and age `<= CLASS_B_MAX_AGE_S` (**frozen at 60s on
  2026-07-26, before any production row existed**). (2) **Timestamp bases may not be mixed in
  lead-lag** — every row carries a derived `timestamp_basis` (`EXCHANGE_TIME` / `RECEIVE_TIME` /
  `RECEIVE_ONLY` / `POLL_RECEIVE_TIME`) and `require_leadlag()` raises `InadmissiblePairing` on any
  pair without a shared basis, so comparing Binance spot's `recv_ts` against Bybit's `exch_ts` —
  network latency dressed as market leadership — is impossible rather than merely discouraged.
  `POLL_RECEIVE_TIME` maps to the empty set: a polled stream can carry slow state, never leadership.
  Also enforces **stable natural event identity**: `event_key` comes from the venue (trade id /
  update id / publication time), never a poll-local counter, which would restart at 1 in a fresh
  process and fail to recognise a re-fetched observation; a polled row without one is recorded but
  barred from features. And **receive-basis lead-lag features may not be named `venue_lead`** —
  `leadlag_feature_name()` permits only `observer_time_lead` / `collector_arrival_lead`, because
  receive order also contains routing, publication latency, batching, scheduling and reconnect
  state. Gates live in SQL so they cannot be bypassed by post-filtering a DataFrame.
- **Clarification records** (separately hashed, protocol file untouched):
  [`PREREG_BINANCE_V1_CLARIFICATION_001.md`](active/PREREG_BINANCE_V1_CLARIFICATION_001.md)
  `12bf5e1e5829d320…` completes `CLASS_B_MAX_AGE_S = 60.0`, the limit section 10 names but leaves
  unvalued; [`PREREG_BINANCE_V1_CLARIFICATION_002.md`](active/PREREG_BINANCE_V1_CLARIFICATION_002.md)
  `320631b2a83aaaca…` binds the receive-basis interpretation rule; and
  [`PREREG_BINANCE_V1_CLARIFICATION_003.md`](active/PREREG_BINANCE_V1_CLARIFICATION_003.md)
  `05e3ab773b80e81b…` binds the nine-stream denominator, feed-silence semantics and strict
  continuity. All were declared 2026-07-26 with **0 production rows and no analysis run**, so
  they complete rather than amend. Revising any of them after an M0 result invalidates the experiment.
- **[COLLECTOR_DEPLOYMENT_RUNBOOK_2026-07-26.md](active/COLLECTOR_DEPLOYMENT_RUNBOOK_2026-07-26.md)** —
  executable handoff for whoever holds Oracle shell access: admin-token security procedure
  (generate, `.env` at `chmod 600`, `EnvironmentFile=`, **verify the token is absent from
  `journalctl`**), the 62-column recorder restart, the `btc-venues.service` unit with a
  `--selftest` `ExecStartPre` gate, per-step pass/fail checks, and a completion record that captures
  the `collection_start_ts` where the evidence clock actually starts. **It starts the clock; it does
  not shorten it.**

## CLOSED — dynamic-exit lane (do not reopen without new information)
- **[CONDITIONAL_STOPPING_V1_CLOSED_2026-07-26.md](archive/CONDITIONAL_STOPPING_V1_CLOSED_2026-07-26.md)** —
  closure record. The one permitted conditional-stopping experiment was **frozen, run to its M0
  gate, and closed without fitting a single model.** M0 required observable state to stratify; the
  only strict pass came in **1 of 28** searches, exactly the chance rate (P(≥1 by chance) = 37.5%),
  and the analysis script's automated PASS was **overturned** before any decision. The one real
  effect (`net_pnl`, +12–21pp in 4/4 cells) points *toward holding* — it endorses the incumbent.
  Protocol integrity verified at closure: the frozen prereg's SHA-256 is unchanged, and the file was
  deliberately left unedited (not even a banner) to preserve it. Artifacts:
  `active/PREREG_CONDITIONAL_STOPPING_V1.md`, `active/PREREG_HASH.txt`,
  `active/COND_STOPPING_M0_2026-07-26.md`, `backend/research/cond_stopping_m0.py`.
  **The distinction it establishes:** a temporary profitable exit frequently exists *in hindsight*;
  no causally observable stopping state has shown that taking it beats holding.

## Latest completed strategy test
- **[STOPPING_BASELINES_2026-07-25.md](active/STOPPING_BASELINES_2026-07-25.md)** -
  the pre-declared gate in front of the dynamic-exit lane: does ANY frozen causal stopping policy
  beat holding to settlement? Seven policies (first +1c/+2c, persist-2, momentum-reversal,
  timeouts, random control), causal execution (decision at quote i fills at i+1), entry at ask +
  fee, exits at bid - fee. **Every policy at both entry checkpoints is WORSE than HOLD, all with
  negative lower bounds and 0/4 positive weeks.** Meanwhile the hindsight ceiling is +19.0c /
  +10.7c - which is exactly what the "90% of rounds have a profitable exit" statistic measures.
  **The lane stops here: no ML, no survival model, no RL.** The phenomenon lives in the path, not
  in any observable state available beforehand.
- **[HEAD_CALIBRATION_2026-07-25.md](active/HEAD_CALIBRATION_2026-07-25.md)** -
  Priority-1 test: are the app's DEPLOYED probabilities calibrated live? (21d Oracle, one row per
  round; official-only corrected n=6,725). **P(Hold) is over-confident by 6.7pp** (predicted 96.1%
  vs realized 89.3%; its
  95-100% band holds 82% of all rounds and realizes 93.4%) - a ~6.7c bias in fair value, about
  **seven times the frozen rule's entire +0.90c edge**, always in the optimistic direction.
  **Champion action tiers are INVERTED** (PAPER held 69.4% vs WAIT 89.6%) - they rank cheapness,
  not confidence, and fail the monotone stratifier rule. **Flip risk (BSS +0.002) and the $20 shock
  head (BSS -0.013) carry no usable information.** Recalibrate before any head is used in a policy.
  Also documents a measurement bug found and fixed inside the test itself.
- **[LATE_LEADER_RECONCILIATION_2026-07-25.md](active/LATE_LEADER_RECONCILIATION_2026-07-25.md)** -
  settles the ledger (+0.90c) vs replay (-0.07c) discrepancy round by round. Verdict: **neither
  implementation is buggy - the rule is latency-fragile.** ~0.41c of the gap is round selection (the
  live rule only fires when a <=5s bridge quote exists; the 532 rounds it declined average -2.76c);
  ~0.56c is **entry timing** - the ledger enters 0.8s earlier at an ask 0.64c cheaper, because the
  leader's ask climbs toward $1 as the clock runs out. Leader definition agrees 99.6% of the time.
  **The rule loses ~0.6-0.8c per second of delay against a total edge of +0.90c: one second of
  latency consumes all of it.** Explains the offline->live decay, the maker loss (-9.53c = resting
  is maximum delay), and why the 90%-profitable-exit is uncapturable.
- **[ORACLE_CAPACITY_TEST_2026-07-25.md](active/ORACLE_CAPACITY_TEST_2026-07-25.md)** -
  answers the capacity question: **there is no size at which the rule is a business.** EV falls
  monotonically with size (1sh ~0c, 25sh -0.26c, 250sh -1.48c / -$8,071) because the first depth
  band beyond the top level costs ~1c while the entire gross edge is under 1c.
- **[STRUCTURAL_EDGE_HUNT_2026-07-25.md](active/STRUCTURAL_EDGE_HUNT_2026-07-25.md)** -
  two STRUCTURAL (non-conditional) hunts on 14,226 settled 5m rounds / 3.78M executable ticks.
  **(1) Complement arbitrage: NO EDGE** — the book crosses into guaranteed profit in 0.0006% of ticks
  (21 of 3.78M, 3 rounds), and the ~36c mean "locked profit" proves those hits are **stale/collapsed
  book artifacts, not fillable prints** (a real arb would be many small 0.5–2c crossings, not 20 of 21
  implausibly huge ones) — independent evidence for why the ≤5s freshness + complement-sanity gates
  matter. **(2) Next-round opening drift: CLEAN KILL** — continuation −2.74c, reversal −2.08c, random
  −1.98c (n=13,018 pairs); all three land at ≈ minus the spread+fee, the signature of an efficient
  book. The boundary-lag species does NOT generalize from the expiry boundary to the round open.
- **[ECONOMIC_ALPHA_ENGINES_AND_COMPLETE_SET_ARBITRAGE_V1_2026-07-28.md](active/ECONOMIC_ALPHA_ENGINES_AND_COMPLETE_SET_ARBITRAGE_V1_2026-07-28.md)** -
  implements the research-only `POLY_COMPLETE_SET_ARBITRAGE_V1` forward scanner without changing
  the historical NO-EDGE verdict. It uses current per-token fee and market-rule endpoints,
  synchronized full L2 ladders, exact equal-size VWAP, capacity search, gap duration and
  250/500/1000 ms pair/failed-leg stress in a separate DuckDB. Current live smoke found zero gaps;
  promotion is hard-blocked until measured operating costs, real two-leg fills, 500 candidates,
  eight weeks and the complete locked robustness gate exist. Also records the economic-engine
  roadmap and explicitly distinguishes implemented work from future campaigns.
- **[POLYMARKET_STRUCTURAL_EDGES_AND_MODEL_STRADDLES_2026-07-04.md](active/POLYMARKET_STRUCTURAL_EDGES_AND_MODEL_STRADDLES_2026-07-04.md)** -
  fee-aware complement-arbitrage and next-round drift tests, five-model OOS straddle selectors, and the
  restart-safe sequential opposite-side paper strategy. This is a historical research record: the
  later executable evidence in `STRUCTURAL_EDGE_HUNT_2026-07-25.md` rejects complement arbitrage
  and opening drift, and no straddle is approved for real money.
- **[VIRTUE_COMPLEXITY_LATE_LEADER_2026-07-04.md](active/VIRTUE_COMPLEXITY_LATE_LEADER_2026-07-04.md)** -
  the Kelly/Malamud/Zhou "virtue of complexity" recipe applied to late-leader fair value (30 days of
  kachoio executable asks, ridge/poly/RFF up to 1,500 features, purged walk-forward, mandatory nulls).
  **Result: the shuffled-label null trades at the SAME EV as the real models (+3.41c vs +3.43c) and the
  no-model baseline (+3.05c, LB +1.55c) beats most models — the late-leader edge is a STRUCTURAL INTERCEPT
  (constant average underpricing), not conditional alpha.** Fourth independent confirmation that the ask is
  the sufficient statistic. Operator ruling recorded: no complexity head; the frozen rule stays small and
  dumb; complexity is reserved for recorder-gated execution targets; next proof = live replication at n≥500.
- **[PAPER_STRATEGY_LAB_2026-07-04.md](active/PAPER_STRATEGY_LAB_2026-07-04.md)** -
  the single reference for ALL 14 auto-trading paper strategies: the frozen LATE_LEADER_30S_V1 rule (+its
  promotion gate), the edge-candidate ladder (15s/60s/15m/MAKER-at-bid), the 3 dead-strategy replications,
  the model-gated variants (fade/straddle/ride/cheap-SAFE/shock-sniper), frozen specs, honest accounting
  (real asks/bids, fees every leg, BTC-at-entry/exit in PYTH, explicit denominator caveats), and the 📒 Trades
  tab (5m/15m horizon tabs + per-strategy filter chips). The 07-04 **addendum** records the row-by-row
  accounting validation (Pyth≠Binance; stops gap through the visible bid — correct, not a bug), the first
  live-day snapshot (frozen rule swung −60.8c at n=35 — why the n≥500 gate exists), five defects found+fixed
  (TDZ blank-UI, relative-URL fetch, straddle orphan floor, false "no entries", missing lab row), and the
  stale-backend verification recipe (port-owner start time vs file mtime).
- **[FULL_1500D_RETRAIN_RUNBOOK_2026-07-03.md](active/FULL_1500D_RETRAIN_RUNBOOK_2026-07-03.md)** -
  historical 1,500-day target contract, 16GB-laptop safeguards, disk/time expectations,
  incumbent-to-candidate atomic swap behavior, operator steps, completion evidence and mandatory
  post-training evaluation. **Current executable state differs:** `start.bat` requests 1,265 days,
  the current research-matrix manifest contains 360 days, and no 1,265-day or 1,500-day completion
  marker exists. Do not describe the serving bundle as a 1,500-day model.
- **[CODEBASE_INTEGRITY_AUDIT_2026-07-04.md](active/CODEBASE_INTEGRITY_AUDIT_2026-07-04.md)** -
  latest launch, promotion, backtest and paper-ledger integrity fixes plus remaining risks.
- **[CODEBASE_AUDIT_2026-07-02.md](active/CODEBASE_AUDIT_2026-07-02.md)** -
  repository-wide compile, build, contract, persistence, feed-sync, UI and documentation audit. Records
  the late-Pyth/cross-feed correctness fixes, restored per-base-model analytics, lightweight model
  compatibility contract and verification evidence. Its former 400-day next-start warning is historical;
  use the current manifest and launcher values recorded above rather than inferring a completed long-window
  model from an old runbook.
- **[PREDICTION_HEADS_DATA_SPLIT_AND_TEST_LEDGER_2026-07-02.md](active/PREDICTION_HEADS_DATA_SPLIT_AND_TEST_LEDGER_2026-07-02.md)** -
  classifies every proposed prediction head as **Category A (existing data)** vs **Category B (forward/live
  recorder)** vs **Composition**, with my one-line opinion on each of the 30 latest heads (~8 A / ~19 B / ~3 C).
  Includes the **complete Test Ledger** — every test we ran (26 entries), its method, result, and evidence doc —
  and my seven on-the-record opinions (direction is dead; the leader anomaly is likely a latency race; ~75% of
  remaining ideas are forward-gated; killing false findings was the real win). Answers "what can be tested now vs
  needs forward data" definitively.
- **[APP_ENHANCEMENTS_AND_TESTS_CATALOG_2026-07-02.md](active/APP_ENHANCEMENTS_AND_TESTS_CATALOG_2026-07-02.md)** -
  master catalog of **every** recommendation, enhancement head/feature idea (~60, merged from both operator
  lists + mine), and testing idea for the app — each tagged DONE / TESTABLE-NOW / RECORDER-GATED / COMPOSITION /
  DEAD. Includes the current honest state, the 12-point dataset audit checklist, the target decision-tree app
  design, the prioritized build order, and the guardrails (P(Hold) leader-only, look-ahead leaks, round-level
  nulls, trade-price≠ask). Start here for the roadmap of what to build/test next and why.
- **[ROUND_STATE_DECISION_PANEL_2026-07-02.md](active/ROUND_STATE_DECISION_PANEL_2026-07-02.md)** -
  implemented 5m/15m SHADOW decision support for future anchor-cross risk, remaining
  $20/$50/$100 shocks, next-three-round opportunity, path type and executable quote status.
  Includes every purged held-out metric, serving gate, UI/API behavior and recorder boundary.
- **[HF_EDGE_ROBUSTNESS_2026-07-02.md](active/HF_EDGE_ROBUSTNESS_2026-07-02.md)** -
  null + stability tests that **corrected** the HF trade-edge headline. The "+27% P(Hold) edge" is **NOT a P(Hold)
  edge**: a shuffle-null (P(Hold) permuted) still returns +25.7% ≈ real, so P(Hold) adds ~nothing. The real,
  P(Hold)-independent signal is structural — **buying every leader wins 65.6% at 0.574 → +14.3% ROI (leaders
  underpriced in the trade data)**; pipeline consistent (invert loses −24.9%). Executed-trade only, March-only,
  suspicious vs the barbell book — validate on live /book, not a P(Hold) result.
- **[HF_TRADE_EDGE_PIPELINE_2026-07-02.md](active/HF_TRADE_EDGE_PIPELINE_2026-07-02.md)** -
  the 4-script HF trades pipeline (token map → snapshots → P(Hold) backfill → edge). Headline +27% is corrected by
  the robustness doc above (P(Hold)-independent; leaders-underpriced; fillability unproven). Companions:
  `HF_TRADE_EDGE_ANALYSIS`, `HF_TRADES_TOKEN_MAPPING`, `HF_POLYMARKET_DATASET_AUDIT` (orderbook KILL).
- **[VWAP_BOLLINGER_PATH_RESEARCH_2026-07-02.md](active/VWAP_BOLLINGER_PATH_RESEARCH_2026-07-02.md)** -
  360-day causal 5m/15m test of rolling VWAP, Bollinger state, mechanical support/resistance,
  and combined FADE/RIDE contexts. VWAP alone is flat, Bollinger adds small touch-timing lift,
  ORB owns the line-cross lift, and every proposed P(Hold) veto is rejected. No live promotion.
- **[ROUND_ORB_AND_SYSTEMIC_ABSORPTION_RESULTS_2026-07-02.md](active/ROUND_ORB_AND_SYSTEMIC_ABSORPTION_RESULTS_2026-07-02.md)** -
  360-day causal round-ORB and 180-day cross-asset PCA absorption tests. ORB adds a narrow 15m
  line-cross lift but fails as a P(Hold veto; systemic absorption worsens every move/drop model and
  retained-call metric. Both remain outside production.
- **[ALL_MODELS_PREDICTIONS_AND_FEATURES_2026-07-02.md](active/ALL_MODELS_PREDICTIONS_AND_FEATURES_2026-07-02.md)** -
  canonical code-derived inventory of active, filtered, shadow, gated, disabled and research-only
  models; targets, algorithms, feature sets, artifacts and user-facing output ownership. Includes the
  active 69-feature mask, excluded 67 features and corrections to stale model claims.
- **[FREE_DATA_SOURCING_AND_RECORDER_STATE_2026-07-02.md](active/FREE_DATA_SOURCING_AND_RECORDER_STATE_2026-07-02.md)** -
  live tests of the free data sources (Binance bookDepth = 30s aggregate, not L2; Polymarket CLOB /book works but is
  live-only; /prices-history too sparse for 5m). Confirms the full-book+P(Hold)+depth+settlement recorder is ALREADY
  built (live_btc_updown_recorder + Codex l2_recorder) — the gap is operational (run them), not code.
- **[BOOKDEPTH_LIQUIDITY_PROBE_2026-07-02.md](active/BOOKDEPTH_LIQUIDITY_PROBE_2026-07-02.md)** -
  causal probe of free Binance bookDepth liquidity features vs an rv baseline. NEGATIVE: no lift on big-move
  (0.747→0.747) or big-drop (0.707→0.706); 30s aggregate depth is redundant with realized vol. Do not wire.
- **[BOOKDEPTH_VETO_PROBE_2026-07-02.md](active/BOOKDEPTH_VETO_PROBE_2026-07-02.md)** -
  bookDepth's "second chance" as a shadow VETO/regime layer on 12k real P(Hold)≥0.93 snapshots. Also NEGATIVE:
  held% flat across liquidity regimes; vetoing VACUUM removes 90 bad vs 2,078 good. Dead 3 ways — drop entirely.
- **[POLYMARKET_EXACT_DEPTH_AND_QUEUE_SIMULATION_2026-07-01.md](active/POLYMARKET_EXACT_DEPTH_AND_QUEUE_SIMULATION_2026-07-01.md)** -
  public full-L2 recorder, deterministic book reconstruction, exact size-specific taker VWAP,
  conservative/base/optimistic maker queue replay, reconnect boundaries, DuckDB schema, runners and
  promotion gates. Live protocol smoke test passed; queue rank remains an estimate until calibrated.
- **[TP50_SL10_WALKFORWARD_AUDIT_2026-07-01.md](active/TP50_SL10_WALKFORWARD_AUDIT_2026-07-01.md)** -
  five-era expanding walk-forward and policy-overfitting audit. Fixed TP50/SL10 remains positive in every
  5m/15m era at the $2 BTC-proxy cost and is more stable than validation-selected switching. Promotes the
  rule only to frozen PAPER/quote replay because it is not Polymarket share PnL.
- **[POLYMARKET_SHOCK_SHARE_REPLAY_RESULTS_2026-07-01.md](active/POLYMARKET_SHOCK_SHARE_REPLAY_RESULTS_2026-07-01.md)** -
  causal real-share quote replay after first $10/$20/$30 BTC shocks. Compares MOMENTUM and FADE using
  recorded ask entries, bid exits, fees, 0s/2s latency and settlement. Short round trips are negative;
  no configuration survives confidence and multiple-test gates.
- **[POLYMARKET_MARKET_RESPONSE_TEST_2026-07-01.md](active/POLYMARKET_MARKET_RESPONSE_TEST_2026-07-01.md)** -
  read-only test of BTC-shock quote response, edge duration, first profitable exit, UP+DOWN complement
  parity, checkpoint calibration and recorded depth. Finds no reliable underreaction/arbitrage edge and
  keeps model-edge results inconclusive because only 29 trustworthy settled rounds span two days.
- **[MODEL_RESULTS_INTERPRETATION_AND_NEXT_PREDICTIONS_2026-07-01.md](active/MODEL_RESULTS_INTERPRETATION_AND_NEXT_PREDICTIONS_2026-07-01.md)** -
  canonical plain-language map of what every specialist head means, how to interpret metrics after many
  model tests, what is usable/rejected/blocked, which predictions to build next, and which public data
  sources offer genuinely new information. Includes the recommended small-ensemble decision flow and
  staged accuracy/profit improvement plan.
- **[ROUND_STATE_AND_STOPPING_RESULTS_2026-07-01.md](active/ROUND_STATE_AND_STOPPING_RESULTS_2026-07-01.md)** -
  causal 180-day test of in-round side-flip risk, touch-to-settlement conversion, late shocks,
  opportunity drought, path-state timing, and validation-selected TP/SL exits. Retains several risk
  heads for shadowing and finds a promising BTC-path TP50/SL10 policy, but explicitly blocks promotion
  until executable Polymarket quote, fill, fee and settlement replay confirms net expectancy.
- **[EXISTING_DATA_PATH_DYNAMICS_RESULTS_2026-07-01.md](active/EXISTING_DATA_PATH_DYNAMICS_RESULTS_2026-07-01.md)** -
  existing-history-only test of competing first-touch side/time, continuation, retracement depth,
  next-round opportunity arrival, flow bursts and controlled spot/perpetual propagation. Retains 5m
  first-touch, round-trip timing and flow-heat shadows; rejects continuation and venue-lead claims.
- **[DECISION_HEAD_RESEARCH_RESULTS_2026-07-01.md](active/DECISION_HEAD_RESEARCH_RESULTS_2026-07-01.md)** -
  causal 180-day evaluation of time-to-touch, first-barrier order, reversal timing, excursion quantiles,
  regime transition, cascade proxy, model-failure filtering, and recorder-backed EV/fair-price/exit/fill.
  Retains touch timing and 5m volatility-risk heads; rejects fade/failure heads; quote economics remain blocked.
- **[ANCHOR_ROUNDTRIP_180D_RESULTS_2026-07-01.md](active/ANCHOR_ROUNDTRIP_180D_RESULTS_2026-07-01.md)** -
  causal 180-day test of the 5m/15m anchor, reversal, hold, path and timing strategy. The requested fade
  trade is rejected at 41.97% first-entry wins; activity, range and P(Hold) remain quote-gated shadow
  candidates. Includes all models, features, labels, metrics, limitations and promotion requirements.

## ⭐ master reference (read this first)
- **[PROFITABILITY_AND_BETTING_VALIDATION_2026-07-01.md](active/PROFITABILITY_AND_BETTING_VALIDATION_2026-07-01.md)** —
  latest profitability audit and current source of truth. Retracts the leaked fade scores, separates snapshot
  calibration from independent entries, adds exact taker-fee/quote requirements, and defines the PAPER-only
  promotion gate. This overrides conflicting same-day claims below.
- **[PROJECT_MASTER_REFERENCE_2026-06-30.md](active/PROJECT_MASTER_REFERENCE_2026-06-30.md)** — the single
  canonical reference: every model in the app, every experiment (worked + failed, with *why*), how the
  system works end-to-end, and all forward plans. Ties the scattered docs together; start here.
- **[FADE_ROUNDTRIP_ENGINE_2026-07-01.md](active/FADE_ROUNDTRIP_ENGINE_2026-07-01.md)** — historical v4 fade
  implementation record. Its performance table is retracted by the July 1 profitability audit; v5 is causal
  and fail-closed pending retraining.
- **[REVERSAL_STRATEGY_BACKTEST_2026-07-01.md](active/REVERSAL_STRATEGY_BACKTEST_2026-07-01.md)** — historical
  v4 research output. The touch-context scores and proxy-profit wording are retracted; rerun only with the
  corrected causal/ambiguous-bar logic.
- **[PATH_CHAMPION_LIFT_2026-06-30.md](active/PATH_CHAMPION_LIFT_2026-06-30.md)** — does the path play
  improve champion decisions? **WATCH** — a real P(Hold)-independent risk signal (matched +4.2pp, p=0.000)
  but not a binary filter; now shadow-logged to earn a forward holdout.
- **[IMPACT_REVERSION_PROBE_2026-06-30.md](active/IMPACT_REVERSION_PROBE_2026-06-30.md)** — does market
  impact/absorption predict reversal/big-drop? **NEGATIVE** even after a corrected (fitted-scale, conditional)
  rebuild — the effect lives sub-second; gated on the L2 recorder.

## active/ — the living documents (read these)
- **[V5.md](active/V5.md)** — the current improvement plan: class-balanced loss (shipped
  with the 2026-06-12 retrain), new-feature roadmap, speed levers, the 90-day experiment,
  and the explicit accuracy-first constraint. **Start here.**
- **[MODEL_ROSTER_PLAN.md](active/MODEL_ROSTER_PLAN.md)** — the roster surgery plan
  (remove Kronos/SGD/FSR-PPO, TCN's seat decision), the additions that buy precision
  (persistence model, p_up pricing), GPU speed levers, and the honest ladder to the
  95%-precision tier. PLAN ONLY — actions gated on the v5 live run.
- **[V3_CHANGES_AND_AUDIT.md](active/V3_CHANGES_AND_AUDIT.md)** — the complete audit
  trail: every bug found (incl. the dual-semantic `hit` class, venue mixing, backtest
  contamination, the smoke-test incident), every fix, every retraction. The project's
  honest memory — §5a through §5am and counting.

## reference/ — how the system works
- **[system_architecture.md](reference/system_architecture.md)** — components, data
  flows, model ensemble structure.
- **[UI_GUIDE.md](reference/UI_GUIDE.md)** — dashboard panels (see also the in-app
  guides: `public/guide.html` and `public/polymarket-betting.html`).

## latest additions
- **[QUANT_RESEARCH_100_CEILING_BREAK_IDEAS_2026-06-30.md](active/QUANT_RESEARCH_100_CEILING_BREAK_IDEAS_2026-06-30.md)** -
  evidence-ranked backlog of 100 non-duplicate experiments drawn from foundational quant research.
  Covers event-time microstructure, cross-venue price discovery, path/volatility, options, on-chain,
  macro, Polymarket pricing, labels, research controls, and execution, with free-data feasibility and
  a falsification gate for every idea.
- **[CODE_AND_LOGIC_VALIDATION_2026-06-30.md](active/CODE_AND_LOGIC_VALIDATION_2026-06-30.md)** -
  latest full audit: restored live large-trade features, corrected exact-dollar path targets and stable
  round plans, hardened model saves/preflight, fixed recorder anchor/volatility/token logic, repaired
  seconds-to-milliseconds P(Hold joins, filtered corrupt anchor rounds, and recorded all validation results.
- **[PATH_FORECASTER_TRADE_PLAN_HEAD_2026-06-30.md](active/PATH_FORECASTER_TRADE_PLAN_HEAD_2026-06-30.md)** -
  canonical Layer-2 path-head design and exact-dollar 360-day results. Defines what the path plan predicts,
  how it freezes at the round open, its hot-reload/retrain lifecycle, and why it cannot choose UP/DOWN.
- **[CEILING_INVESTIGATION_AND_PATH_FINDING_2026-06-30.md](active/CEILING_INVESTIGATION_AND_PATH_FINDING_2026-06-30.md)** -
  measured direction-ceiling investigation and constructive path/magnitude finding across model families,
  volatility estimators, futures flow, conditional pockets, and early-exit research.
- **[MICROSTRUCTURE_PARITY_BUG_AND_FIXES_2026-06-28.md](active/MICROSTRUCTURE_PARITY_BUG_AND_FIXES_2026-06-28.md)** -
  train/serve parity investigation for CVD, VPIN, and large-trade flow, including the June 30 correction
  that finally restores both selected large-trade features live.
- **[FULL_360D_RETRAIN_IMPLEMENTATION_2026-06-22.md](active/FULL_360D_RETRAIN_IMPLEMENTATION_2026-06-22.md)** -
  implementation record for the one-time 360-day retrain and freeze lifecycle: completion marker,
  sequential fail-closed specialist heads, disk-backed 69-feature sequences, representative
  direction-model sampling for a 16 GB laptop, evidence recorder startup, operator commands, and
  post-training verification. It also states the resource compromise and what the retrain does not prove.
- **[POST_TRAINING_EVALUATION_RUNBOOK_2026-06-21.md](active/POST_TRAINING_EVALUATION_RUNBOOK_2026-06-21.md)** -
  locked post-retrain protocol covering completion verification, clean forward sample requirements,
  horizon calibration, 5m/15m retained-call precision, regime/champion shadows, and the Polymarket
  quote+official-settlement profit gate. No model changes are allowed during this measurement window.
- **[PRESTART_VALIDATION_2026-06-21.md](active/PRESTART_VALIDATION_2026-06-21.md)** -
  full no-start audit: compile/import/self-tests, 415 saved artifacts, DuckDB recovery, frontend build,
  150-day matrix validation, the required three-horizon migration retrain, instant-launch protection,
  pre-sequence feature pruning, and corrected 98% out-of-sample boundary accounting.
- **[SETTLEMENT_INGESTION_2026-06-21.md](active/SETTLEMENT_INGESTION_2026-06-21.md)** -
  restart-safe official Polymarket outcome ingestion, 364/364 backlog recovery, automatic recorder startup,
  and corrected one-entry-per-round edge accounting. Settlement plumbing is complete; quote accrual is now
  the Phase-0 bottleneck (4 joined rounds, need at least 500).
- **[REGIME_GATE_SHADOW_2026-06-21.md](active/REGIME_GATE_SHADOW_2026-06-21.md)** —
  read-only shadow monitor (`regime_gate_shadow.py`, no live wiring) replaying candidate regime-selection
  gate policies over logged `price_to_beat` rounds. Full regime-era window shows prefer-RANGE/LOW_VOL at
  Wilson-LB 53.5%, but the **recent-250 window dips below 50% (⚠️)** — edge not yet confirmed forward; do
  not promote. Re-run to extend the drift series (`data/regime_gate_shadow_log.csv`).
- **[DUCKDB_METRICS_ANALYSIS_2026-06-21.md](active/DUCKDB_METRICS_ANALYSIS_2026-06-21.md)** —
  full live-log breakdown (`analyze_duckdb_metrics.py`) by day, model_version, and horizon (5m/15m):
  ensemble + price-to-beat tracker + 8 individual models; accuracy, per-class precision, signals, regime,
  confluence. Verdict: everything converges on coin-flip (tracker 50.2%/50.5%); the only faint levers are
  regime-based abstention and a flagged confluence-grade inversion (B>A).
- **[CEILING_BREAK_EXPERIMENTS_2026-06-20.md](active/CEILING_BREAK_EXPERIMENTS_2026-06-20.md)** —
  results of the 5 ceiling-break experiments (`run_ceiling_break_experiments.py`, 30d, 70/30): triple-barrier
  is net-**negative after a 2bps spread** at every horizon; the flow/cross-venue proxy gives no top-bucket
  lift; Exp 3/4 blocked on recorder data; the meta-skip ranks well but mostly re-expresses P(Hold). Verdict:
  the only untested ceiling-break levers are true L2 (record-forward) + Polymarket ask mispricing.
- **[MASTER_STRATEGY_CEILING_BREAK_AND_RECOMMENDER_2026-06-18.md](active/MASTER_STRATEGY_CEILING_BREAK_AND_RECOMMENDER_2026-06-18.md)** —
  **THE forward strategy.** Merges the Final Specialist-Head Plan + Current Truth + ceiling-break levers
  (new data / new labels / new policy) + the Netflix-style Live Market Recommender into one disciplined,
  prioritized, gated build plan. Read this for "how do we improve the app from here."
- **[V10_CONSOLIDATED_MASTER_AND_PROPOSALS_2026-06-18.md](active/V10_CONSOLIDATED_MASTER_AND_PROPOSALS_2026-06-18.md)** —
  **the big picture / where we are.** Consolidates every version (V3→v11), the current architecture,
  and all proposals (built / deferred / gated / rejected) into one index, with the make-or-break gate.
- **[OVERNIGHT_150D_RETRAIN_RESULTS_2026-06-18.md](active/OVERNIGHT_150D_RETRAIN_RESULTS_2026-06-18.md)** —
  full 150d retrain evidence: single-knob + 98/2 split confirmed; heads' held-out 2% test; the
  direction-unprofitable OOS backtest (profit factor < 1 every horizon) + confusion matrices.
- **[CALIBRATION_MONITOR_2026-06-18.md](active/CALIBRATION_MONITOR_2026-06-18.md)** —
  live P(hold) calibration drift report (`calibration_monitor.py`): found ~2-pt top-tier optimism on
  13,972 resolved rounds; overall ECE 0.033, STABLE. Drives the opt-in recalibration overlay.
- **[OVERNIGHT_180D_ALL_MODEL_TRAINING_2026-06-18.md](active/OVERNIGHT_180D_ALL_MODEL_TRAINING_2026-06-18.md)** -
  one-command overnight trigger for 180-day data rebuild, forced standalone-head retraining,
  forced main-ensemble retraining, matrix coverage checks, and expected 8-11 hour runtime.
- **[SPECIALIST_HEAD_CHAMPION_IMPLEMENTATION_2026-06-17.md](active/SPECIALIST_HEAD_CHAMPION_IMPLEMENTATION_2026-06-17.md)** -
  implementation report for the deployable specialist-head system:
  calibrated big-move and big-drop heads, live big-up/big-down confirmation heads,
  activity/range head, probability buckets, champion snapshots, data-gated meta champion,
  quantile reward/risk veto, champion validator, and UI card wiring.
- **[polymarket-specialist-guide.html](../public/polymarket-specialist-guide.html)** -
  current plain-English Polymarket guide for the specialist-head Champion layer,
  $30-$50 big-move logic, 1m-30m horizon coverage, and 180/360-day training expectations.
- **[MODEL_OWNERSHIP_AND_CHAMPION_ENSEMBLE_PLAN_2026-06-17.md](active/MODEL_OWNERSHIP_AND_CHAMPION_ENSEMBLE_PLAN_2026-06-17.md)** -
  practical model ownership map: which specialist ensemble should predict each app output,
  how the champion validator should combine them, and what to build next.
- **[DIRECTIONAL_BIGMOVE_RESEARCH_RUNBOOK_2026-06-17.md](active/DIRECTIONAL_BIGMOVE_RESEARCH_RUNBOOK_2026-06-17.md)** -
  research-only 180-day lane for `big_up`, `big_down`, and path-aware `big_drop`.
  Includes the batch runner, labels, models, outputs, and promotion rules for live-app use.
- **[BTC_180D_RESEARCH_RESULTS_AND_APP_PLAN_2026-06-17.md](active/BTC_180D_RESEARCH_RESULTS_AND_APP_PLAN_2026-06-17.md)** —
  canonical summary of the 180-day BTC research lane: models tested, targets,
  accuracy/error results, quantile coverage, feature health, and proposed app changes
  such as big-move probability, directional big-move labels, quantile bands and trade-room scoring.
- **[MODEL_RESEARCH_CATALOG_AND_APP_PROPOSAL_2026-06-17.md](active/MODEL_RESEARCH_CATALOG_AND_APP_PROPOSAL_2026-06-17.md)** â€”
  full catalog of every tested model family, prediction target, performance category,
  feature group, and proposed live-app promotion plan.
- **[ADVANCED_SEQUENCE_RESEARCH_RUNBOOK_2026-06-17.md](active/ADVANCED_SEQUENCE_RESEARCH_RUNBOOK_2026-06-17.md)** —
  advanced sequence lane for VLSTM, LPatchTST, PatchTST, iTransformer and optional
  Mamba/Mamba2/VSN+Mamba2 research.
- **[FORECAST_360D_RESEARCH_RUNBOOK_2026-06-16.md](active/FORECAST_360D_RESEARCH_RUNBOOK_2026-06-16.md)** —
  standalone 360-day BTC 5m/15m multi-target forecasting lane. It tests price,
  return, high/low/range, volume, direction and big-move targets with strict
  chronological 64/16/20 splits. Research-only; it does not touch live bot models.
- **[MULTIHEAD_UPDOWN_BAKEOFF_RUNBOOK_2026-06-16.md](active/MULTIHEAD_UPDOWN_BAKEOFF_RUNBOOK_2026-06-16.md)** —
  Binance anchor/up-down multi-head bakeoff for BTC fair-value heads and
  Polymarket-style price-to-beat research.
- **[TRAINING_PLAN_DISCUSSION_2026-06-15.md](active/TRAINING_PLAN_DISCUSSION_2026-06-15.md)** —
  current training-window and feature-pruning notes. Main ensemble now trains/predicts on a
  69-feature model mask while preserving the 136-feature app schema.
- **[ENSEMBLE_ENHANCEMENTS_AND_TESTS_2026-06-15.md](active/ENSEMBLE_ENHANCEMENTS_AND_TESTS_2026-06-15.md)** —
  measured outcomes for RF/selectivity, signed quantile bands, dead-feature pruning and the
  current "accuracy vs hygiene" verdicts.

## archive/ — historical (superseded, kept for forensics)
Plans and analyses from v2/v3 development. Numbers quoted in these may use the OLD
`hit`-based grading (retracted 2026-06-11) — trust only sign-truth figures from the
active docs.

| File | What it was |
|---|---|
| V3_ACCURACY_PLAN.md / V3_NOW_VS_LATER.md / CODEX_FIX_PLAN.md | v3-era improvement plans (superseded by V5.md) |
| V2_CONTEXT.md | v2-era context |
| ANALYSIS.md | early deep-dive analysis |
| SIGNAL_BASELINE_2026-06-09.md | signal baseline snapshot |
| CLAUDE_ANTIGRAVITY_IMPORT.md / implementation_plan.md / task.md | imported plans/tasks from other assistants |
| crash_log.txt | an old crash log |

### 2026-07-25 — Oracle deployment evidence
- **[EXECUTABLE_EVIDENCE_AND_ENHANCEMENTS_2026-07-25.md](active/EXECUTABLE_EVIDENCE_AND_ENHANCEMENTS_2026-07-25.md)**
  — CANONICAL. 21 days of live data: `LATE_LEADER_30S_V1` fails its own gate (+0.90c, block-LB
  -0.60c, PF 1.08); 15m static TP-before-SL killed (0 of 2,880 cells positive); the
  opportunity-vs-capturability paradox; four implemented enhancements; ranked backlog.
- **[ORACLE_DATA_TEST_HANDOFF_2026-07-25.md](active/ORACLE_DATA_TEST_HANDOFF_2026-07-25.md)**
  — handoff for a fresh session: what is closed, how to run the tooling, and the ranked list of
  heads/models still worth testing (Tier A = score the 186,985 live head snapshots against
  outcomes; none of it has been done).
- Both docs cover, in one place: the live gate verdict, the 15m kill + null battery, the six
  implemented enhancements (block-bootstrap gate, win-rate demotion, unmeasurable-trigger labels,
  research infrastructure, the Oracle production merge incl. admin auth, and the version-string
  collision fix), the deployment topology, and the ranked Tier A/B/C test plan for new work.
- **[COLLECTOR_INTEGRITY_FIXES_2026-07-26.md](active/COLLECTOR_INTEGRITY_FIXES_2026-07-26.md)**
  — silent evidence-qualification defects found by external review of `8998d5b` and closed:
  a required Class-A stream missing from the health gate (8/8 -> 9/9), stale feeds qualifying,
  the evidence clock starting before any row persisted, episode health counting parsed instead
  of persisted rows, dedup scoped inside the lookback, REST revision identity, connection_id
  conflated with poll_id, writer-task exceptions being swallowed, synchronous REST calls blocking
  the event loop, and 'four continuous weeks' enforced as count+span rather than continuity.
  Regression suite: `backend/venues/test_collector_integrity.py`.
- **[DECISION_LOCKDOWN_AND_CALIBRATION_2026-07-26.md](active/DECISION_LOCKDOWN_AND_CALIBRATION_2026-07-26.md)**
  — BEHAVIOUR CHANGE. `PAPER_BET` is disabled by default (P(hold) is 12pp optimistic at the exact
  0.93 gate that authorized it; the live `PAPER` tier realized 64.0% vs `SETUP` 99.4%), Kelly
  disabled in favour of fixed quantity 1, and frozen artifacts made genuinely immutable across all
  five loaders. Plus the P(hold) recalibration challenger (5m overconfidence +8.70pp -> +0.57pp,
  skill quadrupled) and the head-health monitor. Switches, results and what is deliberately NOT
  wired are listed in the doc.
- **[EXTERNAL_REVIEW_CONSOLIDATION_2026-07-26.md](active/EXTERNAL_REVIEW_CONSOLIDATION_2026-07-26.md)**
  — canonical reconciliation of the latest external GitHub reviews against the newer local code
  and executable research. Separates implemented fixes, evidence-backed rejections, recorder-gated
  work and true remaining priorities; also records the current model/data state and validation suite.
