# P5_34_HOLD_EXIT_SWITCH_LOCK_COUNTERFACTUAL

## Question

Which executable next-checkpoint action has the best incremental value?

## Contract

- Engine: `readiness`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_hold_exit_switch_lock_counterfactual\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_hold_exit_switch_lock_counterfactual\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
