# P(hold) Calibration Monitor — 2026-06-21 22:42

Does the SERVED P(hold) still mean what it says, on REAL resolved rounds? fair_value = P(hold), so this is the calibration the champion + edge gate depend on. Read-only; does not change serving.

### Overall (all horizons)
n=79,019 · base hold-rate 74.1% · ECE **0.0093** · Brier 0.1674 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 26,458 | 34% | 92.5% | 92.1% | +0.4pt |
| 0.90 | 19,593 | 25% | 94.4% | 94.6% | -0.2pt |
| 0.93 | 16,670 | 21% | 95.5% | 96.3% | -0.8pt |
| 0.95 | 13,949 | 18% | 96.3% | 97.2% | -0.9pt |

Reliability (predicted → realized): 0.53→0.53(n11979) · 0.57→0.57(n8161) · 0.62→0.62(n7523) · 0.68→0.67(n7445) · 0.73→0.72(n6458) · 0.78→0.79(n5225) · 0.82→0.83(n5254) · 0.88→0.87(n6865) · 0.93→0.90(n5644) · 0.99→0.96(n13949)

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
n=11,289 · base hold-rate 72.6% · ECE **0.0137** · Brier 0.1763 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 3,482 | 31% | 91.9% | 92.1% | -0.2pt |
| 0.90 | 2,729 | 24% | 93.0% | 94.6% | -1.6pt |
| 0.93 | 2,411 | 21% | 93.9% | 96.3% | -2.4pt |
| 0.95 | 2,107 | 19% | 94.7% | 97.2% | -2.5pt |

Reliability (predicted → realized): 0.53→0.52(n2013) · 0.57→0.58(n1291) · 0.62→0.62(n1182) · 0.68→0.68(n1026) · 0.73→0.74(n839) · 0.78→0.76(n717) · 0.82→0.81(n685) · 0.88→0.88(n753) · 0.93→0.87(n622) · 0.99→0.95(n2107)

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
n=11,078 · base hold-rate 77.3% · ECE **0.0251** · Brier 0.1512 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 3,687 | 33% | 95.8% | 92.1% | +3.7pt |
| 0.90 | 2,690 | 24% | 96.7% | 94.6% | +2.1pt |
| 0.93 | 2,340 | 21% | 97.1% | 96.3% | +0.8pt |
| 0.95 | 2,003 | 18% | 97.6% | 97.2% | +0.4pt |

Reliability (predicted → realized): 0.53→0.53(n1503) · 0.57→0.61(n1200) · 0.62→0.63(n1149) · 0.68→0.72(n1091) · 0.73→0.78(n887) · 0.78→0.81(n764) · 0.82→0.86(n743) · 0.88→0.94(n997) · 0.93→0.94(n687) · 0.99→0.98(n2003)

### Horizon 30m
n=10,937 · base hold-rate 75.8% · ECE **0.0152** · Brier 0.1579 · verdict: **STABLE — calibration holds**

| P(hold) ≥ | n | coverage | realized | trained claim | drift |
|---|---:|---:|---:|---:|---:|
| 0.85 | 3,541 | 32% | 94.9% | 92.1% | +2.8pt |
| 0.90 | 2,699 | 25% | 96.2% | 94.6% | +1.6pt |
| 0.93 | 2,261 | 21% | 97.1% | 96.3% | +0.8pt |
| 0.95 | 1,886 | 17% | 97.7% | 97.2% | +0.5pt |

Reliability (predicted → realized): 0.53→0.53(n1421) · 0.58→0.59(n1279) · 0.62→0.65(n1223) · 0.68→0.68(n1080) · 0.73→0.75(n812) · 0.78→0.80(n752) · 0.82→0.86(n737) · 0.88→0.91(n842) · 0.93→0.93(n813) · 0.99→0.98(n1886)
