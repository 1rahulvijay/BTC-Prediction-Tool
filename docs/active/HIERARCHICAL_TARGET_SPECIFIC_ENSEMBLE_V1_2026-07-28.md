# Hierarchical Target-Specific Ensemble V1

Date: 2026-07-28

Status: **research kernel implemented; evidence collection and target-specific
training are not yet complete; no serving, paper-order or live-order path**

## Decision

The app must never combine unrelated predictions through a majority vote.
Models may share an ensemble only when they predict the same economic object
under the same venue, instrument, horizon and outcome definition.

The implemented hierarchy is:

```text
Market data and feed health
    -> explicit target ownership
    -> immutable OOF/forward forecast ledger
    -> same-target constrained ensembles
    -> regime-conditioned expert weights
    -> reliability and disagreement controls
    -> action-specific post-cost return distributions
    -> conservative q20 expected-value gate
    -> independently promoted strategy allocator
    -> WAIT unless every gate passes
```

This does not replace the existing direction ensemble, Polymarket Champion or
Binance paper engine. It creates the governed research layer needed to test
whether specialist combinations add real information.

## Campaign Status

| Campaign | Implemented now | Still evidence-gated |
|---|---|---|
| `MODEL_FORECAST_LEDGER_V1` | Universal immutable forecast/outcome DuckDB API, strict provenance, target contracts, integrity verification, and provenance-correct forward adapters | Source recorders must accumulate resolved evidence; unavailable source heads remain explicit blockers |
| `TARGET_SPECIFIC_STACKING_V1` | Equal, inverse-Brier and non-negative simplex stackers; ledger-row pivot; market-prior floor | A stacker cannot fit until one target has enough aligned OOF/forward forecasts |
| `REGIME_MIXTURE_OF_EXPERTS_V1` | Global fallback plus per-regime constrained fits and soft regime routing | Regime-specific fits require the frozen minimum sample count |
| `MODEL_RELIABILITY_HEAD_V1` | Reliability contract, multiplicative quality score, disagreement statistics and fail-closed weight adjustment | A learned reliability classifier requires forward error labels and remains deferred |
| `EXECUTABLE_EV_ENSEMBLE_V1` | Full net-return quantile contract and `q20 - tail reserve` action selector | Action distributions must come from validated cost/fill/return heads |
| `ONLINE_EXPERT_WEIGHTING_SHADOW_V1` | Append-only hash-chained forward-loss ledger, slow exponential updates, bounds and timestamp rollback | Shadow comparison needs at least 50 complete resolved updates per target/regime |
| `MULTI_ALPHA_PORTFOLIO_V1` | Correlation-aware allocation with venue, directional and settlement caps | Returns no allocation until two engines have independent promotion evidence, 1,000 decisions and eight weeks |

The campaign protocol is:

```text
backend/research/hierarchical_ensemble_v1/frozen_protocol.json
```

## Model Ownership

Every registered model owns a `TargetContract`:

```text
target name
role
venue
instrument
horizon seconds
outcome semantics
```

The full contract is hashed. An ensemble compatibility check requires exact
contract equality. A five-second barrier-direction model therefore cannot vote
in a one-hour Polymarket settlement stacker even if both outputs are called
`P(UP)`.

Supported roles are:

```text
SETTLEMENT
REPRICING
DIRECTION_BARRIER
MAGNITUDE
FILL
TOXICITY
COST
RETURN
CARRY
REGIME
RELIABILITY
```

Each registration also contains explicit allowed uses. Matching labels alone
are insufficient if a model is not approved for that ensemble function.

## Universal Forecast Ledger

Code:

```text
backend/quant_platform/forecast_ledger.py
```

Tables:

| Table | Purpose |
|---|---|
| `model_forecasts` | Immutable model output, target contract and complete provenance |
| `model_forecast_outcomes` | Separately resolved outcome and execution economics |

Every forecast records:

```text
forecast and training-cutoff timestamps
market and candidate identity
model ID and version
code commit
dataset, feature-schema and protocol hashes
target role and complete target-contract hash
evidence kind
probability, mean and/or ordered q10/q20/q50/q80/q90
regime and data quality
immutable payload hash
```

Every resolved outcome records:

```text
actual outcome
official resolution source
immutable payload hash
```

Execution economics are nullable because settlement and repricing labels do
not imply a fill, fee, slippage or return. When a target does own execution
economics, supplied values must be finite and costs cannot be negative. The
ledger never fills unavailable economics with zero.

The API rejects:

- a training cutoff that is not strictly earlier than the forecast;
- missing provenance;
- invalid probabilities or unordered quantiles;
- an ID reused with different content;
- resolution before forecast time;
- a second, different outcome for the same forecast;
- meta-training requests containing `IN_SAMPLE` or `LOCKED_TEST` rows.

Only `OOF` and genuinely `FORWARD` rows can train a meta-model. The locked test
is for final evaluation, not meta-training.

Default ledger path:

```text
data/research/model_forecast_ledger_v1.duckdb
```

The live server does not write this file. The report command runs a one-shot,
research-only adapter pass that reads each campaign database and appends only
provenance-complete `FORWARD` forecasts. Source databases remain independently
owned; adapters never write them.

Adapter code:

```text
backend/research/forecast_adapters_v1/
```

Implemented row adapters:

| Source | Forecasts admitted | Resolved label |
|---|---|---|
| `POLY_1H_DIGITAL_FAIR_VALUE_V1` | Market prior, distance/time, volatility mixture at the six frozen reporting checkpoints | Finalized Binance 1h side only after Polymarket reconciliation |
| `POLYMARKET_REPRICING_SHADOW_V1` | Separate UP/DOWN baseline and evidence probabilities for 1c ask worsening within 5s | First valid selected-side +5s quote with measured lag no greater than 1s |
| `BINANCE_MAKER_CONVERSION_V1` | Separate 5s/15s direction, movement and round-trip event probabilities | Not yet available: exact spot-path outcomes are not persisted by the current maker recorder |

Defined but deliberately blocked:

- UP/DOWN 15-second Polymarket ask-worsening heads do not exist in the source
  campaign.
- Polymarket queue-authoritative fill, adverse-selection and taker-cost
  specialist forecasts do not exist.
- Binance maker fill, cost and net-return specialist forecasts do not exist.
- Binance paper confidence and score fields are rule outputs, not calibrated
  target probabilities; their fill/trade rows are outcomes, not forecasts.

No blocked target falls back to another model or horizon.

## Target-Specific Stacking

Code:

```text
backend/quant_platform/target_ensemble.py
```

Implemented baselines:

1. Equal weight.
2. Inverse Brier weight.
3. Constrained non-negative Brier stacker.
4. Regime-conditioned constrained stacker.

All weights satisfy:

```text
w_i >= 0
sum(w_i) = 1
```

Settlement ensembles require an executable market prior and enforce a minimum
weight of at least 50%. This prevents a small research model from silently
dominating the market without independent evidence.

The diversity report calculates:

```text
probability correlation
error correlation
incremental Brier gain over the current champion
```

A candidate is useful only when it improves the ensemble after the champion
forecast is already known. Different architecture names are not evidence of
different errors.

The supplied fit function intentionally does not tune thresholds, choose
regimes or open a locked test. Those steps belong in a frozen target-specific
campaign with purging, embargo and chronological calibration.

## Reliability And Disagreement

Code:

```text
backend/quant_platform/model_reliability.py
```

Current reliability is a transparent fail-closed composite:

```text
R =
    data quality
  * distribution quality
  * calibration quality
  * regime familiarity
  * stability quality
```

Base weights are multiplied by `R` and renormalized. If total reliable mass is
below the frozen minimum, the result is empty and the downstream action is
`WAIT`.

Disagreement reports the probability mean, standard deviation, range and
maximum pair gap. High disagreement is information for reducing size, waiting
or testing relative value. It never increases leverage.

The learned reliability head is deliberately not trained yet. It requires
forward forecast errors, missingness, drift, calibration and stability labels.
Training it on the same history used by the base models would manufacture
confidence rather than measure it.

## Executable EV Selection

Code:

```text
backend/quant_platform/executable_ev.py
```

Each candidate action provides a post-cost distribution:

```text
mean
q10
q20
q50
q80
q90
expected shortfall
P(net return > 0)
estimated cost
reliability
data quality
execution feasibility
risk approval
```

The selector evaluates:

```text
conservative score = q20(net return) - tail-risk reserve
```

It chooses the highest score only when that score is positive and reliability,
data, execution and risk gates all pass. Otherwise the result is `WAIT` with
machine-readable rejection reasons.

Accuracy is not the objective. The selected action must survive fees, spread,
slippage, impact, fill uncertainty and adverse-tail reserve.

## Online Expert Shadow

Code:

```text
backend/quant_platform/online_expert_weighting.py
```

The online layer:

- accepts resolved `FORWARD` evidence only;
- keeps base models frozen;
- uses a maximum learning rate of 0.10;
- waits for a minimum number of complete updates;
- applies minimum and maximum model weights;
- stores an append-only hash chain;
- reconstructs any historical state by timestamp cutoff;
- has no serving pointer and declares `shadow_only = True`.

One profitable hour cannot replace the ensemble. Missing experts in an update
make that update ineligible for replay.

## Multi-Alpha Portfolio

Code:

```text
backend/quant_platform/multi_alpha_portfolio.py
```

The allocator considers only independent promoted engines with:

```text
at least 1,000 forward decisions
at least eight forward weeks
positive expectancy lower bound
positive q20 net return
finite tail risk, capacity and drawdown
valid liquidity and calibration
unique promotion evidence and unique alpha family
complete pairwise correlation data
```

It returns no allocation with fewer than two eligible engines or missing
correlations. Allocations are capped by strategy, venue, absolute BTC direction
and Polymarket settlement exposure. The output is research notional only; no
order type or order-submission method exists.

## Readiness Report

Run:

```powershell
.\research\launchers\report_hierarchical_ensemble.bat
```

Optional paths:

```powershell
.\research\launchers\report_hierarchical_ensemble.bat `
  --ledger data\research\model_forecast_ledger_v1.duckdb `
  --output data\research\hierarchical_ensemble_v1\report
```

Outputs:

```text
summary.json
target_readiness.csv
adapter_readiness.csv
```

`adapter_readiness.csv` contains every catalogued adapter, including zero-row
targets, with source status, blocker, rows seen, forecasts appended and
outcomes appended. `target_readiness.csv` contains ledger slices that actually
exist. The report labels a target slice meta-training-ready only when it
contains at least two models, resolved candidates and eligible OOF/forward
evidence. This is a data-readiness signal, not a promotion result.

## Validation

Executable test:

```powershell
python -m backend.research.hierarchical_ensemble_v1.selftest
python -m backend.research.forecast_adapters_v1.selftest
```

The test uses temporary real DuckDB files and proves:

- model-role registration and mismatch rejection;
- immutable forecast and outcome writes;
- integrity hashes;
- OOF-only ledger extraction;
- ledger-to-stacker panel construction;
- non-negative normalized weights;
- settlement market-prior floor;
- direct target-contract mismatch rejection;
- regime fits and global fallback;
- reliability-adjusted weights and disagreement;
- default `WAIT` for negative conservative EV;
- bounded forward-only online updates and rollback;
- hash-chain verification;
- no portfolio allocation with one alpha;
- allocation only with two independently evidenced alphas.

The self-test is included in Linux and Windows CI.

## What Remains

The next work is evidence accumulation and missing source truth, not another
model family:

1. Keep the source recorders running with clean committed code so new evidence
   contains admissible provenance.
2. Retrain the event bundle explicitly when desired so its new dataset hash,
   feature hash and training cutoff are embedded. This change does not retrain
   it automatically.
3. Persist exact spot-path outcomes for Binance event forecasts before using
   them in stacking.
4. Build and freeze real 15-second ask-worsening heads rather than copying 5s
   probabilities.
5. Train fill, toxicity, cost and return heads only after defensible
   queue-aware forward labels exist.
6. Generate purged OOF forecasts from frozen base bundles.
7. Accumulate aligned forward forecasts and outcomes.
8. Run equal, inverse-Brier and constrained methods on the same frozen splits.
9. Compare against best standalone, equal weight, market and no-trade baselines.
10. Keep online weights in shadow and the portfolio empty until independent
    promotion gates pass.

No claim of improved accuracy or profitability is justified by this
infrastructure alone. It makes future claims harder to fake and prevents
unrelated predictions from being blended into a misleading signal.
