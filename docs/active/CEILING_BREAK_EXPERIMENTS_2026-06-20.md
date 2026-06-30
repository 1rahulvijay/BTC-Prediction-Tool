# Ceiling-Break Experiments — 2026-06-20

Last **30 days** (43,200 1m rows) · **70/30** temporal split (embargo 30 bars) · spread 2.0 bps. Honest scope: Exp 1 & 5 run on real data; Exp 2 is an order-flow PROXY (true L2 needs record-forward); Exp 3 & 4 need recorder Polymarket history (skipped until rounds settle).

## Experiment 1 — Triple-barrier model (RUNNABLE)
Models: logistic, extratrees, lightgbm, xgboost, catboost · barriers {5: 10.0, 15: 20.0} bps · spread 2.0 bps · 70/30 temporal split (embargo 30 bars).

### 5m barrier ±$65 (~10 bps) — label dist: upper 24.6% / lower 26.5% / neutral 48.9%

**UPPER-first** (test n=12,959, base 26.6%):
| model | AUC | top1% prec | top5% prec | top10% prec | top5% profit (bps, after spread) |
|---|---:|---:|---:|---:|---:|
| logistic | 0.679 | 51.2% | 48.8% | 46.9% | -1.8 |
| extratrees | 0.681 | 54.3% | 49.9% | 47.6% | -1.6 |
| lightgbm | 0.673 | 51.9% | 47.6% | 46.5% | -1.5 |
| xgboost | 0.673 | 46.5% | 47.0% | 46.4% | -1.6 |
| catboost | 0.681 | 45.0% | 49.8% | 47.6% | -1.4 |

**LOWER-first** (test n=12,959, base 26.2%):
| model | AUC | top1% prec | top5% prec | top10% prec | top5% profit (bps, after spread) |
|---|---:|---:|---:|---:|---:|
| logistic | 0.68 | 47.3% | 47.8% | 46.7% | -1.8 |
| extratrees | 0.689 | 42.6% | 46.7% | 47.5% | -2.1 |
| lightgbm | 0.678 | 51.2% | 48.7% | 46.5% | -1.1 |
| xgboost | 0.678 | 50.4% | 47.6% | 46.0% | -1.3 |
| catboost | 0.691 | 55.8% | 51.0% | 47.8% | -0.9 |

### 15m barrier ±$129 (~20 bps) — label dist: upper 22.1% / lower 25.1% / neutral 52.7%

**UPPER-first** (test n=12,956, base 23.4%):
| model | AUC | top1% prec | top5% prec | top10% prec | top5% profit (bps, after spread) |
|---|---:|---:|---:|---:|---:|
| logistic | 0.681 | 50.4% | 50.7% | 47.4% | -0.9 |
| extratrees | 0.679 | 50.4% | 50.4% | 45.9% | -0.9 |
| lightgbm | 0.645 | 45.7% | 39.6% | 37.1% | -2.1 |
| xgboost | 0.65 | 41.1% | 41.4% | 37.3% | -1.5 |
| catboost | 0.665 | 49.6% | 45.6% | 42.2% | -1.4 |

**LOWER-first** (test n=12,956, base 23.8%):
| model | AUC | top1% prec | top5% prec | top10% prec | top5% profit (bps, after spread) |
|---|---:|---:|---:|---:|---:|
| logistic | 0.665 | 43.4% | 42.8% | 43.8% | -3.0 |
| extratrees | 0.675 | 43.4% | 47.9% | 42.4% | -1.4 |
| lightgbm | 0.65 | 31.0% | 38.8% | 40.7% | -2.5 |
| xgboost | 0.648 | 34.9% | 38.8% | 38.9% | -2.1 |
| catboost | 0.669 | 35.7% | 41.7% | 42.2% | -2.2 |

## Experiment 2 — New-information value-add (PROXY — not true L2)
> True L2 (microprice / OFI / depth / book pull-refill) has **no free historical depth** — it must be **record-forward**. Here we test the order-flow + cross-venue columns that DO exist (cvd/vpin/large-trade/basis/perp) as a stand-in: does adding them lift the TOP buckets over candle-only? Target = 5m UPPER-first barrier (the Exp-1 label).

| feature set | n_feats | AUC | top1% prec | top5% prec | top5% profit (bps) |
|---|---:|---:|---:|---:|---:|
| candle-only | 12 | 0.675 | 51.9% | 46.8% | -1.8 |
| candle + flow/cross-venue | 28 | 0.673 | 51.9% | 47.6% | -1.5 |

_Read: improvement should show in the TOP buckets, not average AUC. True L2 still needs the record-forward clock (Exp-2 proper)._

## Experiment 3 — Market-lag (ask underreaction) & Experiment 4 — Maker-fill (SKIPPED)

Both need the **Polymarket 5m/15m ask history** from the recorder. Updated 2026-06-21: 364 official outcomes, only 4 joined quote rounds.
- Exp 3 target: `market_ask underreacts to fair_value movement` (fair/ask change 1s/3s, book age, spread, depth, seconds_left, distance, line-cross risk → buy only if `fair − ask − buffer > required_edge` and edge persists).
- Exp 4: simulate maker bids below the ask; measure fill rate, toxic-fill rate, PnL after fill.
**Cannot run on historical data** — Polymarket short-term depth is not archived (project stop-list). Leave the recorder running; re-run once ≥30 rounds settle, then `analyze_recorder_edge.py` / a market-lag fit becomes possible.

## Experiment 5 — Meta-skip model (champion decision quality)

Target = 'the acted side held to resolution'. n=41,360 resolved · test base hold **74.7%** · 70/30 temporal. Lift over base = the value of a skip filter.

| model | test AUC | top10% kept → hold | top25% kept → hold | base hold |
|---|---:|---:|---:|---:|
| logistic | 0.721 | 95.3% | 94.1% | 74.7% |
| histgb | 0.717 | 95.6% | 94.2% | 74.7% |
| catboost | 0.743 | 97.6% | 95.3% | 74.7% |

_Read: if 'top-kept hold' clearly beats base hold, the meta-skip adds real decision value (act only on the kept fraction). Spread / bucket-quality features need the recorder + bucket join — not in this snapshot set yet._

---

## Verdict & honest interpretation
**No runnable experiment produced a cost-clearing edge — exactly what the ceiling thesis predicts.**

**Exp 1 — Triple-barrier: better label, still not tradeable.** AUC 0.65–0.69 *looks* far above coin-flip,
but that is mostly the model detecting **volatility** (when *some* barrier gets hit) — not **which** barrier.
The directional test is the top-bucket precision: ~45–55%, and **profit-after-spread is NEGATIVE at every
horizon and side (−0.9 to −3.0 bps)** after only a 2 bps cost. So a triple-barrier *trade trigger* loses
money. Confirms: stop fighting direction; do **not** promote a barrier head as a trigger. (It may still be
useful as a *risk* read, like big-drop — never an automatic entry.)

**Exp 2 — Flow/cross-venue proxy: no top-bucket lift.** Adding the available order-flow + cross-venue
columns moved 5m UPPER AUC 0.675 → 0.673 and top-5% precision 46.8% → 47.6% — noise. The order-flow we
*already have* doesn't help. This is the justification to start the **true L2 record-forward clock**
(microprice/OFI/depth, which we cannot test historically) rather than expecting existing columns to deliver.

**Exp 3 & 4 — blocked on the make-or-break.** Settlement is fixed, but only 4 joined quote rounds → the two experiments that could
actually show profit (ask-underreaction, maker-fill) cannot run. The edge, if it exists, lives **here**, not
in better BTC labels. Keep the recorder up.

**Exp 5 — Meta-skip: strong ranking, but largely re-expresses P(Hold) — not proven new edge.** Top-decile
hold 97.6% vs 74.7% base is a big separation, and confirms the champion can rank setups by hold-probability.
**Caveat (important):** the features include `p_hold` and `champion_confidence`, so the model is substantially
*recovering calibration we already have* — it has not been shown to add information **beyond p_hold**, and
"side holds" ≠ "profitable after the Polymarket ask." Before treating this as edge it needs (a) an ablation
**with p_hold removed** to isolate marginal value, and (b) the recorder's ask history to test profit, not hold.

### Bottom line
The two BTC-label levers (triple-barrier, flow proxy) **do not clear costs** — more evidence the ceiling is
real. The decision-quality signal (Exp 5) is genuine but mostly the P(Hold) calibration we already trust. The
only untested levers that could break the ceiling are **(1) true L2 record-forward data** and **(2) Polymarket
ask mispricing (Exp 3/4)** — both require *recording forward*, neither can be faked from history. This is the
strategy's core claim, now measured: **you don't beat this with more models on the same data.**
