# Deep release audit - 2026-08-11

## Scope

This audit validates the committed local source tree before the operator starts the deliberate
1,000-day rebuild and retrain. It does not claim that latent defects are impossible, that a model
will be accurate, or that either paper strategy will become profitable.

Audited tree:

- branch: `master`
- commit: `971ba01` (`Fix startup meta-model selftest isolation`)
- worktree: clean before this documentation update
- remote state: local branch is two commits ahead of `origin/master`

## Confirmed startup fix

The previous `start.bat` stop was not a `head_permissions.py` failure. A shared batch failure label
misreported any failure in a 28-command block as that file. The real failure was the positive
meta-model fixture inheriting `BTC_EVIDENCE_MODE=1` before the launcher started recorders.

The repair is deliberately narrow:

- positive offline fit fixture runs with evidence mode disabled;
- explicit dark-evidence refusal fixture runs with evidence mode enabled;
- the caller environment is restored;
- the live forward-evidence gate is unchanged;
- the launcher records and reports the exact failed command.

The exact Windows `BTC_SELFTEST_ONLY=1` launcher path now passes every invariant.

## Executed evidence

| Area | Evidence |
| --- | --- |
| source registry | fresh: 9 hashed sources, 14 registry rows, 12 purposes |
| launcher | every label/guard valid; all 105 invoked files exist |
| long-window preflight | 1,000 days accepted; 346 GB free; source coverage 1,301-1,302 days |
| datastore identity | server, training and audit resolve the same canonical DuckDB |
| Python | compileall passed; pyflakes passed; 155 pytest tests passed |
| full workflow | 216/216 gates passed in 694 seconds |
| real fit smoke | train completed; 21 OOF seat/bucket records; 12 temporary artifacts; reload prediction drift 0 |
| model governance | manifest, hash-before-load, training identity, bundle completeness and atomic promotion gates passed |
| Binance paper | execution, fee, slippage, funding, recovery, rollback and risk contracts passed |
| Polymarket paper | dynamic strategy and equal-capital competition contracts passed |
| settlement | 37 checks passed; proxy endpoint head cannot price Polymarket EV |
| strategy/UI names | all 18 Polymarket strategies are logged and exposed consistently |
| recorder declarations | all 10 stores readable; no schema or timestamp-unit drift |
| canonical schema | additive initialization passed; required identity/resolution columns present |
| HTTP/WebSocket | fail-closed readiness, auth, origin and real-execution boundaries passed |
| frontend | Vite build passed; npm high-severity audit found 0 vulnerabilities |

## Expected pre-retrain refusals

These are correct behavior and must not be patched around:

- the current research matrix is 360 days and refuses direct 1,000-day head training;
- `start.bat` first rebuilds it to 1,000 days, verifies the new training identity, then trains;
- current main ensemble v11 is incompatible with source architecture v14;
- 0/11 legacy specialist artifacts have current manifests;
- P(Hold) calibration is off without a verified release;
- all recorders are stalled while the application is closed;
- the multi-venue archive has 8/9 streams and is missing liquidations;
- production deployment tokens, explicit origins and a dedicated production environment are not configured.

## Launch sequence verified

`start.bat` will:

1. run the fail-closed invariant suite;
2. start ten standalone evidence recorders exactly once;
3. refresh reusable source backfills;
4. rebuild the research matrix to 1,000 days;
5. verify training identity;
6. train mandatory and optional specialist heads sequentially into a transactional staging bundle;
7. refuse main training if the matrix or a mandatory head fails;
8. start the frontend and backend;
9. train/evaluate the main candidate on 98%/2%;
10. stage and smoke-test the full-data shadow refit when the candidate gate passes;
11. write the completion marker only after the complete required run succeeds.

## Residual operational boundaries

- The real-fit smoke took about 32 minutes on this laptop even for one horizon with TCN disabled.
  The 1,000-day, seven-horizon job is a long unattended run.
- The smoke's 13 internal checks and final PASS completed, but the external PTY reported status 1
  after the final line without a traceback. The functional model path passed; this external exit
  anomaly is recorded and is not the sole basis for readiness.
- A backup of the canonical DuckDB was created as
  `data/btc_duckdbs/analytics.duckdb.bak-20260811-preflight` before the idempotent schema check.
- Production remains paper/shadow only after training until release-scoped forward outcomes,
  calibration, recorder health and venue economics clear their frozen gates.

## Verdict

No new reproducible source or business-logic defect was found after the startup fix. The source
tree is ready to launch the 1,000-day rebuild/retrain. The currently saved models are not ready to
serve and are correctly blocked. Accuracy and sustainable profit remain unproven until new
out-of-sample and forward paper evidence exists.
