# P5_20_POLY_ELASTICITY_RESIDUAL_CLOSURE

## Question

After a pricing-response residual appears, does the book catch up before costs?

## Contract

- Engine: `pm_dynamic`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_poly_elasticity_residual_closure\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_poly_elasticity_residual_closure\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
