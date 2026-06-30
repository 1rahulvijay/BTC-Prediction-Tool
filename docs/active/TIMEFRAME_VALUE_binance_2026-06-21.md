# Timeframe VALUE / Elimination Analysis — source=binance — 2026-06-21

History span: 0.0 days. Win% = directional hit on committed UP/DOWN. MODEL = committed lean (lean_source != fallback); a horizon has a *directional edge* only if model Wilson-LB > 50% with n >= 200. Tradeable markets: [5, 15] (5m/15m). DB read: live (db was free).

## Per-horizon value
| hz | market? | n | rounds/day | win % | Wilson-LB | model n | model win % | model LB | fb win % |
|---|---|---|---|---|---|---|---|---|---|
| 1m | no | 50 | 1358.5 | 56.0 | 42.3 | 0 | 0.0 | 0.0 | 56.0 |
| 3m | no | 16 | 434.7 | 37.5 | 18.5 | 0 | 0.0 | 0.0 | 37.5 |
| 5m | YES | 8 | 217.4 | 62.5 | 30.6 | 0 | 0.0 | 0.0 | 62.5 |
| 7m | no | 7 | 190.2 | 42.9 | 15.8 | 0 | 0.0 | 0.0 | 42.9 |
| 10m | no | 4 | 108.7 | 75.0 | 30.1 | 0 | 0.0 | 0.0 | 75.0 |
| 15m | YES | 3 | 81.5 | 33.3 | 6.1 | 0 | 0.0 | 0.0 | 33.3 |
| 30m | no | 1 | 27.2 | 100.0 | 20.7 | 1 | 100.0 | 20.7 | 0.0 |

## Verdict per horizon
| hz | verdict | reason |
|---|---|---|
| 1m | **OPTIONAL** | no market + coin-flip direction; KEEP only for fastest feedback / densest P(hold) snapshots |
| 3m | **REMOVE** | no market + direction ~coin-flip (LB 0.0% <= 50) -> pure training/label cost |
| 5m | **KEEP** | tradeable Polymarket market; direction ~coin-flip (value=P(hold)+band) |
| 7m | **REMOVE** | no market + direction ~coin-flip (LB 0.0% <= 50) -> pure training/label cost |
| 10m | **REMOVE** | no market + direction ~coin-flip (LB 0.0% <= 50) -> pure training/label cost |
| 15m | **KEEP** | tradeable Polymarket market; direction ~coin-flip (value=P(hold)+band) |
| 30m | **REMOVE** | no market + direction ~coin-flip (LB 20.7% <= 50) -> pure training/label cost |

## Bottom line
- **KEEP:** 5m, 15m  (tradeable market or a real directional edge)
- **OPTIONAL:** 1m  (no market/edge; keep only for fastest feedback + P(hold) density)
- **REMOVE:** 3m, 7m, 10m, 30m  (no market, coin-flip direction, pure cost)
- Pruning the REMOVE set drops **4/7 horizons (~57%)** of per-horizon head training + matrix labeling, with **no accuracy lost** (they carry no market and no directional edge).
- Direction is a coin-flip at EVERY horizon, so a non-tradeable horizon can NOT inform a tradeable one (stacking coin-flips != signal). The fine-scale value lives in P(hold) late-entry + L2 microstructure, not in small-TF direction models.

_Recommended keep-set: {1m(optional), 5m, 15m} — leanest tool, the two tradeable markets plus the densest feedback clock. Removing 3m, 7m, 10m, 30m is free speed._