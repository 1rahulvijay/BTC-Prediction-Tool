# P5_11_METAORDER_CONTINUATION_HEAD

## Question

Once a probable parent order is detected, does executable impact continue?

## Contract

- Engine: `event_flow`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_metaorder_continuation_head\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_metaorder_continuation_head\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
