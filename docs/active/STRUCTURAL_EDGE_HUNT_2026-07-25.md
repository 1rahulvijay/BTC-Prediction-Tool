# Structural Edge Hunt — Complement Arb & Opening Drift (2026-07-25)

Two STRUCTURAL (non-conditional) hunts on Kaggle archive (7): 14,226 settled BTC 5m rounds, 4,267,718 executable tick observations. Both ask about market MECHANICS, not round-picking — the only species that has survived testing here. Fees `0.07·p·(1−p)` per leg; round-level; Wilson lower bounds.

## TEST 1 — Complement arbitrage (riskless by construction)

Buy BOTH sides when `UP_ask + DOWN_ask + fees < $1.00`; exactly one leg pays $1 at settlement, so the profit is locked at entry. No model, no direction, no hold risk.

- Ticks scanned (both sides quoted): **3,780,501**
- Ticks where the book crossed into guaranteed profit: **21** (0.0006%)
- Distinct rounds with ≥1 crossing: **3** of 14,226 (0.02%)
- Locked profit per share-pair: mean **36.50c**, median 42.23c, max 61.11c
- Total if every crossing were taken once at 1 pair: **$7.66**
- When they happen: median 148s left (25th–75th pct 105s–181s)

- Crossings worth >2c: **20** (mean 38.25c)

**Read — VERDICT: NO TRADEABLE ARBITRAGE, and the few hits are almost certainly broken books.**

21 crossings in 3.78M ticks (0.0006%), touching 3 of 14,226 rounds. The decisive tell is the SIZE:
a mean locked profit of **36.5c** (max 61c) is economically impossible in a functioning two-sided
market — it would require UP and DOWN to be simultaneously quoted around 20–30c while one of them
was certain to pay $1. No maker leaves that standing for a full second. These are **stale /
collapsed / one-sided book artifacts** (liquidity pulled, a quote frozen from an earlier state), not
fillable prints. A genuine arb would look like 0.5–2c crossings appearing hundreds of times; instead
**20 of the 21 hits are the implausible >2c kind** and there is no population of small, credible
ones — the signature of data artifacts, not of an inefficiency.

**Consequences:** (1) do not build an arb scanner — makers hold UP+DOWN ≥ $1 after fees essentially
always (99.9994% of ticks); (2) this is independent evidence that the **stale-book problem is real
and matters** — the same artifact that fakes an arb would fake an "amazing cheap leader." The live
rule's ≤5s freshness check and complement-sanity gate are exactly what stop the app from buying
these ghosts, and this test quantifies how often such ghosts appear.

## TEST 2 — Next-round opening drift (cross-round momentum)

After round N settles, does round N+1's OPENING book underprice the continuation side (the side that just won)? Entry at the real opening ask (first tick ≥200s left), hold to settlement, fees included, one decision per round. The reversal side is the exact mirror; if the market is efficient at the open, BOTH should sit at ≈0 EV.

Consecutive round pairs: **13,018**

| arm | rounds | win% (Wilson LB) | avg ask | EV/share |
|---|---|---|---|---|
| CONTINUATION (buy prev winner's side) | 13,018 | 49.0% (LB 48.2%) | 50.0c | **-2.74c** (LB -3.60c) |
| REVERSAL (mirror control) | 13,018 | 51.0% (LB 50.1%) | 51.3c | **-2.08c** (LB -2.94c) |
| RANDOM side (noise control) | 13,018 | 50.4% (LB 49.5%) | 50.6c | **-1.98c** (LB -2.84c) |

### Split by the market's own opening confidence

| opening ask on the continuation side | rounds | win% (LB) | EV/share |
|---|---|---|---|
| 0.02–0.45 | 996 | 37.3% (LB 34.4%) | **-4.80c** |
| 0.45–0.50 | 4,719 | 47.0% (LB 45.6%) | **-2.27c** |
| 0.50–0.55 | 5,970 | 50.5% (LB 49.3%) | **-2.86c** |
| 0.55–0.98 | 1,333 | 57.9% (LB 55.2%) | **-2.25c** |

### Verdict
**NEGATIVE — the opening book is efficient** (EV -2.74c, LB -3.60c). The market fully resets between rounds: no cross-round momentum to harvest. Clean kill; the boundary-lag species does NOT generalize from the expiry boundary to the round-open boundary.

## Honest limits
- Top-of-book only: no size/depth, so fillability at the quoted asks is unproven.
- 1-second cadence: sub-second crossings and opening prints are invisible.
- No latency model: the round open is the fastest tape of the round.
- Historical window; live replication on the recorder remains the only real proof.
- Nothing here is wired to any live behavior. PAPER research only.