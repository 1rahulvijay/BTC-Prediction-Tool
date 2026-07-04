# Profitability And Betting Validation - 2026-07-01

## Status

**Real-money status: NOT APPROVED. PAPER ONLY.** The system does not yet have enough independent,
fee-adjusted Polymarket outcomes to demonstrate positive expectancy. This document supersedes the same-day
fade/reversal performance claims in older active documents.

## Evidence hierarchy

1. Official settlement plus executable ask/bid and fees, one entry per round.
2. Forward live decision accuracy, one first qualifying decision per round.
3. Chronological out-of-sample model tests with causal features.
4. Snapshot-level calibration, useful for drift but not an independent-bet count.
5. BTC-price proxy backtests, useful for research but not Polymarket profit.

No lower layer may be presented as proof of a higher layer.

## Current measured truth

| Question | Evidence | Verdict |
|---|---:|---|
| Can the main ensemble call 5m direction? | 47.8% live resolved accuracy | No; context only |
| Can it call 15m direction? | 46.8% live resolved accuracy | No; context only |
| Is served P(Hold) calibrated over snapshots? | n=109,749, ECE 0.0099, Brier 0.1652 | Useful drift diagnostic |
| Does first qualifying P(Hold)>=0.93 hold? | 92.6% over 1,794 independent 5m/15m rounds; mean prediction 97.8%; Wilson lower bound 91.3% | Promising but entry-selected probabilities are overconfident |
| Is the Polymarket edge proven? | 29 trustworthy joined one-entry rounds | No; far too small |
| Fee-adjusted broad recorder scan | sign changes by buffer; 28-29 entries depending on threshold | No promotion |
| Exact champion-equivalent 3c gate | one 5m entry, zero 15m entries | No statistical evidence |
| Is fade performance proven? | Previous top-bucket claims used post-entry touch-candle information | Retracted; causal retrain required |

The 95.7% snapshot figure must not be quoted as a betting win rate. Repeated snapshots from one easy round
are correlated. The decision-level scorecard counts the first qualifying entry once.

## High-impact faults found and corrected

### 1. Fade touch-candle leakage

The old fade trainer and A/B probe used the final high/low of the one-minute candle in which the barrier was
first touched. Those prices include movement after the entry. In the audited sample, roughly 81% of 5m $30
touches and 77% of 15m touches also contained an anchor or stop crossing in the same candle, so OHLC cannot
tell which occurred first.

Corrections:

- fade v5 uses completed bars strictly before the touch candle;
- overshoot is zero at the exact barrier crossing unless tick/1s data is used;
- touch candles that also contain TP/stop are excluded as ungradeable;
- model artifacts are saved atomically;
- the old v4 artifact is rejected by live serving;
- the live card emits `WAIT`/`PAPER ONLY`, never an executable fade entry.

After a causal diagnostic rebuild, the earlier 68-77% top-bucket claims no longer survive. Approximate
causal results were 5m AUC 0.727 with top-decile hold near 44.6%, and 15m AUC 0.597 with top-decile near
50.4%. Those are research results, not a deployable edge.

### 2. Missing market economics

The old champion/recorder paths could compare fair value with ask while omitting the current crypto taker
fee. The current gate is:

```text
entry_fair = min(P(Hold), 0.91)
net_edge = entry_fair - executable_ask - taker_fee - 0.03
paper candidate only when net_edge > 0
```

The fee is calculated per share as `0.07 * price * (1-price)` for the current crypto fee schedule. The
quote must match the exact horizon and round anchor and be no more than five seconds old. Midpoint is never
treated as executable. The 91c cap is the rounded lower confidence bound from independent first-entry
outcomes; it prevents snapshot-level overconfidence from being priced as tradable certainty.

### 3. Recorder contamination and overcounting

- smoke mode now uses an in-memory database and cannot write synthetic future rounds to production;
- rounds whose first quote is more than five seconds from the true anchor are excluded;
- profitability analysis counts at most one first entry per round;
- only official joined settlements count toward sample sufficiency;
- ROI uses `ask + taker_fee` as cost basis;
- a lock-free atomic JSON bridge supplies fresh quotes to the backend without sharing DuckDB's writer lock.

After all champion conditions are reconciled (P(Hold)>=0.93, $10 distance, 15-120 seconds, 91c fair cap,
fee, <=3c spread, positive displayed depth, and a 3c buffer), the existing recorder history contains only
**one 5m entry and zero 15m entries**. That single entry won, but one observation proves nothing.

### 4. Live state and validation defects

- pending and displayed price-to-beat rounds now share the same state object, preserving `late_entry` and
  path-plan fields through resolution;
- `auto_finetune --selftest` now validates the five named cheap-head steps instead of asserting a stale count;
- calibration reporting now separates repeated snapshot diagnostics from independent first-entry results;
- fixed UTC+2 time arithmetic was replaced by `Europe/Warsaw` timezone handling.

## Correct operator workflow

### Real-money rule today

**Do not place a real-money bet from this app yet.** Use the following as a paper protocol:

1. Use only exact Polymarket 5m or 15m rounds.
2. Ignore standalone ensemble/Kronos UP/DOWN calls.
3. Require the side to be already ahead by at least $10.
4. Require 15-120 seconds left and P(Hold)>=0.93.
5. Require a fresh exact-round executable ask.
6. Require `min(P(Hold), 0.91) - ask - taker_fee - 0.03 > 0`.
7. Log only the first qualifying entry per round.
8. Settle against the official market outcome and retain ask, fee, fill assumption, PnL, and ROI.
9. Treat every fade/round-trip event as paper research until an ask-to-exit-bid lifecycle is recorded.

### Promotion gate

Do not enable real-money automation until all are true on a non-overlapping forward sample:

- at least 500 trustworthy independent settled entries;
- positive fee-adjusted expectancy and profit factor above 1;
- positive lower confidence bound after fees/slippage;
- results remain positive separately for 5m and 15m and across at least two time periods;
- no single day/regime supplies most of the profit;
- max drawdown and worst-loss behavior fit a predefined capital limit;
- paper fills use executable ask/depth, not midpoint;
- code and model versions are frozen for the evaluation window.

## Startup and artifact consequence

The installed fade artifact is v4 and is intentionally rejected by the safer v5 serving contract. The next
head retrain must create v5 before fade probabilities appear again. Also, `start.bat` currently defaults to a
400-day historical window while the completion marker is for 360 days, so the next normal launch is expected
to force a long full retrain on the 16 GB laptop. This is intentional configuration state, not an instant boot.

At audit time, `champion_snapshots` last updated at 2026-07-01 05:43 UTC and Polymarket snapshots last
updated at 2026-06-30 22:24 UTC. That means the recorders/app were not currently accruing evidence. A stale
database is not a model failure, but no promotion clock advances while these processes are stopped.

## Validation performed

- full backend `compileall`: pass;
- changed-file Pyflakes undefined-name/static check: pass;
- frontend `node --check` and Vite production build: pass;
- server import without starting lifespan/background tasks: pass;
- saved main ensemble preflight: pass, 69-feature 5m/15m GLOBAL components load and predict;
- startup validate-only: pass, and confirms the pending forced 400-day retrain;
- fade v5, causal A/B, reversal, recorder, analyzer, and auto-finetune self-tests: pass;
- fresh exact-round quote bridge test: accepts a matching 0-second-old quote and rejects stale/wrong-anchor quotes;
- champion scenario test: rejects low P(Hold), small distance, excess time, invalid/wide spread, and accepts only
  a structurally valid fee-adjusted paper candidate;
- DuckDB reports run read-only; no app, retraining process, production order, or wallet action was started.

## Highest-value next work

1. Keep the Polymarket recorder continuously running and resolve its settlement backlog.
2. Accrue 500+ exact-open quote/outcome rounds before changing the decision policy.
3. Add an explicit paper-trade ledger with entry ask, exit bid/settlement, fees, depth, slippage, and model version.
4. Retrain and re-evaluate causal fade v5; reject it unless it beats timing-only on a temporal holdout.
5. Use sub-minute/tick data for touch-event ordering; one-minute OHLC is insufficient for fade execution research.
6. Keep direction models frozen as context. Do not add another model family to chase the measured coin flip.

## Bottom line

The strongest current instrument is conditional P(Hold), not direction and not fade. It can estimate whether
an already-ahead side remains ahead, but a high probability is not a profitable bet when the ask already
prices it. The only legitimate path to profitability is repeated evidence that conservative P(Hold) exceeds
the executable market price after fee, buffer, slippage, and failures. That evidence does not exist yet.
