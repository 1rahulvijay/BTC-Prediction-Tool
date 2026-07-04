# HF BTC-Path State Tests — 2026-07-02

Three existing-data (Category-A) tests on 5,893 HF March rounds (41,307 snapshots). BTC distance/vol + settlement only — **no leader price**, so free of the trade-price/latency confound. Descriptive first read; not causal-predictive, not proof.

## Test 1 — Round archetype (#14)
Round-level. Archetype from the BTC distance trajectory (causal, up to the last checkpoint). 'Hold' = the late leader finished ahead. Useful IF archetypes separate hold rates.

### 5m  (n=4,336 rounds)
| archetype | share | late-leader hold |
|---|---|---|
| QUIET | 13% | n=549 | 69.6% (LB 65.6%) |
| TREND | 39% | n=1,682 | 95.5% (LB 94.4%) |
| ACTIVE | 15% | n=664 | 80.6% (LB 77.4%) |
| CHOP | 33% | n=1,441 | 81.5% (LB 79.4%) |

**TREND − CHOP hold gap: +14.1pp** (archetype separates outcomes → useful).

### 15m  (n=1,557 rounds)
| archetype | share | late-leader hold |
|---|---|---|
| QUIET | 12% | n=189 | 78.8% (LB 72.5%) |
| TREND | 36% | n=566 | 100.0% (LB 99.3%) |
| ACTIVE | 15% | n=227 | 91.6% (LB 87.3%) |
| CHOP | 37% | n=575 | 92.5% (LB 90.1%) |

**TREND − CHOP hold gap: +7.5pp** (archetype separates outcomes → useful).

## Test 2 — Entry-timing quality (#2)
Snapshot-level (one entry per round per checkpoint). Leader-hold rate by seconds-left. Does holding improve late? Does a bigger lead (≥$20) help at each time?

### 5m
| seconds left | all leaders | leaders ≥ $20 |
|---|---|---|
| 240s | 65.7% (LB 65%, n=6,164) | 72.5% (LB 71%, n=3,791) |
| 180s | 73.9% (LB 73%, n=6,205) | 81.6% (LB 80%, n=4,353) |
| 120s | 81.3% (LB 80%, n=6,212) | 87.7% (LB 87%, n=4,755) |
| 60s | 85.4% (LB 84%, n=6,211) | 90.9% (LB 90%, n=5,013) |
| 30s | 85.4% (LB 84%, n=6,211) | 90.9% (LB 90%, n=5,013) |

### 15m
| seconds left | all leaders | leaders ≥ $20 |
|---|---|---|
| 720s | 64.6% (LB 63%, n=2,061) | 68.6% (LB 66%, n=1,599) |
| 540s | 74.5% (LB 73%, n=2,060) | 78.5% (LB 76%, n=1,723) |
| 360s | 82.7% (LB 81%, n=2,061) | 85.9% (LB 84%, n=1,807) |
| 180s | 88.8% (LB 87%, n=2,061) | 92.9% (LB 92%, n=1,836) |
| 60s | 93.4% (LB 92%, n=2,061) | 96.9% (LB 96%, n=1,862) |

## Test 3 — Cross-market 5m/15m consistency (#23)
Each 5m snapshot matched to the nearest-in-time 15m snapshot (±90s). AGREE = both lean the same side. Hypothesis: agreement = more stable = the 5m leader holds more often than when dislocated.

Matched 5m snapshots: 26,545 (of 31,003).
| 5m vs 15m lean | n | 5m leader hold |
|---|---|---|
| AGREE (consistent) | n=19,304 | 86.2% (LB 85.7%) |
| DISAGREE (dislocated) | n=7,241 | 60.4% (LB 59.3%) |

**AGREE − DISAGREE hold gap: +25.8pp** (consistency is a real stability signal).

## Caveats
- HF March-only; leader-had-trades subset; ~5.9k rounds. Not forward-validated.
- Archetype/leader use the last checkpoint (causal), but this is characterization, not a live head.
- Real betting still requires live ask/fill/edge-duration (Category B) — these tests inform UX/risk framing only.