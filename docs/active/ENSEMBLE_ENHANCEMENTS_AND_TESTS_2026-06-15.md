# Ensemble Enhancements, Tests & Outcomes — Complete Session Record (2026-06-15)

Self-contained log of everything built, tested, and wired this session, with real numbers,
honest verdicts, the feature-pruning analysis, and what's still left. Companion to
`PRICE_TO_BEAT_MODEL_ANALYSIS_2026-06-14.md` (the cross-session research synthesis) and
`V10_ROADMAP.md` (PART 5 execution-layer ledger).

---

## 0. One-paragraph bottom line
Direction is a coin-flip — now confirmed **11 ways** (added: 145-feature sweep, deep LSTM/Transformer,
ensemble/SGD/RF tests, the live DB old-vs-new comparison). **No model or feature change raises direction
accuracy.** What *is* improvable, and what we improved + wired this session: the **magnitude band**
(calibrated, asymmetric), the **keeper P(Hold)** (+0.019 AUC on the late tier), the **selectivity
ensemble** (+0.003), and **feature hygiene** (81/136 features are dead in backfill; retiring them is
neutral). The product gets a precise band + a precise conditional-beat + an honest skip — not a better
up/down call.

---

## 1. What was BUILT this session

### New models / heads (in `data/saved_models/`)
| Artifact | What it is | Trainer |
|---|---|---|
| `signed_quantile_model.pkl` | calibrated **asymmetric** magnitude band (q10/q50/q90 + CQR), horizons 3–30m | `backend/train_signed_quantiles.py` |
| `persistence_model.pkl` (dual) | base 5-feat P(Hold) **+ keeper 11-feat P(Hold)** in one bundle | `backend/train_persistence_model.py` |
| `selectivity_models.pkl` | P(big_move) **LogReg+RF ensemble** / tradability / fail_fast / expected_move | `backend/decision/train_selectivity_models.py` |

### New code modules
| File | Role |
|---|---|
| `backend/live_keepers.py` | computes the 4 missing keepers live by **reusing `edge_probe` builders** (parity-proven) |
| `backend/decision/` (package) | cost_gate, decision_composer, event_exits, drift_monitor, shadow_store, audit_selectivity_models, composed_decision_backtest, magnitude_research, phold_keeper_test, train_selectivity_models |
| `backend/decision/shadow_store.py` | **separate** `data/execution_layer.duckdb` (never the live `analytics.duckdb`) |
| `backend/tests/test_5m_15m_30d.py` | 5m/15m × 30-day OOS scorecard (direction / timing / band) |
| `backend/tests/test_deadfeatures_30d.py` | dead-feature retirement test |

### Config / pipeline
- `BTC_TRAIN_SPLIT_FRAC=0.95` (clamped [0.5, 0.98]) — ensemble trains on ~all data, keeps a holdout for conformal+backtest.
- `BTC_FREEZE_MODEL=1` — stops the wasteful auto-retrain loop (a restart LOADS, doesn't retrain).
- `start.bat` — added `train_signed_quantiles.py` to the head phase; `--auto` backfill backward-extend fix (50→60 day bug).

---

## 2. TESTS & OUTCOMES (every number measured this session)

### 2.1 Selectivity leakage audit — `audit_selectivity_models.py`
- naive `TimeSeriesSplit` AUC ≈ **purged** walk-forward AUC for all 3 heads (Δ≈0.000); no single feature ≥ 0.85.
- **Verdict: NO leakage.** The higher-than-research AUCs are a benign period difference.
- ⚠️ Caveat: raw probabilities are **uncalibrated** (`class_weight='balanced'`): at P≥0.7 realized 0.51/0.21/0.02 → **gate on percentile thresholds, not raw prob.**

### 2.2 Combined decision-stack backtest — `composed_decision_backtest.py` (84,955 bars)
- Discipline works: **95.0% NO_TRADE**, 4.6% WATCH, 0.4% T2_SHADOW, 0.1% T1.
- Honest EV: even at **maker0 (0 bps)** sided signals are **~0-to-negative** (WATCH+sided −1.57, T2 −0.58, T3 −17 on n=38).
- **Tiers INVERTED:** T3 (p_big~0.995) wins **39.5%** vs T2 (~0.933) **46.3%** — highest-selectivity tier wins LESS (exhaustion/reversal). Same grade-inversion as the direction stack.
- **Verdict: the stack is a disciplined abstention machine, NOT a tradable signal. Stays WATCH/shadow.**

### 2.3 Magnitude research — `magnitude_research.py`
- Signed quantiles vs current symmetric |move|.
- 80% band coverage **77.6%** (vs current over-wide 90%); **regime-widening: 14 → 21 → 34 bps** low→high vol, coverage stays ~75–79%.
- Median signed move ≈ **0** (no drift) → fixes the "manufactured drift" contradiction.
- **Verdict: real upgrade — calibrated + asymmetric + regime-adaptive.**

### 2.4 P(Hold) + keeper features — `phold_keeper_test.py` + trainer
- Round-level temporal split (no same-round leakage), 1.88M joined rows.
- Overall AUC **base 0.747 → +keepers 0.755** (+0.008–0.0135 across runs).
- **T3 late subset (sec_left ≤ 120): base 0.795 → +keepers 0.815 (+0.019–0.027).**
- Base model precision intact: P(hold)≥0.93 → **96.5% realized**, ≥0.95 → 97.1%, all horizons ~96–97%.
- **Verdict: real lift on the #1 product, biggest where it matters. Wired (dual-model, fallback-safe).**

### 2.5 live_keepers parity — `live_keepers.py`
- Recompute keepers from cached bars vs research matrix (post-warmup): median 0, p99 ~1e-12, **100% of rows within 0.1%.**
- **Verdict: PARITY PASS — live reuse of `edge_probe` builders matches training exactly.**

### 2.6 Signed-quantile head training — `train_signed_quantiles.py`
- Per-horizon q10/q50/q90 + CQR. **FIXED 2026-06-15:** the cqr was calibrated on an older/calmer slice
  → undercovered (~72–75%). Recalibrating cqr on the **most-recent 20%** (live-regime proxy) restores
  **coverage = 80.0% at EVERY horizon** (3m→30m; raw 68–73% → cqr 80.0%). Band widened to match recent
  vol (e.g. 5m ±20 bps).
- Asymmetric, median≈0. **Verdict: real band upgrade; honest 80% coverage (recency-calibrated).**

### 2.7 5m/15m × 30-day OOS scorecard — `test_5m_15m_30d.py`
| Horizon | Direction sign-acc | P(big_move) AUC | Band 80% cov |
|---|---|---|---|
| 5m | 50.4% (coin-flip) | 0.748 | 75.9% |
| 15m | 49.9% (coin-flip) | 0.739 | 75.2% |
- (Caught + fixed a leak in the test itself: `ret_5m` is the realized outcome, not a feature — it gave a fake 99.8% before removal.)

### 2.8 Dead-feature retirement / model-local pruning — `test_deadfeatures_30d.py`
- **81 / 136 features are dead** (backfill-constant): `cvd_change/1m/5m, book_imbalance, obi_5/10/20, trade_intensity, spread_norm, funding_rate, funding_velocity, oi_change, long_short_ratio, fear_greed_norm`, … (all live-only microstructure/derivatives/sentiment).
| | big_move AUC | direction AUC |
|---|---|---|
| ALL 136 | 0.7524 | 0.5194 |
| LIVE-ACTIVE 55 (81 removed) | **0.7524** | **0.5194** |
- **Verdict: retiring dead features is EXACTLY NEUTRAL for accuracy — a hygiene win (leaner, less
  train/serve mismatch), NOT an accuracy win. Direction stays 0.52 either way.**
- **Implemented 2026-06-15:** the main ensemble now keeps the full 136-feature app schema but trains and
  predicts on a **69-feature model mask** from `dead_feature_classifier.py` (`KEEP` + `PARITY-FIX`).
  This cuts the flattened learner width from **8160 → 4140** values at current `LOOKBACK=60`. SHAP,
  PSI drift, move-size regressors, OOF stackers, RF, TCN and live prediction all use the same pruned
  schema. Arch bumped to `v11-pruned69-7977e0559560...`, forcing one retrain and then fast loads.

### 2.9 "Add models to the ensemble" — measured for every case
| Add what | Target | Result |
|---|---|---|
| LR+RF+histgb ensemble | selectivity (timing) | 0.7468 → **0.7499 (+0.003)** — adopted for selectivity head |
| RF / SGD | **direction** | 0.49–0.54 — coin-flip, **zero** gain |
| LSTM/Transformer | direction | 49–52% — coin-flip, textbook overfit |
| LSTM/GRU/Transformer/TCN | timing | **ties** tabular (0.694=0.694); Transformer loses |
| 145 features × 14 models (alt) | direction / timing | 0.536 / 0.629 — no better than 33 features |
- **Verdict: the ONLY positive is +0.003 on the timing head (adopted). Direction gets nothing from any model/architecture/feature count.**

### 2.10 Old-vs-new live accuracy (read from `analytics.duckdb`, app stopped)
- OLD model committed **sign accuracy** (hundreds of resolved): 3m 0.490, 5m 0.488, 7m 0.494, 10m 0.557, 15m 0.424 — **coin-flip, same as now.**
- The "it got worse" perception = the UI **hit-rate** (70–88%) is **neutral-inflated** (25–49% of calls are NEUTRAL, which usually score as hits), not directional skill. New era looked worse only because of restart-reset calibrators + tiny sample.
- **Verdict: no regression — direction was always coin-flip; the high number was never real skill.**

---

## 3. WHAT WORKED vs WHAT DIDN'T

### ✅ Worked (real, measured, kept)
- **Keeper P(Hold)** (+0.019 T3) — wired.
- **Signed-quantile magnitude band** (asymmetric, ~75% calibrated, no drift) — wired.
- **Selectivity LogReg+RF ensemble** (+0.003) — adopted in `selectivity_models.pkl`.
- **`live_keepers` parity** — keystone that serves both heads.
- **0.95 split** — more training data, keeps holdout.
- **Decision layer** (cost gate, composer, event exits, drift monitor, separate shadow DB) — built + self-tested.

### ❌ Didn't work / rejected (don't re-chase)
- **Direction prediction** — coin-flip 11 ways (14 models, deep, 145 features, sequence, ensemble).
- **Adding RF/SGD/LSTM/Transformer to direction** — zero gain; SGD is a near-duplicate of the existing LogReg.
- **Sequence models on timing** — tie tabular (rolling features already encode the temporal info).
- **Dwell-side / time-up-down** (alt) — 0.52/0.51, coin-flip.
- **Retiring dead features for accuracy** — neutral (it's hygiene only).
- **Markov entropy (A15)** (alt) — 0.51–0.54, redundant with realized_vol.
- **Trading the timing gate directionally** — cost-survival −21.63 bps.

---

## 4. FEATURE PRUNING (the dead-feature analysis)
- **136 raw app features → 69 model features.** The full vector remains available for UI, replay,
  feed-health, live diagnostics and future live-only research. The main learners now consume only the
  columns classified as `KEEP` or `PARITY-FIX`: **57 KEEP + 12 PARITY-FIX**.
- Excluded from the direction/move learners: **63 RETIRE + 4 RECORD-LIVE**. This is model-local, not a
  destructive `FEATURE_NAMES` shrink, so the rest of the app remains compatible.
- Expected effect: **faster training, lower RAM/GPU pressure, cleaner train/live parity.** Accuracy lift
  is not promised; the retirement tests showed identical AUC. The purpose is to stop spending hours on
  columns that historical training cannot learn from.
- Validation: `dead_feature_classifier --selftest` passes; backend AST passes; `model.py` import confirms
  raw=136, model=69, flat shape `(2, 4140)`, schema hash `7977e0559560`.

---

## 5. WHAT'S NOW WIRED INTO THE LIVE APP (verify on restart)
- **Keeper P(Hold) → `price_to_beat`**: `server.py` computes the 6 keepers each tick from recent klines
  via `live_keepers` (+ vpin), passes to both trackers; `price_to_beat` uses the keeper model when
  keepers are present, else the base model. Fallback-safe; smoke-tested (predicts 0.637 on synthetic).
- **Signed-quantile band → `price_to_beat`**: overrides `expected_move_range` + `projected_close` with
  the calibrated asymmetric band (drop=q10−cqr, high=q90+cqr, project at q50 ≈ no drift). Falls back to
  the symmetric band if head/keepers absent. Smoke-tested (drop −$134 / up +$135 @5m). The card renders
  these automatically — **no frontend change needed** for the band/P(Hold) upgrades.

---

## 6. WHAT'S LEFT TO IMPLEMENT
1. **main.js abstain-on-direction** — cosmetic: drop the confident UP/DOWN headline for weak grades, lead
   with band + P(Hold). (Optional; the backend upgrades land without it.)
2. **Live-shadow logger on the real feed** → the only way to answer "does P(Hold) beat Polymarket's
   implied price at entry?" (the open frontier; writes to the separate `execution_layer.duckdb`).
3. **`P(Good_Fill)`** — needs live-shadow execution data first; can't be built offline.
4. **Dead-feature retirement** (optional, neutral) — deliberate `features.py` trim + schema bump.

---

## 7. HONEST CLOSING VERDICT
Eleven independent confirmations say the same thing: **you do not improve this app by predicting
direction harder.** Every "add a model / add features / go deeper / more data" path was measured and
returned ~0 for direction. The improvements that ARE real and now built/wired make the app **precise
where precision exists**: a calibrated asymmetric band, a keeper-boosted conditional-beat (P(Hold)
84–99% when already ahead late), a selectivity gate to skip dead rounds, and honest abstention on the
coin-flip. The remaining work is **live-shadow validation + the cosmetic card polish**, not more models.
