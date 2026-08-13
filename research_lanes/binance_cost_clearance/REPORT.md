# BINANCE_COST_CLEARANCE_V1

**Verdict: CLOSE LANE** for held-to-horizon Binance directional trading below ~30 minutes at
taker costs. Not "difficult" — arithmetically impossible, at any accuracy.

Run 2026-08-13 · `research_lanes/binance_cost_clearance/run.py` · trains nothing

---

## Question

Not "will BTC go up." A directional model can be right and still lose money if the move it
predicts is smaller than the round trip. So:

> What directional accuracy would a model need, at each horizon, for expected value to be
> positive after costs?

This is an empirical property of the price series. Nothing is fitted, so nothing can be
overfitted — and the answer bounds *every* model that trades this instrument this way.

## Data

| | |
|---|---|
| source | `data/research_matrix_1m.parquet` |
| rows | 518,400 one-minute bars |
| span | 360 days (`requested_days: 360`) |
| independence unit | UTC day (360 days) |

Every interval below is a **day-block bootstrap**: whole UTC days resampled with replacement,
500 draws. A row bootstrap would be wrong here — overlapping 1-minute observations of a
5-minute forward move share four minutes of price path, so resampling rows reports the
precision of a sample size the data does not contain.

Reported break-even accuracies use the **lower** bound on mean |move|, which is the
conservative direction.

## The move distribution

| horizon | median bps | mean bps | mean LCB | P(\|move\|>12bps) | break-even acc @12bps | base rate up |
|---|---:|---:|---:|---:|---:|---:|
| 1m | 2.4 | 3.8 | 3.6 | 5.3% | **impossible** | 48.3% |
| 2m | 3.5 | 5.5 | 5.2 | 10.9% | **impossible** | 49.2% |
| 3m | 4.3 | 6.8 | 6.4 | 15.6% | **impossible** | 49.5% |
| **5m** | **5.6** | **8.7** | **8.3** | **23.0%** | **impossible** | 49.5% |
| 10m | 8.0 | 12.4 | 11.7 | 34.9% | **impossible** | 49.6% |
| **15m** | **9.8** | **15.2** | **14.4** | **42.4%** | **91.7%** | 49.7% |
| 30m | 13.8 | 21.4 | 20.3 | 55.1% | 79.6% | 49.9% |
| 60m | 19.5 | 30.2 | 28.7 | 65.9% | 70.9% | 49.9% |
| 120m | 27.5 | 42.8 | 40.5 | 74.8% | 64.8% | 50.1% |

"Impossible" is literal: mean |move| at 5m is 8.3 bps against a 12 bps round trip, so a
**100%-accurate** model still loses 3.7 bps per trade. There is no accuracy that fixes it.

## Break-even accuracy surface

Directional accuracy required for EV > 0, by horizon and round-trip cost:

| horizon | 2bps | 4bps | 6bps | 8bps | **12bps** | 16bps | 20bps |
|---|---:|---:|---:|---:|---:|---:|---:|
| 1m | 77.5% | impossible | impossible | impossible | impossible | impossible | impossible |
| 2m | 69.2% | 88.3% | impossible | impossible | impossible | impossible | impossible |
| 3m | 65.6% | 81.2% | 96.8% | impossible | impossible | impossible | impossible |
| **5m** | 62.1% | 74.2% | 86.2% | 98.3% | **impossible** | impossible | impossible |
| 10m | 58.5% | 67.1% | 75.6% | 84.1% | impossible | impossible | impossible |
| **15m** | 57.0% | 63.9% | 70.9% | 77.8% | **91.7%** | impossible | impossible |
| 30m | 54.9% | 59.9% | 64.8% | 69.7% | 79.6% | 89.4% | 99.3% |
| 60m | 53.5% | 57.0% | 60.5% | 63.9% | 70.9% | 77.9% | 84.9% |
| 120m | 52.5% | 54.9% | 57.4% | 59.9% | 64.8% | 69.7% | 74.7% |

## EV per trade at the shipped 12 bps round trip

`StrategyBase.assumed_round_trip_bps = 12`.

| horizon | 52% | 55% | 58% | 60% | 65% | 70% |
|---|---:|---:|---:|---:|---:|---:|
| 1m | -11.9 | -11.6 | -11.4 | -11.3 | -10.9 | -10.5 |
| **5m** | -11.7 | -11.2 | -10.7 | -10.3 | -9.5 | **-8.7** |
| **15m** | -11.4 | -10.6 | -9.7 | -9.1 | -7.7 | **-6.2** |
| 30m | -11.2 | -10.0 | -8.8 | -7.9 | -5.9 | -3.9 |
| 60m | -10.9 | -9.1 | -7.4 | -6.3 | -3.4 | -0.5 |
| 120m | -10.4 | -7.9 | -5.5 | -3.9 | +0.2 | +4.2 |

A **70%-accurate 5-minute model loses 8.7 bps per trade.** The first positive cell in the
entire table is 120m at 65%.

## What this rules out

Held-to-horizon Binance directional trading below ~30 minutes at 12 bps. The app's active
horizons — 5m and 15m — sit in the impossible and near-impossible regions. No improvement to
the direction classifier changes this, because the constraint is the size of the move, not the
sign of it.

## What this does NOT rule out

Stated explicitly, because the result is easy to over-read:

1. **Polymarket.** Completely different payoff. A binary contract bought at 0.61 settles at
   1.00 or 0.00 — the BTC *move* is not the payoff. Edge there is `P_true − P_market`, and
   5 cents on a $1 contract is 500 bps of notional. **Nothing here bounds the Polymarket
   lane.** That is the app's primary venue, and this lane says nothing about it.
2. **Asymmetric payoffs.** `EV = (2p−1)·E|move| − cost` assumes you capture the full move when
   right and lose the full move when wrong. A strategy with a tight stop and a wide target
   truncates one tail. That changes the arithmetic and is not tested here.
3. **Maker execution.** At 4 bps, 5m needs 74.2% and 15m needs 63.9%. Still high, no longer
   impossible. The cost column matters more than the model.
4. **Conditional selection.** These are unconditional moves. A model that trades only the 23%
   of 5m windows exceeding 12 bps faces different economics — but it must first predict
   *which* windows those are, which is the volatility-expansion lane, not this one.
5. **Longer horizons.** 120m is positive at 65% accuracy. Untested whether 65% is reachable.

## Attacks applied

| attack | status |
|---|---|
| day-block bootstrap (not row) | applied, 500 draws, 360 independent days |
| conservative bound used | yes — break-even computed from mean LCB |
| cost stress | yes — 2 to 20 bps grid |
| horizon sweep | yes — 1m to 120m, no assumption that 5/15m is right |
| overfitting | not possible — nothing is fitted |

Not yet applied: regime breakdown, second-era replication, PnL concentration. Those matter for
a *strategy*; this lane measures a property of the series, so they would refine rather than
overturn it.

## Caveat on the data window

The matrix is currently **360 days**, not the 1000 the app requests — it was rebuilt by
`BTC_AutoFinetune` mid-retrain (see `TRAINING_PIPELINE_CONCURRENCY_INCIDENT_2026-08-13.md`).
360 days is ample for a move distribution, and 360 independent days is a real sample, but the
window is not the one the app trains on. Re-run after the next matrix build to confirm the
numbers hold on 900 days.

## Recommended next lanes

This result reorders the queue:

1. **`POLYMARKET_RESIDUAL_V1`** — the venue this lane does not bound, and where the payoff
   structure actually admits edge. Target `logit(p_true) = logit(p_market) + f(X)`.
2. **`VOLATILITY_EXPANSION_V1`** — if only 23% of 5m windows clear costs, predicting *which*
   ones is worth more than predicting direction.
3. **`FUNDING_BASIS_CARRY_V1`** — return source independent of BTC direction entirely.

Rebuilding the Binance directional classifier is not on this list.
