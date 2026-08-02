# P5_14_PRICE_IMPACT_EFFICIENCY_DECAY

## Question

Does aggressive flow continue moving price efficiently?

## Contract

- Engine: `event_flow`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_price_impact_efficiency_decay\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_price_impact_efficiency_decay\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
