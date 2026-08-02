# P5_37_ANYTIME_VALID_FORWARD_EVIDENCE

## Question

Can a frozen candidate be monitored repeatedly without optional-stopping inflation?

## Contract

- Engine: `ledger`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_anytime_valid_forward_evidence\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_anytime_valid_forward_evidence\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
