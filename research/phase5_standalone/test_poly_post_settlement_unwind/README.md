# P5_24_POLY_POST_SETTLEMENT_UNWIND

## Question

Does settlement create predictable hedge unwinding in BTC or the next round?

## Contract

- Engine: `readiness`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_poly_post_settlement_unwind\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_poly_post_settlement_unwind\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
