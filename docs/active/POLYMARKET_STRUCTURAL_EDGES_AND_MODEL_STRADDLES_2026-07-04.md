# Polymarket Structural Edges And Model Straddles

Date: 2026-07-04

Status: **research + forward paper execution only**. Nothing in this build submits a real order.

## Why this lane exists

Repeated tests showed that selecting apparently favourable directional rounds usually does not create
profit after the executable ask and fees. This lane therefore tests structurally different claims:

1. both contracts temporarily cost less than their guaranteed combined settlement value;
2. the boundary between consecutive rounds creates a delayed repricing;
3. a path model can select the minority of simultaneous straddles whose swings pay for both spreads;
4. a staged strategy can avoid paying for the second side until the opposite extreme actually occurs.

## Offline test

Run:

```powershell
.\run_polymarket_structural_edges_test.bat
```

Implementation:

- `backend/research/test_polymarket_structural_edges.py`
- kachoio BTC 5m archive: per-second executable UP/DOWN bid/ask and settled outcome;
- both taker entry fees and every early-exit fee are charged;
- chronological oldest 70% train / newest 30% test;
- five lightweight model families are fit one at a time and released from memory;
- each model's selection threshold is learned from a chronological calibration tail inside the 70%
  training period; the newest 30% test score distribution is never used to choose the threshold;
- all OOS trades and metrics are written under `data/research/polymarket_structural_edges/`.

The queued run waits for the active 1,500-day `train_heads.py --force` process to finish before loading
the archive. This prevents the research test from competing for the laptop's 16 GB RAM.

## Test 1: complement arbitrage

At one simultaneous second:

```text
all_in_cost = UP ask + DOWN ask + UP taker fee + DOWN taker fee
locked_margin = 1.00 - all_in_cost
```

Because exactly one contract settles at $1, a positive margin is riskless only if both asks can actually
be filled together. Candidates require at least one displayed share on both asks. The test records
same-second frequency, displayed common size, the best
margin per round, and whether the margin still exists one second later. A fleeting candidate is an alert,
not proven fillable profit; live promotion requires simultaneous L2/VWAP confirmation.

## Test 2: next-round opening drift

For contiguous 5m rounds, the previous round is called strong when the **observable market leader at
30 seconds remaining** has a bid of at least 75c and gained at least 20c between 240 and 30 seconds left.
The settled winner is deliberately not used to construct the signal. At the next round's first quote
(maximum 10-second delay), the test buys that prior leader at its executable ask, charges the taker fee,
and holds to settlement. The opposite side is evaluated as a placebo.

Promotion requires positive OOS EV, positive 95% lower-bound EV, and robustness across time. Accuracy
without positive ask-priced EV is a rejection.

## Test 3: simultaneous model straddle

This reproduces the frozen blind mechanics: first near-50/50 quote with 270-180 seconds left, both spreads
at most 2c, buy both asks, sell each leg at the first bid 20% above entry, otherwise settle. The historical
ungated result is **-10.7c per straddle**. Five classifiers predict whether both exits occur, but qualify
only if selected OOS trades have positive EV and beat the ungated strategy. AUC alone does not qualify.

The live `MODEL_STRADDLE_LIVE_V1` already implements the simultaneous model version as a paper shadow,
gated by path style `two_sided` and `P(round-trip) >= 35%`; no duplicate rule was added.

## Test 4: sequential model reversal

The new `MODEL_SEQUENTIAL_REVERSAL_V1` is a separate forward paper strategy:

1. The path head identifies `FADE-SETUP` after the first BTC barrier touch.
2. The first fade grade must report `P(reach anchor) >= 55%`.
3. Buy the now-cheap opposite contract at its live ask.
4. Add the other contract only after an opposite touch whose independent fade grade is also at least 55%.
5. Each purchased leg exits at a live bid 20% above entry or settles.

This differs from a simultaneous straddle because leg two's spread and fee are paid only after the
predicted return begins. It differs from `MODEL_FADE_LIVE_V1` because both approved legs are retained in
one auditable staged position.

### Accounting guarantees

- cumulative asks and entry fees update atomically when leg two is added;
- each leg stores its entry, exit bid and exit fee;
- state persists in DuckDB and restores after restart;
- settlement values only contracts actually purchased;
- the Trades tab names it `Model sequential reversal`;
- execution remains paper-only.

## Models and causal features

Offline selectors: Logistic Regression, HistGradientBoosting, Random Forest, Extra Trees, and Gradient
Boosting. They use only data known at the decision second: seconds remaining, both bids/asks/spreads,
bid/ask complements, contract mids, ask-size log ratio, and timestamp-based 5/15/30/60-second changes in
mids and ask complement. Settlement and future quotes never enter a feature row. Model score thresholds
come from a chronological calibration tail inside training, never from the newest 30% test distribution.

## Promotion rules

No result changes Champion or enables real-money execution automatically. Promotion requires positive
newest-30% EV after asks/fees/bid exits, positive 95% lower-bound EV, improvement over the ungated
baseline, independent live L2 reproduction, enough samples, and no one-day or one-bucket concentration.
Until every gate passes, the action remains **PAPER / WAIT**, not BUY.
