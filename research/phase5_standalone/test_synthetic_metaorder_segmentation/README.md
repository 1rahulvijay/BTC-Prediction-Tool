# P5_10_SYNTHETIC_METAORDER_SEGMENTATION

## Question

Can public event data identify runs with more structure than random trade runs?

## Contract

- Engine: `event_flow`
- Capital authority: `false`
- Protocol: `frozen_protocol.json`
- Output is immutable: reruns require a new output directory.
- The untouched test is never used for feature, threshold, or policy selection.

## Run

```powershell
& 'C:\Users\rahul\AppData\Local\Programs\Python\Python313\python.exe' `
  research\phase5_standalone\test_synthetic_metaorder_segmentation\run.py `
  --data-dir data `
  --output data\research\phase5_standalone\test_synthetic_metaorder_segmentation\RUN_ID `
  --seed 20260802
```

Use `--selftest` for code/contract validation and `--dry-run` for a non-persisting data run.
