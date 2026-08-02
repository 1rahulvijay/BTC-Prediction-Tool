# P5_21_POLY_DEADLINE_CONVEXITY_SURFACE

## Question

Does executable probability sensitivity change correctly near expiry?

## Contract

- Engine: `pm_dynamic`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_poly_deadline_convexity_surface\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_poly_deadline_convexity_surface\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
