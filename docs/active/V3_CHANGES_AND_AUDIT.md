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

## 5aa. THE FULL `hit`-CLASS SWEEP — every consumer audited, hunt CLOSED (2026-06-11)

§5z found the dual-semantic `hit` bug; the operator's instinct ("I feel like there are more")
was right — it was a CLASS. This pass audited **every one of the 15 files** that touch `hit`.

### Poisoned consumers found & FIXED (sign-truth: raw_direction vs sign(actual_move))
| Consumer | Path type | Damage before fix |
|---|---|---|
| `calibration.py` labels (§5z) | live (would auto-activate) | inverted confidence map |
| `prediction_verifier.get_regime_horizon_quality` (§5z) | live (regime skip rules) | inverted regime quality |
| `analytics.validate_regime_thresholds` → `poor_regimes` blocker | **live** (server loop imports it) | **INVERTED: 5m TRENDING_UP read 83.3% under `hit`, TRUE = 33.3% (Δ+50)** — the model's worst regime (fading uptrends) looked best, so the blocker never blocked it; TRENDING_DOWN Δ+25, LOW_VOL Δ+16.7. Measured on live DB. |
| `server.py` → `CascadeMonitor.record_outcome` | **live** (cascade auto-enable/disable + `avg_impact` multiplier on expected-move) | cascade-on vs cascade-off accuracy comparison computed on inverted labels → auto-disable could fire backwards. Now fed lean sign-truth, directional rows only. |
| `automl.py` challenger tuner label | offline only (never imported by server — verified) | would tune hyperparams to predict the inverted target; fixed as a foot-gun. |
| `meta_model.py` trainability check (`df["hit"].nunique()`) | live-adjacent | could block/allow training on the wrong column's class balance; now checks the actual `profitable` label. (Its TRAINING label was already clean: cost-aware sign-truth.) |
| `analytics.analyze_conviction_performance` | report | filtered raw-directional rows but graded them with `hit` — self-contradictory; now sign-truth. |

### Audited CLEAN (no action)
`get_signal_policy` (grades raw vs actual_direction directly — feeds the gate, clean),
`get_regime_calibration` + `refit_confidence_calibrators` (committed-rows-only → `hit` unambiguous),
`ab_testing`, `model_verifier`, `kronos_verifier`, `exchange_verifier`, `analyze_signals.py`
(reads clean-semantics tables), `price_to_beat`/`database` resolvers (direct comparisons),
`trading_simulator`/`polymarket_client` (docstrings only).

### Kept `hit` semantics BY DESIGN (final-action panels, labeled as such)
`prediction_verifier` accuracy panels (grade gate quality), `analytics` avoid-success /
meta-filter / skip-reason panels. Never to be quoted as betting accuracy.

### Fresh sign-truth scorecard (post-relearn v3 model, all resolved rows)
1m 49.9% (463) · 3m 46.8% (355) · 5m 51.4% (292) · 7m 48.3% (230) · 10m 51.8% (164) ·
15m 45.1% (133). Mirror: 5m model-lean 51% (201), 15m model-lean 41% (94). Still coin-flip —
confirms §5z; the relearn the operator ran today (old code, finished 17:04, saved v3-126)
did NOT fix it, as predicted. 5m remains a DOWN-machine in the last 24h (156 DOWN / 24 UP).

### Tooling
`backend/sign_truth_scorecard.py` — permanent measurement script (run with the app STOPPED):
per-horizon sign-truth accuracy, 24h bias split, mirror split, regime feed old-vs-new
comparison, freshness. This is THE yardstick for judging v4.

### State at end of this pass
App STOPPED (~18:00); saved arch `v3-dirfeat-126` (today's relearn) vs code `v4-trend-regime-130`
→ **next start.bat auto-retrains v4** with: trend features (DOWN-bias fix), P4.3 HMM regime
alignment, `_model_directions` GLOBAL fallback, Pyth anchor, dual UI views, and ALL grading
fixes above. One restart ships everything.

## 5ab. Venue-tab hardening, Pyth fallback guard, guide re-base (2026-06-11, late)

### Bugs found & fixed this pass
1. **Binance tab "Live acc" was permanently blank** — `renderBinanceView` read
   `data.live_accuracy`/`data.accuracy`, neither of which exists in the payload; per-horizon
   accuracy lives at `verification.accuracy`. Fixed; it now shows `directional_accuracy`
   (committed UP/DOWN calls only — the clean subset of `hit`).
2. **Binance tab regime cell** read `data.regime_info` (payload key is `regime`) — always "—".
3. **Mid-round VENUE MIXING in the price-to-beat ticker** (real correctness bug): if Pyth
   went stale (>10s) mid-round, `ref` silently switched to raw Binance — a round ANCHORED on
   Pyth would RESOLVE against a Binance price (~$40-80 offset → thin rounds mis-graded).
   Fix: the ticker maintains an EWMA `pyth − binance` offset while both feeds are fresh; the
   Binance fallback is converted into Pyth units (and kline boundary-recovery is disabled in
   that mode, since klines are Binance units).

### UI enhancements (both venue tabs, venue-correct datastreams)
- **Binance tab:** 24h range/volume in the strip; per-card adds measured P(win)
  (`expectedPrecision`), model agreement, conviction grade; 12-cell indicator grid (adds
  MACD hist, BB position, CCI, Williams %R, EMA9/21 cross); NEW order-flow/derivatives strip
  rendered from `training_signals` — the exact per-candle values the model trains on.
- **Polymarket tab:** Chainlink price added to the strip (settlement reference); lean-source
  badge (MODEL vs ⚠ WEAK-skip); ⚡ late-entry chip; kline-recovery anchor flag; live-lean
  drift warning; resolved rounds show the $move; NEW win-rate-by-grade/-source strip
  (computed from the recent-rounds buffer, labeled as a small-n hint); discipline banner.

### guide.html re-based (the honesty pass)
- New "What changed 2026-06-11" section: the grading retraction, Pyth anchor, v4, the tabs.
- Win-rate ladder REWRITTEN on sign-truth: blind 50% · model leans ~51% (not yet an edge —
  paper-track until 55%+) · grades/A-rounds = hypotheses pending clean-sample re-measure ·
  late-entry = structurally favored. Stale "~64%" claims corrected in place; footer dated.
- New "two venue tabs" section: feed-per-job table + why direction is venue-agnostic.

### Validation
`npm run build` clean (275.6 kB); ALL backend files `py_compile` clean; 130-feature build
smoke test PASS (X=(599,130), no NaN, trend slots positive in an uptrend); payload field
audit of both render fns against server.py — every key verified present (`pyth_price`,
`pyth_price_age_s`, `ticker_24h`, `verification.accuracy`, `training_signals`,
`price_to_beat.{latest,accuracy,recent}`, round fields incl. `late_entry`,
`ref_captured_late_ms`, `live_lean`, `move`).

## 5ac. Antigravity remediation plan — verified, triaged, implemented (2026-06-11, late)

Antigravity proposed 4 fixes; each was verified against the actual code before acting
(their record is mixed — one prior real find, one moot claim). Verdicts:

| # | Claim | Verdict | Action |
|---|---|---|---|
| 1 | `lean_hit` column (P5.1) | **Real need, scoped down.** All consumers were already fixed via sign-truth SQL (§5z–5aa); the column adds a safe-by-default source for FUTURE code. Their "unify the UI payload" sub-item = the long-deferred P5 refactor — NOT done now (big cross-cutting change before v4 validates). | Added `lean_hit BOOLEAN` to all `predictions_*m` (NULL = unresolved/neutral lean); boot-time backfill migration for historical rows (idempotent); verifier computes it (pure raw-lean vs sign(actual_move)); `update_outcome` writes it. Proven by test: `hit=TRUE` (good avoid) coexists with `lean_hit=FALSE` — decoupled. Existing sign-truth queries intentionally unchanged (equivalent + proven). |
| 2 | Kelly freeze | **Half stale, half real.** The $0-trade death spiral was ALREADY fixed (skip guard). But the no-recovery residue was real: Kelly≤0 → no trades → history frozen → sizing pinned at 0 FOREVER (even after a retrain improves the model). Also: unbounded `trade_history` let a stale losing era dominate. | Kelly now evaluates the LAST 100 trades (recency) and floors at a 0.5% paper-probe (cap unchanged 2%) so evidence keeps flowing and sizing can recover. Tested: all-loss history → 0.005; recent winners after ancient losers → 0.02. |
| 3 | Sharpe time-scaling | **Confirmed real.** `sqrt(min(252, n))` treated n TRADES as n DAYS (30 trades in 2h got the 30-trading-days multiplier). Display-only metric, but wrong math. | Annualization = `sqrt(trades_per_year)` from the MEASURED span of the recent trades (`_pnl_times` deque, aligned with `_pnl_history`); falls back to the per-trade ratio when span too short. Tested: 60 trades/30 days → factor 26.8 (old formula: 7.75). |
| 4 | Cross-asset 0.0 outliers | **Overstated but real at one spot.** Features 86/87 are CLIPPED ratios (a 0 floors them — no "catastrophic crash"). The real damage: feature 100 (lead-lag) DIFFS the raw ETH series, so one 0 outage bar = two false full-scale ±1.0 spikes. Their NaN+ffill suggestion adapted: NaN must never enter the matrix (training asserts no-NaN), so zeros are forward-filled instead. | `_ffill_zeros()` applied to eth/sol PRICE series inside the shared build path (train/serve consistent; leading zeros backfilled; all-zero series unchanged → zero-variance harmless). Volumes/imbalances keep honest 0. Tested: 3000→0→3000 spikes eliminated. |

Also this pass (operator request): **Chainlink price removed from the Polymarket tab** —
strip shows Pyth (settlement proxy) + Binance only.

Validation: 4/4 functional tests PASS (migration on a throwaway DB; ffill edge cases; Kelly
floor + recency; Sharpe factor exact); all touched backend files compile; `npm run build` clean.
Note: the feature-100 sanitization slightly changes inputs vs what the OLD model trained on
(outage bars only — rare); the pending v4 retrain bakes it in consistently.

## 5ad. Binance-tab accuracy/log + training-time fixes (2026-06-11, operator screenshots)

Operator's screenshots (taken mid-v4-train) exposed three frontend bugs, all fixed:
1. **Early return starved the whole tab**: with no predictions (i.e. during the entire
   ~5h train) `renderBinanceView` returned after the empty-state, so indicators, flow —
   everything below — stayed blank. Now everything renders regardless; the empty state is
   training-aware ("Model is training (X%) — …" from `relearn_status`).
2. **ticker_24h field names**: payload uses snake_case (`price_change_percent`,
   `high_price`, `low_price`) — the strip read camelCase, so only volume rendered.
3. Polymarket "anchored via kline recovery" label was wrong under the Pyth anchor (it
   means LATE CAPTURE there; klines aren't used) — now "late anchor capture +X.Xs".

New on the Binance tab (operator ask: "where are model accuracy and trades log?"):
- **Model Accuracy panel**: per-horizon committed-call accuracy with sample size, color
  bands (≥55% green / 48-55 amber / <48 red), the UP/DOWN side split (the v4 bias watch),
  and the current streak.
- **Recent Calls log**: every resolved directional lean — time, TF, lean (+ whether the
  gate traded or waited), confidence, realized move, and ✓ CORRECT / ✗ WRONG graded by
  SIGN-TRUTH (`lean_hit` from the backend when present; client-side sign fallback for
  rows from older code, so the log works WITHOUT restarting the in-progress train).
- `_format_verification` now exposes `lean_hit` (backend side; applies on next natural
  restart — NOT needed for the log to work).

Operator note: do NOT restart while the v4 train is running — frontend changes need only
a hard refresh (Ctrl+Shift+R); the one backend line rides along on the next restart.

## 5ae. Antigravity list #2 triage + DB-coverage audit (2026-06-11, late)

### Antigravity's 5-item list: 4 of 5 were ALREADY DONE this session (§5ac)
Items 1 (lean_hit), 2 (Kelly), 3 (Sharpe), 4 (cross-asset zeros) were implemented and
unit-proven earlier today — their scan read stale docs. Item 5 was new and REAL (minor):
- **FSR-PPO overtrade memory** — `last_actions[h]` updated on EVERY call including AVOID,
  so an AVOID between two committed trades erased the flip-flop memory (BUY→AVOID→SELL
  saw last="AVOID" → 0 penalty instead of 0.08) and refreshed the timestamp. Now only
  committed actions update the memory. Tested: flip-flop penalty fires (0.08). Isolated
  challenger — never touches the live signal — but its reward log is now honest.

### DB-coverage audit (operator: "are we catching ALL signals/analytics in the DB?")
**Already captured (comprehensive):** every ensemble prediction with full meta context
(probs, agreement, regime, conviction, quantiles, wf-stats, expectancy, model_dirs,
raw_direction, hit + lean_hit); per-base-model votes (`model_predictions`, 7 models × 6
horizons); Kronos calls; price-to-beat rounds (+lean_source); FSR-PPO decisions+rewards;
simulated trades (Kelly/slippage/fees/PnL); A/B results; SHAP feature importance;
1/min analysis snapshots; feature-retirement events. Signal history → pkl + backfill parquet.

**Gaps found → FIXED (all additive migrations, auto-applied at next restart):**
1. `predictions_*m` — the decision layer's outputs were computed but never persisted:
   `confluence_grade` (A/B/C), `expected_precision` (measured P(win) used by the gate),
   `calibrated_confidence`. Without these, the grade/calibration machinery could never be
   evaluated from the DB. Now columns + written at insert (verified the insert call site
   runs AFTER the grade/calibration block — values are present).
2. `price_to_beat` — grade + late_entry existed only in the 20-round memory buffer; the
   grade-discipline win rates (the actual betting strategy) were not durably measurable.
   Now `confluence_grade` (at open) + `late_entry` (at resolve) columns.
3. (checked, NOT a bug) `predictions.confluence` DOUBLE vs the grade dict: safe by call
   order — meta context is built before `p["confluence"]` becomes a dict; the column holds
   the model's numeric confluence, the new `confluence_grade` holds the grade.

Validation: temp-DB round-trip PASS (grade/precision/calibrated persisted + read back;
late_entry + grade on price_to_beat); FSR-PPO penalty test PASS; all touched files compile.
**Operator note:** all of §5ae is backend-only → applies on the NEXT restart. Do NOT
interrupt the running v4 train for it; the migrations are additive and run safely at boot.

## 5af. Linter sweep — pyflakes now CLEAN (2026-06-11, late)

Antigravity's unused-variable list verified with a real pyflakes run; each flagged item was
checked for "forgotten use" (the dangerous kind) before deleting. **Verdict: zero logic
bugs — all dead weight — but one STALE COMMENT was actively misleading.**

| Item | Verdict | Action |
|---|---|---|
| `server.py ptb_ref` | dead since the Pyth-anchor refactor; the 7-line comment above it still described the OLD Binance-proxy anchor rationale — misleading for future edits | variable + stale comment removed; replaced with a 3-line accurate note (fast ticker owns the anchor, Pyth + offset-corrected fallback) |
| `server.py websocket data` | broadcast-only socket; inbound deliberately discarded | unbound + comment |
| `backfill cvd1m` | dead array build; divergence correctly derives at merge time (comment already said so) | deleted, comment tightened |
| `data_ingestion e` | silently swallowed parse errors (intentional skip) | unbound + comment |
| `calibration.py` | CLEAN — only an intentional `c_` unpack; SQL column indices re-verified correct | none |
| `fix_model.py` | stale one-off patch script hardcoded to the OLD OneDrive path — cannot work, only confuse | **deleted** (in git history) |
| unused imports (11 files) | cosmetic | removed (incl. server's unused `compute_polymarket_features`; kept `LabelEncoder` that gates HAS_SKLEARN in meta_model) |
| `analytics.py` f-string w/o placeholders | cosmetic | `f` prefix dropped |

Validation: **pyflakes CLEAN across backend/**, all files compile, import smoke OK
(automl excluded — `optuna` not installed in this env, pre-existing, server never imports it).

## 5ag. Deep audit #6 — label alignment, train/serve skew, backtest honesty (2026-06-12)

Targeted the classic quant killers that hadn't been line-audited: label construction,
index alignment, and evaluation contamination. Verdicts:

**CLEAN (verified correct, no action):**
- Triple-barrier labels (`build_sequences`): entry = decision candle's close, barrier scan
  starts strictly at i+1 (no entry-candle leakage), dual-touch resolved by bar net
  direction, sample range excludes the partial tail candle.
- P4.3 regime labels: indexed by the same decision-candle range as X, with a length guard.
- Regime training loop: `regime_indices` built only over [:split_idx] → `X_flat[reg_idx]`,
  `y_train[reg_idx]`, `recency_w[reg_idx]` all consistent. No misalignment.
- Purged walk-forward: embargoed (LOOKBACK+h), per-fold fits — honest by construction.

**FIXED:**
- **In-sample backtest contamination** — after a fresh train, the latest-12000 backtest
  window overlapped ~28% training rows (43461 candles, 80% train ≈ 34.8k; 12000 > the
  ~8.7k held-out tail). All reported backtest accuracies were inflated. Now: training
  records the split-boundary candle ts (persisted to `saved_models/train_boundary.json`,
  restored at boot when models load from disk), and `run_backtest` scores only
  post-boundary candles with a max-horizon embargo; warns loudly if <500 held-out candles
  or all candles pre-boundary. Applies from the NEXT backtest run after restart — the
  boundary json gets written on the next training; until then the restored-boundary path
  is inactive (legacy bundle → old behavior, by design).

**MEASURED BEFORE FIXING (instrumented, not changed):**
- **Partial-candle train/serve skew**: `handle_kline` updates the forming candle in place,
  so serve-time predictions use a partial final bar vs training's complete bars.
  Deliberately NOT changed now (would skew against the just-trained v4). The scorecard's
  new §6 buckets 5m sign-accuracy by second-of-minute; a clear early-minute deficit
  (≥4 pts) confirms the skew → V5 bar-progress normalization (V5.md §2.5c).

**V5-DEFERRED (documented in V5.md §2.5):**
- Scalar triple-barrier threshold → per-candle ATR-scaled barriers (label quality).
- Conformal residuals computed in-sample → held-out residuals (band honesty; affects the
  new projected-close display's implied confidence).

Validation: server.py + scorecard compile clean. No frontend changes this pass.

## 5ah. Lean sign-truth in the accuracy panel (2026-06-12, operator screenshots)

Operator saw "no resolved calls yet" across the whole Model Accuracy panel while the
Recent Calls log showed resolved leans. Cause (by design, but misleading): the panel read
`directional_*` = COMMITTED calls only, and v4's gate (correctly) waited on every
low-confidence early lean — so the panel would stay empty for days on a cautious gate.

Fix: `prediction_verifier` accuracy cache now also computes **lean sign-truth** counters
(`lean_accuracy/total`, `lean_up_*`, `lean_down_*`) over EVERY raw lean — `lean_hit` when
present, sign fallback for legacy rows. The Binance panel now leads with lean sign-truth
(+ UP/DOWN split for the bias watch) and shows committed-call accuracy as the secondary
line ("gate: all waits so far" until it fires). Backend part applies on next restart;
panel works against the running process via the recent-calls fallback in the interim.

Operator context note (v4 first hours): 7 resolved leans, +3.47% surge day, mixed UP and
DOWN leans present (the old model was 89% DOWN — bias diversity visibly improved), gate
waited on all of them. n=7 is noise; judgment point remains the 24h scorecard.

## 5ai. Auto-learning was steered by the poisoned metric — 8th hit-class consumer (2026-06-12)

Operator asked "should I turn auto-learning off?" — auditing before answering found the
LAST unconverted consumer of the dual-semantic `hit` accuracy:

`get_learning_feedback` fed `apply_learning_feedback` the BLENDED accuracy
(`accuracy_cache[h]["accuracy"]`, where gated rows count avoid_success as a hit). Live
failure mode with v4's cautious gate: nearly all rows are NEUTRAL avoids; in chop,
avoid_success is high even when the leans are WRONG → blended acc > 0.6 → auto-learning
LOWERED the confidence threshold (toward 0.38) and smoothing — i.e. it opened the trade
gate wider because the leans were failing. Exactly backwards.

Fix: auto-learning now runs on **lean sign-truth** (`lean_accuracy`/`lean_total`, the
counters added in §5ah), including the UP/DOWN split, the retrain trend (history rows now
carry `lean_accuracy`; legacy rows fall back), and the needs_retrain rule. The adjusted
parameters are bounded (smoothing 0.08–0.20, threshold 0.38–0.52) and reset at restart,
so any past drift from the poisoned signal is already gone after the operator's restart.

Verdict for the operator: KEEP auto-learning ON — after this fix it nudges in the right
direction (low lean-accuracy → raise the bar; high → relax slightly), is bounded, and
self-resets. Backend-only; applies on next restart (until then its inputs are too sparse
on the fresh DB to fire anyway: needs total ≥ 10–15 per horizon).

## 5aj. Resolved-rounds table rehydration (2026-06-12, operator screenshot)

Symptom: win-rate strip showed "5m: model 40% (5)" while the Recent Resolved Rounds table
said "No resolved rounds yet." Cause: at boot the tracker rehydrates its win/loss
COUNTERS from the DB (`fetch_price_to_beat_history`) but `recent_rounds` (the UI table's
source) was memory-only — every restart emptied the table while the counts survived.

Fix: new `fetch_price_to_beat_recent(20)` (same model-era filter as the history fetch —
the table must describe ONE model) returns newest-first round dicts shaped like the
tracker's in-memory resolved rounds, including the now-persisted `confluence_grade`
(→ `confluence: {grade}`) and `late_entry`; boot appends them into `recent_rounds`.
Grade-discipline stats strip repopulates too (it computes from the same rows).
Functional test PASS (temp DB round-trip). Applies on next restart.

## 5ak. Deep scan #7 — data hygiene + storage audit (2026-06-12)

Operator suspected unclean data storage. Audit results:

**CLEAN (verified, no action):**
- `signal_history.pkl` saves ATOMICALLY (tmp + os.replace) — the irreplaceable coverage
  file survives a crash mid-save.
- Depth/tick parquet logging disabled by default (`BTC_LOG_TICKS_PARQUET=1` to enable) —
  no disk bloat from the orderbook stream.
- Price-to-beat round IDs are deterministic (`ptb_{h}m_{win_start}`) — restarts within a
  window dedupe via INSERT OR REPLACE, no duplicate rounds.
- predictions_*m pendings rehydrate at boot (48h window) — no orphan class there.
- pyflakes: backend still fully clean (re-run).

**FIXED — orphaned pending rows (the "not storing cleanly" instinct was right):**
`price_to_beat`, `model_predictions`, `kronos_predictions` keep pending state in memory
only; rows left `resolved=FALSE` at shutdown can never resolve and accumulated forever
(every restart added more). New `cleanup_orphan_pending_rows()` boot janitor deletes
pending rows whose `verify_at` already passed (10-min grace; readers were never poisoned
— they filter resolved=TRUE — this is pure hygiene). Tested on a temp DB: deletes only
dead pendings, keeps live pendings and resolved rows. predictions_*m deliberately
untouched (its pendings restore legitimately).

**90-day training question** → answered in V5.md §2.6 (env knob exists; RAM/backfill
caveats; run as its own measured change after class-balance).

## 5al. Magnitude display honesty + grade-label contradiction (2026-06-12, operator)

Operator observed: expected move "always ~$40-45" while real windows swing $100+, and one
card showing header "Grade C" with advice "[Setup grade A (5/5)]".

**Magnitude (verified, not a wiring bug — a presentation lie):** the move-size regressor
is squared-error on |move| (60 iters × 15 leaves) → predicts ≈ the conditional mean ≈ the
average 5m move (~$40 at current vol) for nearly every window; the band adds FIXED
per-regime residual quantiles and is **q25–q75 = 50% coverage only**. So the number is
honest *as a typical-move estimate* but was DISPLAYED as a path forecast. UI now reads
"Typical rise/drop for this setup ≈ $X · 50% band $lo–$hi (tails run larger)" + an
explicit note that $100+ windows land outside the band ~50% of the time by design.
Real fix (conditional quantile regressors, breathing band) documented in V5.md §2.5b-ii
with an acceptance test (band width must correlate with realized |move| out-of-sample).

**Grade contradiction (fixed):** card header shows the grade AT OPEN (frozen with the
bet); the advice box appended the LIVE grade unlabeled — header C vs advice A looked
broken but both were true at different times. The advice now reads
"[Live grade A (5/5, opened C).]" — labeled, with the delta when they differ.

Validation: compile + build clean. Backend half applies on next restart; UI on refresh.

## 5am. INCIDENT: smoke test corrupted the saved-bundle metadata (2026-06-12 01:50)

**Cause (Claude's error, on record):** the v5 training smoke test redirected
`BTC_DB_PATH` to a temp dir but NOT the model directory. Its `train()` call (degenerate
all-NEUTRAL synthetic labels → every component skipped) still reached `_save_models()`,
which at 01:50:01 overwrote the REAL bundle's metadata in `data/saved_models/` — empty
`stackers.pkl` / `class_priors.pkl` / `conformal_residuals.pkl` / `accuracies.pkl` /
`move_size_stats.pkl` / `feature_reference.pkl` — and stamped `architecture_version.pkl`
as `v5-classbal` with NO v5 training having run.

**Blast radius:** the lying arch stamp made every subsequent boot "match" v5 → models
LOADED (v4 components + empty metadata Franken-bundle) → **the operator's intended
overnight v5 retrain silently never started.** Two app instances ended up alive
(01:26:59 owning port 8000 with old code; 02:00:50 loaded the corrupt bundle, couldn't
bind). The operator's relearn click hit the old process and was ignored (is_training
guard + idle).

**Recovery (02:40):** killed both processes; deleted the seven corrupted metadata pkls
(component pkls left in place — harmlessly orphaned without the arch file and overwritten
by the retrain); deleted the smoke script. Next `start.bat`: no arch file → "startup
training required" → clean full v5-classbal train.

**Lessons (binding for future sessions):**
1. Any test that constructs `MultiModelEnsemble` and calls `train()` MUST redirect the
   model dir (or monkeypatch `_save_models`) — `BTC_DB_PATH` alone is NOT isolation.
2. `train()` saving unconditionally at the end — even when every component was skipped —
   is itself a footgun; a future hardening: skip `_save_models()` when zero components
   trained. (Not changed tonight — no code edits between the operator's retrain attempt
   and morning measurement.)

## 5an. RUN RECORD — v5-classbal training (2026-06-12, 02:40–~07:25)

**Config actually executed:** 40 days (NOT the 35 in start.bat — GOTCHA: the operator's
console session still had BTC_HISTORICAL_DAYS=40 from the earlier run, and start.bat
uses `if not defined`, which keeps a pre-existing env var. To change the window, edit
start.bat AND open a fresh console, or set `$env:BTC_HISTORICAL_DAYS` explicitly).
57,600 candles → 57,525 samples (46,020 train) · 130 features · threads 12 · full data
budgets (caps off per the accuracy-first constraint) · arch `2026-06-12-v5-classbal-130-tcn`.

**Pre-train:** the backfill parquet was REBUILT FROM SCRATCH and captured the FULL
window — May 3 → June 10, every day downloaded (56,160 bars). June 11 404'd (not yet
published) and the new skip-day patch worked exactly as designed (one log line, run
continued, parquet written). June 11 price/klines ARE in training; only its flow
overlay arrives at the next start.bat after Binance publishes it.

**v5 features confirmed live in the log:**
- Class weights fired per horizon, adapting to each horizon's imbalance:
  1m [1.378, 0.5, 1.532] · 3m [1.28, 0.5, 1.383] · 5m [1.166, 0.58, 1.254] ·
  7m [1.068, 0.805, 1.127] · 10m [0.916, 1.124, 0.961] · 15m [0.695, 1.58, 0.725]
  (UP boosted at short horizons per the bearish window; NEUTRAL boosted at 10/15m
  where the triple-barrier makes it the rare class — the balancer adapts correctly).
- Held-out conformal residuals ACTIVE: every magnitude bucket logged
  `MoveSizeRegressorFast_conformal[held-out]`.
- P4.3 HMM bucketing active: TREND 28,537 / RANGE 27,500 / VOLATILE 1,488 (VOLATILE
  again <1000 in-train → GLOBAL fallback, expected).

**Observations (full analysis + decision gates in MODEL_ROSTER_PLAN.md):**
- SGD catastrophic: OOF 0.228 (5m G) / 0.136 (7m G) / 0.124 (10m G) — anti-signal.
- Tree quartet (xgb/lgb/cat/histgb) OOF ≈ identical everywhere — clones.
- TCN trains 18× but is EXCLUDED from the stacker features — half a seat.
- LightGBM ran on GPU → the box has a working GPU; XGB/CatBoost/TCN still on CPU.
- NB: balanced training lowers raw 3-class OOF vs majority-class cheating BY DESIGN
  (1m RANGE 0.95 ≈ NEUTRAL base rate, not skill). Judge by live sign-truth only.
- Duration: 02:41 → ~07:25 (~4h45m at 40d / 12 threads / full budgets).

**Measurement protocol for today:** leave it running, no relearn clicks. Watch the
accuracy panel's UP/DOWN lean split (bias check — visible within hours). Run
`python backend/sign_truth_scorecard.py` (app stopped briefly) BEFORE ~02:00 tonight —
the 24h auto-relearn fires ~02:40 and would reset the model era mid-measurement
(alternative: set BTC_FREEZE_MODEL=1 to extend the window). Decision gates on the
result: MODEL_ROSTER_PLAN.md §5.

## 5ao. Advice/outlook coherence fix (2026-06-12, operator screenshot)

Operator caught the card disagreeing with itself: UP lean, −$250 below the line, 55s
left, typical travel ~$70 — the magnitude line correctly projected "DOWN resolves" and
the outlook correctly said STRETCH/skip, but the ADVICE box still said "HOLD / WAIT —
reversal possible." `_advice` consulted lean+position but not the magnitude math the
card itself displays. A lean can be RIGHT about direction and still unable to cross
the line in time — for a binary window that is a losing hold.

Fix: when the path outlook is STRETCH, the advice is overridden to **EXIT / SKIP**
("counted out for THIS window: ~$70 travel vs $250 gap with 55s left"). Display-only —
touches no lean, grade, or recorded metric, so the v5 measurement is unaffected.
Activates at next natural restart (no restart needed mid-measurement).

Note: the same screenshot showed three honesty features working as designed —
magnitude projection contradicting the lean, STRETCH outlook, and the live-grade delta
("Live grade C (0/5, opened B)" = flows abandoned the setup mid-round, correctly shown).
The deeper fix for the lean-vs-line gap remains A2 (`p_up` = P(close ≥ beat) — the
bet's actual probability, where a +$70 lean against a $250 gap prices to ≈0).

## 5ap. Mid-day auto-relearn split the measurement window (2026-06-12)

The 13:49 era stamp in the live scorecard revealed an UNNOTICED second retrain
(~09:00→13:49). Cause chain: the operator's ~08:00 restart reset the in-memory
`last_train_time` → the auto-relearn COOLDOWN (24h) restarted from zero → the
(now sign-truth-driven) needs_retrain flag fired shortly after boot (7m was at 35%,
degrading) → full retrain → era split. `BTC_FREEZE_MODEL=0` permitted it.

Consequences: the day's evidence spans TWO v5 model instances (07:22 and 13:49 eras).
Era-filtered readers (scorecard, calibration, mirror REHYDRATION) handled it
correctly by design; the in-memory mirror strip (since ~08:00) blends both eras —
explains strip-vs-scorecard divergences the operator observed.

Standing gap (plan item): `last_train_time` is not persisted, so any restart zeroes
the relearn cooldown — with FREEZE=0 this guarantees era fragmentation. Options:
persist it beside train_boundary.json, or run FREEZE=1 between deliberate retrains
(recommended for measurement discipline; retrains become operator decisions).

**Findings that SURVIVED the era split (consistent across both v5 instances, the
day's real discovery):**
- **DOWN-lean edge: ~65%** (morning 63%, afternoon 65%, pooled n≈70) — when the
  model fights the up-drift to call DOWN, it's right ~2/3 of the time.
- **UP leans ~45%** (heavy majority of leans on an up-day — momentum-following noise).
- **Mirror > raw**: 5m mirror (committed boundary bets) 58% vs raw all-leans 50.9% —
  the selectivity machinery adds ~7 points. The gates earn their keep.

## 5aq. 1m/3m practice mirrors + per-model scorecard + log tabs (2026-06-12, evening)

Operator request. NO RETRAIN NEEDED (the model already serves all six horizons; this
is tracking/UI only). Activates at next restart + hard refresh.

- **1m/3m price-to-beat mirrors** added to the tracker (1,3,5,15). HONESTY LABEL:
  Polymarket's shortest real BTC market is 5m — the 1m/3m cards carry a visible
  "PRACTICE — no real market" badge and a footnote on the win-rate strip. Their
  purpose is evidence velocity (~60+20 rounds/hour vs 12+4), not betting.
- **Late-entry window now scales with horizon** (final ~40% of the window, cap 120s;
  min-ahead $5 for 1m/3m vs $10 for 5m/15m) — the fixed 15-120s rule would have
  flagged nearly an entire 1m round as "late entry".
- **Per-timeframe tabs on the resolved-rounds log** (All/1m/3m/5m/15m with counts,
  win/loss row borders); payload recent feed 20→40 so slow horizons stay visible.
- **Per-base-model accuracy section** in /api/scorecard + the script (new section 3):
  era-filtered directional accuracy per model per horizon from `model_predictions`
  (the per-model vote verifier's resolved rows) — the live "which model earns its
  seat" view that the roster surgery (R2/R5/TCN decisions) will be judged against.

Validation: pyflakes clean, compile clean, build clean.

## 5ar. v6 ROSTER SURGERY implemented on disk (2026-06-12, late) — activates at the
## operator's next natural restart (running session untouched)

Operator directive: implement everything possible now, no restart; the next natural
restart triggers the v6 retrain (arch bumped → auto-retrain). Retrain-#2 items (path
labels, time features, quantile bands) deliberately NOT included — they stack a second
experiment; they ship after v6 is measured.

**Implemented (Retrain-#1 bundle):**
- **R2 SGD retired** — removed from: store init, training block, stacker inputs, blend
  weights, dynamic weights, agreement votes + pairwise concordance, inventory,
  model_verifier MODELS, frontend label. Its `hit` rows remain in DB (era-filtered out).
- **A6 TCN promoted** — epochs 3→12; FULL stacker seat (special-cased fold construction:
  the PyTorch wrapper isn't sklearn-clonable → fresh instance per fold at half budget;
  the existing per-model try/except means a dl failure degrades gracefully, never
  crashes the stacker). TCN already used CUDA when available.
- **F1 GPU probes** — XGBoost CUDA + CatBoost GPU probed at import (LightGBM-probe
  pattern, silent CPU fallback). **SMOKE RESULT ON THIS MACHINE: XGB=cuda (NVIDIA
  confirmed!), CatBoost=CPU (pip build lacks GPU), LGB=gpu.** XGBoost was the slowest
  component (~235s/bucket) — expect a large training speedup.
- **R1 Kronos removed (backend-complete)** — imports, verifier, inference task, payload
  fields (kronos_forecasts/status/accuracy), scoreboard kronos columns, analysis
  snapshot fields; `kronos_model.py` + `kronos_verifier.py` DELETED (git history holds
  them). model.py's Kronos hooks left in place BY DESIGN: they self-gate on
  `kronos_accuracy` (absent → inert) — zero behavior, zero risk to the decision block.
  Frontend Kronos panels DEFERRED: all defensive (`|| {}`/`|| []`) → degrade to
  'waiting/fallback/NONE' placeholders safely; cosmetic cleanup rides the next UI pass.
- **R3 FSR-PPO mothballed** — `BTC_FSR_PPO=0` default (start.bat + env guard); payload
  carries a stub; code + tables intact for revival.
- **Arch bump:** `2026-06-12-v6-classbal-roster-130-tcn` — "classbal" kept in the string
  ON PURPOSE: the prior-division retirement is keyed on that substring.

**Also implemented (restart-only, no retrain):**
- **A2-lite p_up fair value** — P(close ≥ beat) from the projected close + conformal IQR
  (σ = IQR/1.349, floored): the Polymarket card now shows "Fair value: UP ≈ 62¢ ·
  DOWN ≈ 38¢ — buy a side only when the market asks LESS."
- **A13 early-exit hints** — at p_up ≥ 0.97 (or ≤ 0.03) the advice adds "[EXIT-EDGE:
  selling near 97¢ locks the win without tail risk]".

**Validation:** pyflakes CLEAN across backend; all files compile; full
`import server` + `MultiModelEnsemble()` smoke in a subprocess (temp DB/data dirs —
the §5am lesson applied): arch v6 confirmed, SGD absent from stores, FSR disabled,
GPU probes resolved. Frontend build clean.

**Activation:** everything above goes live at the operator's next natural restart,
which also auto-retrains v6 (~2.5-3.5h expected with XGB on CUDA). The running v5
session is untouched and keeps accruing evidence until then.

**RE-AUDIT (operator-requested, same night) — one REAL logic gap found & fixed:**
the TCN wrapper's `fit()` ACCEPTED `sample_weight` but silently IGNORED it — the
class-balanced loss (v5's headline fix) never reached TCN while the other five
classifiers trained balanced; in v6, with TCN promoted to a stacker seat, that gap
mattered. Fixed: per-sample weights folded into per-class CrossEntropyLoss weights
(exact for our class-constant × recency weights), 25k-cap slice now also slices the
weights. Smoke-proven: weights [1.125, 0.375, 1.5] reach the loss for a 2.0/0.5/1.5
weighting. Everything else re-verified clean: SGD = comments only; roster iterates
MODEL_LABELS keys (sgd row vanishes safely against BOTH old and new backends);
XGBoost train-on-GPU/infer-on-CPU handoff intact in both calibrated and fallback
paths; p_up correctly scoped and computed before the advice chain; EXIT-EDGE
direction-matched; build_scoreboard single definition + call; zero kronos code refs
(one docstring mention); model.py kronos decision hooks confirmed self-gating.

## 5as. Re-audit pass #3 (operator-requested, 2026-06-13) — 3 more findings, all fixed

1. **STRETCH override DIRECTION BUG (operator's screenshot = the proof):** §5ao's
   coherence override keyed only on the outlook scenario, but the outlook describes
   the LIVE lean — when the lean flips mid-round, the stretched lean is the
   OPPONENT of the held side. A DOWN bet ahead $91 was told "EXIT / SKIP — do not
   enter" while being near-certain to WIN. Fixed side-aware: lean==bet → exit/skip;
   lean flipped while bet leads → "HOLD — opponent counted out" (tone good).
   This bug was LIVE in the running session; fix activates at the v6 restart.
2. **⚡ LATE-ENTRY flagged on ⚠ fallback leans** — "persistence odds strongly favor
   UP" rendered on a card whose own badge said "WEAK — skip" (operator's 1m
   screenshot). Gated to committed model leans only.
3. **OOF stacker folds trained OFF-GPU** — fold refits inherited the post-fit
   inference downgrade (device='cpu') from the saved estimator, so the single
   biggest training block (~400s × 18 buckets) would have missed the CUDA speedup.
   Folds now train on the probed XGB_DEVICE.
   Also re-verified: the FSR-PPO frontend is fully defensive against the mothball
   stub (`block.by_horizon || {}` → clean empty state) — no patch needed.
4. **Practice-mirror DILUTION of the headline stats (operator's strip):** the 1m/3m
   mirrors fire ~5x faster and flooded the "Last 40 rounds" window (25/40 were 1m
   practice), so "Fallback 65% vs Model 44%" described practice luck + an n=9 model
   bucket — a blended statistic of exactly the kind this project keeps killing.
   Fixed (frontend-only, applies on hard refresh — no restart needed): headline
   grade/lean stats now compute over REAL markets (5m/15m) only, with a separate
   muted practice line. Cumulative per-horizon numbers were always clean (per-horizon).

## 5at. Redundant v5 relearn caught mid-flight + 7m/10m mirrors + 33h analysis (2026-06-13)

**Operational finding:** the 24h scheduled auto-relearn fired inside the RUNNING
process (~22:20) — and a running process trains the code it BOOTED with: the log shows
SGDLogLoss training, TCN at 3 CPU epochs, 'sgd' in the stacker = the OLD v5
architecture, NOT the v6 surgery on disk. That train would burn ~4.5h CPU and be
discarded at the next restart anyway (arch v5 ≠ v6 → v6 retrains).
**Recommendation given: restart NOW** — cancels the redundant train (sunk cost ~25min),
boots v6, retrains on GPU in ~2-3h. Standing lesson: with code-on-disk ahead of the
running process, scheduled relearns train stale code; set BTC_FREEZE_MODEL=1 between
deliberate restarts (also §5ap).

**7m/10m practice mirrors added** (operator request): tracker now (1,3,5,7,10,15);
practice badges on everything except the real 5m/15m markets; six log tabs; headline
stats stay 5m/15m-only (§5as fix); endpoint + scorecard extended; recent feed 40→60.
Activates at the same restart.

**33h era analysis (scorecard, n now meaningful):**
- **5m committed mirror 58% (104)** — borderline statistically significant (z≈1.6).
  The product's core (selectivity over raw leans: 58% vs 48.6% raw) keeps proving out.
- **DOWN-lean edge persists at scale**: DOWN ≈60% pooled (n=89) vs UP ≈47% (n=331)
  across horizons — consistent across three model instances now.
- **Problem horizons:** 3m raw 49% with a 136:11 UP flood (short-horizon UP-tilt is
  structural); 15m UP leans 14% (7/36 era rows) — 15m UP calls are near-inverse.
- **Per-model live table (3-class acc vs ~43%/57% NEUTRAL-rate ceilings — judge
  RELATIVE per column):** sgd LAST nearly everywhere (1m 5%, 5m 18%) — live data
  vindicates R2; **dl (TCN) top-3 at 5m/7m on 3 CPU epochs** — vindicates A6 before
  its budget even landed; lr massively overcommits at 1m (247 directional votes, 9%).
- Partial-candle buckets: 56/52/32/52 — odd 30-44s dip, n≈22/bucket, no verdict yet.

## 5au. RUN RECORD — v6 training launched (2026-06-13 23:26) + GPU findings

**Config (operator-set):** 50 days (71,925 samples / 57,540 train) · `BTC_FREEZE_MODEL=1`
(training is now a deliberate act — the boot-train runs on arch mismatch; auto/scheduled
relearns are OFF) · fresh console (50d confirmed in boot log) · arch target
`2026-06-12-v6-classbal-roster-130-tcn`.

**Plan confirmed in the log:** 24 eligible buckets / ~192 components — **the VOLATILE
regime tier trains for the FIRST TIME** (1,153 samples at 50d vs 998 at 40d — the
operator's window choice cleared the 1,000-sample guard by 153). Direction roster shows
6 models, no SGDLogLoss. Class weights firing (1m [1.399, 0.5, 1.527]). Components:
7/regime bucket, 11 GLOBAL — v6 arithmetic exact.

**Milestone observed at boot:** `[PRECISION] 3m calibrator ACTIVE (n=150, base rate
0.493)` — the isotonic calibration engine's FIRST activation ever, exactly at threshold,
on clean sign-truth labels (v5-era sample; re-earns after the v6 save, by design).
Boot also confirmed §5aj rehydration ("Restored 20 resolved price-to-beat rounds for
the UI") and 513 mirror outcomes restored.

**GPU hardware findings (operator observed "Intel GPU busy, RTX idle"):**
- `nvidia-smi` PROOF: the app process (PID 7232) holds a CUDA compute context on the
  **RTX 4050** — XGBoost CUDA is real; utilization spikes only during XGB phases and
  Task Manager hides the CUDA engine by default.
- **LightGBM's "gpu" is OpenCL and lands on the Intel iGPU** (platform 0 on dual-GPU
  laptops) — the activity the operator saw. Improvement queued: pin
  `gpu_platform_id/gpu_device_id` to the NVIDIA platform (Retrain #2 rider).
- **torch is the CPU build** → TCN trains on CPU (log: device=cpu). Upgrade queued:
  CUDA PyTorch install so A6's 12-epoch budget rides the RTX (Retrain #2 rider).
- CatBoost: CPU (pip build lacks GPU) — accepted.

**Ops notes:** memory ~85% during train = expected peak (2.2GB tensor at 50d, freed at
completion via explicit gc); training needs zero network from [TRAIN 1/192] onward —
low bandwidth only affects live feeds, which auto-reconnect. Backfill "current through
2026-06-11": Jun-12 publishes next start; the OLDEST ~10 days of the 50d window have no
CVD overlay (--auto extends forward only; recency weights make this minor; optional
one-time `--days 50` run fills it).

**Final pre-train static sweep:** pyflakes clean · all compile · zero imports of the
deleted kronos modules · zero live sgd references · residual kronos mentions verified
harmless (snapshot column writes '{}'; offline analytics reader; self-gating model
hooks; comments) · `fetch_kronos_accuracy()` = dead function, cleanup-pass candidate.

**Measurement plan:** v6 hot-swaps ~03:00-03:30; FROZEN single era; ~24h serve; then
the scorecard (per-model table + VOLATILE's first live report card + per-grade splits)
gates Retrain #2 (path labels + session/time features + quantile bands + CUDA-torch +
LGB platform pin). NOTE for reading the scorecard: v6 vs v5 differs by roster AND
window (50 vs 40 days) — a deliberate, low-risk confound accepted by the operator.

## 5av. v6 TRAINED OK but serving loop crashed — FSR mothball bug (2026-06-13 03:33)

**v6 train SUCCEEDED & SAVED** (03:33:31, arch `v6-classbal-roster-130-tcn`): GLOBAL 42 +
VOLATILE 36 components (VOLATILE tier trained for the first time), 8MB stackers (TCN
INCLUDED — log confirms `OOF...dl=0.346`, stacker features `['xgb','histgb','lr','lgb',
'cat','dl']` → A6 landed), backtest ran 03:48. Zero training loss.

**But the serving loop crashed every cycle** with
`UnboundLocalError: fsr_ppo_block` at server.py:2644. CAUSE (Claude's bug, §5ar
mothball): the FSR-PPO `recommend()` assignment was correctly guarded by
`if FSR_PPO_ENABLED:`, but a SECOND consumer 100 lines later (the per-horizon
`log_fsr_ppo_decision` block) still referenced `fsr_ppo_block` unconditionally. With
FSR off (default), the var is never created → crash after predictions generate but
before they record/broadcast (UI froze; no rows logged).

**Fix:** the logging block now reads `data_state["fsr_ppo"]` (always set — real block
or stub) and is gated by `FSR_PPO_ENABLED`. pyflakes clean, compiles.

**Why it slipped past verification (the real lesson):** pyflakes CANNOT flag
conditional-assignment-then-unconditional-use (the name IS bound on one branch), and
the pre-train smoke test imported + instantiated but never RAN the async `main_loop`.
Static checks + import smoke are necessary but NOT sufficient for control-flow bugs in
the serving loop. Future serving-path edits need a loop execution check, not just import.

**Recovery:** restart loads v6 from disk (arch matches → NO retrain, ~15s) with the
fixed loop. The 3.5h train is intact on disk.

## 5aw. Deep scan after the fsr bug — control-flow class audit (2026-06-13)

Triggered by the §5av crash, this pass hunted the SAME bug CLASS (conditional
assignment / missing var in the serving hot path — invisible to pyflakes) rather than
just re-linting. Findings:

**Serving-loop variable trace (the fsr bug class) — CLEAN except the one already fixed:**
- `predictions = []` and `meta_contexts = {}` initialized UNCONDITIONALLY at the top of
  every loop iteration (server.py 2442-43) → the recording block's `meta_contexts.get(h)`
  is always safe.
- `cascade_data` assigned and used only inside the same `if model.is_trained` block.
- `current_price` / `reference_price` assigned before every use (re-assigned at 2477,
  2563, 2673). No unbound paths.
- `fsr_ppo_block` was the ONLY conditional-assign-then-use bug (fixed §5av); re-verified
  it's now referenced only inside its own `if` block; the logging path reads the always-
  set `data_state["fsr_ppo"]`.

**Kronos hot-path hooks (left in model.predict, claimed "self-gating") — VERIFIED inert:**
- `_kronos_direction`: `data_state.get("kronos_forecasts") or []` → empty → returns
  "NONE" immediately. No crash.
- decision gate: `kronos_accuracy or {}` → total=0 → not proven → `kronos_dir_decision`
  "NONE" → no confluence vote, no `contradicted_by_kronos` veto.
- soft-confirmation nudge: empty accuracy → kdir "NONE" → zero nudge.
  → With kronos data absent (the v6 state), all hooks contribute nothing and cannot
  crash. Confirmed safe, not just asserted.

**Frontend removed-symbol consumers — all DEFENSIVE (cosmetic only):** every `data.kronos*`
read uses `|| {}` / `|| []` / `!= null ?` / `|| 'NONE'`; the "Kronos path" roster row and
scoreboard kronos cells render '--' placeholders. No crash; UI tidy deferred to the
cosmetic pass.

**7m/10m mirror — no hardcoded horizon assumptions:** the only `(5,15)` in price_to_beat
is the overridden default param; all internals use `self.horizons`; late-entry window
scales by horizon (verified for 1/7/10m).

**Static gauntlet:** pyflakes CLEAN backend-wide; all backend compiles; frontend builds
clean; `/api/scorecard` + per-model table proven working live (operator output).

**Honest scope limit:** static analysis + import smoke CANNOT execute the async
`main_loop`; the variable trace above is by-inspection. The definitive proof is the
operator's restart booting cleanly and the loop serving without the §5av error —
recommend watching the first ~60s of logs for any `Loop error`.

## 5ax. Kronos/FSR UI cleanup + v6 restart confirmed (2026-06-13)

**v6 confirmed serving correctly:** runtime probe — relearn IDLE (no retrain),
model_trained True, arch on disk `v6-classbal-roster-130-tcn`, serving loop healthy
(the §5av fix worked). Enhancement + training verified correct.

**Operator distrust was driven by DEAD KRONOS UI** (red ✗ chips, "Kronos NONE/WAIT",
"Kronos cross-check" everywhere) — backend retired Kronos but the UI still rendered its
ghost, making working components look broken. The deferred "cosmetic pass" was actually
hurting confidence, so done now (frontend-only, applies on hard refresh):
- Scoreboard: removed kronos chip + Kronos column + Agree column → now Ensemble-final +
  Raw-lean (the gate is no longer dragged down by a permanently-failing kronos check).
- Decision Center: removed the Kronos GATE (was counting as a failing evidence check,
  lowering the rating) and the "next confirmation" kronos text; replaced the "Kronos
  cross-check" card with an "Expected move" card.
- Roster: removed the "Kronos path" row.
- Price-to-beat confluence card: removed kronos line, chip, acc.
- Forecast Scorecard: replaced the dead Kronos acc/err columns with a **Directional /
  UP / DOWN / move-err** layout — the bias-watch split, genuinely useful.
- FSR-PPO panel: "WAITING" → "MOTHBALLED" (reads status.enabled=false from the stub).
- index.html: "Kronos Forecast Targets" section hidden (ids kept as empty elements so
  the still-running forecast-targets renderer can't null-crash); all Kronos mentions in
  section descriptions removed.

**Reminder for reading metrics now:** the restart reset the model era — current
mirror/scorecard rows are the FIRST v6 rounds (n=2-6 = pure noise). Judge only at n>100.

**Validation:** frontend builds clean (bundle shrank 282.3→281.3 kB = dead code gone);
no dangling references (getKronosAtHorizon/kronosStatus retained but null-safe).

## 5ay. Fair-value removed (display-only) + Binance per-TF table (2026-06-13)

- **Fair-value / p_up removed** (operator: "incorrect, not adding value"). CONFIRMED
  display-only: p_up lived in price_to_beat.py (downstream of the model, consumes its
  output), was NEVER a training feature or a prediction input — removing it changed
  ZERO model behavior, accuracy/precision byte-identical. It was Φ(edge/σ) on the flat
  ~$40 mean-move magnitude = fake-precise cents. Proper p_up returns in Retrain #2 atop
  A3 (breathing quantile magnitude). EXIT-EDGE hint removed too (same basis).
- **Binance tab: per-timeframe resolved-calls table** (operator request, mirrors the
  Polymarket table). Per-TF tabs (All/1/3/5/7/10/15m) with counts, per-tab W/L + UP/DOWN
  summary, sign-truth grading (✓/✗), up to 25 rows, scrollable. Sourced from the
  already-sent `verification.histories` (30/horizon) → NO backend change, applies on
  hard refresh. This is the model's PURE directional accuracy (lean vs realized move),
  the cleanest skill measure — distinct from Polymarket's beat-the-anchor grading.

## 5az-2. Offside/flipped Polymarket header (2026-06-13) — frontend only

Operator: cards looked "contradictory." Root cause confirmed NOT a model bug — the
colored header is FROZEN at window-open while everything below is live; a bet that has
gone wrong still showed a big confident "UP · Grade A" while the small print said
flipped/EXIT. The UI juxtaposed opened-vs-live without resolving them.

Fix (renderPolymarketView card header): compute `offside` (live current_position
opposite the opened bet side = losing) and `flipped` (live_lean differs from opened
lean). Header now shows:
- offside → red "⚠ OFFSIDE — bet UP, price now on DOWN side" + muted "opened UP·A" tag;
  card border turns red.
- flipped → amber "⚠ LEAN FLIPPED → model now DOWN" + muted opened tag; border amber.
- else → the normal confident opened header (unchanged).
The big visual now matches live reality; the stale opening grade is de-emphasized, not
removed (still visible as "opened …"). Display-only, no measurement impact, hard-refresh.

## 5ba. Per-model accuracy neutral-poisoning fix + price-to-beat docstrings (2026-06-13)
## — activates at the operator's NEXT restart (running 07:50 process has the old code)

Two changes this session, both **measurement/display-only** (no model behavior, no schema change,
no impact on the frozen v6 evidence run). Found during an operator-requested DuckDB/model analysis
(via `/api/scorecard` — the DB file is exclusively locked by the live app).

**1. `model_verifier.check` — per-base-model accuracy was neutral-poisoned (the 9th hit/neutral
grading-class bug).** The per-model panel showed every base model at ~0–20% across all horizons
(`lr` 0/48 at 1m, `cat` 6% at 5m, etc.) — not skill, an artifact. A base model's argmax is NEUTRAL
on most ticks (abstention, esp. short horizons), but the grader compared that NEUTRAL against a
near-always-moved market (`actual_dir` UP/DOWN over 5–15m), scoring every abstention as a directional
miss with all of them in the denominator. It was measuring "how often does an abstention equal a
moved market" ≈ 0. Same family as the §5z–5ai sign-truth fixes (calibration, regime-quality,
analytics, auto-learning). **Fix:** grade only COMMITTED (UP/DOWN) votes by strict close-vs-ref sign;
exclude NEUTRAL from the denominator; NEUTRAL rows still resolved with `hit=NULL` (BOOLEAN col is
nullable) so they don't sit pending; `latest_vote` still exposes the raw argmax. After restart the
panel reads ~40–55% (real) instead of ~5%. Syntax-verified.

**2. `price_to_beat.py` docstrings corrected (Chainlink/Binance → Pyth).** Module header said the bet
anchored/resolved on *Chainlink*; `update()` said *Binance aggTrade*. The live anchor is **Pyth**
(REST-polled) with an offset-corrected Binance fallback, same-feed rule (`klines=None` whenever the
anchor isn't raw Binance). Since this file documents the *bet-settlement feed*, the wrong name was
actively misleading. Corrected both docstrings to the real Pyth-with-fallback behavior. No logic
change (a logic audit this session found the same-feed enforcement already correct in all branches).

**Operational note (not a code change):** the `[0/3]` step now successfully backfilled 2026-06-12
SPOT aggTrades (787,812 trades → 59,040 bars, CVD/VPIN non-zero & varied) — the aggTrade history gap
is closed; the next retrain sees full history on the trade-derived features.

**New design docs added this session** (pointers for the next retrain): the honest measurement-window
record [MEASUREMENT_WINDOW_2026-06-13.md](MEASUREMENT_WINDOW_2026-06-13.md) (regime edge map, gate /
meta-model status, betting mirror, logic audit, DuckDB analysis) and the accuracy spec
[SPEC_ACCURACY_NEXT_RETRAIN.md](SPEC_ACCURACY_NEXT_RETRAIN.md) (the train/serve-gap diagnosis +
Tracks A/B/C). Both cross-linked from [V5.md](V5.md).

## 5bb. B1 feature-logging + B2 conviction-gate implemented (2026-06-13)
## — NO retrain; activates at the operator's NEXT restart (running process has old code)

Operator asked to implement B1 + B2 + B4 with **no retrain** (and ideally no restart). Clarified:
B1/B2 are serving-loop CODE → they require ONE restart to load (a frozen ~12s boot, NOT a retrain,
era preserved); a running Python process cannot hot-reload. B4 is hardware (wired Ethernet) — no code.

**B1 — live per-bar feature logging (the train/serve-gap fix infrastructure).** NO TRAIN.
- `database.py`: new additive table `feature_outcome_log(ts BIGINT PK, schema_hash VARCHAR,
  regime VARCHAR, features DOUBLE[])` in `init_db`; new crash-safe helper `log_feature_vector(...)`
  using `INSERT OR IGNORE` (dedupes the once-per-cycle key).
- `server.py` record loop: logs `seq[-1]` (the live per-bar feature vector) ONCE per recording cycle,
  keyed by `now_ms` == `predictions_{h}m.timestamp`. Guarded by `_feature_logged` + try/except so a
  logging failure can never break serving. No resolution hook — the outcome already persists in
  `predictions_*`; a future retrain JOINs on ts:
  ```sql
  SELECT f.features,
         CASE WHEN (p.raw_direction='UP' AND p.actual_move>0)
                OR (p.raw_direction='DOWN' AND p.actual_move<0) THEN 1 ELSE 0 END AS label
  FROM feature_outcome_log f JOIN predictions_{h}m p ON f.ts = p.timestamp
  WHERE p.resolved AND p.raw_direction IN ('UP','DOWN');
  ```
- WHY: the high-edge microstructure features are constant in the historical training matrix
  (`server.py:1160` broadcasts one live snapshot over 50d). Logging them live is the only way a
  future retrain can learn them. Value is calendar-gated — start the clock now. (SPEC Track B1.)
- Storage: ~1 row per recording cycle (deduped per ts), 130 doubles/row — bounded, append-only.

**B2 — conviction-gate (Option A: visible-but-not-a-bet).** NO TRAIN.
- `server.py apply_live_quality_filters`, right after the <50% poor-regime gate: a cell that cleared
  the gate but is still measured **50–54%** (READY, e.g. 5m LOW_VOL ~51.7%) keeps its directional
  READ (direction unchanged) but has `actionable` stripped + `convictionCapped`/`convictionCapReason`
  set. Conviction reserved for PROVEN-edge cells (≥54%). Marginal-but-not-ready cells keep the benefit
  of the doubt. Serving-side only — no effect on `raw_direction` or the sign-truth tables.

**Verification (all PASS):** `ast.parse` both files; throwaway-DuckDB test proved DOUBLE[] binding,
`INSERT OR IGNORE` dedup (3 inserts/1 dup → 2 rows), 130-len vectors, and the train-time join (2 rows,
correct sign-truth labels); import smoke test (helper present, schema_hash `4d2aec96c919`,
NUM_FEATURES 130, B2 marker present). The live DB is exclusively locked, so nothing touched it.

**Post-restart confirmation (operator):** after the next restart, `SELECT COUNT(*) FROM
feature_outcome_log` should climb; B2-capped cells carry `convictionCapped=true` in the payload.

## 5bc. New "Binance · Price to Beat" tab — Binance-anchored mirror (2026-06-13)
## — backend activates on NEXT restart; frontend on Vite rebuild (done). NO retrain.

Operator: want an EXACT replica of the Polymarket tab but priced on **live Binance** (the model's
native feed), keeping the existing Pyth Polymarket tab untouched. Rationale: judge the model against
the data it actually learns on; the Pyth tab stays for real Polymarket betting.

- `price_to_beat.py`: added `persist=True` param. A secondary tracker MUST be in-memory (it reuses the
  `ptb_{h}m_{win_start}` ids → would collide/corrupt the shared `price_to_beat` table). Both
  `database.log_price_to_beat` / `resolve_price_to_beat` calls now guarded by `if self.persist`.
- `server.py`: `price_to_beat_binance_tracker = PriceToBeatTracker(..., persist=False)`; in the
  price-to-beat ticker, after the Pyth update, a parallel `update()` anchored on `live_price`
  (same-feed → klines boundary recovery valid), with its own freshness tracking; new payload key
  `price_to_beat_binance` mirroring `price_to_beat`. In-memory → rebuilds live after restart.
- `index.html`: nav tab `🟡 Binance · Price to Beat` + `#binancepm-view` section (bpm- ids).
- `main.js`: refactored `renderPolymarketView` into a shared `renderPMCore(data, cfg)` + two thin
  wrappers (`PM_CFG.pyth` / `PM_CFG.binance`); the Pyth tab output is byte-identical (same ids, same
  data key). Binance variant reads `price_to_beat_binance` + the Binance price; tab toggle, live-update
  dispatch, and the resolved-log TF filter all branch on `currentAppTab==='binancepm'`.

Verified: `ast.parse` server.py + price_to_beat.py; `npm run build` clean (283 kB). Existing
Polymarket (Pyth) tab unchanged. Backend tracker activates on the next restart.

## 5bd. A1 persistence recorder built; A9 crowd recorder BLOCKED (2026-06-13)
## — NO retrain; activates on next restart

Operator asked for A9 + A1 recorders (B1 pattern, no retrain).

**A9 (Polymarket crowd price as a feature) — NOT BUILT, blocked by data.** `polymarket_client`
only discovers LONG-DATED threshold markets ("Will BTC hit $150k by …", confirmed by
`_extract_reference_price` regex). The 5m/15m up/down markets are geo-blocked on this box (prior
probe: gamma-api returns long-dated only; polymarket.com blocked w/o VPN). Recording long-dated
prices as a 5m feature = noise, so A9 is deferred until an accessible 5m crowd feed exists. NOT built.

**A1 (late-entry / T3 persistence recorder) — BUILT.** Self-contained, no external feed.
- `database.py`: table `persistence_snapshot(round_id, horizon, ts, seconds_left, distance, position)`
  + crash-safe helper `log_persistence_snapshot(...)`.
- `price_to_beat.py`: in `_refresh_live`, the **Pyth tracker only** (settlement feed; `persist=True`)
  logs one snapshot per open round per ~15s (`self._last_snap` dedupe): distance-to-line, seconds
  left, current side. No resolution hook — the LABEL ("did this side hold to close?") is derived at
  TRAIN time by joining `round_id -> price_to_beat.actual_direction` (same pattern as B1). The Binance
  mirror (`persist=False`) does NOT log → no double-write.
- Train-time query: `SELECT s.*, CASE WHEN s.position = p.actual_direction THEN 1 ELSE 0 END AS held
  FROM persistence_snapshot s JOIN price_to_beat p ON s.round_id = p.id WHERE p.resolved`.
- Verified: `ast.parse` both files; throwaway-DuckDB test (3 snapshots → correct held/flip labels via
  the join). Activates on next restart.

This is the data source for A1 (the T3 95%-precision engine) and A1-ext (path labels). Combined with
B1 (full feature vectors by ts), a future retrain has both the microstructure features AND the
intra-window persistence trajectories.

## 5be. Offline data-collection pipeline built + VALIDATED (2026-06-13) — no retrain, no uptime

Operator: build the offline backfill scripts, validate them, apply the same pattern across all
collectors, document everything. Goal = reconstruct the retrain data from archived history instead of
weeks of live uptime.

**Built (2 new scripts) + validated end-to-end on a real day (2026-06-12):**
- `build_persistence_dataset.py` (A1) → `persistence_dataset.parquet`. Replays tick-level SPOT
  aggTrades → intra-window snapshots `(distance, seconds_left, position, vol_60s_pct) → held`. VALID:
  787k trades → 31,924 snapshots; **late-entry (≤60s, ≥$10 ahead) held 90.8%/95.1%/94.8%/97.2% at
  5/7/10/15m** — the T3 95%-tier signal, visible in one day of real data.
- `build_crossvenue_flow.py` (A4) → `crossvenue_flow.parquet`. Binance SPOT-vs-PERP per-1m-bar
  `cvd_spot/cvd_perp/cvd_divergence/perp_spot_basis_bps`. VALID: spot 787k + perp 1.40M trades → 1,440
  bars; basis ~−5 bps (persistent), cvd_divergence ±300. Chose spot-vs-perp over Coinbase/Bybit because
  perp aggTrades ARE archived (Coinbase has no bulk history → would re-create the train/serve gap).
- `backfill_trade_features.py` (existing) re-validated: 787k → 1,440 bars, CVD/VPIN non-zero/varied.

Both new builders follow the existing keystone pattern (reuse `download_day`/`load_aggtrades`,
`--validate DATE` dry-run, testable pure core unit-tested on synthetic data, parquet out). Synthetic
core tests PASS for both; real-day validates PASS.

**PARITY TODO (documented):** `crossvenue_flow` needs a live Binance futures-aggTrade recorder
(same per-bar CVD) BEFORE its columns go into `FEATURE_NAMES` — else constant in serving. A1 already
has its live twin (`persistence_snapshot`). B1's L2 subset has no offline twin (not archived).

**Docs:** new [DATA_COLLECTORS.md](DATA_COLLECTORS.md) (unified registry of all collectors, offline +
live, with the standard pattern + parity rules) and [RETRAIN_RUNBOOK.md](RETRAIN_RUNBOOK.md) (the
60–90 day staged retrain). No serving-loop changes; no schema bump; no retrain.

## 5bf. Live perp-CVD recorder (A4 parity) + start.bat data knob (2026-06-13) — activates on restart

**Live perp-CVD recorder** — the parity twin for `build_crossvenue_flow.py`. The spot leg is already
live (trade_features keystone); this fills the missing PERP leg so the spot-vs-perp divergence can
later become a model feature with train/serve parity. NO schema bump — it records to a side table now.
- `data_ingestion.py BinanceFuturesWebSocketClient`: added `btcusdt@aggTrade` to STREAMS + a testable
  `_ingest_perp_trade()` that accumulates per-clock-1m-bar perp CVD (taker-buy positive — IDENTICAL
  sign to the offline `_per_bar`) and emits a `perp_bar` on rollover. Sentinel is `None` (a real
  bar_ms is never None) so a bar_ms of 0 can't collide.
- `database.py`: `perp_cvd_live(ts PK, cvd_perp, vol_perp, perp_price)` + crash-safe
  `log_perp_cvd_bar` (INSERT OR IGNORE dedup).
- `server.py`: `handle_perp_bar` + `futures_ws_client.on("perp_bar", handle_perp_bar)` (mirrors
  `handle_liquidation`). Crash-guarded.
- VERIFIED: live accumulator emits the right bar; **PARITY live==offline CVD**; DB table+dedup; all
  backend parses. At retrain: UNION `perp_cvd_live` + `crossvenue_flow.parquet` → the divergence feature.

**start.bat data knob** — new `BTC_BACKFILL_DAYS` (defaults to `BTC_HISTORICAL_DAYS`) drives all three
offline builders. Set it to 60/90 and every collector adjusts. Verified `--auto` math earlier.

## 5bg. GEX (dealer gamma) recorder + backfillable feature plan (2026-06-13)
## — NO retrain, NO schema bump; recorder activates on next restart

Operator: build the highest-value NEW signal (Deribit GEX) + queue the backfillable
features. Rationale (from external data-source research + the ceiling diagnosis): more
L2-derived features are dead weight until L2 history exists; the features that actually
help are NON-price, NON-L2 *new information* — and GEX (options dealer positioning) is
the standout, sourceable now.

**GEX recorder — BUILT + VALIDATED.** Net dealer Gamma EXposure ($/1% move): calls +,
puts − (positive=dealers long gamma→price pins/mean-reverts; negative=short gamma→
trending/explosive — a REGIME signal that is neither price- nor order-book-derived).
- `institutional_feeds.py`: `_bs_gamma` (analytic Black-Scholes gamma, math-only, no
  scipy) + `compute_gex(instruments, now_ms)` — reuses the EXISTING `get_book_summary`
  fetch (OI, mark_iv, underlying, strike, expiry); NO per-instrument ticker call. Wired
  into `fetch_options_summary` → `self.data["gex"]`/`["total_gamma"]` (guarded).
- `database.py`: side table `gex_live(ts PK, gex, total_gamma, spot, pcr, atm_iv)` +
  crash-safe `log_gex` (INSERT OR IGNORE dedup). NO FEATURE_NAMES change → frozen v6 model
  unaffected; a future retrain aligns on ts to add GEX as a slowly-varying feature.
- `server.py`: logs GEX once per recording cycle right after the B1 hook, guarded by
  try/except (a logging failure can never affect serving). `deribit_client` is a module
  global; all vars in scope → no unbound risk (the §5av lesson applied).
- VERIFIED: ATM gamma > OTM > 0, degenerate→0; call-heavy book → +GEX, put-heavy → −GEX,
  empty/garbage → safe zeros; DB round-trip + dedup. pyflakes clean, all compile.
- Backfill note: real-time OI/IV is only CURRENT from the free book-summary, so GEX is a
  LIVE-accumulation signal (start-the-clock, like B1's L2) — Deribit history/vendor could
  backfill later. Activates on next restart; confirm `SELECT COUNT(*) FROM gex_live`.

**Backfillable feature additions — QUEUED for the next retrain** (added to RETRAIN_RUNBOOK
Phase 1; NOT added to features.py now because that bumps the schema → breaks the frozen
model → forces a retrain). All non-L2, so they actually get learned (unlike L2 dead weight):
- `variance_ratio` (var(k-ret)/(k·var(1-ret))) — mean-revert vs trend; kline-derived, free.
- price-efficiency: permanent-vs-temporary impact, return autocorrelation — from aggTrades.
- `rv_term_structure`, A8 session/time, funding×momentum — already in the runbook.
These join the C1/C2 backfillable bundle (the cheapest "new edge" retrain).

## 5bh. start.bat syntax fix (2026-06-13) — "Updating was unexpected at this time"

The §5bf three-builder backfill block used unescaped `(a)`/`(b)`/`(c)` labels inside the
`else ( ... )` block → cmd's block parser hit the stray `)` and aborted with
"Updating was unexpected at this time" (boot never started). Python was validated that
session but the Windows cmd parsing of the .bat wasn't. Fix: removed ALL parens from
those three echo lines (`(a)`→`a.`, and the `^(...^)` descriptors → ` - ...`). Lesson:
test .bat edits on the actual cmd shell, not just the Python they call.

## 6. Known limitations / honest notes
- `vpin` and the backfill's `funding_velocity` are present in the parquet but intentionally not
  fed to training (skew-avoidance). Feature 17's funding_velocity uses the existing
  `funding_rate` key (sparse historically — improves only with live coverage).
- Expected accuracy lift from this batch is **modest** (Class-A features are real but
  secondary); the larger gains remain calibration + meta-labeling once data accrues.
- The schema hash changed → any cached feature-importance/PSI baselines recompute on the new
  117-feature set after the retrain (expected, additive).
