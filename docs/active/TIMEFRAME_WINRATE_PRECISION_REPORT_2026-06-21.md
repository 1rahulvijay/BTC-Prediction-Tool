# Timeframe · Win-Rate · Prediction-Precision Report (2026-06-21)

**Scope:** one consolidated, evidence-backed report of **per-timeframe results, win rate, and prediction
precision**, plus the **which-timeframe-to-eliminate** verdict and the **ceiling-break** conclusion.
All numbers are measured this session and cited to their source script/doc — nothing assumed.

**Sources:**
[TIMEFRAME_PERFORMANCE_pyth_2026-06-21.md](TIMEFRAME_PERFORMANCE_pyth_2026-06-21.md) (win rates) ·
[CEILING_BREAK_EXPERIMENTS_2026-06-21.md](CEILING_BREAK_EXPERIMENTS_2026-06-21.md) (precision) ·
[analyze_timeframe_value.py](../../backend/research/standalone/analyze_timeframe_value.py) (elimination logic) ·
[analyze_timeframe_performance.py](../../backend/research/standalone/analyze_timeframe_performance.py).

---

## 0. Executive summary

- **Directional win rate is a coin-flip at every timeframe** (~50% ±2, Wilson-LB straddles 50). 1m, 3m,
  5m, 7m, 10m, 15m, 30m — none has a statistically real directional edge.
- **Prediction precision splits in two:** *direction* precision = coin-flip (AUC ~0.50); *timing* ("will
  it move enough?") precision is **real** (AUC **0.65–0.69**), but the directional barrier bet it implies
  **loses after a 2 bps cost** (top-5% profit −0.3 to −2.7 bps). So precision exists, but not a tradeable
  directional one.
- **Eliminate 3m / 7m / 10m / 30m** (no Polymarket market, coin-flip direction, pure compute). **Keep
  5m + 15m** (tradeable markets); **1m optional** (fastest feedback + densest P(hold)). → ~57% less
  per-horizon training, **zero accuracy lost**.
- **Only standout cell:** **15m in the 20:00–24:00 CEST block = 61.8%** (Wilson-LB 51.4, the only cell
  clearing 50) and >50% on 7/8 days. **Watch, not yet tradeable** (tiny n + selection bias).
- **Ceiling-break verdict:** no historical lever breaks it. The two remaining levers are forward-only —
  L2 microstructure (recording now) and Polymarket mispricing (364 official outcomes, only 4 quote rounds).

---

## 1. Win rate by timeframe (the elimination view)

### 1a. Per-horizon, ALL HISTORY (9.6 days — authoritative, run with backend stopped)
Source: `analyze_timeframe_value.py --source pyth` → [TIMEFRAME_VALUE_pyth_2026-06-21.md](TIMEFRAME_VALUE_pyth_2026-06-21.md).

| horizon | n | rounds/day | win % | Wilson-LB | model n | model win % | model LB | tradeable? |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| 1m  | 6729 | 702.4 | 50.1 | 48.9 | 277 | 48.0 | 42.2 | no |
| 3m  | 2224 | 232.1 | 49.6 | 47.6 | 784 | 49.1 | 45.6 | no |
| 5m  | 1454 | 151.8 | 50.1 | 47.5 | 711 | 50.6 | 47.0 | **YES** |
| 7m  | 902  | 94.1  | 50.2 | 47.0 | 412 | 51.5 | 46.6 | no |
| 10m | 620  | 64.7  | 49.7 | 45.8 | 312 | 49.7 | 44.2 | no |
| 15m | 470  | 49.1  | 50.6 | 46.1 | 338 | 50.3 | 45.0 | **YES** |
| 30m | 159  | 16.6  | 47.8 | 40.2 | 146 | 47.9 | 40.0 | no |

**Every horizon is a coin-flip with real sample sizes now** — overall win% 47.8–50.6, and crucially the
**committed MODEL-lean Wilson-LB is below 50 at every horizon** (42.2–47.0). No timeframe — tradeable or
not, model or fallback — beats chance. `rounds/day` shows 1m's value: 702 rounds/day (densest feedback
+ P(hold) snapshots), vs 17/day at 30m.

_(The earlier last-24h table showed 15m 56% / 30m 34% — both were small-n flukes; this 9.6-day table is
the real picture.)_

---

## 2. Prediction precision (from the ceiling-break experiments, 30 days, 70/30 temporal split)

"Precision" measured two honest ways. **Direction** = which side wins. **Timing/barrier** = will price
hit ±T bps within the horizon (the vol-clustering question).

### 2a. Direction precision — coin-flip
Across all 14-model / 145-feature / multi-horizon sweeps this project: **AUC 0.50–0.54**, win-rate ~50%.
No model family, feature set, or timeframe lifts it. (Reconfirmed §1 above.)

### 2b. Timing/barrier precision — REAL, but not tradeable as a directional bet
Triple-barrier model, 5 families (logistic/extratrees/lightgbm/xgboost/catboost):

| target | horizon | AUC | top-1% prec | top-5% prec | top-10% prec | top-5% profit (bps, after 2bps spread) |
|---|---|---:|---:|---:|---:|---:|
| UPPER-first | 5m | 0.654–0.666 | 45.7–51.9% | 46.8–50.7% | 44.1–47.2% | **−0.9 to −1.9** |
| LOWER-first | 5m | 0.673–0.686 | 47.3–52.7% | 46.4–50.7% | 44.2–46.4% | **−0.6 to −1.9** |
| UPPER-first | 15m | 0.647–0.669 | 42.6–48.8% | 42.8–49.5% | 37.0–44.5% | **−0.3 to −1.1** |
| LOWER-first | 15m | 0.639–0.665 | 24.8–45.0% | 35.2–43.9% | 37.2–41.0% | **−0.9 to −2.7** |

**Read:** the model has genuine precision on *whether* a move happens (AUC ~0.66) — but turning that into
a directional barrier trade is **net-negative at every cell after only a 2 bps cost**. Knowing *when* it
moves without the *side* is not tradeable on BTC spot. (NB: this is the BTC-spot cost framing; the
*Polymarket* profitability question is Exp 3/4, still gated on settled rounds.)

### 2c. Does new information add precision? (Exp 2 proxy)
| feature set | n_feats | AUC | top-5% prec | top-5% profit (bps) |
|---|---:|---:|---:|---:|
| candle-only | 12 | 0.660 | 46.7% | −1.5 |
| candle + flow/cross-venue | 28 | 0.657 | 48.1% | −1.3 |

Adding order-flow + cross-venue (cvd/vpin/basis/perp) **does not lift precision**. True L2
(microprice/OFI/depth) has no free history → must be record-forward (the microstructure recorder).

---

## 3. Win rate by time-of-day (CEST) — the one cell worth watching

### 3a. 4-hour blocks, 15m only
| block (CEST) | n | win % | Wilson-LB | model win % |
|---|---:|---:|---:|---:|
| 00:00–04:00 | 64 | 54.7 | 42.6 | 60.5 |
| 04:00–08:00 | 65 | 50.8 | 38.9 | 57.9 |
| 08:00–12:00 | 71 | 42.3 | 31.5 | 38.2 |
| 12:00–16:00 | 81 | 46.9 | 36.4 | 50.8 |
| 16:00–20:00 | 97 | 47.4 | 37.8 | 42.7 |
| **20:00–24:00** | **89** | **61.8** | **51.4 ✅** | **59.1** |

The evening **20:00–24:00 CEST on 15m** is the **only cell in the whole grid whose Wilson-LB clears 50%.**
Worst is the morning 08:00–12:00 (42%).

### 3b. Repeatability of the evening-15m cell
| day | block n | block win % | rest-of-day % | vs rest |
|---|---:|---:|---:|---|
| 06-12 | 13 | 61.5 | 41.7 | ✓ |
| 06-13 | 10 | 30.0 | 38.5 | ✗ |
| 06-14 | 15 | 73.3 | 53.5 | ✓ |
| 06-15 | 10 | 60.0 | 45.8 | ✓ |
| 06-16 | 10 | 60.0 | 38.3 | ✓ |
| 06-18 | 9  | 55.6 | – | – |
| 06-19 | 6  | 83.3 | 51.4 | ✓ |
| 06-20 | 16 | 68.8 | 65.4 | ✓ |

**>50% on 7/8 days; beat rest-of-day on 6/8.** A genuine recurring tendency — **but WATCH, not tradeable:**
6–16 rounds/day (very noisy) + selection bias (the block was *chosen* for looking best across 18 cells).
Decisive test = does it hold on *future* days it wasn't selected from.

### 3c. Per-day / day-of-week
Flat: every calendar day 47.5–51.5%, every weekday 47.5–50.4%, all Wilson-LBs < 50. No standout day or weekday.

---

## 4. Recent live sample (last 100 rounds, Pyth anchor)
- **5m:** 47 WON / 53 LOST (**47%**) — almost all fallback leans.
- **15m:** 60 WON / 40 LOST (**60%**) — mostly fallback + a favorable recent streak; n=100, don't bank on it.
- Full rows: `data/last_rounds_pyth_5m.csv`, `data/last_rounds_pyth_15m.csv`.

---

## 5. Timeframe elimination — verdict

Logic ([analyze_timeframe_value.py](../../backend/research/standalone/analyze_timeframe_value.py)): a horizon earns its keep
only via **(1)** a tradeable Polymarket market, **(2)** a real directional edge (model Wilson-LB > 50%,
n ≥ 200 — empty, per §1/§2), or **(3)** fastest-feedback density. Else REMOVE.

| horizon | verdict | reason |
|---|---|---|
| 1m  | **OPTIONAL** | no market + coin-flip; keep only for fastest feedback + densest P(hold) snapshots |
| 3m  | **REMOVE** | no market + coin-flip → pure training/label cost |
| 5m  | **KEEP** | tradeable Polymarket market (value = P(hold) + band, not direction) |
| 7m  | **REMOVE** | no market + coin-flip → pure cost |
| 10m | **REMOVE** | no market + coin-flip → pure cost |
| 15m | **KEEP** | tradeable Polymarket market (+ the evening cell to watch) |
| 30m | **REMOVE** | no market + worst accuracy → pure cost |

- **Recommended keep-set: {1m (optional), 5m, 15m}.** Removing 3m/7m/10m/30m drops **4 of 7 horizons
  (~57%)** of per-horizon head training + matrix labeling, with **no accuracy lost** (no market, no edge).
- **Why not multi-timeframe direction-stacking:** direction is a coin-flip at *every* horizon, so a
  non-tradeable horizon cannot inform a tradeable one (stacking coin-flips ≠ signal). Fine-scale value
  lives in **P(hold) late-entry + L2 microstructure**, not small-TF direction models.

---

## 6. Ceiling-break conclusion & next steps

- **Historical levers exhausted:** triple-barrier timing is precise (AUC ~0.66) but not a tradeable
  direction (negative after 2 bps); flow/cross-venue adds nothing; direction is coin-flip across every
  timeframe, hour, block, day, weekday.
- **Two forward-only levers remain:**
  1. **L2 microstructure** (microprice/OFI/depth/cross-venue lead-lag) — recording now; retrain on it in
     2–4 weeks and test whether it lifts the top buckets over candle-only.
  2. **Polymarket mispricing** (`P(Hold) − ask − buffer`) — settlement is fixed, but only **4 joined quote
     rounds** exist; this is the only path to *profit* (vs. a nicer probability). Gated on quote accrual.
- **The evening-15m cell** is not a ceiling-break — it's a **selectivity filter**: if it survives
  out-of-sample, use it to *abstain* (only consider 15m bets 20:00–24:00 CEST), then let the Polymarket
  mispricing edge decide whether to actually bet. Window + P(hold) + a mispriced ask = a real trade;
  window + coin-flip direction = not.

**Next:** (1) keep all recorders running; (2) accrue joined Polymarket quote+outcome rounds so the edge proof can
populate; (3) prune to {1m, 5m, 15m} when ready; (4) re-run the live per-horizon elimination table (needs
a brief app stop) for exact all-history n.

---

*Note on freshness: §1 is now the exact all-history per-horizon table (9.6 days, run 2026-06-21 with the
backend stopped). §2 precision is the 30-day ceiling-break; §3 time-of-day is all-history. The Binance
anchor has ~no data yet (persistence just enabled — n=1–50, span 0.0d; re-run `--source binance` after
it accrues). Verdict unchanged: KEEP {5m,15m}, OPTIONAL {1m}, REMOVE {3m,7m,10m,30m}.*
