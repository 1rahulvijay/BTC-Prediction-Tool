# Standalone Alpha Laboratory

This directory contains isolated economic experiments. A lane must prove positive executable
net value before it can influence serving, paper trading, sizing or exits.

Nothing here is capital authority. Research scripts must not import live decision code, write
serving artifacts or mutate trading state.

## One-Command Campaign

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research_lanes\run_all_experiments.py --maximum-rows 100000
```

The command runs sequentially in separate child processes so memory is returned after each
stage. It executes:

- all 42 frozen Phase 5 packages;
- all 46 frozen Phase 5B packages;
- all nine Phase 5C diagnostics;
- Binance cost clearance, volatility, time-phase, basis and causal path extensions;
- Polymarket market-prior, residual, complete-set, state-atlas, disagreement, entry-timing,
  settlement-sensitivity and hypothetical maker-markout lanes.

Polymarket inputs are copied into the immutable campaign directory and hashed before testing.
Live DuckDB writers are never stopped or copied. A test that needs a live-locked or missing
source returns `BLOCKED_DATA` instead of crashing or inventing a proxy.

Latest canonical report:
[Standalone Alpha Laboratory - Complete Campaign](../docs/active/STANDALONE_ALPHA_LAB_COMPLETE_CAMPAIGN_2026-08-13.md)

Detailed inventories:

- [Batch 2](BATCH_2_REPORT.md)
- [Batch 3](BATCH_3_REPORT.md)
- [Complete test inventory](TEST_INVENTORY.md)

## Current Verdict

Validated run `20260813T063543Z` executed all 19 campaign stages successfully. Across the 97
frozen packages and 21 directly summarized named lanes:

- zero strategy earned promotion;
- zero economic configuration had a positive family-adjusted lower bound after declared costs;
- zero state-atlas cell survived family-wise correction;
- all causal fixed-delay Polymarket entry intervals spanned zero.

The strongest diagnostics are not trades:

- volatility expansion predicts movement (AUC 0.765), not direction or an executable payoff;
- 30-minute compression selects larger absolute moves, but supplies no direction/instrument;
- spot/perpetual basis and microbasis revert with high hit rates but only about 1 bps gross
  against a 12 bps optimistic round trip;
- Polymarket's own price beats the standalone model, especially when they disagree;
- complete-set arbitrage is mechanically real but totaled only $43.82 of theoretical top-book
  value over ten recorded days;
- maker upper bounds cannot be promoted without actual fill, queue and adverse-selection data.

## Scientific Status Versus Process Status

`PASS` in the master command means the experiment process completed and wrote a report. It does
not mean the hypothesis passed. Scientific statuses include:

- `FAIL_NO_EDGE`: measured result did not clear economics;
- `FAIL_UNSTABLE`: result failed robustness/stability;
- `INSUFFICIENT_SAMPLE`: correct data exists but the independent sample is too small;
- `BLOCKED_DATA`: the required causal source or execution outcome does not exist;
- `DIAGNOSTIC_ONLY`: useful state information with no proven executable payoff.

## Promotion Rule

A candidate remains research-only until all of the following are true:

1. chronological out-of-sample net EV is positive at executable prices;
2. the day/round-clustered lower confidence bound is positive;
3. multiple comparisons are controlled;
4. fees, spread, slippage, latency and financing are included;
5. results survive cost/latency stress, regime slices and concentration checks;
6. the candidate beats a simple baseline and a matched null;
7. independent forward shadow and paper windows confirm it.

Accuracy, AUC, a positive point estimate or an optimistic fill upper bound is not promotion.

## Blocked Frontier

The main untested frontier requires data the current historical stores do not contain:

- actual maker fills, queue position and fill-conditioned markouts;
- synchronized sub-second BTC and Polymarket quote revisions;
- per-level L2 add/cancel/execute events;
- actual funding payment timestamps/rates and financing cash flows;
- synchronized second-venue funding, ETH/SOL relative-value and Deribit chain history;
- causal liquidation and open-interest event histories.

Keep the corresponding recorders running. Historical APIs cannot reconstruct executable PM
bid/ask ladders or queue events that were never captured.

No file in this directory authorizes real-money trading.

## Append-only update - Action-value brief batch (2026-08-13)

Seven additional runnable families were executed from the later research briefs using a frozen
60/10/30 chronological protocol and 12 bps Binance cost. None produced a positive
family-adjusted after-cost lower bound. Detailed results:

- Runner: `python research_lanes/run_action_value_brief_batch.py`
- [BRIEF_ACTION_VALUE_BATCH_REPORT_2026-08-13.md](BRIEF_ACTION_VALUE_BATCH_REPORT_2026-08-13.md)
- `results/action_value_brief_batch_20260813T071004Z.json`

The important distinction is unchanged: movement can be forecast (5m AUC 0.770), but the tested
direction, timing, failure-filter, breakout and position-management policies did not monetize it.

## Append-only update - complete brief coverage (2026-08-13)

The full proposal-to-evidence crosswalk is in
[COMPLETE_DISCUSSION_TEST_COVERAGE_2026-08-13.md](COMPLETE_DISCUSSION_TEST_COVERAGE_2026-08-13.md).
It classifies all 60 questions from the first brief and all 35 sections from the second brief,
including exact reasons for every test that was not run.

## Append-only update - Multi-engine brief batch (2026-08-13)

The later 40-question multi-engine brief is fully reconciled in
[MULTI_ENGINE_BRIEF_BATCH_REPORT_2026-08-13.md](MULTI_ENGINE_BRIEF_BATCH_REPORT_2026-08-13.md).

Five newly answerable families ran in one sequential standalone batch:

- recorded PM reference versus causally completed Binance spot and derived perp;
- strong spot/perp CVD disagreement;
- funding event behavior and next-rate forecasting;
- $100/$500/$1,000 psychological-level continuation;
- direction confidence-threshold economics.

No family produced a promotable configuration. The recorded PM reference result is useful for
input-source integrity, while flow disagreement is useful only as a movement warning. Neither is
an executable trade edge. All remaining questions have explicit data, execution, evidence or
design blockers in the report.

- Runner: `python research_lanes/run_multi_engine_brief_batch.py`
- Result: `results/multi_engine_brief_batch_20260813T072836Z.json`
- Capital authority: **false**

## Current registers (updated 2026-08-14)

Read these two first; everything else is a snapshot of a moment.

| File | What it holds |
|---|---|
| `TEST_INVENTORY.md` | every lane that has RUN, with its verdict and independence-unit count |
| `CANNOT_RUN_INVENTORY.md` | every lane that CANNOT run, the blocker, and what removes it |

Batch reports, newest first: `BATCH_5_REPORT.md`, `BATCH_4_REPORT.md`, `BATCH_3_REPORT.md`,
`BATCH_2_REPORT.md`. Per-lane detail lives in `<lane>/REPORT.md`.

`LATEST_RESULTS.md` is the Batch 1-3 campaign document; it carries a currency notice and an
appendix pointing at the later batches.

**Status: 29 lanes run, 0 tradeable edges.** Nothing further is runnable on current data - the
queue is short of independent observations, not ideas. `CANNOT_RUN_INVENTORY.md` names the single
action that unblocks the largest set.

### The standing rule

Before adding a lane, state its **independence unit count**, not its row count. Below ~30 units a
lane produces a number, not evidence. Three separate artifacts in Batch 4-5 each produced an
apparent edge that survived a naive bootstrap; see the batch reports for the checks that removed
them.
