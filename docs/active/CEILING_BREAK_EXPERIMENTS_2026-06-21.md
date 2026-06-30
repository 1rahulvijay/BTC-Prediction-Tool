# Ceiling-Break Experiments — 2026-06-21

Last **30 days** (43,200 1m rows) · **70/30** temporal split (embargo 30 bars) · spread 2.0 bps. Honest scope: Exp 1 & 5 run on real data; Exp 2 is an order-flow PROXY (true L2 needs record-forward); Exp 3 & 4 need recorder Polymarket history (skipped until rounds settle).

## Experiment 1 — Triple-barrier model (RUNNABLE)
Models: logistic, extratrees, lightgbm, xgboost, catboost · barriers {5: 10.0, 15: 20.0} bps · spread 2.0 bps · 70/30 temporal split (embargo 30 bars).

### 5m barrier ±$64 (~10 bps) — label dist: upper 25.1% / lower 27.0% / neutral 47.9%

**UPPER-first** (test n=12,959, base 23.6%):
| model | AUC | top1% prec | top5% prec | top10% prec | top5% profit (bps, after spread) |
|---|---:|---:|---:|---:|---:|
| logistic | 0.662 | 51.2% | 47.4% | 45.6% | -1.9 |
| extratrees | 0.666 | 51.9% | 48.4% | 46.3% | -1.6 |
| lightgbm | 0.657 | 46.5% | 48.1% | 46.0% | -1.3 |
| xgboost | 0.654 | 45.7% | 46.8% | 44.1% | -1.3 |
| catboost | 0.666 | 48.1% | 50.7% | 47.2% | -0.9 |

**LOWER-first** (test n=12,959, base 23.3%):
| model | AUC | top1% prec | top5% prec | top10% prec | top5% profit (bps, after spread) |
|---|---:|---:|---:|---:|---:|
| logistic | 0.673 | 49.6% | 46.4% | 44.2% | -1.9 |
| extratrees | 0.683 | 47.3% | 46.8% | 45.1% | -1.8 |
| lightgbm | 0.674 | 48.1% | 50.7% | 45.6% | -0.6 |
| xgboost | 0.678 | 47.3% | 49.3% | 45.2% | -0.7 |
| catboost | 0.686 | 52.7% | 48.2% | 46.4% | -1.3 |

### 15m barrier ±$127 (~20 bps) — label dist: upper 22.7% / lower 25.6% / neutral 51.7%

**UPPER-first** (test n=12,956, base 21.2%):
| model | AUC | top1% prec | top5% prec | top10% prec | top5% profit (bps, after spread) |
|---|---:|---:|---:|---:|---:|
| logistic | 0.669 | 47.3% | 49.0% | 44.5% | -0.5 |
| extratrees | 0.667 | 48.1% | 49.5% | 41.6% | -0.5 |
| lightgbm | 0.65 | 48.8% | 44.7% | 39.9% | -0.3 |
| xgboost | 0.647 | 42.6% | 42.8% | 37.0% | -0.6 |
| catboost | 0.663 | 44.2% | 44.4% | 40.9% | -1.1 |

**LOWER-first** (test n=12,956, base 20.7%):
| model | AUC | top1% prec | top5% prec | top10% prec | top5% profit (bps, after spread) |
|---|---:|---:|---:|---:|---:|
| logistic | 0.655 | 40.3% | 40.6% | 40.2% | -2.7 |
| extratrees | 0.665 | 45.0% | 43.9% | 41.0% | -2.2 |
| lightgbm | 0.639 | 27.9% | 38.5% | 38.9% | -1.1 |
| xgboost | 0.651 | 24.8% | 35.2% | 37.2% | -2.3 |
| catboost | 0.659 | 25.6% | 39.6% | 40.2% | -0.9 |

## Experiment 2 — New-information value-add (PROXY — not true L2)
> True L2 (microprice / OFI / depth / book pull-refill) has **no free historical depth** — it must be **record-forward**. Here we test the order-flow + cross-venue columns that DO exist (cvd/vpin/large-trade/basis/perp) as a stand-in: does adding them lift the TOP buckets over candle-only? Target = 5m UPPER-first barrier (the Exp-1 label).

| feature set | n_feats | AUC | top1% prec | top5% prec | top5% profit (bps) |
|---|---:|---:|---:|---:|---:|
| candle-only | 12 | 0.66 | 48.1% | 46.7% | -1.5 |
| candle + flow/cross-venue | 28 | 0.657 | 46.5% | 48.1% | -1.3 |

_Read: improvement should show in the TOP buckets, not average AUC. True L2 still needs the record-forward clock (Exp-2 proper)._

## Experiment 3 — Market-lag (ask underreaction) & Experiment 4 — Maker-fill (SKIPPED)

Both need the **Polymarket 5m/15m ask history** from the recorder. Official outcomes: **364**; joined quote rounds: **4**, still insufficient.
- Exp 3 target: `market_ask underreacts to fair_value movement` (fair/ask change 1s/3s, book age, spread, depth, seconds_left, distance, line-cross risk → buy only if `fair − ask − buffer > required_edge` and edge persists).
- Exp 4: simulate maker bids below the ask; measure fill rate, toxic-fill rate, PnL after fill.
**Cannot run on historical data** — Polymarket short-term depth is not archived (project stop-list). Leave the recorder running; re-run once ≥30 rounds settle, then `analyze_recorder_edge.py` / a market-lag fit becomes possible.

## Experiment 5 — Meta-skip model (champion decision quality)

_Could not read champion_snapshots ⋈ price_to_beat: IO Error: Cannot open file "c:\users\rahul\documents\btc-prediction-tool\data\an_
