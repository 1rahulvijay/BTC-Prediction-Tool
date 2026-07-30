# Polymarket BTC-Shock Share Replay Results

Date: 2026-07-01  
Status: completed, PAPER research only  
Implementation: `backend/research/test_polymarket_shock_trade_replay.py`  
Runner: `tests\launchers\run_polymarket_shock_trade_replay.bat`
Output: `data/research/polymarket_shock_trade_replay/`

## Question

After BTC moves quickly, can the app profit from actual recorded Polymarket share quotes by either:

1. buying the share in the BTC-shock direction (`MOMENTUM`), or
2. buying the temporarily losing share (`FADE`)?

This is more realistic than a BTC-path proxy because entries use recorded share asks and timed exits use later recorded share bids. It remains a quote replay rather than a fill replay.

## Executive Verdict

No tested configuration passed the declared robustness gate.

- Short 2-30 second round trips were generally negative after crossing the spread and paying entry/exit fees.
- The strongest pooled `$20` fade exits were approximately flat, with confidence intervals crossing zero.
- Holding a 5m momentum share to settlement looked positive in 20-21 rounds, but its confidence interval crossed zero.
- All multiple-test-adjusted q-values were `1.0`.
- Only 29 officially settled trustworthy rounds across two days were available.

Decision: do not modify live actions, thresholds or models. Continue recording and rerun the frozen test.

## Data

| Item | Result |
|---|---:|
| Trustworthy snapshots | 6,738 |
| Trustworthy rounds | 32 |
| Officially joined rounds | 29 |
| Joined 5m rounds | 22 |
| Joined 15m rounds | 7 |
| Independent days | 2 |
| Off-open rounds removed | 28 |

### Causal shock events

| Shock threshold | 5m events | 15m events | Total |
|---:|---:|---:|---:|
| $10 | 23 | 8 | 31 |
| $20 | 10 | 7 | 17 |
| $30 | 3 | 2 | 5 |

Only the first qualifying five-second shock per market round and threshold is used. Threshold families overlap by design, so a `$30` event can also appear in the `$10` and `$20` families. Multiple-testing correction includes these correlated searches conservatively.

## Trade Design

For each event:

- `MOMENTUM` buys UP after an upward shock or DOWN after a downward shock.
- `FADE` buys the opposite share.
- Entry occurs at the recorded ask with either zero or two seconds of simulated decision delay.
- Fixed exits occur at the recorded bid after 2, 5, 10, 20 or 30 seconds.
- Settlement exits use official Polymarket outcomes.
- Fade also tests `RECROSS_OR_SETTLEMENT`: sell at the recorded bid when BTC returns through its pre-shock level, otherwise hold to settlement.
- Taker-fee estimates are charged on both legs of a round trip.
- Entry spread must be no greater than five cents.
- Recorded top-ask size must support at least one contract.
- Every metric cell uses at most one event per round.

The test reports bootstrap mean intervals, Wilson win-rate bounds, profit factor, drawdown, CVaR, a one-sided sign-flip test and Benjamini-Hochberg q-values.

The exploratory robustness gate requires all of:

- at least 20 trades;
- positive bootstrap lower bound for mean net return;
- BH-adjusted q-value no greater than 0.05.

No cell passed.

## 1. Short Round-Trip Exits

### $10 shocks, pooled 5m+15m, immediate entry

| Strategy | Exit | Trades | Mean net/contract | 95% interval | Profit factor |
|---|---:|---:|---:|---:|---:|
| FADE | 2s | 30 | -$0.0498 | -$0.0616 to -$0.0383 | 0.02 |
| FADE | 5s | 30 | -$0.0435 | -$0.0583 to -$0.0289 | 0.09 |
| FADE | 10s | 30 | -$0.0402 | -$0.0630 to -$0.0153 | 0.24 |
| FADE | 20s | 30 | -$0.0301 | -$0.0643 to +$0.0058 | 0.44 |
| FADE | 30s | 30 | -$0.0298 | -$0.0682 to +$0.0124 | 0.49 |
| MOMENTUM | 2s | 30 | -$0.0355 | -$0.0482 to -$0.0233 | 0.08 |
| MOMENTUM | 5s | 30 | -$0.0421 | -$0.0611 to -$0.0259 | 0.09 |
| MOMENTUM | 10s | 30 | -$0.0442 | -$0.0722 to -$0.0197 | 0.15 |
| MOMENTUM | 20s | 30 | -$0.0528 | -$0.0893 to -$0.0162 | 0.27 |
| MOMENTUM | 30s | 30 | -$0.0534 | -$0.0964 to -$0.0137 | 0.32 |

This is a strong rejection of a simple short-horizon round trip after a `$10` shock in the observed sample. The spread and two fees consume more than the typical subsequent bid improvement.

### $20 shocks

The best larger cells were fade exits:

| Entry delay | Exit | Trades | Mean net | 95% interval | Decision |
|---:|---:|---:|---:|---:|---|
| 0s | 20s | 17 | -$0.0002 | -$0.0326 to +$0.0347 | Flat/inconclusive |
| 0s | 30s | 17 | +$0.0025 | -$0.0263 to +$0.0333 | Flat/inconclusive |
| 2s | 20s | 16 | +$0.0014 | -$0.0266 to +$0.0343 | Flat/inconclusive |
| 2s | 30s | 16 | -$0.0026 | -$0.0277 to +$0.0233 | Flat/inconclusive |

These cells do not support a profitable fade. Their means are effectively zero and every confidence interval includes meaningful loss.

`$30` shock cells contain only four or five events and cannot support a conclusion.

## 2. Settlement Momentum Observation

The largest apparent positive result was buying the shock-direction share and holding to official settlement:

| Shock | Horizon | Entry delay | Trades | Settlement wins | Mean net | 95% interval | BH q-value |
|---:|---:|---:|---:|---:|---:|---:|---:|
| $10 | 5m | 0s | 20 | 75.00% | +$0.0972 | -$0.0927 to +$0.2686 | 1.0 |
| $10 | 5m | 2s | 21 | 76.19% | +$0.0987 | -$0.0945 to +$0.2659 | 1.0 |
| $10 | pooled | 0s | 27 | 70.37% | +$0.0768 | -$0.1028 to +$0.2333 | 1.0 |
| $20 | 5m | 0s | 9 | 77.78% | +$0.0683 | -$0.2123 to +$0.2904 | 1.0 |

Why this is not a signal:

- the lower confidence bounds are negative;
- losses are large when buying expensive shares;
- only two independent days are represented;
- the threshold/horizon/delay/exit search creates many comparisons;
- q-values show no surviving statistical evidence;
- quote availability is not an observed fill.

This configuration may be frozen as a candidate for future replay. Its threshold must not be changed while new rounds accumulate.

## 3. Fade-To-Settlement

After a `$10` shock, pooled immediate-entry FADE settlement produced:

- 27 trades;
- 29.63% settlement wins;
- mean net `-$0.1197` per contract;
- 95% interval `-$0.2797` to `+$0.0553`;
- profit factor `0.57`.

This aligns with the earlier causal fade rejection. Buying the losing share after every small shock is not supported.

Some 15m fade cells after a `$20` shock appeared positive, but they contain only five to seven trades. They remain noise-compatible and must not be selected.

## 4. Recross-Or-Settlement

The recross rule sells a fade position when BTC returns to its pre-shock level; if no recross occurs, it holds to settlement.

The best apparent `$30` pooled cells contained only five trades and had positive means around 7 cents, but their intervals crossed zero and q-values were 1.0. Smaller-shock recross results did not establish a stable edge.

Decision: no promotion. The rule remains useful as a predefined future recorder test because it reflects the intended two-sided strategy without hindsight-best exits.

## 5. What The Test Teaches

1. Polymarket quote response is fast enough that simple 2-10 second taker round trips lose to spread and fees.
2. Fading every BTC shock is worse than waiting for a selective, separately validated setup.
3. Settlement momentum may deserve frozen forward observation, but current evidence is nowhere near promotion quality.
4. A profitable system must predict **mispricing**, not merely the BTC move that market makers already see.
5. Maker execution could change economics, but it cannot be assumed without queue/fill records.

## 6. Limitations

- Recorded quotes are REST observations, not synchronized WebSocket events.
- An ask or bid is not proof that the requested order filled after latency.
- Entry delay is simulated from the next recorder observation, not an actual order round trip.
- Top-of-book size is recorded, but exact queue position is absent.
- Exit bid size is not separately preserved in this schema.
- Only two days are represented.
- Pooled 5m/15m rows can be temporally correlated and are secondary evidence.

## 7. Next Action

Keep this test frozen and rerun it as the recorder grows:

```powershell
.\tests\launchers\run_polymarket_shock_trade_replay.bat
```

Do not tune thresholds from each small update. Review at these checkpoints:

- 100 trustworthy settled rounds: calibration/debug review only;
- 200 rounds: trainability and first frozen holdout design;
- 500 rounds: independent horizon/time-left slices;
- 1,000 rounds: potential PAPER promotion review.

Before testing maker strategies, add WebSocket event/receive timestamps, full book updates, trades and a paper-order lifecycle ledger.

## Artifacts

- `REPORT.md`
- `coverage.csv`
- `shock_events.csv`
- `trade_replay.csv`
- `strategy_metrics.csv`

## Final Decision

The requested real-share strategy has now been tested with recorded asks, bids and fee estimates. Short-term momentum and fade exits do not currently make money. Settlement momentum is an interesting but statistically unsupported observation. No action should be added to the app from this run.
