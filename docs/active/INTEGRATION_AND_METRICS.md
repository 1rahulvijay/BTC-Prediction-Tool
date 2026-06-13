# Integration & Metrics — how the pieces compose, and how we know each one works (2026-06-13)

The tool is NOT one model. It is a **two-layer** system: a direction stack (one ensemble) and
a decision composer (serving). The heads we build (P(hold), P(beat), path, magnitude,
fingerprints) answer **different questions** and are composed at decision time — they are not
averaged into the direction model. This doc is the map: which piece lives where, the metric that
validates each, and the END-TO-END metric for the composed decision (the one that was missing).

**Admission rule (the whole game):** nothing enters Layer 1 or Layer 2 until it clears its own
gate on held-out / sign-truth data. "Proven working" is per-component and measured — never big-bang.

---

## Layer 1 — the DIRECTION STACK (one model)
Per regime (TREND/RANGE/VOLATILE/GLOBAL) × horizon: base seats {xgb, lgb, cat, histgb, lr, TCN}
→ an OOF meta-stacker → **direction + P(up/down)**. Anything that predicts *direction* joins HERE,
as a new **OOF seat in the one stacker** — never as a parallel ensemble.
- Future seats: **kNN setup-fingerprint voter** (after A10), **transformer** (hard-gated on TCN
  showing decorrelated lift), and **P(hold)/P(beat) outputs fed back as features**.
- **Metric:** committed-lean **sign-truth** per horizon/regime, UP/DOWN balance.
  Validators: `sign_truth_scorecard.py`, `diagnose_model.py §1`, `validate_regime_thresholds`,
  per-base-model via `model_verifier.py` (§5ba committed-vote fix). **Gate: 5m ≥ ~56%, balanced.**

## Layer 2 — the DECISION COMPOSER (serving: `price_to_beat.py` + the card)
The heads answer different questions and are composed into the card, each gating/refining a
different facet of the SAME bet:

| Head | Question | Role | Status | Metric / validator |
|---|---|---|---|---|
| Ensemble (L1) | which way? | the lean | live | sign-truth (above) |
| **P(hold)** | already ahead late — will it hold? | the ⚡ late-entry gate | ✅ wired | calibration+AUC+coverage; `train_persistence_model` held-out (AUC 0.747; 0.93→95.3%) |
| **P(beat)** | calibrated P(close ≥ line) | fair value / `p_up` | ⚠ rebuild (leaked §5bs) | AUC+calibration, SIGNAL/NOISE gate; `train_beat_classifier` |
| **Magnitude** | how far? | does it clear the line? | ⚠ rebuild | pinball vs base-q50 + monotonicity; `train_magnitude_quantiles` |
| **Path** | how it travels? | UP_THEN_DOWN/DIRECT/CHOP outlook | ⚠ rebuild | per-class accuracy; `build_path_labels` |
| **Fingerprints** | did similar setups win? | evidence weight | ⚠ rebuild | per-bucket win-rate stratification |
| **Grade** | setup quality | A/B/C | ❌ **inverted (§5br)** | A≥B≥C, n≥100, A-LB>C; `diagnose_model §5` |

## The missing piece — the COMPOSED-DECISION metric (now built, no-train)
Per-component metrics existed; the **composition** had none — the number that says *"when the tool
stacks its gates into one recommendation, does THAT bet win?"* Without it you can pass every part
and lose as a whole. Built as a **read-only scorecard** (no serving change, no schema, no retrain):
`backend/composed_decision_scorecard.py`. It reads gates already persisted on `price_to_beat`
(`our_direction`, `lean_source`, `actionable`, `confluence_grade`, `late_entry`, resolved `hit`)
and computes a **gate ladder**, each row adding one gate:

```
L0  all committed leans            (raw "always bet a side")
L1  + model lean only              (drop the coin-flip fallback)
L2  + actionable (B2 gate)         (drop the gated-out coin-flips)
L3  + late-entry (P(hold) ≥ .93)   (the proven high-precision tier)
```
A healthy composition is **MONOTONE** — win-rate and the **Wilson 95% lower bound** rise as gates
are added, and the top tier's LB clears the **bettable break-even** (~52% = coin-flip + spread).
A point estimate of 60% on n=10 is not an edge; the headline is the top tier's **lower bound**.
It also re-checks the grade stratification (flags the §5br inversion) and labels 5m/15m as the only
real markets (1/3/7/10 are practice mirrors). Run **app-stopped** (DuckDB single-writer); it's in
the morning checklist. Self-tested on synthetic data (`--selftest`).

This is the seed of the V9 "paper-tracked positive EV" gate: the betting layer ships only when this
metric's top tier clears break-even with confidence — not when any single component looks good.

## Two standing validation gates (each earned by a real failure — see V8_ROADMAP)
1. **Too-good = leakage.** 5m is coin-flip; honest 5m AUC ≈ 0.50–0.55. AUC ≫ that ⇒ presumed leaked
   until feature↔label time-alignment is audited. A noise gate does NOT catch leakage. (§5bs)
2. **A stratifier must stratify.** Any A/B/C or confidence bucket must show monotone sign-truth
   (top≥bottom, each n≥100, top-LB > bottom-rate) before it is surfaced or gated on. (§5br)
