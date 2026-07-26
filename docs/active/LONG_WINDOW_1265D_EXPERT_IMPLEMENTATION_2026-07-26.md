# 1,265-Day Multi-Window Expert Implementation

Date: 2026-07-26

Purpose: implement the long-window engineering and research controls proposed
in the external ChatGPT reviews without overstating unrun experiments as proven
accuracy or profitability.

## Executive Verdict

The requested long-window infrastructure is implemented. The 1,265-day data
download, full model training, and full experiment grid have **not** completed
yet.

The current matrix remains:

```text
requested_days: 360
rows: 518,400
actual_span: 360.0 days
```

The code now refuses to call that matrix 1,265-day data. A real-matrix smoke
test exposed this mismatch and was quarantined under:

```text
data/research/multiwindow_experts/INVALID_20260726_094626_span_contract
```

Those smoke metrics are not 1,265-day evidence and must not be used.

## Implementation Status

| Requested item | Implementation | Evidence status |
|---|---|---|
| 1,265-day launcher configuration | `start.bat` uses one 1,265-day data/training contract | Implemented; long run not completed |
| Mandatory model artifact/data manifests | Main bundle, standalone heads, and multi-window runs receive hash-backed manifests | Implemented |
| Reject data/span/hash mismatches | Load and save paths fail closed on days, span, end time, rows, source hash, matrix hash, feature schema, and artifact hash | Implemented and tamper-tested |
| True OHLC reconstruction | Minute open is first aggregate trade, high/low are extrema, close is last trade | Implemented |
| Official OHLC parity | Reconstructed OHLC is checked against the cached official Binance 1m tail | Implemented; runs during matrix build |
| Monthly source coverage | Per-month minute/source coverage, gaps, duplicates, NaNs, zeros, constants, and OHLC invariants | Implemented |
| W90/W400/W1265 harness | Four frozen experts: W90, W400, W1265_RECENCY, W1265_SIMILARITY | Implemented; full grid not run |
| Purged rolling cross-fitting | Rolling validation with `60 + horizon` row purge | Implemented |
| Regime-similarity weighting | Robust distance over causal volatility/trend/regime features | Implemented in main training and challenger harness |
| 40K/100K/250K/all direction experiments | Same final chronological test and purge for every budget | Implemented; full grid not run |
| 6K/25K/50K stacker experiments | Meta-model uses purged base OOF probabilities only | Implemented; full grid not run |
| Regime-balanced TCN sampling | 50% recent, 25% historical-regime, 25% historical-tail | Implemented and unit-tested |
| Target-specific windows | Central policy in `backend/target_windows.py`; target-name resolution is unit-tested | Implemented as a frozen research policy; production choice awaits evidence |
| Oracle window comparison | All experts emit probabilities on identical IDs; `window_expert_shadow.py` scores identical new matrix rows and preserves later resolutions | Implemented; no 1,265-day forward observations yet |

## Data Integrity

### True aggregate-trade OHLC

`backend/edge_probe.py` now constructs:

```text
open   = first aggregate-trade price in the minute
high   = maximum aggregate-trade price in the minute
low    = minimum aggregate-trade price in the minute
close  = final aggregate-trade price in the minute
volume = sum aggregate-trade quantity
```

The old `open = close` approximation was removed.

### Official parity gate

`backend/build_research_matrix.py` compares the reconstructed tail against the
small cached official Binance 1m kline file. It records:

```text
overlap minutes
median absolute OHLC difference
99th-percentile absolute OHLC difference
maximum difference by field
```

Frozen defaults:

```text
median absolute difference <= $0.001
p99 absolute difference <= $0.011
minimum overlap >= 100 minutes
```

If an official reference is available and parity fails, the previous matrix is
preserved and training aborts.

### Monthly quality gate

Every observed month records:

```text
expected minute rows
actual unique minute rows
minute coverage
trade-feature coverage
cross-venue coverage
duplicate rows
maximum contiguous gap
core OHLCV null rows
invalid OHLC rows
feature NaN percentage
feature zero percentage
constant feature count
```

Frozen gates:

```text
minute coverage >= 98%
trade-feature coverage >= 98%
cross-venue coverage >= 98%
maximum unexplained gap <= 15 minutes
duplicates = 0
future timestamps = 0
core null rows = 0
invalid OHLC rows = 0
```

Outputs:

```text
data/research_matrix_monthly_quality.json
data/research_matrix_monthly_quality.csv
```

The previous implementation also had a missing-source bug: a missing marker
column could be represented by zeros and then counted as 100% non-null coverage.
Missing sources now correctly report 0% coverage.

## Artifact Identity

`backend/artifact_identity.py` is the shared source of truth.

Each production artifact records:

```text
requested_days
matrix_requested_days
actual_start_ts_ms
actual_end_ts_ms
actual_span_days
row_count
training_data_hash
source_manifest_hash
feature_schema_hash
code_hash
split timestamps
calibration timestamps
full_refit
artifact_hash
```

Main ensemble:

```text
data/saved_models/artifact_manifest.json
```

Standalone heads:

```text
<artifact>.pkl.manifest.json
```

Multi-window research runs:

```text
data/research/multiwindow_experts/<run>/artifact_manifest.json
```

Strict loading is enabled by:

```bat
BTC_STRICT_ARTIFACT_IDENTITY=1
```

An invalid identity is checked before the first main-model file is written, so
a bad matrix cannot partially overwrite the incumbent bundle.

## Main Ensemble Long-Window Changes

### Sample weights

The production default is:

```text
sample_weight
= recency_weight
* regime_similarity_weight
* data_quality_weight
```

The monthly admission gate rejects bad data, so admitted rows have
`data_quality_weight = 1`.

Regime similarity uses causal inputs available before the target:

```text
ATR
ADX
volume ratio
5m/15m realized volatility
EWMA volatility
variance ratio
volatility term structure
```

Weights are computed against the latest 1,440 training rows using robust
median/IQR distance. No validation or future row enters the reference state.

### TCN sampling

The old behavior retained only the latest 25,000 sequences. The new behavior
uses:

```text
50% recent contiguous sequences
25% historical regime-balanced sequences
25% historical tail/large-state-change sequences
```

Selection uses input sequences only, not labels.

### Main stacker purge

The main OOF stacker now uses:

```text
TimeSeriesSplit
gap = min(LOOKBACK + horizon, one eighth of stacker rows)
```

This prevents overlapping sequence windows and target horizons from touching
across fold boundaries.

### Architecture version

The main contract is bumped to v12. The change forces one deliberate retrain
and prevents v11 artifacts from being treated as equivalent.

## Multi-Window Research Harness

Files:

```text
backend/target_windows.py
backend/research/multiwindow_experiment.py
run_multiwindow_experts_1265d.bat
```

Experts:

```text
W90
W400
W1265_RECENCY
W1265_SIMILARITY
```

Default full model families:

```text
Logistic Regression
HistGradientBoosting
Random Forest
XGBoost
LightGBM
CatBoost
```

The script trains one model at a time, writes completed models separately, then
deletes references and runs garbage collection. This avoids retaining all fitted
models in 16 GB RAM.

### Causal feature set

The harness excludes all columns prefixed `future_` and all labels. It uses
relative, causal features:

```text
1m return in bps
1m range/body in bps
log volume and 60m volume z-score
taker imbalance
realized volatility and term structure
trade intensity and volume acceleration
VPIN
compression/range/shock features
CVD and large-trade flow
funding velocity
spot/perpetual CVD divergence and basis
session sine/cosine and weekend state
```

Raw BTC price is not a predictive input.

### Output files

Each successful run writes:

```text
metrics.csv
budget_experiments.csv
oracle_shadow_predictions.parquet
run_manifest.json
artifact_manifest.json
models/<target>/<expert>__<family>.joblib
```

Every prediction row contains one shared `prediction_id`, timestamp, horizon,
fold, actual outcome, and every available expert probability.

The causal selector for fold N uses only Brier scores from folds before N. A
retrospective per-row oracle is never deployable and is not allowed to promote a
model.

### Forward shadow scoring

After a valid multi-window run exists and the research matrix has acquired new
rows, run:

```powershell
.\run_multiwindow_shadow.bat
```

`backend/research/window_expert_shadow.py` verifies the run artifact hash,
refuses any run that is not explicitly shadow-only, loads one model at a time,
and scores every expert on identical timestamps. It preserves unresolved rows
and reconciles them when the corresponding future outcome becomes available.
It cannot promote or route a trading decision.

The output is:

```text
data/research/multiwindow_experts/<run>/forward_shadow_predictions.parquet
```

The matrix must contain genuinely new causal rows before this file can provide
forward evidence. Re-scoring the training span is not forward proof.

## Correct Run Order

### 1. Build, validate, and train the 1,265-day app

```powershell
.\start.bat
```

The launcher will:

1. Resume cached source downloads.
2. Build true aggregate-trade OHLC.
3. run official OHLC parity.
4. run monthly coverage gates.
5. write the matrix manifest and hashes.
6. train standalone heads sequentially.
7. train the 98% candidate.
8. test the untouched recent 2%.
9. refit all rows only if the candidate passes frozen gates.
10. reload and smoke-test the full-data artifact.
11. install the full-data model as a silent live challenger.
12. keep the evaluated model/incumbent as the decision source.

Step 9 applies literally to the main direction ensemble and to keeper heads that
can rebuild leak-free OOF calibration from the complete span. P(Hold and the path
forecaster intentionally fit through the first 98% and reserve the freshest 2%
for isotonic or conformal calibration. Those calibration rows are used by the
production bundle, but they are not fitted by the underlying estimator. Calling
that design a literal 100% estimator fit would be inaccurate.

Training identity is captured before a long-running fit begins. The save is
rejected if the matrix span, source hash, feature schema, monthly gate, trainer
code hash, or requested-day contract changes before commit. Standalone heads use
the same start-to-save identity check before their sidecar manifest is written.

Do not close the terminal during this process.

### 2. Run the multi-window and budget experiments

Only after the matrix manifest says 1,265 days and all quality gates pass:

```powershell
.\run_multiwindow_experts_1265d.bat
```

This is a separate research process. It does not promote or modify production
decisions.

### 3. Accumulate forward expert evidence

After new rows have been rebuilt into the research matrix:

```powershell
.\run_multiwindow_shadow.bat
```

Run it again after the 5m/15m outcomes exist so unresolved predictions are
reconciled.

## Promotion Rules

No candidate is promoted because it has higher in-sample accuracy.

Required evidence remains:

```text
purged temporal OOF
same-ID comparison
untouched holdout
Brier/log-loss/ECE
retained-call precision
post-cost expectancy and profit factor
drawdown/tail loss
forward live shadow
enough independent outcomes
```

Raw 5m/15m direction remains a low-expectation target. A result near 50% is not
a software failure. Long history is expected to be more useful for movement,
path, regime duration, and tail risk than for exact final direction.

## Windows GPU Stability Finding

Repeated process-exit tests isolated an intermittent native access violation
(`0xC0000005`) to LightGBM's Windows OpenCL GPU path. XGBoost CUDA completed the
same repeated probe cleanly. The production default is therefore:

```text
BTC_LGB_DEVICE=cpu
```

LightGBM remains an ensemble seat on CPU. XGBoost and CUDA-enabled PyTorch can
still use the RTX 4050. This removes an unsafe import-time training probe and
prevents a native-driver teardown crash from compromising a multi-day run or its
final artifact commit.

## What Is Still Evidence-Gated

The external reviews also proposed a Shadow Gate Laboratory, execution-cost
quantiles, quote survival, liquidity deterioration, local sequence-valid L2
books, options-implied fair value, and relative-value strategies.

Those are not all trained or promoted by this change:

| Proposal | Current status |
|---|---|
| Immutable decision/policy shadow foundation | Existing `backend/decision/shadow_store.py`; broader ten-policy lab remains separate work |
| Execution cost q50/q80/q95 | Requires enough size-specific L2 outcomes |
| Quote survival at 500ms/1s | Requires forward quote records with stable timestamps |
| Liquidity deterioration | Requires sequence-valid multi-level books |
| Options-implied Polymarket fair value | Requires synchronized short-expiry options and executable market quotes |
| Relative-value strategies | Must first pass deterministic executable residual tests |
| Real-money automation | Prohibited until forward post-cost lower-bound evidence passes |

Implementing code cannot manufacture the missing evidence. These remain
fail-closed research lanes rather than fabricated production signals.

## Validation Completed

Passed:

```text
full backend compileall
artifact identity self-test
artifact tamper/mismatch load rejection
main-bundle compatible load and code-hash rejection
edge-probe/OHLC aggregation self-test
monthly gap/source/OHLC invariant test
missing-calendar-month and month-boundary gap tests
official OHLC parity pass/fail synthetic test
target-window policy self-test
balanced TCN and regime-weight self-test
multi-window synthetic purged-OOF self-test
forward window-expert scorer self-test
six repeated clean server import/exit cycles with LightGBM on CPU
1,265-day span-contract rejection on the real 360-day matrix
start.bat validation mode
pyflakes on all changed Python files
Vite production build
```

The system is safer and more testable. It is not guaranteed profitable, and the
1,265-day evidence does not exist until the long run and forward shadow finish.
