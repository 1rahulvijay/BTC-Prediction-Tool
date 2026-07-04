# HF Market Calibration Curve (leader trade-price) — 2026-07-02

When the leader TRADES at price X, does it win X%? 37,789 leader snapshots / 5,893 rounds. gap = actual win% − price. ⚠️ **TRADE prices, not resting asks** — gap>0 means leaders traded cheap, NOT that you could buy them cheap (barbell book). Live /book required for executable calibration.

### All leader snapshots (calibration curve — correlated)  (n=37,789)
| leader price | n | actual win% | Wilson-LB | gap (win−price) |
|---|---|---|---|---|
| (0.3, 0.35] | 400 | 51.7 | 46.9 | **+19.0pp** ⬆underpriced |
| (0.35, 0.4] | 564 | 68.1 | 64.1 | **+30.2pp** ⬆underpriced |
| (0.4, 0.45] | 883 | 61.6 | 58.4 | **+18.3pp** ⬆underpriced |
| (0.45, 0.5] | 11,292 | 76.3 | 75.5 | **+26.8pp** ⬆underpriced |
| (0.5, 0.55] | 10,344 | 78.7 | 77.9 | **+27.3pp** ⬆underpriced |
| (0.55, 0.6] | 1,025 | 82.7 | 80.3 | **+25.1pp** ⬆underpriced |
| (0.6, 0.65] | 952 | 82.1 | 79.6 | **+19.3pp** ⬆underpriced |
| (0.65, 0.7] | 881 | 88.8 | 86.5 | **+20.7pp** ⬆underpriced |
| (0.7, 0.8] | 1,525 | 90.4 | 88.8 | **+15.3pp** ⬆underpriced |
| (0.8, 1.01] | 7,928 | 98.6 | 98.3 | **+2.1pp** |

### ROUND-level (one earliest snapshot per round — honest independent n)  (n=5,893)
| leader price | n | actual win% | Wilson-LB | gap (win−price) |
|---|---|---|---|---|
| (0.3, 0.35] | 68 | 27.9 | 18.7 | **-4.7pp** ⬇overpriced |
| (0.35, 0.4] | 101 | 50.5 | 40.9 | **+12.7pp** ⬆underpriced |
| (0.4, 0.45] | 171 | 42.7 | 35.5 | **-0.7pp** |
| (0.45, 0.5] | 1,654 | 61.0 | 58.6 | **+11.6pp** ⬆underpriced |
| (0.5, 0.55] | 1,494 | 65.5 | 63.0 | **+14.0pp** ⬆underpriced |
| (0.55, 0.6] | 154 | 67.5 | 59.8 | **+9.9pp** |
| (0.6, 0.65] | 177 | 72.3 | 65.3 | **+9.4pp** |
| (0.65, 0.7] | 151 | 80.8 | 73.8 | **+12.7pp** ⬆underpriced |
| (0.7, 0.8] | 255 | 83.5 | 78.5 | **+8.3pp** ⬆underpriced |
| (0.8, 1.01] | 1,160 | 97.4 | 96.3 | **+1.2pp** |

### 5m only  (n=28,186)
| leader price | n | actual win% | Wilson-LB | gap (win−price) |
|---|---|---|---|---|
| (0.3, 0.35] | 230 | 51.3 | 44.9 | **+18.2pp** ⬆underpriced |
| (0.35, 0.4] | 337 | 67.1 | 61.9 | **+29.3pp** ⬆underpriced |
| (0.4, 0.45] | 542 | 62.0 | 57.8 | **+18.9pp** ⬆underpriced |
| (0.45, 0.5] | 10,027 | 76.3 | 75.5 | **+26.8pp** ⬆underpriced |
| (0.5, 0.55] | 8,903 | 78.4 | 77.6 | **+27.1pp** ⬆underpriced |
| (0.55, 0.6] | 550 | 84.5 | 81.3 | **+27.0pp** ⬆underpriced |
| (0.6, 0.65] | 509 | 82.9 | 79.4 | **+20.0pp** ⬆underpriced |
| (0.65, 0.7] | 494 | 93.9 | 91.5 | **+25.8pp** ⬆underpriced |
| (0.7, 0.8] | 774 | 91.2 | 89.0 | **+16.2pp** ⬆underpriced |
| (0.8, 1.01] | 4,557 | 98.9 | 98.6 | **+2.0pp** |

### 15m only  (n=9,603)
| leader price | n | actual win% | Wilson-LB | gap (win−price) |
|---|---|---|---|---|
| (0.3, 0.35] | 170 | 52.4 | 44.9 | **+20.1pp** ⬆underpriced |
| (0.35, 0.4] | 227 | 69.6 | 63.3 | **+31.6pp** ⬆underpriced |
| (0.4, 0.45] | 341 | 61.0 | 55.7 | **+17.4pp** ⬆underpriced |
| (0.45, 0.5] | 1,265 | 76.0 | 73.5 | **+27.0pp** ⬆underpriced |
| (0.5, 0.55] | 1,441 | 80.4 | 78.2 | **+28.5pp** ⬆underpriced |
| (0.55, 0.6] | 475 | 80.6 | 76.8 | **+23.0pp** ⬆underpriced |
| (0.6, 0.65] | 443 | 81.3 | 77.4 | **+18.4pp** ⬆underpriced |
| (0.65, 0.7] | 387 | 82.2 | 78.0 | **+14.2pp** ⬆underpriced |
| (0.7, 0.8] | 751 | 89.5 | 87.1 | **+14.3pp** ⬆underpriced |
| (0.8, 1.01] | 3,371 | 98.2 | 97.6 | **+2.3pp** |

### Early (seconds_left ≥ 180)  (n=18,917)
| leader price | n | actual win% | Wilson-LB | gap (win−price) |
|---|---|---|---|---|
| (0.3, 0.35] | 218 | 42.7 | 36.3 | **+10.2pp** ⬆underpriced |
| (0.35, 0.4] | 315 | 59.4 | 53.9 | **+21.4pp** ⬆underpriced |
| (0.4, 0.45] | 496 | 51.8 | 47.4 | **+8.4pp** ⬆underpriced |
| (0.45, 0.5] | 5,045 | 68.0 | 66.7 | **+18.6pp** ⬆underpriced |
| (0.5, 0.55] | 4,700 | 71.6 | 70.3 | **+20.1pp** ⬆underpriced |
| (0.55, 0.6] | 588 | 75.0 | 71.3 | **+17.4pp** ⬆underpriced |
| (0.6, 0.65] | 578 | 75.6 | 71.9 | **+12.7pp** ⬆underpriced |
| (0.65, 0.7] | 504 | 83.1 | 79.6 | **+15.1pp** ⬆underpriced |
| (0.7, 0.8] | 919 | 86.4 | 84.0 | **+11.3pp** ⬆underpriced |
| (0.8, 1.01] | 4,346 | 97.9 | 97.4 | **+1.7pp** |

### Late (seconds_left < 120)  (n=13,217)
| leader price | n | actual win% | Wilson-LB | gap (win−price) |
|---|---|---|---|---|
| (0.3, 0.35] | 134 | 65.7 | 57.3 | **+32.7pp** ⬆underpriced |
| (0.35, 0.4] | 187 | 82.4 | 76.3 | **+44.6pp** ⬆underpriced |
| (0.4, 0.45] | 280 | 76.1 | 70.7 | **+32.9pp** ⬆underpriced |
| (0.45, 0.5] | 4,229 | 84.5 | 83.4 | **+35.0pp** ⬆underpriced |
| (0.5, 0.55] | 3,866 | 86.1 | 85.0 | **+34.7pp** ⬆underpriced |
| (0.55, 0.6] | 327 | 94.5 | 91.5 | **+36.9pp** ⬆underpriced |
| (0.6, 0.65] | 267 | 95.9 | 92.8 | **+33.1pp** ⬆underpriced |
| (0.65, 0.7] | 279 | 96.8 | 94.0 | **+28.7pp** ⬆underpriced |
| (0.7, 0.8] | 448 | 97.8 | 95.9 | **+22.7pp** ⬆underpriced |
| (0.8, 1.01] | 2,644 | 99.7 | 99.5 | **+2.9pp** |

## Verdict
- Overall leader gap: snapshot **+19.6pp** (win 79.1% vs price 59.4%), round-level **+8.2pp**.
- **Leaders are systematically underpriced in the TRADE data across price levels** — a real (research) market-calibration finding.
- ⚠️ This is a **trade-price** calibration; the *executable* version (leader ASK vs win rate) can only be measured on the live /book recorder. gap>0 here is the hypothesis to validate live, not a tradeable edge.
- Decision-support use (once validated live): show `leader historical win rate in this state` next to the live ask — flag when the ask is below the state's win rate (the cheap-leader signal).