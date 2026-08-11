# Price-to-Beat — Complete Model Analysis, Outputs & Wiring Plan (2026-06-14)

**Scope:** the full multi-model / multi-target / 60-day offline analysis for the Polymarket 5m & 15m
price-to-beat use case. Every probe built this session, its measured output, and the evidence-backed
recommendation for what to wire into the app.

**Bottom line (one paragraph).** We tried to predict price-to-beat direction with **145 engineered
features × 14 model families × deep-sequence × tabular** over 60 days. **Direction is dead** — a
coin-flip at every horizon, confirmed ~10 independent ways. The only things that are precisely
predictable are **(a) timing** — "will this window move enough?" (`P(big_move)` AUC ~0.62–0.72, pure
volatility clustering) — and **(b) late-entry persistence** — `P(Hold)` of the already-winning side,
which hits **87–99%** when price is already ahead late in the round. The product is therefore an
**abstention machine**: it stays out of most rounds, predicts a calibrated move-size band, and only
commits a side via late-entry P(Hold). "Improving accuracy" means being **selective**, not adding
features — adding features provably does nothing for direction.

---

## 1. Tools built this session (all leak-free, self-tested, offline)

| Script | Question it answers | Verdict |
|---|---|---|
| `backend/research/standalone/entropy_edge_probe.py` | does order-flow Markov entropy time BTC moves? (A15) | ❌ no (AUC ~0.50) |
| `backend/edge_probe.py` | 17-hypothesis engine: which features carry direction vs time moves? | direction dead; timing real |
| `backend/model_bakeoff.py` (+SGD, +`--label big_move`, +14 models) | every model family on direction AND timing | direction NOISE; timing SIGNAL |
| `backend/seq_timing_probe.py` | do LSTM/GRU/Transformer/TCN beat tabular on timing? | ❌ no (sequence adds nothing) |
| `backend/research/standalone/dwell_probe.py` | is "time spent up/down" predictable? | ❌ no (side 0.52, commit 0.51) |
| `backend/research/standalone/final_analysis.py` | capstone: 14 models × 4 targets × 5m/15m on the research matrix | the tractability split |
| `backend/research/standalone/expanded_matrix_analysis.py` | **145** engineered features × 14 models × 3 targets | direction still dead; timing saturated |
| `backend/probes/probe_expected_move_cost_gate.py` (alt session, re-run) | does the timing gate survive cost as a directional trade? | ❌ −21.63 bps |
| `backend/decision/composed_decision_backtest.py` (alt session, re-run) | does the full staged flow make money? | ❌ negative even at maker0 |

All save nothing the live app uses (read-only research) except `data/expanded_matrix.parquet`
(reusable 145-feature matrix).

---

## 2. Results by price-to-beat sub-target

The price-to-beat prediction is **not one task**. It decomposes into pieces of very different
tractability — lumping them together is what makes the card contradict itself.

| Sub-target | Precisely predictable? | Best measured result |
|---|---|---|
| **1. Up / down** (vs line ≈ price) | ❌ No — coin-flip | dir AUC 0.51–0.54 (10 ways) |
| **2. Beats or not** (fresh, line ≈ price) | ❌ No — coin-flip | = direction |
| **2b. Beats** when *already ahead late* | ✅ **Yes** | **P(Hold) 87–99%** |
| **3. Time up / time down** (dwell) | ❌ No | side 0.52, commit 0.51 |
| **4. Expected drop / expected high** | ✅ band (needs conformal) | calibrated ~80% band |

---

## 3. Direction is dead — the evidence (10 independent confirmations)

| # | Method | Result |
|---|---|---|
| 1 | Original 5-way bakeoff | AUC 0.50–0.54 |
| 2 | Trading-edge backtest | ~0 expectancy |
| 3 | Live shadow | coin-flip |
| 4 | `depth_edge_probe` (L2 book) | AUC 0.53 |
| 5 | `edge_probe` 17 features (cvd, taker_ratio, large_trade, xvenue_divergence, ofi, autocorr, variance_ratio, price_impact, absorption, trend_consistency) | all dir AUC ~0.50 |
| 6 | SGD 60d (scaled) | AUC 0.49–0.53 (the old "below-chance" was an unscaled artifact) |
| 7 | 14-model bakeoff (logistic, sgd, RF, extra_trees, histgb, gradient_boost, adaboost, knn, gaussian_nb, qda, mlp, lightgbm, xgboost, catboost) | all NOISE, AUC 0.49–0.54 |
| 8 | Deep direction (LSTM/Transformer, 7 horizons) | sign_acc 49–52%, loss collapses but acc frozen = overfit |
| 9 | `final_analysis` capstone w/ cross-venue features | dir 0.51–0.52 |
| 10 | **145-feature × 14-model sweep** | dir best **0.536** (5m) / **0.535** (15m) — still NOISE |

**Cross-venue features did not help.** The recorded-but-unwired `cvd_spot/perp/divergence`,
`perp_spot_basis_bps`, `funding_velocity`, `vol_spot/perp` were included in the capstone and the
145-feature sweep — direction stayed ~0.52. **More features cannot fix an information ceiling.**

---

## 4. Timing is the real edge — but it's saturated and not directional

`P(big_move)` = "will |move| exceed the median?" is genuinely predictable and robust across every
model family. But adding features past the volatility keepers does **not** help — it saturates ~0.62–0.72.

**`edge_probe` per-feature timing AUC (3m/5m/10m/15m):**
```
range_compression  0.642 0.634 0.603 0.598    <- strongest
realized_vol       0.630 0.621 0.593 0.571    <- baseline
intensity          0.625 0.622 0.580 0.565
liquidity_shock    0.618 0.613 0.573 0.557
vpin               0.599 0.588 0.562 0.572
```

**14-model bakeoff on `big_move` (5m):** RF/histgb/xgboost 0.676, catboost 0.675, sgd 0.674 — all SIGNAL.

**145-feature sweep `big_move`:** 0.629 (5m) / 0.620 (15m) — **no better than 33 features.**
Top timing features (the wire list): `range_15m, rvol_60/90, atr_norm, micro_range_15m, parkinson_15,
shock_max15, rv_15m/60m` (+ minor: basis_lag, min_of_hr, vpin). **All pure volatility/range — already
in `selectivity_models.pkl` (Selectivity v2, AUC 0.720).**

**Entropy (A15) REJECTED:** `entropy_edge_probe` BIG_MOVE_AUC 0.51–0.54, low-entropy |move| lift
1.03–1.18× (paper claimed 2.89×). Strictly worse than realized_vol.

**Sequence models add nothing:** `seq_timing_probe` 5m — tabular RF 0.694 = LSTM 0.694, Transformer
0.687 (overfit); 15m — RF 0.681, LSTM 0.682, Transformer 0.656. The rolling features already summarize
the temporal info.

---

## 5. The timing edge does NOT survive as a directional trade

Knowing *when* a big move comes is worthless without the *side* — and the side is a coin-flip.

- **`probe_expected_move_cost_gate` (cost-survival):** gating to high-expected-move windows that pass
  Expected-Move/Cost ≥ 2.5 → **Net EV −21.63 bps, Win% 36.9%** (30m hold, 14 bps).
- **`composed_decision_backtest` (full staged flow, MAKER_MAKER):** 95% NO_TRADE / 4.6% WATCH /
  0.5% T1+T2; paper EV **negative even at maker0** (T2 −0.58, T3 −17.1 bps, win 39–46%).
- This matches the alt session's Phase 15/16: gross dir EV **+0.04 bps**, break-even cost = **0 bps**,
  dies at any taker fee. **Don't bet a side, even gated.**

---

## 6. P(Hold) late-entry — the one high-precision product ✅

Direction-**invariant**: ride the side that's *already* ahead late in the round; never predict it.
Measured directly on `persistence_dataset.parquet` (1.95M rows). Hold-rate by (seconds_left, |distance|):

**5-minute rounds (overall hold 74.3%):**
```
sec_left   |dist|      n      hold%   Wilson-LB
0-30       2-5 bps    9,327   87.5%   86.8%   <- T3
0-30       5-10 bps   8,936   97.0%   96.7%   <- T3
0-30       >=10 bps   8,874   99.7%   99.6%   <- T3
30-60      2-5 bps    9,676   81.3%   80.5%   <- T3
30-60      5-10 bps   8,855   92.6%   92.0%   <- T3
60-120     5-10 bps  17,480   88.4%   87.9%   <- T3
120+       >=10 bps  21,199   88.2%   87.8%   <- T3
```
**15-minute rounds:** similar/stronger (0-30s & ≥10bps = 99.9%, LB 99.8%). Clean, **monotone**:
more-ahead + less-time-left ⇒ higher hold. This is the realistic Polymarket product.

**Honest caveat:** the hold *probability* is proven offline. Whether it *pays* depends on beating
Polymarket's **implied price** at entry (99.7% hold quoted at 97¢ = +2.7% edge; quoted at 99.9¢ = none).
That can only be tested with **live order-book data → the live-shadow frontier**.

---

## 7. Magnitude band (expected drop / high) — real but needs calibration

Signed-quantile q10/q50/q90. In the clean capstone + 145-feature run the conditioning is **~flat**
(pinball ties a constant band) and coverage is **72%** (target 80%) because the recent test window is
more volatile than the train median. The band is near-symmetric (5m: drop −13.5 / high +14.9 bps;
15m: −22.1 / +25.2). **Fix:** conformal-widen to honest 80% coverage; the value is "a calibrated
range," not clever conditioning.

---

## 8. Reconciliation with the parallel session (Phase 1–16, `backend/feature_finding/docs/`)

Two independent research lines **converged**: direction coin-flip (~0.53); selectivity is the edge
(Selectivity v2 AUC 0.720, realized_vol+intensity dominant); side selection ~50%; XGBoost side-selector
failed (0.49); gross +0.04 bps dies at any cost; only maker execution survives → live shadow. One
nuance reconciled: the alt session's "Markov entropy 0.596" is a **60-minute** transition matrix
(≈ vol regime, redundant with realized_vol), not our fast 1-second Singha construction (dead). Net:
entropy adds nothing beyond realized_vol either way.

---

## 9. What to WIRE (evidence-backed; all use already-built models — no new training)

| Wire | Why | Type |
|---|---|---|
| **P(Hold) late-entry tier** + the §6 hold-map (n / hold% / Wilson-LB) | the #1 high-precision product | surfacing (`persistence_model.pkl`) |
| **Calibrated magnitude band** (conformal-widen q10/q90 → 80%) | real precision on expected drop/high | light fix |
| **P(Big_Move) round filter** (AVOID / WATCH / TRADEABLE) | skip dead rounds | surfacing (`selectivity_models.pkl`) |
| **Abstain on direction** — drop the confident UP/DOWN headline; lead with band + P(Hold) | stops the card self-contradiction | UI logic |

The ideal round flow: open → check P(Big_Move) → low ⇒ AVOID / high ⇒ WATCH → at final 30–120s check
P(Hold) on the already-ahead side → high + favorable market price ⇒ T3 candidate, else AVOID.

---

## 10. What NOT to do (dead ends — do not re-chase)

- Don't add a direction model / more direction features (10 confirmations).
- Don't add a sequence model (overfits; ties tabular).
- Don't add the entropy (A15) head (doesn't transfer).
- Don't bet a side even gated by selectivity (loses even at maker0).
- Don't trust the magnitude conditioning over a flat band (just calibrate the band).
- Don't predict dwell side / committed (coin-flip).

---

## 11. Open frontier (the only thing offline can't settle)

**Does P(Hold) beat Polymarket's implied price at entry?** Build a live comparison in
`live/live_shadow_logger.py`: at entry, log our P(Hold) **vs the live Polymarket order-book price**,
resolve at expiry, measure the gap and the Mode A/B/C net EV. Offline proved the *probability*; only
live data proves the *price edge*. Promotion gate: ≥500 resolved, positive paper EV after cost, no
drawdown/fakeout increase.

---

## 12. One possible model improvement still worth measuring

The `P(Hold)` model currently uses only ~5 features (`distance, seconds_left, position, vol_60s_pct,
distance_pct`). It does **not** use the volatility keepers. Test: **add the keeper features
(rv / vpin / compression / shock) to the P(Hold) model** — does broader vol-regime context lift the
hold prediction beyond trailing-60s vol? This is the single remaining model lift worth a probe before
moving entirely to the live-shadow phase.

**ANSWERED (2026-06-14) — YES, modest but real, biggest on the T3 region.** `phold_keeper_test.py`
+ `train_persistence_model.py` (round-level temporal split, keepers joined from `research_matrix_1m`
at each row's current minute, n=1.88M joined):
- overall AUC **base 0.747 → +keepers 0.755** (+0.008–0.0135 across runs);
- **T3 late subset (seconds_left ≤ 120): base 0.795 → +keepers 0.815 (+0.019–0.027)** — the lift
  concentrates exactly where the high-precision product lives.

**Wired SAFELY:** `train_persistence_model.py` now trains BOTH a base (5-feat) and a keeper (11-feat)
model and saves them in one bundle (`clf`/`features` + `clf_keeper`/`features_keeper`). The live serve
path keeps using the BASE model (no breakage); the keeper model activates only once the keepers
(`rv_15m/30m/60m, vpin, compression_ratio, shock_magnitude`) are plumbed into `price_to_beat` at serve
time (DEFERRED — the one remaining serve-side wiring). Verdict: adopt the keeper model after the
serve-side plumbing; it's the one validated model gain on the #1 product.
