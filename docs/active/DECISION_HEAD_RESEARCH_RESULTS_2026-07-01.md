# Decision-Head Research Results

Date: 2026-07-01  
Status: completed, causal 180-day test, paper only  
Implementation: `backend/research/test_180d_decision_heads.py`  
Runner: `run_180d_decision_heads.bat`  
Output: `data/research/decision_heads_180d_30s/`

## Executive Decision

The experiment tested every requested prediction without inventing missing market data.

| Requested prediction | Result | Decision |
|---|---|---|
| Net expected value | Computable on only 1-7 eligible entries per cost setting | BLOCKED |
| Fair share price | 25 representative quoted rounds; mixed model-vs-market calibration | BLOCKED |
| Time-to-touch | AUC 0.81-0.89 across barriers/checkpoints | RETAIN |
| Barrier order | Revert AUC 0.647, selected precision 42.1% | REJECT as trade trigger |
| Maximum adverse excursion | Quantile bands provide useful risk zones but some under-coverage remains | SHADOW |
| Exit opportunity | Positive price-only exits in tiny cells; no bid-depth proof | BLOCKED |
| Fill probability | Taker depth proxy works; passive fill cannot be labeled | PARTIAL/BLOCKED |
| Volatility quantiles | Useful median/range estimates; conformal bands roughly 77-80% coverage | SHADOW |
| Regime transition | Strong 5m ranking; selective 15m transition pocket | RETAIN as risk context |
| Cascade risk | Weak price/volume proxy and no liquidation labels | REJECT proxy; collect real data |
| Model failure | Failure AUC near 0.50; no reliable filtering improvement | REJECT |

Nothing here authorizes live betting. The deployable economic question remains whether conservative fair value exceeds the executable ask plus fees, slippage and a safety buffer.

## Experiment Design

- Source: 180 cached days of Binance one-second trades resampled to 30 seconds.
- Rounds: 69,102 exact 5m/15m anchor rounds.
- Primary-touch events: 28,991 causal, non-ambiguous events.
- Features: the validated 70 open-time features from the anchor research lane.
- Split: chronological 64% train, 16% validation, 20% untouched test.
- Boundary purge: labels crossing train/validation or validation/test are removed.
- Classifiers: Logistic Regression, Random Forest, Extra Trees, HistGradientBoosting, XGBoost, LightGBM and CatBoost.
- Ensemble: the top three validation models, then isotonic probability calibration.
- Quantiles: LightGBM 10th/50th/90th percentiles with validation-only conformal widening.
- Runtime: 13.6 minutes on the laptop with four threads.
- Safety: no deployed model or live app behavior was changed.

## 1. Time-To-Touch

This head asks whether BTC will touch a dollar barrier within 30, 60 or 120 seconds from the round open. It is implemented as discrete checkpoint probabilities, which is a practical discrete-time survival representation.

### Key untouched-test results

| Horizon / event | AUC | Selected precision | Signals | Base rate |
|---|---:|---:|---:|---:|
| 5m: touch +/-$30 within 30s | 0.830 | 89.81% | 775 | 33.99% |
| 5m: touch +/-$30 within 60s | 0.827 | 94.87% | 1,658 | 50.93% |
| 5m: touch +/-$30 within 120s | 0.834 | 98.07% | 2,389 | 69.73% |
| 15m: touch +/-$50 within 30s | 0.831 | 61.96% | 347 | 17.02% |
| 15m: touch +/-$50 within 60s | 0.825 | 87.08% | 325 | 30.13% |
| 15m: touch +/-$50 within 120s | 0.825 | 91.48% | 610 | 48.80% |

The head is genuinely rankable, but small barriers have high natural base rates. The useful output is a barrier-by-time probability table, not a BUY/SELL call.

Recommended use:

- estimate whether there is enough movement for a setup;
- choose realistic target distance and expiry;
- avoid buying expensive optionality when touch probability is low;
- combine with direction/fair-price heads, never use alone.

## 2. Barrier Order And Reversal Timing

After the first primary touch ($30 for 5m, $50 for 15m), the test labels which happens first:

- return to anchor (`REVERT`),
- equal-distance adverse stop (`STOP`), or
- neither before expiry (`TIMEOUT`).

Same-bar entry ambiguity is excluded. Later bars crossing both target and stop are scored stop-first.

| Target | AUC | Selected precision | Base rate | Decision |
|---|---:|---:|---:|---|
| Revert before stop/expiry | 0.647 | 42.05% | 32.11% | Reject as trade trigger |
| Stop before revert/expiry | 0.633 | 50.46% | 41.22% | Insufficient |
| Timeout | 0.839 | 99.12% | 26.67% | Useful skip/context pocket |
| Revert within 30s | 0.720 | 14.79% | 6.36% | Ranking only |
| Revert within 60s | 0.704 | 26.06% | 12.49% | Ranking only |
| Revert within 120s | 0.677 | 36.98% | 21.00% | Ranking only |

This confirms the earlier fade result: reversal can be ranked, but the selected reversal trades do not clear a symmetric 50% breakeven before market costs. The timeout head may help identify rounds where no second leg should be attempted.

## 3. Maximum Favorable/Adverse Excursion And Quantiles

For an UP position:

- maximum favorable excursion = future maximum above anchor;
- maximum adverse excursion = future maximum below anchor.

For a DOWN position the mapping is reversed.

The model produces 10th, median and 90th percentile estimates for maximum up, maximum down and total path range.

| Horizon / target | Median MAE | Raw 10-90 coverage | Conformal coverage | Conformal mean width |
|---|---:|---:|---:|---:|
| 5m maximum up | $41.80 | 77.29% | 78.71% | $134.98 |
| 5m maximum down | $42.16 | 78.51% | 80.35% | $137.82 |
| 5m total range | $44.69 | 74.18% | 77.19% | $145.11 |
| 15m maximum up | $72.21 | 74.36% | 77.83% | $224.35 |
| 15m maximum down | $75.02 | 75.95% | 80.00% | $240.00 |
| 15m total range | $78.88 | 69.73% | 76.96% | $256.18 |

Conformal widening improves coverage, but temporal distribution shift prevents every band from reaching 80%. Use these as approximate risk zones with the displayed coverage, not guaranteed bounds.

## 4. Regime Transition

The future regime is now computed with the exact same regime engine at the round end. The earlier draft used a different future-rule definition and was discarded before documentation.

| Target | AUC | Selected precision | Signals | Base rate |
|---|---:|---:|---:|---:|
| 5m regime transition | 0.841 | 75.25% | 303 | 17.07% |
| 5m future HIGH_VOL | 0.943 | 94.12% | 204 | 4.38% |
| 5m future trend | 0.846 | 29.81% | 426 | 4.23% |
| 15m regime transition | 0.753 | 97.70% | 174 | 26.92% |
| 15m future HIGH_VOL | 0.701 | 16.49% | 97 | 4.52% |
| 15m future trend | 0.621 | 5.56% | 72 | 4.28% |

Recommended use is risk control:

- suppress calm-regime assumptions when a high-volatility transition is likely;
- widen expected path ranges;
- reduce size or avoid passive exits during transition risk;
- do not treat regime transition as direction.

The 5m HIGH_VOL head is the strongest new specialist. It still needs forward shadow verification before live use.

## 5. Cascade Risk

Historical liquidation events were not available for this 180-day lane. The test therefore used an explicitly named proxy requiring a large directional BTC move, high future activity and efficient one-sided path.

- 5m proxy AUC: approximately 0.66-0.68 depending on side.
- 15m proxy AUC: approximately 0.60-0.63.
- Selected event precision remained low because the event is rare.

Decision: do not call this a liquidation model and do not deploy it. Record Binance/Bybit liquidation streams and train against actual liquidation notional, side and cluster timing.

## 6. Model-Failure / Meta-Skip Head

The nested design used base models trained on the first 64%, a meta-model fitted inside validation, threshold selection on the remaining validation segment, and final measurement on untouched test.

| Horizon | Base accuracy | Failure AUC | Retained calls | Retained accuracy | Wilson LB |
|---|---:|---:|---:|---:|---:|
| 5m | 52.16% | 0.509 | 4,805 | 53.28% | 51.87% |
| 15m | 52.42% | 0.499 | 479 | 50.73% | 46.27% |

Decision: reject this version. It cannot reliably identify when the direction ensemble is wrong. The 5m lift is too small and the 15m filter is harmful.

A future failure model should use forward live features unavailable in the historical matrix: quote disagreement, live calibration drift, feed health, ensemble version, market spread and recent resolved accuracy.

## 7. Net Expected Value

The recorder contains:

- 11,631 raw quote snapshots;
- 60 quoted rounds;
- 32 rounds passing anchor-integrity checks;
- 29 trustworthy rounds joined to official settlements.

Training requires at least 200 independent joined rounds. Promotion requires at least 1,000. Both gates are blocked.

The current one-entry-per-round exploratory result:

- zero-buffer/zero-slippage: 7 entries, 85.7% wins, but **-$0.0205 per contract** after the taker fee;
- stricter edge settings show positive results but retain only 1-3 entries;
- Wilson lower bounds remain far too low for a profit claim.

The tested equation is:

`net = settlement payout - executable ask - taker fee - slippage`

Slippage scenarios of 0c, 1c and 2c are reported separately in `quote_ev_metrics.csv`.

## 8. Fair Share Price

On 25 representative eligible quoted rounds:

| Metric | Model fair | Market ask |
|---|---:|---:|
| Brier score | 0.0737 | 0.0805 |
| Mean absolute probability error | 0.1556 | 0.1204 |

The model wins on Brier but loses on MAE, while averaging 4.72 cents below the market ask. With 25 rounds this is descriptive noise, not evidence that either estimate is superior.

Decision: keep recording. Train a fair-price residual model only after 200+ independent rounds; consider promotion only after 1,000+.

## 9. Exit Opportunity

For each first eligible entry, the test searches later snapshots in the same round for the best same-side bid and subtracts entry and exit fee estimates.

The tiny eligible cells all found a positive later bid, but most cells contain only one to five entries. The recorder does not preserve top-bid size, so these are price opportunities rather than proven executable exits.

Required recorder enhancement:

- persist top bid size and depth bands for both sides;
- retain quote sequence without gaps;
- evaluate first achievable exit, not hindsight-best exit;
- include latency and requested position size.

The existing best-future-bid number is an optimistic upper bound and must not be used as expected PnL.

## 10. Fill Probability

For a taker order, top-ask depth provides a size-availability proxy on 25 eligible rounds:

| Order size | Recorded top-ask depth sufficient |
|---:|---:|
| 1 contract | 100% |
| 10 contracts | 96% |
| 50 contracts | 76% |
| 100 contracts | 72% |

This does not prove a fill after latency. Passive fill probability is completely blocked because the recorder does not match posted orders to subsequent trades or queue position.

## Recommended Architecture

Retain separate specialist ownership:

1. Time-to-touch head: movement timing and barrier feasibility.
2. Quantile path head: favorable/adverse excursion and range.
3. Regime-transition head: risk state and volatility warning.
4. Direction head: side lean only.
5. P(Hold)/fair-value head: settlement probability.
6. Net-EV gate: executable ask, fee, slippage and buffer.
7. Exit/fill head: blocked until recorder depth is sufficient.

The champion should not average these into one probability. It should use them in order:

`feed integrity -> movement feasible -> regime risk -> side probability -> market price edge -> fill/exit feasibility -> PAPER action`

## Immediate Next Work

1. Keep the Polymarket recorder running until 200 joined rounds for trainability and 1,000 for promotion evidence.
2. Add top-bid size/depth and first-achievable-exit labels.
3. Record real Binance/Bybit liquidations for a legitimate cascade target.
4. Shadow the 5m time-to-touch and 5m HIGH_VOL heads; do not wire actions yet.
5. Reject the current model-failure head and post-touch fade trigger.
6. Re-run the quote scorecard without changing thresholds while evidence accumulates.

## Artifacts

The output directory contains:

- `REPORT.md`
- `classification_metrics.csv`
- `classification_predictions.csv`
- `quantile_metrics.csv`
- `quantile_predictions.csv`
- `model_failure_metrics.csv`
- `model_failure_predictions.csv`
- `quote_data_status.csv`
- `quote_ev_metrics.csv`
- `quote_fair_value_metrics.csv`
- `quote_exit_metrics.csv`
- `quote_fill_metrics.csv`
- `quote_calibration.csv`
- `path_labels.parquet`
- `primary_touch_events.parquet`
- `config.json`
- `run.log`

## Reproduction

```powershell
.\run_180d_decision_heads.bat
```

The command is paper-only and does not retrain or replace the live application ensemble.
