# P5_09_SPOT_PERPETUAL_BASIS_RESIDUAL

## Question

Does abnormal spot/perpetual basis predict continuation or reversal after costs?

## Contract

- Engine: `btc_signal`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_spot_perpetual_basis_residual\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_spot_perpetual_basis_residual\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
