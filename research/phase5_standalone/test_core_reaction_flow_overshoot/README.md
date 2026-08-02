# P5_13_CORE_REACTION_FLOW_OVERSHOOT

## Question

Does reaction flow overshoot initiating flow enough to support a causal fade?

## Contract

- Engine: `event_flow`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_core_reaction_flow_overshoot\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_core_reaction_flow_overshoot\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
