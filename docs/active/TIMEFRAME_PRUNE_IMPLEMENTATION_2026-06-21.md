# Timeframe Prune — Implementation Plan: Done / Not-Done + Overnight Retrain (2026-06-21)

> **⚠️ SUPERSEDED (2026-06-22): FINAL keep-set = {5, 15}** (markets only — 1m was also dropped). This doc
> describes the earlier **{1,5,15}** plan + a 250d retrain. The as-built code is uniformly **{5,15}**
> (arch `2horizon-5-15`), and the retrain is now the **360d completion-marker lifecycle** — see
> [FULL_360D_RETRAIN_IMPLEMENTATION](FULL_360D_RETRAIN_IMPLEMENTATION_2026-06-22.md). Read below for the
> *mechanics* (phases, gating, runtime-safety, legacy-head note), but treat every `{1,5,15}` / `250d` /
> `BTC_HISTORICAL_DAYS=250` mention as historical — the live answer is `{5,15}` and `.\start.bat`.

**Keep-set: {1, 5, 15}** (5m/15m = the only tradeable Polymarket markets; 1m = fastest feedback +
densest P(hold)). **Removed: {3, 7, 10, 30}** (no market, coin-flip direction, pure compute).
Path-labels & the conformal band = {5, 15} only (they have no 1m).

This was done as Phase 1 (tracker/UI, no-retrain) + Phase 2 (models/datasets, needs the overnight retrain).
Both phases are **code-complete and validated**; the **retrain itself is the user's overnight action**.

---

## ✅ IMPLEMENTED (code-complete, validated: py_compile + pyflakes + node --check + import test)

### Phase 1 — tracker + UI (takes effect on next boot, no retrain needed by itself)
| File | Change |
|---|---|
| `backend/database.py` | all `timeframes`/`for h in` lists (round creation, action-log, A/B PnL, rehydration, last-ts) → **[1,5,15]** |
| `backend/server.py` | `price_to_beat_tracker`, `price_to_beat_binance_tracker` → **(1,5,15)**; `model_verifier` → (1,5,15); `exchange_verifier` (5,15,30)→**(5,15)**; `_recent_conf` dict; two accuracy-endpoint loops → [1,5,15] |
| `src/main.js` | `ROSTER_HORIZONS`→[1,5,15]; `PTB_HORIZONS` (5,15,30)→**[5,15]**; accuracy chips, tf-tabs (×2), per-horizon grids (×2), forecast scorecard → [1,5,15]; scoreboard + replay → [5,15]. **Rebuilt** → `dist/assets/index-EgJ0QB3S.js` |

### Phase 2 — models + datasets (takes effect ON the overnight retrain)
| File | Change | Forces retrain via |
|---|---|---|
| `backend/model.py` | `MODEL_ARCH_VERSION` `7horizon`→**`3horizon-1-5-15`**; `MultiModelEnsemble` default horizons → [1,5,15] | **arch mismatch → startup train** |
| `backend/features.py` | matrix label horizons → [1,5,15] | rebuilt by `build_research_matrix` |
| `backend/keeper_head_training.py` | `HORIZONS` → (1,5,15) | keeper heads' version bakes the 240d window → retrain |
| `backend/train_signed_quantiles.py` | `HORIZONS`→[5,15]; **HEAD_VERSION bumped** | version change → retrain |
| `backend/train_magnitude_quantiles.py` | `HORIZONS` → (1,5,15) | **legacy** — needs force (see below) |
| `backend/train_beat_classifier.py` | `HORIZONS` → (1,5,15) | legacy/noise (rarely saved) |
| `backend/build_path_labels.py` | `HORIZONS` → (5,15) | legacy — needs force |
| `backend/build_fingerprints_historical.py` | `HORIZONS` → (1,5,15) | legacy — needs force |
| `backend/analytics.py`, `calibration.py`, `prediction_verifier.py`, `model_verifier.py` | all horizon lists → [1,5,15] | serving/analytics consistency (no retrain needed) |

**Validated:** `MODEL_ARCH_VERSION = ...-3horizon-1-5-15-...`; `MultiModelEnsemble().horizons = [1,5,15]`;
every head/verifier constant confirmed {1,5,15} (path & band {5,15}) via import test.

### Also done this session (separate item)
- **1m P(Hold) recalibration:** persistence head retrained on fresh 5.76M snapshots; per-horizon isotonic
  tested = **wash** (reverted serving to global iso; kept as bundle diagnostic). The fresh-data retrain is
  the real recalibration. (See [ACTION_ITEMS_AND_TIMEFRAME_PRUNE](ACTION_ITEMS_AND_TIMEFRAME_PRUNE_2026-06-21.md) #1.)

---

## ❌ NOT DONE (by design — your action, or intentionally left)

1. **The retrain itself** — this is your overnight run (below). Until then the app **will not boot into a
   serving state**: the next boot sees the arch mismatch and *starts the retrain* (this is intended).
2. **Legacy heads** (`magnitude`, `path`, `fingerprints`, `beat`) — `train_heads.py` SKIPS these if the
   .pkl is present (they have no version tag). Their HORIZONS edits are in place but **won't apply unless
   forced** → set `BTC_FORCE_HEAD_RETRAIN=1` for the overnight run (procedure below). They're non-critical
   (gated behind coin-flip direction), so if you skip the force they simply keep 3/7/10/30 internally
   (harmless; serving only asks for {1,5,15}).
3. **Research / diagnostic scripts left at 7 ON PURPOSE** — `analyze_timeframe_performance.py`,
   `analyze_timeframe_value.py`, `sign_truth_scorecard.py`, `model_bakeoff.py`, `phold_tier_scorecard.py`,
   `trading_edge_backtest.py`, `automl.py`, `composed_decision_scorecard.py`, `diagnose_model.py`,
   `anti_signal_scan.py`, `analyze_signals.py`, `fsr_ppo_strategy.py`, `shadow_live_predictor.py`,
   `research/*`. These analyze HISTORICAL data (which still contains 3/7/10/30 rounds) — pruning them would
   blind the analysis. Not in the serving/training path.
4. **Old `predictions_3m/7m/10m/30m` DuckDB tables** — left in place (historical data preserved). They just
   stop receiving new rows. No migration/drop needed.

---

## 🌙 Overnight retrain procedure (240–260 days)

The next `start.bat` IS the retrain (arch bump). To get your 240–260d window + prune the legacy heads, set
two env vars first. From a terminal in the project root:

```bat
set BTC_HISTORICAL_DAYS=250
set BTC_FORCE_HEAD_RETRAIN=1
start.bat
```

What it does, in order (leave it overnight, browser/IDE closed):
1. **Backfill 250 days** (trade-features, persistence, cross-venue) — first run downloads ~250d of spot
   aggTrades (multi-GB, slow ONCE; cached after).
2. **Rebuild the 1m research matrix** to 250d (drives every head, now at {1,5,15}).
3. **Train all heads** (`--force`): selectivity, signed_quantile [5,15], persistence [1,5,15], the 4 keepers
   [1,5,15], + legacy magnitude/path/fingerprints/beat at the new horizons.
4. **Retrain the main ensemble** at {1,5,15} on 250d — the ~6h job (now ~3/7 the horizons → faster + lower
   RAM than the old 7-horizon run). Triggered automatically by the arch mismatch (runs even with FREEZE=1,
   because no compatible model exists to load).
5. Boots serving on {1,5,15}; saves the new bundle → subsequent restarts LOAD it (no retrain).

**Notes / cautions:**
- You do **not** need to set `BTC_FREEZE_MODEL=0` — the startup train fires on arch mismatch regardless.
  Leave FREEZE=1 so it doesn't *also* schedule future auto-retrains.
- 250d × the sequence tensors is heavier than 150d on 16GB, but the horizon prune (7→3) offsets a lot of
  it. If RAM is tight, drop to `BTC_HISTORICAL_DAYS=240`.
- Don't interrupt the 6h ensemble train. The dashboard is usable during it (non-blocking boot), but the
  feed will be CPU-starved while it runs — expect lag until it finishes.

---

## ✅ After the retrain — verify
- Boot log shows `MODEL_ARCH_VERSION ...3horizon-1-5-15...` and **"Model is FROZEN" + loads** on the
  *second* restart (proves the new bundle saved).
- UI shows only **1m / 5m / 15m** tabs; price-to-beat cards only 5m/15m.
- `python backend/research/standalone/analyze_timeframe_value.py --source pyth` → only {1,5,15} have new rows.
- `python backend/calibration_monitor.py` → re-check 1m drift on the fresher model (the recalibration test).

---

## Expected payoff
- **~57% fewer per-horizon head trainings + matrix labels**; smaller sequence tensors → faster, lighter
  retrain on 16GB.
- **Cleaner tool:** 3 timeframe tabs instead of 7; betting cards only on real markets.
- **No accuracy lost:** the removed horizons were coin-flip with no market (measured —
  [TIMEFRAME_VALUE_pyth](TIMEFRAME_VALUE_pyth_2026-06-21.md)).
