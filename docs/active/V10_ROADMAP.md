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
   - `python backend\research\standalone\diagnose_model.py` — §1 horizon health (did 5m move off ~50%?), §3 dead
     features (B1 has rows now), §5 grade (still inverted?).
   - `python backend\research\standalone\sign_truth_scorecard.py` — 5m committed-lean sign-truth + UP/DOWN balance.
   - `python backend\research\standalone\composed_decision_scorecard.py` — **the end-to-end metric** (does the gate
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
- **`trading_edge_backtest.py`** — the TRADING-EDGE yardstick: turns P(up) into a cost-aware
  BUY/SELL/AVOID strategy and measures what decides tradability — **expectancy, profit factor,
  Sharpe, max drawdown, hit-rate, coverage** — after fees+slippage, out-of-sample (LightGBM+isotonic
  trained on the past, backtested on the unseen future, non-overlapping windows + h-bar embargo,
  cost swept 0/5/10 bps, edge-threshold δ swept). Writes `data/trading_edge_report.json`. The
  HONEST yardstick: expect ~0/negative expectancy on today's features (a "proven edge" needs forward
  LIVE measurement — the shadow lane + composed scorecard); it becomes meaningful once L2/order-flow
  lands. A "proven trading edge" can NEVER be backfilled — only measured forward.

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

---

## PART 5 — EXECUTION-DECISION LAYER (the Phase-16 pivot, 2026-06-14)

**Why:** Phase 16 proved the bottleneck moved from prediction → execution. The 5m signal
edge is real but break-evens at **0 bps cost** (gross +0.04 bps); taker fees / fixed holds
kill it. The next edge is **execution discipline**, not another feature. Operator split:
**70% execution / 20% side-rule / 10% features**; broad feature-hunting is PAUSED.

### ✅ DONE NOW (offline-safe — built & self-tested while the 60-day retrain ran; no DB lock)
- **`backend/decision/` package** (pure-Python, unit-tested, pyflakes-clean, needs NO retraining):
  - `cost_gate.py` — the hard rule `expected_move / expected_cost ≥ 2.5` + a 3-mode exec-cost
    model (MAKER_MAKER 0 / MAKER_TAKER 7 / TAKER_TAKER 14 bps base + spread). Reproduces Phase 16.
  - `decision_composer.py` — the live single-tick ladder `NO_TRADE/WATCH/T1/T2/T2_SHADOW/T3`,
    **reusing the validated `rules/microstructure_side_engine.py`** for SIDE (no new ML side
    model — XGBoost failed). T3 is hard-gated on `maker_fill_proven` (default off) → caps at
    `T2_SHADOW` until live shadow proves fills. A cost-gate FAIL → WATCH (not NO_TRADE).
  - `event_exits.py` — deterministic event-driven exits (hard_stop, mfe_target, opposite_rule,
    basis_normalized, vpin_resolved, tradability_decay, move_remaining_low, anchor_cross,
    max_hold backstop). Phase 16 proved fixed-time holds fail.
- **`train_selectivity_models.py`** → persisted `data/saved_models/selectivity_models.pkl`
  (selectivity / tradability / fail-fast LogReg + expected-move Ridge + 60d side thresholds).
  This is a tiny offline fit on `research_matrix_1m.parquet` — **NOT the 136-feature ensemble retrain.**
  - ⚠️ **AUDIT FLAG:** OOS AUCs came in ABOVE the research docs (selectivity 0.739 vs ~0.720,
    tradability 0.793 vs ~0.739, **fail-fast 0.613 vs ~0.537**). Per our "too-good = leakage"
    rule these are **CANDIDATES, not trusted** until the audit below clears. Likely-benign cause:
    used the rule-composer feature list (`rv_15m/vpin_15m/compression_ratio/shock_magnitude`)
    rather than the canonical 6 keepers, on the current matrix. Expected-move Ridge is honestly
    weak (MAE 8.6 ≈ mean 8.1 bps).

### ⏳ DEFERRED TO V10 (needs the app running, live data, or validation — cannot do mid-train)
1. **[AUDIT] Selectivity-model leakage/parity audit** — reconcile the feature list vs the canonical
   6 keepers; confirm `tradable_move_label`/`fail_fast_label` alignment in the matrix; re-measure
   purged-walk-forward. Do NOT serve `selectivity_models.pkl` until AUCs are explained and clean.
2. **[WIRE] Live feature parity** — `compose_decision` needs `rv_*/vpin_15m/compression_ratio/
   shock_magnitude/perp_spot_basis_bps/cvd_divergence` computed at SERVE time with the same math as
   the matrix. `vpin/rv` exist live; **basis/cvd depend on perp flow which is geo-blocked** (V8/V10
   note) — needs a live recorder twin or a fallback (side engine loses its strongest rule without basis).
3. **[WIRE] Compose into `server.py`** — call `compose_decision` per tick from the persisted models;
   add `decision` (action/tier/side/reason/move_cost_ratio + gate diagnostics) to the WS payload.
4. **[WIRE] Live-shadow logging on the real feed** — connect `backend/live/live_shadow_logger.py`
   (currently standalone sqlite) to the live tick stream; add `shadow_orders` / `live_shadow_signals`
   tables to the app DuckDB (additive `CREATE TABLE IF NOT EXISTS`). Resolve after 5m/15m windows.
5. **[BUILD — LIVE DATA FIRST] Fill-quality family** — `P(Good_Fill)`, `P(Adverse_Selection_After_Fill)`,
   maker_fill_rate, time_to_fill. CANNOT be modeled offline (Phase 16); needs the shadow logger
   running for days/weeks first. This is the gate to unlocking T3 live.
6. **[BUILD] Rejection-reason analytics + live-calibration dashboard + drift downgrades**
   (`backend/analytics/rejection_reason_report.py`, `live_calibration_report.py`, tier-downgrade
   monitor). Report which gate blocks/saves the most trades; downgrade T3→T2→T1→WATCH on drift.
7. **[WIRE] Frontend `PrecisionSignalCard`** — the honest WATCH card: show every gate value + the
   rejection reason ("edge exists, cost too high — move/cost 1.28×"). Never a bare BUY/SELL.
8. **[WIRE] `event_exits` into the live position tracker** — needs live MFE/MAE + basis/vpin state.

**Promotion gate to LIVE_ACTIVE (unchanged):** ≥500 resolved live-shadow candidates, positive paper
EV after real fills, stable calibration, no fakeout-rate increase, maker fill quality proven.

### ✅ DONE (offline-safe additions, 2026-06-14 — built while the live model ran, nothing wired)
- **`audit_selectivity_models.py`** — ran the V10 PART-5 #1 leakage audit. **VERDICT: no leakage.**
  Naive `TimeSeriesSplit` AUC ≈ PURGED walk-forward AUC for all three heads (Δ≈0.000), and no single
  feature ≥ 0.85. The higher-than-research AUCs (sel 0.738 / trad 0.791 / fail 0.612) are a benign
  period difference, NOT a bug — the models are trustworthy for RANKING.
  - ⚠️ **BUT raw probabilities are NOT calibrated** (`class_weight='balanced'` inflates them: at P≥0.7
    realized rates are 0.51/0.21/0.02). **Serving must gate on the PERCENTILE thresholds**
    (`sel_t1≈0.88`), not the composer's hardcoded `0.95` probability floor, and needs an isotonic layer.
- **`composed_decision_backtest.py`** — ran the ACTUAL `compose_decision` over 84,955 purged-OOF bars.
  - **Discipline works:** 95.0% NO_TRADE (weak_selectivity), 4.6% WATCH, 0.4% T2_SHADOW, 0.1% T1.
  - **Honest EV (the hard truth):** even at **maker0 (0 bps)** the sided signals are **~0-to-negative**
    — WATCH+sided −1.57 bps, T2 −0.58 bps, T3 −17 bps (n=38, noise). Side win-rate ~46% (BELOW the
    research's 53–55%). So the deterministic side edge **does not reproduce positive gross EV under
    honest purged-OOF**, consistent with Phase 16's "≈0 gross, break-even at 0 bps". **The system's
    value today is the 95% disciplined abstention, NOT a tradable signal. Stays WATCH/shadow.**
  - Open question for research (20% side-rule bucket): why side win-rate decays vs the probes (purged
    OOF vs their split? threshold/regime-split differences?). Re-examine before trusting any tier live.
- **`drift_monitor.py`** — pure tier-downgrade rules (T3→T2→T1→WATCH→NO_TRADE) on live calibration
  drift / negative live EV / fakeouts. Self-tested. Feeds the (deferred) live loop.
- **`shadow_store.py`** — a **SEPARATE DuckDB** (`data/execution_layer.duckdb`, NOT `analytics.duckdb`)
  holding `shadow_signals` / `shadow_orders` / `rejection_events`. DuckDB is single-writer per file,
  so the execution layer can be written/queried with ZERO contention or risk to the live ensemble's
  DB. Created empty + self-tested; offline analytics (`rejection_summary`, `tier_scorecard`) read it.
  This is the foundation the (deferred, sign-off-gated) live-shadow wiring writes to.
- **`composed_decision_backtest.py --persist`** + **`shadow_report.py`** — an INDEPENDENT,
  no-retrain pipeline: replay the 60-day matrix through the real `compose_decision`, write every
  decision + resolution into `execution_layer.duckdb` (638 sided signals + 70,795 rejection_events),
  then report rejection breakdown / tier scorecard / calibration. Works entirely on the separate DB.
  - ⚠️ **Tiers are INVERTED (same grade-inversion as the direction stack):** T3 (p_big_move~0.995)
    wins **39.5%** vs T2 (~0.933) **46.3%** — the highest-selectivity tier wins LESS. Full-agreement
    clusters at exhaustion/reversal. So a naive "trust the top tier" gate is wrong here; the side
    edge does not improve with selectivity. Another reason it stays WATCH/shadow until live data.
- All are pure/offline, pyflakes-clean, and never touch `saved_models/` ensemble files or
  `analytics.duckdb`.

**OPERATOR NOTE — auto-retrain loop (2026-06-14):** with `BTC_FREEZE_MODEL=0`, the post-backtest
auto-learner flagged 3m (`acc=0.400, degrading`) and kicked off ANOTHER full ~6h retrain. But the
purged walk-forward shows ALL horizons `below_chance` (1m 0.36 → 30m 0.50) — that is the information
ceiling, NOT a defect retraining can fix. **RESOLVED:** set `BTC_FREEZE_MODEL=1` in `start.bat`
(2026-06-14). Boot now LOADS the saved v8 model (arch matches) → no startup retrain, no auto-retrain
loop. The edge today remains P(hold) + selectivity/abstention, not direction.

---

## PART 5 — STATUS LEDGER (consolidated, 2026-06-14)

**Runtime:** backend UP on :8000, serving the FROZEN 60-day v8 model (`BTC_FREEZE_MODEL=1`). The
execution-decision layer is wired into NOTHING — the running app is unchanged. Per operator rule,
nothing touches the live app/models without explicit sign-off ([[confirm-before-wiring]]).

### ✅ DONE — offline execution-decision layer (built, self-tested, isolated)
All in `backend/decision/` (+ reuses `backend/rules/microstructure_side_engine.py`):
| File | Role |
|---|---|
| `cost_gate.py` | hard `move/cost ≥ 2.5` rule + 3-mode exec-cost model |
| `decision_composer.py` | `NO_TRADE/WATCH/T1/T2/T2_SHADOW/T3` ladder (reuses side engine) |
| `event_exits.py` | 9 event-driven exits (no fixed-time holds) |
| `drift_monitor.py` | tier downgrade on live drift / negative EV / fakeouts |
| `train_selectivity_models.py` | fits + persists `selectivity_models.pkl` (NOT the ensemble) |
| `audit_selectivity_models.py` | leakage audit — **ran: no leakage** (gate on percentiles) |
| `composed_decision_backtest.py` `--persist` | full-stack proof; writes to the separate DB |
| `shadow_store.py` | **separate** `execution_layer.duckdb` (never `analytics.duckdb`) |
| `shadow_report.py` | rejection / tier scorecard / calibration analytics |

Honest results: 95% disciplined NO_TRADE; even at maker(0 bps) sided EV is ~0-to-negative; tiers
INVERTED (T3 wins < T2). System value today = abstention, NOT a tradable signal → stays WATCH/shadow.

### ❌ NOT DONE — live integration (0%, gated; cannot be done independently)
| Item | Blocker |
|---|---|
| Wire composer into `server.py` + WS payload | live-app integration → needs sign-off |
| Frontend `PrecisionSignalCard` (honest WATCH card) | needs the payload wired first |
| Wire `event_exits` into the live position tracker | live-app integration → needs sign-off |
| Live feature parity (`rv_*/vpin_15m/compression/shock` at serve time) | needs live feed; `perp_spot_basis_bps`/`cvd_divergence` are **geo-blocked** |
| Standalone live-shadow runner → `execution_layer.duckdb` | needs live feed + VALIDATED parity (no "no-parity feature") |
| `P(Good_Fill)` fill-quality + adverse-selection models | needs live-shadow fills that **don't exist yet** |
| Live calibration dashboard + live rejection analytics | needs the live-shadow data above |

### NEXT (operator picks)
1. **Build the standalone live-shadow runner** — independent process, own public feed, writes the
   separate DB; unblocks `P(Good_Fill)` + calibration. Needs careful parity work first.
2. **OR sign-off to wire `compose_decision` into `server.py`** so the dashboard shows WATCH/tier
   decisions (writes only to `execution_layer.duckdb`).
Promotion to LIVE_ACTIVE stays gated: ≥500 resolved live-shadow candidates, positive paper EV after
real fills, stable calibration, no fakeout increase, maker fill quality proven.

---

## PART 6 — POLYMARKET MISPRICING MODEL (the real ceiling-breaker, parked 2026-06-15)

**Thesis.** BTC direction is a coin-flip (proven 11 ways) — so stop predicting direction. The edge
that *can* exist is **MISPRICING**: the gap between our calibrated `P(Hold)` and the market's implied
price. This is the one path that breaks the ceiling, because it doesn't need a direction edge:
```
edge = calibrated_P(Hold) − market_ask − spread/cost_buffer
trade only if edge > 0 AND P(Hold) is calibrated AND line-cross risk is low
```
You need TWO histories joined: (1) BTC/oracle per-round (we have it → P(Hold)/P(Big_Move)/P(Tradable));
(2) **Polymarket** price/spread/liquidity per round (we do NOT have it yet → this part).

### Data sources (public APIs; app already has a live Polymarket CLOB WS to hook into)
| Source | Endpoint | Gives |
|---|---|---|
| Gamma API | discovery | events/markets/slugs/condition_id/**clob_token_id**/start-end/resolution |
| CLOB | `GET /prices-history` (+ batch) | historical UP/DOWN price series → implied prob, lag, overreaction |
| Data API | `GET data-api.polymarket.com/trades` | trade prints (price/size) — but **trust trade direction only from on-chain `OrderFilled`**, public-feed inferred side disagrees with ground truth |
| CLOB | `GET /book` | CURRENT orderbook (bids/asks/spread/depth) — **historical depth is NOT reliably free** (decommissioned; paid vendors exist) |

### Scripts to build (`backend/polymarket/`)
1. `fetch_markets_gamma.py` → `polymarket_markets.parquet` (find BTC 5m/15m markets, token IDs, times).
2. `fetch_price_history.py` → `polymarket_price_history.parquet` (UP/DOWN price vs seconds_left).
3. `fetch_trades.py` → `polymarket_trades.parquet` (trade prints).
4. `live_quote_recorder.py` → record `/book` every 1–2s during active rounds (best_bid/ask UP+DOWN,
   spread, mid, top_depth, seconds_left). **MANDATORY for realistic fill modeling; accrues from NOW.**
5. `build_round_replay_matrix.py` → join BTC/Pyth + round metadata + PM price/spread/liquidity +
   P(Hold) features + settlement → `polymarket_replay_matrix.parquet` (one row per second/update).

### Labels & models
- **Label 1 `hold_success`** (best): current side ahead now AND same side wins at expiry → trains
  `P(Hold_UP)/P(Hold_DOWN)` (maps to our strongest edge).
- **Label 4 `line_cross_risk`**: winning side flips before expiry (the danger model) — inputs
  distance_bps, seconds_left, vol_30s, recent_cross_count, tick_intensity.
- **`good_entry` / EV**: `EV = P(win) − price − buffer`; `good_entry = realized_roi > 0`.
- Models: **calibrated Logistic + isotonic FIRST** (P(Hold) is smooth in distance/time/vol), then
  LightGBM/CatBoost for P(Line_Cross)/P(Good_Entry); **DL only as challenger** (we proved sequence
  models don't beat tabular here). Validation: **round-level purged split**, no same-round leakage,
  report AUC + **Brier + calibration** (calibration matters more than accuracy) + precision@0.90/0.93/
  0.95 + Wilson-LB + realized ROI after spread.

### Decision logic (first production gate)
```
edge_up   = p_hold_up   − up_ask   − buffer
edge_down = p_hold_down − down_ask − buffer
if seconds_left > 120:                    WAIT
if |distance_bps| < min_distance:         NO_TRADE_LINE_RISK
if p_line_cross > 0.25:                    NO_TRADE_FLIP_RISK
if edge_up   > 0.03:                       T3_UP
if edge_down > 0.03:                       T3_DOWN
else:                                      NO_TRADE_NO_PRICE_EDGE
```

### What's offline-testable NOW vs needs live recording
- ✅ **Now (public APIs):** scripts 1–3 + a first `P(mispriced)` / entry-edge test on CLOB
  price-history vs settlement ("Option B — good enough for first training").
- ⏳ **Needs the recorder accruing for days/weeks:** spreads / fill quality / `P(Good_Fill)` — script 4
  (the live `/book` recorder). Realistic execution modeling cannot be done from public history.
- ❌ Historical orderbook depth — not reliably free (paid vendor only).

### Next action when resumed
Build scripts **1–3 + the first `P(mispriced)` test** (offline-testable), and stand up **script 4**
(live quote recorder) so spread/fill data starts accruing immediately. The model we actually want is
**calibrated P(Hold) + a Polymarket mispricing detector**, NOT a BTC up/down predictor.

---

## PART 7 — CHAMPION / CHALLENGER FRAMEWORK (strategy parked 2026-06-15, NOT implemented)

**Decision (operator, 2026-06-15):** keep the champion/challenger idea as V10 strategy — do NOT build now.
The frozen `v11-pruned69` ensemble is the **champion**. A challenger is trained separately, scored
head-to-head on the same OOS/live stream, and **promoted only if it beats the champion on a pre-set
gate** — never auto-promoted (per [[confirm-before-wiring]]). Offline-safe: separate saved-models dir +
separate `challenger_eval.duckdb`, never touches the frozen champion or the live `analytics.duckdb`.

### Why a keeper-augmented DIRECTION challenger will NOT win (measured, don't re-litigate)
Ablation on `research_matrix_1m.parquet` (HistGB, temporal 70/30 split, leak-free):
| Target | 6 keepers (rv_15/30/60m, compression, shock, vpin) | the 4 (rv_30m, rv_60m, compression_ratio, shock_magnitude) |
|---|---|---|
| **Direction (up/down)** | **0.503** | **0.509** |
| **Magnitude (big-move)** | **0.703** | **0.703** |
The keepers carry **strong magnitude signal, ZERO direction signal**. A challenger that adds them to a
**direction** ensemble ties the champion at ~0.50 — a ~6.4h retrain for a measured null. The keeper
magnitude signal is ALREADY deployed where it works: the band (`signed_quantile`), `P(big_move)`
(`selectivity`), and `P(Hold)` (`persistence` keeper model). **Direction stays at the information ceiling.**

### Option B (the challenger that CAN win) — Polymarket mispricing model
Point the first real challenger at **PART 6**: `edge = calibrated_P(Hold) − market_ask − buffer`. This is
NOT fighting the direction ceiling, so a second model can genuinely beat the champion's economics. The
harness scores it on shadow data (the live quote recorder's spreads + settlement) before any promotion.

### Option C (the "see-it-yourself" challenger) — keeper-augmented direction ensemble
Only to settle it empirically. To build: add rv_30m/rv_60m/compression_ratio/shock_magnitude to
`features.py` (136→140; parity already proven via `live_keepers.py`), train a SEPARATE bundle with its
own arch tag + `saved_models/challenger/` dir, score head-to-head vs `v11-pruned69` on the OOS holdout +
live shadow. **Expected: a tie (~0.50 direction).** Build only if you want the head-to-head with your own eyes.

### Harness sketch (when built)
`backend/champion_challenger.py` — loads both bundles, scores both on the same OOS/live candle stream,
writes per-prediction rows + a head-to-head summary to a separate `challenger_eval.duckdb`. Reports
sign-accuracy, Brier, calibration, and (for Option B) realized EV after spread. **Promotion gate:**
challenger replaces champion only if it beats it on the target metric by a margin on ≥500 resolved
samples with stable calibration — and only on explicit operator sign-off. Otherwise it stays shadow.

### Next action when resumed
Decide the first challenger target (recommended: **Option B**, the Polymarket mispricing model — it's
the only one with measured upside). Build the harness + `challenger_eval.duckdb` first, then the
challenger model. Option C is available as an empirical sanity check but is a measured null for direction.

---

## PART 8 — 180-DAY MULTITARGET FORECASTER RESULTS (research, 2026-06-16) — CLOSES THE BTC-SIDE BOOK
The expanded forecaster (`backend/research/train_360d_multitarget_forecaster.py`, run at **180 days**) —
~14 targets × {5m, 15m} × 8 tabular models + LightGBM/GBR quantile + CQR. Test = **newest 20% (51,789
samples)**. This is the larger-scale repeat of the 90-day bakeoff (VNEXT §12).

| Target | Best model | Result | Verdict |
|---|---|---|---|
| **Direction 5m** | RF | 51.6% / **AUC 0.528** | **COIN-FLIP** |
| **Direction 15m** | RF | 51.1% / **AUC 0.526** | **COIN-FLIP** |
| **Big-move 5m** | CatBoost | 74.3% / **AUC 0.745** | USEFUL |
| **Big-move 15m** | CatBoost | 70.1% / **AUC 0.707** | USEFUL |
| **Exact price 5m** | *current-price baseline wins* | $60.71 MAE | NOT predictable |
| **Exact price 15m** | *baseline wins* | $104.84 MAE | NOT predictable |
| Return 5m / 15m | ExtraTrees / RF | 8.76 / 15.22 bps MAE | weak |
| High/Low/Range 5m | ElasticNet | ~5.9 bps MAE | USEFUL (beats baseline) |
| High/Low/Range 15m | ElasticNet | ~10.4 bps MAE | USEFUL |
| Volume 5m / 15m | CatBoost | 0.45 / 0.41 log-vol MAE | USEFUL |
| Quantile bands | LightGBM / GBR | coverage **81–83%** | CALIBRATED (~80%) |

**VERDICT (13th independent confirmation):** 180 days — 2× the data, 4× the compute of the 90-day run —
reproduces the **exact same conclusion**. Direction is a coin-flip (more data ≠ signal: variance, not bias,
is the limit); predicting **exact price is impossible** (the naive "current price" baseline beats every model).
The genuinely useful heads are **big-move, range, high/low, volume**. → This **confirms the BTC-side FREEZE**
— no reason to expand BTC modeling further; the frozen stack (fair_value / danger / timing / band) stands.
The product should decide via **big-move + range + P(Hold) + abstention, NOT raw direction**.

**Operational:** GBR quantile ≈ **2 h/target** (CPU) — too slow; script patched with
`--quantile-backends lightgbm` + `--skip-{regression,classification,quantile,sequence}` for fast GPU
quantiles. Outputs: `data/research/forecast_360d_*.csv`, `FORECAST_360D_RESEARCH_RUNBOOK_2026-06-16.md`.

**Make-or-break is UNCHANGED:** still `fair_value − ask − buffer` on recorder data. This run closes the book
on BTC-side direction research — the next lever is the **execution/edge layer**, not more BTC models.
