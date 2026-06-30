# P(hold) Calibration Monitor — 2026-06-18 21:32

Does the SERVED P(hold) still mean what it says, on REAL resolved rounds? fair_value = P(hold), so this is the calibration the champion + edge gate depend on. Read-only; does not change serving.

### Overall (all horizons)
n=13,972 · base hold-rate 74.5% · ECE **0.0327** · Brier 0.1687 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 5,078 | 36% | 91.4% | 92.1% | -0.7pt |
| 0.90 | 4,288 | 31% | 92.7% | 94.6% | -1.9pt |
| 0.93 | 3,704 | 26% | 94.0% | 96.3% | -2.3pt |
| 0.95 | 3,284 | 24% | 94.9% | 97.2% | -2.3pt |

Reliability (predicted → realized): 0.52→0.52(n1991) · 0.57→0.61(n1673) · 0.63→0.69(n938) · 0.68→0.70(n1052) · 0.73→0.72(n1285) · 0.77→0.76(n625) · 0.82→0.78(n903) · 0.88→0.84(n790) · 0.93→0.85(n1004) · 0.99→0.95(n3284)

### Horizon 1m
n=2,099 · base hold-rate 68.9% · ECE **0.0545** · Brier 0.1925 · verdict: **DRIFT — recalibrate**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 783 | 37% | 87.6% | 92.1% | -4.5pt |
| 0.90 | 634 | 30% | 88.3% | 94.6% | -6.3pt |
| 0.93 | 532 | 25% | 89.8% | 96.3% | -6.5pt |
| 0.95 | 444 | 21% | 91.4% | 97.2% | -5.8pt |

Reliability (predicted → realized): 0.52→0.52(n340) · 0.57→0.56(n182) · 0.62→0.62(n86) · 0.68→0.59(n136) · 0.73→0.59(n179) · 0.77→0.73(n103) · 0.82→0.77(n124) · 0.88→0.85(n149) · 0.93→0.81(n190) · 0.98→0.91(n444)

### Horizon 3m
n=2,011 · base hold-rate 69.2% · ECE **0.056** · Brier 0.1945 · verdict: **DRIFT — recalibrate**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 731 | 36% | 86.0% | 92.1% | -6.1pt |
| 0.90 | 632 | 31% | 87.8% | 94.6% | -6.8pt |
| 0.93 | 550 | 27% | 90.0% | 96.3% | -6.3pt |
| 0.95 | 498 | 25% | 90.6% | 97.2% | -6.6pt |

Reliability (predicted → realized): 0.52→0.48(n380) · 0.58→0.59(n225) · 0.63→0.64(n131) · 0.68→0.60(n128) · 0.73→0.70(n175) · 0.78→0.72(n74) · 0.82→0.75(n114) · 0.88→0.75(n99) · 0.93→0.78(n134) · 0.99→0.91(n498)

### Horizon 5m
n=1,988 · base hold-rate 76.2% · ECE **0.0322** · Brier 0.1595 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 710 | 36% | 93.4% | 92.1% | +1.3pt |
| 0.90 | 606 | 30% | 94.7% | 94.6% | +0.1pt |
| 0.93 | 523 | 26% | 95.2% | 96.3% | -1.1pt |
| 0.95 | 460 | 23% | 95.9% | 97.2% | -1.3pt |

Reliability (predicted → realized): 0.52→0.54(n295) · 0.57→0.59(n268) · 0.63→0.67(n135) · 0.67→0.75(n138) · 0.73→0.79(n178) · 0.77→0.75(n87) · 0.82→0.81(n131) · 0.88→0.86(n104) · 0.93→0.91(n146) · 0.99→0.96(n460)

### Horizon 7m
n=1,993 · base hold-rate 75.3% · ECE **0.0551** · Brier 0.1694 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 766 | 38% | 91.6% | 92.1% | -0.5pt |
| 0.90 | 638 | 32% | 93.3% | 94.6% | -1.3pt |
| 0.93 | 547 | 27% | 95.2% | 96.3% | -1.1pt |
| 0.95 | 491 | 25% | 95.5% | 97.2% | -1.7pt |

Reliability (predicted → realized): 0.52→0.57(n282) · 0.57→0.61(n235) · 0.63→0.71(n129) · 0.68→0.71(n137) · 0.73→0.67(n188) · 0.77→0.71(n91) · 0.82→0.71(n134) · 0.88→0.84(n128) · 0.93→0.86(n147) · 0.99→0.95(n491)

### Horizon 10m
n=1,969 · base hold-rate 73.5% · ECE **0.0378** · Brier 0.1733 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 667 | 34% | 91.3% | 92.1% | -0.8pt |
| 0.90 | 558 | 28% | 92.8% | 94.6% | -1.8pt |
| 0.93 | 479 | 24% | 94.6% | 96.3% | -1.7pt |
| 0.95 | 414 | 21% | 97.1% | 97.2% | -0.1pt |

Reliability (predicted → realized): 0.52→0.47(n251) · 0.57→0.60(n248) · 0.63→0.68(n139) · 0.68→0.73(n166) · 0.73→0.73(n216) · 0.77→0.71(n97) · 0.82→0.73(n150) · 0.88→0.83(n109) · 0.93→0.81(n144) · 0.99→0.97(n414)

### Horizon 15m
n=1,979 · base hold-rate 76.8% · ECE **0.0252** · Brier 0.1554 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 727 | 37% | 94.1% | 92.1% | +2.0pt |
| 0.90 | 616 | 31% | 95.0% | 94.6% | +0.4pt |
| 0.93 | 539 | 27% | 95.2% | 96.3% | -1.1pt |
| 0.95 | 484 | 24% | 95.5% | 97.2% | -1.7pt |

Reliability (predicted → realized): 0.52→0.52(n258) · 0.58→0.63(n245) · 0.63→0.68(n163) · 0.68→0.68(n160) · 0.73→0.74(n172) · 0.78→0.83(n88) · 0.82→0.82(n130) · 0.88→0.89(n111) · 0.93→0.93(n132) · 0.99→0.95(n484)

### Horizon 30m
n=1,933 · base hold-rate 82.2% · ECE **0.0679** · Brier 0.1336 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 694 | 36% | 96.3% | 92.1% | +4.2pt |
| 0.90 | 604 | 31% | 97.2% | 94.6% | +2.6pt |
| 0.93 | 534 | 28% | 98.1% | 96.3% | +1.8pt |
| 0.95 | 493 | 26% | 98.6% | 97.2% | +1.4pt |

Reliability (predicted → realized): 0.52→0.57(n185) · 0.58→0.69(n270) · 0.62→0.80(n155) · 0.68→0.78(n187) · 0.73→0.85(n177) · 0.78→0.85(n85) · 0.82→0.89(n120) · 0.88→0.90(n90) · 0.93→0.91(n111) · 0.99→0.99(n493)
