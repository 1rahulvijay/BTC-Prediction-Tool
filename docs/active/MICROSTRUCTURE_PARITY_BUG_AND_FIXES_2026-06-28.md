# Microstructure feature parity bug — investigation, root causes & fixes (2026-06-28)

**Trigger:** operator instinct — *"the app has some major incorrect logic in prediction and training."*
**Outcome:** a real, multi-day production bug found and (mostly) fixed. The model's **trade-derived
microstructure features were dead-zero in live serving** while it was trained on real values.

---

## 0. TL;DR

| Feature(s) | Live value the model saw | Should have been | Status |
|---|---|---|---|
| `cvd_1m`, `cvd_5m`, `cvd_change` | **0** (constant) | live order-flow CVD (e.g. −1.63) | ✅ **FIXED** (live-overlay) |
| `large_trade_delta`, `large_trade_imbalance` | **0** | live large-trade flow | ✅ **FIXED** (live-overlay) |
| `vpin` | **0** | order-flow toxicity (~0.3–0.5) | ✅ **serve-path FIXED**; analyzer value is a slow cold-start (not a bug) — see §3 |
| `rv_15m`, `funding_velocity` | (scale-divergent) | — | 🟦 advisory; likely definitional, not a live break |

**Blast radius (honest):** this did **not** break `P(Hold)` or the band (both price-derived → why P(Hold)
calibration stayed stable), and it did **not** cause the 5m/15m coin-flip (direction is dead *with* live CVD
too, per `edge_probe`). It **degraded the selectivity head** (lost `vpin`) and fed the **direction
ensemble** ~6 dead features. It also gave a **false-green "feed alive"** flag for days.

---

## 1. How it was found (the probe + diagnostic chain)

1. **`backend/research/standalone/probe_feature_parity.py`** — compares the LIVE feature vector (`feature_outcome_log`) to the
   offline training matrix, per feature. Flag = **DEAD-IN-LIVE** (live std ≈ 0 while offline varies).
   Result: **6 features exactly 0 across 10,074 live rows.**
2. **`backend/research/standalone/probe_trade_feed.py`** — independent WS to `btcusdt@aggTrade`: **296 trades/20s** → the
   endpoint delivers fine. So it's not the feed.
3. **`[ws-rx]` counter** (data_ingestion) — `btcusdt@aggTrade` flowing (974→7,232) with **zero** parse/emit
   errors → the WS → emit → `handle_trade` → `process_trade` chain works.
4. **`[trade-diag]`** (server.py `handle_trade`) — `analyzer.cvd`, `cvd_1m(60s)`, and `summary.cvd_1m` are
   all **live and non-zero** (e.g. 11.79), but **`vpin=0.0000`** at every checkpoint (up to 7,500 trades).
5. **`[feat-diag]`** (server.py feature-log site) — the smoking gun:
   ```
   [feat-diag] of.cvd_1m=-1.6286   sighist.cvd_1m[-1]=0.0000   seq[-1][cvd_1m]=0.000000   sighist_len=1500
   ```
   Live snapshot has it; `signal_history` returns 0; built feature is 0.

---

## 2. Root cause #1 — CVD / large-trade masking (FIXED)

**Chain:** the live predict loop builds features via
`build_features_from_klines(recent_klines, data_state["order_flow"], …, signal_history=live_sig_hist)`.
Inside, the `series(key, snapshot_val)` helper (features.py:779) does:
```python
arr = sh.get(key)
if arr is not None and len(arr) == n:   # use the history array
    return np.asarray(arr)
return np.full(n, snapshot_val)          # else broadcast the live snapshot
```
- `live_sig_hist = signal_buffer.get_aligned_series(...)` returns a **full-length array** where every
  candle WITHOUT a live snapshot is filled with the **neutral default `0.0`** (signal_history.py:209-215).
- Live snapshot coverage is sparse, so the array is effectively all-zeros.
- `series()` sees a non-None, full-length array → **uses the zeros**, ignoring the live `of.cvd_1m`.
- **Training is unaffected** because `overlay_backfill()` fills those gaps from the backfill parquet
  (signal_history.py:217) — but **live serving has no overlay**, so the gaps stay 0.

**Fix applied (`backend/server.py`, live predict loop, ~2870):** drop the keys the live order_flow already
provides from `live_sig_hist`, so `series()` falls back to broadcasting the live value (the live equivalent
of training's overlay):
```python
for _msk in ("cvd_change", "cvd_1m", "cvd_5m", "large_trade_delta", "large_trade_imbalance"):
    live_sig_hist.pop(_msk, None)
```
Surgical, live-path only — does not touch `build_features_from_klines` or the training path. After this,
`series("cvd_1m", of.get("cvd_1m"))` broadcasts the live CVD across the window → `seq[-1]` (the model's
input AND the logged vector) carries the real value.

---

## 3. Root cause #2 — VPIN (serve-path FIXED; analyzer is a slow cold-start, NOT a code bug)

`[trade-diag]` showed `analyzer.vpin = 0.0000` across **49,000 trades** (a 68-min run). My first hypothesis
— a "one-shot `==`" bug — was **WRONG**. The update code (order_flow.py:144-148):
```python
if self._vpin_fill >= self._vpin_bucket_vol - 1e-12:
    self._vpin_imb.append(abs(self._vpin_buy - self._vpin_sell))
    self._vpin_buy = self._vpin_sell = self._vpin_fill = 0.0
    if len(self._vpin_imb) == self._vpin_n:
        self.vpin = sum(self._vpin_imb) / (self._vpin_n * self._vpin_bucket_vol)
```
`self._vpin_imb` is a **`deque(maxlen=_vpin_n)`** (order_flow.py:70), so once it fills it *stays* at
`maxlen` — `len(...) == self._vpin_n` is True for **every** bucket thereafter, and `self.vpin` recomputes
each bucket. The logic is correct.

**The real reason it reads 0 is warmup, and it's expensive.** With
`trade_features.DEFAULT_ROLLING_BUCKETS = 50` and `DEFAULT_BUCKET_VOLUME_BTC = 15.0`, the deque only fills
after **50 × 15 = 750 BTC of spot volume** (~1 h of continuous live trades on this box), and the deque
**resets to empty on every restart**. So `vpin` legitimately reads 0 for the first ~hour of any run — the
49,000-trade log was simply still short of 50 full buckets. This is **cold-start, not a bug**; no analyzer
change made (changing the bucket params would break train/serve parity with the backfill, which also uses
15 BTC / 50 buckets).

**VPIN serve path — FIXED (2026-06-28).** Even a *warm* analyzer vpin could not reach the model, for the
same two reasons CVD couldn't:
1. `features.py:1146` hardcoded `series("vpin", 0.0)` → **changed to** `series("vpin", of.get("vpin", 0.0))`.
   Train path is unaffected: `overlay_backfill()` overwrites col 112 from the parquet (signal_history.py:234),
   so this only changes the *live* fallback.
2. `live_sig_hist` masked it with the 0.0-default array → **`"vpin"` added to the `live_sig_hist.pop(...)`
   set** (server.py ~2878), the same live-overlay trick used for CVD.

Net: once the analyzer warms (~1 h continuous), vpin now flows all the way to the model and the
`feature_outcome_log`. Until then it reads 0 — expected.

---

## 4. Fixes MADE this session (chronological)

| # | Fix | File(s) | What |
|---|---|---|---|
| 1 | **Microstructure live-overlay (CVD/large-trade + VPIN)** | `server.py` (~2870), `features.py:1146` | the headline fix above — un-deads cvd/large_trade live; vpin added to the pop set + its hardcoded-0.0 snapshot replaced with `of.get("vpin")` so a warm vpin lands (analyzer warmup ~1 h, §3) |
| 2 | **Trade-freshness guard + loud warning** | `server.py` `handle_trade` (`last_trade_ms`) + feature-log gate | feature-log now keys off TRADE freshness, not depth; `[feed] TRADE stream stale` warning so a trade outage can't go silent |
| 3 | **5m UP-tilt experiment (retracted)** | `model.py` (`BTC_DIR_MARGIN_{h}`), `start.bat`, `start_instant.bat` (`BTC_DIR_MARGIN_5=0`) | measurement showed wider margins selected a more UP-skewed subset; bias must be repaired in training/calibration, not by this dead zone |
| 4 | **auto_finetune 360d window** | `auto_finetune.py` | the nightly recalibration's matrix step now uses `BTC_HISTORICAL_DAYS or 360` (was silently 60d) |
| 5 | **A/B/C grade demoted to experimental** | `src/main.js` (rounds panel) | the grade can't stratify direction; demoted to a dim labeled sub-line, Model-vs-Fallback promoted; precision = T3 P(Hold) |
| 6 | **Diagnostics (temporary)** | `data_ingestion.py` (`[ws-rx]`, elevated parse-error WARNING), `server.py` (`[trade-diag]`, `[feat-diag]`) | the instrumentation that localized the bug — **remove after verifying** (see §6) |

**Probes/tools built (read-only, reusable):** `probe_feature_parity.py`, `probe_trade_feed.py`,
`grade_scorecard.py`, `probe_direction_tilt.py`, `probe_l2_linecross.py`, `probe_impact_residual.py` —
each with a `--selftest`.

---

## 5. Verification

1. **Restart** the app (picks up the CVD fix + the guard).
2. The `[feat-diag]` line should now read `seq[-1][cvd_1m]` ≈ `of.cvd_1m` (non-zero) — CVD is no longer
   masked.
3. After a few minutes, stop the app and run `python backend\research\standalone\probe_feature_parity.py` — `cvd_*` /
   `large_trade_*` should drop off the **DEAD-IN-LIVE** list (their live std > 0). `vpin` will read
   non-zero only **after ~1 h of continuous uptime** (750 BTC warmup, §3); a short run still shows it 0,
   which is expected — re-check parity on a log that spans a multi-hour session.

---

## 6. Cleanup — DONE (2026-06-28, post-verification)

**CVD fix confirmed in production** on the restart: `[feat-diag]` read
`of.cvd_1m=0.1954  sighist.cvd_1m[-1]=-999.0000  seq[-1][cvd_1m]=0.000192  sighist_len=None` — the built
feature is now **non-zero** (was exactly `0.000000` on every pre-fix tick), proportional to the live
snapshot after normalization; the `-999`/`None` sentinels confirm the `live_sig_hist.pop` is firing. Both
calibrators also came up ACTIVE (5m n=362, 15m n=166).

Temporary diagnostics then **removed**:
- `data_ingestion.py`: the `[ws-rx]` per-stream counter block (all 3 connections). **Kept** the parse-error
  WARNING (`[ws] parse/emit error …`) — genuinely useful to surface swallowed WS errors.
- `server.py`: the `[trade-diag]` block in `handle_trade` and the `[feat-diag]` block at the feature-log
  site. **Kept** `data_state["last_trade_ms"]` + the `[feed] TRADE stream stale` warning (fix #2) — permanent.

**VPIN** still reads 0 short-term (warmup, §3); confirm it later via `probe_feature_parity.py` on a log
spanning a multi-hour continuous session.

---

## 7. Lessons

- **A per-substream freshness signal is mandatory** when one feed (depth) can mask another's (trades)
  outage. The `[feed]` guard now enforces this.
- **`get_aligned_series` returning default-filled arrays + `series()` preferring them over the live
  snapshot** is the exact "history masks the live value" trap. Live serving needed the same overlay
  training had — now it broadcasts the live snapshot.
- The operator's instinct was right four times this session; the parity probe is what converted "something
  feels wrong" into a precise, fixable defect.

---

## 8. June 30 correction - large-trade features were not fully restored

The June 28 report overstated the large-trade fix. `server.py` correctly removed
`large_trade_delta` and `large_trade_imbalance` from the sparse live history, but
`features.py` still called:

```python
series("large_trade_delta", 0.0)
series("large_trade_imbalance", 0.0)
```

That meant the fallback remained zero after the history key was removed. Both columns are selected in
the active 69-feature model schema, so this was a real serving defect.

The fallback now uses the live order-flow snapshot:

```python
series("large_trade_delta", of.get("large_trade_delta", 0.0))
series("large_trade_imbalance", of.get("large_trade_imbalance", 0.0))
```

A focused synthetic serving test confirmed exact nonzero values for both features and VPIN. No direction
retrain is required: historical training already used the trade backfill; this restores train/serve parity.
