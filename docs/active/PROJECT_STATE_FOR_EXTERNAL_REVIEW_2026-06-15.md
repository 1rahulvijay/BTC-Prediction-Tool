# BTC Prediction Tool — Self-Contained State & Decisions (for external review, 2026-06-15)

**Purpose:** a single, self-contained brief so an external model/reviewer can understand the whole
project in one read — what it is, what was measured, what's built, what's open, and *why* each
decision was made (with evidence). You do NOT need the other docs to follow this; pointers are at the
end for depth.

---

## 1. What the app is
A real-time BTC prediction tool for **Polymarket-style 5-minute & 15-minute "price-to-beat"** markets
(will BTC close above/below a reference price in N minutes). It ingests Binance spot (live + 60-day
backfill of aggTrades), Pyth (the Polymarket settlement-oracle proxy), and order-flow/derivatives
feeds, and serves a decision card. Runs locally on a 16 GB Windows laptop (GPU present, 6 GB).

## 2. The single most important finding (measured, not assumed)
**5-minute BTC direction is a coin-flip — it is information-limited, not model-limited.** This was
confirmed ~10 independent ways:

| # | Method | Direction result |
|---|---|---|
| 1–3 | 5-way bakeoff / trading-edge backtest / live shadow | AUC 0.50–0.54 / ~0 expectancy |
| 4 | L2 order-book depth probe | AUC 0.53 |
| 5 | 17 microstructure features (CVD, taker-ratio, whale flow, cross-venue divergence, OFI, autocorr, variance-ratio, price-impact, absorption, trend) | all dir AUC ~0.50 |
| 6–7 | SGD (scaled) + **14 model families** (logistic, sgd, RF, extra_trees, histgb, gradient_boost, adaboost, knn, gaussian_nb, qda, mlp, lightgbm, xgboost, catboost) | all NOISE 0.49–0.54 |
| 8 | Deep sequence (LSTM/Transformer/TCN, 7 horizons) | 49–52% (loss collapses, test acc frozen = overfit) |
| 9–10 | Capstone w/ cross-venue features + **145 engineered features × 14 models** | dir best 0.536 (5m) / 0.535 (15m) — still NOISE |

**Implication:** more data, more features, more models, and deeper models all fail to beat the
direction ceiling. (This is the most-tested conclusion in the project — external reviewers should
treat "just add model/feature/data X for direction" as already-refuted unless they have a genuinely
new *information source*, not a new model.)

## 3. What IS predictable (the two real edges)
- **Timing — `P(big_move)`** ("will this 5m window move enough to matter?"): AUC **~0.72** (LogReg/RF
  ensemble on volatility keepers: realized_vol, range_compression, intensity, vpin, liquidity_shock).
  It is **direction-invariant** (says *when*, not *which way*) and **saturated** (adding models/features
  past the keepers gives ≤+0.003). Use = a **selectivity gate** (AVOID dead rounds).
- **Late-entry persistence — `P(Hold)`** ("price is already ahead with little time left — does it hold
  to close?"): measured on 1.95M snapshots, hold-rate by (seconds_left × distance) is clean and
  monotone: e.g. **0–30 s left & ≥10 bps ahead = 99.7%** (Wilson-LB 99.6%); 5–10 bps = 97%; 2–5 bps =
  87%. **This is the one high-precision product.** It is direction-INVARIANT — you ride the side that's
  *already* winning, you never predict the side.

## 4. The honest economic reality
- The directional staged flow (selectivity → side rules → bet a side) **loses even at 0-cost maker**
  (Net EV −0.6 to −17 bps; win 39–46%) because the side is a coin-flip. Gross directional EV is
  ~+0.04 bps → any fee kills it. **Don't bet a side, even gated.**
- The **expected drop/high band** (signed-quantile, conformally calibrated) is honest: **80% coverage
  at every horizon** (after a recency-calibration fix; was 72%). It says nothing about direction — it's
  a calibrated *range* of the move size.
- **The product is an abstention machine:** mostly NO_TRADE; predict a calibrated band; commit a side
  only via late-entry P(Hold); and the open question is whether P(Hold) beats Polymarket's *implied
  price* (only testable live).

## 5. Architecture (heads compose; they do NOT merge into one model)
- **Layer 1 — direction stack** (per regime × horizon ensemble {xgb,lgb,cat,histgb,lr,TCN} → OOF
  meta-stacker → P(up/down/neutral)). Coin-flip at 5m (the ceiling). **FROZEN** (`BTC_FREEZE_MODEL=1`)
  — retraining it is ~6 h for ~0 gain.
- **Layer 2 — decision composer (serving):** specialist heads answering *different* questions:
  - `P(big_move)` selectivity (`selectivity_models.pkl`, LogReg+RF voting ensemble, AUC ~0.72)
  - `P(Hold)` persistence (`persistence_model.pkl`, base + volatility-keeper features)
  - signed-quantile **band** (`signed_quantile_model.pkl`, q10/q50/q90 + conformal cqr → 80%)
  - magnitude / path / tradability / fingerprint heads + a deterministic microstructure side engine
  - a `decision_gate` (NO_TRADE / WEAK_LEAN / TRADE with reasons)
- **Everything on the card is an ML output;** the final BUY/SELL/AVOID is a *rule-based gate* on those
  ML probabilities (correct: a coin-flip direction must be gated, not "predicted" by yet another model).

## 6. What is built, validated, and wired (as of 2026-06-15)
- ✅ All 3 product heads trained, on disk, load cleanly; **keeper features confirmed in each**.
- ✅ Live wiring: server computes 6 volatility keepers/tick → keeper `P(Hold)` + calibrated band feed
  the card, **fallback-safe** (reverts to prior behavior if a head/keepers are absent).
- ✅ Card: abstain-on-direction (labels a coin-flip lean "informational"), correct 80%-band labels.
- ✅ **Model-metrics logger** → a SEPARATE `model_metrics.duckdb` (never the live DB): logs every
  model's per-horizon output each tick (direction, P(up/down/neutral), P(Hold), tier, band, projection)
  for offline scoring.
- ✅ Validation: full compile/lint, model-load checks, leak-free probes with `--selftest`, parity test
  for the live keepers, sign-truth + Wilson-LB scorecards.
- ✅ Restart needs **no retrain** (FREEZE=1 loads saved heads in ~12 s).

## 6b. Latest builds (2026-06-15, this session)
- ✅ **Auto-finetune stack** — `price_to_beat.py` now **hot-reloads** the band/P(Hold) `.pkl`s on mtime
  change (throttled 30s, crash-safe), and `auto_finetune.py` nightly-refits the 3 cheap heads so a
  refit goes **live within 30s without a restart**. Payload = recalibration (keeps band/P(Hold) honest
  as vol drifts), zero accuracy expectation. Direction ensemble untouched (6h FREEZE=0 job).
- ✅ **Dead-feature classifier** — `dead_feature_classifier.py` maps all 136 features → source →
  action: **57 KEEP (kline), 12 PARITY-FIX (aggTrade), 63 RETIRE (external feeds, proven no edge),
  4 RECORD-LIVE (Polymarket)**. Turns "81 dead" into a concrete per-feature plan.

## 7. What is NOT done / open (where external input is most useful)
- ⚠️ **Live-shadow price edge (THE open question):** does `P(Hold)` beat Polymarket's **implied price**
  at entry? A 99.7%-hold quoted at 97¢ = +2.7% edge; at 99.9¢ = none. **Only testable with live
  order-book data** — the one path to *profit* (vs. calibrated probability). The single highest-value
  remaining build.
- 🟢 **Dead features — RESOLVED to a plan** (`dead_feature_classifier.py`): 63 retire (neutral hygiene),
  12 parity-fix (aggTrade flow — verify filled in training), 4 record-live (Polymarket). Execution
  (schema trim + parity wiring) is mechanical and pending the operator's go.
- 🟢 **Auto-finetune — BUILT** (hot-reload + `auto_finetune.py` + Task Scheduler). Just needs the
  scheduler entry created on the box.

## 8. The discipline (why decisions were made this way)
1. **Too-good = leakage.** An honest 5m AUC is 0.50–0.55; anything ≫ that is presumed leaked until the
   feature↔label time-alignment is audited.
2. **Measure before you build.** Every feature/head is probed leak-free (offline, `--selftest`) BEFORE
   wiring. This repeatedly saved whole builds (entropy head, P(Hold) dynamics, depth — all probed →
   rejected at +0.00).
3. **A stratifier must stratify** (monotone sign-truth, n≥100, top Wilson-LB > bottom) before it gates.
4. **Calibration > accuracy here.** Since accuracy is ceilinged, the lever is calibrated probabilities
   (so they're comparable to a market price). Don't trade calibration holdout for marginal training rows.
5. **Don't chase a proven dead end** (direction). Build selectivity + late-entry instead.

## 9. Questions an external reviewer could genuinely help with
- Is there an *information source* we haven't tried that could carry 5m direction (vs. just another
  model/feature, which we've exhausted)? Realistic candidates given India geo-blocks: ?
- For the live-shadow: best design to estimate the **P(Hold)-vs-implied-price** edge with minimal live
  data + maker-fill realism?
- Is the abstention/late-entry framing the right product, or is there a defensible directional angle we
  dismissed too early?

## 10. Pointers (depth, if needed)
- `docs/active/PRICE_TO_BEAT_MODEL_ANALYSIS_2026-06-14.md` — full probe outputs + the wiring plan.
- `docs/active/TRAINING_PLAN_DISCUSSION_2026-06-15.md` — 95%/90-day/laptop/auto-append analysis.
- `docs/active/V3_CHANGES_AND_AUDIT.md` (§ latest) — the chronological change/validation log.
- `.claude/skills/quant-ml-expert/SKILL.md` — the operating manual (the 7 hard rules + proven facts).
- `public/guide.html` — the end-user "how to read the card / how to bet" guide.
