# P5_18_SURPRISE_AS_VOLATILITY_NOT_DIRECTION

## Question

Do market surprises predict movement magnitude even when direction is weak?

## Contract

- Engine: `btc_magnitude`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_surprise_as_volatility_not_direction\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_surprise_as_volatility_not_direction\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
