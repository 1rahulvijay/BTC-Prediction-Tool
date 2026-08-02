# Phase 5 Standalone Research Suite

Date: 2026-08-02

## Decision

All 42 requested standalone experiment packages are implemented and executable. Each has its
own frozen protocol, `run.py`, `selftest.py`, and README. The suite is isolated from production
models and trading code.

This implementation does not create evidence that the available data does not contain. The
current economic conclusion is:

```text
PASS_CANDIDATE economic strategies: 0
capital authority:              false
production wiring:              none
```

## Shared Research Contract

Every script uses:

1. TRAIN, CALIBRATION, POLICY_SELECTION, and UNTOUCHED_TEST in chronological order.
2. Purge rows between partitions.
3. Model and threshold selection before untouched testing.
4. Exact Binance or Polymarket costs with 1.5x and 2x stress.
5. No-trade and matched-random controls where an action policy exists.
6. Day/week block confidence bounds, minimum 10 days and four weeks for promotion.
7. Profit factor, drawdown, expected shortfall, turnover, capital duration and concentration.
8. Immutable JSON reports with protocol, data-slice, Git-state and suite-code hashes.
9. Fail-closed `BLOCKED_DATA`, `BLOCKED_SCHEMA`, or `INSUFFICIENT_SAMPLE` outcomes.

For Binance, `ret_5m` is explicitly declared as USD and divided by entry `close` before a
basis-point cost is applied. A five-minute action reserves capital for five minutes. Random
controls preserve action count, sign balance, UTC day and holding duration.

## Validation Performed

| Validation | Result |
|---|---:|
| Experiment directories | 42 |
| Frozen protocols | 42 |
| Standalone `run.py` entry points | 42 |
| Standalone `selftest.py` entry points | 42 |
| Cross-process standalone self-tests | 42/42 pass |
| Corrected real-data smoke run | 42/42 completed |
| Python compile | PASS |
| Pyflakes | PASS |
| Phase 5 pytest | 4 passed |
| Repository workflow gate | 84/84 steps pass (`backend/run_ci_locally.py --all`) |
| Frontend build and high-severity audit | PASS |
| `git diff --check` | PASS |

Canonical corrected smoke run:

```text
data/research/phase5_standalone/_suite_runs/20260802T111027Z/suite_summary.json
```

It used the newest 5,000 rows per source to validate real schemas and runtime paths. It is not a
full economic backtest. Status counts were:

```text
FAIL_NO_EDGE         6
FAIL_AFTER_COSTS     1
FAIL_UNSTABLE        1
INSUFFICIENT_SAMPLE 14
BLOCKED_DATA        20
PASS_CANDIDATE       0
```

## Experiment Ledger

| # | Experiment | Corrected smoke status | Meaning |
|---:|---|---|---|
| 01 | Alpha extractability upper bound | FAIL_NO_EDGE | Oracle opportunity exists, but locked models extracted no test actions. |
| 02 | History/live transportability | FAIL_UNSTABLE | Recent rows are distinguishable from older rows. |
| 03 | Feed incremental economic value | INSUFFICIENT_SAMPLE | Eight non-overlapping test actions in the smoke slice. |
| 04 | Oracle/current disagreement | BLOCKED_DATA | Central Oracle artifacts were overwritten; paired reconstruction is impossible. |
| 05 | Signal context sign reversal | INSUFFICIENT_SAMPLE | Eight non-overlapping model actions; fixed context diagnostics were still written. |
| 06 | Dynamic crypto factor residual | BLOCKED_DATA | No causal cross-asset factor panel. |
| 07 | Residual continuation/reversion | BLOCKED_DATA | Depends on experiment 06's residual event dataset. |
| 08 | Cross-exchange dislocation decay | FAIL_AFTER_COSTS | Positive smoke gross PnL did not survive costs. Full source run was FAIL_NO_EDGE. |
| 09 | Spot/perpetual basis residual | INSUFFICIENT_SAMPLE | Two test actions in smoke; five in the corrected 100k run. |
| 10 | Synthetic metaorder segmentation | INSUFFICIENT_SAMPLE | Larger run covered only about 15 minutes, below five-day gate. |
| 11 | Metaorder continuation | INSUFFICIENT_SAMPLE | No locked economic actions. |
| 12 | Metaorder exhaustion reversal | INSUFFICIENT_SAMPLE | No locked economic actions. |
| 13 | Core/reaction overshoot | INSUFFICIENT_SAMPLE | No locked economic actions. |
| 14 | Impact-efficiency decay | INSUFFICIENT_SAMPLE | No locked economic actions. |
| 15 | Liquidity-withdrawal hazard | INSUFFICIENT_SAMPLE | Smoke target lacked both classes; full diagnostic is non-trading without base-policy replay. |
| 16 | Event-language surprise | INSUFFICIENT_SAMPLE | Smoke too short; larger diagnostic is non-trading by protocol. |
| 17 | Surprise propagation | INSUFFICIENT_SAMPLE | No locked economic actions. |
| 18 | Surprise as volatility | BLOCKED_DATA | Magnitude prediction has no executable magnitude instrument in the dataset. |
| 19 | Polymarket probability elasticity | FAIL_NO_EDGE | Smoke gross PnL non-positive; full run had only four selected actions. |
| 20 | Elasticity residual closure | FAIL_NO_EDGE | Smoke gross PnL non-positive; full run had only four selected actions. |
| 21 | Deadline convexity | FAIL_NO_EDGE | Smoke gross PnL non-positive; full run had 11 selected actions. |
| 22 | New-round inheritance | INSUFFICIENT_SAMPLE | Full run had 50 selected actions. |
| 23 | Boundary resonance | FAIL_NO_EDGE | Smoke gross PnL non-positive; full run had 84 selected actions. |
| 24 | Post-settlement unwind | BLOCKED_DATA | Missing canonical settlement-to-Binance causal join. |
| 25 | Cross-expiry consistency | INSUFFICIENT_SAMPLE | Full causal data produced 36 positive lock observations, below 100-action gate. |
| 26 | Complete-set lock frequency | BLOCKED_DATA | Missing open-position plus opposite-depth paths. |
| 27 | Alpha context specialization | BLOCKED_DATA | No canonical per-decision candidate evidence. |
| 28 | Alpha redundancy | BLOCKED_DATA | No canonical per-decision candidate evidence. |
| 29 | Alpha collision | BLOCKED_DATA | No canonical per-decision candidate evidence. |
| 30 | Alpha decay | BLOCKED_DATA | No canonical per-decision candidate evidence. |
| 31 | Regime revival | BLOCKED_DATA | No canonical per-decision candidate evidence. |
| 32 | Reward transport | BLOCKED_DATA | No canonical per-decision candidate evidence. |
| 33 | Enter now versus wait | FAIL_NO_EDGE | Smoke gross PnL non-positive; full run had four selected actions. |
| 34 | Hold/exit/reduce/switch/lock | BLOCKED_DATA | Missing one causal open-position population with all action arms. |
| 35 | Conformal net-EV gate | BLOCKED_DATA | No canonical candidate predictions and realized PnL. |
| 36 | Capacity curve | BLOCKED_DATA | Capacity is undefined until an alpha passes. L2 depth is available. |
| 37 | Anytime-valid evidence | INSUFFICIENT_SAMPLE | Atomic opportunity ledger has zero joined economic outcomes. |
| 38 | Timing placebo | BLOCKED_DATA | No canonical per-decision candidate evidence. |
| 39 | Sign/label randomization | BLOCKED_DATA | No canonical per-decision candidate evidence. |
| 40 | Parameter neighborhood | BLOCKED_DATA | No canonical per-decision candidate evidence. |
| 41 | Profit concentration | BLOCKED_DATA | No canonical per-decision candidate evidence. |
| 42 | Negative control | BLOCKED_DATA | No canonical per-decision candidate evidence. |

## Larger Targeted Runs

The corrected 100,000-row BTC run is under `20260802T_phase5_matrix_100k_final`:

- Extractability: `FAIL_NO_EDGE`; constrained oracle had opportunity but models selected no test actions.
- Feed ablation: `INSUFFICIENT_SAMPLE`; no all-source test actions after costs.
- Context reversal: `INSUFFICIENT_SAMPLE`; no model test actions. Fixed CVD context tables remain diagnostics only.
- Basis residual: `INSUFFICIENT_SAMPLE`; five actions.

The 500,000-event run and its corrected span gate are under
`20260802T_phase5_targeted_spanfix`. The events cover roughly 15 minutes, so the earlier apparent
segmentation persistence is not stable evidence.

The full causal Polymarket checkpoint run is under `20260802T_phase5_targeted_final`. No test
reached the 100-action minimum: elasticity 4, residual closure 4, deadline 11, opening 50,
boundary 84, cross-expiry 36, and enter/wait 4.

The full cross-venue run under `20260802T_phase5_market_full` was `FAIL_NO_EDGE`. The L2 hazard
diagnostic ran, but economic use remains blocked until it can be replayed as a veto against an
unchanged base strategy.

## Retracted Phase 5 Outputs

Immutable reports are never edited. Retractions are recorded in
`research/phase5_standalone/retractions.json`.

1. The first `phase5_matrix_100k` apparent candidates are invalid: `ret_5m` was a dollar move
   treated as a fractional return.
2. The first two metaorder segmentation passes are invalid for stability: 500,000 events covered
   only about 15 minutes and the protocol initially lacked a five-day minimum.

Neither result may be quoted or promoted.

## What Unblocks The Remaining Scripts

1. Build a causal cross-asset factor panel for experiments 06-07.
2. Preserve paired Oracle/current decisions going forward; historical overwritten predictions
   cannot be reconstructed.
3. Build a causal settlement-to-Binance event join for experiment 24.
4. Persist open-position quote/depth paths with every action arm for experiments 26 and 34.
5. Produce `data/research/phase5_candidate_evidence.parquet` only after a candidate passes an
   earlier experiment; this unlocks 27-35 and 38-42.
6. Accumulate at least 100 joined opportunity-ledger outcomes for experiment 37.

No script automatically exports or deploys a model. A later challenger export must remain
`capital_authority=false` and pass independent forward validation.
