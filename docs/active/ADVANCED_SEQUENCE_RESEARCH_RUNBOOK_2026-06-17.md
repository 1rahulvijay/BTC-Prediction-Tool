# Advanced Sequence Research Runbook

Date: 2026-06-17

Purpose: test deeper sequence architectures on the 180-day BTC 5m/15m research dataset without modifying live app models.

## Files Added

```text
backend/research/train_180d_advanced_sequence_models.py
run_180d_advanced_sequence_models.bat
docs/active/ADVANCED_SEQUENCE_RESEARCH_RUNBOOK_2026-06-17.md
```

## Models

Implemented directly in PyTorch:

```text
VLSTM
LPatchTST
PatchTST
iTransformer
```

Optional, skipped unless `mamba_ssm` is installed:

```text
Mamba
Mamba2
VSN+Mamba2
```

Current local check:

```text
mamba_ssm: not installed
default torch: CPU-only
research CUDA venv: installed
```

If `.venv_research_cuda` exists, the batch launcher uses it automatically.

## Dependency Status

Installed research environment:

```text
.venv_research_cuda
```

Validated:

```text
torch 2.11.0+cu128
cuda_available True
GPU: NVIDIA GeForce RTX 4050 Laptop GPU
numpy
pandas
requests
scikit-learn
pyarrow
wheel
ninja
packaging
einops
```

Mamba status:

```text
mamba-ssm did not install on Windows/Python 3.13.
```

Failure reason:

```text
mamba-ssm requires source-build/NVCC support in this environment.
The installed PyTorch CUDA runtime is enough for normal PyTorch models,
but not enough to compile mamba-ssm from source.
```

Default batch behavior:

```text
run_180d_advanced_sequence_models.bat now runs:
VLSTM
LPatchTST
PatchTST
iTransformer
```

It does not include Mamba/Mamba2/VSN+Mamba2 by default on this machine.

## Run

```powershell
.\run_180d_advanced_sequence_models.bat
```

Monitor:

```powershell
Get-Content data\logs\forecast_360d_advanced_sequence.log -Wait
```

## Targets

For 5m and 15m:

```text
future return
UP/DOWN direction
big-move probability
```

## Outputs

```text
data/research/forecast_180d_advanced_sequence_regression_metrics.csv
data/research/forecast_180d_advanced_sequence_classification_metrics.csv
data/research/forecast_180d_advanced_sequence_predictions.csv
data/research/forecast_180d_advanced_sequence_summary.csv
data/research/forecast_180d_advanced_sequence_model_inventory.csv
```

For the completed 360-day run, files are:

```text
data/research/forecast_360d_advanced_sequence_regression_metrics.csv
data/research/forecast_360d_advanced_sequence_classification_metrics.csv
data/research/forecast_360d_advanced_sequence_predictions.csv
data/research/forecast_360d_advanced_sequence_summary.csv
data/research/forecast_360d_advanced_sequence_model_inventory.csv
data/research/forecast_360d_advanced_sequence_analysis_summary.csv
```

Note:

```text
The advanced sequence script writes CSV outputs as the source of truth.
Automatic Parquet conversion is disabled for this CUDA research path because
Windows/PyTorch CUDA teardown produced native shutdown faults after successful output writes.
Convert CSV to Parquet later in a separate non-CUDA process if needed.
```

## Smoke Test

Validated with:

```powershell
python backend\research\train_180d_advanced_sequence_models.py --models vlstm,lpatchtst,mamba --smoke --no-save-models --output-prefix forecast_advanced_sequence_smoke
```

Result:

```text
VLSTM: OK
LPatchTST: OK
Mamba: skipped cleanly because mamba_ssm is missing
```

## Notes

This is research only. A model should only be considered for the app if it beats the already measured tabular/quantile baselines on the unseen test set, especially:

```text
big_move_5m
big_move_15m
top-confidence direction
return MAE
```

## Completed Baseline Sequence Results

Completed baseline sequence run:

```text
run_180d_sequence_only.bat
```

Models tested:

```text
LSTM
GRU
TCN
Transformer
```

Outputs:

```text
data/research/forecast_180d_sequence_only_summary.csv
data/research/forecast_180d_sequence_only_model_inventory.csv
data/research/forecast_180d_sequence_only_predictions.csv
data/research/forecast_180d_sequence_only_predictions.parquet
```

Run environment:

```text
device=cpu
train rows: 100,000
test rows: 33,333
total fit time: about 89.4 minutes
```

Fit-time summary:

| Model | Total fit time | Notes |
|---|---:|---|
| Transformer | 69.4 min | slow and did not beat TCN |
| GRU | 9.6 min | did not win |
| LSTM | 6.2 min | did not win |
| TCN | 4.2 min | best sequence candidate |

Best completed sequence results:

| Target | Best sequence | Result | Current stronger baseline |
|---|---|---:|---:|
| 5m UP/DOWN | Transformer | AUC 0.513 | RF AUC 0.528 |
| 15m UP/DOWN | GRU | AUC 0.520 | RF AUC 0.526 |
| 5m big move | TCN | AUC 0.715 | CatBoost AUC 0.745 |
| 15m big move | TCN | AUC 0.668 | CatBoost AUC 0.707 |
| 5m return | TCN | MAE 10.20 bps | RF/ExtraTrees about 8.76-8.78 bps |
| 15m return | TCN | MAE 18.93 bps | RF 15.22 bps |

Promotion verdict:

```text
Do not promote LSTM, GRU, TCN, or basic Transformer into the live app yet.
```

Reason:

```text
The completed sequence-only models did not beat the tabular baselines on unseen-test direction, big-move, or return targets.
```

The advanced sequence run must beat these baselines before any live app promotion:

```text
CatBoost 5m big-move AUC: 0.745
CatBoost 15m big-move AUC: 0.707
RF/ExtraTrees 5m return MAE: about 8.76-8.78 bps
RF 15m return MAE: 15.22 bps
RF 5m UP/DOWN AUC: 0.528
RF 15m UP/DOWN AUC: 0.526
```

## Completed 360-Day Advanced Sequence Run

Completed run:

```text
run_180d_advanced_sequence_models.bat
```

Actual run settings:

```text
days: 360
models: VLSTM, LPatchTST, PatchTST, iTransformer
targets: 5m return, 15m return, 5m direction, 15m direction, 5m big move, 15m big move
total model fits: 24
device: CUDA / RTX 4050 Laptop GPU
train rows per fit: 80,000
test rows per fit: 103,629
epochs: 5
```

Runtime:

```text
wall time: about 30 minutes
total model fit time: about 10.7 minutes
```

Saved models:

```text
24 .pt files
data/saved_models/research_advanced_sequence/forecast_360d_advanced_sequence/
```

Fit time by model:

| Model | Total fit time |
|---|---:|
| iTransformer | 331.3 sec |
| PatchTST | 109.0 sec |
| VLSTM | 108.7 sec |
| LPatchTST | 92.7 sec |

Best 360d advanced results:

| Target | Best model | Result | Verdict |
|---|---|---:|---|
| 5m UP/DOWN | VLSTM | AUC 0.523 | weak |
| 15m UP/DOWN | LPatchTST | AUC 0.516 | weak |
| 5m big move | VLSTM | AUC 0.724 | useful but below CatBoost 0.745 |
| 15m big move | LPatchTST | AUC 0.692 | useful but below CatBoost 0.707 |
| 5m return | iTransformer | MAE 8.123 bps | did not beat same-test zero baseline 8.121 bps |
| 15m return | iTransformer | MAE 14.109 bps | did not beat same-test zero baseline 14.104 bps |

Promotion verdict:

```text
Do not promote the 360d advanced sequence models into the live app yet.
```

Reason:

```text
They did not beat the strongest tabular big-move models.
They did not make raw UP/DOWN direction strong.
Their return MAE did not beat a same-test zero-return baseline.
```

Keep as research candidates:

```text
VLSTM for 5m big-move probability
LPatchTST for 15m big-move probability
iTransformer only for continued return-model research
```
