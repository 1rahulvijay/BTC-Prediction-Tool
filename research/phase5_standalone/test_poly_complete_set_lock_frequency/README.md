# P5_26_POLY_COMPLETE_SET_LOCK_FREQUENCY

## Question

How often can an existing open paper position be locked into guaranteed profit?

## Contract

- Engine: `readiness`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_poly_complete_set_lock_frequency\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_poly_complete_set_lock_frequency\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
