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
