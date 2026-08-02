# P5_22_POLY_NEW_ROUND_OPENING_INHERITANCE

## Question

Does a new market inherit stale executable pricing from the previous round?

## Contract

- Engine: `pm_dynamic`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_poly_new_round_opening_inheritance\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_poly_new_round_opening_inheritance\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
