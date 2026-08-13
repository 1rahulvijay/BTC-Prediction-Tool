# Training Pipeline Concurrency Incident

Date: 2026-08-13  
Affected run: forced 1,000-day retrain started 2026-08-12 23:34 local time  
Result: candidate transaction rejected; no staged artifact reached serving

## What Happened

The full launcher correctly built a 1,000-day, 1,440,000-row research matrix and began
transactional specialist-head training. At 04:00, the existing Windows scheduled task
`BTC_AutoFinetune` started independently. Its process did not inherit `start.bat` environment
variables, so `auto_finetune.py` selected its literal 360-day fallback and rebuilt the same
canonical `research_matrix_1m.parquet` as 518,400 rows while the full retrain was fitting.

The directional trainer completed, but `train_heads.py` compared the source snapshot from before
and after fitting and emitted:

```text
[heads] FAIL directional (trainer-owned source changed during fit; artifact not stamped)
```

This was a real provenance refusal, not a directional-model exception. Because the full head run
was transactional, the live `saved_models` directory was not swapped. Ten independent forward
recorders continued running throughout.

## Evidence From The Rejected Run

These are evaluation observations, not profit claims, and the rejected transaction is not a
serviceable bundle:

- Matrix before interference: 1,000 days, 1,440,000 rows, 100% joined trade-feature and
  cross-venue coverage; monthly quality gate passed.
- P(Hold): test AUC 0.7331; at calibrated `P(hold) >= 0.93`, 97.7% realized hold across 44,116
  test observations (5m 96.5%, 15m 98.7%). These observations overlap by round and still require
  forward/economic verification.
- Path heads: touch AUCs 0.802/0.818 at 5m and 0.836/0.784 at 15m; round-trip AUC 0.915/0.812.
- Round-state heads passed their predeclared AUC gates, including flip risk and $20/$50/$100 shock.
- Big-move test AUC: 0.772 (5m), 0.761 (15m).
- Big-drop test AUC: 0.794 (5m), 0.782 (15m).
- Directional rare-event test AUC ranged 0.711-0.781, but top-5% precision was only 17.1-22.5%.
  These heads remain confirmation/risk context, not standalone trade authority.

## Fix

1. Added `backend/training_pipeline_lease.py`, an atomic cross-process lease for any job that may
   rewrite canonical training inputs.
2. `start.bat` acquires the lease before stopping an existing app or starting data side effects,
   holds it across backfills, matrix construction, identity verification and every specialist head,
   then releases it.
3. `auto_finetune.py` takes the same lease. If a full retrain owns it, the nightly job exits cleanly
   without running a builder or trainer and tries again on its next schedule.
4. Nightly window selection now uses `resolve_history_days()`: explicit model/history environment
   first, otherwise the canonical matrix manifest. The removed literal 360-day fallback cannot
   silently narrow a wider matrix.
5. Both the shared lease and the nightly local lock recover dead-owner files. A newly created but
   not-yet-written lease is treated as busy, closing an O_EXCL initialization race.
6. Added an executable regression that proves mutual exclusion, stale-owner recovery, actual nightly
   skip behavior and launcher ordering. It is registered in CI and the Windows launcher selftests.

## Required Next Action

Run `start.bat` again from the canonical repository. It will rebuild the canonical matrix to 1,000
days from cached sources and start a new transactional head run. If the 04:00 scheduled task fires
during training, it will log `SKIPPED` and will not touch the matrix. No completion marker exists, so
the launcher will correctly force the full retrain.

Keep both venues paper-only. Successful code execution and strong conditional classification
metrics do not establish executable net profit.
