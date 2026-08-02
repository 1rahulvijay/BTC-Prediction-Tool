# P5_05_SIGNAL_CONTEXT_SIGN_REVERSAL

## Question

Does an existing flow signal change economic sign across frozen contexts?

## Contract

- Engine: `signal_context`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_signal_context_sign_reversal\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_signal_context_sign_reversal\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
