# Timeframe / Time-of-Day / Per-Day Performance — Polymarket / Pyth — 2026-06-21

Source = `pyth`. Win-rate = directional hit on UP/DOWN. MODEL = committed lean; FALLBACK = two-way tilt (~coin-flip). Wilson-LB = 95% lower bound. **All clock buckets are in `Europe/Berlin`.**

## 1. Best timeframe (last 24h)
| hz | n | win % | Wilson-LB | model n | model win % | fb n | fb win % |
|---|---|---|---|---|---|---|---|
| 1m | 1111 | 51.6 | 48.6 | 0 | – | 1111 | 51.6 |
| 3m | 366 | 50.5 | 45.4 | 15 | 53.3 | 351 | 50.4 |
| 5m | 220 | 50.9 | 44.3 | 4 | 50.0 | 216 | 50.9 |
| 7m | 154 | 51.9 | 44.1 | 10 | 60.0 | 144 | 51.4 |
| 10m | 109 | 50.5 | 41.2 | 6 | 0.0 | 103 | 53.4 |
| 15m | 71 | 56.3 | 44.8 | 13 | 53.8 | 58 | 56.9 |
| 30m | 35 | 34.3 | 20.8 | 25 | 36.0 | 10 | 30.0 |
_24h is small — rankings noisy; weigh Wilson-LB and the model/fallback split._

## 2. Best time-of-day (Europe/Berlin, all history, n=12451)
| Berlin hour | n | win % | model n | model win % |
|---|---|---|---|---|
| 00:00 | 440 | 49.8 | 133 | 49.6 |
| 01:00 | 448 | 51.1 | 101 | 53.5 |
| 02:00 | 445 | 50.6 | 99 | 46.5 |
| 03:00 | 492 | 48.6 | 112 | 43.8 |
| 04:00 | 549 | 47.7 | 86 | 50.0 |
| 05:00 | 482 | 53.1 | 63 | 54.0 |
| 06:00 | 401 | 47.9 | 49 | 42.9 |
| 07:00 | 494 | 49.0 | 59 | 50.8 |
| 08:00 | 539 | 47.7 | 91 | 50.5 |
| 09:00 | 445 | 45.4 | 80 | 40.0 |
| 10:00 | 321 | 50.8 | 81 | 55.6 |
| 11:00 | 353 | 51.3 | 112 | 53.6 |
| 12:00 | 427 | 52.2 | 59 | 59.3 |
| 13:00 | 513 | 47.0 | 78 | 53.8 |
| 14:00 | 466 | 47.4 | 104 | 51.0 |
| 15:00 | 525 | 53.3 | 193 | 48.2 |
| 16:00 | 507 | 50.7 | 215 | 51.6 |
| 17:00 | 636 | 47.6 | 273 | 43.6 |
| 18:00 | 712 | 52.4 | 195 | 50.8 |
| 19:00 | 751 | 50.1 | 209 | 46.9 |
| 20:00 | 722 | 52.4 | 200 | 54.0 |
| 21:00 | 691 | 49.6 | 151 | 51.7 |
| 22:00 | 620 | 50.6 | 118 | 49.2 |
| 23:00 | 472 | 51.1 | 118 | 54.2 |

**Best 5-hour windows (≥30 rounds):**
| window | n | win % | Wilson-LB | model n | model win % |
|---|---|---|---|---|---|
| 18:00–23:00 | 3496 | 51.0 | 49.4 | 873 | 50.5 |
| 20:00–01:00 | 2945 | 50.8 | 49.0 | 720 | 51.9 |
| 15:00–20:00 | 3131 | 50.8 | 49.0 | 1085 | 47.9 |

**15:00–20:00 Europe/Berlin vs rest:** window **50.8%** (n=3131, LB 49.0%, model 47.9%) vs rest **49.7%** — window is stronger.

## 2b. Time-of-day in 4-hour blocks (Europe/Berlin)

**All horizons:**
| block | n | win % | Wilson-LB | model win % |
|---|---|---|---|---|
| 00:00–04:00 | 1825 | 50.0 | 47.7 | 48.3 |
| 04:00–08:00 | 1926 | 49.4 | 47.2 | 49.8 |
| 08:00–12:00 | 1658 | 48.4 | 46.0 | 50.3 |
| 12:00–16:00 | 1931 | 50.0 | 47.7 | 51.4 |
| 16:00–20:00 | 2606 | 50.2 | 48.3 | 47.9 |
| 20:00–24:00 | 2505 | 50.9 | 49.0 | 52.5 |
- **best block: 20:00–24:00** at 50.9% (n=2505, Wilson-LB 49.0%) — note: LB < 50%, not a real edge yet.

**5m only (shortest tradeable):**
| block | n | win % | Wilson-LB | model win % |
|---|---|---|---|---|
| 00:00–04:00 | 194 | 52.6 | 45.6 | 48.3 |
| 04:00–08:00 | 209 | 45.9 | 39.3 | 50.0 |
| 08:00–12:00 | 216 | 47.2 | 40.7 | 52.9 |
| 12:00–16:00 | 247 | 51.8 | 45.6 | 51.9 |
| 16:00–20:00 | 304 | 49.7 | 44.1 | 49.8 |
| 20:00–24:00 | 273 | 52.4 | 46.5 | 51.1 |
- **best block: 00:00–04:00** at 52.6% (n=194, Wilson-LB 45.6%) — note: LB < 50%, not a real edge yet.

**15m only:**
| block | n | win % | Wilson-LB | model win % |
|---|---|---|---|---|
| 00:00–04:00 | 64 | 54.7 | 42.6 | 60.5 |
| 04:00–08:00 | 65 | 50.8 | 38.9 | 57.9 |
| 08:00–12:00 | 71 | 42.3 | 31.5 | 38.2 |
| 12:00–16:00 | 81 | 46.9 | 36.4 | 50.8 |
| 16:00–20:00 | 97 | 47.4 | 37.8 | 42.7 |
| 20:00–24:00 | 89 | 61.8 | 51.4 | 59.1 |
- **best block: 20:00–24:00** at 61.8% (n=89, Wilson-LB 51.4%)

## 3. Per-day (Europe/Berlin, all history)
| day | n | win % | Wilson-LB | model n | model win % |
|---|---|---|---|---|---|
| 2026-06-12 | 613 | 51.5 | 47.6 | 315 | 52.1 |
| 2026-06-13 | 1469 | 49.8 | 47.2 | 233 | 47.2 |
| 2026-06-14 | 1773 | 49.7 | 47.4 | 471 | 47.8 |
| 2026-06-15 | 1671 | 49.9 | 47.5 | 585 | 49.6 |
| 2026-06-16 | 1642 | 49.3 | 46.9 | 738 | 51.6 |
| 2026-06-18 | 263 | 47.5 | 41.6 | 66 | 51.5 |
| 2026-06-19 | 2175 | 49.3 | 47.2 | 358 | 50.6 |
| 2026-06-20 | 1194 | 50.8 | 47.9 | 145 | 49.7 |
| 2026-06-21 | 1651 | 51.1 | 48.6 | 68 | 39.7 |

- **Best day:** 2026-06-12 at 51.5% (n=613). Day-to-day swing is mostly which regime dominated that day, not a repeatable signal.

**By day-of-week:**
| weekday | n | win % | Wilson-LB | model win % |
|---|---|---|---|---|
| Mon | 1671 | 49.9 | 47.5 | 49.6 |
| Tue | 1642 | 49.3 | 46.9 | 51.6 |
| Thu | 263 | 47.5 | 41.6 | 51.5 |
| Fri | 2788 | 49.8 | 47.9 | 51.3 |
| Sat | 2663 | 50.2 | 48.3 | 48.1 |
| Sun | 3424 | 50.4 | 48.7 | 46.8 |

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
| 2026-06-19 | 164 | 50.0 | 49.2 | ✓ better |
| 2026-06-20 | 450 | 51.3 | 50.4 | ✓ better |
| 2026-06-21 | 7 | 28.6 | 51.2 | ✗ worse |
- Block was **>50% on 5/9 days**; **beat the rest of that day on 6/9 days**. A real time-of-day edge should beat the rest of the day on most days — 6/9 is consistent.

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
- Block was **>50% on 7/8 days**; **beat the rest of that day on 6/8 days**. A real time-of-day edge should beat the rest of the day on most days — 6/8 is consistent.

## 4. Last 100 rounds — 5m & 15m (Polymarket / Pyth)

**5m — last 100: 47 WON / 53 LOST (47%)**
| time (Berlin) | beat | close | dir | result | lean | regime |
|---|---|---|---|---|---|---|
| 2026-06-21 20:00 | 64078.2 | 63904.4 | UP | LOST | fallback | TRENDING_UP |
| 2026-06-21 19:55 | 64099.3 | 64078.2 | UP | LOST | fallback | TRENDING_UP |
| 2026-06-21 19:50 | 64107.1 | 64099.3 | UP | LOST | fallback | TRENDING_UP |
| 2026-06-21 19:45 | 64050.8 | 64107.1 | UP | WON | fallback | TRENDING_UP |
| 2026-06-21 19:40 | 64008.0 | 64050.8 | UP | WON | fallback | TRENDING_DOWN |
| 2026-06-21 19:35 | 64003.4 | 64008.0 | UP | WON | fallback | TRENDING_DOWN |
| 2026-06-21 19:30 | 64026.1 | 64003.4 | UP | LOST | fallback | LOW_VOLATILITY |
| 2026-06-21 19:25 | 64079.3 | 64026.1 | UP | LOST | fallback | LOW_VOLATILITY |
| 2026-06-21 19:15 | 64131.1 | 64096.4 | UP | LOST | fallback | TRENDING_UP |
| 2026-06-21 19:10 | 64133.9 | 64131.1 | UP | LOST | fallback | TRENDING_UP |
| 2026-06-21 19:05 | 64121.8 | 64133.9 | UP | WON | fallback | TRENDING_UP |
| 2026-06-21 19:00 | 64067.4 | 64121.8 | UP | WON | fallback | TRENDING_UP |
| 2026-06-21 18:55 | 64066.9 | 64067.4 | UP | WON | fallback | TRENDING_UP |
| 2026-06-21 18:50 | 64068.3 | 64066.9 | UP | LOST | fallback | TRENDING_UP |
| 2026-06-21 18:45 | 64033.5 | 64068.3 | UP | WON | fallback | TRENDING_DOWN |
| 2026-06-21 18:40 | 63985.5 | 64033.5 | UP | WON | fallback | TRENDING_DOWN |
| 2026-06-21 18:35 | 63979.2 | 63985.5 | UP | WON | fallback | TRENDING_DOWN |
| 2026-06-21 18:30 | 64107.3 | 63979.2 | UP | LOST | fallback | HIGH_VOLATILITY |
| 2026-06-21 18:25 | 64142.2 | 64107.3 | UP | LOST | fallback | TRENDING_UP |
| 2026-06-21 18:20 | 64098.3 | 64142.2 | UP | WON | fallback | TRENDING_DOWN |
| 2026-06-21 18:15 | 64063.3 | 64098.3 | UP | WON | fallback | TRENDING_DOWN |
| 2026-06-21 18:10 | 64111.3 | 64063.3 | UP | LOST | fallback | RANGE |
| 2026-06-21 18:05 | 64163.5 | 64111.3 | UP | LOST | fallback | RANGE |
| 2026-06-21 18:00 | 64147.7 | 64163.5 | UP | WON | fallback | TRENDING_DOWN |
| 2026-06-21 17:55 | 64166.4 | 64147.7 | UP | LOST | fallback | TRENDING_DOWN |
_Full 100 → `data\last_rounds_pyth_5m.csv`_

**15m — last 100: 60 WON / 40 LOST (60%)**
| time (Berlin) | beat | close | dir | result | lean | regime |
|---|---|---|---|---|---|---|
| 2026-06-21 19:45 | 64050.8 | 64078.2 | UP | WON | fallback | TRENDING_UP |
| 2026-06-21 19:30 | 64026.1 | 64050.8 | UP | WON | fallback | LOW_VOLATILITY |
| 2026-06-21 19:15 | 64131.1 | 64026.1 | UP | LOST | fallback | TRENDING_UP |
| 2026-06-21 19:00 | 64067.4 | 64131.1 | UP | WON | fallback | TRENDING_UP |
| 2026-06-21 18:45 | 64033.5 | 64067.4 | UP | WON | fallback | TRENDING_DOWN |
| 2026-06-21 18:30 | 64107.3 | 64033.5 | UP | LOST | model | HIGH_VOLATILITY |
| 2026-06-21 18:15 | 64063.3 | 64107.3 | UP | WON | fallback | TRENDING_DOWN |
| 2026-06-21 18:00 | 64147.7 | 64063.3 | UP | LOST | fallback | TRENDING_DOWN |
| 2026-06-21 17:45 | 64219.9 | 64147.7 | UP | LOST | model | HIGH_VOLATILITY |
| 2026-06-21 17:30 | 64182.5 | 64219.9 | UP | WON | fallback | TRENDING_UP |
| 2026-06-21 16:15 | 63994.9 | 63957.9 | DOWN | WON | fallback | TRENDING_DOWN |
| 2026-06-21 16:00 | 64068.0 | 63994.9 | DOWN | WON | fallback | TRENDING_UP |
| 2026-06-21 15:45 | 63972.6 | 64068.0 | DOWN | LOST | fallback | TRENDING_DOWN |
| 2026-06-21 15:30 | 64087.0 | 63972.6 | DOWN | WON | model | LOW_VOLATILITY |
| 2026-06-21 15:15 | 64035.6 | 64087.0 | DOWN | LOST | fallback | RANGE |
| 2026-06-21 15:00 | 64121.7 | 64035.6 | DOWN | WON | fallback | TRENDING_UP |
| 2026-06-21 14:45 | 63975.6 | 64121.7 | UP | WON | model | TRENDING_UP |
| 2026-06-21 14:30 | 63927.1 | 63975.6 | UP | WON | model | TRENDING_DOWN |
| 2026-06-21 14:15 | 64003.3 | 63927.1 | UP | LOST | model | TRENDING_DOWN |
| 2026-06-21 14:00 | 64082.6 | 64003.3 | UP | LOST | model | TRENDING_DOWN |
| 2026-06-21 13:45 | 64212.3 | 64082.6 | UP | LOST | fallback | RANGE |
| 2026-06-21 13:30 | 64307.9 | 64212.3 | UP | LOST | fallback | RANGE |
| 2026-06-21 13:15 | 64347.1 | 64307.9 | UP | LOST | fallback | RANGE |
| 2026-06-21 13:00 | 64286.3 | 64347.1 | DOWN | LOST | fallback | RANGE |
| 2026-06-21 12:45 | 64267.8 | 64286.3 | UP | WON | fallback | TRENDING_UP |
_Full 100 → `data\last_rounds_pyth_15m.csv`_