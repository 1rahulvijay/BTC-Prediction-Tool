# Polymarket Market-Response Test

Date: 2026-07-01  
Status: completed, descriptive PAPER research  
Implementation: `backend/research/test_polymarket_market_response.py`  
Runner: `run_polymarket_market_response_test.bat`  
Output: `data/research/polymarket_market_response/`

## Purpose

This test took the highest-priority ideas from `MODEL_RESULTS_INTERPRETATION_AND_NEXT_PREDICTIONS_2026-07-01.md` and tested everything the current forward recorder can support without changing the live app:

- edge duration and first profitable exit;
- Polymarket quote response after a BTC shock;
- UP+DOWN complement-book parity;
- current-side market versus model calibration;
- top-of-book and 1/2/5-cent depth availability;
- exact data blockers for fair value, fill and maker/taker models.

The analyzer is read-only. It does not train or replace models, write to the recorder database, restart the application or place orders.

## Data Integrity

| Item | Result |
|---|---:|
| Raw recorded snapshots | 11,631 |
| Raw quoted rounds | 60 |
| Off-open rounds removed | 28 |
| Trustworthy snapshots | 6,738 |
| Trustworthy rounds | 32 |
| Officially joined rounds | 29 |
| Joined 5m rounds | 22 |
| Joined 15m rounds | 7 |
| Independent calendar days | 2 |
| Trustworthy time span | 2026-06-29 07:30 UTC to 2026-06-30 22:24 UTC |

A trustworthy round must have its first recorder row within five seconds of the timestamp encoded in the market slug. Snapshot rows are never counted as independent trades; all entry tests retain one first qualifying entry per round.

The sample is suitable for validating calculations and discovering missing recorder fields. It is not suitable for training a fair-value model or proving profitability. The declared gates remain 200 independent joined rounds for trainability and 1,000 for promotion evidence.

## Executive Decisions

| Test | Result | Decision |
|---|---|---|
| BTC-shock quote underreaction | Immediate repricing; no delayed positive confidence bound | NOT SUPPORTED |
| Complement-book arbitrage | 2 candidate snapshots/rounds; maximum margin 0.0915 cents | NOT ACTIONABLE |
| Model fair-value edge | Largest cell n=7; stricter cells n=1-3 | INCONCLUSIVE |
| First profitable exit | Appeared in every tiny edge cell | DESCRIPTIVE ONLY; no fill proof |
| Market/model calibration | Mixed by checkpoint and horizon | INCONCLUSIVE |
| Small-order depth | Usually visible for 1-10 contracts | DESCRIPTIVE ONLY |
| Passive fill probability | Required fields absent | BLOCKED |
| Exact depth-adjusted VWAP | Full price ladder absent | BLOCKED |

## 1. BTC-Shock Quote Response

For each trustworthy round and each BTC threshold, the analyzer selects only the first five-second shock. It then measures the signed UP-midpoint response and follows the same quote after 2, 5, 10, 20 and 30 seconds.

| BTC shock | Independent events | Mean immediate UP-mid response |
|---:|---:|---:|
| $10+ | 31 | 6.76 cents |
| $20+ | 17 | 10.60 cents |
| $30+ | 5 | 12.00 cents |

### Delayed response

| Shock | Lag | Mean later signed quote move | 95% interval | Interpretation |
|---:|---:|---:|---:|---|
| $10+ | 2s | +0.81c | -0.39c to +2.00c | No reliable continuation |
| $10+ | 5s | +0.19c | -1.48c to +1.71c | No reliable continuation |
| $10+ | 20s | -1.18c | -4.68c to +2.26c | No underreaction evidence |
| $20+ | 2s | 0.00c | -1.53c to +1.35c | Immediate repricing already occurred |
| $20+ | 20s | -3.79c | -7.50c to -0.44c | Reversal, not delayed continuation |
| $20+ | 30s | -4.06c | -7.29c to -1.12c | Reversal, not delayed continuation |
| $30+ | 10s | -14.00c | -23.75c to -1.75c | Very small n; observed reversal |

No delayed-response cell had a positive 95% lower confidence bound. This sample therefore rejects a simple rule of buying after observing a BTC shock because Polymarket has not moved yet. The quote generally moved during the measured five-second shock window.

The result does not prove permanent market efficiency. The recorder currently uses REST snapshots and lacks exchange-event timestamps, so sub-second lags remain unmeasured. A proper underreaction test requires WebSocket event time, receive time and a 100/250/500/1000ms response study.

## 2. Complement-Book Parity

The test calculates:

`margin = 1 - UP ask - DOWN ask - UP taker fee - DOWN taker fee`

Only two snapshots had a positive calculated margin:

| Horizon | Seconds left | UP ask | DOWN ask | After-fee margin | Shared top size |
|---:|---:|---:|---:|---:|---:|
| 15m | 83.55 | 0.962 | 0.033 | 0.0208 cents | 16 contracts |
| 5m | 83.14 | 0.280 | 0.690 | 0.0915 cents | 5 contracts |

These margins are microscopic. More importantly, the recorder fetched UP and DOWN REST books sequentially, so the prices are not guaranteed to have coexisted or remained available for both fills.

Decision: do not build a complement-arbitrage bot from these rows. Upgrade to simultaneous WebSocket-maintained books and require a margin exceeding latency/slippage buffer at a common executable size.

## 3. Model Edge Episodes

The episode test uses:

- fixed UP or DOWN side at entry;
- side probability at least 0.93, capped to 0.91 fair value;
- 15-120 seconds remaining;
- at least $10 BTC anchor distance;
- spread no greater than 3 cents;
- recorded ask and top-ask size;
- exact taker-fee estimate;
- one first entry per round;
- official Polymarket settlement;
- future recorded bids for exit diagnostics.

### Largest cells

| Required raw edge | Horizon | Entries | Wins | Mean settlement net | Median edge duration |
|---:|---:|---:|---:|---:|---:|
| 0c | 5m | 5 | 5 | +$0.130/contract | 1.73s |
| 0c | 15m | 2 | 1 | -$0.397/contract | 28.73s |
| 1c | 5m | 2 | 2 | +$0.185/contract | 6.63s |
| 1c | 15m | 1 | 1 | +$0.113/contract | 0.00s |
| 2c+ | either | 1 per cell | mixed only by cell | Not statistically meaningful | Not meaningful |

The positive 5m rows are observations, not evidence. Five wins produce only a 56.55% Wilson lower bound; one win produces a 20.65% lower bound. Thresholds also reuse the same underlying rounds, so they are not independent experiments.

Every tiny cell later displayed a bid that would have made the recorded round trip positive after estimated entry and exit fees. That is an optimistic opportunity observation because the recorder does not prove that an order filled at the entry ask or that the future bid had enough size after latency.

Decision: keep recording. Do not fit a fair-value residual or change action thresholds from this sample.

## 4. Checkpoint Calibration

One row per round was selected near 120, 60 and 30 seconds remaining. Market midpoint and model P(Hold) were compared against official settlement.

### 5m

| Time left | Rounds | Actual hold | Market mean/Brier | Model mean/Brier | Lower Brier |
|---:|---:|---:|---:|---:|---|
| 120s | 21 | 85.71% | 79.19% / 0.1560 | 80.24% / 0.1403 | Model |
| 60s | 18 | 88.89% | 86.23% / 0.0853 | 84.33% / 0.0907 | Market |
| 30s | 16 | 93.75% | 94.38% / 0.0400 | 90.09% / 0.0423 | Market |

### 15m

Only four to seven rounds are available per checkpoint. Model and market results alternate, with no defensible winner.

Decision: market probability must remain the primary baseline. Future fair-value training should predict the residual versus market probability, not ignore the market and start from candles alone.

## 5. Recorded Depth

At round checkpoints, current-side top-ask availability was:

- 1 contract: 100% of measured 5m and 15m checkpoints;
- 10 contracts: 94-100% at 5m; 71-100% at 15m;
- 50 contracts: 87-95% at 5m; 25-71% at the top 15m ask;
- 100 contracts: 70-87% at the 5m top ask; 25-71% at 15m;
- 500 contracts: only 25-52% at the 5m top ask and 14-29% at 15m.

Cumulative depth within two to five cents is much larger, but those bands do not reveal exact VWAP. Visible size also does not guarantee availability when an order arrives.

Decision: the recorder can support a top-of-book size gate for PAPER diagnostics. It cannot yet support a real fill model or depth-adjusted execution price.

## 6. What Remains Blocked

| Prediction | Missing evidence |
|---|---|
| Quote age | Exchange event timestamp and last update timestamp |
| Sub-second underreaction | WebSocket event/receive timestamps and synchronized BTC events |
| Passive fill probability | Queue estimate, market trades and user order lifecycle |
| Taker fill after latency | Requested order timestamp/size and resulting fill/reject |
| Exact depth VWAP | Full maintained price ladder |
| First achievable exit | Observed entry fill plus future executable bid size |
| Maker-versus-taker selector | Observed maker/taker paper orders and missed-edge outcomes |
| Fair-value residual | At least 200 independent joined rounds |
| Promotion | At least 1,000 rounds and a later untouched era |

## 7. Recorder Changes Required Next

1. Replace REST polling as the research clock with the public Polymarket market WebSocket.
2. Store exchange event timestamp and local monotonic/UTC receive timestamps.
3. Maintain both outcome books from snapshots plus price-level updates.
4. Store best bid/ask size, full changed levels and synchronized book sequence.
5. Store last-trade price, size, side proxy and event timestamp.
6. Create a PAPER order ledger with requested price/size, decision time and model version.
7. When authenticated PAPER/live testing is approved later, store user-channel order/trade lifecycle.
8. Continue official CLOB/Gamma settlement joins and strict near-open anchor filtering.

## 8. Reproduction

```powershell
.\run_polymarket_market_response_test.bat
```

The run takes seconds, reads the live DuckDB when available and falls back to periodic parquet exports when the database is locked.

## Artifacts

- `REPORT.md`
- `coverage.csv`
- `complement_metrics.csv`
- `complement_candidates.csv`
- `edge_metrics.csv`
- `edge_episodes.csv`
- `shock_response_metrics.csv`
- `shock_response_events.csv`
- `calibration_metrics.csv`
- `calibration_points.csv`
- `depth_availability.csv`
- `config.json`

## Final Verdict

This test found no reliable Polymarket quote-underreaction or complement-arbitrage edge. It found a few profitable-looking model-edge episodes, but the independent sample is far too small and concentrated in two days.

The productive next step is not a new model. It is higher-fidelity WebSocket recording and more independent rounds. Once 200 trustworthy quote+settlement rounds exist, fair-value residual research becomes trainable. Until then, every result remains descriptive PAPER evidence.
