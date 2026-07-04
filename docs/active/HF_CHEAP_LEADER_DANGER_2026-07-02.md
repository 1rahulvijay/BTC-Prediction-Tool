# Cheap-Leader: Valid vs Dangerous — 2026-07-02

Does the cheap-leader anomaly survive when conditioned on fragility? Round-level, cheap = leader trade-price 0.42–0.58 (n=3,383). Fragility = dist_vol_ratio (small lead vs vol = easy to flip). ⚠️ Trade prices, not asks; live /book required.

## Cheap leaders split by fragility
| bucket | result |
|---|---|
| FRAGILE (low dist/vol) | n=1,128 win=55.6% LB=52.7% price=50.3% gap=+5.3pp |
| MID | n=1,127 win=65.4% LB=62.6% price=50.4% gap=+15.0pp |
| SAFE (high dist/vol) | n=1,128 win=66.7% LB=63.9% price=50.4% gap=+16.3pp |

## Cheap leaders by other danger axes
| axis | result |
|---|---|
| early (secs≥180) | n=3,382 win=62.5% LB=60.9% price=50.4% gap=+12.2pp |
| late (secs<120) | n=0 (too few) |
| low vol (<median) | n=1,689 win=56.8% LB=54.5% price=50.3% gap=+6.5pp |
| high vol (≥median) | n=1,694 win=68.2% LB=66.0% price=50.4% gap=+17.9pp |

## Verdict
**PARTLY 'cheap for a reason' — the gap concentrates in SAFE leaders (+16.3pp) and shrinks for FRAGILE ones (+5.3pp).** So the market prices *some* of the cheapness as real flip-risk (CHEAP-DANGEROUS). The exploitable mispricing (if any) is the **SAFE-cheap** set — the cheap-VALID signal. A live head should require low fragility before flagging a cheap leader.

- **⚠️ Latency-artifact signature:** the edge is BIGGER in high vol (+17.9pp vs +6.5pp low vol) and exists only
  EARLY (cheap leaders vanish late — n=0 at secs<120). A real *mispricing* would shrink in high vol; a *feed-latency
  race* grows with it. Our "leader" is defined by **Binance**, but Polymarket settles on **Chainlink/Pyth** — so this
  profile is consistent with **Binance leading the Polymarket oracle** (largest divergence in high vol / early), i.e.
  a LATENCY RACE, not a calm mispricing you can buy. Exploitability then hinges on acting before the Polymarket ask
  catches up — which **only the live recorder can measure** (edge-duration 1s/3s/5s).
- Decision-support use (once live): classify a cheap leader **CHEAP-VALID** (safe lead, book fresh, edge persists) vs
  **CHEAP-DANGEROUS** (fragile / late-shock) vs **CHEAP-ILLIQUID** (bad book) vs **CHEAP-STALE-LATENCY** (edge gone in
  1–3s). Only CHEAP-VALID + fillable + persistent live ask is a candidate. All PAPER until the recorder proves it.