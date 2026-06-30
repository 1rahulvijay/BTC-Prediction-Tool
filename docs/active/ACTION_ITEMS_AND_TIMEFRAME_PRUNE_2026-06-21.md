# Action Items + Timeframe-Removal Plan (2026-06-21)

Consolidated, executable register of everything proposed this session + the external review, with status,
and a precise **timeframe-removal-from-models** plan separated into *no-retrain* vs *needs-retrain*.

Keep-set decision (from [TIMEFRAME_VALUE_pyth](TIMEFRAME_VALUE_pyth_2026-06-21.md)):
**FINAL: KEEP {5, 15}** (the only tradeable Polymarket markets). **REMOVE 1/3/7/10/30** (no market,
coin-flip direction, pure compute). _(1m was initially kept as a feedback clock, then dropped 2026-06-22 by
the alt session + operator for the leanest build; arch `2horizon-5-15`.)_

---

## 1. Timeframe removal — where horizons are wired, and the safe sequence

Horizons appear in ~30 places. There is a documented **silent-drop bug** (`features.py:1405` — horizons
were once silently `[1,5,10,15]`), so every site must change **coherently** or the model trains/serves a
mismatched set. Split by blast radius:

### PHASE 1 — tracker + UI (NO retrain; immediate live-CPU + clutter win)
Stops *creating / serving / displaying* the dead-horizon rounds. The frozen ensemble + heads still contain
them internally but are never queried, so nothing breaks and no retrain is needed.

| File | Site | Change |
|---|---|---|
| `backend/database.py` | `:66, :1578, :1717, :1900, :1950` timeframes lists | `[1,3,5,7,10,15,30]` → `[1,5,15]` |
| `backend/server.py` | tracker horizon iteration (verify) | restrict to `[1,5,15]` |
| `src/main.js` | `:2813 ROSTER_HORIZONS`, `:2532/:3062/:3143` grids, `:2971/:3183` tab lists, `:3389` | → `[1,5,15]` |
| `src/main.js` | `:2812 PTB_HORIZONS=[5,15,30]`, `:2707`, `:545 replay` | drop 30 → `[5,15]` |

- **Impact:** ~7→3 live round-trackers per anchor → **~57% less per-tick serving CPU**; cleaner UI; the
  1m feedback clock + the two tradeable markets remain.
- **Risk:** low. Existing `predictions_3m/7m/10m/30m` tables keep their history; they just stop getting
  new rows. `analytics.py _prediction_union` still reads them fine.
- **Reversible:** yes — restore the lists.

### PHASE 2 — models + datasets (bundle with the NEXT deliberate retrain; do NOT trigger one just for this)
Changes the ensemble arch + which heads/datasets exist. `model.py:317` arch is `"7horizon-..."` → editing
horizons bumps `MODEL_ARCH_VERSION` → boot rejects the saved bundle → **~6h retrain**. Since direction is
at the ceiling (retrain gains ~0), only do this *when you're already retraining for another reason*.

| File | Site | Change | Effect |
|---|---|---|---|
| `backend/model.py` | `:428` horizons, `:317` arch `"7horizon"`→`"3horizon"` | `[1,5,15]` | leaner ensemble; **6h retrain** |
| `backend/features.py` | `:1405` horizons | `[1,5,15]` | fewer per-horizon feature labels |
| `backend/keeper_head_training.py` | `:23 HORIZONS` (+ bucket map) | `[1,5,15]` | ~57% less head training |
| `backend/build_persistence_dataset.py` | `:39 HORIZONS` | `[1,5,15]` | smaller P(hold) dataset |
| `backend/build_path_labels.py` `:29`, `build_fingerprints_historical.py` `:30`, `build_binance_updown_feature_dataset.py` | HORIZONS | `[1,5,15]` | smaller datasets |
| `backend/analytics.py` `:8`, `calibration.py` `:33`, `composed_decision_scorecard.py`, `automl.py`, `fsr_ppo_strategy.py`, `anti_signal_scan.py`, `analyze_signals.py`, `diagnose_model.py` | HORIZONS | `[1,5,15]` | analytics/diagnostic consistency (low risk) |

- **Impact:** ~57% less per-horizon head training + matrix labeling; smaller sequence tensors (lower RAM,
  faster TCN/LSTM); faster retrain on 16GB.
- **Discipline:** change ALL Phase-2 sites in one commit; **keep `FEATURE_NAMES` order** (append-only rule —
  do not reorder 0..N); bump `MODEL_ARCH_VERSION`; verify on the purged walk-forward before adopting.
- **Leave alone:** the research/probe scripts (`edge_probe`, `dwell_probe`, `expanded_matrix_analysis`,
  `final_analysis`, `depth_edge_probe`, etc.) — they already use their own horizon subsets; not serving.

**Recommendation:** execute **Phase 1 now** (free win, no retrain). Defer **Phase 2** to the next time a
retrain is run for another reason; don't burn 6h on a prune alone.

---

## 2. Action register — every proposed idea, with status

| # | Idea / proposal | Verdict | Status / next |
|---|---|---|---|
| 1 | **1m P(Hold) recalibration** | VERIFIED / STILL DRIFTING | Post-retrain live check: overall P(Hold) is stable (n=79,019, ECE 0.0093), but **1m still drifts** (n=11,971, ECE 0.0533; high tiers 2.6-5.0pt optimistic). Do not claim the retrain fixed 1m and do not auto-apply an overlay without a temporal holdout test. |
| 2 | **Remove dead timeframes from models** | **IMPLEMENTED ({5,15})** | FINAL keep-set **{5,15}** (markets only; 1m dropped 2026-06-22). All layers uniform {5,15}, arch `2horizon-5-15`, validated coherent. Activates on the **360d completion-marker retrain** (`.\start.bat`). See [FULL_360D_RETRAIN_IMPLEMENTATION](FULL_360D_RETRAIN_IMPLEMENTATION_2026-06-22.md). |
| 3 | **Regime gate (RANGE/LOW_VOL)** | SHADOW / DO NOT PROMOTE | Latest n=772: pooled history is 56.6% (LB 50.6), but the independent recent-250 window fell to **46.2% (LB 35.7)**. Keep `regime_gate_shadow.py` + `champion_v2_shadow.py` accruing; recent evidence fails promotion. |
| 4 | **Meta-skip CatBoost head** | REJECTED | `champion_v2_shadow.py`: beats a plain P(Hold) threshold by **+0.8/+0.7 pts** = ~0 → it's P(Hold) re-derived, not a new edge. Don't build a meta head. |
| 5 | **Fallback-abstain in TRENDING** | REJECTED | Shadow killed it (LB < 50; harmful at 15m). Do not wire. |
| 6 | **Evening 15m (20:00–24:00 CEST)** | WATCH | Best cell (61.8%, LB 51.4, 7/8 days) but selection-biased + tiny n. Use as a *feature/shadow*, not a live gate; re-test on future days. |
| 7 | **Triple-barrier** | FEATURE only | AUC ~0.66 ranking but negative after 2 bps → path-pressure/risk feature, never a trade trigger. |
| 8 | **L2 microstructure + cross-venue** | RECORDING | `microstructure_recorder.py` accruing (record-forward). Join-probe in 2–4 weeks; only forward lever that could move direction. |
| 9 | **Settlement ingestion** | **DONE** | Official-only, restart-safe CLOB/Gamma resolver built and auto-started. Backfilled **364/364** persisted outcomes; no Binance proxy labels. See [SETTLEMENT_INGESTION](SETTLEMENT_INGESTION_2026-06-21.md). |
| 10 | **Polymarket auto-bot / execution** | BLOCKED | Settlement plumbing is done, but only **4 quote rounds** currently join to outcomes. Gated on a positive edge table over ≥500 quote+settlement rounds after costs. No direction bot. |
| 11 | **Binance second anchor** | LIVE (thin) | Persisting; re-run `analyze_timeframe_value.py --source binance` after a few days. |

---

## 3. Priority order (what to do next)

1. **Keep the Polymarket recorder running continuously** so quote rounds, not merely outcomes, reach ≥500.
2. **Keep shadows accruing** (regime gate, champion_v2, evening-15m) + the microstructure recorder.
3. **Treat 1m P(Hold) as overconfident** until a temporally held-out 1m calibration fix proves better live.
4. **Re-run `analyze_pm_recorder.py`** as joined quote rounds grow; do not infer edge from 364 outcomes alone.
5. **Build paper execution only after the edge table passes** the predeclared after-cost gate.

---

*Recalibration files changed this session: `train_persistence_model.py` (per-horizon iso + ECE diagnostic,
HEAD_VERSION → `2026-06-21-keeper-dual-perhorizon-iso`), `price_to_beat.py` + `analyze_pm_recorder.py`
(serve global iso, documented why). Model retrained + saved. Validated: compile + pyflakes + selftests.*
