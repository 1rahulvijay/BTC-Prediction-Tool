# P5_01_ALPHA_EXTRACTABILITY_UPPER_BOUND

## Question

Does the recorded market contain enough executable opportunity to justify more modeling?

## Contract

- Engine: `alpha_upper_bound`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_alpha_extractability_upper_bound\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_alpha_extractability_upper_bound\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
