# champion_v2 Shadow - regime / meta-skip vs P(Hold) baseline (pyth) - 2026-06-21

Rounds: 6,652 (one per round, last snapshot) - mean seconds_left 10s = LATE-ENTRY. Train 4,656 / Test 1,996 (temporal, by round-time -> no leak). Meta model: catboost.
Overall test held = 90.9% ; mean test p_hold = 89.7% (close => P(Hold) is well-calibrated and IS the signal).

## TRACK A - 'acted side held' (late-entry; this is P(Hold), not a fresh direction edge)
| policy | n | coverage | held % | Wilson-LB | note |
|---|---:|---:|---:|---:|---|
| baseline (act all) | 1996 | 100% | 90.9% | 89.6% |  |
| regime friendly (RANGE/LOW_VOL) | 681 | 34% | 90.5% | 88.0% |  |
| P(Hold) top25 (baseline for meta) | 514 | 26% | 99.0% | 97.7% |  |
| P(Hold) top10 | 291 | 15% | 99.3% | 97.5% |  |
| meta-skip top25 | 499 | 25% | 99.8% | 98.9% |  |
| meta-skip top10 | 200 | 10% | 100.0% | 98.1% |  |
| regime + meta-skip top25 | 125 | 6% | 100.0% | 97.0% |  |

**KEY: meta-skip vs plain P(Hold) at matched coverage** - top25 delta **+0.8 pts**, top10 delta **+0.7 pts**. Meta-skip adds ~nothing over a P(Hold) threshold => it is P(Hold) re-derived, NOT a new edge. Do not promote meta-skip as a separate head.

_Reminder: TRACK A held% is high because these are late, already-ahead snapshots. That is the known P(Hold) edge - it is NOT a fresh tradeable signal (Polymarket prices late states too). It converts to profit ONLY if the market misprices it (analyze_pm_recorder.py: 364 official outcomes, 4 joined quote rounds)._

## TRACK B - fresh DIRECTION correct (price_to_beat, 5m+15m, regime-era - the hard question)
| regime | n | direction acc % | Wilson-LB | friendly? |
|---|---:|---:|---:|---|
| TRENDING_UP | 242 | 48.8% | 42.5% | avoid |
| RANGE | 226 | 55.8% | 49.2% | YES |
| TRENDING_DOWN | 195 | 51.8% | 44.8% |  |
| HIGH_VOLATILITY | 54 | 44.4% | 32.0% | avoid |
| LOW_VOLATILITY | 44 | 61.4% | 46.6% | YES |

**Regime gate (RANGE/LOW_VOL) on fresh direction:** overall 56.7% (n=270, LB 50.7%) ; recent-250 56.8% (n=250, LB 50.6%).
- Promotion gate (overall LB>50 AND recent-250 LB>50): **PASS** - candidate to promote from shadow.
  - CAVEAT: regime-era n is only 270, so recent-250 OVERLAPS overall - NOT an independent drift check yet. The PASS rests on a single ~270-round sample.
  - CAVEAT: neither friendly regime clears LB>50 ALONE (RANGE 49.2%, LOW_VOLATILITY 46.6%) - only the POOLED set does. Marginal/fragile: keep accruing before any live wiring.

## Verdict
- **Meta-skip is not a separate edge** if the KEY delta above is ~0 - it re-derives P(Hold). Keep P(Hold) as the single fair-value backbone; don't add a CatBoost meta head.
- **Regime gate** is the only policy touching the hard (fresh-direction) question. Promote ONLY if TRACK B passes overall AND recent-window (it was drifting). Until then: shadow.
- **The real ceiling-break stays gated on joined Polymarket quotes + outcomes** (364 official outcomes, 4 joined quote rounds today). No TRACK-A held% is profit until the market is shown to misprice it.