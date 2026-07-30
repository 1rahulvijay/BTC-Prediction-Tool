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
| Main ensemble compatibility | **FAIL**: legacy bundle is v11 while code is v12, and it has no strict integrity sidecar | Retrain and promote the current architecture |
| Active specialist feature contract | **FAIL**: 0/11 serviceable | Retrain through `train_heads.py`; it now writes semantics, provenance and integrity |
| Active specialist training identity | **FAIL**: 0/11 have full manifests | Same retrain; never backfill identity onto old bytes |
| Complete-trade serving | **FAIL**: no verified `champion.json` bundle | Train/evaluate/promote a verified champion; evidence mode refuses legacy models |
| Python environment | **FAIL**: global environment is polluted and version-mismatched | Build `.venv-prod` from `backend/requirements-prod.txt` |
| Public transport | Not configured | Keep Uvicorn on loopback and terminate HTTPS at a reverse proxy/private tunnel |
| Service supervision/backups | Not configured by this repository | Configure a service manager, alerts, and offline DuckDB/artifact backups |

The fail-closed paper-production preflight reported seven failed prerequisites and started no
server. The global Python environment also fails `pip check` because unrelated Reflex/Streamlit
packages require different Starlette, Packaging and PyArrow versions. Do not repair that shared
environment in place; use `.venv-prod`.

The old artifacts must not be restamped. A manifest generated after training cannot prove which
data, code, feature semantics or dependency stack created old bytes.

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
