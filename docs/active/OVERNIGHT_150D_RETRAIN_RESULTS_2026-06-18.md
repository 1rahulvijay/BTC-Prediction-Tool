# Overnight 150-Day Full Retrain — Results & Validation (2026-06-18)

Operator-run overnight retrain on **150 days** of data via `research\launchers\train_180d_all_models_overnight.bat`
(the bat is named "180d" but was configured for `BTC_HISTORICAL_DAYS=150`). This is the first run
that exercises the **single-knob days pipeline** + the **98/2 train/test split** end-to-end, *and*
retrains the full main ensemble (`BTC_FORCE_MAIN_RETRAIN=1`).

**One-line conclusion:** the 150-day evidence reconfirms the architecture — **P(hold), big-drop, and
activity are real and generalize to the unseen 2% tail; raw direction is not tradeable at any horizon**
(the main-ensemble directional backtest is net-negative everywhere). Keep direction in
confirmation/abstention mode; put tradeable weight on the specialist heads + the Polymarket edge gate.

---

## 1. Run metadata
| Field | Value |
|---|---|
| Window | **150 days** (2026-01-18 .. 2026-06-17), 216,000 1m bars |
| Matrix | rebuilt automatically (`days_match=False, coverage_ok=False` → rebuild); `manifest.json` coverage check |
| Main ensemble | arch `…v11-pruned69…7horizon…split98…`, 211,534 train (98%) / 4,318 holdout (2%) |
| Main ensemble cost | 252 components planned, **231 completed / 203 saved**, **~10.3 h** (36,934 s) |
| Specialist heads | retrained once each (versions carry the `…-split-150d` tag) |
| Backtest | out-of-sample only — 11,970 held-out candles, embargo 30 |

**Pipeline mechanisms confirmed working in the real run:**
- **Single days knob → matrix → every head.** Setting the window rebuilt `research_matrix_1m.parquet`
  to 150 d and every specialist head retrained on it (version tag flips with the window).
- **98/2 split.** Every keeper head reports a held-out `test_AUC … (n=4,320)`; 4,320 = exactly 2% of
  216,000. The most-recent 2% was never seen in fit / isotonic calibration / tier construction.

---

## 2. Specialist heads — train OOF → **held-out 2% test** (the honest generalization check)

### Keepers that generalize (the real edge)
| Head | Train OOF AUC | **Held-out test AUC** | Held-out top-5% | Read |
|---|---|---|---|---|
| **P(hold)** keeper | 0.746 → **0.762** (+keepers) | test AUC **0.746**, T3-late **0.828** | P≥0.95 → **97.2%** realized | workhorse, calibration excellent |
| **activity/range** | 0.86–0.91 | **0.78–0.86** | ~99–100% | strong, generalizes |
| **big-drop** | ~0.67 | **0.643–0.665** | 56–73% | real downside signal |
| **big-move** | 0.66–0.68 | 0.575–0.647 | 64–83% | modest gate, usable |

P(hold) reliability (calibrated → realized, 846,890 held-out snapshots): P≥0.85→92.1%, P≥0.90→94.6%,
P≥0.93→96.3%, P≥0.95→**97.2%**. Per-horizon @P≥0.93: 1m 95.5% … 15m 96.9% … 30m 95.9%. signed_quantile
band coverage = **80.0%** (CQR) exact across h=3,5,7,10,15,30.

### The head that does NOT generalize (confirmation-only by design)
| Head | Train OOF AUC | **Held-out test AUC** | Held-out top-5% |
|---|---|---|---|
| directional big_up | 0.58–0.61 | **0.55–0.59** | 33–47% |
| directional big_down | 0.58–0.60 | **0.55–0.59** | **20.8–36.6%** |

The 98/2 split did its job: it separated the heads that hold up on unseen data from the directional
head, which collapses to near-noise (30m big_down top-5% = 20.8%). Directional stays **confirmation
only** — it explains conflicts, never triggers a trade.

### Legacy heads
- **`beat` (raw direction): 0/7 horizons cleared the noise gate (AUC 0.50–0.52) → NOT saved.** Correct.
- `magnitude`: 7/7 SIGNAL (conditional P50 beats flat baseline). `path`: 6/6 SIGNAL. `fingerprints`: 301
  cells, strongest ~57%.

---

## 3. Main-ensemble directional backtest (out-of-sample, held-out tail)

| Horizon | n | Dir. acc | Trades | Profit factor | Sharpe | Avg ret | NEUTRAL-pred share |
|---|---|---|---|---|---|---|---|
| 1m | 2,398 | — | **1** | — | — | −0.16% | **100%** |
| 3m | 2,396 | 27.2% | 669 | 0.32 | −0.41 | −0.08% | 72% |
| 5m | 2,394 | 29.7% | 1,018 | 0.43 | −0.30 | −0.06% | 58% |
| 7m | 2,392 | 32.7% | 965 | 0.49 | −0.25 | −0.06% | 60% |
| 10m | 2,389 | 28.2% | 1,146 | 0.34 | −0.38 | −0.10% | 52% |
| 15m | 2,384 | 32.4% | 2,148 | 0.45 | −0.28 | −0.08% | 10% |
| 30m | 2,369 | 37.0% | 2,327 | 0.43 | −0.28 | −0.11% | 2% |

**Every horizon 3m–30m: profit factor < 1, negative Sharpe, negative average return.** Directional
trading loses. At 1m the model abstains (100% NEUTRAL) — its 84.6% "accuracy" is just the NEUTRAL base
rate, not edge.

### Confusion matrices (pred ↓ / actual →)
```
h=1m            DOWN  NEUTRAL    UP        h=3m            DOWN  NEUTRAL    UP
  pred DOWN        0       0      0          pred DOWN       92     156     75
  pred NEUTRAL   190    2030    177          pred NEUTRAL   250    1239    238
  pred UP          1       0      0          pred UP         97     159     90

h=5m            DOWN  NEUTRAL    UP        h=7m            DOWN  NEUTRAL    UP
  pred DOWN      248     411    205          pred DOWN      117     118     60
  pred NEUTRAL   265     875    236          pred NEUTRAL   311     787    329
  pred UP         42      58     54          pred UP        207     264    199

h=10m           DOWN  NEUTRAL    UP        h=15m           DOWN  NEUTRAL    UP
  pred DOWN      102     128    107          pred DOWN      298     301    273
  pred NEUTRAL   332     568    343          pred NEUTRAL   100      87     49
  pred UP        302     286    221          pred UP        441     438    397

h=30m           DOWN  NEUTRAL    UP
  pred DOWN      280     205    243
  pred NEUTRAL    31      10      1
  pred UP        641     378    580
```
The long-horizon rows are the clearest tell: at **30m the model predicts UP 1,599×, but the actual
close is DOWN (641) more often than UP (580)** — direction is not just coin-flip, it's *anti-predictive*
noise once the neutral band shrinks. As the horizon grows the dead-band (NEUTRAL) collapses (72% of preds
at 3m → 2% at 30m), the model is forced into more directional bets, and it bleeds more.

---

## 4. What the backtest measures — and what it does NOT
- **Measures:** the main ensemble as a *naive directional trader* — bet every UP/DOWN signal, no
  conviction gate, no abstention, no costs beyond the realized move.
- **Does NOT measure:** P(hold), big-drop, activity, the conviction/selectivity gate, the champion's
  WAIT/AVOID logic, or the Polymarket `fair − ask − buffer` edge gate.

So a net-negative directional backtest is **expected and consistent** — it stress-tests the weakest
component (raw direction) in its worst mode (trade everything). It is the *floor*, not the system. The
parts validated as real (§2) are not in this number.

---

## 5. Decisions reconfirmed at 150 days
1. **More data did not create a direction edge** — 150 d behaves like 60 d, like n=5,829 live, like the
   SOTA sequence models. Direction is a coin-flip (or worse at long horizons). Stop trying to trade it.
2. **The main ensemble's profitable mode is abstention** — it is good at knowing when *not* to trade
   (1m: 100% NEUTRAL). Keep it as a confirmation/abstention layer, not a trade trigger.
3. **The tradeable weight belongs on:** P(hold) (97% @ ≥0.95), big-drop, activity, the quantile band,
   and the **champion + Polymarket edge gate** — exactly the architecture already built.
4. **The make-or-break is unchanged:** `fair_value − ask − buffer` on recorded Polymarket rounds. None
   of this run changes that gate; it sharpens the inputs to it.

---

## 6. Operational notes
- The ~10.3 h main retrain (`BTC_FREEZE_MODEL=0`) saturated CPU and degraded the live feeds throughout
  (`WebSocket disconnected` / poll timeouts). If the main ensemble doesn't need retraining, keep
  `BTC_FREEZE_MODEL=1` — the specialist heads (the part that actually improved) train in minutes.
- One 404 (`aggTrades-2026-06-17` perp) — that day's futures dump wasn't published yet; handled
  gracefully (skipped). Harmless.
- To train 180/360 d instead, set `BTC_HISTORICAL_DAYS` accordingly; the matrix + every head retrain on
  that window automatically (version tag flips, manifest forces the rebuild).

Source artifacts: `data/saved_models/backtest_cache.json` (saved 2026-06-18 21:09, `historical_days=150`),
the keeper `.pkl` bundles (each carries `test_auc`/`n_test`/`split_frac`), and the overnight console log.
