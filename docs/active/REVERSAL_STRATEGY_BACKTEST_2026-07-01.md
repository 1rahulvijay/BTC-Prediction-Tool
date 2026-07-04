# Reversal / Round-Trip Strategy Backtest — 2026-07-01

> **RETRACTED:** This report predates the causal touch-bar correction. Its touch-context scores and
> proxy-profit wording must not be used for betting. See `PROFITABILITY_AND_BETTING_VALIDATION_2026-07-01.md`.

Data: **1m matrix**, 518,400 bars, 360 days. Anchor = window-open (Polymarket price-to-beat). 80/20 time split. **P&L is BTC-reversion (a PROXY) — real Polymarket P&L needs the share mispriced vs these odds after costs.** Direction = coin-flip (null); the edge is reversal selection + timing.


## 5m windows — primary barrier $30  (n=103,619)
- Base rates: direction-UP **0.495** (≈coin-flip null) · touch $30 **0.896** · round-trip **0.279** · **fade reach-anchor 0.314**
- **NULL check — predicting settled direction:** ENSEMBLE AUC **0.530** (≈0.50 confirms the coin-flip; do not trade it).

### Model bake-off — fade reach-anchor ($30, graded AT the touch; the profit target)
| model | AUC | win@top25% | win@top10% | win@top5% |
|---|---|---|---|---|
| lgb | 0.772 | 0.610 | 0.703 | 0.767 |
| catboost | 0.772 | 0.613 | 0.706 | 0.766 |
| xgb | 0.772 | 0.609 | 0.702 | 0.774 |
| ENSEMBLE | 0.772 | 0.612 | 0.699 | 0.768 |
| histgb | 0.771 | 0.610 | 0.706 | 0.770 |
| logreg | 0.764 | 0.599 | 0.686 | 0.752 |
| rf | 0.755 | 0.588 | 0.678 | 0.748 |
| extratrees | 0.691 | 0.496 | 0.573 | 0.662 |

**P&L — fade at 1:1 (label R/R; TP=anchor & stop are both $30 from the touch; breakeven 50%), $1 cost/trade:**
- **@P≥0.55:** 2622 trades (14.1% of touches), win **0.677** (LB 0.659) vs breakeven 0.50 → edge **+0.177**, avg **$+9.6**/trade, total **$+25,278** → ✅ PROFITABLE (proxy)
- **@P≥0.60:** 1715 trades (9.2% of touches), win **0.703** (LB 0.681) vs breakeven 0.50 → edge **+0.203**, avg **$+11.2**/trade, total **$+19,195** → ✅ PROFITABLE (proxy)
- **@P≥0.65:** 1051 trades (5.7% of touches), win **0.752** (LB 0.725) vs breakeven 0.50 → edge **+0.252**, avg **$+14.1**/trade, total **$+14,819** → ✅ PROFITABLE (proxy)
- _sensitivity @P≥0.60, wider 2:1 stop (breakeven 66.7%): win 0.703 → +EV. On Polymarket you BUY the cheap losing side (~35-45c) and TP at ~50c, which is BETTER than 1:1 → lower breakeven than 50%._

### Best trading windows ($30 fade reach-anchor by time)
- **by hour:** hour=17 fade=0.40 touch=0.95 vol_z=-0.01 · hour=18 fade=0.39 touch=0.95 vol_z=-0.04 · hour=16 fade=0.39 touch=0.95 vol_z=+0.24
- **by block4h:** block4h=4 fade=0.39 touch=0.95 vol_z=+0.04 · block4h=5 fade=0.32 touch=0.89 vol_z=-0.01 · block4h=0 fade=0.31 touch=0.91 vol_z=+0.11
- **by dow:** dow=0 fade=0.37 touch=0.96 vol_z=+0.07 · dow=1 fade=0.36 touch=0.96 vol_z=+0.08 · dow=4 fade=0.36 touch=0.95 vol_z=+0.07

### Latest 5m signal → **SKIP (low reach-anchor odds)**  (model P(reach-anchor)=0.21, regime hurst=0.39, chop=0.57, vol_z=-0.57)

## 15m windows — primary barrier $50  (n=34,499)
- Base rates: direction-UP **0.496** (≈coin-flip null) · touch $50 **0.941** · round-trip **0.354** · **fade reach-anchor 0.362**
- **NULL check — predicting settled direction:** ENSEMBLE AUC **0.535** (≈0.50 confirms the coin-flip; do not trade it).

### Model bake-off — fade reach-anchor ($50, graded AT the touch; the profit target)
| model | AUC | win@top25% | win@top10% | win@top5% |
|---|---|---|---|---|
| ENSEMBLE | 0.720 | 0.587 | 0.669 | 0.707 |
| catboost | 0.718 | 0.588 | 0.680 | 0.698 |
| histgb | 0.717 | 0.582 | 0.659 | 0.707 |
| lgb | 0.715 | 0.582 | 0.655 | 0.682 |
| xgb | 0.714 | 0.580 | 0.670 | 0.673 |
| logreg | 0.714 | 0.575 | 0.655 | 0.701 |
| rf | 0.710 | 0.587 | 0.644 | 0.664 |
| extratrees | 0.676 | 0.544 | 0.596 | 0.611 |

**P&L — fade at 1:1 (label R/R; TP=anchor & stop are both $50 from the touch; breakeven 50%), $1 cost/trade:**
- **@P≥0.55:** 946 trades (14.6% of touches), win **0.625** (LB 0.593) vs breakeven 0.50 → edge **+0.125**, avg **$+11.5**/trade, total **$+10,854** → ✅ PROFITABLE (proxy)
- **@P≥0.60:** 491 trades (7.6% of touches), win **0.692** (LB 0.650) vs breakeven 0.50 → edge **+0.192**, avg **$+18.2**/trade, total **$+8,959** → ✅ PROFITABLE (proxy)
- **@P≥0.65:** 227 trades (3.5% of touches), win **0.740** (LB 0.679) vs breakeven 0.50 → edge **+0.240**, avg **$+23.0**/trade, total **$+5,223** → ✅ PROFITABLE (proxy)
- _sensitivity @P≥0.60, wider 2:1 stop (breakeven 66.7%): win 0.692 → +EV. On Polymarket you BUY the cheap losing side (~35-45c) and TP at ~50c, which is BETTER than 1:1 → lower breakeven than 50%._

### Best trading windows ($50 fade reach-anchor by time)
- **by hour:** hour=17 fade=0.43 touch=0.97 vol_z=+0.33 · hour=18 fade=0.42 touch=0.98 vol_z=+0.05 · hour=20 fade=0.41 touch=0.94 vol_z=-0.15
- **by block4h:** block4h=4 fade=0.41 touch=0.97 vol_z=+0.22 · block4h=0 fade=0.37 touch=0.95 vol_z=+0.20 · block4h=3 fade=0.37 touch=0.94 vol_z=+0.26
- **by dow:** dow=2 fade=0.41 touch=0.98 vol_z=+0.15 · dow=1 fade=0.40 touch=0.98 vol_z=+0.15 · dow=3 fade=0.40 touch=0.98 vol_z=+0.20

### Latest 15m signal → **SKIP (low reach-anchor odds)**  (model P(reach-anchor)=0.39, regime hurst=0.45, chop=0.56, vol_z=-0.49)

## Honest verdict
- **Direction is a coin-flip** (null AUC ≈0.50) — never traded. Predicting UP/DOWN from the anchor is dead.
- **The fade is decided AT THE TOUCH, not at open** — grading with the overshoot/spring context lifts the reach-anchor AUC well above the window-open version. The edge is real and concentrated in the top touches.
- **Reward:risk is the whole game.** The label is a symmetric 1:1 fade (breakeven 50%); on Polymarket you buy the cheap losing side (~35-45c) and take profit near 50c, which is BETTER than 1:1 → an even lower breakeven. A wider 2:1 stop needs 66.7% and is much harder.
- **Still a PROXY.** This is BTC-price reversion. Real Polymarket P&L needs the actual share ask mispriced vs these odds after costs (recorder-gated). This sizes the OPPORTUNITY and the best windows, not proven profit.
- **When to trade:** the best-window rows (hour / 4h-block / weekday) show where reversals cluster + volume peaks — be selective there.
