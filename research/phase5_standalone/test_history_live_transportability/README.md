# P5_02_HISTORY_LIVE_TRANSPORTABILITY

## Question

Is the historical training archive representative of the recent environment?

## Contract

- Engine: `history_transport`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_history_live_transportability\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_history_live_transportability\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
