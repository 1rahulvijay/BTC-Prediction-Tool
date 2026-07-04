# Documentation Index

## Latest completed strategy test
- **[POLYMARKET_STRUCTURAL_EDGES_AND_MODEL_STRADDLES_2026-07-04.md](active/POLYMARKET_STRUCTURAL_EDGES_AND_MODEL_STRADDLES_2026-07-04.md)** -
  fee-aware complement-arbitrage and next-round drift tests, five-model OOS straddle selectors, and the
  restart-safe sequential opposite-side paper strategy. The full replay is queued behind the active
  1,500-day retrain so both jobs do not compete for the laptop's 16 GB RAM.
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
  current 1,500-day training contract, 16GB-laptop safeguards, disk/time expectations, incumbent-to-candidate
  atomic swap behavior, operator steps, completion evidence and mandatory post-training evaluation.
- **[CODEBASE_INTEGRITY_AUDIT_2026-07-04.md](active/CODEBASE_INTEGRITY_AUDIT_2026-07-04.md)** -
  latest launch, promotion, backtest and paper-ledger integrity fixes plus remaining risks.
- **[CODEBASE_AUDIT_2026-07-02.md](active/CODEBASE_AUDIT_2026-07-02.md)** -
  repository-wide compile, build, contract, persistence, feed-sync, UI and documentation audit. Records
  the late-Pyth/cross-feed correctness fixes, restored per-base-model analytics, lightweight model
  compatibility contract and verification evidence. Its former 400-day next-start warning is marked
  superseded by the current 1,500-day retrain runbook.
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
