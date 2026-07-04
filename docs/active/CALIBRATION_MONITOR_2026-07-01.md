# P(hold) Calibration Monitor — 2026-07-01 11:47

Does the SERVED P(hold) still mean what it says, on REAL resolved rounds? The champion consumes this probability but caps entry fair value at 91c. Read-only; does not change serving.

> Snapshot rows are repeated observations inside the same round. They diagnose calibration drift but are not independent betting opportunities.

## Decision-Level Late-Entry Scorecard (Independent Rounds)

Each row counts at most one decision per round: the first 5m/15m snapshot that clears P(hold), 15<seconds_left<=120, and |move|>=$10. This is side-hold precision, not profitability; market ask, taker fee, spread, depth, and fill are not included.

| P(hold) >= | rounds | held | mean predicted | 95% Wilson lower bound |
|---:|---:|---:|---:|---:|
| 0.85 | 1,888 | 90.8% | 94.4% | 89.5% |
| 0.90 | 1,840 | 91.7% | 96.7% | 90.3% |
| 0.93 | 1,794 | 92.6% | 97.8% | 91.3% |
| 0.95 | 1,752 | 93.2% | 98.6% | 91.9% |

Snapshot counts below are useful for drift diagnosis only. Do not quote them as independent bets.

### Snapshot Diagnostics — Overall (all horizons)
n=109,749 · base hold-rate 74.6% · ECE **0.0099** · Brier 0.1652 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 36,829 | 34% | 93.0% | 92.1% | +0.9pt |
| 0.90 | 28,174 | 26% | 94.7% | 94.6% | +0.1pt |
| 0.93 | 24,055 | 22% | 95.7% | 96.3% | -0.6pt |
| 0.95 | 20,685 | 19% | 96.3% | 97.2% | -0.9pt |

Reliability (predicted → realized): 0.53→0.53(n16702) · 0.58→0.58(n11678) · 0.62→0.63(n10358) · 0.68→0.68(n10104) · 0.73→0.73(n8681) · 0.78→0.79(n7387) · 0.83→0.83(n7225) · 0.88→0.87(n8655) · 0.93→0.90(n7489) · 0.99→0.96(n20685)

### Horizon 1m
n=11,971 · base hold-rate 71.0% · ECE **0.0533** · Brier 0.1869 · verdict: **DRIFT — recalibrate**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 4,532 | 38% | 87.1% | 92.1% | -5.0pt |
| 0.90 | 2,780 | 23% | 91.0% | 94.6% | -3.6pt |
| 0.93 | 2,053 | 17% | 93.2% | 96.3% | -3.1pt |
| 0.95 | 1,410 | 12% | 94.6% | 97.2% | -2.6pt |

Reliability (predicted → realized): 0.53→0.53(n1492) · 0.57→0.53(n840) · 0.62→0.56(n664) · 0.68→0.59(n1271) · 0.73→0.63(n1173) · 0.78→0.73(n838) · 0.83→0.78(n980) · 0.88→0.81(n1752) · 0.93→0.87(n1370) · 0.98→0.95(n1410)

### Horizon 3m
n=11,389 · base hold-rate 73.7% · ECE **0.0186** · Brier 0.1695 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 4,097 | 36% | 91.2% | 92.1% | -0.9pt |
| 0.90 | 3,242 | 28% | 93.1% | 94.6% | -1.5pt |
| 0.93 | 2,897 | 25% | 94.3% | 96.3% | -2.0pt |
| 0.95 | 2,522 | 22% | 95.2% | 97.2% | -2.0pt |

Reliability (predicted → realized): 0.53→0.53(n2033) · 0.57→0.57(n1030) · 0.62→0.62(n1048) · 0.68→0.68(n962) · 0.73→0.72(n839) · 0.78→0.79(n669) · 0.82→0.82(n654) · 0.88→0.84(n855) · 0.93→0.86(n720) · 0.99→0.95(n2522)

### Horizon 5m
n=26,741 · base hold-rate 73.5% · ECE **0.0143** · Brier 0.1713 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 8,717 | 33% | 92.2% | 92.1% | +0.1pt |
| 0.90 | 7,068 | 26% | 93.5% | 94.6% | -1.1pt |
| 0.93 | 6,179 | 23% | 94.3% | 96.3% | -2.0pt |
| 0.95 | 5,567 | 21% | 94.8% | 97.2% | -2.4pt |

Reliability (predicted → realized): 0.53→0.52(n4552) · 0.58→0.58(n2952) · 0.62→0.63(n2583) · 0.68→0.69(n2366) · 0.72→0.73(n1954) · 0.78→0.77(n1736) · 0.83→0.82(n1724) · 0.88→0.87(n1649) · 0.93→0.89(n1501) · 0.99→0.95(n5567)

### Horizon 7m
n=11,192 · base hold-rate 73.9% · ECE **0.0119** · Brier 0.1663 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 3,594 | 32% | 94.1% | 92.1% | +2.0pt |
| 0.90 | 2,776 | 25% | 95.5% | 94.6% | +0.9pt |
| 0.93 | 2,408 | 22% | 96.5% | 96.3% | +0.2pt |
| 0.95 | 2,074 | 18% | 96.6% | 97.2% | -0.6pt |

Reliability (predicted → realized): 0.53→0.52(n1788) · 0.57→0.55(n1242) · 0.62→0.64(n1152) · 0.68→0.69(n980) · 0.73→0.73(n958) · 0.78→0.78(n727) · 0.82→0.81(n714) · 0.88→0.89(n818) · 0.93→0.92(n702) · 0.99→0.97(n2074)

### Horizon 10m
n=11,163 · base hold-rate 74.8% · ECE **0.0142** · Brier 0.1619 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 3,525 | 32% | 94.1% | 92.1% | +2.0pt |
| 0.90 | 2,677 | 24% | 95.7% | 94.6% | +1.1pt |
| 0.93 | 2,300 | 21% | 96.7% | 96.3% | +0.4pt |
| 0.95 | 1,947 | 17% | 97.5% | 97.2% | +0.3pt |

Reliability (predicted → realized): 0.53→0.53(n1729) · 0.57→0.57(n1279) · 0.62→0.61(n1105) · 0.68→0.70(n1035) · 0.73→0.76(n950) · 0.78→0.83(n758) · 0.82→0.84(n741) · 0.88→0.89(n848) · 0.93→0.91(n730) · 0.99→0.97(n1947)

### Horizon 15m
n=26,356 · base hold-rate 77.5% · ECE **0.0262** · Brier 0.1511 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 8,823 | 34% | 95.8% | 92.1% | +3.7pt |
| 0.90 | 6,932 | 26% | 96.9% | 94.6% | +2.3pt |
| 0.93 | 5,957 | 23% | 97.4% | 96.3% | +1.1pt |
| 0.95 | 5,279 | 20% | 97.9% | 97.2% | +0.7pt |

Reliability (predicted → realized): 0.53→0.54(n3687) · 0.58→0.62(n3056) · 0.62→0.66(n2583) · 0.68→0.72(n2410) · 0.72→0.77(n1995) · 0.78→0.82(n1907) · 0.83→0.84(n1675) · 0.88→0.92(n1891) · 0.93→0.94(n1653) · 0.99→0.98(n5279)

### Horizon 30m
n=10,937 · base hold-rate 75.8% · ECE **0.0152** · Brier 0.1579 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 3,541 | 32% | 94.9% | 92.1% | +2.8pt |
| 0.90 | 2,699 | 25% | 96.2% | 94.6% | +1.6pt |
| 0.93 | 2,261 | 21% | 97.1% | 96.3% | +0.8pt |
| 0.95 | 1,886 | 17% | 97.7% | 97.2% | +0.5pt |

Reliability (predicted → realized): 0.53→0.53(n1421) · 0.58→0.59(n1279) · 0.62→0.65(n1223) · 0.68→0.68(n1080) · 0.73→0.75(n812) · 0.78→0.80(n752) · 0.82→0.86(n737) · 0.88→0.91(n842) · 0.93→0.93(n813) · 0.99→0.98(n1886)
