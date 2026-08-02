# P5_38_PLACEBO_TIMING

## Question

Does a candidate weaken when its signal is shifted before or after the information event?

## Contract

- Engine: `candidate_audit`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_placebo_timing\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_placebo_timing\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
