# P5_25_POLY_CROSS_EXPIRY_CONSISTENCY

## Question

Are simultaneous 5m and 15m contracts jointly inconsistent after executable costs?

## Contract

- Engine: `pm_cross_expiry`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_poly_cross_expiry_consistency\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_poly_cross_expiry_consistency\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
