# P5_28_ALPHA_REDUNDANCY_AND_UNIQUE_VALUE

## Question

Does a candidate add independent PnL rather than duplicate an incumbent?

## Contract

- Engine: `candidate_audit`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_alpha_redundancy_and_unique_value\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_alpha_redundancy_and_unique_value\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
