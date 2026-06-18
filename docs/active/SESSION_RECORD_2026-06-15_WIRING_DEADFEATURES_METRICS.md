# Session Record — Wiring, Dead-Feature Plan, Metrics Logging, Auto-Finetune (2026-06-15)

Complete record of what was **discussed, tried, changed, and implemented** in this session.
Companion to: `PRICE_TO_BEAT_MODEL_ANALYSIS_2026-06-14.md` (research synthesis),
`ENSEMBLE_ENHANCEMENTS_AND_TESTS_2026-06-15.md` (alt-session test log),
`PROJECT_STATE_FOR_EXTERNAL_REVIEW_2026-06-15.md` (self-contained brief),
`TRAINING_PLAN_DISCUSSION_2026-06-15.md` (95%/90d/auto-finetune analysis).

---

## 0. Bottom line — now externally validated
Direction is dead (confirmed **11 ways**); the product is **P(Hold) late-entry + abstention +
calibration**; the real ceiling-break is **Polymarket mispricing** (P(Hold) vs implied price), which
can't be backfilled. This session: **two independent external models (ChatGPT + another) reached the
SAME conclusions** as our measurements and the alt session — strong convergent validation. The work
this session was *wiring the app around the few things that survived* and *making the records honest*,
not building bigger models.

---

## 1. What we IMPLEMENTED (code on disk, validated)

| Change | File(s) | Result |
|---|---|---|
| **RF added to selectivity (timing) ensemble** | `decision/train_selectivity_models.py` | `VotingClassifier(lr+rf)`, OOS **0.741 vs 0.739** (+0.002); drop-in `predict_proba`; saved to `selectivity_models.pkl` |
| **RF persistence correction for main ensemble** | `model.py`, `model_verifier.py`, `src/main.js` | RF is now a persisted/measured 7th direction seat: train -> save/load -> fallback weights -> inventory -> live accuracy roster. Arch bumped to v10 so stale v9 bundles retrain once. |
| **30m visibility/cadence correction** | `model.py`, `server.py`, `index.html`, `src/main.js` | Added 30m lock (`1780s`) and exposed 30m in scoreboard, price-to-beat tabs, replay default, exchange verifier, and UI timeframe tabs. |
| **Conformal band FIX → honest 80%** | `train_signed_quantiles.py` | recalibrate `cqr` on the **most-recent 20%** → coverage **80.0% at every horizon** (was 68–73%) |
| **Keeper P(Hold) + signed band wired live** | `server.py`, `price_to_beat.py`, `live_keepers.py` | fallback-safe; card serves keeper P(Hold) + calibrated asymmetric band on restart |
| **Card: abstain-on-direction + band labels** | `src/main.js` | AVOID labels direction "~coin-flip, informational"; band shows "80% (calibrated)" not "50%" |
| **Model-metrics DuckDB (NEW)** | `model_metrics_logger.py` + `server.py` | separate `data/model_metrics.duckdb`; logs every model's per-horizon output each tick (`direction_log` + `ptb_log`), crash-safe, never touches the live DB |
| **Dead-feature classifier (NEW)** | `dead_feature_classifier.py` | maps all 136 features → source → action: **57 KEEP / 12 PARITY-FIX / 63 RETIRE / 4 RECORD-LIVE** |

All compile + `pyflakes` + `node --check` clean; each new tool has a `--selftest`.

---

## 2. The DEAD-FEATURE PLAN (the classifier output — the actionable per-feature map)

"81 dead in backfill" decomposed by **why** (`dead_feature_classifier.py`):

| Action | n | Source | What to do |
|---|---|---|---|
| **KEEP** | 57 | klines | live-active, perfect parity — no action |
| **PARITY-FIX** | 12 | aggTrades | `cvd_change/1m/5m, trade_intensity, vpin, cvd_delta_divergence, delta_ratio, delta_acceleration, flow_efficiency, cvd_slope_divergence, large_trade_delta/imbalance` — real signal; **verify the training matrix fills them from aggTrades** (they appear dead only in a klines-only build) |
| **RETIRE** | 63 | orderbook(26) / derivatives(13) / options(4) / liquidations(4) / crossasset(8) / external(8) | external feeds not in backfill; **proven no direction edge** (depth 0.53, cross-venue 0.52) → drop from training schema (neutral hygiene). **Exception: keep `basis_spread/basis_velocity` for the SIDE rule.** |
| **RECORD-LIVE** | 4 | polymarket | `polymarket_relevant_event/probability_change/liquidity/event_shock` — **CANNOT backfill; the real edge** → record live (the frontier) |

**Why dead (3 root causes):** (1) the backfill has only spot klines + spot aggTrades — anything needing
another feed is constant; (2) geo-block (futures funding/OI/liquidations + Coinbase blocked from India);
(3) parity gap (aggTrade features look dead in a klines-only build but are filled by
`backfill_trade_features.py`).

**Honest expectation:** filling/retiring dead features is **hygiene, not accuracy** — the 81-removed
test was EXACTLY NEUTRAL (big_move 0.7524, dir 0.5194 either way), and every fillable category was
already tested → no direction edge. The keepers that DO carry signal (rv/vpin/compression/shock) are
already filled and in the heads.

---

## 3. What we TRIED / TESTED this session

| Test | Result | Verdict |
|---|---|---|
| **P(Hold) dynamics features** (`phold_dynamics_probe.py`: distance_velocity, line_cross_count, time_since_last_cross) | 5m 0.730→0.730, 15m 0.743→0.744 (+0.000/+0.001) | ❌ no lift → **not built** (measure-first saved a rebuild) |
| **RF in selectivity ensemble** | +0.002 OOS | ✅ adopted (harmless, tiny) |
| **Conformal recency-calibration** | 72% → **80.0%** coverage | ✅ real fix |
| **Selectivity calibration (isotonic)** | composer gates on **percentiles** (calibration-robust) | ✅ not needed |
| **Dead-feature classification** | 57/12/63/4 | ✅ actionable plan |
| **Main-ensemble feature pruning** | 136 raw → **69 model features** | ✅ wired in `model.py`; speed/RAM hygiene, not an accuracy promise |

---

## 4. What we DISCUSSED (decisions + analysis)

- **95% split / 90-day training:** will NOT raise accuracy (information ceiling); buys robustness only.
  Keep the band's recent calibration holdout. Direction ensemble already 0.95 + frozen. See
  `TRAINING_PLAN_DISCUSSION_2026-06-15.md`.
- **Auto-finetune:** the trainers are cheap (minutes); the serving loaders **cache-once** (no
  hot-reload), so a nightly refit needs a restart OR an mtime hot-reload. The real payload is
  **recalibration** (conformal cqr / isotonic drift with vol regime), not weight-chasing. Direction
  ensemble stays a manual ~6h job. (Proposed: `auto_finetune.py` + mtime hot-reload + Task Scheduler —
  NOT yet built.)
- **Meta-models "insufficient data: N/100":** these are the live **execution-quality** filter
  (`TrainedMetaModel`), which learns from the app's OWN served predictions + live order-book context —
  features that **don't exist in backfill**. Can't be seeded historically; benign (pass-through until
  trained); and would mostly learn "abstain" anyway (coin-flip after cost).
- **Adding more models to selectivity:** measured +0.002–0.003 (LR+RF / +histgb) — at the ceiling.
- **External-model review:** both ChatGPT and the other model independently confirmed the strategy and
  flagged the **Polymarket price comparison** as the real ceiling-break.

---

## 5. What CHANGED (files this session)
- **New:** `model_metrics_logger.py`, `dead_feature_classifier.py`, `phold_dynamics_probe.py`,
  `PROJECT_STATE_FOR_EXTERNAL_REVIEW_2026-06-15.md`, `TRAINING_PLAN_DISCUSSION_2026-06-15.md`, this file.
- **Modified:** `decision/train_selectivity_models.py` (RF selectivity ensemble), `model.py`
  (main-ensemble RF persistence + v11 pruned-69 arch + 30m lock + train/serve feature mask),
  `model_verifier.py` / `src/main.js`
  (RF live accuracy), `train_signed_quantiles.py` (recency-cqr → 80%), `server.py`
  (metrics logging + 30m scoreboard/replay/exchange verification), `index.html`
  (30m tabs), `src/main.js` (abstain + band labels + 30m PTB/replay),
  `public/guide.html` (enhancements section), `V3_CHANGES_AND_AUDIT.md` (log entries),
  `ENSEMBLE_ENHANCEMENTS_AND_TESTS_2026-06-15.md` (corrected stale 75%→80% band line).
- **Models on disk (current):** `selectivity_models.pkl` (voting lr+rf), `signed_quantile_model.pkl`
  (80% band), `persistence_model.pkl` (dual base + keeper P(Hold)).

---

## 6. What WORKED vs what DIDN'T (cumulative, this + prior sessions)
**✅ Worked / kept:** keeper P(Hold) (+0.019 T3), calibrated 80% asymmetric band, selectivity RF
ensemble (+0.002), `live_keepers` parity, metrics logging, abstain-on-direction.
**❌ Rejected (don't re-chase):** direction (11 ways), more models/features/depth for direction,
sequence models on timing (tie tabular), Markov entropy (A15), dwell/time-up-down, P(Hold) dynamics
features, trading the timing gate directionally (−21.63 bps), retiring dead features *for accuracy*
(neutral).

---

## 7. What's LEFT (priority order)
1. **Live Polymarket price logger + edge scorecard** — `P(Hold) − implied_price − spread`. The ONLY
   path to *profit* (vs. calibrated probability); can't be backfilled. **The frontier.**
2. **Parity-fix** the 12 aggTrade flow features (verify they're filled in training; close the train/serve gap).
3. ~~Retire the 63 external-feed features from the training schema~~ **DONE 2026-06-15 as a model-local
   mask:** `model.py` now trains/predicts on 69 features (`KEEP` + `PARITY-FIX`) while preserving the
   136-feature app schema for UI/replay/live diagnostics. Arch bumped to
   `v11-pruned69-7977e0559560`; next boot retrains once, then loads cached pruned models.
4. ~~Auto-finetune~~ **DONE 2026-06-15**: mtime hot-reload in `price_to_beat.py` (both loaders,
   throttled 30s, crash-safe) + `auto_finetune.py` (nightly refit/recalibrate the 3 cheap heads;
   `--with-backfill --days N`) + Task Scheduler command (below). Refreshed `.pkl`s go live within 30s,
   **no restart**. Recalibration payload; zero accuracy expectation by design.
5. **Restart** (FREEZE=1, no retrain) to serve the wired heads + start the metrics DuckDB.

### Task Scheduler (nightly auto-finetune, Windows)
```
schtasks /Create /TN "BTC_AutoFinetune" /SC DAILY /ST 04:00 /TR ^
  "cmd /c cd /d C:\Users\rahul\Documents\BTC-Prediction-Tool && python backend\auto_finetune.py --with-backfill --days 90 >> data\auto_finetune.log 2>&1"
```

---

## 8. Closing verdict
The session confirmed — now with **independent external agreement** — that this app improves not by
predicting direction harder but by being **precise where precision exists**: a calibrated 80% band, a
keeper-boosted P(Hold), a selectivity gate, honest abstention, and full model-metrics logging. The
dead features are now a concrete plan (keep/parity-fix/retire/record-live), and the one remaining edge
is the **Polymarket mispricing** comparison — live data, not a bigger model.
