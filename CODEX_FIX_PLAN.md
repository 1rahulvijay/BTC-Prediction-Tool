# Codex Implementation Plan — Consistency & Correctness (post `target-align-v2`)

Work in **`C:\Users\rahul\Documents\BTC-Prediction-Tool`** (the live copy; OneDrive sync OFF).
All app-generated files live under `data/` (`BTC_DATA_DIR`). Single DB = `database.DB_PATH`.

> **STATUS (2026-06-09):** P1.1, P1.2, P1.3, P2.1, P2.2, P2.3, P4.1 implemented & verified.
> **P3.1 DONE** (per-model OOF accuracy now populates `model_accuracies`). **P3.2 DONE** via the
> "hide A/B until trained" option (UI shows *Not active — challenger not trained*; the challenger
> is never trained). **P4.2 DONE** (the ML feature already carries the live oracle as a bounded
> deviation; stale comment fixed). Arch bumped to `2026-06-09-consistency-v3`.
> **Still open / DEFERRED:** P4.3 (regime-routing source) and **P5.1 (canonical prediction
> object)** — do last, after the validation run. See `task.md` Phase 15 for the full change list
> (also includes runtime-safety fixes: WS/DB NaN serialization, executor offload + kline-snapshot
> race, scheduled-relearn cooldown, and the 1m walk-forward precision metric).

## Ground rules (do not skip)
1. **Validate first.** Before any of this, run one 24h debug launch (`BTC_HISTORICAL_DAYS=1`) so the
   already-shipped target-align-v2 fixes prove out. Don't stack new changes on an unvalidated base.
2. **One coherent change at a time.** After each item: `python -m py_compile` the touched files,
   `import server` smoke-test, and (for label/training changes) confirm a fast retrain runs.
3. **Bump `MODEL_ARCH_VERSION`** (model.py) whenever a change alters training/labels/saved-model shape,
   so stale bundles retrain once.
4. Mark each item done in `task.md` as you go.

---

## Priority 1 — Persistence & target consistency (highest ROI)

### P1.1 Save/load `stackers_by_regime` — CONFIRMED
- **Problem:** the OOF stacker is trained (`model.py` ~957) and used at inference (~1199) but
  `_save_models()` (~2128–2145) never persists it. After restart the stacking layer is gone →
  predictions silently fall back to weighted averaging.
- **Fix:** in `_save_models()` `joblib.dump(self.stackers_by_regime, MODEL_DIR/stackers.pkl)`;
  in `_load_models()` load it back (guard: file may be absent → empty dict, treat as "no stacker").
  Ensure the stacker objects are picklable (they hold sklearn/xgb models — they are).
- **Verify:** train once, restart, confirm log shows the stacker loaded and `_predict_from_regime`
  takes the stacker path (add a one-time debug log).

### P1.2 Align main backtest target with training — CONFIRMED
- **Problem:** `backtester.py` (~196) still uses `current_price = closes[i+1]`, `future = closes[i+1+h]`.
  Training now enters at `closes[i]` over `i+1..i+h` (target-align-v2). Backtest measures a different
  target than the model learned.
- **Fix:** change backtester entry to `closes[i]` and horizon end to `closes[i+h]`, matching
  `features.build_sequences`. Keep purged walk-forward as the trusted metric either way.
- **Verify:** backtest direction labels match `build_sequences` labels on the same window (spot-check).

### P1.3 Feature-retirement reads the wrong DB — CONFIRMED (same class as the analytics/automl path fix)
- **Problem:** `features.py` (~462) opens root `analytics.duckdb`; real DB is `data/analytics.duckdb`.
  Retirement logic reads stale/empty data.
- **Fix:** `from database import DB_PATH` and use it (mind import order — `database` must not import
  `features` circularly; if it does, replicate the `BTC_DATA_DIR` path logic inline instead).
- **Verify:** retirement query returns rows from the live DB.

---

## Priority 2 — Measurement integrity

### P2.1 Real Sharpe — CONFIRMED
- **Problem:** `backtester.py` (~335) computes "Sharpe" as `win_rate - 0.5`. Not a Sharpe ratio.
- **Fix:** compute from per-trade net returns: `mean(returns) / (std(returns)+eps) * sqrt(periods_per_year)`.
  If trade returns aren't tracked in backtest, label the field honestly (`win_rate_edge`) until they are.
- **Verify:** value is plausible (not bounded to [-0.5,0.5]).

### P2.2 A/B promotion gates from real metrics — CONFIRMED
- **Problem:** `ab_testing.py` (~213–214) hardcodes `challenger_pf = 1.05`, `challenger_ev = -0.10`.
- **Fix:** compute PF/EV from the challenger's resolved rows in `ab_results` (you already persist them).
  Until enough samples, report `insufficient_evidence` and block promotion — never promote on placeholders.
- **Verify:** gates read DB-derived numbers; promotion stays blocked with low n.

### P2.3 Sum duplicate HMM regime probabilities — CONFIRMED
- **Problem:** `regime.py get_confidence_vector()` (~206) dedups repeated labels with `max()`.
  If two HMM states both map to `TRENDING_DOWN`, their probabilities aren't summed → wrong confidence.
- **Fix:** aggregate by **sum** over states sharing a mapped label, then renormalize.
- **Verify:** a synthetic 2-state→same-label case sums correctly.

---

## Priority 3 — Ensemble correctness (VERIFY before fixing)

### P3.1 Dynamic weighting key-structure mismatch
- **Problem (verify):** `model_accuracies` initialized **regime-keyed** (`model.py` ~367) but read
  **horizon-keyed** (~1080); `learning_adjustments` similar (~1084). If so, weights silently no-op.
- **Fix:** make write and read use the same key scheme (recommend `[regime][horizon][model]`), update
  both sites + `_get_dynamic_weights`. **Verify the actual structures first** — confirm the mismatch is
  real before editing.
- **Verify:** log the resolved weights for a known horizon/regime and confirm they vary with accuracy.

### P3.2 Challenger actually trained/loaded
- **Problem (verify):** challenger ensemble created (`server.py` ~233) but boot only trains/loads the
  primary (~1783). If the challenger is never `.train()`d/loaded, its predictions are invalid.
- **Fix:** either train/load the challenger alongside the primary (heavier on a laptop), or
  **hide the A/B card + promotion** behind a "challenger trained" flag so the UI doesn't imply a live
  comparison that isn't happening.
- **Verify:** challenger produces non-degenerate predictions, or the UI honestly shows "not active."

---

## Priority 4 — Feature data quality

### P4.1 Missing-vs-neutral defaults + add live keys — CONFIRMED
- **Problem:** `signal_history.get_aligned_series` (~176) fills missing snapshots with `0.0`, but some
  features treat **1.0** as neutral (e.g. `book_replenishment_rate`, `queue_depletion_rate`,
  wall-growth). Also `bids_added` / `book_replenishment_rate` are consumed in `features.py` (~954, ~962)
  but NOT in `signal_history.KEYS` (~30) → they broadcast the current snapshot across training instead
  of real per-candle history.
- **Fix:** (a) add the missing keys to `signal_history.KEYS` and to `_snapshot()`; (b) give
  `get_aligned_series` a per-key neutral default (a small dict: keys whose neutral is 1.0 vs 0.0),
  filling missing candles with the correct neutral instead of blanket 0.0.
- **Verify:** Feed Health panel shows the new keys; aligned series uses 1.0 where appropriate.

### P4.2 Chainlink: feed it to the model or stop implying it — CONFIRMED
- **Problem:** `chainlink_price` is fetched (`server.py` ~1771) and used for price-to-beat/UI (~2187),
  but the ML feature is forced to `0.0` (~829). Model never learns from it.
- **Fix:** set `der["chainlink_price"] = data_state["chainlink_price"]` so the feature carries the real
  value (it already polls). If you'd rather not, remove the Chainlink wording from the ML-facing UI.
- **Verify:** the chainlink feature column is non-zero/varying in Feed Health.

### P4.3 Train/serve regime routing consistency — DESIGN DECISION
- **Problem:** training buckets regimes by ADX/vol thresholds (`model.py` ~447); runtime routes by HMM
  state (`regime.py` ~77). A model trained as threshold-"TREND" may be served under a different HMM label.
- **Fix:** pick ONE regime source for both train-bucketing and serve-routing (recommend HMM for both,
  or thresholds for both). This is a behavior change — do it deliberately, after P1–P3, and revalidate.

---

## Priority 5 — Canonical prediction object (largest; do LAST, after validation)

### P5.1 One source of truth for direction/signal/action
- **Problem:** multiple truths — `raw_direction`, `direction`, `signal`; DB `signal` column stores
  `direction` (`server.py` ~2125); verifier grades `direction` not the final action (~154); agreement
  uses hard current-regime votes while prediction may blend experts (~1278 vs ~1164); target-error
  blends NEUTRAL (~255). UI shows NEUTRAL next to an UP target.
- **Fix:** define one object with explicit fields and use it EVERYWHERE:
  `raw_direction → ensemble_direction → lean → final_action → final_trade_direction → verification_result`.
  - DB: store both `direction` and `signal`/`action` (additive columns).
  - Verifier: grade the **final action** for "did we trade well", keep direction accuracy as a separate
    metric. Preserve the intentional **lean-vs-action** split (show "lean UP, action WAIT").
  - Target-error: compute only over actual UP/DOWN calls.
  - Agreement: compute on the same expert blend used for the final probability.
- **Verify:** UI, DB, verifier, roster, price-to-beat all read the same fields; no contradictory display.

---

## Deferred / low priority
- **FSR-PPO real PPO training** — currently a deterministic warm-start; also updates `last_actions`
  every recommendation (`fsr_ppo_strategy.py` ~276) even with no trade. Keep as research challenger;
  don't let it influence the live signal until it's a trained policy with positive logged reward.
- **Meta-model expected-profit unit check** — verify `order_flow_state` passed to the simulator
  actually contains `bid_depth`/`ask_depth` (`trading_simulator.py` ~148, `meta_model.py` ~87); if not,
  expectancy/meta filtering may be too harsh. Verify before changing.

---

## Suggested execution order (one PR each)
1. P1.1 stacker save/load → 2. P1.2 backtest target → 3. P1.3 retirement DB path →
4. P2.3 HMM sum → 5. P2.1 real Sharpe → 6. P2.2 A/B gates →
7. P3.1 weighting (verify) → 8. P3.2 challenger (verify) →
9. P4.1 signal keys/defaults → 10. P4.2 chainlink feature → 11. P4.3 regime routing →
12. P5.1 canonical object (big, last).

Validate on a 24h debug launch after P1, again after P3, and again after P5.
