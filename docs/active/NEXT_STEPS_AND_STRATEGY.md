# Next Steps & Strategy — the recommendation (2026-06-13)

Single read for "where are we and what's next." Companion to the
[IMPLEMENTATION_QUEUE.md](IMPLEMENTATION_QUEUE.md) master matrix, the
[SPEC_ACCURACY_NEXT_RETRAIN.md](SPEC_ACCURACY_NEXT_RETRAIN.md) diagnosis, and the
[MEASUREMENT_WINDOW_2026-06-13.md](MEASUREMENT_WINDOW_2026-06-13.md) evidence.

---

## 0. The recommendation in one line

**Do NOT retrain now. Restart once to activate the no-train work + start the data clocks, let it
collect ~3–4 weeks while the B2 gate gives you precision today, THEN do one deliberate retrain
(A7 + A4 + A1).**

---

## 1. What the data says (the basis for the recommendation)

- **The model is a coin-flip at the bettable horizons** — 5m ~50%, 7m ~46%, 10m ~55% — confirmed by
  three independent views (regime edge map, sign-truth scorecard, betting mirror).
- **Real edge exists only in calm/RANGE regimes** (3m LOW_VOL 56.5%, 10m 54.4%, 5m RANGE OOF ~.63).
- **Committing a direction buys ~0 edge over the fallback** at 5m (model 51% ≈ fallback 51%).
- **Root cause is proven, not guessed:** all five model families converge to ~0.51 OOF = an
  INFORMATION ceiling. The high-edge microstructure features are *constant in the training matrix*
  (`server.py:1160` broadcasts one live snapshot over 50d), so the trees can't learn them.
- **Roster:** SGD removed (was noise, .12–.23); cat/lgb/xgb are the workers; histgb redundant; lr the
  linear seat; TCN now a full stacker seat in v6 (A6 done).

## 2. Do we need retraining? Not now — and here's why retraining now is wrong

1. A retrain on the CURRENT features **cannot beat 0.51** — the information isn't in the trainable
   features (proven by the 5-way convergence). It would reshuffle noise.
2. The data that WOULD break the ceiling (B1 microstructure, A1 persistence) **was just built and has
   zero rows** until the restart. You can't train on data you haven't collected.
3. Retraining now costs hours, **fragments the data collection** (schema-hash split), and **resets the
   measurement window** — all for a coin-flip. That's activity, not progress.

## 3. The plan — phased

### Phase 1 — NOW (no retrain): restart + collect
- **Restart once.** Frozen reload (~12s, `Startup training required: no`); NOT a retrain; era
  preserved. This single restart:
  - starts the **B1 + A1 data clocks** (the whole point),
  - turns on the **B2 conviction gate** → your one *immediate* precision win (commit only proven
    cells, stay quiet on coin-flips),
  - activates the per-model metric fix + the Binance·PtB tab.
- **Let it run ~3–4 weeks.** The frozen model serves with B2 selectivity; recorders fill; the
  multi-week window also answers whether the DOWN-bias is real or just a down-market.
- **Optional no-train build:** the A10 setup-fingerprint recorder (same pattern) — starts a third
  data clock so the evidence layer + T3 gate have data for the same retrain.

### Phase 2 — ONE deliberate retrain (~3–4 weeks out): the bundle
- **A7 (Optuna)** — tune off near-default hyperparams. No new data; biggest pure-tuning lever.
- **A4 (microstructure)** — now trainable via B1. The real ceiling lift.
- **A1 (persistence model)** — fed by the recorder; the high-precision T3 subset you can bet.
- Cheap riders: **A8 (session/time features)**, **ATR triple-barrier labels**.

### Phase 3 — the precision tier (after Phase 2 proves out)
- **A1-ext** path labels, **A10** fingerprints + **T3 Wilson-LB gate** (n≥100, ≥80% lower bound) →
  the rare 90–95%-precision tier. Then, and only then, the betting layer (A2 fair value re-anchored
  to A1, A11 penny-sniper) comes off the shelf.

## 4. Retrain TRIGGER conditions (pull the trigger when ALL hold)
- B1 has **≥ ~3–4 weeks** of multi-regime rows (microstructure varies with outcome).
- A1 recorder has enough resolved rounds with snapshots to train a persistence classifier.
- The frozen-window scorecard is read (DOWN-bias verdict in; if bias is real → add A5 focal loss to
  the bundle).
- You're ready to commit the GPU hours for one clean, measured retrain.

## 5. The strategy ladder — how 51% becomes harvestable precision
Not "make every call 60%." Stack three different effects in the one retrain:
- **A7** tightens what's already there (+~1–2 pts).
- **A4** adds genuinely new signal (the ceiling lift — the only true new-information lever).
- **A1** carves out a rare near-certain subset (the T3 tier — few calls, 80–90%+).
Result: *mostly silent, but when it speaks it's right* — exactly the precision objective. A coin-flip
you can't bet becomes a small number of calls you can.

## 6. What NOT to do (so effort isn't wasted)
- No more TA indicators (priced in, ~0 edge at 5m).
- No bigger/deeper models (you're at the data ceiling, not capacity).
- No retrain on the same 130 features (reshuffles noise).
- No touching the regime gate mid-measurement (it's working; changing it resets evidence).
- No betting-layer work (fair value, penny-sniper) until the precision tier is proven.

## 8. Stacker / new models / transformer / "another ensemble?" — the decision

**Q: another ensemble, or extend the existing stacker?** → **Extend the ONE stacker.** Documented
consensus (MODEL_ROSTER_PLAN §6): the stacker IS the ensemble-combiner; new models join it via OOF
features, NOT as a parallel app. A second ensemble = duplicated complexity, no clear gain, harder to
combine. Current seats (v6): xgb, lgb, cat, histgb, lr, **dl/TCN (now a FULL stacker seat)**.

**Q: add a transformer?** → **Not yet — and there's a hard gate.** Transformers are data-hungry; at
~46k samples (50d of 1m bars) one will overfit and lose to the trees. Sequence: (1) the 90-day window
(~130k samples) + new features land, (2) **measure v6's TCN stacker contribution** — TCN is the cheap
proxy for "do sequence models pay here?". If TCN shows decorrelated lift → a SMALL patch-attention
encoder is the next candidate (added to the existing stacker). If TCN shows nothing → sequence models
don't pay at this scale; no transformer, no LSTM/GRU (they'd be TCN-flavored clones). **Don't add a
transformer speculatively.**

**Q: what models to add?** → NOT more trees (xgb/lgb/cat/histgb already move as one block — clones add
robustness, not diversity). The only genuinely decorrelated candidates: **(a) the kNN setup-fingerprint
voter (A10-voter)** — memory-based, truly different — after A10 fingerprints exist; **(b) the
transformer**, gated as above. Each enters as a challenger and earns its seat only on held-out
sign-truth lift.

**Q: what data elements to add?** (all at the retrain, schema-bumping):
- **A4 spot-vs-perp flow** (`crossvenue_flow.parquet` ✅ collected) — needs its live perp-CVD twin first.
- **A8 time/session** + **rv_term_structure** — cheap, backfillable, zero parity risk.
- **L2 depth** (slots ~52–72) — already wired but constant-in-training; activates via live B1.
- **★ A1 `P(hold)` as a FEATURE** into the main stacker — feed the persistence model's output back in
  so the direction model can use "this setup historically holds" as a signal. High-value, novel.

### Additional feature-matrix candidates (curated — new information, NOT TA reshuffles)
All appended at the retrain (schema bump). B = backfillable now / L = live-only.

| Candidate | Why it has edge | Source |
|---|---|---|
| **taker_buy_ratio** (1m/5m) | aggressive-flow imbalance leads price; cleaner than raw CVD | B (aggTrades) |
| **perp_spot_basis_bps + cvd_divergence** | perp leads spot; funding tension precedes reversion | B ✅ (built: cross-flow) |
| **oi_price_divergence** | OI↑+price↓ = shorts loading; OI↑+price↑ = strong trend | B (futures OI history) |
| **funding_sign_flip / funding_regime** | funding flips = sentiment turn | B (funding history) |
| **liquidation_momentum** (signed cascade) | liq cascades ARE the 5m move | L (forceOrder) + partial B |
| **price_acceleration / jerk** (2nd/3rd deriv) | momentum dynamics, not level | B (klines, free) |
| **vol_of_vol** (std of rv) | regime instability / breakout precursor | B (klines, free) |
| **trade_size_entropy / whale_ratio** | informed flow concentration | B (aggTrades) |
| **L2 depth slope / wall imbalance** (multi-level) | resting liquidity shapes the next minutes | L (depth20) |
| **ETH/SOL lead-lag + corr regime** | alt moves often lead BTC micro-moves | B (cross-asset trades) |

Priority for the next retrain (highest marginal edge, backfillable): **taker_buy_ratio,
oi_price_divergence, funding_regime, price_acceleration, vol_of_vol** — all free-or-cheap and
parity-safe. The L2/liquidation ones ride the live B1/recorder path for a later retrain.

## 9. start.bat — all three offline builders auto-run on boot (2026-06-13)
`[0/3]` now runs, in order and incrementally (`--auto`, only missing days; first run full, then ~1-day
top-ups), all guarded by `BTC_SKIP_BACKFILL` and non-blocking: (a) `backfill_trade_features.py`,
(b) `build_persistence_dataset.py`, (c) `build_crossvenue_flow.py`. They share `data\backfill_cache\`.
So a normal app start keeps all three datasets current for the eventual retrain with zero extra steps.

## 7. Verification status (deep scan 2026-06-13 — all PASS)
All session work verified end-to-end against a throwaway DB: `init_db` creates the new tables; B1
logs 130-float vectors; `persist=True` tracker writes rounds+snapshots while `persist=False` (Binance
mirror) writes nothing (no DB collision); A1 label-join yields correct hold/flip labels; all backend
parses; Vite build clean; wiring greps green. Changes activate on the next restart (none retrain).
