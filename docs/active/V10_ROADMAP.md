# V10 Roadmap — tomorrow's implementation + the 60–90 day keeper run (2026-06-13)

The consolidated plan: (1) what we execute tomorrow morning on the 30-day validation run, and
(2) the 60–90 day keeper retrain bundle. Companions: [V8_ROADMAP.md](V8_ROADMAP.md),
[V9_ROADMAP.md](V9_ROADMAP.md), [INTEGRATION_AND_METRICS.md](INTEGRATION_AND_METRICS.md),
[PRERUN_VALIDATION_2026-06-13.md](PRERUN_VALIDATION_2026-06-13.md).

---

## STATUS & YOUR TWO QUESTIONS (read first, 2026-06-13 evening)
**Q: do I need to run the recently-implemented models manually?** — Mostly no, here's the split:
- ✅ **Already ran** tonight: `model_bakeoff.py` (light tier, 6 families × 6 TFs) →
  `data/model_bakeoff_report.json`. **All ~0.50–0.54 AUC = coin-flip** (ceiling confirmed; §5bt).
- ⏳ **Run AFTER the train finishes (GPU free):** `model_bakeoff.py --deep` (LSTM/Transformer) and
  `seq_model_feasibility.py --run data\seq.npz` (sequence-seat decision).
- ⏳ **Run with the app STOPPED (morning):** `composed_decision_scorecard.py` + the PART 1 scripts.

**Q: do I restart the app once training finishes?** — **YES, once, tomorrow.** Sequence:
1. stop the app → 2. delete the 4 LEAKED heads (PART 1 step 2) → 3. start.bat. The restart
rebuilds the 4 heads **leak-free**, LOADS the already-trained v7 ensemble (no re-train), and
activates the P(hold) wiring. That single restart is the only manual app action needed.

**Validation:** every script built this session compiles, is pyflakes-clean, and passes its
self-test (composed-decision, seq-feasibility, bakeoff). One real catch fixed (redundant torch
import). Nothing touched the running train — all no-train, independent, report-only.

## PART 1 — Tomorrow morning (app STOPPED; ~30 min)
The 30-day run tonight is the **validation** run; tomorrow we read it, fix the leaked heads, then
commit to the keeper window.

1. **Stop the app.** (DuckDB is single-writer; all analysis needs it released.)
2. **Delete the 4 LEAKED head artifacts** (§5bs — first build read the outcome bar's close):
   `del data\saved_models\beat_model.pkl  magnitude_model.pkl  path_model.pkl` and
   `del data\fingerprint_evidence.parquet`.
3. **Run the analysis checklist** (each is read-only, no retrain):
   - `python backend\diagnose_model.py` — §1 horizon health (did 5m move off ~50%?), §3 dead
     features (B1 has rows now), §5 grade (still inverted?).
   - `python backend\sign_truth_scorecard.py` — 5m committed-lean sign-truth + UP/DOWN balance.
   - `python backend\composed_decision_scorecard.py` — **the end-to-end metric** (does the gate
     ladder improve, does the top tier clear break-even). The integration verdict.
   - `python backend\data_quality_audit.py --days 30` — was the window one-directional (bias)?
4. **Restart** → `start.bat` rebuilds the 4 heads **leak-free** ([0/3] d, train-if-missing) and
   LOADS the already-trained v7 ensemble (matching arch → no ensemble retrain). Expect the honest
   heads to be MODEST — mostly NOISE at 5m. That is correct, not failure.
5. **Decision gate:** if 5m committed-lean ≥ ~56% and balanced → direction edge is real → proceed to
   wire the heads + the V10 information bundle. If still ~50% (expected) → it is the information
   ceiling → the edge today stays **P(hold)**; the keeper run's value is the new INFORMATION, not
   refinement. Either way, bump the window to 60–90 days for the keeper.

## PART 2 — The 60–90 day KEEPER run (the V10 retrain)
Set `BTC_HISTORICAL_DAYS=60` (or 90; `BACKFILL_DAYS` follows). ~130k samples at 90d. One deliberate,
measured retrain bundling everything proven parity-safe. Adopt each lever ONLY if it beats the
incumbent on purged walk-forward sign-truth — one change at a time.
- **Already in v7 (carried):** 136 features incl. variance_ratio/rv_term_structure/session, class
  balance, full CVD/VPIN/large-trade backfill, TCN seat, per-model metric fix, A1 P(hold) live.
- **Add (the ceiling lift — see V8):** A4 spot-vs-perp (bridge `perp_cvd_live` → buffer + `candle_ts`
  overlay, then the slots), options positioning (GEX→feature once `gex_live` has history, 0DTE
  gamma, skew, put/call), L2 depth via live B1 accumulation (or Tardis archive).
- **Tune:** A7 Optuna (per-horizon, purged walk-forward). **Warm-start:** OOF calibration/meta/
  signal_history (kill post-retrain dormancy).
- **Precision tier:** A10 fingerprints → **rebuild the inverted grade** (regime/maturity-aware) →
  kNN voter → T3 Wilson-LB gate (n≥100, ≥80% LB). A1-ext path labels live.
- **Wire the leak-free heads** (P(beat)/path/magnitude/fingerprints) into the card + the composer,
  then validate the whole stack with `composed_decision_scorecard.py` (the integration metric).

## PART 3 — Validation gates (every lever clears these before adoption)
- **Per-lever, purged walk-forward sign-truth** — one change → measure → adopt only if it beats the
  incumbent. No bundling unmeasured changes.
- **Too-good = leakage** — honest 5m AUC ≈ 0.50–0.55; AUC ≫ that ⇒ audit feature↔label alignment (§5bs).
- **A stratifier must stratify** — A/B/C or confidence bins must show monotone sign-truth (top≥bottom,
  each n≥100, top-LB > bottom-rate) before being surfaced or gated on (§5br).
- **The integration number** — `composed_decision_scorecard.py`: the gated top tier's Wilson-LB must
  clear break-even before the betting layer ships (the V9 "paper-tracked positive EV" gate, seeded).

## PART 4 — Independent model research (which model actually earns a seat)
Two self-contained research harnesses. Both are INDEPENDENT — they do NOT touch the live ensemble,
the schema, serving, or `saved_models/`; they only emit reports. Both reuse the LEAK-FREE beat
builder / app tensor shape so results are honest (the §5bs lesson: re-deriving features is how
leakage creeps in).

- **`model_bakeoff.py`** — trains MANY model families on the SAME leak-free beat task
  (P(close≥open) per horizon) and writes `data/model_bakeoff_report.json`: per model × horizon —
  accuracy, precision, recall, F1, AUC, Brier, log-loss, **ECE (calibration)**, base-rate, the
  SIGNAL/NOISE verdict, and top feature importances. Probabilities are isotonic-CALIBRATED (for a
  prediction market, calibration > raw accuracy).
  - LIGHT (CPU, default): majority-baseline, logistic, random-forest, histgb (incumbent), lightgbm,
    mlp. Run gently alongside a train with `OMP_NUM_THREADS=2`.
  - DEEP (`--deep`, GPU, run AFTER a train): LSTM + Transformer on lookback sequences of the beat
    features. NOT-built (honest): CNN-LSTM/DeepLOB needs historical L2 (un-backfillable → live B1
    later); RL is out of scope (non-stationary reward).
  - Note: the "volatility-distance" baseline the research describes is the INTRA-window question
    (distance/time-left), which is **already** our A1 P(hold) model — not the at-open beat task here.
- **`seq_model_feasibility.py`** — the sequence-model gate (V8 theme 5): trains TCN/LSTM/Transformer
  on the app's exact (60×136) sequences with a purged split, reports sign-truth **and
  decorrelation-vs-TCN**. A seat is justified only on decorrelated lift, not raw accuracy.
- **`shadow_live_predictor.py`** — LIVE shadow test of the light models with ZERO app interference:
  a SEPARATE process on its own public Binance REST feed (read-only), own model file + output
  (`data/shadow/`), never the app's DuckDB/serving — no restart needed. `--start --hours N` trains
  then predicts each minute and self-resolves → `data/shadow/shadow_live_resolved.parquet`
  (predict_ms, ref_price, horizon, model, p_up, actual_up). The live analog of the backtest (expect
  the same coin-flip); its lasting value is the shadow-lane TEMPLATE for when a model is worth it.

**How to read it tomorrow (the decision):** at 5m/15m, which families clear SIGNAL, and does any
model beat lightgbm/histgb with a LOWER ECE (better-calibrated)? Expected (docs): lightgbm ≈ histgb
on top; DL overfits at this scale; nothing escapes the 5m information ceiling — the beat label is
just cleaner. If a family shows a calibrated edge, it becomes a challenger for the betting/fair-value
layer (P(beat)); it does NOT replace the direction stack.

## NEXT STEPS (ordered)
1. **Tomorrow AM:** PART 1 checklist (delete leaked heads → 4 analysis scripts incl.
   `composed_decision_scorecard.py` → restart rebuilds heads leak-free → read the 5m gate).
2. **Read `data/model_bakeoff_report.json`** (the light run finishes overnight) — pick the
   best-calibrated tabular family for P(beat); confirm DL isn't needed yet.
3. **After the train (GPU free):** run `model_bakeoff.py --deep` and `seq_model_feasibility.py --run`
   to settle the sequence-model question with real data.
4. **Decide the keeper window** (60–90d) and assemble the PART 2 bundle — one measured lever at a time.
5. **Wire the leak-free heads** into the card + composer; validate end-to-end with the composed metric.
