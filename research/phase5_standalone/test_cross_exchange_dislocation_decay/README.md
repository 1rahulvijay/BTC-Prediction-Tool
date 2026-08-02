# P5_08_CROSS_EXCHANGE_DISLOCATION_DECAY

## Question

When BTC venues disagree, which venue catches up and which reverses?

## Contract

- Engine: `crossvenue`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_cross_exchange_dislocation_decay\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_cross_exchange_dislocation_decay\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
