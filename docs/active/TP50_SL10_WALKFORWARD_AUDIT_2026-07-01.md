# TP50/SL10 Walk-Forward Overfitting Audit

Date: 2026-07-01  
Status: completed, promising BTC-path policy, PAPER only  
Implementation: `backend/research/test_180d_stopping_overfit_audit.py`  
Runner: `run_180d_stopping_overfit_audit.bat`  
Output: `data/research/stopping_overfit_audit_180d/`

## Purpose

The earlier stopping experiment selected `take profit $50 / stop loss $10` from one chronological development/test split. This follow-up asks whether that policy was a lucky winner from a 20-policy grid.

The audit regenerates first-touch-side signals in five expanding chronological folds, reselects probability thresholds and policies only on each validation era, and measures all policies on the following untouched era.

This remains a signed BTC-dollar path simulation. It is not Polymarket share PnL.

## Executive Verdict

TP50/SL10 passed the declared historical stability audit at the `$2` BTC-proxy cost assumption for both horizons.

| Horizon | OOS signals | Mean net | Profit factor | Day-block 95% interval | Positive folds | Worst fold | Median policy rank | PBO estimate |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 5m | 2,474 | +$8.24 | 2.08 | +$7.28 to +$9.35 | 5/5 | +$6.06 | 1 | 0.00 |
| 15m | 4,337 | +$3.75 | 1.42 | +$2.89 to +$4.72 | 5/5 | +$0.74 | 1 | 0.10 |

Interpretation:

- The fixed policy is not explained by one favorable 20-day period.
- It remains near the top of the tested policy family across changing thresholds and refitted signal models.
- Validation-driven policy switching is less stable than keeping TP50/SL10 fixed.
- The result supports TP50/SL10 as a **BTC-path stopping candidate for quote replay and PAPER testing**.
- It does not establish a profitable binary-share strategy.

## Validation Design

- Source: existing 180-day 30-second Binance path archive.
- Features: 70 causal pre-anchor features plus 13 historically covered flow features.
- Target: first touch of `+$30/-$30` at 5m and `+$50/-$50` at 15m.
- Signal: exactly one of independently calibrated UP/DOWN heads must fire.
- Initial training history: 60 days.
- Validation era: preceding 20 days.
- Test era: next 20 days; final available era is 17 days because the flow archive ends on June 28.
- Five expanding walk-forward folds.
- Horizon-aware outcome purging at every boundary.
- Fixed model families from the prior research result, trained sequentially in every fold.
- Isotonic calibration and side thresholds fit on validation only.
- Twenty candidate stopping policies.
- Same-bar target/stop ambiguity scored stop-first.
- Policy selection cost: `$2` per BTC-path signal.

The model-family roster is frozen from earlier research, so this is a post-selection stability audit rather than a pristine first discovery.

## Fold Periods

| Fold | Validation | Untouched test | Note |
|---:|---|---|---|
| 1 | 2026-03-03 to 2026-03-23 | 2026-03-23 to 2026-04-12 | Full 20 days |
| 2 | 2026-03-23 to 2026-04-12 | 2026-04-12 to 2026-05-02 | Full 20 days |
| 3 | 2026-04-12 to 2026-05-02 | 2026-05-02 to 2026-05-22 | Full 20 days |
| 4 | 2026-05-02 to 2026-05-22 | 2026-05-22 to 2026-06-11 | Full 20 days |
| 5 | 2026-05-22 to 2026-06-11 | 2026-06-11 to 2026-06-28 | Final available 17 days |

## 1. Signal Stability

### 5m

| Fold | Exclusive calls | First-touch-side precision | UP/DOWN AUC | TP50/SL10 mean | TP50/SL10 rank |
|---:|---:|---:|---:|---:|---:|
| 1 | 206 | 55.83% | 0.574 / 0.569 | +$6.06 | 2 |
| 2 | 98 | 70.41% | 0.596 / 0.590 | +$11.70 | 4 |
| 3 | 318 | 56.92% | 0.599 / 0.607 | +$8.24 | 1 |
| 4 | 472 | 58.26% | 0.590 / 0.613 | +$10.07 | 1 |
| 5 | 1,380 | 54.93% | 0.583 / 0.598 | +$7.69 | 1 |

Thresholds varied materially by fold, but the fixed exit remained positive. Fold 5's high call count reflects a less selective validation threshold; the policy still remained positive in that era.

### 15m

| Fold | Exclusive calls | First-touch-side precision | UP/DOWN AUC | TP50/SL10 mean | TP50/SL10 rank |
|---:|---:|---:|---:|---:|---:|
| 1 | 1,130 | 45.22% | 0.534 / 0.520 | +$0.74 | 1 |
| 2 | 1,192 | 51.34% | 0.565 / 0.557 | +$3.61 | 2 |
| 3 | 804 | 49.75% | 0.561 / 0.577 | +$4.36 | 2 |
| 4 | 846 | 51.18% | 0.547 / 0.566 | +$5.49 | 1 |
| 5 | 365 | 56.44% | 0.546 / 0.589 | +$8.15 | 1 |

First-touch-side accuracy is not the policy win rate. TP50/SL10 needs fewer large correct moves to offset frequent small stops; its nominal barrier-payoff breakeven before costs is approximately 16.7%.

## 2. Cost Sensitivity

### Fixed TP50/SL10

| Horizon | Assumed cost | Mean net | Profit factor | 95% interval | Positive folds |
|---|---:|---:|---:|---:|---:|
| 5m | $0 | +$10.24 | 2.61 | +$9.28 to +$11.35 | 5/5 |
| 5m | $2 | +$8.24 | 2.08 | +$7.28 to +$9.35 | 5/5 |
| 5m | $5 | +$5.24 | 1.55 | +$4.28 to +$6.35 | 5/5 |
| 15m | $0 | +$5.75 | 1.78 | +$4.89 to +$6.72 | 5/5 |
| 15m | $2 | +$3.75 | 1.42 | +$2.89 to +$4.72 | 5/5 |
| 15m | $5 | +$0.75 | 1.07 | -$0.11 to +$1.72 | 4/5 |

The 5m proxy is robust across the tested fixed costs. The 15m edge is thinner and no longer robust at `$5`: its lower interval becomes negative and one fold loses.

These costs are BTC-dollar proxy deductions, not Polymarket cents, share fees or slippage. They test policy fragility, not actual trade economics.

## 3. Policy Ranking

Across the twenty-policy family:

- 5m TP50/SL10 mean test value: `+$8.24` after `$2`; median rank `1`.
- 15m TP50/SL10 mean test value: `+$3.75` after `$2`; median rank `1`.
- 5m policy-selection PBO estimate: `0.00` across ten fold combinations.
- 15m policy-selection PBO estimate: `0.10` across ten fold combinations.

PBO here is an approximate era-combination estimate because five folds are not the even symmetric partition used by formal CSCV. It is still useful as a policy-instability warning, not a precise probability theorem.

The risk/reward pattern is coherent:

- `$10` targets are negative or weak because reward is too small.
- `$20/$30` targets improve as the target/stop ratio grows.
- `$50/$10` is consistently strongest or near strongest.
- Wider stops generally weaken results.

This monotonic structure is more credible than one isolated grid winner.

## 4. Fixed Policy Versus Validation Switching

Validation selected:

- 5m: settlement three times, TP50/SL20 once, settlement again in the latest fold.
- 15m: TP50/SL10 twice and settlement three times.

The fold-selected controller performed worse:

| Horizon | Policy method | Mean at $2 | Profit factor | 95% interval | Positive folds | Max drawdown |
|---|---|---:|---:|---:|---:|---:|
| 5m | Fixed TP50/SL10 | +$8.24 | 2.08 | +$7.28 to +$9.35 | 5/5 | $204 |
| 5m | Validation-selected | +$7.24 | 1.29 | +$3.61 to +$10.71 | 5/5 | $2,485 |
| 15m | Fixed TP50/SL10 | +$3.75 | 1.42 | +$2.89 to +$4.72 | 5/5 | $1,308 |
| 15m | Validation-selected | +$1.16 | 1.04 | -$2.09 to +$4.21 | 3/5 | $8,224 |

The adaptive selector chased settlement performance and failed badly in the latest 15m folds. This is direct evidence against dynamically switching exit policy from one recent validation window without stronger regularization.

Decision: keep TP50/SL10 fixed for future PAPER replay. Do not deploy the validation-selected controller.

## 5. Why This Is Still Not A Profitable Bot

The simulation assumes:

- a signed BTC-dollar entry at the round anchor;
- exact target/stop execution when a 30-second bar reaches the level;
- stop-first handling when both occur in one bar;
- a fixed dollar cost subtraction.

Polymarket trading instead requires:

- buying a binary share at its actual ask;
- nonlinear share repricing as BTC distance and time change;
- available size and fill after latency;
- selling at an executable bid or receiving settlement payout;
- exact market fees and slippage;
- handling oracle/settlement differences.

The separate real-share shock replay found that short ask-to-bid round trips generally lose after spread and fees. Therefore the strong BTC-path policy cannot be translated directly into a share trade.

## 6. Proper Next Use

TP50/SL10 can now serve as a **path-risk policy feature** in recorder analysis:

1. At the round open, freeze the first-touch side signal.
2. Record whether BTC reaches the policy's stop or target first.
3. Simultaneously record the chosen Polymarket share's ask, future bids, sizes and settlement.
4. Compare actual share outcomes under:
   - no trade;
   - settlement hold;
   - BTC TP50/SL10-triggered exit;
   - first profitable executable share exit.
5. Keep one entry per independent round.
6. Require positive net expectancy and confidence bounds in a later forward period.

The policy should not be wired as an automatic bet from this historical audit.

## 7. Reproduction

```powershell
.\run_180d_stopping_overfit_audit.bat
```

Runtime on the current laptop was approximately 1.5 minutes with four threads. Models are trained sequentially and no production artifact is replaced.

## Artifacts

- `REPORT.md`
- `fold_summary.csv`
- `fold_policy_metrics.csv`
- `policy_aggregate.csv`
- `walkforward_signal_predictions.csv`
- `fixed_policy_trades.csv`
- `selected_policy_trades.csv`
- `policy_pbo_combinations.csv`
- `policy_pbo_summary.csv`
- `aggregate_metrics.csv`
- `config.json`
- `run.log`

## Final Decision

The original TP50/SL10 BTC-path result survives a substantially stronger multi-era audit. It is more stable than reselecting an exit policy every validation window, and the result persists through the latest available 17-day era.

Promote it only from **initial research** to **frozen PAPER/quote-replay candidate**. Do not treat the reported BTC-dollar expectancy as Polymarket profit.
