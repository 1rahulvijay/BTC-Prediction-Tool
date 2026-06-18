# Enhancements & Validation Ledger (Claude) — 2026-06-17

Single consolidated record of **every enhancement proposed, built, and validated** in the Claude sessions —
previous and new. Companion to the Codex docs (`MODEL_OWNERSHIP_AND_CHAMPION_ENSEMBLE_PLAN`,
`MODEL_RESEARCH_CATALOG_AND_APP_PROPOSAL`, `BTC_180D_RESEARCH_RESULTS_AND_APP_PLAN`,
`DIRECTIONAL_BIGMOVE_RESEARCH_RUNBOOK`) and the canonical `POLYMARKET_BOT_MASTER.md` / `VNEXT_BREAK_THE_CEILING_VISION.md` / `V10_ROADMAP.md`.

**Distinctive principle of this ledger: every claim carries a measured number.** Codex proposes the
architecture; this doc records what was *built and proven*, with the evidence.

---

## 0. The thesis everything serves
Direction is a coin-flip — confirmed **~15 ways** (tabular, RF/XGB/Cat, deep LSTM/TCN/Transformer, SOTA
PatchTST/iTransformer on 360d GPU, 145-feature sweep, **n=5,829 live**). **No model/feature/architecture/
data-volume raises it.** So the product is **calibrated conditional probability + abstention**, and the only
edge that can exist is **`fair_value − ask − buffer`** on recorded Polymarket data. Every item below serves that.

---

## 1. SHIPPED this session — built, wired, validated
| # | Enhancement | Files | Validation | Status |
|---|---|---|---|---|
| 1 | **Big-move on the card** (`bigmove_keeper`: LogReg+RF voting on 4 parity-safe keepers) | `train_bigmove_keeper.py`, `price_to_beat.py`, `main.js`, `train_heads.py` | OOS AUC **0.733**; tiers monotonic (quiet **14%** → likely **56%** big-move rate) | ✅ live (needs restart+refresh) |
| 2 | **UI honesty rework** — direction demoted to grey/info, **P(Hold) promoted to headline**, 🟢🔴 **CONFIDENCE badge** (traffic-light by P(Hold)), grade as neutral→colored chip, big-move tier line, live-gauge note | `main.js` | `node --check` pass; matches n=5,829 finding (grade ≠ confidence) | ✅ (browser refresh) |
| 3 | **Bug fix: P(hold) 98% vs 40%** — path-outlook quoted the direction-model precision (coin-flip) next to the P(Hold) header | `price_to_beat.py` | now quotes P(hold) consistently; parse-verified | ✅ (backend restart) |
| 4 | **Polymarket recorder** (shadow-only, own DB, frozen model read-only, logs book+P(Hold)+settlement+edges) | `live_btc_updown_recorder.py`, `start_recorder.bat` | smoke pass: discovers rounds, deep book (~$50k/side, 1¢ spread) | ✅ built (run persistently) |
| 5 | **Edge analyzer** — the make-or-break table (`fair_value − ask` vs buffers, round-clustered Wilson, market-calibration) | `analyze_recorder_edge.py` | runs clean; gracefully reports "insufficient data" until rounds accrue | ✅ ready |
| 6 | **Multi-head bakeoff** + dataset builder (round-grouped walk-forward, CQR) | `backend/research/anchor_multihead_bakeoff.py`, `build_binance_updown_feature_dataset.py` | see §2 | ✅ |
| 7 | **Version-aware head trainer** + cleanup + 0.98 split | `train_heads.py`, `cleanup_artifacts.py`, `start.bat` | dry-run verified (SKIP up-to-date) | ✅ |
| 8 | **RF in main ensemble** (operator-requested), then **v11 frozen** | `model.py` | RF = weakest seat, auto-down-weighted by stacker; zero direction gain | ✅ frozen |

---

## 1b. SHIPPED — Final Specialist-Head + Champion Plan (P1–P5, 2026-06-17)
Implemented the merged Codex/Claude "Final Specialist-Head + Champion Plan" end-to-end, in priority
order, each behind a validation gate. All code-complete, parse-checked, and **active on next backend restart**
(crash-safe + gated behind model presence — nothing changes live until restart).

| P | Enhancement | Files | Validation (measured) | Status |
|---|---|---|---|---|
| **P1** | Big-Move Keeper → **4-model** ensemble (LogReg+RF+ExtraTrees+CatBoost) on the 4 parity-safe keepers | `train_bigmove_keeper.py` | OOS AUC **0.733** (stable vs 2-model — correlated trees, no degradation); tiers monotonic | ✅ |
| **P2** | **Big-Drop Risk head** (same 4-model ensemble; label = future low ≤ −10 bps) + full live wiring (loader, prediction block, card gauge, head registration) | `train_bigdrop_keeper.py`, `price_to_beat.py`, `main.js`, `train_heads.py` | OOS AUC **0.751**, top-5% precision **63.5%**, base 24.9% — **GATE PASS** (≥0.73 & ≥60%) | ✅ |
| **P3** | **Probability-bucket scorecard** for every head (leak-free OOF top-1/5/10/20% event rate + lift + favorable/adverse move + calibration deciles + ECE + monotonicity); signed_quantile coverage | `head_probability_buckets.py` → `docs/active/HEAD_PROBABILITY_BUCKETS_2026-06-17.md`, `data/head_probability_buckets.parquet` | bigmove top-5% **58.7%** (lift), bigdrop top-5% **63.7%**, **both monotonic**; band coverage **87.5%** (target 80%). ECE high (~0.18) = rank-calibrated → **gate on tiers, not raw prob** | ✅ |
| **P4** | **Quantile range → champion reward/risk veto** (favorable-room calc, 80% band zone, thin-room flag, DOWN-setup room gate) | folded into `decision_champion.py` | exercised in champion smoke-test (9 scenarios) | ✅ |
| **P5** | **Champion decision validator** — rules-first, strict: feed→quiet→at-line→drop-risk×direction→P(Hold) fair value→range veto→**edge gate** (fair − ask − buffer); outputs ACTION + confidence + plain-English reason + risk flags + invalidate + zone; **bet candidate ONLY when a live market ask clears the buffer** | `decision_champion.py`, `price_to_beat.py` (`rnd["champion"]`), `main.js` (verdict strip) | 9-scenario smoke-test: stale→AVOID, quiet→WAIT, HIGH-drop+UP→AVOID_LONG, HIGH-drop+DOWN→POSSIBLE DOWN, strong+no-ask→SETUP (no edge eval), ask-pass→PAPER_BET +3c, ask-priced→NO_EDGE, weak→WAIT | ✅ |

**Doctrine preserved in code:** direction stays confirmation-only; the champion never raises a *bet candidate*
on probability alone — only `fair_value − ask − costs − buffer > required_edge` with a live Polymarket ask does.
Without an ask it reports "edge not evaluated." The make-or-break (§6) is unchanged.

---

## 2. VALIDATIONS RUN — the evidence ledger (every number measured)
| Claim | Method | Result |
|---|---|---|
| Direction = coin-flip (live) | `analytics.duckdb` price_to_beat, committed sign-acc, Wilson | REAL 5m+15m **49.3% [46.2,52.4]** (n=974); practice **49.9%** (n=4,855); **no grade beats 50%** (A 47/B 55[47.7,61.8]/C 48) |
| P(Hold) calibrated + deployable | persistence holdout | P≥0.93 → **95.1%**, P≥0.95 → **96.0%** realized |
| ML P(Hold) ≈ analytic barrier | σ-recalibrated barrier vs model | Brier 0.163 ≈ 0.160; barrier saturates → model carves the deployable tier |
| Near-line danger surface | persistence buckets | `dist<0.02% & >60s` → hold ≤68% (NO_TRADE); `dist>0.1% & <60s` → **98–99%** hold (T3) |
| Line-cross danger head (redefined forward-from-snapshot) | 90-day bakeoff | AUC **5m 0.85 / 15m 0.86**; **false-safe 4–7%** in trade zone |
| CQR bands hit 80% | bakeoff split-conformal | coverage **0.80–0.84** across 5m/15m |
| Big-move keeper | `train_bigmove_keeper` OOS | **0.733**; quiet 14% → likely 56% |
| live_keepers parity | recompute vs research matrix | median **0**, p99 ~1e-12 |
| Directional big-move (Codex-run, Claude-analyzed) | 180d directional bakeoff | **big_drop real** (5m AUC 0.762 / top-5% **65.9%**; 15m 0.738 / **71.3%**); big_up/down high-AUC but **low precision 33–36%** = magnitude-in-disguise |
| Recorder edge | `analyze_recorder_edge` | **PENDING** — needs ≥30 resolved rounds (currently 0 settled) |

---

## 3. ENHANCEMENTS PROPOSED — status
| Idea | Where | Status |
|---|---|---|
| Anchor-Beat reframe (predict hold-vs-anchor, not direction) | VNEXT | ✅ adopted (product reframed) |
| `fair_value = min(barrier, keeper)`; edge = `fair_value − ask − buffer` | master spec | ✅ doctrine; needs recorder data |
| Champion/challenger as the promotion gate (champion = the market) | V10 PART 7 / master §6 | ✅ documented |
| Polymarket recorder + offline edge analyzer | §1 above | ✅ built |
| **Big-move keeper → 4-model ensemble** (LogReg+RF+CatBoost+ExtraTrees, same 4 keepers) | this doc §4 | ⏳ proposed (low-risk) |
| **69-feature Big Drop Risk head** (parity-safe via `build_features_from_klines`) | this doc §4 | ⏳ proposed (the new pitch) |
| Don't add 360-day forecaster / sequence models / L2-book training | §5 | ✅ recommended (parked) |

---

## 4. THE NEW ENHANCEMENTS I'm pitching now (with validation plan)
### 4a. Big-Move Keeper → 4-model ensemble (low-risk)
Extend `bigmove_keeper` from LogReg+RF to **LogReg + RF + CatBoost + ExtraTrees** on the *same 4 parity-safe
keepers*. Honest expectation: **~+0.005–0.01 AUC** (correlated tree models). Deployable, parity-safe, cheap.

### 4b. Big Drop Risk head on ALL 69 live features (the real new lever)
**Why:** `big_drop` is the one directional label that's genuinely strong (5m AUC **0.762**, top-5% precision
**65.9%**; 15m **0.738 / 71.3%**) — crypto's **fat left tail** (downside flushes predict better than pumps).
big_up/big_down are *not* (33–36% precision = the direction split eats the signal).

**Build (parity-safe):** train a **CatBoost+RF+ExtraTrees+LogReg ensemble** on the **69 live features** (built
by `build_features_from_klines` — the *same* builder the live ensemble uses, so parity is free), labels
`big_drop = future low dips ≤ −T bps`. Expected **0.74–0.76** (above the 4-keeper 0.73; below the research
160-feature only by the futures/mark/funding it can't compute live).

**Serve:** a **"Big Drop Risk: LOW/ELEVATED/HIGH"** gauge on the card — an *input*, not a trade button:
- High drop-risk + weak UP → **avoid long**
- High drop-risk + DOWN confirmation + range/EV positive → **flag DOWN-side Polymarket setup**
- High drop-risk + stale feed/wide spread → **wait**

**Validation gate before wiring (mandatory, like the keeper head):** report OOS **AUC + top-5% precision +
base rate + by-seconds-left bucket** on a round-grouped temporal split; wire only if top-5% precision ≥ ~60%
and it beats the 4-keeper version. Calibrate (isotonic) before display.

---

## 5. The honest STOP-LIST (what NOT to build)
- ❌ More direction models / ensembles — proven coin-flip ~15 ways; the OOF stacker already weights the best.
- ❌ Exact-price prediction — naive current-price baseline wins (Pearson ≈ 1.0 = it predicts ~now).
- ❌ big_up / big_down heads — high AUC, **33–36% precision** = magnitude-in-disguise.
- ❌ Promote sequence models (LSTM→iTransformer) — none beat tabular on the same test.
- ❌ Train on L2 order-book (OBI) — **no free historical depth**; live-only, can't validate (record forward or buy history first, and it still won't move direction).
- ❌ Wire the 160-feature research models — parity gap (futures/mark/funding not computed live).

---

## 6. The make-or-break (unchanged, the only thing that matters)
Everything above sharpens the **probability/risk dashboard**. None of it is *tradeable edge*. The single open
question is **`fair_value − ask − buffer` on recorded Polymarket rounds** — answered only by running the
recorder through live windows, then `analyze_recorder_edge.py`. Positive after costs → paper agent → micro-live
→ scale (all gated). Flat → it's a world-class dashboard, not a bot. **Both honest outcomes.**

---

## 7. Alignment with Codex's 8-head ownership plan
This ledger *agrees* with `MODEL_OWNERSHIP_AND_CHAMPION_ENSEMBLE_PLAN` and adds the validation evidence + the
parity discipline:
| Codex head | This ledger's contribution |
|---|---|
| Big-Move Keeper | ✅ **built + validated** (0.733, monotonic tiers, on card) |
| Directional Big-Move | analyzed: **only big_drop is real**; big_up/down rejected on precision |
| Big Drop Risk | **§4b build plan** (69 parity-safe features, validation gate) |
| Main Direction (confirmation only) | ✅ frozen v11; demoted in UI |
| Quantile Range | ✅ already live (`signed_quantile`, CQR 0.80) |
| Price-to-Beat / Hold | ✅ P(Hold) keeper, validated 95–96% |
| Volume/Activity | useful confirmation only (not a trade signal) |
| Regime + Skip / Champion validator | = the `compose_decision` gate + champion/challenger in master §5–6 |

**One rigor add-on to Codex's plan:** every specialist head must pass a **validation gate (AUC + top-N
precision + parity check + calibration)** *before* it touches the champion layer — never promote on a nicer
backtest. That gate is exactly how the big-move keeper and line-cross heads were admitted, and how the Big
Drop head will be.
