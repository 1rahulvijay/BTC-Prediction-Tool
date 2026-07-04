# Binance bookDepth Liquidity Probe — 2026-07-02

Free Binance futures **bookDepth** (30s aggregate depth bands) joined to the 1m matrix (90d, join coverage 25%). Does resting-liquidity state add lift OVER an rv baseline on clean forward labels? Causal, temporal 70/30, shuffled-null. Research only — nothing wired unless it clears.


## Target: big_move  (n=127,319, base rate 16.6%)
- rv baseline AUC **0.747**  ·  +bookDepth AUC **0.747**  ·  LIFT **-0.000**  (null95 +0.000, p=0.390)
- top univariate bookDepth features: imb_1=0.532, imb_2=0.524, depth_slope=0.518, imb_0p2=0.516
- → **no lift over rv (liquidity state redundant here)**

## Target: big_drop  (n=127,319, base rate 37.2%)
- rv baseline AUC **0.707**  ·  +bookDepth AUC **0.706**  ·  LIFT **-0.002**  (null95 +0.000, p=1.000)
- top univariate bookDepth features: imb_1=0.519, imb_2=0.516, depth_slope=0.513, depth_chg_2m=0.504
- → **no lift over rv (liquidity state redundant here)**

## Verdict
- bookDepth is free, real, 30s aggregate depth — a *liquidity-context* layer, not tick L2.
- Wire the features into the live model ONLY if a target shows a significant lift above; otherwise keep as optional display context. The true microstructure edge still needs the record-forward diff-depth WS.
- If SIGNAL: the live model would need the equivalent live depth feed (Binance depth WS) to deploy — the 30s historical files are for backtesting/probing, not live serving.