# V3 Changes & Deep Audit (pre-retrain)

**Date:** 2026-06-10. **Purpose:** document every change in the V3 "NOW" batch and record the
correctness/integration audit, so the upcoming retrain bakes in verified code.
**Bottom line:** all changes validated; one real bug found and fixed during audit (action-log
UNION SQL); feature pipeline builds end-to-end to 117 features with no NaN; the model is fully
dynamic on feature count, so the retrain adapts cleanly.

---

## 1. Change inventory

### New files
| File | Purpose | Validation |
|---|---|---|
| `backend/trade_features.py` | Shared CVD/VPIN/divergence functions — the **train/serve consistency keystone**; used by BOTH the live recorder and the backfill | deterministic self-test PASS |
| `backend/backfill_trade_features.py` | Offline backfill: downloads `data.binance.vision` **SPOT** aggTrades + futures premium-index → per-1m-bar CVD/VPIN/funding-velocity parquet | validated on real 2026-06-08 data |

### `backend/features.py`  (109 → **117** features)
| Change | Slot | Notes |
|---|---|---|
| Appended 8 feature names | 109–116 | append-only; 0–108 untouched |
| `times` array extraction | — | for time_to_funding + ORB |
| `rolling_volume_profile()` helper | — | POC/LVN/value-area (real TPO) |
| `opening_range_breakout()` helper | — | 00:00-UTC anchored, 60-min range |
| **funding_velocity** (was `0.0` stub) | 17 | Δ funding-rate series |
| **liq_acceleration** (was `0.0` stub) | 45 | 2nd diff of liq imbalance |
| **volume-profile POC/LVN** (was VWAP-proxy / `0.0`) | 101/102 | real rolling profile |
| **time_to_funding** (was `0.0` stub) | 104 | cyclical `cos` of 8h funding clock |
| twap_deviation | 109 | price vs rolling TWAP |
| exhaustion | 110 | fade on fading range+volume |
| volume_profile_value_area_pos | 111 | position in rolling range |
| vpin | 112 | **inert (0) for now** — reserved; not yet live-recorded |
| cvd_delta_divergence | 113 | from `cvd_1m` series (shared fn) |
| oi_momentum | 114 | Δ of OI change |
| orb_position / orb_breakout | 115/116 | opening-range breakout |

### `backend/model.py`
- `MODEL_ARCH_VERSION` → `2026-06-10-v3-dirfeat-117-{arch}` → forces a one-time retrain on restart.

### `backend/signal_history.py`
- New `overlay_backfill(sig_hist, candle_ts, parquet_path)` — train-time overlay of historical
  CVD from the backfill parquet; **ms→s key conversion**, fills only bars lacking a live
  snapshot, defensive no-op if the parquet is absent.

### `backend/server.py`
- Train feature-build site: calls `signal_buffer.overlay_backfill(...)` (path `DATA_DIR/trade_features_backfill.parquet`) and logs cells filled.
- (earlier this session) Total freeze: `apply_learning_feedback` gated behind `not MODEL_FROZEN`.

### `backend/database.py`
- `fetch_action_log`: per-table `ORDER BY … LIMIT`, **each UNION branch parenthesized** (bug fix, see §2).

### `backend/price_to_beat.py` + `src/main.js`  (earlier this session, restart-pending)
- Price-to-beat = **direction only**: `_bet_lean` always surfaces a side (two-way prob fallback); `fair_value_cents` (value-betting) removed from compute + UI.
- Action-log fetch hardened: 8s timeout, keep-last-good on a failed poll.

---

## 2. Audit — findings & verdicts

| # | Check | Result |
|---|---|---|
| 1 | **Hardcoded 109 feature count anywhere?** | **No.** `model.py` derives `n_features` from `X.shape`; DL uses `input_dim=NUM_FEATURES`; dummy input `LOOKBACK*NUM_FEATURES`. Adapts to 117. ✅ |
| 2 | **Action-log UNION-ALL SQL** | **BUG FOUND + FIXED.** `ORDER BY/LIMIT` on an unparenthesized UNION branch is ambiguous; each branch is now parenthesized. Re-tested in an isolated DuckDB → parses + returns rows. ✅ |
| 3 | `_t_s` defined before ORB/time_to_funding use it | ✅ proven by a successful build (slots 104/115/116 populated, no NameError) |
| 4 | **Full training path** `build_features`→`build_sequences` | ✅ (1499,117) → (1424,60,117); labels one-hot valid; no NaN/inf |
| 5 | Revived stubs alive when signal-history present | ✅ funding_velocity std 0.81, liq_accel 0.245, cvd_divergence 0.60, oi_momentum, cvd all vary |
| 6 | `overlay_backfill` correctness | ✅ exact value match after ms→s; no-op on missing file; never overwrites live coverage |
| 7 | **Inference performance** (loops over recent klines) | ✅ inference builds on `klines[-1500:]` only (~0.15s) and is offloaded to a worker — no event-loop lag |
| 8 | Restart behavior | ✅ stale `MODEL_ARCH_VERSION` → `load_models` clears + returns False → startup retrain runs **even under freeze** |
| 9 | `vpin` (not yet recorded) | ✅ `series("vpin")` falls back to constant 0 — no error, identical in train + serve (no skew) |
| 10 | All edited files compile | ✅ 8/8 syntax-clean |

### Train/serve consistency (the make-or-break)
- CVD (`cvd_change/1m/5m`): live `order_flow.get_time_based_cvd` ↔ backfill reproduces the SAME
  rolling-window-at-close via the shared `trade_features` functions. Backfill uses **SPOT**
  aggTrades to match the live `btcusdt@aggTrade` feed. Timestamps normalized to **ms** in
  backfill, then **ms→s** in the overlay to match the kline `time` (seconds) convention. ✅
- `vpin` / `funding_velocity` from the backfill are **deliberately NOT merged** into training —
  the live recorder doesn't yet produce them identically, so merging would cause skew. They
  stay inert/derived-from-existing-keys until a dedicated follow-up. ✅

---

## 3. What happens when you restart (your retrain)
1. `load_models` sees arch `…v4` ≠ `…v3-dirfeat-117` → clears models, `is_trained=False`.
2. Boot logs `No compatible saved models found. Startup training is required.` and trains in
   the background (UI stays live). **This proceeds even though the model is FROZEN** — the
   schema rebuild isn't an auto-improve retrain.
3. Training builds 117-feature sequences; the **backfill overlay** runs (logs `Backfill overlay
   filled N CVD cells` if a parquet exists) — currently only the 1-day validation parquet, so
   N will be small until you run the full download (see §4).
4. After it lands: real volume-profile / exhaustion / TWAP / ORB / time_to_funding / funding-
   velocity / liq-acceleration are active immediately (kline-derived, full history).

## 4. Operator steps to realize the FULL backfill value
The validated retrain works now, but the *trade-derived history* needs the full download:
```
# run with the BACKEND CLOSED (RAM headroom), one-off:
python backend/backfill_trade_features.py --start 2026-05-10 --end 2026-06-09
# → writes data/trade_features_backfill.parquet (covers the 30-day train window)
# then start the app → it retrains and the overlay fills CVD history for the whole window
```
Notes: ~180–480 MB/day download, cached to `data/backfill_cache/` (deletable afterward).

## 5. Deferred (not in this batch — by design)
- **VPIN live-streaming** in `order_flow.py` (+ `vpin` key) so feature 112 becomes non-zero
  consistently. Until then 112 is an inert reserved slot (no skew).
- **cross_exchange_lead_lag (100)** real fix — needs per-bar Coinbase price recording (can't
  backfill free). Left as the existing proxy.
- **Backtest overlay** — the offline backtest still reads live-only coverage (slightly
  *pessimistic*, which is safe). Only the train path overlays the backfill.
- **Calibration / meta-labeling / per-model pruning** — data-gated (see `V3_NOW_VS_LATER.md`).

## 5b. Microstructure trade-flow batch (added 2026-06-10, features 117→**124**)
Implemented from a microstructure research brief (OFI / order-book / price-impact families).
**Only the backfillable / derivable ones were added** — they derive from the already
train/serve-consistent `cvd_1m` series + klines + OI, so they need **no new live recording,
no backfill-script change**, and ride the same backfill path as CVD.

| Feature | slot | research family | derivation |
|---|---|---|---|
| delta_ratio | 117 | OFI (#4) | `cvd_1m / volume` (normalized flow) |
| delta_acceleration | 118 | flow accel (#5) | Δ of per-bar `cvd_1m` |
| flow_efficiency (signed price impact) | 119 | price impact (#14) | `return / cvd_1m` |
| cvd_slope_divergence | 120 | divergence (#4, improved) | continuous `price_slope − cvd_slope` |
| rv_upside / rv_downside | 121/122 | semivariance (#13) | up/down realized-vol |
| price_oi_interaction | 123 | OI×price (#11) | `return × oi_change` |

`MODEL_ARCH_VERSION → …dirfeat-124`. **Audit:** build → (N,124), no NaN/inf, all 7 vary, no
regression on 0–116; sequences → (N,60,124), labels valid; model dynamic on feature count.
**Bug caught + fixed during scan:** feature 119 was assigned length-`n` instead of `n−1`
(missing `[1:]`) → broadcast error; fixed and re-validated.

### NOT added (honest deferral — top-ranked but LIVE-ONLY)
The research's #1–3 (multi-level **OFI**, **microprice edge**, **liquidity-adjusted flow**) and
**large-trade delta** need historical **L2 order-book** data, which we do **not** download and
cannot cheaply backfill (live feed is depth20@100ms; data.binance.vision L2 is coarse/huge).
Adding them now would create **dead columns** in this retrain (no history) + require order_flow
surgery. Correct path = a **record-now / retrain-later** follow-up: compute them live in
`order_flow.py`, record in `signal_history`, let them accrue 1–2 weeks, then retrain. Deferred
deliberately, not overlooked.

## 5c. Large-trade delta (added 2026-06-10, features 124→**126**)
Correction to §5b: **large-trade delta needs only trade data, NOT L2** — so it is backfillable
from the aggTrades we already download. Implemented as a full vertical slice:
- `trade_features.large_trade_per_bar()` — shared batch computation. A trade is "large" if its
  notional > `LARGE_TRADE_MULT(5)` × EWMA(`α=0.001`) of prior notional (adaptive, not a fixed $).
- `order_flow.py` — streaming EWMA with the SAME constants + per-trade `is_large` flag; `get_summary`
  emits `large_trade_delta` (Σ large signed notional / Σ notional, last 60s) + `large_trade_imbalance`.
- `signal_history.py` — both keys recorded + added to the overlay merge list.
- `backfill_trade_features.py` — writes both columns to the parquet.
- `features.py` — slots 124 (`large_trade_delta`) + 125 (`large_trade_imbalance`).
- `MODEL_ARCH_VERSION → …dirfeat-126`.

**Audit:** live-vs-backfill **consistency proven** (same trades → `large_trade_delta` live −0.4751 ==
backfill −0.4751, diff 3e-5; imbalance diff 2e-5). Build → (N,126) no NaN; both columns vary;
sequences (N,60,126). trade_features self-test extended + PASS.

**Pre-existing bug fixed in passing:** `recent_trades` deque was `maxlen=3000` (~3.4 min), which
**silently truncated the 5-minute `cvd_5m` window** live (backfill used the full 5 min). Raised to
**20000** (~22 min) so all time-window trade sums match the backfill even during bursts. (~6 MB.)

**Operator note:** the existing `trade_features_backfill.parquet` (from validation) predates the
large-trade columns — **re-run `backfill.bat`** to regenerate it with `large_trade_delta` /
`large_trade_imbalance`. The overlay skips missing columns gracefully (no error) if you don't.

## 5d. Deep scan, performance fix & L2 recording (2026-06-10)

### 🔴 Performance bug found + fixed (the important one)
`OrderFlowAnalyzer.get_summary()` is called on **every trade AND every depth update**
(server.py:643, 658 → ~25–110×/s). Each call scans `recent_trades` ~7× (the time-window
CVDs), so at `maxlen=20000` it cost ~7–14 ms/call → **at high trade rates it exceeded a full
CPU core**, risking the exact event-loop/price lag we'd fixed. (My `recent_trades` 3000→20000
bump for `cvd_5m` consistency amplified a pre-existing inefficiency.)
**Fix:** an internal **250 ms TTL cache** in `get_summary` — consumers (main loop ~3 s,
per-candle snapshot) only need ~per-second freshness. Measured: 5000 cached calls = **0.6 ms
total (0.1 µs/call)**; actual recompute now ~4×/s. Event loop stays free.

### L2 microstructure — RECORDING ONLY (accrue for a "V2" retrain)
Per the L2 plan, microprice + best-level OFI are computed from the **existing depth20@100ms**
stream (no new WS needed) and **recorded** into `signal_history`, but are **NOT yet model
features** (so they don't become dead columns in this retrain):
- `order_flow.process_depth`: `microprice_edge_bps` (volume-weighted mid vs mid, in bps) and
  `ofi_best` (Cont top-of-book OFI between consecutive snapshots, depth-normalized, EWMA-smoothed).
- `signal_history.KEYS`: `microprice_edge_bps`, `ofi_best` (recorded each candle; accrue going forward).
- After ~1–2 weeks of coverage → add them as feature slots + retrain a V2 model.
- NOT added: multi-level OBI velocity / liquidity-adjusted flow (need richer L2) and true
  event-level OFI (needs `depth@100ms` diff stream + local-book reconstruction) — future work.

### Audit of this pass
| Check | Result |
|---|---|
| Throttle returns cached object within TTL, recomputes after | ✅ (`s is s2`; recompute after TTL) |
| Throttle perf | ✅ 0.1 µs/call cached (was 7–14 ms uncached) |
| microprice/OFI compute from depth | ✅ non-zero, bounded |
| Not added as model features (no dead columns) | ✅ NUM_FEATURES still 126; names absent |
| Large-trade EWMA: per-day backfill resets vs continuous live | ⚠️ minor — ~1 min/day warmup mismatch at UTC day boundaries (α=0.001 keeps warmup short → ~0.07% of bars); accepted/documented |
| Syntax all touched files | ✅ |

## 5e. Deep scan #2 — logic fixes, VPIN alive, evidence-driven split (2026-06-10)

### Bugs found & fixed
1. **Price-to-beat advice used the wrong live lean.** `_refresh_live` read `rawDirection`
   (usually NEUTRAL) while the bet was opened via `_bet_lean` (two-way fallback) → advice
   said "lean faded to NEUTRAL → LOCK IN/EXIT" on nearly every tick. Now both use `_bet_lean`.
2. **Backtest didn't apply the backfill overlay** (train did) → the backtest would have
   evaluated the model on emptier features than it trained on. Overlay now applied in the
   live backtest path too (legacy `run_backtest_legacy_unused` left alone).

### VPIN made ALIVE (feature 112 was inert)
- **Streaming VPIN** in `order_flow.process_trade` — identical equal-volume-bucket algorithm
  to `trade_features.vpin_buckets`; proven equal: live 0.03583 == batch 0.03583 (diff 0.000000
  over 60k trades). Exposed in `get_summary`, recorded in `signal_history.KEYS`, merged by the
  overlay.
- **Fixed shared bucket constant** `DEFAULT_BUCKET_VOLUME_BTC = 15.0` (≈1 bucket/min; ~50 min
  rolling window; ~1 h live warmup) used by BOTH paths — the backfill previously calibrated
  per-run, which would have made historical VPIN a different feature than live VPIN (skew).
- ⚠️ Re-run `backfill.bat` so the parquet's VPIN uses the fixed constant.

### DuckDB evidence (9.6 h) → lean-source split
| Metric | Value |
|---|---|
| Internal 5m directional winrate | **64.4%** (n=87) — committed 3-class leans |
| Price-to-beat mirror 5m | **51.5%** (n=101) — diluted by two-way *fallback* leans |
| Conviction 5m | **INVERTED**: high(60+) 58% vs mid 73% — calibration target, small n |
| Regime 5m | LOW_VOL 58% (n=60) weakest; RANGE 73%, TREND_DOWN 87% (tiny n) |

The fallback leans (model near-neutral, side from probability tilt) are ~coin-flip; the
committed model leans are the real edge. Changes:
- `price_to_beat` entries carry **`lean_source`** ("model"/"fallback"); resolved history
  stores `(hit, source)`; `accuracy()` reports `model_accuracy`/`fallback_accuracy` splits
  (back-compat with legacy int entries).
- Live advice prefixes a **"[Weak lean …]"** warning on fallback rounds — bet guidance:
  *bet model leans, skip fallback leans.*
- UI accuracy strip shows `model X% (n) | all Y% (n)`.

### Validation
Streaming VPIN == batch (0.000000); ptb split test PASS (model 1.0 / fallback 0.0, warning
present, back-compat PASS); all backend files syntax-clean; `npm run build` clean.

## 5f. Backfill auto-runs from start.bat (2026-06-10)
- `backfill_trade_features.py --auto`: **incremental** — first run = full default window
  (multi-GB); later runs download only the days since the last covered date and **merge**
  into the existing parquet (dedupe by `candle_ts`, new wins); already-current = instant no-op.
- `start.bat` step `[0/3]` runs `--auto` before the app starts; `BTC_SKIP_BACKFILL=1` skips;
  a failure never blocks the app (overlay falls back to existing history).
- Stale validation parquet (capped read, old VPIN constant, no large-trade cols) **deleted**
  so the first auto-run rebuilds clean — otherwise `--auto` would have kept the junk history.
- Tested: "current → nothing to do" and "stale → incremental range" both verified.
- `backfill.bat` remains for manual/custom runs (`--start/--end`, `--keep-cache`).

## 5g. "100% NEUTRAL" diagnosis — gate calibration bugs (2026-06-10)

**Operator report:** "100% of time model is neutral." **DB evidence (24h):** the model is NOT
neutral — 5m raw leans are directional (newest rows all UP, conviction 40–80) — but the
post-prediction gate (`apply_live_quality_filters`) demoted nearly all of them to
NEUTRAL/WAIT in the recent stretch (24h overall: 28/102 actionable). Two real bugs:

1. **Unreachable safety bar after restart.** With <20 rolling confidence samples the p72
   percentile cap is inactive while the learned policy clamp allowed up to **0.76** —
   recorded bars hit **0.61/0.62/0.63**, above the 3-class structural confidence cap
   (~0.50–0.55) → mathematically unpassable → guaranteed 100% NEUTRAL until the window
   refilled. **Fix:** unconditional `threshold = min(threshold, 0.50)` immediately before
   the comparison (after all raises).
2. **Entropy gate sharper than the model can be.** Max 3-class entropy = ln 3 = 1.0986; the
   1.05 cut killed clearly-leaning outputs ([0.40,0.35,0.25] → 1.079). **Fix:** 1.085.
   Verified: clear leans pass; near-uniform ([0.34,0.33,0.33] → 1.099) still blocked.

**Left intentionally as-is:**
- 1m model-internal agreement gate (few calls, but they won 76.7% — that IS precision).
- "Negative Expectancy after costs" (26/102 5m kills) — models CEX taker costs; conservative
  for the *trade action*. For Polymarket betting the product is the **raw lean** (Directional
  Calls log + price-to-beat model-lean split), which is ungated by design.
- "Confidence 0.42 below bar 0.42" messages are display rounding (0.4199 < 0.4201), not a bug.

## 5h. Per-horizon issue map — the gate is INVERTED at 5m+ (2026-06-10, all resolved data)

| h | raw leans | raw winrate | gate-passed | passed winrate | verdict |
|---|---|---|---|---|---|
| 1m | 43 | 76.7% | 20 | **90.0%** | ✅ gate WORKS — don't touch |
| 3m | 142 | **71.8%** | 33 | 69.7% | ⚠️ best horizon, was choked by the 1.05 entropy gate (fixed → 1.085) |
| 5m | 87 | 64.4% | 28 | **50.0%** | ❌ gate ANTI-selects |
| 7m | 75 | 66.7% | 29 | 58.6% | ❌ gate degrades |
| 10m | 52 | 61.5% | 28 | 57.1% | ❌ gate degrades |
| 15m | 35 | 62.9% | 8 | **50.0%** | ❌ gate anti-selects (tiny n) |

**Interpretation:** at 5m+ the gate-passed subset performs WORSE than the raw leans — the
model's uncalibrated confidence is anti-correlated with success at those horizons (same
phenomenon as the conviction inversion: high(60+) 58% vs mid 73%). The gate passes the
overconfident calls and kills the good ones. Top killers: 3m = entropy gate (fixed);
5/7/10/15m = "Negative Expectancy after costs" + confidence bar.
**This is the #1 calibration target:** once ≥150 resolved leans/horizon exist (3m is at
142 — days away), fit isotonic calibration and re-base the gate on CALIBRATED confidence.
Until then, the raw lean (Directional Calls / price-to-beat model-lean) is the betting
product; the gated action is conservative-but-untrustworthy at 5m+.

**Smoke test (pre-restart):** full backend import OK (light modules 0.5s, model.py 23.1s
torch load, server.py 1.2s), arch `2026-06-10-v3-dirfeat-126-tcn`, `MODEL_FROZEN=True`.

## 5i. Precision stages implemented as auto-activating machinery (2026-06-10)

All four stages of the 80%-precision plan are now CODE, each activating on its own data
threshold (nothing waits on a human):

| Stage | Mechanism | Status |
|---|---|---|
| 1. Calibration | `calibration.PrecisionEngine` — per-horizon isotonic fitted on LIVE resolved leans (genuinely OOS); gate switches to **calibratedConfidence** when active | **auto-activates at ≥150 leans/horizon** (3m at 142 — days away) |
| 2. Meta v0 | shrunk empirical precision bins P(hit \| horizon, regime, conviction-bin), Laplace-shrunk toward the horizon base rate → `expectedPrecision` on every prediction | **ACTIVE NOW** — already discriminates: 5m LOW_VOL/high-conv 0.57 vs RANGE/mid 0.70 |
| 3. Confluence | `_confluence()` — grade A/B/C from model-lean + regime + CVD + large-trade + book agreement; attached to predictions + price-to-beat rounds + advice text | **ACTIVE NOW** |
| 4. Late entry | price-to-beat flags `late_entry` when ≤120s left, price ahead on the leaned side, model still agrees — the "persistence bet" with the highest conditional win prob | **ACTIVE NOW** |

Wiring: engine refits off the event loop (first ~15s after boot via tick 5, then 6h
staleness window); refit is NOT gated by MODEL_FROZEN (post-processing on live outcomes,
not weights — and it is the fix for the proven anti-selecting gate). The safety-bar
comparison + percentile window now use calibrated confidence when available.

Validation: engine fit on the real DB (global rates match the issue-map exactly);
calibrators correctly waiting (<150); late-entry fires at 60s-left/+$30 and not early in
the window; grade text in advice; `server` full import OK.

## 5j. Component replacement policy + Kronos veto bug (2026-06-10)

**Bug fixed:** Kronos's probability nudge was accuracy-gated (>53% over ≥20 samples), but its
**confluence vote** and its **`contradicted_by_kronos` VETO on `actionable`** were NOT — so
the fallback forecaster (measured ~45%, below chance, real Kronos module not installed) was
randomly dragging conviction and **blocking real signals**. All decision influence now flows
through `kronos_dir_decision`, gated on the same proven-skill rule; the display keeps the raw
direction + a new `kronosProven` flag.

**Replacement policy (evidence-gated, not blind swapping):**
| Component | Status | Policy |
|---|---|---|
| Kronos | module missing → fallback at ~45% | Influence now self-gating: zero effect until it PROVES >53% live. Replace later by either installing real Kronos or any forecaster scoring into `kronos_forecasts` — the verifier + gate auto-admit whatever proves skill. |
| FSR-PPO | deterministic research stub | Already isolated (never touches the live signal, per V2 constraint). Replace/train only as a silent challenger; promotion requires logged positive reward over enough resolved decisions. |
| Ensemble base models (xgb/lgb/cat/histgb/dl/lr/sgd) | individually ~chance at 3-15m, but the ensemble leans win 64-72% — diversity is doing the work | Do NOT hand-swap. The OOF stacker + dynamic weights already downweight weak members implicitly; explicit prune/replace at ≥200 resolved per model per horizon via `model_predictions` evidence. |

## 5k. The 5m-window freeze — root cause found & fixed (2026-06-10)

**Operator report:** the Polymarket 5m window freezes; when it resumes, the reference price
is wrong (captured late); mid-app freezes with no live data.

**Root cause (the smoking gun):** `signal_buffer.save()` ran **synchronously on the WS
event loop** at candle close — and because it only writes once 5 candles have accrued, the
multi-MB pickle landed **exactly every 5th minute = every 5m window boundary**, blocking
the 1s price-to-beat ticker at precisely the moment it must open/resolve a round.

**Fixes:**
1. **Async save** — `snapshot_payload()` (shallow copy in-loop, ~ms) + `write_payload()`
   (pickle+atomic rename in an executor thread). The loop never blocks on the write.
2. **Late-tick boundary recovery** — even if a tick is late (>3s), the round now anchors
   and resolves at the **TRUE boundary price recovered from 1m klines** (`_price_at_boundary`:
   open of the candle starting at the boundary, else close of the prior candle).
   `ref_captured_late_ms` records when this happened. Tested: late open anchors at 60000
   (not the +100 drifted live price); late resolve grades at the true window-end 59980
   (not a +400 post-boundary spike) — late grading is now CORRECT, not just less wrong.
3. **Retrain thread pressure** — `BTC_TRAIN_THREADS` 12→10 (+ OMP/OPENBLAS/MKL aligned) so
   the live ticker/feeds keep 6 cores during the startup retrain (~20% longer train).

Validation: late-open/late-resolve unit tests PASS; snapshot/write/load round-trip PASS;
all touched files compile.

## 5l. Path outlook on the Polymarket panel (2026-06-10)
New per-window "how will price travel vs the line" narrative (`price_to_beat._path_outlook`,
shown in the live block + documented in guide.html):
- **HOLD** — already on the leaned side; expect wobbles that test the line but hold to close.
- **CROSS** — on the wrong side, but expected move ≥ distance → wobble first, then cross.
- **STRETCH** — lean exists but expected move < distance → unlikely to make it; skip/wait.
- **CHOP** — weak/fallback lean → oscillation, coin-flip close; skip.
Odds quoted use MEASURED `expectedPrecision` when available (else two-way prob) — never
invented numbers. All four scenarios unit-tested; build clean.

## 5m. Tab-by-tab guide + Live Training Signals panel (2026-06-10)
- **guide.html** expanded with three new sections — Tab 1 (Technical + Live Feed), Tab 2
  (Decision Center), Tab 3 (Models & Signals) — every value with "if it's between X–Y →
  meaning / what tends to happen" range tables (RSI/ADX/BB/funding/OI×price/liqs/regimes,
  conviction grades incl. the measured B-band inversion, quantile spread, VPIN bands,
  microprice/OFI/spoof/absorption, feed-health states, skip-reason translations).
- **Live Training Signals panel** (Models & Signals tab): new payload field
  `training_signals` = `signal_buffer._snapshot(data_state)` evaluated live each loop —
  the EXACT 71 per-candle values the recorder writes for training, rendered as grouped,
  sign-colored cards (Order Flow / Big Players & Toxicity / L2 Microstructure / Derivatives).
  Validated: snapshot returns all keys with correct live values; build clean.

## 5n. Antigravity-changes audit + live-retrain pre-flight (2026-06-10 ~13:45)
**Context:** the operator's retrain crashed twice this morning (NUM_FEATURES NameError at
~12:04; DuckDB lock collision in `fetch_fsr_ppo_summary` at ~12:12). Antigravity (IDE agent)
fixed both and restarted; the current run (PID 18060, started 12:16) has trained
uninterrupted since.

**Audit of Antigravity's changes:**
- `NUM_FEATURES` import added to server.py — verified present + correct.
- `database.py` regex sweep (conn inside try, `if conn: conn.close()` in finally) —
  **verified sound**: 28 functions correctly converted; functional round-trip on a temp DB
  passed (init_db, fetch_action_log, **fetch_fsr_ppo_summary — the 12:12 crasher**,
  price_to_beat log/resolve); the crash class is fixed (broken/locked DB now logs + returns
  empty instead of raising). Two "leftovers" were both `init_db()` = intentional fail-fast
  at boot — now documented in-code so a future sweep doesn't "fix" it.
- Kronos try/except advice — correct; we'd already gone further (proven-skill decision gate).
- My earlier `fetch_action_log` LIMIT-pushdown fix survived the sweep intact.

**Live state:** training healthy ~90 min in; API responsive mid-train; RAM 77% system-wide
with headroom; backfill parquet (3.3MB, 12:02) was in place BEFORE training → CVD/VPIN/
large-trade overlay active in this run.

**Memory (operator question):** models MUST stay resident for live inference — the pickles
in saved_models/ are the restart-time copy, not a swap-out. What gets freed: training
intermediates (~1.3GB sequence tensor + OOF arrays). Added explicit `del X,Y,Ymag,features
+ gc.collect()` after training completes (server.py) so the process slims promptly instead
of holding allocator pages for hours. The full slim-down remains the post-train restart:
loads from pickle in seconds at ~1GB resident vs 4-5GB post-train.

## 5o. Mid-train feed staleness + price-to-beat pollution guard (2026-06-10 ~14:00)
**Operator report:** Polymarket shows price-to-beat 60,732 while the app shows 60,976.
**Measured root cause:** during heavy training phases the GIL stalls the event loop in long
bursts (verified live: the WS broadcast emitted NOTHING for 30+ straight seconds while the
HTTP API answered 0.3s between bursts). The app's displayed price (and any round anchored
during a stall) is a frozen snapshot, while Polymarket/Binance move on (Binance live was
61,049 at measurement). This is a TRAINING-TIME transient — it ends when training completes.
**Fix (effective next restart):** feed-freshness guard in the price-to-beat ticker — if the
live ref hasn't changed for >10s (BTC normally ticks sub-second), the tracker still
resolves/refreshes (kline recovery handles grading) but **does NOT open new rounds**, so
stale anchors can no longer pollute the win-rate stats. Validated: stale → no round opens;
fresh → opens normally.

## 5p. Architecture-gap scan (2026-06-10 ~14:30) — 4 fixed, 4 documented

### Fixed now (effective at next restart; all unit-tested on an isolated DB)
1. **Calibration version-blindness** — the precision engine fit on ALL historical outcomes,
   including the OLD model's. Each retrain changes the confidence distribution, so that's
   silent skew. Now: outcomes are filtered to the current model's era (`architecture_
   version.pkl` mtime); calibration deliberately re-earns its sample after every retrain.
2. **Backtest cache arch-blindness** — after a retrain, `_load_backtest_cache` happily
   served the PREVIOUS model's backtest and the startup path then SKIPPED running a fresh
   one (cache checked only cache_version + days). Now the cache carries `model_arch` and a
   mismatch discards it → fresh backtest runs for the new model.
3. **Mirror stats lost on restart** — price-to-beat win-rate history was memory-only.
   Now: additive `lean_source` column on the `price_to_beat` table, logged per round, and
   the tracker rehydrates `(hit, lean_source)` per horizon at boot (oldest-first, ≤500).
   Pre-column rows default to "model" (historically accurate). Split survives restarts.
4. *(earlier today, §5o)* feed-staleness guard on round opening.

### Documented, deliberately deferred (each needs its own validated change-set)
- **P4.3 Regime train/serve mismatch (the big one):** training clusters regime buckets by
  THRESHOLD rules on features (ADX idx22 / vol idx50, model.py train ~454) while serving
  routes by the HMM's labels (`_get_regime_from_state`). The regime experts learn one
  partition and answer for another. Fix design: after `regime_engine.fit_hmm` (already
  fitted on full history BEFORE training), label every training row with the HMM and pass
  `regime_labels` into `model.train` to replace the threshold clustering. Requires a
  retrain to matter — schedule as its own change-set AFTER the current model validates.
- **P5 canonical prediction object** — lean/action/signal field unification (long-standing).
- **No training checkpointing** — a crash at hour 5 restarts training from zero. Proper fix
  = per-horizon incremental saves; meaningful effort, low frequency of payoff.
- **VPIN live warmup zeros** — first ~1h after boot records vpin=0 (warming) vs backfill's
  true values; tiny distortion, accepted.

Note on #1: after TODAY's training completes, calibration counters intentionally reset to
the new model's era — activation (~150 leans/horizon) restarts from that moment (~2-3 days
at 5m). Correct behavior, worth knowing.

## 5q. Startup-training progress visibility (2026-06-10 ~16:20)
During the (hours-long) startup train the UI showed relearn_status "Idle" and the Decision
Center showed only WAITs — the operator reasonably concluded "the ensemble is not working".
Verified via /api/runtime-status that training was in fact deep in progress (TREND 6/6,
RANGE 6/6, GLOBAL 5/6 at the time). Fix: `_startup_train_then_backtest` now reports
running/complete/failed through `relearn_status` like the relearn path does, so the header
chip shows training during first-boot trains. (Effective next code load.)

## 5r. DuckDB self-lock root cause + anchor connection (2026-06-10 ~16:45)
**Symptom (operator log, 15:53):** Insert / FSR-PPO-summary / analysis-snapshot all failed
with "file in use by another process — PID 18060" — the app's OWN pid — then self-healed
within ~20s. Training unaffected (graceful degradation worked; at most one insert lost).
**Root cause:** connection churn. Every helper opens→queries→closes; when the LAST
connection closes, DuckDB closes the instance and runs a close-checkpoint, which under a
training-time WAL (SHAP blobs + insert bursts) holds the OS file handle for seconds. A new
connect in that window can't open the file, exhausts the ~5s retry, and fails citing the
process's own PID. (Path strings are consistent across modules — verified; automl's
read_only connect is unused by the server — verified.)
**Fix:** a process-lifetime ANCHOR connection created in init_db and never closed — the
instance stays alive in DuckDB's same-process cache, so helper connects ATTACH instead of
re-opening the file; the self-lock window cannot occur. Churn-tested: 200 rapid open/close
cycles, zero failures. (Effective at next restart.)

## 5s. P4.3 regime train/serve alignment — IMPLEMENTED (dormant; 2026-06-10 ~late)
The single highest-ROI model fix. Before: training bucketed rows into TREND/RANGE/VOLATILE
by ADX/vol THRESHOLDS (model.py train) while serving routed by HMM labels — experts learned
one partition and answered for another. Now: `regime.classify_series(closes, volumes)`
labels every bar via the fitted GMM emissions → the SAME 3 buckets serving uses; server.py
passes per-row `regime_labels` (aligned 1:1 to X) into `train()`; `train(..., regime_labels=)`
buckets by them, falling back to thresholds if absent/misaligned (defensive — no crash).
Validated: classify_series partitions a synthetic series sensibly, labels align exactly to X.
**DORMANT BY DESIGN:** it only activates on a RETRAIN, and MODEL_ARCH_VERSION is NOT bumped,
so the current evidence model + any non-retrain restart are unaffected. To activate: bump
MODEL_ARCH_VERSION (or POST /api/relearn) → next train uses HMM-aligned regimes. Recommended
AFTER the current model's 2-3 day evidence run, as its own measured change.

## 5t. Polymarket reference oracle — IDENTIFIED + the train/serve question (2026-06-10)
**Finding (from the standalone probe):** Polymarket BTC 5m up/down resolves on the
**Chainlink BTC/USD data stream** (`data.chain.link/streams/btc-usd`), confirmed in the
gamma event `resolutionSource` + `description`. NOT Binance, NOT Pyth, NOT spot.
**Reachability from operator's network (no VPN):** gamma-api ✅, Pyth ✅, Binance ✅,
on-chain Chainlink via public Polygon RPC ✅, **polymarket.com ❌ (geo-blocked)** — which is
why the website-scrape needed a VPN but the APIs/oracles do not. The strike is NOT exposed
as a gamma field; it's pulled from Chainlink at the boundary. Probe sample: Chainlink-onchain
62498.99, Pyth 62476.09, Binance-boundary 62544.90 (Binance ~$46 below the settlement feed).

**The operator's key question — "if we switch price-to-beat to Pyth/Chainlink but train on
Binance, won't the $50-80 venue offset break it?" — answer: NO. The concern conflates two
different things:**
- The MODEL predicts **direction (a sign)**, not an absolute price level. Its features are
  normalized/scale-invariant. BTC venues differ in absolute LEVEL (~$50-80) but move TOGETHER
  (5m return correlation ~0.995-0.999), so the SIGN (UP/DOWN) is identical across venues.
  Training on Binance and resolving on Chainlink does NOT create a model mismatch.
- The price-to-beat is the RESOLUTION ANCHOR, independent of model inputs. The ONLY rule that
  matters: **anchor the window-open AND resolve the window-close on the SAME feed** — then the
  venue offset cancels exactly (it's a ≥ comparison of two prices from one feed).
- Switching the anchor Binance→Chainlink makes the mirror MORE truthful (matches the actual
  Polymarket settlement), and removes the Binance-vs-settlement basis noise from the measured
  win rate. Net: strictly better for the mirror, neutral-to-better for the model.
- Residual noise = only razor-thin windows (<$10-15 move) where venues might disagree on sign;
  those are exactly the low-conviction windows the grade/conviction gate already skips.
**Decision (deferred to post-evidence-run):** keep MODEL INPUTS on Binance (best depth/
microstructure); move the RESOLUTION anchor to the settlement oracle. Probe both Pyth and
on-chain Chainlink — the on-chain feed updates on heartbeat/deviation (can lag), while Pyth is
sub-second and may track the real-time strike tighter (operator observed Pyth within $2-6 of
the real value). Pick the closest from the logged CSV before integrating.

## 5u. Price-to-beat anchor switched to Pyth (2026-06-10)
Per the §5t analysis, the price-to-beat reference now anchors on **Pyth BTC/USD** (the
sub-second oracle that tracks Polymarket's Chainlink settlement within ~$2-6), not Binance
spot. New `pyth_price_poller()` task fetches Pyth ~every 1.5s off the event loop into
`data_state["pyth_price"]`; `price_to_beat_ticker` anchors + resolves on it. SAME-FEED rule
enforced: when on Pyth, `klines=None` (no Binance-kline boundary recovery — would re-mix
feeds). Falls back to Binance live if Pyth stale >10s (panel never freezes). **Model inputs
and the entire feature/training pipeline are UNCHANGED (Binance)** — only the resolution
anchor moved, which is correct because the model predicts direction (venue-agnostic) while
the mirror now matches the real settlement. Validated: syntax + live Pyth fetch (BTC 62,722).
Effective next restart.

## 5v. Open model issues flagged (2026-06-10) — for the next retrain, NOT bolt-ons
- **Overnight LOW_VOLATILITY DOWN-bias:** live evidence (3:50-5:00 ET) showed 15 consecutive
  DOWN leans into a steady +$255 grind-up, ~33% correct. Root: LOW_VOL routes to the
  mean-reverting RANGE expert, which fights slow trends. **No feature fixes this** — it's the
  regime train/serve mismatch. P4.3 (staged §5s) is the fix; activate on the next retrain and
  measure. The gate already marks these "lean only" (not actionable) — discipline = skip them.
- **Kronos:** the fallback forecaster (~45%, below chance) is already gated out of all
  decisions (§5j). Recommendation: hide from UI; do NOT install real Kronos (not the bottleneck).
- **ChatGPT L2 feature dump:** ~80% already built/planned; net-new = L2 microstructure
  (multi-level OFI/OBI velocity/liquidity-adjusted flow/microprice aggregations) = the V2
  track (OKX historical L2 or Binance depth recording). Defer until AFTER P4.3 proves out —
  features don't fix a regime-bias problem.

## 5w. Deep scan + v4 retrain fix (2026-06-11) — the DOWN-bias bottleneck

### Live data analysis (running v3 model, APIs)
- **THE bottleneck: structural DOWN bias.** Today's leans = 40 DOWN / 5 UP (89% DOWN). The
  model inherited a DOWN lean from a net-bearish 30-day training window and can't switch UP
  when the current move trends up. It *looks* good today (5m action-log 90%) only because the
  market is falling — a DOWN-machine wins in down markets and dies in up markets (the overnight
  +$255 grind disaster, 15 straight DOWN, ~33%). The de-bias (alpha=0.5 prior correction) is
  too mild to overcome it.
- **SGD is below chance** at 3-5m (3m 29%, 5m 15%) — actively harmful base model.
- **Mirror 50%/38%** (era-filtered) is dragged by the overnight disaster; per-prediction
  action-log today is far better (3m 100%, 5m 90%).

### Code/logic bugs FIXED (runtime — effective on restart)
1. **`_model_directions` missing GLOBAL fallback** (Antigravity's find, verified real): in
   VOLATILE/empty regimes it returned {} → agreement 0 → meta-filter forced NEUTRAL (model
   blind in volatility). Now mirrors `_predict_from_regime`'s GLOBAL fallback. (Their
   `predict_move_size` claim was moot — that path already falls back.)
2. **VOLATILE folder empty is EXPECTED, not a bug:** training skips regimes with <1000
   samples (overfitting guard); 30 days rarely has 1000 HIGH_VOLATILITY candles → GLOBAL
   covers it. The real bug was only the missing agreement-fallback (#1).
3. **Pyth price exposed in payload** (`pyth_price` + age) so the UI shows both Binance (model
   feed) and Pyth (settlement side).

### v4 retrain fix (MODEL_ARCH_VERSION → `2026-06-11-v4-trend-regime-130`; next restart retrains)
4. **Trend-persistence features (126-129):** `trend_efficiency`, `signed_streak`,
   `momentum_fast_slow`, `return_acceleration` — kline-derived; let the trees SEE persistent
   drift so the RANGE/low-vol experts can FOLLOW a trend instead of always fading it.
   Validated: positive in an uptrend (trend_efficiency/streak → +1.0). **This is the direct
   fix for the DOWN-bias-in-uptrend failure.**
5. **P4.3 regime alignment** activates this retrain (regime_labels now wired) — experts train
   on the HMM partition they serve on.
6. **Pyth anchor** (§5u) goes live this restart.

### Pyth anchor — implemented correctly, NOT yet live
Verified: the running process (07:51) predates the Pyth code (server.py 11:17), so the live
panel still shows Binance (offset +0.00). Implementation validated (live fetch works). Goes
live on restart. **Will it improve accuracy? It improves MEASUREMENT/bet-matching, not model
accuracy** — the model is venue-agnostic (predicts direction); Pyth makes the mirror match the
real settlement so your win/loss record is truthful. Accuracy itself comes from #4/#5.

### NOT done (deliberate, with reason)
- **Class-balanced training** (the other DOWN-bias lever): deferred — try trend features +
  P4.3 first; if v4 still leans DOWN, add balanced class weights next (bigger per-model change).
- **Drop SGD:** the OOF stacker already downweights it; explicit removal deferred to avoid an
  unmeasured ensemble change in the same retrain.
- **Dual Binance/Polymarket UI views:** large frontend build, NOT the accuracy bottleneck —
  planned (§5x) but not built this pass; the model fix takes priority.

## 5x. PLANNED — dual venue views (Binance / Polymarket) [NOT yet built]
Two dedicated dashboard views: a **Binance view** (1/3/5/7/10/15m predictions, accuracy,
technical+fundamental indicators) and a **Polymarket view** (5/15m price-to-beat with Pyth
anchor, model leans, grades, mirror accuracy). Most data already flows in the payload; this is
a frontend reorganization (~new view + render fns + nav). Sequenced AFTER v4 proves out, so we
don't rebuild UI around a model that's about to change.

## 5y. Dual venue views + SOL/ETH + concurrency check (2026-06-11)
- **BUILT: two dedicated views** (nav tabs 📈 Binance, 🎯 Polymarket; build clean).
  - **Binance view:** 6-horizon (1/3/5/7/10/15m) prediction cards — direction, calibrated
    confidence, expected move/target, gated action, grade, live accuracy + an indicators/regime
    strip. For long/short entry decisions.
  - **Polymarket view:** Pyth-anchored price-to-beat (5m/15m) — live Pyth + Binance both shown
    with delta, lean, grade, path outlook, hold/exit advice, mirror win-rate split, recent
    resolved rounds. Reads existing payload (+ the new `pyth_price`); no backend change beyond
    the payload field already added.
- **SOL/ETH null→0 (operator-flagged):** assessed — `data_state.get("eth_price",0)or 0` →
  cross-asset features 86-91 read 0/floor when the (intermittent) cross-asset WS is down.
  **Low impact, NOT a meaningful bug:** these are low-importance features (top features are
  atr_norm/vol), the 0-fill is bounded (clipped 0-1) and transient (feed-outage/startup only).
  imbalance→0 is correctly neutral; only the price-ratio 0-floor is mildly wrong. Not worth a
  fix vs the v4 retrain; carry-forward could be added later if cross-asset weight grows.
- **Relearn concurrency: NO bug** — `schedule_relearn` guards on both `relearn_task` running
  and `backend_state["is_training"]`, so clicking relearn during a startup-train is safely
  ignored. Verified.
- **Operator state (12:02 process):** the in-progress relearn predates this session's v4 edits
  (running process can't reload code) → it does NOT contain the DOWN-bias fix. The arch is now
  v4-130 on disk, so the **next start.bat auto-retrains v4** with trend features + P4.3 +
  `_model_directions` fix. Recommendation: restart for v4; the current relearn lacks the fix.

## 5z. THE GRADING AUDIT — dual-semantic `hit` poisoned the evidence chain (2026-06-11)

### The bug class (verifier semantics, not a crash)
`prediction_verifier.check_and_verify`: for rows whose FINAL direction was gated to NEUTRAL
(raw lean UP/DOWN — the majority), `hit = avoid_success` = TRUE when the outcome was neutral
**or the raw lean was WRONG**. So the `hit` column means "lean was right" on committed rows
but "good thing we didn't trade" on gated rows — two opposite meanings in one column.
Measured: **79% of resolved directional rows (251/319) have `hit` ≠ sign-truth.**

### Consequences + fixes
1. **Calibration/precision engine would have trained on poisoned labels** (wrong lean →
   label 1 on gated rows). FIXED before first activation: labels now =
   `(raw=UP AND actual_move>0) OR (raw=DOWN AND actual_move<0)` — proven by unit test
   (poisoned semantics would have read the test case inverted).
2. **CORRECTION OF PRIOR REPORTS:** the recent action-log "winrates" (5m 83-90%, 7m 77% etc.)
   used `hit` and are RETRACTED — they blended avoid-success into wins. Clean numbers were
   always: simulate_pnl (sign-based) and the price-to-beat mirror. The verifier's own
   accuracy panels still use `hit` semantics BY DESIGN (they grade final-action quality);
   they're fine for gating but NOT for betting-accuracy claims.
3. `model_verifier` audited: CLEAN (each vote graded vs its own rule).

### THE TRUE STATE OF THE v3 MODEL (sign-based, n=319, era since 06-10 16:57)
| h | true lean acc | | direction | true acc |
|---|---|---|---|---|
| 1m | 42% (146) | | UP leans | **42% (201)** |
| 3m | 47% (58) | | DOWN leans | **52% (118)** |
| 5m | **57% (42)** | | | |
| 7m | 45% (33) | 10m | 41% (22) | 15m: 50% (18) |

**Honest bottom line: v3's raw leans are ~coin-flip** (5m's 57% is the only hint of edge).
The earlier celebration numbers were a grading artifact. What remains real: the mirror's
grade-A subset (~80%, small n) and the selectivity machinery. This RAISES the stakes on the
v4 retrain (trend features + P4.3) — v4 must prove it clears 50% at all, measured ONLY by
sign-truth / the mirror from now on. guide.html's win-rate ladder needs revision after v4.

## 6. Known limitations / honest notes
- `vpin` and the backfill's `funding_velocity` are present in the parquet but intentionally not
  fed to training (skew-avoidance). Feature 17's funding_velocity uses the existing
  `funding_rate` key (sparse historically — improves only with live coverage).
- Expected accuracy lift from this batch is **modest** (Class-A features are real but
  secondary); the larger gains remain calibration + meta-labeling once data accrues.
- The schema hash changed → any cached feature-importance/PSI baselines recompute on the new
  117-feature set after the retrain (expected, additive).
