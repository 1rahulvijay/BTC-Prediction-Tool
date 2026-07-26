# Simple causal stopping baselines (2026-07-25)

The gate the canonical plan puts in front of the dynamic-exit lane: **before any model, does any FROZEN causal stopping policy beat holding to settlement?** Seven pre-declared policies, no fitting. Entry pays ask+fee; every early exit pays bid-fee; a condition seen at quote *i* executes at quote *i+1* (~1.8s later), so no policy acts on information at the instant it appears.

## Entry at 240s left  (n = 3,817 rounds, 21 days)

| policy | mean EV | day-block LB | profit factor | weeks + | vs HOLD |
|---|---|---|---|---|---|
| HOLD | **-1.70c** | -2.77c | 0.92 | 0/4 | +0.00c |
| FIRST_+1c | **-3.32c** | -4.11c | 0.53 | 0/4 | -1.62c |
| FIRST_+2c | **-3.35c** | -4.16c | 0.57 | 0/4 | -1.64c |
| PERSIST_2 | **-3.39c** | -4.18c | 0.57 | 0/4 | -1.69c |
| MOMENTUM_REV | **-4.16c** | -4.52c | 0.29 | 0/4 | -2.46c |
| TIMEOUT_10S | **-4.12c** | -4.42c | 0.27 | 0/4 | -2.42c |
| TIMEOUT_30S | **-4.05c** | -4.59c | 0.43 | 0/4 | -2.35c |
| RANDOM | **-4.22c** | -4.97c | 0.63 | 0/4 | -2.52c |

*Hindsight ceiling (best exit in each round, unknowable in advance): **+19.00c** - this is what the '90% of rounds have a profitable exit' statistic actually measures. The gap between it and every policy above is the price of not knowing the future.*

## Entry at 60s left  (n = 3,379 rounds, 21 days)

| policy | mean EV | day-block LB | profit factor | weeks + | vs HOLD |
|---|---|---|---|---|---|
| HOLD | **-1.86c** | -2.93c | 0.87 | 0/4 | +0.00c |
| FIRST_+1c | **-3.23c** | -3.92c | 0.57 | 0/4 | -1.37c |
| FIRST_+2c | **-3.23c** | -4.10c | 0.61 | 0/4 | -1.37c |
| PERSIST_2 | **-3.12c** | -3.98c | 0.64 | 0/4 | -1.25c |
| MOMENTUM_REV | **-2.88c** | -3.22c | 0.54 | 0/4 | -1.02c |
| TIMEOUT_10S | **-3.04c** | -3.37c | 0.52 | 0/4 | -1.18c |
| TIMEOUT_30S | **-3.22c** | -4.02c | 0.66 | 0/4 | -1.36c |
| RANDOM | **-3.74c** | -4.53c | 0.58 | 0/4 | -1.88c |

*Hindsight ceiling (best exit in each round, unknowable in advance): **+10.70c** - this is what the '90% of rounds have a profitable exit' statistic actually measures. The gap between it and every policy above is the price of not knowing the future.*

## Verdict

**NO simple causal stopping policy beats holding to settlement with a positive lower bound.** Per the pre-declared gate, the dynamic-exit lane **stops here**: no ML, no survival model, no RL.

The reason is visible in the numbers: the hindsight ceiling is large and positive, yet every causal policy lands at or below HOLD. That is the signature of a phenomenon that exists in the *path* but not in any *observable state* available beforehand. Exiting early converts a settlement payoff into a spread crossing plus a second fee, and the ~1.8s delay removes whatever is left.

Same lesson as TP-or-settle and the maker test, now proven for ADAPTIVE rules too: **on this market every extra decision costs more than the information it acts on.**

## Limits
- Exits are top-of-book bids; `top_bid_size` was not recorded in this window, so exits assume 1 share. Real size makes every early-exit policy WORSE, never better.
- Quote cadence ~1.8s median: sub-second opportunities are invisible here.
- 5m rounds, 21 days, one decision per round per policy. Enough to kill, never to promote.

**Nothing here changes a threshold or promotes anything. PAPER research only.**