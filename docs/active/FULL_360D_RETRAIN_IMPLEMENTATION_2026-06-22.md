# Full 360-Day Retrain Implementation - 2026-06-22

## Purpose

This document records the changes made to support one deliberate 360-day training cycle on a
16 GB Windows laptop, then freeze the completed model bundle for stable live measurement.

The implementation has four goals:

1. train every standalone specialist head and the main ensemble once;
2. keep the machine within practical RAM limits;
3. keep Polymarket and microstructure evidence recording during the long run;
4. make later starts load the completed bundle instead of retraining it.

This is an engineering and model-governance upgrade. It does **not** guarantee a higher win rate.
Accuracy and profitability must still pass the locked checks in
[`POST_TRAINING_EVALUATION_RUNBOOK_2026-06-21.md`](POST_TRAINING_EVALUATION_RUNBOOK_2026-06-21.md).

## Final Startup Lifecycle

`start.bat` now uses one completion marker for the requested historical window:

```text
data/saved_models/full_retrain_360d_complete.json
```

### First start without the marker

1. Start the Polymarket quote/settlement recorder.
2. Start the L2 and cross-exchange microstructure recorder.
3. Incrementally backfill the 360-day source datasets.
4. Build or refresh the 360-day research matrix.
5. Force all 13 standalone specialist jobs, one subprocess at a time.
6. Start the frontend and backend.
7. Force the main three-horizon ensemble retrain.
8. Save the trained main model.
9. Atomically write the completion marker only if the standalone heads and main model succeeded.
10. Keep automatic/scheduled retraining frozen after that run.

### Later starts with the marker

The batch file disables the force flags, loads the compatible saved artifacts, keeps the model
frozen, and continues recording data. A browser refresh does not retrain or restart the backend.

### Failure behavior

The marker is fail-closed:

- a failed research-matrix build leaves `BTC_HEAD_RETRAIN_COMPLETE=0`;
- a head subprocess with a nonzero exit code, missing output, or unchanged forced output fails the
  head stage;
- a failed main-model train/save cannot write the marker;
- a missing marker causes the next normal start to retry the complete requested cycle.

The marker is written through a temporary file and `os.replace`, so a crash cannot leave a
half-written marker that falsely marks the bundle complete.

## Changes By File

### `start.bat`

Implemented:

- changed the default historical and backfill window to 360 days;
- added the per-window completion-marker lifecycle;
- added `BTC_FORCE_360_RETRAIN=1` as an explicit one-shot rebuild override;
- forced `BTC_FREEZE_MODEL=1` after startup selection so unattended scheduled retraining cannot
  start another multi-hour cycle;
- reduced Python/BLAS training thread defaults from 12 to 8;
- set `BTC_SEQUENCE_MEMMAP_THRESHOLD_MB=1024`;
- set `BTC_DIRECTION_MAX_SAMPLES=40000`;
- starts both evidence recorders before expensive backfills;
- forces all standalone heads when the completion marker is absent;
- carries standalone-head success into the backend through `BTC_HEAD_RETRAIN_COMPLETE`;
- leaves the startup backtest disabled by default because it previously starved the live event loop;
- added `BTC_VALIDATE_STARTUP=1`, which prints the resolved startup configuration and exits before
  launching recorders, data builders, frontend, backend, or training.

Why:

- one marker makes the expensive lifecycle deterministic and restart-safe;
- eight threads leave CPU headroom for Windows, WebSockets, and recorders;
- freeze mode preserves a fixed model version for honest forward evaluation;
- validation mode lets an operator check the batch logic without accidentally starting training;
- recording during training turns the long run into useful evidence-collection time.

### `backend/features.py`

Implemented:

- `build_sequences` now preallocates `X`, `Y`, and magnitude targets instead of appending millions
  of Python objects;
- added optional `memmap_path` support for a disk-backed float32 sequence tensor;
- retained the existing triple-barrier direction labels and move-size targets;
- flushed disk-backed sequences before returning them.

Why:

The 360-day sequence tensor is too large for a 16 GB laptop if it is constructed as Python lists
and then copied into NumPy. Preallocation removes the list-copy peak. A memory map stores the largest
tensor on disk while exposing the same NumPy-compatible interface to training code.

An exact parity test confirmed that RAM-backed and disk-backed construction produce identical
features, direction labels, and magnitude targets.

### `backend/model.py`

Implemented:

- added `BTC_DIRECTION_MAX_SAMPLES`;
- added representative sampling that allocates half the budget across older history and keeps the
  other half as an exact recent tail;
- applies that cap before advanced NumPy indexing for each regime bucket;
- applies target-size caps before materializing regression arrays;
- records the actual training split fraction and row boundary on the model;
- corrected fallback regime lookup to use selected feature names (`adx_norm`, `ewma_vol`) instead
  of stale raw-column positions;
- logs every eligible horizon/regime bucket, sample count, class set, component count, and component
  elapsed time;
- the active direction horizons are 5m and 15m (markets only; 1m dropped 2026-06-22, arch `2horizon-5-15`).

Why:

A disk-backed source tensor alone was insufficient: advanced indexing such as
`X_flat[regime_indices]` could still create another multi-gigabyte in-memory copy. Sampling the index
first bounds that copy while preserving broad historical coverage and recent-market relevance.

The selected main-ensemble feature count remains 69. The app may build a wider 136-column raw
feature matrix, but retired columns are removed before lookback-sequence expansion and model fitting.

### `backend/server.py`

Implemented:

- estimates sequence memory before construction;
- switches to a process-specific disk memory map above the configured threshold;
- removes stale training memory maps older than one hour;
- closes and deletes the active memory map after successful training and on handled failures;
- records the exact 98% train boundary used by the model instead of assuming an older 80% split;
- writes the full-retrain completion marker only after standalone heads and the main model report
  success;
- marker metadata includes date, historical days, architecture version, horizons, selected feature
  count, train split, head status, and main-model status.

Why:

This keeps temporary storage bounded, avoids stale multi-gigabyte files after interrupted runs, and
prevents the launcher from treating an incomplete bundle as finished. Recording the exact boundary
also keeps later out-of-sample analysis aligned with what the model actually saw.

### `backend/train_heads.py`

Implemented:

- keeps specialist training sequential, one subprocess at a time;
- checks every subprocess exit code;
- verifies the expected artifact exists;
- for forced runs, verifies that an existing artifact was actually refreshed;
- exits nonzero with a consolidated failure list when any requested head fails.

Why:

Sequential subprocesses release each model's memory before the next model begins. Artifact checks
prevent a script that prints an error but returns control from being mistaken for a complete retrain.

The forced run now covers these 13 jobs:

1. selectivity;
2. signed quantile;
3. persistence / P(Hold);
4. path forecaster (high/low, exact-dollar touch, round trip);
5. big move;
6. big drop;
7. directional big move;
8. activity / range;
9. champion meta-model;
10. price-to-beat classifier;
11. magnitude quantiles;
12. legacy path labels/model;
13. historical fingerprints.

## Memory And Resource Design

For roughly 518,000 usable rows, lookback 60, float32:

| Sequence representation | Approximate size |
|---|---:|
| 136 raw features | 15.7 GiB |
| 69 selected features | 8.0 GiB |
| one capped 40,000-row regime copy | 0.62 GiB |

The implementation therefore uses:

- all 360 days for source construction, labels, regime statistics, class priors, specialist heads,
  and the chronological holdout;
- 69 selected features for main-ensemble sequences;
- an approximately 8 GiB temporary disk-backed sequence file;
- at most 40,000 representative rows per main direction-model regime bucket;
- half of that capped budget spread across older history and half reserved for the exact recent tail;
- sequential standalone-head subprocesses;
- eight training threads by default.

This is the transparent laptop compromise: every one of the approximately 518,000 sequence rows is
not copied into every direction classifier. Doing so would be unsafe on 16 GB and could trigger
swapping, process termination, WebSocket timeouts, or an unusable desktop. The full historical window
still determines the data universe and evaluation tail.

The temporary sequence file needs adequate disk space. The last audit found sufficient free space,
but at least 20 GiB free is a sensible operating minimum for the tensor plus caches and artifacts.

## Operator Commands

### Inspect the resolved mode without starting anything

```powershell
cmd /d /c "set BTC_VALIDATE_STARTUP=1&& call start.bat"
```

Expected first-run lines include:

```text
force_heads=1 force_main=1 frozen=1
direction_cap=40000 memmap_threshold_mb=1024
```

### Start the one-time 360-day cycle

```powershell
.\start.bat
```

Keep the laptop plugged in, disable sleep, and close memory-heavy browsers, IDEs, games, and other
training processes. The first run can take 18-36 hours or longer when large aggTrade backfills are
not already cached. This is an estimate, not a guarantee.

### Force a fresh 360-day cycle later

```powershell
$env:BTC_FORCE_360_RETRAIN = "1"
.\start.bat
```

Use a new PowerShell window afterward or remove the override:

```powershell
Remove-Item Env:BTC_FORCE_360_RETRAIN -ErrorAction SilentlyContinue
```

### Optional recorder opt-outs

```powershell
$env:BTC_SKIP_PM_RECORDER = "1"
$env:BTC_SKIP_MICROSTRUCTURE_RECORDER = "1"
```

These should normally remain unset because the recorder evidence is required to assess real
Polymarket edge and microstructure value.

## Completion Checklist

Do not stop at the final model-component log. Wait for both:

```text
Background startup training complete.
Full retrain completion marker written: ...full_retrain_360d_complete.json
```

Then run:

```powershell
python backend\check_model_compatibility.py
```

Required result:

```text
Compatible saved main ensemble
```

Also confirm:

- predictions resume for 5m and 15m;
- no training traceback appears;
- `data/saved_models/architecture_version.pkl` has a new modification time;
- `data/saved_models/full_retrain_360d_complete.json` exists and reports 69 model features;
- both recorders continue writing data.

Once compatible, later normal starts should report that the completed 360-day bundle was found and
remain frozen. `start_instant.bat` should be used only after the compatibility check succeeds.

## Validation Performed Before Documentation

- Python compile checks passed for `features.py`, `model.py`, `server.py`, and `train_heads.py`;
- static undefined-name checks passed for those files;
- RAM/memory-map sequence parity passed exactly;
- representative-index testing confirmed the 40,000-row cap and recent-tail preservation;
- the 360-day specialist-head dry run listed all 13 jobs;
- atomic completion-marker creation was tested with a temporary target;
- `start.bat` validation mode resolved the expected 360-day force/freeze/memory settings without
  starting the application;
- no application or training process was launched by these validation steps.

## What This Change Does Not Prove

It does not prove that 360 days are better than 150 or 180 days, that more training produces more
profit, or that any model has a durable market edge. Longer history can improve rare-regime coverage,
but it can also dilute recent behavior. The correct comparison is forward evidence for the frozen
bundle, not in-sample training accuracy.

After completion, make no model, threshold, feature, label, or ensemble changes during the evidence
window. Evaluate only:

1. P(Hold) calibration by horizon;
2. retained-call precision for 5m and 15m;
3. regime-gate shadow performance;
4. champion-v2 versus plain P(Hold) at matched coverage;
5. Polymarket quote plus official-settlement edge.
