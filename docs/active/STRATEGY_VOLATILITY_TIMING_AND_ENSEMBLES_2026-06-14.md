# Strategy — Volatility/Timing Head (A15) + Specialist-Ensemble Architecture (2026-06-14)

Two researched strategies to raise PRECISION (not raw direction), aligned with the proven thesis:
5m direction is near-efficient (4 ways), the edge is **selectivity + timing**, the product is an
abstention machine anchored on P(hold). Both are TO-TRY, gated on BTC validation — not yet built.

---

## A15 — Order-flow Markov-entropy VOLATILITY/TIMING head

> **🔴 MEASURED RESULT (2026-06-14) — REJECTED. DO NOT BUILD.** `entropy_edge_probe.py --days 7`
> (8,124 leak-free minutes): BIG_MOVE_AUC **0.51–0.54**, low-entropy |move| lift **1.03–1.18×**
> (paper claimed ~2.89×). The SPY/36-day result **does NOT transfer to BTC.** Crucially, a *real*
> timing edge DOES exist — but it's carried by **simple `realized_vol` / `range_compression`
> (AUC 0.57–0.64, `edge_probe.py`)**, not entropy. Entropy is strictly worse and adds nothing over
> realized vol. The probe-first discipline paid off again: one offline run saved the whole A15 build.
> The timing keeper is the simple vol-cluster gate, not the entropy machine.

**Source:** Singha, "Hidden Order in Trades Predicts the Size of Price Moves" (arXiv:2512.15720).
Order flow as a 15-state Markov chain (price-sign × volume-quintile); **entropy of the transition
matrix predicts ABSOLUTE move size, NOT direction.** Low entropy (<5th pct) → ~2.89× larger
subsequent 5m |return|; directional accuracy stays ~45% (chance). A constrained rule showed ~1,000
bps OOS across 5 walk-forward folds, **driven by TIMING, not direction.**

**Caveats (why it's a head to TEST, not a truth):** SPY only, 36 trading days, one asset, results
concentrated in specific periods, idealized execution. **Early evidence, not a production edge.** BTC
is 24/7, fragmented, derivatives-heavy — the result may NOT transfer.

**Why it fits THIS app perfectly:** it answers the question we keep missing — *"is the next 5m window
worth predicting at all?"* — not "UP or DOWN." Many wrong trades happen because the direction model
fires in dead, low-opportunity windows. A `P(big_move)` head lets the composer say:
`direction edge + LOW expected movement → AVOID`, and `direction edge + big-move window + P(hold) →
T2/T3 candidate`. **Entropy is direction-INVARIANT by construction** (invariant under buy/sell
permutation) — a clean property: it cannot leak direction, so it can't masquerade as a direction edge.

**Output (it must NOT output UP/DOWN):** `entropy_60s/120s/300s`, `entropy_percentile`,
`low_entropy_flag`, `expected_abs_move_multiplier`, `P(big_move)`, `vol_timing_grade`.

**Build it the project way (parity-disciplined, no shortcuts):**
1. States per 1s bucket: `price_sign ∈ {-1,0,+1}` × `volume_quintile ∈ {1..5}` = 15 states.
2. Rolling transition matrix (60/120/300s) → Markov entropy `H = -Σ πᵢ Σ Pᵢⱼ log Pᵢⱼ`.
3. **Offline builder** `build_orderflow_entropy.py` (mirror the collectors: `--validate/--start-end/
   --days`, reuse `download_day`, cached, pure core, → `data/orderflow_entropy.parquet`) AND a **live
   recorder twin** with IDENTICAL math. **No parity, no feature** (hard rule #3).
4. **Separate head** `train_entropy_volatility_head.py`: target `BIG_MOVE = |move_h| ≥ max($50, ATR,
   cost)`. Metrics: AUC for BIG_MOVE, calibration of P(big_move), **|move| lift in the low-entropy
   bucket**, AND — the leakage check — **directional accuracy MUST stay ~chance** (if it shows a
   direction edge, be suspicious until audited; entropy can't carry direction by construction).
5. Do NOT add to the 136-feature ensemble until parity + BTC validation pass. Test across spot/perp,
   LOW_VOL/TREND/VOLATILE, Asia/EU/US, weekend, high-liquidation windows. If it works only in some
   regimes/sessions — still useful, but GATE it there.

**Where it lifts the 95% tier:** T3 gate becomes `direction agrees AND P(hold) high AND Wilson-LB
strong AND (entropy_pct ≤ 5% OR P(big_move) ≥ 0.80)` — only after BTC validation proves the entropy
lift transfers. Reuses the depth_edge_probe pattern: a leak-free offline edge test answers
"does entropy time BTC moves?" before any wiring.

---

## Specialist-ensemble architecture (the RIGHT version)

**Valid IF each ensemble specializes in a different QUESTION/regime; harmful if it's the same models
renamed** (a model zoo all predicting UP/DOWN → overfit). This is already our two-layer design,
formalized:

| Specialist head | Question | Status |
|---|---|---|
| Direction stack | which side? | built (coin-flip at 5m) |
| **Volatility/timing (A15)** | is the window worth predicting? | TO BUILD (above) |
| **Persistence P(hold)** | already ahead late — holds? | ✅ the edge |
| Magnitude (quantile) | how far? clears cost? | built |
| Path | direct / fakeout / chop? | built |
| Setup-fingerprint | have similar setups worked? (evidence, not ML) | built |

**Final composer (Layer 2) — do NOT average.** Decide ACTIONABILITY + tier:
`NO_TRADE / WATCH / T1 / T2 / T3`, with side from the direction stack only.
- Direction chooses SIDE · Volatility chooses TIMING · Magnitude checks SIZE-vs-cost · P(hold) checks
  late-entry reliability · Path checks fakeout · Fingerprint checks historical proof.

**Staged build (safe, debuggable — do NOT big-bang a meta-stacker):**
1. **Stage 1 — log only.** Every head writes its output + outcome to DuckDB; NOTHING controls the
   decision yet. (We already log decision-state + forward-EV — extend per head.)
2. **Stage 2 — RULE-based composer** (start here, easy to debug): `IF direction_conf > t AND
   P(big_move) > t AND P(hold) > t AND Wilson-LB > t → T2/T3 ELSE WATCH/AVOID`.
3. **Stage 3 — shallow meta-stacker** (logistic / shallow CatBoost) ONLY after enough resolved
   actionable samples (≥500 basic, 2k better, 5k strong), target = "signal worked after cost".
4. **Stage 4 — promote** the meta-stacker only if it beats the rule-based composer on walk-forward
   LIVE data.

**Guardrails (the failure modes):** overfitting (no meta-stacker until enough live samples); leakage
(every head passes the leakage audit before joining); correlated heads (each must use a different
information source/question — 5 ensembles on the same price features are NOT diverse); component
scorecards stay visible (a good aggregate can hide one broken head).

---

## Priority (highest-EV first, all gated on measurement)
```
1. P(hold) productization (the proven edge)        ← do now
2. A15 Markov-entropy volatility/timing head        ← REJECTED 2026-06-14 (probe AUC ~0.50, lift ~1x)
2b. SIMPLE vol-timing gate (realized_vol/range_compression)  ← the real keeper (AUC .57-.64); test cost-survival next
3. Setup fingerprints (evidence layer)
4. Cross-venue flow (REJECTED 2026-06-14: xvenue_divergence dir AUC ~0.50, no edge)
5. Magnitude / path heads (signed-quantile band card upgrade — light fit, no direction)
6. Specialist meta-stacker (Stage 3) — only after enough resolved samples
```

## The one-line honest verdict
Both strategies pushed **selectivity/timing**, not louder direction — the right axis. The 17-feature
`edge_probe.py` run + `entropy_edge_probe.py` settled it on real BTC: **direction is dead across ALL
17 microstructure features (dir AUC ~0.50), and the entropy head does NOT transfer (AUC ~0.50, lift
~1x).** BUT a **real timing edge exists** — `realized_vol` / `range_compression` / `intensity` /
`vpin` / `liquidity_shock` predict |move| at **AUC 0.57–0.64** (one signal — volatility clustering —
seen five ways). So: **drop A15 + the direction-flow features; build the keeper as a simple
`P(big_move)` selectivity gate** (best carriers: range_compression + realized_vol), gated on a
redundancy check and a cost-survival test BEFORE wiring into the composer.
