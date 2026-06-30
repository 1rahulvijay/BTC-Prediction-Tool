# Head Probability Buckets — 2026-06-18

Leak-free OOF (TimeSeriesSplit-5) bucket quality for each specialist head. The champion validator should trust a head's probability region only where its bucket event-rate + calibration here are good. Raw AUC alone is insufficient.

## bigmove (5m abs close move >= $30)
n=120,000 · base rate **59.2%** · OOF AUC **0.664** (saved 0.6658357332000814) · **ECE 0.0** (isotonic-calibrated; raw 0.0846) · monotonic deciles: **YES**

| bucket | n | event rate | lift | avg favorable | avg adverse |
|---|---:|---:|---:|---:|---:|
| top 1% | 1,200 | 86.5% | 1.46× | 162.22 | None |
| top 5% | 6,000 | 82.4% | 1.39× | 123.77 | None |
| top 10% | 12,000 | 81.1% | 1.37× | 111.9 | None |
| top 20% | 24,000 | 77.7% | 1.31× | 97.95 | None |

Calibration (decile mean-pred → realized): d1:0.33→0.33 · d2:0.46→0.46 · d3:0.50→0.50 · d4:0.55→0.55 · d5:0.59→0.59 · d6:0.63→0.63 · d7:0.66→0.66 · d8:0.70→0.70 · d9:0.75→0.75 · d10:0.81→0.81

## bigdrop (5m low <= -$30)
n=120,000 · base rate **52.9%** · OOF AUC **0.671** (saved 0.671047902367941) · **ECE 0.0** (isotonic-calibrated; raw 0.0402) · monotonic deciles: **YES**

| bucket | n | event rate | lift | avg favorable | avg adverse |
|---|---:|---:|---:|---:|---:|
| top 1% | 1,200 | 80.5% | 1.52× | 159.39 | -142.83 |
| top 5% | 6,000 | 79.0% | 1.49× | 125.18 | -119.36 |
| top 10% | 12,000 | 76.8% | 1.45× | 111.61 | -107.08 |
| top 20% | 24,000 | 72.9% | 1.38× | 97.82 | -92.48 |

Calibration (decile mean-pred → realized): d1:0.25→0.25 · d2:0.39→0.39 · d3:0.43→0.43 · d4:0.47→0.47 · d5:0.52→0.52 · d6:0.57→0.57 · d7:0.62→0.62 · d8:0.65→0.65 · d9:0.70→0.70 · d10:0.78→0.78

## big_up confirmation (5m close >= +$30)
n=120,000 · base rate **29.4%** · OOF AUC **0.59** (saved 0.5909670208915676) · **ECE 0.0** (isotonic-calibrated; raw 0.1375) · monotonic deciles: **YES**

| bucket | n | event rate | lift | avg favorable | avg adverse |
|---|---:|---:|---:|---:|---:|
| top 1% | 1,200 | 45.5% | 1.55× | 147.46 | 14.48 |
| top 5% | 6,000 | 40.1% | 1.36× | 120.44 | 0.28 |
| top 10% | 12,000 | 40.8% | 1.39× | 110.11 | 3.05 |
| top 20% | 24,000 | 38.2% | 1.3× | 96.04 | 1.31 |

Calibration (decile mean-pred → realized): d1:0.18→0.18 · d2:0.22→0.22 · d3:0.25→0.25 · d4:0.27→0.27 · d5:0.30→0.30 · d6:0.31→0.31 · d7:0.33→0.33 · d8:0.35→0.35 · d9:0.36→0.36 · d10:0.41→0.41

## big_down confirmation (5m close <= -$30)
n=120,000 · base rate **29.8%** · OOF AUC **0.597** (saved 0.5890997174433833) · **ECE 0.0** (isotonic-calibrated; raw 0.1359) · monotonic deciles: **YES**

| bucket | n | event rate | lift | avg favorable | avg adverse |
|---|---:|---:|---:|---:|---:|
| top 1% | 1,200 | 36.6% | 1.23× | 140.62 | 28.94 |
| top 5% | 6,000 | 42.4% | 1.43× | 120.03 | 0.7 |
| top 10% | 12,000 | 41.3% | 1.39× | 109.21 | 0.01 |
| top 20% | 24,000 | 39.3% | 1.32× | 96.96 | 0.49 |

Calibration (decile mean-pred → realized): d1:0.16→0.16 · d2:0.23→0.23 · d3:0.26→0.26 · d4:0.28→0.28 · d5:0.28→0.28 · d6:0.32→0.32 · d7:0.34→0.34 · d8:0.35→0.35 · d9:0.39→0.39 · d10:0.42→0.42

## activity_range (5m range >= $30)
n=120,000 · base rate **91.6%** · OOF AUC **0.874** (saved 0.9006376823626485) · **ECE 0.0** (isotonic-calibrated; raw 0.2163) · monotonic deciles: **YES**

| bucket | n | event rate | lift | avg favorable | avg adverse |
|---|---:|---:|---:|---:|---:|
| top 1% | 1,200 | 100.0% | 1.09× | 279.13 | None |
| top 5% | 6,000 | 100.0% | 1.09× | 229.76 | None |
| top 10% | 12,000 | 100.0% | 1.09× | 204.78 | None |
| top 20% | 24,000 | 99.9% | 1.09× | 185.5 | None |

Calibration (decile mean-pred → realized): d1:0.61→0.61 · d2:0.82→0.82 · d3:0.90→0.90 · d4:0.94→0.94 · d5:0.97→0.97 · d6:0.98→0.98 · d7:0.99→0.99 · d8:1.00→1.00 · d9:1.00→1.00

## signed_quantile (80% band)
n=143,995 · realized coverage **79.7%** (target 80%) · avg band width 26.2 bps

## P(Hold) and direction
- **P(Hold)** is validated on its own snapshot holdout — run `phold_tier_scorecard.py` (P≥0.93 → 95.1% realized, P≥0.95 → 96.0%). Different feature space (intra-window snapshots), not recomputed here.
- **Directional big-up / big-down confirmation** is bucketed above, but remains confirmation-only. Top-score precision is better than base rate, yet not strong enough to trade alone; see `sign_truth_scorecard.py` for ordinary direction truth.