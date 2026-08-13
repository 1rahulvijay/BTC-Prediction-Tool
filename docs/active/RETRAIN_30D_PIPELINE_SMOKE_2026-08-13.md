# 30-Day Full-Pipeline Smoke Retrain

Date: 2026-08-13
Status: configured and validated; not started by this change.

## Purpose

Run every production training stage on a smaller matrix before committing the laptop to the
900-day job. This run is intended to expose orchestration, data-contract, trainer, memory,
transactional staging, artifact-manifest, save/load, completion-marker and live-inference defects.

It is **not** intended to establish model accuracy, calibration stability, economic edge or
production readiness. Thirty days contains too little regime diversity for those claims.

## Active Contract

| Setting | Value | Reason |
| --- | ---: | --- |
| `BTC_HISTORICAL_DAYS` | 30 | smaller complete source/matrix window |
| `BTC_MODEL_TRAINING_DAYS` | inherits 30 | artifact identity matches the fit window |
| `BTC_BACKFILL_DAYS` | inherits 30 | all offline builders share one namespace |
| `BTC_TRAIN_SPLIT_FRAC` | 0.95 | about 2,160 holdout minutes, above the 1,000-row gate |
| marker | `full_retrain_30d_complete.json` | cannot collide with 360/400/900 artifacts |
| production identity | remains 900 | production must reject the smoke artifact |

The 95/5 split is deliberate. At 98/2, a 30-day one-minute matrix has only about 864 holdout rows,
so the unchanged `BTC_PROMOTION_MIN_HOLDOUT_SAMPLES=1000` gate would refuse before the full
promotion path could be exercised. No accuracy, Brier, ECE, directional-call or economic gate is
lowered. The smoke days and split are assigned unconditionally in `start.bat`, so stale shell
variables cannot silently turn this run back into a 900-day job.

## What Success Means

The smoke succeeds only if:

1. the matrix manifest reports 30 requested days and acceptable source coverage;
2. every required specialist head completes inside the staging transaction;
3. trainer-owned inputs remain immutable throughout fitting;
4. strict manifests and artifact hashes validate before deserialization;
5. the main candidate trains and receives an honest recent holdout report;
6. a gate-approved bootstrap candidate can be atomically installed when no incumbent exists;
7. any full-data refit remains a shadow challenger;
8. `full_retrain_30d_complete.json` validates against the installed bundle;
9. predictions, heads and paper-only engines resume without shape or release-id mismatches.

A holdout rejection caused by weak precision, calibration or Brier score is an honest model result,
not a code bug. A traceback, source mutation, missing artifact, manifest mismatch, failed reload,
invalid marker or stuck training phase is a pipeline defect to fix before the 900-day run.

## After The Smoke

Do not use the 30-day bundle for production or profit claims. Once pipeline defects are resolved:

1. change `start.bat` default days from 30 to 900;
2. restore `BTC_TRAIN_SPLIT_FRAC` from 0.95 to 0.98;
3. change `start_instant.bat` model identity from 30 to 900;
4. keep `start_production.bat` and `deploy/production.env.example` at 900;
5. update the launcher contract and canonical configured-window documentation;
6. rerun all invariants and the 900-day preflight;
7. launch the full 900-day evaluated retrain.

## Pre-Launch Validation

- adversarial launcher dry run with stale 900-day shell values resolved to
  `days=30 model_days=30 backfill=30 split=0.95`;
- marker resolved to `full_retrain_30d_complete.json` and is currently absent, so the run is forced;
- exact Windows `start.bat` selftest-only sequence passed every invariant group;
- pytest: 155 passed, with 13 existing third-party deprecation warnings;
- current documentation and launcher contracts passed;
- `BTC_AutoFinetune` is disabled and the shared training-pipeline lease is idle;
- 10/10 forward recorders were advancing after validation.
