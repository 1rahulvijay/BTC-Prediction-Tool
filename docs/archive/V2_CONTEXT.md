# BTC Quantum Trader — V2 Context, Plan & Strategy

> Read this first. It's the single source of "where we are, what to do next, and why."
> Detailed references: `system_architecture.md` (engine), `UI_GUIDE.md` (screens),
> `CODEX_FIX_PLAN.md` (the prioritized fix backlog), `task.md` (running checklist).

---

## 0. The one-paragraph truth (read before anything)
No model reliably predicts short-horizon BTC price; a *useful* directional accuracy is only
modestly above 50%, and it falls after fees/slippage. The edge — if any — comes from
**selectivity** (trading only high-conviction, cost-positive setups), not from more models or
more frequent calls. The **#1 limiter right now is data coverage**, not architecture: only
~27 of 109 features are live until the signal-history buffer accrues days of per-candle
microstructure. So the winning strategy is **make the system internally consistent, freeze it,
run it, accumulate data, and measure** — not keep adding models.

---

## 1. What "V2" is (the corrected baseline = `target-align-v2`)
V2 is the first version where **training, inference, and verification all measure the same
target.** Previously they disagreed, which capped accuracy and made every metric untrustworthy.

Fixed in V2 (all verified):
- **Label/serve time alignment** — training now enters at `closes[i]` (the last-feature candle)
  and scans barriers `i+1..i+h`, matching live inference's 0-bar gap (was `closes[i+1]`, a 1-bar skew).
- **Cost-floored label threshold** — neutral band = `max(cost_floor≈0.08%, ATR%×0.15)`; the model
  is never trained to call UP/DOWN on a sub-cost move. Env: `BTC_LABEL_COST_FLOOR`.
- **Verification on the same band** — each prediction carries `neutralBand`; the verifier grades
  against it (was a hardcoded 0.01%), so accuracy + auto-learning measure the trained target.
- **Hysteresis** `0.05 → 0.015` — sized to the model's ~0.5 confidence cap so real reversals flip.
- **NaN-safe features** — `build_features_from_klines` sanitizes NaN/inf; linear models (LR/SGD)
  no longer emit degenerate probabilities (the sklearn "invalid value in divide" warning is gone).
- **Consistency:** single DB (`data/analytics.duckdb` via `database.DB_PATH`), all four verifiers
  use the same 0.0008 band, default horizons `[1,3,5,7,10,15]`.

`MODEL_ARCH_VERSION = target-align-v2` → the next launch retrains once automatically.

---

## 2. Operating rules (the discipline that's been missing)
1. **One coherent change per launch/PR.** Compile + `import server` smoke-test each. Never stack
   many changes on an unvalidated base — that's what made every prior result uninterpretable.
2. **Validate, then freeze.** After a change set, run it and measure before changing more.
3. **Files & env:** work in `C:\Users\rahul\Documents\BTC-Prediction-Tool`; **OneDrive sync OFF**
   for Documents; all generated files under `data/` (`BTC_DATA_DIR`); launch with `start.bat`.
4. **Debug vs evidence DB:** debug (24h) runs write junk — use a throwaway
   `BTC_DB_PATH=...\data\debug.duckdb` so the real evidence DB stays clean.
5. Bump `MODEL_ARCH_VERSION` on any training/label/saved-model change.

---

## 3. Immediate plan (do in this order)
1. **Smoke-test V2** — `start.bat` with `BTC_HISTORICAL_DAYS=1` (≈24h, retrains in minutes).
   Goal: surface execution bugs fast. Watch for errors, confirm a clean train + first predictions.
2. **Work the `CODEX_FIX_PLAN.md` backlog** in priority order, one change at a time, re-validating
   after P1 and P3 (see §4).
3. **Switch to the real run** — set `BTC_HISTORICAL_DAYS=30`, fresh evidence DB, **freeze**, and
   leave it running continuously for days. Watch the **Feed Health** panel (coverage climbing,
   dead feeds going alive) and the **actionable / grade-A** bucket.
4. **Measure the V2 payoff:** does direction accuracy and the NEUTRAL/directional split improve now
   that the target is consistent and cost-aware? If yes, the V2 fixes earned it; if not, the ceiling
   is the market, not a bug.

---

## 4. Improvement roadmap (prioritized — full detail in `CODEX_FIX_PLAN.md`)
> **Update 2026-06-09 (Phase 15):** P1–P2 done; **P3.1, P3.2, P4.1, P4.2 now done too.** Only
> **P4.3** (regime routing) and **P5** (canonical object) remain — both deliberately deferred to
> after the validation run. Also shipped this pass (not in the original list): scheduled-relearn
> **6h cooldown** (was retraining ~constantly), **NaN-safe serialization** on the WS broadcast and
> the action-log endpoint (the literal-`NaN` token was silently dropping live updates / 500ing the
> API), **feature-build offloaded to a worker thread** with a **kline-snapshot race fix** (stops
> the WebSocket ping-timeout disconnects and a features-vs-labels desync), and a corrected **1m
> walk-forward metric** (directional *precision* with a true 0.50 baseline, instead of recall that
> was structurally <0.5 on a NEUTRAL-heavy horizon). Arch → `2026-06-09-consistency-v3`. Full list
> in `task.md` Phase 15.

**P1 — Persistence & target consistency (highest ROI)**
- Save/load `stackers_by_regime` (currently trained but NOT saved → stacking layer lost on restart).
- Align main backtest target with training (backtester still uses `closes[i+1]`).
- Fix feature-retirement DB path (`features.py` still reads root `analytics.duckdb`).

**P2 — Measurement integrity**
- Real Sharpe (backtester computes `win_rate-0.5`, not trade returns).
- A/B promotion gates from real DB metrics (currently hardcoded PF=1.05, EV=-0.10).
- Sum duplicate HMM regime-state probabilities (currently `max()` → wrong confidence).

**P3 — Ensemble correctness (verify first)**
- Dynamic-weighting key mismatch (regime-keyed vs horizon-keyed reads).
- Challenger A/B model actually trained/loaded, or hide A/B until it is.

**P4 — Feature data quality**
- Missing≠neutral defaults + add missing live keys to `signal_history.KEYS`.
- Feed Chainlink into the ML feature (fetched/shown but currently 0 for the model), or drop the
  Chainlink wording from the ML-facing UI.
- Train/serve regime-routing consistency (threshold buckets vs HMM) — design decision.

**P5 — Canonical prediction object (largest; do LAST, after validation)**
- One source of truth: `raw_direction → ensemble_direction → lean → final_action → verification`.
  Used identically by UI, DuckDB (the `signal` column currently stores `direction`), verifier
  (grades `direction`, not the final action), agreement display, and target-error (currently
  blends NEUTRAL). **Preserve the intentional lean-vs-action split** ("lean UP, action WAIT").

**Deferred:** FSR-PPO as a *trained* policy (currently a deterministic research stub — keep it as a
silent challenger, never let it touch the live signal until it logs positive reward over enough
resolved decisions); meta-model expectancy unit check.

---

## 4a. Deep dive — the Canonical Prediction Object (P5, the big deferred refactor)
This is the single most important *structural* item, and the audit's central insight. It is
**deliberately NOT in V2** — it's the planned next step *after* V2 validates. Captured here in
full so the context isn't lost.

**The problem: one prediction has many disagreeing "truths".**
- `raw_direction` (the model's lean) vs `direction` (post-gate) vs `signal` (LONG/SHORT/NEUTRAL action).
- The DuckDB **`signal` column actually stores `direction`** (`server.py` logs `signal=p["direction"]`),
  not the final action — so DB analytics can disagree with what the UI showed.
- The **verifier grades `direction`**, not the final action — so "accuracy" measures the lean, not
  what the user was told to do.
- **Agreement** is computed on hard current-regime votes while the final probability may be a
  *blended* regime-expert output — so the roster/agreement can disagree with the actual prediction.
- **Target-error blends NEUTRAL** calls into the dollar-error stat, polluting it.

**Symptom:** the UI shows `NEUTRAL` next to an `UP` target price and a directional ✓/✗ grade that
all appear to contradict each other → erodes trust and makes measured accuracy ambiguous.

**The fix (architecturally correct):** one canonical object, used *identically* by model → server →
DB → verifier → satellite verifiers → UI:
```
raw_direction  →  ensemble_direction  →  lean  →  final_action  →  final_trade_direction  →  verification_result
```
- DB: store **both** `direction` and `action` (additive columns — don't overload one field).
- Verifier: grade the **final action** as the primary "did we trade well" metric; keep raw direction
  accuracy as a **separate** metric. Apply the same per-prediction `neutralBand` everywhere.
- Target-error: compute **only over actual UP/DOWN** calls, never NEUTRAL.
- Agreement: compute on the **same expert blend** used for the final probability.

**Critical nuance — formalize, don't erase, the lean-vs-action split.** Part of this "inconsistency"
is *intentional*: the system is meant to say "**lean UP, but action = WAIT**" (Gemini's deliberate
design, and the right behavior — a directional read that's still not worth trading). The canonical
object must make `lean` and `action` **explicit, separate fields** carried end-to-end — not collapse
them into one. The goal is clarity (every consumer reads the same explicit fields), not forcing the
lean and the action to always agree.

**Why deferred (not churn-avoidance excuse — real reasoning):** it's a large cross-cutting refactor
touching `model.py`, `server.py`, `prediction_verifier.py`, the three satellite verifiers, the
DuckDB schema, and the UI. V2 just made a major correctness change (target-align) and **has not
completed one clean validated run.** Layering a big refactor on top means you can't attribute any
accuracy change to either one. Validate V2 first; then do this as its own scoped change.

**When/how:** P5 in `CODEX_FIX_PLAN.md`, **last**, after P1–P4 and after the V2 validation run.
Do it as a single dedicated PR with the schema change + all consumers updated together, then
re-validate. Migration: add new DB columns additively (don't rewrite history); backfill `action`
from existing rows where derivable, else leave null.

---

## 5. The accuracy strategy (how the numbers actually move)
1. **Coverage first.** Keep it running so the ~70 dead microstructure features come alive. This is
   the single biggest lever and it costs nothing but uptime. (Don't delete `signal_history.pkl`.)
2. **Selectivity, not frequency.** The actionable/high-conviction bucket is where any edge lives.
   Don't loosen the AVOID gate to get more signals — widen the *sample* of good signals instead.
3. **Cost economics.** With a ~$32 round-trip cost and a model capping ~0.5 confidence, expected
   value is often negative → AVOID is correct. Levers: lower cost (`BTC_TAKER_FEE_BPS`) only if you
   truly trade that cheap; or higher confidence (comes from coverage + a cleaner target, i.e. V2).
4. **Per-model evidence.** Once `model_predictions` fills, prune/upweight base models by *incremental*
   contribution, not standalone accuracy.
5. **Calibration honesty.** Show raw prob, calibrated prob, and evidence quality separately.

---

## 6. Proof gates — when is it allowed near real money
Do NOT use meaningful capital until, on a frozen bundle:
- ≥ 500 resolved **actionable** predictions per horizon, over 30–90 live days.
- Positive expectancy **after** modeled fees/slippage; profit factor > 1.2.
- Stable calibration; positive in ≥ 2 distinct regimes; AVOID demonstrably helps.
Until then: **paper-trade only.** Treat every signal as experimental.

---

## 7. Where things live (quick map)
- **Code:** `C:\Users\rahul\Documents\BTC-Prediction-Tool` (OneDrive OFF).
- **Generated data:** `data/` — `analytics.duckdb`, `signal_history.pkl`, `saved_models/`, `cache/`.
- **Launch:** `start.bat` (sets `BTC_DATA_DIR`, `BTC_HISTORICAL_DAYS`, `BTC_RUN_STARTUP_BACKTEST`).
- **Key envs:** `BTC_DATA_DIR`, `BTC_DB_PATH`, `BTC_HISTORICAL_DAYS`, `BTC_LABEL_COST_FLOOR`,
  `BTC_TAKER_FEE_BPS`, `BTC_AUTO_RELEARN_COOLDOWN_SEC`, `BTC_LOG_TICKS_PARQUET`, `BTC_DL_ARCH`.
- **Docs:** `system_architecture.md`, `UI_GUIDE.md`, `CODEX_FIX_PLAN.md`, `task.md`, this file.

---

## 8. Honest bottom line
V2 removed the self-inflicted errors (target skew, mismatched scoring, sub-cost labels, NaN votes,
split DB). That makes the system **measure and train toward the same goal** for the first time —
the prerequisite for any real edge to show up. It is **not** a guarantee of profit. The next move
is validation and patience, not more architecture. Build the `CODEX_FIX_PLAN.md` backlog
carefully, accumulate data, and let the evidence — not the ideas — decide.
