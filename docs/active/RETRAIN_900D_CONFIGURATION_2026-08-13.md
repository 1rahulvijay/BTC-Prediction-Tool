# 900-Day Retrain Configuration

Date: 2026-08-13  
Status: configured and validated before launch; retrain not started by this change.

## Decision

The next complete training run uses **900 days**, not 1,000 days. This is a committed launcher
default, so opening a new shell cannot silently restore the old window.

## Canonical Runtime Contract

| Setting | Full retrain | Instant/production serving |
| --- | ---: | ---: |
| `BTC_HISTORICAL_DAYS` | 900 | 3 |
| `BTC_SERVING_WARMUP_DAYS` | 900 | 3 |
| `BTC_MODEL_TRAINING_DAYS` | inherits 900 | 900 |
| `BTC_BACKFILL_DAYS` | inherits 900 | skipped |
| `BTC_TRAIN_SPLIT_FRAC` | 0.98 | not applicable |
| completion marker | `full_retrain_900d_complete.json` | validates the 900-day artifact |

The candidate fit therefore uses the oldest 98% of the 900-day temporal window and evaluates on
the most recent 2%, approximately 18 days before purge/embargo effects. An accepted full-data refit
may then use all eligible rows, but it remains a shadow challenger and does not erase the recorded
holdout result.

## What Happens On The Next `start.bat`

1. No valid 900-day completion marker exists, so one full retrain is forced.
2. The long-window preflight keeps the 80 GB safety floor for this 900-day build.
3. Existing derived sources exceed 900 days, so the expected path is `REBUILD`, not a bulk first
   download.
4. Backfills, matrix construction and specialist heads use the same 900-day namespace.
5. The shared training-pipeline lease prevents the nightly finetune process from replacing the
   matrix during fitting.
6. Specialist heads train sequentially into staging. A source mutation, failed required head, bad
   manifest or failed smoke test prevents publication.
7. Main-model training starts only after the matrix and required specialist transaction complete.
8. The marker is written only after the required head and main-model flow completes.

The currently installed research matrix is still the 360-day matrix left by the rejected nightly
collision. That is expected. The next full launch must replace it with a 900-day matrix before any
new artifact can be stamped as 900-day-trained.

## Expected Resource Difference

At one-minute resolution, 900 calendar days is approximately 1,296,000 rows versus 1,440,000 for
1,000 days, about 10% fewer source rows. Runtime and memory do not necessarily fall by exactly 10%
because some direction and stacking learners are sample-capped, while full-matrix specialist heads
and feature construction scale more directly with row count.

## Historical Evidence Boundary

The failed 1,000-day attempt remains documented as a 1,000-day incident and its measured holdout
statistics remain attached to that rejected transaction. They are not evidence for the future
900-day bundle. Accuracy, calibration, retained-call precision and paper economics must be measured
again from the published 900-day release.

## Operator Action

Run `start.bat` from the canonical repository when ready. No PowerShell environment override is
required. Do not run a second trainer or matrix builder manually. Keep both venues paper-only until
the new release is published and release-scoped forward evidence clears the existing gates.

## Validation Record

Performed after the configuration change, without starting a retrain:

- launcher dry run: `days=900`, `backfill=900`, model identity inherits 900, split `0.98`, marker
  `full_retrain_900d_complete.json`;
- production dry run: three-day warm-up with `model_days=900`;
- real long-window preflight: `REBUILD`, 337 GB free, weakest derived source about 1,304 days,
  80 GB required;
- exact `start.bat` selftest-only path: all invariant groups passed;
- workflow-defined local CI mirror: 217/217 gates passed in 537 seconds;
- Python compile and pyflakes: passed;
- pytest: 155 passed, with 13 existing third-party deprecation warnings;
- launcher/documentation contracts: passed;
- Vite production build: passed; npm audit: zero vulnerabilities;
- recorder health after validation: 10/10 recorders `ADVANCING`.
