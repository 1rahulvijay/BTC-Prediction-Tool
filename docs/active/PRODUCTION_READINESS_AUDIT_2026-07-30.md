# Production Readiness Audit

Date: 2026-07-30

## Verdict

The codebase is suitable for continued paper/shadow operation, but the current machine state is
**not ready for production serving yet**.

This statement has two separate meanings:

1. **Paper/shadow decision-support production:** implemented behind a fail-closed preflight, but
   blocked until the artifact and environment gates below pass.
2. **Real-money production:** not implemented or authorized. The health API explicitly reports
   `live_execution.available=false`, no real-order adapter is loaded, and repository policy keeps
   Binance and Polymarket real orders disabled.

No model or validation result guarantees profit.

## Confirmed Blockers On This Machine

| Gate | Current result | Required resolution |
|---|---|---|
| Main ensemble compatibility | **FAIL**: strict preflight refuses the legacy architecture artifact before deserialization because it has no integrity manifest | Retrain and promote the current architecture |
| Active specialist feature contract | **FAIL**: 0/11 serviceable | Retrain through `train_heads.py`; it now writes semantics, provenance and integrity |
| Active specialist training identity | **FAIL**: 0/11 have full manifests | Same retrain; never backfill identity onto old bytes |
| Complete-trade serving | **FAIL**: no verified `champion.json` bundle | Train/evaluate/promote a verified champion; evidence mode refuses legacy models |
| Python environment | **FAIL**: global environment is polluted and version-mismatched | Build `.venv-prod` from `backend/requirements-prod.txt` |
| Public transport | Not configured | Keep Uvicorn on loopback and terminate HTTPS at a reverse proxy/private tunnel |
| Service supervision/backups | Not configured by this repository | Configure a service manager, alerts, and offline DuckDB/artifact backups |

With temporary valid deployment settings supplied, the latest fail-closed paper-production
preflight reported six artifact/model prerequisites and started no server. The global Python
environment also fails `pip check` because unrelated Reflex/Streamlit
packages require different Starlette, Packaging and PyArrow versions. Do not repair that shared
environment in place; use `.venv-prod`.

The old artifacts must not be restamped. A manifest generated after training cannot prove which
data, code, feature semantics or dependency stack created old bytes.

## Second Hardening Pass - 2026-07-30

The external stop-ship audit was checked against executable code. The following defects were
confirmed and fixed without retraining or rewriting evidence:

### Canonical Polymarket market state

- The app-facing client now uses the current raw CLOB market stream, application-level `PING`,
  rolling subscribe/unsubscribe, and the tested Decimal `L2Book`.
- A `book` event is the only event that establishes synchronized state. Incremental
  `price_change` messages received first are refused and counted.
- Zero-size increments delete levels instead of leaving stale liquidity behind.
- Raw CLOB and SDK-style nested payloads are normalized; tick-size, top-of-book, trade and market
  resolution events have explicit handlers.
- Market metadata retains condition/event/market IDs, outcome-token identity, fee fields, tick
  size, order minimum, rules and resolution source.
- Human-readable question text is never parsed into the price-to-beat. That authority remains in
  the dedicated oracle/round tracker.
- Socket liveness and valid-book freshness are separate. Production readiness fails on a
  disconnected socket, stale valid book, excessive parse failures, or repeated increments before
  a snapshot.

Protocol source: `https://docs.polymarket.com/market-data/realtime-data#market-stream`.

### Feed content health

- Binance spot, Binance futures and Coinbase parsers no longer silently swallow malformed public
  frames.
- Each parser reports socket state, valid/unknown/error counts, recent parse-error rate, last-valid
  age, typed stream counts and its latest error.
- Rejected public frames are written as bounded previews under `data/quarantine/*.jsonl`.
- `/api/system-health` exposes protocol health. Production requires healthy Binance spot/futures
  content and healthy Polymarket content, not merely open sockets.

### Order, authority and risk contracts

- The adapter-neutral order lifecycle is now durable and transition-matrix driven. Every
  nonterminal state reserves the instrument, terminal states are immutable, cumulative fills are
  monotonic and bounded, and restart restores unresolved reservations and history.
- Local timeout/connection/5xx outcomes remain `UNKNOWN` until explicit venue reconciliation.
- Future real-order authority is no longer a boolean. A capability is control-token authenticated,
  release-bound, venue/strategy scoped, notional capped, operator attributed, short-lived and
  append/fsync audited.
- No real adapter exists or is authorized; active capability checks alone cannot change that.
- Reduce-only is proven from signed current position, side, quantity and price. Oversized,
  wrong-side, position-flipping, unknown-position and venue-unenforced derivative requests are
  refused. A verified close may still pass through a kill switch/stale feed as an audited degraded
  action so safety logic cannot trap a known position.

### Artifact and research semantics

- `verified_io.stats()` now separates process-local observed loads from repository-wide migration
  state. A fresh process with zero old loads no longer reports migration complete.
- Current measured migration debt remains **39 raw save sites and 14 raw load sites**, all outside
  production serving. The ratchet prevents either count from increasing.
- `research/run_all_sequence.py` now includes the authoritative ceiling/maker scripts, hashes every
  executed script, records runner/Python identity, reports child failures/timeouts and exits
  nonzero when any required child fails. It is still a research suite, not a promotion authority.
- The 98/2 offline gate remains a **shadow-admission gate**. The full-data refit can become primary
  only after independent live paired evidence, minimum calendar duration, positive bootstrap
  lower bound, sufficient economic samples, profit factor and positive expectancy all pass.

### Launch-path validation defect

The full `start.bat` invariant path exposed a pre-existing test bug: the launcher correctly exports
`BTC_FREEZE_MODEL=1`, while the serving pointer-swap test assumed an unfrozen environment. The
standalone test passed and the real launcher test failed. The test now explicitly runs its normal
pointer-swap phase unfrozen, runs the freeze phase frozen, and restores the caller environment.

`BTC_SELFTEST_ONLY=1` now executes the launcher gate and exits before any data download, training,
server startup or database mutation.

## Validation Evidence - Second Pass

The following completed successfully on the modified source:

- repository-wide Python compile and Pyflakes checks;
- `git diff --check`;
- Vite production build;
- canonical Polymarket protocol tests;
- feed content-health/quarantine tests;
- order lifecycle durability/transition tests;
- authenticated authority tests;
- reduce-only adversarial tests;
- control-plane tests over real HTTP;
- production liveness/readiness/WebSocket-origin tests;
- low-level and full Binance paper execution/accounting suites;
- artifact verify-before-deserialize and migration-ratchet tests;
- production-preflight policy selftest;
- the complete `start.bat` invariant gate in selftest-only mode.

The actual strict production preflight was also run. It correctly refused to start and reported:

```text
complete-trade heads: 3/3 refused (unverified legacy directory; no champion pointer)
main ensemble: refused before deserialization (missing integrity manifest)
specialist heads: 0/11 serviceable (missing provenance manifests)
```

This is the intended fail-closed outcome. Do not create manifests for old bytes. Complete a clean
manifest-writing retrain, evaluate the untouched tail, promote only through the frozen gates, and
rerun strict preflight.

## Accuracy And Profit Contract

Code correctness protects measurements; it does not create market edge. The production target is
therefore ordered:

1. preserve causal labels, feature parity and probability calibration;
2. abstain when data, model identity, expected value or execution evidence is weak;
3. measure precision and calibration by horizon, regime, action and price bucket;
4. measure executable PnL with actual ask/bid, fees, slippage, fills and settlement;
5. promote only on independent forward evidence.

No accuracy percentage, win rate or profitability is guaranteed. Real-money execution remains
unimplemented and unauthorized.

## Third Hardening Pass - 2026-07-31

The latest external reviews were triaged against current code instead of being accepted as a
feature list. The proposed 200-signal engine was rejected: duplicated indicators and unvalidated
factor families increase multiple-testing and overfitting risk without proving executable edge.

Two remaining execution-integrity gaps were confirmed and fixed:

### Sequenced Binance L2 evidence

- `backend/venues/binance_l2_recorder.py` now records Binance USD-M BTCUSDT public depth as an
  isolated, read-only archive in `data/binance_l2.duckdb`.
- Every session stores the REST snapshot and `lastUpdateId`, raw diff rows with `U`, `u` and `pu`,
  exchange and receive timestamps, payload hashes, top-book replay hashes, and explicit gap rows.
- A sequence gap closes the current session and forces a fresh WebSocket plus REST snapshot. No
  uncertain local book continues after a gap.
- The database has a 10 GB default cap, batched writes, duplicate-process protection through the
  recorder supervisor, a standalone launcher, a report command and an offline replay self-test.
- An 8-second public-feed smoke synchronized successfully, recorded 66 raw diffs with zero gaps,
  and replay reproduced the final update ID, best bid, best ask and top-book SHA-256 exactly.
- Public aggregate L2 still does not expose individual order priority. Exact passive-fill labels
  and production execution RL remain refused; only deterministic book replay and conservative
  queue research are supported.

Run or inspect it with:

```powershell
.\start_binance_l2_recorder.bat
python backend\venues\binance_l2_recorder.py --report
python backend\venues\rl_data_readiness.py
```

### Maker research accounting

- `research/maker_lever_test.py` no longer grants both entry and exit passive fills.
- The test now models strict-trade-through passive entry followed by an explicit taker exit at the
  fixed horizon.
- Directional return is calculated directly from limit entry to horizon exit; the prior additive
  approximation was removed.
- Cost comes from the frozen maker-conversion protocol and includes a taker-exit spread allowance.
  Account-specific fees remain unverified, so the result is diagnostic and cannot support
  promotion.
- Hard-coded historical conclusions were removed. The script reports only the result generated by
  the current data and code.
- `research/ceiling_analysis.py` now compares taker/taker with a 7 bps passive-entry/taker-exit
  proxy instead of the invalid 1.5 bps two-passive-leg assumption.

These changes improve evidence correctness. They do not improve model accuracy by themselves and
do not establish profitability.

Validation for this pass included the new offline snapshot/diff/gap/replay self-test, an 8-second
public Binance smoke and deterministic replay, corrected maker accounting self-test and full
200,000-row run, launcher/layout checks, backend Pyflakes, repository compilation, documentation
table checks, research/preregistration audits, Vite production build, npm audit with zero known
vulnerabilities, and the complete `start.bat` selftest-only gate. A whole-repository Pyflakes scan
still reports unused imports and constant f-strings in preserved legacy `research/v*` prototypes;
those scripts are already classified as non-evidence and were not rewritten.

## Production Hardening Implemented

### Artifact safety

- `verified_io` now separates byte integrity (`.integrity.json`) from full provenance
  (`.manifest.json`). The prior filename collision could overwrite one contract with the other.
- Full provenance hashes can be verified before deserialization.
- Standalone-head training writes:
  - artifact SHA-256;
  - feature and training semantics versions;
  - feature-schema and training-dataset hashes;
  - training cutoff;
  - Git commit and dirty-state marker;
  - model runtime dependency fingerprint.
- Active standalone loaders enforce both feature semantics and current training identity.
- A dirty working tree is refused by `train_heads.py` unless deliberately overridden for research.
- The rejected fade challenger is research-only and no longer blocks active serving readiness.
- Model identity no longer confuses the 3-day live boot window with a 1,265-day training window.

### HTTP and control-plane safety

- Production CORS and WebSocket origins use the same explicit allowlist.
- Production WebSockets reject unlisted browser origins.
- Admin token comparison is constant-time and weak configured tokens are refused.
- Interactive API documentation is disabled in production.
- Security headers include CSP, frame denial, MIME sniffing prevention and HSTS behind HTTPS.
- Replay parameters are bounded.
- Browser admin buttons are disabled in production; privileged actions require operator headers.
- `/healthz` is mechanical liveness; `/readyz` returns HTTP 503 until boot, models, feeds, writers
  and required complete-trade heads are healthy.

### Deployment behavior

- Frontend API/WebSocket locations are environment-aware and same-origin in production.
- FastAPI can serve the immutable Vite `dist/` build.
- `start_production.bat`:
  - never trains or backfills;
  - forces real-order flags off;
  - forces strict artifact identity and evidence mode on;
  - refuses an ambiguous launch if the configured port is already occupied;
  - builds the frontend;
  - runs the production preflight;
  - starts one Uvicorn worker only after every gate passes.
- Backend logs can rotate under `BTC_LOG_DIR`.

### Recorder correctness

- Archive timestamps stored in epoch seconds are normalized to milliseconds before span
  calculations. The old report understated coverage by 1,000 times.
- Event-conditional contracts now name the streams the recorder actually writes:
  `aggTrade_rest`, `premiumIndex`, and `coinbase/ticker`.
- Binance `forceOrder` liquidation events are recorded with stable event identity.
- Liquidations are not treated as a per-episode liveness requirement because a quiet interval is
  valid.

## Clean Environment

From a clean PowerShell:

```powershell
py -3.13 -m venv .venv-prod
.\.venv-prod\Scripts\python.exe -m pip install --upgrade pip
.\.venv-prod\Scripts\python.exe -m pip install -r backend\requirements-prod.txt
.\.venv-prod\Scripts\python.exe -m pip check
```

The production requirements include the PyTorch sequence-model runtime. A CUDA build may replace
the CPU wheel, but its PyTorch version must remain identical because dependency versions are part
of model identity.

## Artifact Build And Promotion

Run training only from a clean committed `master`:

```powershell
git status --short
.\start.bat
```

After the long-window run finishes, do not start production until all commands return zero:

```powershell
.\.venv-prod\Scripts\python.exe backend\check_model_compatibility.py
.\.venv-prod\Scripts\python.exe backend\check_feature_contract.py --enforce-serving
.\.venv-prod\Scripts\python.exe backend\verify_artifact_identity.py --strict
```

Evaluate the challenger with the predeclared holdout, calibration, head-health and economic gates.
Only then run the dry-run and applied promotion commands against the actual challenger directory:

```powershell
.\.venv-prod\Scripts\python.exe backend\promote_challenger.py --challenger <challenger-dir> --days 1265
.\.venv-prod\Scripts\python.exe backend\promote_challenger.py --challenger <challenger-dir> --days 1265 --apply
```

The production preflight additionally requires all three complete-trade heads to resolve through a
verified champion pointer. It will not accept the legacy `saved_models` fallback.

## Production Launch

Set secrets outside Git:

```powershell
$env:BTC_ADMIN_TOKEN = '<random-secret>'
$env:BTC_CONTROL_TOKEN = '<different-random-secret>'
$env:BTC_ALLOWED_ORIGINS = 'https://btc.example.com'
```

Then:

```powershell
.\start_production.bat
```

Expected probes:

```text
GET /healthz  -> 200 when the process is alive
GET /readyz   -> 200 only when the service may receive traffic
GET /readyz   -> 503 with explicit blockers otherwise
```

Keep `BTC_BIND_HOST=127.0.0.1` and place TLS/authenticated network access in front of it. A
non-loopback bind is refused unless `BTC_ALLOW_PUBLIC_BIND=1` is explicitly set. Do not expose raw
Uvicorn over the public internet.

## Validation Evidence

The complete 64-step local platform gate passed on 2026-07-30 after two detected test-fixture
regressions were corrected without relaxing artifact checks. It covered:

- repository-wide Python compilation;
- frontend production build;
- npm dependency audit: zero known vulnerabilities;
- control-plane authentication over real HTTP;
- production liveness/readiness, headers and WebSocket-origin tests;
- paper execution, accounting, fees, slippage, funding, risk and restart recovery;
- order unknown-state and close-only authority;
- recorder parser/episode selftests;
- event-conditional archive and timestamp-unit selftests;
- all 16 DuckDB files opened read-only and passed metadata/storage queries.

GitHub-hosted CI has not executed because the account is billing-locked, so this evidence is local.
The complete CI/platform gate must be rerun after every production-hardening change and again after
artifact promotion. Passing code tests does not override the current artifact blockers.
