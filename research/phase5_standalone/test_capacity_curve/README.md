# P5_36_CAPACITY_CURVE

## Question

At what size does a passing alpha lose its lower-bound edge?

## Contract

- Engine: `l2_capacity`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_capacity_curve\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_capacity_curve\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
