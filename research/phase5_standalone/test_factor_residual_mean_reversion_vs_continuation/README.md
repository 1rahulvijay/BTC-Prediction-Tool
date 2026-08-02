# P5_07_FACTOR_RESIDUAL_MEAN_REVERSION_VS_CONTINUATION

## Question

When does a BTC factor residual continue and when does it revert?

## Contract

- Engine: `readiness`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_factor_residual_mean_reversion_vs_continuation\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_factor_residual_mean_reversion_vs_continuation\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
