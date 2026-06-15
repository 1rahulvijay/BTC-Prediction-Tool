# Training Plan — Discussion Doc (2026-06-15)

**Status: FOR DISCUSSION — nothing executed.** Operator wants to review before training. Covers:
95% split, 90-day window, laptop feasibility, auto-appending new data, and the dead-feature question.
Every claim here is verified against the current code, not assumed.

---

## TL;DR (the honest headline)
- **95% / 90-day will NOT raise accuracy.** Direction is at the information ceiling (proven ~10 ways);
  timing is saturated (~0.72); P(Hold) ~0.73. More data buys **robustness across more regimes**, not
  a higher number. Don't expect the win-rate to move.
- **The dead features are NOT removed** (still 136). Removing the truly-dead ones is the **single
  highest-value training change** here — it speeds training, halves RAM/GPU, and makes 90-day feasible
  on your laptop. The 95%/90-day knobs alone do ~nothing for accuracy.
- **The band has a calibration tradeoff:** pushing it to 95% train steals from the slice that makes
  its 80% coverage honest. Keep the recent calibration holdout.
- **For "auto-append new data": keep periodic full-retrain** (the backfill already auto-appends missing
  days). True online/incremental learning isn't worth the risk here.

---

## 1. Train on 95% — per model (it's not one answer)

| Model | Current fit | 95% verdict |
|---|---|---|
| **Direction ensemble** (model.py) | `BTC_TRAIN_SPLIT_FRAC=0.95` already set (clamped [0.5,0.98]) | Already 95%. But FROZEN + at the ceiling → 95% won't change the coin-flip. The 5% holdout is **mandatory** (conformal bands + OOS backtest calibrate on it). |
| **selectivity_models.pkl** (RF ensemble) | final fit on **ALL rows**; OOS via TimeSeriesSplit | Already "100%" for the deployed model. Nothing to change; the OOS metric stays honest. |
| **signed_quantile_model.pkl** (80% band) | q-models on 80%, **cqr calibrated on recent 20%** | ⚠️ **Do NOT push to 95%.** The recent 20% IS the conformal calibration that makes the band cover 80%. Shrink it to 5% and the band's honesty degrades. Keep ≥15–20% recent for cqr. |
| **persistence_model.pkl** (keeper P(Hold)) | fits on the dataset + isotonic calibration | 95% fine for the point model; keep a holdout for the isotonic calibration. Already 1.95M rows — data-rich. |

**Principle:** "95% so it sees more patterns" helps generalization *marginally*, but every model needs a
**holdout for its calibration** (conformal band, isotonic P(Hold)). Calibration is what turns a
probability into a *tradeable* number (the whole Polymarket edge). **Don't trade calibration for 5%
more training rows.**

---

## 2. 90 days vs 60 days
- **Upside:** more regimes (more trend/chop/vol environments) → marginally more robust generalization,
  steadier calibration.
- **Reality:** ceilings unchanged — direction stays coin-flip, timing ~0.72, P(Hold) ~0.73. Expect
  **~0 accuracy lift.**
- **Cost:** ~50% more data → more RAM + longer train (see §3).
- **Verdict:** fine for robustness *if the laptop handles it*; it is **not** an accuracy lever. The
  bigger levers are removing dead features (§5) and live data (§4).

---

## 3. Will it work on your laptop? (16 GB)
- **Offline heads** (selectivity, signed-quantile, persistence, beat, magnitude, path): **trivial** —
  seconds to a few minutes each, no GPU, low RAM. 90 days is no problem.
- **Direction ensemble retrain:** the **heavy** one (~6 h at 60d, GPU + ~12 cores, the live feed
  freezes during it). 90 days ≈ +50% data → a bigger 90d×136 feature matrix + larger sequence tensors
  (TCN/LSTM). **On 16 GB this is the risk** — matrix + sequence buffers could get tight.
- **Mitigation (important):** removing the 78 dead features (§5) ~**halves** the matrix width and the
  DL input dim → less RAM, ~2× faster DL, half the GPU memory. **Do dead-feature removal first; then
  90-day is much more comfortable on 16 GB.**
- **Practical:** run the ensemble retrain overnight with the IDE/browser closed (as the 60-day run was).
  The heads can run anytime (they don't freeze the feed).

---

## 4. Auto-appending new data into existing models
The right architecture (and what's already supported):
- **Data auto-appends today:** `start.bat` runs the builders with `--auto --days N`, which fetches
  **only the days missing since the last run** and appends to the cached parquet/aggTrades. So new days
  flow in automatically on each start.
- **Models: periodic FULL retrain on the grown window (recommended).** Tree ensembles (xgb/lgb/cat/RF)
  don't incrementally update cleanly, and the OOF meta-stacker needs a consistent fit — the standard,
  robust approach is a **scheduled full retrain** (e.g., weekly/monthly) on the expanded data, not
  continuous online updates.
- **Why NOT true online/incremental learning here:** (a) tree models warm-start poorly and risk
  recency-overfit; (b) it complicates the leak-free OOF stacking + calibration; (c) **direction is at
  the ceiling**, so incremental learning would chase noise for zero gain. The heads are cheap to fully
  refit, so there's no compute reason to go incremental.
- **The one place "new data" genuinely helps:** the **live recorders** filling the currently-dead
  microstructure features going forward (§5b). Once those have history, a retrain that *includes* them
  is the only path to information the offline backfill can't provide. That's the real "feed it new
  data" win — not online updates of the existing (saturated) features.

**Suggested cadence:** leave `BTC_FREEZE_MODEL=1` for daily use; once a week/month, set `=0` overnight
(IDE/browser closed) to retrain on the auto-grown window. Heads can be re-run anytime (delete the pkl
or run the trainer).

---

## 5. Dead features — the real opportunity (NOT done yet)
**Verified:** `NUM_FEATURES = 136`, `len(FEATURE_NAMES) = 136`, arch `...session-136-tcn`. The 78
dead-zero features `diagnose_model` found were **identified but never removed** from the training
schema. **So training is NOT faster yet** — the model still carries 78 all-zero columns.

**(a) Removing the truly-dead → big speed/RAM win:** ~136 → ~58 features halves the matrix → faster
tree training, **~2× faster TCN/LSTM, ~half the GPU memory** → makes 90-day feasible on 16 GB. This is
the highest-value training change in this whole list.

**(b) The critical caveat — don't remove blindly:** some features are "dead in BACKFILL" only because
the offline builder can't compute them — but they DO have **live** values (live-only microstructure).
Removing those means the model can *never* use them, even after the live recorders accumulate history.
So split the 78 into:
- **Truly constant (zero live too)** → remove (pure waste).
- **Live-only, no backfill history** → keep, and let the live recorders fill them; include in a future
  retrain once they have history (this is the §4 "real new-data win").

**Action:** run an audit that classifies the 78 (constant-everywhere vs live-only), remove group 1,
bump `MODEL_ARCH_VERSION`, retrain. Net: faster + cleaner + 90-day-feasible, with the live-only
features preserved for the data that actually matters.

---

## Recommendation (for our discussion)
Ranked by value:
1. **Audit + remove the truly-dead features, then retrain** — the only change here that materially helps
   (speed, RAM, 90-day feasibility). Distinguish constant-everywhere from live-only first.
2. **Keep the band's recent calibration holdout** — don't push the signed-quantile band to 95%.
3. **90 days: optional**, robustness-only, ~0 accuracy; do it *after* dead-feature removal so it fits in
   16 GB.
4. **Auto-append = scheduled full retrain** on the auto-grown window; skip online learning.
5. **The real accuracy frontier is still live data** (live-only features + the Polymarket-price shadow),
   not 95%/90-day on the saturated offline features.

**Open questions for you:**
- Do you want me to build the dead-feature audit (classify the 78) next?
- Weekly or monthly retrain cadence?
- 90 days now, or wait until dead features are removed (so it fits comfortably)?
