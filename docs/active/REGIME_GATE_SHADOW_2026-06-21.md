# Regime-Gate Shadow Monitor — 2026-06-21

Read-only replay over **772 regime-era directional rounds** (2026-06-18 → 2026-06-21, 5m+15m). No live decision is affected. Promote a policy only when its **Wilson-LB stays > 50%** as n grows.


## All regime-era rounds  (n=772)
| policy | acts | coverage % | acc % | Wilson-LB | LB>50? |
|---|---|---|---|---|---|
| baseline (act on all) | 772 | 100.0 | 52.1 | 48.5 | — |
| prefer RANGE+LOW_VOLATILITY | 274 | 35.5 | 56.6 | 50.6 | ✅ |
| avoid TRENDING_UP | 525 | 68.0 | 53.7 | 49.4 | ⚠️ |
| avoid TRENDING_UP + HIGH_VOL | 470 | 60.9 | 54.7 | 50.2 | ✅ |

## Recent 250 rounds (drift watch)  (n=250)
| policy | acts | coverage % | acc % | Wilson-LB | LB>50? |
|---|---|---|---|---|---|
| baseline (act on all) | 250 | 100.0 | 50.8 | 44.6 | — |
| prefer RANGE+LOW_VOLATILITY | 80 | 32.0 | 46.2 | 35.7 | ⚠️ |
| avoid TRENDING_UP | 167 | 66.8 | 49.1 | 41.6 | ⚠️ |
| avoid TRENDING_UP + HIGH_VOL | 136 | 54.4 | 50.0 | 41.7 | ⚠️ |

## 5m only  (n=582)
| policy | acts | coverage % | acc % | Wilson-LB | LB>50? |
|---|---|---|---|---|---|
| baseline (act on all) | 582 | 100.0 | 50.7 | 46.6 | — |
| prefer RANGE+LOW_VOLATILITY | 203 | 34.9 | 57.6 | 50.8 | ✅ |
| avoid TRENDING_UP | 396 | 68.0 | 53.3 | 48.4 | ⚠️ |
| avoid TRENDING_UP + HIGH_VOL | 354 | 60.8 | 54.2 | 49.0 | ⚠️ |

## 15m only  (n=190)
| policy | acts | coverage % | acc % | Wilson-LB | LB>50? |
|---|---|---|---|---|---|
| baseline (act on all) | 190 | 100.0 | 56.3 | 49.2 | — |
| prefer RANGE+LOW_VOLATILITY | 71 | 37.4 | 53.5 | 42.0 | ⚠️ |
| avoid TRENDING_UP | 129 | 67.9 | 55.0 | 46.4 | ⚠️ |
| avoid TRENDING_UP + HIGH_VOL | 116 | 61.1 | 56.0 | 47.0 | ⚠️ |

## Per-regime reference (regime-era)
| regime | n | acc % | Wilson-LB |
|---|---|---|---|
| LOW_VOLATILITY | 44 | 61.4 | 46.6 |
| RANGE | 230 | 55.7 | 49.2 |
| TRENDING_DOWN | 196 | 52.0 | 45.1 |
| TRENDING_UP | 247 | 48.6 | 42.4 |
| HIGH_VOLATILITY | 55 | 45.5 | 33.0 |

_Appended headline to `data\regime_gate_shadow_log.csv` (prefer-RANGE policy: n=274, acc=56.6%, Wilson-LB=50.6%). Re-run to extend the drift series._

## Read
- A ✅ policy has Wilson-LB > 50% — its edge survives sampling error **at this n**.
- Watch the **recent-window** section: if a policy's LB there drops below 50%, the edge is decaying — do not promote.
- This stays a shadow monitor until you explicitly approve wiring a regime gate into the live decision path.