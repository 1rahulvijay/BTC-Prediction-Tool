# Reversal / Round-Trip Strategy Backtest — 2026-07-01

> **RETRACTED:** This report predates the causal touch-bar correction. Its touch-context scores and
> proxy-profit wording must not be used for betting. See `PROFITABILITY_AND_BETTING_VALIDATION_2026-07-01.md`.

Data: **30s (30s)**, 518,400 bars, 180 days. Anchor = window-open (Polymarket price-to-beat). 80/20 time split. **P&L is BTC-reversion (a PROXY) — real Polymarket P&L needs the share mispriced vs these odds after costs.** Direction = coin-flip (null); the edge is reversal selection + timing.


## 5m windows — primary barrier $30  (n=51,779)
- Base rates: direction-UP **0.500** (≈coin-flip null) · touch $30 **0.884** · round-trip **0.260** · **fade reach-anchor 0.300**
- **NULL check — predicting settled direction:** ENSEMBLE AUC **0.520** (≈0.50 confirms the coin-flip; do not trade it).

### Model bake-off — fade reach-anchor ($30, graded AT the touch; the profit target)
| model | AUC | win@top25% | win@top10% | win@top5% |
|---|---|---|---|---|
| ENSEMBLE | 0.731 | 0.563 | 0.633 | 0.694 |
| catboost | 0.731 | 0.556 | 0.628 | 0.694 |
| xgb | 0.730 | 0.556 | 0.631 | 0.678 |
| histgb | 0.729 | 0.562 | 0.627 | 0.685 |
| lgb | 0.728 | 0.551 | 0.631 | 0.687 |
| logreg | 0.727 | 0.557 | 0.639 | 0.691 |
| rf | 0.721 | 0.558 | 0.626 | 0.681 |
| extratrees | 0.676 | 0.507 | 0.564 | 0.613 |

**P&L — fade at 1:1 (label R/R; TP=anchor & stop are both $30 from the touch; breakeven 50%), $1 cost/trade:**
- **@P≥0.55:** 1005 trades (11.0% of touches), win **0.631** (LB 0.601) vs breakeven 0.50 → edge **+0.131**, avg **$+6.9**/trade, total **$+6,885** → ✅ PROFITABLE (proxy)
- **@P≥0.60:** 534 trades (5.8% of touches), win **0.682** (LB 0.641) vs breakeven 0.50 → edge **+0.182**, avg **$+9.9**/trade, total **$+5,286** → ✅ PROFITABLE (proxy)
- **@P≥0.65:** 273 trades (3.0% of touches), win **0.744** (LB 0.689) vs breakeven 0.50 → edge **+0.244**, avg **$+13.6**/trade, total **$+3,717** → ✅ PROFITABLE (proxy)
- _sensitivity @P≥0.60, wider 2:1 stop (breakeven 66.7%): win 0.682 → +EV. On Polymarket you BUY the cheap losing side (~35-45c) and TP at ~50c, which is BETTER than 1:1 → lower breakeven than 50%._

### Best trading windows ($30 fade reach-anchor by time)
- **by hour:** hour=18 fade=0.38 touch=0.94 vol_z=+0.02 · hour=17 fade=0.38 touch=0.95 vol_z=+0.04 · hour=16 fade=0.36 touch=0.95 vol_z=+0.22
- **by block4h:** block4h=4 fade=0.37 touch=0.94 vol_z=+0.07 · block4h=0 fade=0.31 touch=0.91 vol_z=+0.19 · block4h=5 fade=0.30 touch=0.88 vol_z=+0.07
- **by dow:** dow=0 fade=0.35 touch=0.95 vol_z=+0.14 · dow=3 fade=0.34 touch=0.96 vol_z=+0.14 · dow=2 fade=0.34 touch=0.95 vol_z=+0.13

### Latest 5m signal → **SKIP (low reach-anchor odds)**  (model P(reach-anchor)=0.13, regime hurst=0.45, chop=0.55, vol_z=+0.10)

## 15m windows — primary barrier $50  (n=17,219)
- Base rates: direction-UP **0.493** (≈coin-flip null) · touch $50 **0.928** · round-trip **0.329** · **fade reach-anchor 0.351**
- **NULL check — predicting settled direction:** ENSEMBLE AUC **0.531** (≈0.50 confirms the coin-flip; do not trade it).

### Model bake-off — fade reach-anchor ($50, graded AT the touch; the profit target)
| model | AUC | win@top25% | win@top10% | win@top5% |
|---|---|---|---|---|
| ENSEMBLE | 0.684 | 0.543 | 0.618 | 0.635 |
| catboost | 0.683 | 0.549 | 0.618 | 0.642 |
| logreg | 0.682 | 0.544 | 0.589 | 0.616 |
| histgb | 0.677 | 0.543 | 0.605 | 0.623 |
| rf | 0.677 | 0.543 | 0.555 | 0.654 |
| lgb | 0.677 | 0.544 | 0.602 | 0.604 |
| xgb | 0.673 | 0.546 | 0.592 | 0.610 |
| extratrees | 0.658 | 0.506 | 0.527 | 0.560 |

**P&L — fade at 1:1 (label R/R; TP=anchor & stop are both $50 from the touch; breakeven 50%), $1 cost/trade:**
- **@P≥0.55:** 321 trades (10.0% of touches), win **0.617** (LB 0.563) vs breakeven 0.50 → edge **+0.117**, avg **$+10.7**/trade, total **$+3,429** → ✅ PROFITABLE (proxy)
- **@P≥0.60:** 122 trades (3.8% of touches), win **0.648** (LB 0.559) vs breakeven 0.50 → edge **+0.148**, avg **$+13.8**/trade, total **$+1,678** → ✅ PROFITABLE (proxy)
- **@P≥0.65:** 45 trades (1.4% of touches), win **0.689** (LB 0.543) vs breakeven 0.50 → edge **+0.189**, avg **$+17.9**/trade, total **$+805** → ✅ PROFITABLE (proxy)
- _sensitivity @P≥0.60, wider 2:1 stop (breakeven 66.7%): win 0.648 → not +EV. On Polymarket you BUY the cheap losing side (~35-45c) and TP at ~50c, which is BETTER than 1:1 → lower breakeven than 50%._

### Best trading windows ($50 fade reach-anchor by time)
- **by hour:** hour=17 fade=0.42 touch=0.97 vol_z=+0.39 · hour=18 fade=0.41 touch=0.98 vol_z=+0.12 · hour=16 fade=0.41 touch=0.97 vol_z=+0.59
- **by block4h:** block4h=4 fade=0.41 touch=0.97 vol_z=+0.26 · block4h=0 fade=0.35 touch=0.94 vol_z=+0.37 · block4h=5 fade=0.35 touch=0.93 vol_z=-0.01
- **by dow:** dow=4 fade=0.40 touch=0.97 vol_z=+0.29 · dow=3 fade=0.39 touch=0.98 vol_z=+0.25 · dow=2 fade=0.39 touch=0.98 vol_z=+0.23

### Latest 15m signal → **SKIP (low reach-anchor odds)**  (model P(reach-anchor)=0.40, regime hurst=0.45, chop=0.54, vol_z=-0.56)

## Honest verdict
- **Direction is a coin-flip** (null AUC ≈0.50) — never traded. Predicting UP/DOWN from the anchor is dead.
- **The fade is decided AT THE TOUCH, not at open** — grading with the overshoot/spring context lifts the reach-anchor AUC well above the window-open version. The edge is real and concentrated in the top touches.
- **Reward:risk is the whole game.** The label is a symmetric 1:1 fade (breakeven 50%); on Polymarket you buy the cheap losing side (~35-45c) and take profit near 50c, which is BETTER than 1:1 → an even lower breakeven. A wider 2:1 stop needs 66.7% and is much harder.
- **Still a PROXY.** This is BTC-price reversion. Real Polymarket P&L needs the actual share ask mispriced vs these odds after costs (recorder-gated). This sizes the OPPORTUNITY and the best windows, not proven profit.
- **When to trade:** the best-window rows (hour / 4h-block / weekday) show where reversals cluster + volume peaks — be selective there.
