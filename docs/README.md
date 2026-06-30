# Documentation Index

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
