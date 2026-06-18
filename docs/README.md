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
