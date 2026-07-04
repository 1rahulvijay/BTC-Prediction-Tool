# HF Trade-Price Edge Analysis (leader-only) — 2026-07-02

First historical test of **our P(Hold) vs the actual traded price**, trading ONLY the currently-leading side (no fabricated symmetric probability). 37,789 leader snapshots over 5,893 rounds (Jan–Mar 2026), price=vwap30.
⚠️ **Executed-trade research, NOT fillability proof** — a trade price is not an executable resting ask; the live /book recorder is still required to prove executable edge.


## Buffer 1c  (edge = P(Hold_leader) − trade_price − 0.01)
| n | avg price | avg P(Hold) | win% | Wilson-LB | mean PnL/sh | PF | max-DD |
|---|---|---|---|---|---|---|---|
**snapshot-level (correlated — context only)** | 28165 | 0.493 | 0.757 | 0.748 | 0.743 | +0.255 | 3.53 | $-9.4 |
**ROUND-level (first qualifying/round — the honest number)** | 5295 | 0.473 | 0.634 | 0.596 | 0.583 | +0.123 | 1.81 | $-5.6 |

_round-level by cut:_
| cut | n | avg price | avg P(Hold) | win% | Wilson-LB | mean PnL/sh | PF | max-DD |
|---|---|---|---|---|---|---|---|---|
| 5m | 3979 | 0.475 | 0.624 | 0.610 | 0.595 | +0.135 | 1.86 | $-5.6 |
| 15m | 1316 | 0.464 | 0.667 | 0.553 | 0.526 | +0.089 | 1.65 | $-4.7 |
| secs_left=30 | 23 | 0.904 | 0.936 | 1.000 | 0.857 | +0.096 | 2215735480.82 | $0.0 |
| secs_left=60 | 163 | 0.670 | 0.851 | 0.755 | 0.683 | +0.084 | 2.48 | $-1.0 |
| secs_left=120 | 165 | 0.579 | 0.761 | 0.752 | 0.680 | +0.173 | 3.70 | $-1.4 |
| secs_left=180 | 502 | 0.518 | 0.677 | 0.631 | 0.588 | +0.113 | 1.76 | $-3.9 |
| secs_left=240 | 3282 | 0.463 | 0.607 | 0.602 | 0.585 | +0.139 | 1.85 | $-6.1 |
| secs_left=360 | 102 | 0.551 | 0.756 | 0.696 | 0.601 | +0.145 | 2.64 | $-1.4 |
| secs_left=540 | 185 | 0.497 | 0.688 | 0.595 | 0.523 | +0.098 | 1.71 | $-3.8 |
| secs_left=720 | 873 | 0.399 | 0.614 | 0.471 | 0.438 | +0.072 | 1.44 | $-4.7 |

## Buffer 2c  (edge = P(Hold_leader) − trade_price − 0.02)
| n | avg price | avg P(Hold) | win% | Wilson-LB | mean PnL/sh | PF | max-DD |
|---|---|---|---|---|---|---|---|
**snapshot-level (correlated — context only)** | 27593 | 0.491 | 0.761 | 0.751 | 0.746 | +0.260 | 3.63 | $-8.9 |
**ROUND-level (first qualifying/round — the honest number)** | 5269 | 0.470 | 0.637 | 0.596 | 0.583 | +0.127 | 1.84 | $-5.5 |

_round-level by cut:_
| cut | n | avg price | avg P(Hold) | win% | Wilson-LB | mean PnL/sh | PF | max-DD |
|---|---|---|---|---|---|---|---|---|
| 5m | 3973 | 0.475 | 0.628 | 0.612 | 0.597 | +0.137 | 1.88 | $-5.5 |
| 15m | 1296 | 0.452 | 0.665 | 0.548 | 0.521 | +0.096 | 1.71 | $-4.7 |
| secs_left=30 | 21 | 0.891 | 0.927 | 1.000 | 0.845 | +0.109 | 2296735169.64 | $0.0 |
| secs_left=60 | 161 | 0.639 | 0.839 | 0.739 | 0.666 | +0.100 | 2.57 | $-1.3 |
| secs_left=120 | 171 | 0.562 | 0.755 | 0.743 | 0.672 | +0.181 | 3.58 | $-2.3 |
| secs_left=180 | 600 | 0.511 | 0.672 | 0.630 | 0.591 | +0.119 | 1.78 | $-4.4 |
| secs_left=240 | 3162 | 0.463 | 0.610 | 0.604 | 0.587 | +0.141 | 1.87 | $-5.5 |
| secs_left=360 | 108 | 0.553 | 0.752 | 0.713 | 0.621 | +0.160 | 2.92 | $-0.8 |
| secs_left=540 | 203 | 0.492 | 0.685 | 0.596 | 0.527 | +0.104 | 1.74 | $-3.3 |
| secs_left=720 | 843 | 0.388 | 0.615 | 0.463 | 0.429 | +0.075 | 1.47 | $-5.6 |

## Buffer 3c  (edge = P(Hold_leader) − trade_price − 0.03)
| n | avg price | avg P(Hold) | win% | Wilson-LB | mean PnL/sh | PF | max-DD |
|---|---|---|---|---|---|---|---|
**snapshot-level (correlated — context only)** | 27136 | 0.489 | 0.763 | 0.753 | 0.748 | +0.263 | 3.70 | $-9.4 |
**ROUND-level (first qualifying/round — the honest number)** | 5244 | 0.467 | 0.639 | 0.597 | 0.584 | +0.131 | 1.87 | $-5.5 |

_round-level by cut:_
| cut | n | avg price | avg P(Hold) | win% | Wilson-LB | mean PnL/sh | PF | max-DD |
|---|---|---|---|---|---|---|---|---|
| 5m | 3962 | 0.474 | 0.631 | 0.616 | 0.600 | +0.142 | 1.92 | $-5.5 |
| 15m | 1282 | 0.445 | 0.664 | 0.541 | 0.514 | +0.096 | 1.70 | $-4.7 |
| secs_left=30 | 20 | 0.877 | 0.925 | 1.000 | 0.839 | +0.123 | 2468725839.24 | $0.0 |
| secs_left=60 | 156 | 0.613 | 0.830 | 0.724 | 0.649 | +0.111 | 2.72 | $-1.1 |
| secs_left=120 | 192 | 0.554 | 0.748 | 0.750 | 0.684 | +0.196 | 3.73 | $-1.4 |
| secs_left=180 | 667 | 0.506 | 0.671 | 0.631 | 0.594 | +0.125 | 1.80 | $-3.8 |
| secs_left=240 | 3063 | 0.461 | 0.613 | 0.606 | 0.589 | +0.146 | 1.92 | $-5.5 |
| secs_left=360 | 111 | 0.547 | 0.750 | 0.703 | 0.612 | +0.155 | 2.71 | $-1.2 |
| secs_left=540 | 210 | 0.486 | 0.687 | 0.600 | 0.533 | +0.114 | 1.84 | $-2.6 |
| secs_left=720 | 825 | 0.383 | 0.615 | 0.453 | 0.420 | +0.070 | 1.44 | $-6.0 |

## Buffer 5c  (edge = P(Hold_leader) − trade_price − 0.05)
| n | avg price | avg P(Hold) | win% | Wilson-LB | mean PnL/sh | PF | max-DD |
|---|---|---|---|---|---|---|---|
**snapshot-level (correlated — context only)** | 26152 | 0.486 | 0.768 | 0.756 | 0.751 | +0.270 | 3.84 | $-9.4 |
**ROUND-level (first qualifying/round — the honest number)** | 5213 | 0.462 | 0.645 | 0.601 | 0.588 | +0.139 | 1.95 | $-5.0 |

_round-level by cut:_
| cut | n | avg price | avg P(Hold) | win% | Wilson-LB | mean PnL/sh | PF | max-DD |
|---|---|---|---|---|---|---|---|---|
| 5m | 3947 | 0.471 | 0.637 | 0.620 | 0.605 | +0.149 | 1.99 | $-5.0 |
| 15m | 1266 | 0.436 | 0.668 | 0.543 | 0.516 | +0.107 | 1.82 | $-3.7 |
| secs_left=30 | 20 | 0.798 | 0.874 | 0.900 | 0.699 | +0.102 | 3.02 | $-0.5 |
| secs_left=60 | 164 | 0.604 | 0.830 | 0.726 | 0.653 | +0.122 | 2.86 | $-1.9 |
| secs_left=120 | 224 | 0.545 | 0.745 | 0.710 | 0.647 | +0.165 | 2.63 | $-1.7 |
| secs_left=180 | 825 | 0.502 | 0.671 | 0.656 | 0.623 | +0.154 | 2.04 | $-3.7 |
| secs_left=240 | 2852 | 0.456 | 0.617 | 0.607 | 0.589 | +0.151 | 1.97 | $-4.4 |
| secs_left=360 | 123 | 0.523 | 0.735 | 0.715 | 0.630 | +0.193 | 3.21 | $-1.2 |
| secs_left=540 | 228 | 0.480 | 0.688 | 0.588 | 0.523 | +0.108 | 1.74 | $-2.7 |
| secs_left=720 | 777 | 0.369 | 0.617 | 0.445 | 0.411 | +0.076 | 1.50 | $-6.3 |

## Verdict
**THESIS ALIVE (research) — round-level @2c: 5269 rounds, win 0.596 (LB 0.583) vs avg price 0.470, ROI +0.270, mean PnL +0.127/share.**
- **Alive** → our P(Hold) disagrees with the market's traded price profitably; NEXT: confirm the SAME buckets on live /book ask + depth + edge-duration (this is NOT fillability proof).

_A trade price is not an executable resting ask; positive = research signal only, not tradeable proof. Live /book ask/depth/edge-duration + settlement is the only executable-edge proof._