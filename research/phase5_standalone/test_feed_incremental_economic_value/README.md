# P5_03_FEED_INCREMENTAL_ECONOMIC_VALUE

## Question

Which recorder feeds add unique economic information after costs?

## Contract

- Engine: `feed_ablation`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_feed_incremental_economic_value\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_feed_incremental_economic_value\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
