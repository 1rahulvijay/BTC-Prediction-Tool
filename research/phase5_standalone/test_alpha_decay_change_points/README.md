# P5_30_ALPHA_DECAY_CHANGE_POINTS

## Question

Is a candidate temporarily weak or structurally dead?

## Contract

- Engine: `candidate_audit`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_alpha_decay_change_points\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_alpha_decay_change_points\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
