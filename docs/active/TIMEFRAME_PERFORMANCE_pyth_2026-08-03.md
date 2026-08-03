# Timeframe / Time-of-Day / Per-Day Performance — Polymarket / Pyth — 2026-08-03

Source = `pyth`. Win-rate = directional hit on UP/DOWN. MODEL = committed lean; FALLBACK = two-way tilt (~coin-flip). Wilson-LB = 95% lower bound. **All clock buckets are in `Europe/Berlin`.**

## 1. Best timeframe (last 24h)
_No resolved rounds in the last 24h._

## 2. Best time-of-day (Europe/Berlin, all history, n=13814)
| Berlin hour | n | win % | model n | model win % |
|---|---|---|---|---|
| 00:00 | 506 | 50.4 | 170 | 53.5 |
| 01:00 | 499 | 49.7 | 134 | 50.0 |
| 02:00 | 495 | 50.5 | 126 | 46.8 |
| 03:00 | 545 | 49.2 | 146 | 46.6 |
| 04:00 | 605 | 48.1 | 115 | 50.4 |
| 05:00 | 521 | 53.4 | 72 | 54.2 |
| 06:00 | 454 | 47.6 | 68 | 44.1 |
| 07:00 | 557 | 47.8 | 94 | 43.6 |
| 08:00 | 594 | 48.5 | 117 | 53.0 |
| 09:00 | 492 | 45.9 | 107 | 43.0 |
| 10:00 | 369 | 50.7 | 116 | 51.7 |
| 11:00 | 398 | 49.5 | 135 | 51.1 |
| 12:00 | 464 | 51.9 | 68 | 61.8 |
| 13:00 | 548 | 47.6 | 90 | 53.3 |
| 14:00 | 513 | 47.0 | 128 | 48.4 |
| 15:00 | 569 | 52.7 | 229 | 48.0 |
| 16:00 | 555 | 49.9 | 252 | 49.2 |
| 17:00 | 682 | 47.5 | 317 | 43.8 |
| 18:00 | 774 | 51.2 | 245 | 46.9 |
| 19:00 | 806 | 49.3 | 254 | 46.5 |
| 20:00 | 846 | 52.6 | 234 | 53.4 |
| 21:00 | 793 | 49.7 | 195 | 52.3 |
| 22:00 | 687 | 50.7 | 158 | 48.1 |
| 23:00 | 542 | 51.5 | 150 | 54.7 |

**Best 5-hour windows (≥30 rounds):**
| window | n | win % | Wilson-LB | model n | model win % |
|---|---|---|---|---|---|
| 20:00–01:00 | 3374 | 51.0 | 49.3 | 907 | 52.5 |
| 19:00–00:00 | 3674 | 50.7 | 49.1 | 991 | 50.8 |
| 18:00–23:00 | 3906 | 50.7 | 49.1 | 1086 | 49.4 |

**15:00–20:00 Europe/Berlin vs rest:** window **50.0%** (n=3386, LB 48.3%, model 46.7%) vs rest **49.7%** — window is stronger.

## 2b. Time-of-day in 4-hour blocks (Europe/Berlin)

**All horizons:**
| block | n | win % | Wilson-LB | model win % |
|---|---|---|---|---|
| 00:00–04:00 | 2045 | 49.9 | 47.8 | 49.5 |
| 04:00–08:00 | 2137 | 49.2 | 47.1 | 48.1 |
| 08:00–12:00 | 1853 | 48.5 | 46.2 | 49.9 |
| 12:00–16:00 | 2094 | 49.8 | 47.7 | 50.9 |
| 16:00–20:00 | 2817 | 49.5 | 47.6 | 46.4 |
| 20:00–24:00 | 2868 | 51.1 | 49.3 | 52.2 |
- **best block: 20:00–24:00** at 51.1% (n=2868, Wilson-LB 49.3%) — note: LB < 50%, not a real edge yet.

**5m only (shortest tradeable):**
| block | n | win % | Wilson-LB | model win % |
|---|---|---|---|---|
| 00:00–04:00 | 360 | 51.7 | 46.5 | 51.1 |
| 04:00–08:00 | 366 | 47.0 | 41.9 | 46.8 |
| 08:00–12:00 | 365 | 47.7 | 42.6 | 50.8 |
| 12:00–16:00 | 370 | 51.6 | 46.5 | 53.5 |
| 16:00–20:00 | 463 | 46.7 | 42.2 | 46.2 |
| 20:00–24:00 | 480 | 51.5 | 47.0 | 50.0 |
- **best block: 00:00–04:00** at 51.7% (n=360, Wilson-LB 46.5%) — note: LB < 50%, not a real edge yet.

**15m only:**
| block | n | win % | Wilson-LB | model win % |
|---|---|---|---|---|
| 00:00–04:00 | 118 | 50.8 | 41.9 | 56.6 |
| 04:00–08:00 | 119 | 47.1 | 38.3 | 51.4 |
| 08:00–12:00 | 117 | 45.3 | 36.6 | 42.4 |
| 12:00–16:00 | 121 | 43.8 | 35.3 | 44.9 |
| 16:00–20:00 | 149 | 44.3 | 36.6 | 40.3 |
| 20:00–24:00 | 152 | 58.6 | 50.6 | 58.2 |
- **best block: 20:00–24:00** at 58.6% (n=152, Wilson-LB 50.6%)

## 3. Per-day (Europe/Berlin, all history)
| day | n | win % | Wilson-LB | model n | model win % |
|---|---|---|---|---|---|
| 2026-06-12 | 613 | 51.5 | 47.6 | 315 | 52.1 |
| 2026-06-13 | 1469 | 49.8 | 47.2 | 233 | 47.2 |
| 2026-06-14 | 1773 | 49.7 | 47.4 | 471 | 47.8 |
| 2026-06-15 | 1671 | 49.9 | 47.5 | 585 | 49.6 |
| 2026-06-16 | 1642 | 49.3 | 46.9 | 738 | 51.6 |
| 2026-06-18 | 263 | 47.5 | 41.6 | 66 | 51.5 |
| 2026-06-19 | 2175 | 49.2 | 47.1 | 358 | 50.6 |
| 2026-06-20 | 1194 | 50.8 | 47.9 | 145 | 49.7 |
| 2026-06-21 | 1758 | 51.3 | 49.0 | 69 | 40.6 |
| 2026-06-22 | 142 | 47.2 | 39.2 | 119 | 47.1 |
| 2026-06-23 | 369 | 40.9 | 36.0 | 265 | 42.3 |
| 2026-06-27 | 88 | 50.0 | 39.8 | 29 | 44.8 |
| 2026-06-28 | 362 | 51.1 | 46.0 | 110 | 48.2 |
| 2026-06-29 | 139 | 46.8 | 38.7 | 131 | 46.6 |
| 2026-06-30 | 18 | 50.0 | 29.0 | 16 | 50.0 |
| 2026-07-01 | 9 | 77.8 | 45.3 | 4 | 100.0 |
| 2026-07-03 | 85 | 57.6 | 47.0 | 56 | 60.7 |
| 2026-07-04 | 44 | 47.7 | 33.8 | 10 | 70.0 |

- **Best day:** 2026-07-01 at 77.8% (n=9). Day-to-day swing is mostly which regime dominated that day, not a repeatable signal.

**By day-of-week:**
| weekday | n | win % | Wilson-LB | model win % |
|---|---|---|---|---|
| Mon | 1952 | 49.5 | 47.3 | 48.7 |
| Tue | 2029 | 47.8 | 45.6 | 49.2 |
| Wed | 9 | 77.8 | 45.3 | 100.0 |
| Thu | 263 | 47.5 | 41.6 | 51.5 |
| Fri | 2873 | 50.0 | 48.2 | 52.0 |
| Sat | 2795 | 50.2 | 48.3 | 48.4 |
| Sun | 3893 | 50.6 | 49.0 | 47.1 |

_Time-of-day / per-day need many days to be trustworthy; with limited history each bucket is thin. Re-run as data grows and weigh the Wilson-LB (a band straddling 50% = no real edge)._

## 3b. Is the 20:00–24:00 Europe/Berlin edge repeating? (per-day)

**All horizons — the 20:00–24:00 Europe/Berlin block, each day:**
| day | block n | block win % | rest-of-day % | vs rest |
|---|---|---|---|---|
| 2026-06-12 | 313 | 53.0 | 50.0 | ✓ better |
| 2026-06-13 | 288 | 52.4 | 49.1 | ✓ better |
| 2026-06-14 | 445 | 50.8 | 49.3 | ✓ better |
| 2026-06-15 | 282 | 53.5 | 49.2 | ✓ better |
| 2026-06-16 | 293 | 48.5 | 49.4 | ✗ worse |
| 2026-06-18 | 263 | 47.5 | – | – |
| 2026-06-19 | 164 | 49.4 | 49.2 | ✓ better |
| 2026-06-20 | 450 | 51.3 | 50.4 | ✓ better |
| 2026-06-21 | 114 | 53.5 | 51.2 | ✓ better |
| 2026-06-22 | 59 | 50.8 | 44.6 | ✓ better |
| 2026-06-23 | 52 | 48.1 | 39.7 | ✓ better |
| 2026-06-27 | 49 | 53.1 | 46.2 | ✓ better |
| 2026-06-28 | 60 | 50.0 | 51.3 | ✗ worse |
| 2026-06-30 | 18 | 50.0 | – | – |
| 2026-07-03 | 18 | 66.7 | 55.2 | ✓ better |
- Block was **>50% on 9/15 days**; **beat the rest of that day on 11/15 days**. A real time-of-day edge should beat the rest of the day on most days — 11/15 is consistent.

**15m only — the 20:00–24:00 Europe/Berlin block, each day:**
| day | block n | block win % | rest-of-day % | vs rest |
|---|---|---|---|---|
| 2026-06-12 | 13 | 61.5 | 41.7 | ✓ better |
| 2026-06-13 | 10 | 30.0 | 38.5 | ✗ worse |
| 2026-06-14 | 15 | 73.3 | 53.5 | ✓ better |
| 2026-06-15 | 10 | 60.0 | 45.8 | ✓ better |
| 2026-06-16 | 10 | 60.0 | 38.3 | ✓ better |
| 2026-06-18 | 9 | 55.6 | – | – |
| 2026-06-19 | 6 | 83.3 | 51.4 | ✓ better |
| 2026-06-20 | 16 | 68.8 | 65.4 | ✓ better |
| 2026-06-21 | 3 | 33.3 | 54.4 | ✗ worse |
| 2026-06-22 | 14 | 50.0 | 35.0 | ✓ better |
| 2026-06-23 | 13 | 61.5 | 32.9 | ✓ better |
| 2026-06-27 | 12 | 58.3 | 60.0 | ✗ worse |
| 2026-06-28 | 14 | 50.0 | 48.6 | ✓ better |
| 2026-06-30 | 4 | 50.0 | – | – |
| 2026-07-03 | 3 | 66.7 | 47.1 | ✓ better |
- Block was **>50% on 10/15 days**; **beat the rest of that day on 10/15 days**. A real time-of-day edge should beat the rest of the day on most days — 10/15 is consistent.

## 4. Last 100 rounds — 5m & 15m (Polymarket / Pyth)

**5m — last 100: 55 WON / 45 LOST (55%)**
| time (Berlin) | beat | close | dir | result | lean | regime |
|---|---|---|---|---|---|---|
| 2026-07-04 12:40 | 62410.8 | 62426.9 | UP | WON | model | RANGE |
| 2026-07-04 12:35 | 62426.6 | 62407.5 | UP | LOST | fallback | RANGE |
| 2026-07-04 12:30 | 62440.2 | 62426.5 | UP | LOST | fallback | RANGE |
| 2026-07-04 12:25 | 62436.6 | 62440.2 | UP | WON | fallback | HIGH_VOLATILITY |
| 2026-07-04 12:20 | 62437.1 | 62436.6 | DOWN | WON | fallback | TRENDING_UP |
| 2026-07-04 12:15 | 62407.7 | 62437.5 | DOWN | LOST | fallback | TRENDING_DOWN |
| 2026-07-04 12:10 | 62407.2 | 62407.7 | DOWN | WON | fallback | TRENDING_DOWN |
| 2026-07-04 12:00 | 62434.2 | 62403.5 | DOWN | WON | fallback | TRENDING_UP |
| 2026-07-04 11:55 | 62437.0 | 62434.1 | DOWN | WON | fallback | TRENDING_UP |
| 2026-07-04 11:50 | 62431.0 | 62430.3 | DOWN | LOST | fallback | TRENDING_UP |
| 2026-07-04 11:45 | 62390.2 | 62421.2 | DOWN | LOST | fallback | TRENDING_DOWN |
| 2026-07-04 11:40 | 62383.7 | 62390.4 | DOWN | LOST | fallback | TRENDING_DOWN |
| 2026-07-04 11:35 | 62390.7 | 62385.2 | UP | LOST | fallback | HIGH_VOLATILITY |
| 2026-07-04 11:30 | 62406.7 | 62394.7 | DOWN | WON | fallback | TRENDING_DOWN |
| 2026-07-04 08:30 | 62359.7 | 62426.0 | DOWN | LOST | fallback | TRENDING_DOWN |
| 2026-07-04 08:25 | 62292.8 | 62336.5 | UP | WON | fallback | TRENDING_DOWN |
| 2026-07-04 08:20 | 62389.8 | 62292.3 | DOWN | WON | fallback | TRENDING_DOWN |
| 2026-07-04 08:10 | 62427.2 | 62407.5 | UP | LOST | fallback | HIGH_VOLATILITY |
| 2026-07-04 08:05 | 62431.1 | 62452.1 | UP | LOST | fallback | TRENDING_DOWN |
| 2026-07-04 08:00 | 62408.5 | 62431.1 | DOWN | LOST | fallback | TRENDING_DOWN |
| 2026-07-04 07:55 | 62416.2 | 62408.5 | UP | LOST | fallback | TRENDING_DOWN |
| 2026-07-04 01:15 | 62506.8 | 62558.0 | UP | WON | fallback | LOW_VOLATILITY |
| 2026-07-04 01:05 | 62512.0 | 62471.9 | UP | LOST | model | LOW_VOLATILITY |
| 2026-07-04 01:00 | 62516.4 | 62512.0 | UP | LOST | fallback | LOW_VOLATILITY |
| 2026-07-04 00:55 | 62534.8 | 62516.4 | UP | LOST | fallback | LOW_VOLATILITY |
_Full 100 → `data\last_rounds_pyth_5m.csv`_

**15m — last 100: 47 WON / 53 LOST (47%)**
| time (Berlin) | beat | close | dir | result | lean | regime |
|---|---|---|---|---|---|---|
| 2026-07-04 12:30 | 62440.2 | 62426.9 | DOWN | WON | model | RANGE |
| 2026-07-04 12:15 | 62407.7 | 62440.2 | DOWN | LOST | fallback | TRENDING_DOWN |
| 2026-07-04 12:00 | 62434.2 | 62403.5 | DOWN | WON | fallback | TRENDING_UP |
| 2026-07-04 11:45 | 62390.2 | 62434.3 | DOWN | LOST | fallback | TRENDING_DOWN |
| 2026-07-04 11:30 | 62406.7 | 62388.0 | DOWN | WON | model | TRENDING_DOWN |
| 2026-07-04 08:00 | 62408.5 | 62407.5 | DOWN | WON | fallback | TRENDING_DOWN |
| 2026-07-04 00:45 | 62554.2 | 62528.5 | UP | LOST | model | TRENDING_UP |
| 2026-07-04 00:30 | 62571.9 | 62546.1 | UP | LOST | model | LOW_VOLATILITY |
| 2026-07-04 00:00 | 62673.4 | 62559.3 | DOWN | WON | model | TRENDING_UP |
| 2026-07-03 23:15 | 62694.8 | 62577.0 | DOWN | WON | model | TRENDING_DOWN |
| 2026-07-03 23:00 | 62712.0 | 62694.8 | UP | LOST | model | TRENDING_UP |
| 2026-07-03 22:45 | 62645.6 | 62710.5 | UP | WON | model | TRENDING_UP |
| 2026-07-03 09:30 | 61687.6 | 61743.4 | UP | WON | model | HIGH_VOLATILITY |
| 2026-07-03 09:15 | 61625.3 | 61655.3 | DOWN | LOST | model | RANGE |
| 2026-07-03 09:00 | 61637.0 | 61624.6 | DOWN | WON | fallback | TRENDING_UP |
| 2026-07-03 08:45 | 61568.8 | 61658.2 | DOWN | LOST | fallback | TRENDING_DOWN |
| 2026-07-03 08:30 | 61646.0 | 61573.3 | DOWN | WON | model | RANGE |
| 2026-07-03 08:15 | 61655.8 | 61646.0 | DOWN | WON | model | TRENDING_DOWN |
| 2026-07-03 08:00 | 61624.2 | 61746.6 | DOWN | LOST | model | RANGE |
| 2026-07-03 07:45 | 61624.9 | 61623.8 | DOWN | LOST | model | TRENDING_UP |
| 2026-07-03 07:30 | 61557.3 | 61608.6 | DOWN | LOST | model | HIGH_VOLATILITY |
| 2026-07-03 07:15 | 61460.7 | 61575.0 | DOWN | LOST | fallback | TRENDING_UP |
| 2026-07-03 07:00 | 61373.8 | 61458.4 | DOWN | LOST | model | TRENDING_UP |
| 2026-07-03 06:45 | 61418.3 | 61373.7 | DOWN | WON | model | HIGH_VOLATILITY |
| 2026-07-03 05:00 | 61363.5 | 61275.2 | DOWN | WON | model | LOW_VOLATILITY |
_Full 100 → `data\last_rounds_pyth_15m.csv`_