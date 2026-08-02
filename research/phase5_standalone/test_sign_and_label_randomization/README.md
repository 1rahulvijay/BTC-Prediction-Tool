# P5_39_SIGN_AND_LABEL_RANDOMIZATION

## Question

Does candidate performance collapse under sign and within-day randomization?

## Contract

- Engine: `candidate_audit`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_sign_and_label_randomization\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_sign_and_label_randomization\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
