# Head Probability Buckets — 2026-06-18

Leak-free OOF (TimeSeriesSplit-5) bucket quality for each specialist head. The champion validator should trust a head's probability region only where its bucket event-rate + calibration here are good. Raw AUC alone is insufficient.

## bigmove (move > p75)
n=72,000 · base rate **24.0%** · OOF AUC **0.733** (saved 0.7327401850018195) · ECE 0.1861 · monotonic deciles: **YES**

| bucket | n | event rate | lift | avg favorable | avg adverse |
|---|---:|---:|---:|---:|---:|
| top 1% | 720 | 62.1% | 2.59× | 150.08 | None |
| top 5% | 3,600 | 58.7% | 2.45× | 129.0 | None |
| top 10% | 7,200 | 54.7% | 2.28× | 115.37 | None |
| top 20% | 14,400 | 48.3% | 2.01× | 100.4 | None |

Calibration (decile mean-pred → realized): d1:0.19→0.05 · d2:0.24→0.09 · d3:0.30→0.12 · d4:0.34→0.15 · d5:0.37→0.17 · d6:0.41→0.22 · d7:0.47→0.29 · d8:0.55→0.35 · d9:0.64→0.42 · d10:0.76→0.55

## bigdrop (low <= -10 bps)
n=72,000 · base rate **24.2%** · OOF AUC **0.751** (saved 0.7508757166386797) · ECE 0.1803 · monotonic deciles: **YES**

| bucket | n | event rate | lift | avg favorable | avg adverse |
|---|---:|---:|---:|---:|---:|
| top 1% | 720 | 66.1% | 2.74× | 152.51 | -21.3 |
| top 5% | 3,600 | 63.7% | 2.63× | 128.11 | -19.15 |
| top 10% | 7,200 | 59.0% | 2.44× | 115.32 | -16.94 |
| top 20% | 14,400 | 51.6% | 2.14× | 100.78 | -14.24 |

Calibration (decile mean-pred → realized): d1:0.19→0.05 · d2:0.25→0.08 · d3:0.29→0.11 · d4:0.33→0.14 · d5:0.36→0.15 · d6:0.41→0.20 · d7:0.47→0.28 · d8:0.55→0.36 · d9:0.63→0.44 · d10:0.74→0.59

## big_up confirmation (close >= +10 bps)
n=72,000 · base rate **12.5%** · OOF AUC **0.712** (saved 0.7115005064982293) · ECE 0.2599 · monotonic deciles: **YES**

| bucket | n | event rate | lift | avg favorable | avg adverse |
|---|---:|---:|---:|---:|---:|
| top 1% | 720 | 39.4% | 3.16× | 158.9 | 4.72 |
| top 5% | 3,600 | 32.0% | 2.56× | 129.45 | 0.42 |
| top 10% | 7,200 | 29.5% | 2.37× | 114.7 | 0.0 |
| top 20% | 14,400 | 26.4% | 2.12× | 100.31 | -0.06 |

Calibration (decile mean-pred → realized): d1:0.17→0.03 · d2:0.22→0.04 · d3:0.28→0.06 · d4:0.32→0.08 · d5:0.35→0.09 · d6:0.38→0.12 · d7:0.42→0.13 · d8:0.48→0.17 · d9:0.55→0.23 · d10:0.67→0.30

## big_down confirmation (close <= -10 bps)
n=72,000 · base rate **13.5%** · OOF AUC **0.71** (saved 0.7097602244396399) · ECE 0.2575 · monotonic deciles: **YES**

| bucket | n | event rate | lift | avg favorable | avg adverse |
|---|---:|---:|---:|---:|---:|
| top 1% | 720 | 34.2% | 2.53× | 132.78 | 1.17 |
| top 5% | 3,600 | 32.2% | 2.39× | 122.5 | 0.79 |
| top 10% | 7,200 | 31.6% | 2.34× | 112.16 | -0.01 |
| top 20% | 14,400 | 27.9% | 2.06× | 100.2 | -0.07 |

Calibration (decile mean-pred → realized): d1:0.20→0.03 · d2:0.26→0.06 · d3:0.30→0.06 · d4:0.33→0.08 · d5:0.35→0.08 · d6:0.38→0.12 · d7:0.43→0.16 · d8:0.49→0.20 · d9:0.55→0.24 · d10:0.63→0.32

## activity_range (5m range >= p75)
n=72,000 · base rate **24.1%** · OOF AUC **0.868** (saved 0.8681896970430202) · ECE 0.1481 · monotonic deciles: **YES**

| bucket | n | event rate | lift | avg favorable | avg adverse |
|---|---:|---:|---:|---:|---:|
| top 1% | 720 | 97.1% | 4.02× | 53.36 | None |
| top 5% | 3,600 | 91.2% | 3.78× | 39.41 | None |
| top 10% | 7,200 | 83.9% | 3.47× | 34.31 | None |
| top 20% | 14,400 | 70.2% | 2.91× | 28.54 | None |

Calibration (decile mean-pred → realized): d1:0.10→0.01 · d2:0.14→0.03 · d3:0.19→0.05 · d4:0.23→0.07 · d5:0.28→0.10 · d6:0.34→0.15 · d7:0.44→0.23 · d8:0.56→0.37 · d9:0.72→0.57 · d10:0.88→0.84

## signed_quantile (80% band)
n=86,395 · realized coverage **87.5%** (target 80%) · avg band width 30.1 bps

## P(Hold) and direction
- **P(Hold)** is validated on its own snapshot holdout — run `phold_tier_scorecard.py` (P≥0.93 → 95.1% realized, P≥0.95 → 96.0%). Different feature space (intra-window snapshots), not recomputed here.
- **Directional big-up / big-down confirmation** is bucketed above, but remains confirmation-only. Top-score precision is better than base rate, yet not strong enough to trade alone; see `sign_truth_scorecard.py` for ordinary direction truth.