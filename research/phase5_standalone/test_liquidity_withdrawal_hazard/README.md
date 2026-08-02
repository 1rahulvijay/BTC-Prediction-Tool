# P5_15_LIQUIDITY_WITHDRAWAL_HAZARD

## Question

Is executable top-of-book liquidity about to disappear?

## Contract

- Engine: `l2_hazard`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_liquidity_withdrawal_hazard\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_liquidity_withdrawal_hazard\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
