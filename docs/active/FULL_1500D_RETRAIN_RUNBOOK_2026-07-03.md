# Full 1,500-Day Retrain Runbook

> **Current executable window (2026-07-04): 1,265 days, not 1,500.** The committed launcher and
> active matrix manifest use 1,265 source-complete days (2023-01-15 onward). The filename is retained
> for historical links. Do not claim that the current artifact includes the 2022 bear market.

Date: 2026-07-03

## Objective

Train the app's current model and specialist-head architecture on a 1,500-day market window without
exceeding the operator laptop's 16GB RAM, discarding the existing working ensemble prematurely, or
claiming accuracy before the held-out results are measured.

This is a research/validation run. A longer window does not guarantee a higher win rate. The established
endpoint UP/DOWN direction ceiling remains approximately coin-flip; the strongest expected benefit is
more robust path, volatility, touch, round-state and risk calibration across multiple BTC price regimes.

## Frozen Configuration

| Setting | Value | Reason |
|---|---:|---|
| `BTC_HISTORICAL_DAYS` | `1500` | Single source window for candles, matrix and specialist heads |
| `BTC_BACKFILL_DAYS` | `1500` | Matches the training contract |
| `BTC_TRAIN_SPLIT_FRAC` | `0.98` | About 1,470 days fit/calibration and 30 days untouched test |
| `BTC_DIRECTION_MAX_SAMPLES` | `40000` | Representative full-history sample plus recent tail for weak direction heads |
| `BTC_SEQUENCE_MEMMAP_THRESHOLD_MB` | `1024` | Builds the roughly 28GB pruned sequence tensor on disk, not RAM |
| `BTC_TRAIN_THREADS` | `8` | Leaves CPU capacity for feeds and the recorder |
| `BTC_FREEZE_MODEL` | `1` | Prevents unrequested recurring retrains after completion |
| `BTC_RUN_STARTUP_BACKTEST` | `0` | Avoids stacking a heavy replay onto the training run |
| `BTC_FULL_REFIT_AFTER_GATE` | `1` | Enables evaluated-candidate to full-data shadow workflow |

### Frozen offline promotion gates

For both 5m and 15m, the untouched 2% candidate must have at least 1,000 samples, directional-call
precision at least 48%, multiclass Brier at most 0.80 and ECE at most 0.20. Where the incumbent has
at least 1,000 genuinely unseen matching rows, candidate precision may trail it by at most 3 points and
Brier may be worse by at most 0.03. The report is persisted under `saved_models/promotion_reports`.

A passing candidate is refit on 100% of rows. Its stacker is rebuilt from purged out-of-fold predictions;
conformal residuals come from the evaluated candidate rather than in-sample residuals. The full refit is
saved under a run-specific challenger directory, reloaded, probability-smoke-tested, and atomically assigned
to the silent A/B challenger slot. It does not replace the decision primary at this stage.

Live promotion still requires at least 30 actual calendar days, 500 resolved predictions, profit factor above
1.20, positive expectancy and sufficient profit samples. It also requires 500 exact paired outcomes,
positive challenger-minus-primary accuracy and a positive 95% paired-bootstrap lower bound. Prediction
count is not used as a fake day counter, and aggregate hit totals are never rearranged into fake pairs.

The 136-column raw feature matrix is pruned to the active model feature contract before lookback sequence
expansion. The 1,500-day move/path labels use basis points rather than fixed dollars so a threshold has
comparable meaning at BTC prices from approximately $15k to $115k.

The fade head is intentionally excluded from the production retrain. Its causal 1-minute artifact missed
the frozen 55% top-decile precision requirement, and the honest 1-second challenger failed its predeclared
joint gate. Existing fade artifacts remain available for research but cannot emit a live entry signal.

## Laptop Capacity

Observed before this change:

- C: free space: approximately 348GB.
- Existing 400-day spot/perpetual daily-file cache: approximately 73GB.
- Estimated additional cache for the missing approximately 1,100 days: 200-230GB.
- Estimated pruned training sequence memmap: approximately 28GB, removed after training.
- Expected remaining free-space margin during training: approximately 80-120GB.

This fits, but the margin is not unlimited. Do not run another large research download at the same time.
The launcher now passes `--keep-cache` to the trade-feature builder so the persistence and cross-venue
builders reuse the same spot files instead of downloading them a second time.

## Runtime Expectation

The first run is expected to take roughly 2-5 days, dominated by downloading and parsing about 1,100 new
spot days and 1,100 new perpetual days. Network throttling can make it longer. It is resumable at the daily
file level: if interrupted, rerunning the launcher reuses completed files. Subsequent 1,500-day rebuilds are
substantially faster because the daily archives remain cached.

The disk guard requires 300GB for the first long build. After at least 1,000 source-day cache files exist,
a retry is treated as a resume and requires an 80GB safety floor rather than incorrectly demanding that
the already-downloaded cache space become free again.

## Safe Model Replacement

When `full_retrain_1500d_complete.json` is absent, `start.bat` forces the specialist heads and main ensemble
once. The backend now loads a compatible incumbent ensemble first. The 1,500-day main ensemble trains as a
separate candidate; predictions continue from the incumbent and the candidate swaps active only after it
finishes successfully. A failure leaves the incumbent active. A gate-rejected candidate is recorded in the
completion marker when an incumbent exists so an ordinary restart does not repeat a multi-day rejected
experiment; an explicit force run is still available.

If no compatible incumbent exists, a passing 98% candidate becomes the temporary primary and the 100%
refit remains the silent challenger. A failed first-ever candidate leaves no completion marker.

Before the first forced head starts, the launcher creates `data/saved_models_pre1500d_backup` from the
current working model directory. It never overwrites that snapshot on retries. A backup failure aborts the
run before any training artifact changes.

The completion marker is written only when:

1. The 1,500-day research matrix passes its candle-span check and both trade-feature and cross-venue
   joins cover at least 98% of requested rows. Matrix replacement is atomic; a failed build preserves
   the previous working matrix.
2. Requested specialist heads finish without a required-head failure.
3. The evaluated candidate passes the frozen gate and the full refit reload/smoke test succeeds, or a
   gate-rejected experiment is safely recorded while the incumbent remains active.

Bundle metadata persists model identity, split location and full-refit state across restarts. If a 100%
refit is eventually promoted, ordinary historical backtest is disabled because no untouched historical
tail remains; use the saved candidate holdout report plus live-shadow evidence instead.

If the matrix or a required specialist head fails, the launcher stops before starting the main-ensemble
job. This avoids spending another day training against incomplete inputs; cached daily downloads remain
available for the retry.

## Operator Procedure

1. Keep the laptop connected to power and disable sleep/hibernation for the run.
2. Close memory-heavy IDEs, games and unrelated browser windows.
3. `start.bat` stops existing frontend/backend listeners on ports 3000/8000 before any long work. Independent
   recorder processes remain running because their launcher deduplicates them. To require a manual stop instead,
   set `BTC_AUTO_STOP_EXISTING_APP=0`; the launcher then warns and aborts without touching either process.
4. Run `start.bat` once from the repository root.
5. Leave the terminal open. Do not start a second copy.
6. Watch for daily download progress, per-head `TRAIN/SKIP/OK` logs, main `[TRAIN x/y]` logs, candidate swap,
   and finally `full_retrain_1500d_complete.json`.

To validate the launcher configuration without downloading or training:

```bat
set BTC_VALIDATE_STARTUP=1
call start.bat
set BTC_VALIDATE_STARTUP=
```

To deliberately repeat a completed 1,500-day run:

```bat
set BTC_FORCE_FULL_RETRAIN=1
call start.bat
```

Do not set the force flag for the first run; the missing marker already triggers it.

## Required Post-Training Evaluation

Do not promote or bet from the new bundle based on training completion. Compare it with the 400-day
incumbent using only held-out/live evidence:

1. P(Hold) calibration by 5m and 15m horizon: Brier score, ECE and reliability buckets.
2. Retained-call precision with Wilson lower bounds, not raw accuracy alone.
3. Path-head band coverage, touch AUC, round-trip AUC and magnitude skill.
4. Champion-v2 and regime-gate shadow results against plain P(Hold).
5. Polymarket executable net EV using actual ask, depth, fees, slippage and settlement.
6. Performance by recent regime and by time period to detect old-regime dilution.

If the 1,500-day candidate is worse on the recent untouched period, retain or restore the 400-day champion.
Longer history is useful only when it improves recent out-of-sample calibration or economic value.

## Final Preflight Evidence (2026-07-03)

- Full backend `compileall`: pass.
- Frontend production build: pass.
- Current saved main ensemble compatibility: pass, 69 active model features and 5m/15m GLOBAL seats.
- Path forecaster self-test: pass, including explicit next-window high/low alignment.
- Fade research self-test: pass; production loader correctly rejects the gate-failed artifact.
- Forced specialist-head dry run: pass; fade is absent from the production head roster.
- Latest 4,320 one-minute bars: zero bad OHLC bars, duplicates, missing minutes, stale runs or extreme returns.
- Required first archive date (`2022-05-25`): HTTP 200 for Binance spot and perpetual aggTrades.
- Rollback snapshot: 475/475 files, 1.695GB/1.695GB, zero path/size differences; critical hashes match.
- Current free disk after backup: above the 300GB launcher threshold.
