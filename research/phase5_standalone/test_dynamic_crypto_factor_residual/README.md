# P5_06_DYNAMIC_CRYPTO_FACTOR_RESIDUAL

## Question

Is BTC-specific residual movement more predictable than raw BTC movement?

## Contract

- Engine: `readiness`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_dynamic_crypto_factor_residual\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_dynamic_crypto_factor_residual\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
