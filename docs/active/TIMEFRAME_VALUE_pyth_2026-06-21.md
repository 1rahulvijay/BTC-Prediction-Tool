# Timeframe VALUE / Elimination Analysis — source=pyth — 2026-06-21

History span: 9.6 days. Win% = directional hit on committed UP/DOWN. MODEL = committed lean (lean_source != fallback); a horizon has a *directional edge* only if model Wilson-LB > 50% with n >= 200. Tradeable markets: [5, 15] (5m/15m). DB read: live (db was free).

## Per-horizon value
| hz | market? | n | rounds/day | win % | Wilson-LB | model n | model win % | model LB | fb win % |
|---|---|---|---|---|---|---|---|---|---|
| 1m | no | 6729 | 702.4 | 50.1 | 48.9 | 277 | 48.0 | 42.2 | 50.2 |
| 3m | no | 2224 | 232.1 | 49.6 | 47.6 | 784 | 49.1 | 45.6 | 49.9 |
| 5m | YES | 1454 | 151.8 | 50.1 | 47.5 | 711 | 50.6 | 47.0 | 49.5 |
| 7m | no | 902 | 94.1 | 50.2 | 47.0 | 412 | 51.5 | 46.6 | 49.2 |
| 10m | no | 620 | 64.7 | 49.7 | 45.8 | 312 | 49.7 | 44.2 | 49.7 |
| 15m | YES | 470 | 49.1 | 50.6 | 46.1 | 338 | 50.3 | 45.0 | 51.5 |
| 30m | no | 159 | 16.6 | 47.8 | 40.2 | 146 | 47.9 | 40.0 | 46.2 |

## Verdict per horizon
| hz | verdict | reason |
|---|---|---|
| 1m | **OPTIONAL** | no market + coin-flip direction; KEEP only for fastest feedback / densest P(hold) snapshots |
| 3m | **REMOVE** | no market + direction ~coin-flip (LB 45.6% <= 50) -> pure training/label cost |
| 5m | **KEEP** | tradeable Polymarket market; direction ~coin-flip (value=P(hold)+band) |
| 7m | **REMOVE** | no market + direction ~coin-flip (LB 46.6% <= 50) -> pure training/label cost |
| 10m | **REMOVE** | no market + direction ~coin-flip (LB 44.2% <= 50) -> pure training/label cost |
| 15m | **KEEP** | tradeable Polymarket market; direction ~coin-flip (value=P(hold)+band) |
| 30m | **REMOVE** | no market + direction ~coin-flip (LB 40.0% <= 50) -> pure training/label cost |

## Bottom line
- **KEEP:** 5m, 15m  (tradeable market or a real directional edge)
- **OPTIONAL:** 1m  (no market/edge; keep only for fastest feedback + P(hold) density)
- **REMOVE:** 3m, 7m, 10m, 30m  (no market, coin-flip direction, pure cost)
- Pruning the REMOVE set drops **4/7 horizons (~57%)** of per-horizon head training + matrix labeling, with **no accuracy lost** (they carry no market and no directional edge).
- Direction is a coin-flip at EVERY horizon, so a non-tradeable horizon can NOT inform a tradeable one (stacking coin-flips != signal). The fine-scale value lives in P(hold) late-entry + L2 microstructure, not in small-TF direction models.

_Recommended keep-set: {1m(optional), 5m, 15m} — leanest tool, the two tradeable markets plus the densest feedback clock. Removing 3m, 7m, 10m, 30m is free speed._