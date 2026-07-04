# Recommender Idea — Ledger Test Results (2026-07-02)

The idea (from `MASTER_STRATEGY_CEILING_BREAK_AND_RECOMMENDER_2026-06-18.md` §4): reframe the product as a
**live recommendation system** — "markets like this usually did X" — layered on top of the specialist heads
(the 7-layer "Netflix" architecture). This doc answers: **how much is built, and does it work?**

## Build status vs the 7 layers

| Layer | Spec | Status |
|---|---|---|
| 1 Feature engine | multi-timeframe ring buffers | ✅ built (keepers + live tracker) |
| 2 Specialist heads | P(hold), big-move/drop, path, fade, round-state | ✅ all built + validated |
| 3 Similar-setup memory | kNN over setup fingerprints → "n=842, 66% dropped, LB 88%" | ⚠️ partial (T3 tier-proof is a coarse version; full kNN NOT built) |
| 4 Online calibration | live per-horizon/bucket probability adjustment | ⚠️ partial (calibration_monitor measures; per-horizon iso measured a wash) |
| 5 Regime head-weighting | style-driven head trust | ✅ in champion rules |
| 6 Champion recommender | ACTION + zone + confidence + reason + invalidation | ✅ built (`decision_champion.py`, incl. meta-filter + Kelly stake) |
| 7 Record/learn loop | every recommendation→outcome | ✅ recording (`champion_snapshots`, 111,065 rows) + meta model trains from it |
| (later) Contextual bandit | LinUCB/Thompson, paper-only | ❌ gated (needs ≥500 resolved PAPER_BETs) |

## The honest test (run 2026-07-02)

Per round: the champion's action at its **last snapshot with ≤120s left**, graded by whether the side it was
looking at finished ahead. Round-level (no snapshot pooling), Wilson lower bounds, n=7,834 live rounds:

| champion action | n | held% | Wilson-LB |
|---|---:|---:|---:|
| **SETUP** (act bucket) | 4,265 | **96.5%** | **95.9%** |
| AVOID_LONG | 270 | 87.4% | 82.9% |
| LEAN | 124 | 86.3% | 79.1% |
| WAIT | 3,162 | 78.7% | 77.2% |

**Verdict: the recommender stratifies for real.** SETUP−WAIT separation = **+17.8pp** with huge n; passes
rule 2 (monotone, n≥100, top LB 95.9% > bottom rate 78.7%). The Layer-6/7 core of the idea is not just
useful — it is already the most validated decision layer in the app.

**What it does NOT yet prove:** profit. Held% is not ROI — converting 96.5% hold into money still requires
the executable ask below fair value (the recorders + the new Kaggle book data). No PAPER_BET rows had
resolved quotes yet at test time.

## Layer 3 BUILT + VALIDATED + WIRED (2026-07-02, same day)

**Validation (temporal holdout, 6,655 memory / 1,175 test rounds):** banded neighborhood lookup
(horizon exact, seconds_left ±20s, P(hold) ±0.04, |lead| band, regime family; bands widen ≤2× until
n≥30) **beats the global action-bucket baseline** — Brier 0.0483 vs 0.0515 — with monotone,
near-perfectly calibrated quartiles (predicted 79.7 / 93.6 / 99.5% → realized 79.7 / 97.1 / 99.5%),
94% coverage, median 69 neighbors. Passes the pre-declared gate and rule 2.

**Wiring:** `database.similar_setup_stats()` (read-only banded SQL over the app's own graded ledger,
crash-safe, None when evidence is thin) → called once per round on entering the late window in
`price_to_beat` (recomputed only if P(hold) moves ≥0.10) → `rnd["similar_setups"]` → UI line under the
ACTION strip: *"🧠 Markets like this: n=156 similar late setups → held 96.2% (Wilson-LB 91.9%)"*.
The line carries the honest disclaimer: memory is EVIDENCE, not a prediction — profit still requires
the ask below fair value (the ask-lift test proved hold% alone is already priced by the market).

## How the engine LEARNS (cadence contract — per the adversarial-market rule)

| What | Cadence | Mechanism |
|---|---|---|
| Similar-setup memory | **every round, automatically** | accumulation — each graded round becomes a new neighbor; no fitting, no drift risk |
| Bucket stats / calibration | fast (hours) | calibration_monitor + ledger queries (observation, not weights) |
| Meta-filter, REFIT heads (P(hold)/path/fade/round-state) | slow (nightly / retrain window) | proper retrains with temporal splits |
| Per-tick weight learning | **never** | stop-list: learns noise; markets adapt |
| Contextual bandit (Layer 8) | gated | needs ≥500 resolved paper bets with quotes |

## Remaining next steps
1. **Re-run this ledger test monthly** (one query) — decay watch.
2. **Bandit stays gated** until ≥500 resolved paper bets with executable quotes.
3. Memory dims are deliberately 5 (validated); adding features thins neighborhoods (curse of
   dimensionality) — any new dim must re-pass the holdout gate before wiring.
