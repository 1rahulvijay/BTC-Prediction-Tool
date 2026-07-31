# Profit And Precision Execution Plan

Date: 2026-07-31

## Objective

Increase retained-call precision and post-cost expectancy without manufacturing confidence,
leaking future data or promoting a simulation that cannot be executed.

No model or engineering change guarantees profit. Accuracy is useful only when probabilities are
calibrated, calls are selective, and the execution/settlement ledger remains positive after all
costs.

## Current Decision

Do not build a 200-signal majority-vote engine. Many proposed signals are restatements of the same
trend, volatility, flow or liquidity factor. Adding them independently would amplify correlated
noise and multiple-testing risk.

Use target-specific specialist heads and a conservative Champion:

```text
market/feed health
-> target-specific calibrated heads
-> economic gate using executable prices and costs
-> ACT/SKIP selection
-> paper/shadow ledger
-> independent forward promotion
```

## Engineering State

Implemented and tested:

- causal model/feature gates and strict serving identities;
- calibrated/shadow promotion pipeline;
- durable paper order lifecycle and restart recovery;
- fail-closed feed protocol health;
- canonical Polymarket L2 state;
- exact visible Polymarket depth/VWAP recording;
- Binance top-of-book multi-venue recording;
- Binance USD-M sequenced snapshot/diff L2 recording and replay;
- maker-entry/taker-exit accounting correction;
- real-order authority disabled and unavailable.

Still blocked:

- strict serving of legacy artifacts until a clean manifest-writing retrain;
- verified complete-trade champion pointers;
- account-specific Binance fees;
- exact passive queue priority;
- enough independent forward days/samples for economic promotion;
- hosted CI execution while the GitHub account is billing-blocked;
- real-money adapters and operational authorization.

## Research Campaigns Worth Running

Every campaign is separate, preregistered and shadow-only. A campaign that fails its frozen gate is
closed, not retuned until it passes.

1. **Volatility-first action engine**
   Predict quiet/active, range quantiles, jump hazard and time-to-touch before direction. Use it
   for abstention, timing and required-edge buffers.

2. **Polymarket settlement mispricing**
   Compare calibrated settlement probability with executable ask, bid, fees, slippage and
   settlement. Probability disagreement alone is not edge.

3. **Funding/basis relative value**
   Test spot/perpetual basis, funding velocity, OI divergence and cross-venue dislocations with
   explicit carry and execution cost.

4. **Statistical jump model**
   Estimate jump probability and direction conditional on volatility state, liquidation flow and
   book depletion. Use initially as a risk veto.

5. **Deribit volatility-surface state**
   Test ATM IV, skew, term structure and surface changes as volatility/risk features, not direct
   BUY/SELL votes.

6. **Maker TTL and toxicity**
   Use replayable L2 plus public trades to compare taker/taker, maker/taker, maker-TTL-cross and
   maker/maker policies. Conservative queue estimates never become observed fills.

7. **Optimal stopping**
   Predict ACT/SKIP/WAIT and exit timing from executable state. Compare against HOLD and simple
   deterministic exits on the same candidate stream.

8. **Strategy dependency and co-failure**
   Measure shared drawdown days, factor exposure and failure clustering before combining
   strategies. Diversification claims require independent residual PnL, not different names.

## Frozen Evaluation Contract

For a predictive head:

- chronological or purged rolling validation;
- untouched final window;
- calibration curve, Brier score and ECE;
- precision/recall and coverage at frozen thresholds;
- metrics by horizon, regime, side and confidence bucket;
- null/shuffle and simple-baseline comparisons;
- feature/source/label/time-span manifest.

For a trading policy:

- actual causal bid/ask at decision and exit;
- fee, spread, slippage, latency, partial-fill and missed-fill accounting;
- expectancy and profit factor, not accuracy alone;
- day-block lower confidence bound above zero;
- both LONG and SHORT results;
- maximum drawdown and loss concentration;
- final untouched period positive;
- minimum independent sample and calendar duration;
- forward shadow before any authority change.

## Operator Sequence

1. Complete the clean retrain on committed `master`.
2. Run strict artifact and feature-contract checks.
3. Promote only a verified challenger bundle.
4. Run `start_production.bat`; expect refusal until every serving prerequisite passes.
5. Keep all recorders running, including sequenced Binance L2.
6. Review calibration and executable paper ledgers weekly.
7. Run one frozen research campaign at a time.
8. Keep real orders disabled until a later explicitly authorized deployment phase.

## Commands

```powershell
set BTC_SELFTEST_ONLY=1
call start.bat

python backend\verify_artifact_identity.py --strict
python backend\check_feature_contract.py --enforce-serving
python backend\production_readiness.py

python backend\venues\binance_l2_recorder.py --report
python backend\venues\rl_data_readiness.py
```

Passing software tests means the measurements are less likely to be wrong. It does not mean the
market edge exists. The only acceptable profit claim comes from a frozen, executable, independent
forward ledger.
