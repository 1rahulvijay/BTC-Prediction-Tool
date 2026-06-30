# Session Master Log — 2026-06-21 (complete record)

**The one document to read for this session.** Everything we did, discussed, measured, decided, built,
and what's left — with links to the detailed sub-docs. Supersedes the earlier
[SESSION_2026-06-21_FINDINGS_STRATEGY_AND_DECISIONS](SESSION_2026-06-21_FINDINGS_STRATEGY_AND_DECISIONS.md)
as the master index.

---

## 0. TL;DR

- **Direction is a coin-flip at every timeframe / hour / day** — reconfirmed ~5 new ways this session
  (ceiling-break experiments, full-history per-horizon, champion_v2 shadow). The ceiling is informational.
- **The product is selective action + P(Hold), not better prediction.** P(Hold) late-entry remains the one
  candidate edge; profit still depends on Polymarket mispricing. Settlement ingestion is now live with
  **364 official outcomes**, but only **4 joined quote rounds**, so the money edge remains unproven.
- **Two things were IMPLEMENTED + validated:** (1) timeframe prune to **{5, 15}** (markets only — 1m/3m/7m/10m/30m removed; the alt session took 1m out too, confirmed by operator 2026-06-22);
  (2) 1m P(Hold) recalibration (fresh-data retrain; per-horizon isotonic tested = wash, reverted).
- **Two external-review claims were CORRECTED by measurement:** meta-skip is *not* a new edge (it re-derives
  P(Hold)); the regime gate is a *marginal/fragile* candidate, not ready for live.
- **Your one action:** the 360-day retrain — just run `.\start.bat` (alt session added a completion-marker lifecycle that defaults to 360d + forces heads; see [FULL_360D_RETRAIN_IMPLEMENTATION](FULL_360D_RETRAIN_IMPLEMENTATION_2026-06-22.md)). The old `BTC_HISTORICAL_DAYS=250` command is superseded.
- **Highest-value next action:** keep the auto-started Polymarket recorder running until at least 500
  quote+official-outcome rounds can be evaluated by `analyze_pm_recorder.py`.

---

## 1. What we discussed (the narrative)

1. **"How to make a profitable Polymarket bot?"** → A bot executes an edge, it doesn't create one. The hard
   gate is proving the edge first. Full spec written: [POLYMARKET_BOT_REQUIREMENTS](POLYMARKET_BOT_REQUIREMENTS_2026-06-21.md).
2. **"Which timeframe is garbage / can we remove it?"** → Built a dedicated analyzer; measured every horizon;
   verdict: keep markets-only **{5,15}**, remove 1/3/7/10/30. (1m was first kept as a feedback clock, then dropped by the alt session + operator on 2026-06-22 for the leanest build.)
3. **"How to break the ceiling in 15m / the CEST evening window?"** → Ran the ceiling-break experiments. No
   historical lever works; the evening-15m cell is a selectivity *watch*, not a ceiling-break.
4. **"Document results, win rate, prediction precision"** → consolidated report written.
5. **External multi-report review** (regime gate / meta-skip / fallback-abstain / etc.) → built
   `champion_v2_shadow.py` to test the proposals; corrected two over-claims.
6. **"Do the 1m recalibration + the timeframe removal + document"** → both implemented + validated this session.

---

## 2. Key measured findings (the numbers)

### 2a. Win rate by timeframe (all-history, 9.6 days) — all coin-flip
| hz | n | win % | Wilson-LB | model-lean win % | model LB |
|---|---:|---:|---:|---:|---:|
| 1m | 6729 | 50.1 | 48.9 | 48.0 | 42.2 |
| 5m | 1454 | 50.1 | 47.5 | 50.6 | 47.0 |
| 15m | 470 | 50.6 | 46.1 | 50.3 | 45.0 |
| (3/7/10/30) | — | 47.8–50.2 | all <50 | — | all <50 |

**Every horizon's committed model-lean Wilson-LB is below 50.** No timeframe beats a coin-flip.
Detail: [TIMEFRAME_VALUE_pyth](TIMEFRAME_VALUE_pyth_2026-06-21.md), [TIMEFRAME_WINRATE_PRECISION_REPORT](TIMEFRAME_WINRATE_PRECISION_REPORT_2026-06-21.md).

### 2b. Prediction precision (ceiling-break, 30d, 70/30)
- **Direction:** AUC 0.50–0.54 (coin-flip).
- **Timing/barrier:** AUC **0.65–0.69** (real ranking) BUT top-5% directional bet is **net-negative after 2 bps**
  (−0.3 to −2.7). Precise on *whether* it moves, useless without the *side*.
- **New-info proxy:** candle 0.660 vs +flow/cross-venue 0.657 → adds nothing.
- **Exp 3/4 (Polymarket market-lag/maker):** still unrunnable — 364 official outcomes, but only 4 joined quote rounds.
Detail: [CEILING_BREAK_EXPERIMENTS](CEILING_BREAK_EXPERIMENTS_2026-06-21.md).

### 2c. Time-of-day (CEST) — one watch-cell
- **15m @ 20:00–24:00 CEST = 61.8%** (Wilson-LB 51.4, >50% on 7/8 days) — the only cell clearing 50. But
  tiny n (6–16/day) + selection bias → **WATCH, not tradeable.** Everything else flat.
Detail: [TIMEFRAME_PERFORMANCE_pyth](TIMEFRAME_PERFORMANCE_pyth_2026-06-21.md).

### 2d. champion_v2 shadow — corrected two external claims
- **Meta-skip:** beats a plain P(Hold) threshold by **+0.8/+0.7 pts** at matched coverage = ~0 →
  **it's P(Hold) re-derived, not a new edge.** Don't build a meta head.
- **Regime gate (RANGE/LOW_VOL, 5m+15m):** pooled 56.7% (LB 50.7) — *marginal PASS*, but neither regime
  clears 50 alone and n≈270 (recent overlaps overall) → **fragile, keep as shadow.**
Detail: [CHAMPION_V2_SHADOW](CHAMPION_V2_SHADOW_2026-06-21.md).

### 2e. P(Hold) calibration
- Overall STABLE (ECE 0.0108); **1m drifting live (ECE 0.0545)** → recalibrated (see §4).
Detail: [CALIBRATION_MONITOR](CALIBRATION_MONITOR_2026-06-21.md).

---

## 3. Timeframe prune — IMPLEMENTED (keep {5,15}, remove 1/3/7/10/30)

> **FINAL KEEP-SET = {5, 15}** (markets only). The code, all heads, trackers, verifiers, analytics, and the
> UI are uniformly {5,15} (arch `2horizon-5-15`), confirmed by import test 2026-06-22. Earlier mentions of
> {1,5,15} below reflect the initial plan before 1m was dropped.

Code-complete + validated (compile + pyflakes + node + import). Activates on the overnight retrain.
~17 serving/training files + UI edited. Full file-by-file plan + the overnight procedure:
**[TIMEFRAME_PRUNE_IMPLEMENTATION](TIMEFRAME_PRUNE_IMPLEMENTATION_2026-06-21.md).**

- **Phase 1 (tracker/UI, no-retrain):** `database.py`, `server.py` (both PTB trackers + verifiers + endpoints),
  `src/main.js` (roster/accuracy/grids/tabs → [1,5,15]; betting cards + replay → [5,15]).
- **Phase 2 (models/datasets, needs retrain):** `model.py` (arch `7horizon`→`3horizon-1-5-15`),
  `features.py`, `keeper_head_training.py`, `train_signed_quantiles.py` (+version bump),
  `train_magnitude_quantiles.py`, `train_beat_classifier.py`, `build_path_labels.py` ({5,15}),
  `build_fingerprints_historical.py`, `analytics.py`, `calibration.py`, `prediction_verifier.py`,
  `model_verifier.py`.
- **Validated runtime-safe:** 1m falls back to the symmetric band (guarded `if _hh in models`); all 4 keeper
  trainers inherit the pruned HORIZONS; no stray 7-horizon list remains in serving/training.
- **Payoff:** ~57% fewer per-horizon head trainings + matrix labels; smaller sequence tensors; cleaner UI;
  no accuracy lost (removed horizons were coin-flip with no market).

---

## 4. 1m P(Hold) recalibration — IMPLEMENTED (with an honest twist)

- **Retrained the persistence head on fresh 5.76M snapshots** (AUC 0.744, keeper lift +0.015) — the actual
  recalibration.
- Implemented per-horizon isotonic as the candidate fix, **measured it = wash** (1m better, 5m/15m slightly
  worse). This proved the global iso was *already* well-calibrated offline (1m ECE 0.0286, not the 0.0545 seen
  live) → **the live drift is a train/serve distribution gap, not a mapping bug.**
- Per "don't wire unproven complexity," **reverted serving to the global iso** (kept per-horizon in the bundle
  as a diagnostic). Files: `train_persistence_model.py` (+ECE diagnostic, version bump), `price_to_beat.py`,
  `analyze_pm_recorder.py`. Verify live post-restart with `calibration_monitor.py`.

---

## 5. New scripts built this session (all read-only, self-tested)
| Script | Purpose |
|---|---|
| `backend/analyze_timeframe_value.py` | per-horizon KEEP/REMOVE verdict (tradeable / edge / feedback / cost) |
| `backend/champion_v2_shadow.py` | tests regime + meta-skip vs P(Hold) baseline (leak-free, round-level) |
| (earlier) `analyze_timeframe_performance.py`, `run_ceiling_break_experiments.py`, `microstructure_recorder.py`, `calibration_monitor.py`, `regime_gate_shadow.py`, `fallback_abstain_shadow.py`, `analyze_pm_recorder.py` | timeframe/time-of-day, ceiling-break, L2 recorder, calibration, shadows, edge proof |

---

## 6. Decisions log

| Decision | Verdict |
|---|---|
| Remove timeframes 1/3/7/10/30; keep {5,15} (markets only) | ✅ IMPLEMENTED ({5,15}, arch 2horizon-5-15) |
| 1m P(Hold) recalibration | ✅ DONE (fresh retrain; per-horizon iso = wash, reverted) |
| Meta-skip CatBoost head | ❌ REJECTED (= P(Hold) re-derived) |
| Fallback-abstain policy | ❌ REJECTED (shadow killed it) |
| Regime gate (RANGE/LOW_VOL) | 🟡 SHADOW (marginal, fragile) |
| Evening 15m (20–24 CEST) | 🟡 WATCH / feature only |
| Triple-barrier timing | FEATURE only (negative after cost) |
| Multi-timeframe direction-stacking | ❌ (stacking coin-flips ≠ signal) |
| Polymarket bot / live execution | ⛔ BLOCKED on edge proof |
| Polymarket official settlement ingestion | ✅ DONE (364/364 backlog resolved) |
| Don't retrain longer for accuracy | ✅ (ceiling; retrain = robustness, not a higher number) |

---

## 7. What's left to implement (priority order)

1. **The overnight retrain** (your action) — `set BTC_HISTORICAL_DAYS=250 & set BTC_FORCE_HEAD_RETRAIN=1 & start.bat`.
   The next boot retrains automatically (arch bump); the force flag also prunes the 4 legacy heads.
2. **Keep the Polymarket recorder running** — settlements are fixed, but quote coverage is 4 rounds; need
   ≥500 joined quote+outcome rounds before any profit claim.
3. **1m calibration remains open** — post-retrain ECE 0.0533 and high tiers remain optimistic; overall is stable.
4. **Keep shadows + recorders accruing** — the recent regime window now fails promotion (46.2%, LB 35.7).
5. **Microstructure join-probe** in ~2–4 weeks (the only forward lever that could move direction).
6. **If edge proves out:** the bot pipeline (live edge engine → paper → risk engine + kill switch → micro-live).
7. **Cosmetic:** `public/polymarket-specialist-guide.html` still shows a 7-horizon bucket example.

---

## 8. Index of docs created/updated this session
- [POLYMARKET_BOT_REQUIREMENTS_2026-06-21.md](POLYMARKET_BOT_REQUIREMENTS_2026-06-21.md) — bot spec + phased gates
- [TIMEFRAME_VALUE_pyth_2026-06-21.md](TIMEFRAME_VALUE_pyth_2026-06-21.md) / `_binance_` — per-horizon elimination
- [TIMEFRAME_PERFORMANCE_pyth_2026-06-21.md](TIMEFRAME_PERFORMANCE_pyth_2026-06-21.md) — time-of-day / CEST
- [TIMEFRAME_WINRATE_PRECISION_REPORT_2026-06-21.md](TIMEFRAME_WINRATE_PRECISION_REPORT_2026-06-21.md) — win rate + precision
- [CEILING_BREAK_EXPERIMENTS_2026-06-21.md](CEILING_BREAK_EXPERIMENTS_2026-06-21.md) — the 5 experiments
- [CHAMPION_V2_SHADOW_2026-06-21.md](CHAMPION_V2_SHADOW_2026-06-21.md) — regime/meta-skip vs P(Hold)
- [ACTION_ITEMS_AND_TIMEFRAME_PRUNE_2026-06-21.md](ACTION_ITEMS_AND_TIMEFRAME_PRUNE_2026-06-21.md) — action register + wiring map
- [TIMEFRAME_PRUNE_IMPLEMENTATION_2026-06-21.md](TIMEFRAME_PRUNE_IMPLEMENTATION_2026-06-21.md) — done/not-done + overnight procedure
- [SESSION_2026-06-21_FINDINGS_STRATEGY_AND_DECISIONS.md](SESSION_2026-06-21_FINDINGS_STRATEGY_AND_DECISIONS.md) — earlier findings doc

---

## 9. The throughline (unchanged, reinforced)
> More honest state → better measurement → stricter gates → fewer bad trades → higher precision.

The ceiling breaks through **abstention + setup selection + market mispricing**, not raw direction. This
session pruned the dead timeframes, recalibrated P(Hold), killed two false leads (meta-skip, fallback-abstain),
and kept the real levers (regime shadow, L2 recording, the Polymarket edge proof) on the board.
