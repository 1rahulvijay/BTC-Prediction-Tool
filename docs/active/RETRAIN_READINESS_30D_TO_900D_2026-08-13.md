# Retrain Readiness: 30-Day Smoke To 900-Day Run

Date: 2026-08-13

## Scope

This pass repaired the failed 30-day research-matrix build and validated the exact startup,
training, model-publication, paper-trading, recorder, backend, and frontend gates used by
`start.bat`. It did not claim that any model or strategy is profitable.

## Failure And Root Cause

The failed run reconstructed 30 days of Binance spot aggregate trades correctly, ending on
2026-08-11. The OHLC parity gate compared those rows only with
`data/cache/btcusdt_1m_3d.json`, whose final official Binance candle was 2026-07-04. There were
zero overlapping minutes because the reference was stale, not because reconstructed OHLC was
wrong.

The old implementation treated zero date overlap as an OHLC mismatch and stopped all specialist
and ensemble training. Preserving the previous matrix was correct; selecting a fixed stale cache
was not.

## Code Corrections

### Overlap-aware official OHLC parity

`backend/build_research_matrix.py` now:

- examines available official Binance 1-minute cache files in freshness order;
- selects a reference only when at least 100 timestamps overlap the reconstructed bars;
- normalizes seconds, milliseconds, and microseconds safely;
- fetches and atomically caches a small aligned official Binance REST tail only when no local
  reference overlaps;
- records every checked reference and overlap count in the matrix manifest;
- still fails closed on a real OHLC mismatch or on insufficient proof;
- preserves the previous valid matrix on failure.

A regression test proves that a stale short cache is skipped in favor of an overlapping valid
cache. The existing deliberate-price-corruption test still fails parity as required.

### One-knob 30-day to 900-day transition

`start.bat` now accepts `BTC_HISTORICAL_DAYS` as an environment override while retaining 30 days
as the default. The train/holdout split follows the requested window automatically:

- 30 days or less: `BTC_TRAIN_SPLIT_FRAC=0.95` for a usable smoke holdout;
- more than 30 days, including 900: `BTC_TRAIN_SPLIT_FRAC=0.98`;
- an explicitly pre-set `BTC_TRAIN_SPLIT_FRAC` remains an operator override.

The historical, serving warm-up, model-training, backfill, completion-marker, and split settings
were validated for both 30 and 900 days.

## Measured 30-Day Matrix Result

The complete forced rebuild succeeded using cached source files:

| Check | Result |
|---|---:|
| Rows | 43,200 |
| Unique timestamps | 43,200 |
| Span | 30.0 days |
| Sorted timestamps | yes |
| Trade-feature coverage | 100.00% |
| Cross-venue coverage | 100.00% |
| Monthly quality gate | passed, 2 months |
| Official reference overlap | 37,928 minutes |
| OHLC median absolute difference | 0.0 USD |
| OHLC p99 absolute difference | 0.0 USD |
| Infinite numeric cells | 0 |

The 30 null cells are only the unavoidable unresolved future-label tail. They are not input-feature
gaps.

## Feature-Parity Result

The application continues to build 136 raw fields because the UI and diagnostics consume them.
The main fitted ensemble uses 63 model fields classified as `KEEP` or `PARITY-FIX`. Live-only
order-book, derivatives, options, liquidation, external, regime-snapshot, and Polymarket fields
without causal historical parity remain excluded from the main model. The warning about 66
live-signal columns therefore does not mean those zero-history fields enter the fitted ensemble.

## Validation Executed

- complete `start.bat` invariant suite: passed;
- complete backend Python compilation: passed;
- research-matrix selftest, including stale-cache regression: passed;
- real 30-day matrix build: passed;
- training identity check: passed;
- specialist-head transactional dry run: passed;
- training integrity: passed;
- bundle completeness and rollback tests: passed;
- 30-day launcher configuration simulation: passed, 95/5;
- 900-day launcher configuration simulation: passed, 98/2;
- 900-day resource preflight: passed in `REBUILD` mode;
- frontend Vite production build: passed;
- standalone capture application: 21 tests passed from repository-root discovery;
- all changed capture/research Python files: compilation and Pyflakes passed;
- all research-lane JSON result artifacts: parsed successfully;
- whitespace and Python lint checks for the parity change: passed.

The 900-day preflight measured more than 1,300 days of both trade-feature and cross-venue derived
coverage, more than 1,000 cached aggregate-trade files, and enough free disk for the 80 GB rebuild
floor. This is a readiness check, not an estimate that the full training will be fast.

## Operator Sequence

### First run: 30-day smoke

Run `start.bat` normally. Do not set `BTC_HISTORICAL_DAYS`; 30 is the default. Let the full head
and ensemble training finish. A successful run writes
`data/saved_models/full_retrain_30d_complete.json`. Do not treat the 30-day artifact as the
production candidate; it validates mechanics and publication.

### Second run: 900-day candidate

Close the backend after the 30-day smoke completes, then launch from PowerShell with:

```powershell
$env:BTC_HISTORICAL_DAYS = "900"
.\start.bat
```

The launcher automatically sets model-training days, backfill days, serving warm-up, completion
marker, and the 98/2 split to 900-day values. Existing daily cache files are reused.

## Honest Limits

- Passing software and data-integrity tests does not guarantee accuracy, win rate, profit, or
  sustainable income.
- The 30-day smoke must not be promoted on its training metrics.
- The 900-day candidate still needs untouched-tail evaluation, staged publication, smoke reload,
  and forward paper evidence before promotion.
- Both the Binance and Polymarket money paths remain paper-only until cost-adjusted, live,
  bundle-scoped evidence clears their predeclared gates.
- A clean Git commit is mandatory before training so artifact manifests can record reproducible
  code provenance. Training from a dirty tree remains blocked intentionally.

## Complete Runtime Configuration At Commit 773b61c

The validated repository state is:

| Item | Value |
|---|---|
| Branch | `master` |
| Commit | `773b61c` |
| Working tree after commit | clean |
| Default training window | 30 days |
| Default 30-day split | 95% fit / 5% untouched tail |
| 900-day split | 98% fit / 2% untouched tail |
| Main horizons | 5m and 15m |
| Raw application features | 136 |
| Main-model features | 63 |
| Feature selection | `KEEP,PARITY-FIX` |
| Direction sample cap | 40,000 |
| TCN sample cap | 25,000 |
| Training threads | 8 |
| LightGBM device | CPU |
| Scheduled relearning after boot | frozen |
| Startup backtest | disabled |
| Strict artifact identity | enabled |
| Real-money conclusion | not authorized by this validation |

`BTC_FREEZE_MODEL=1` means the deliberate startup retrain still runs when the completion marker is
absent, but scheduled retraining stays disabled after the trained bundle is available. A browser
refresh does not invoke `start.bat` and does not restart training.

## Main Ensemble Inventory

The main direction ensemble fits and serves these seven base seats where the dependency is
installed and the horizon/regime has a valid fitted artifact:

| Seat | Role |
|---|---|
| XGBoost | nonlinear boosted-tree direction model |
| LightGBM | diverse boosted-tree direction model, CPU on this laptop |
| CatBoost | noise-tolerant boosted-tree direction model |
| HistGradientBoosting | CPU histogram-tree baseline/diversifier |
| Logistic Regression | linear sanity-check model |
| Random Forest | bagged-tree diversity and variance control |
| TCN deep model | temporal sequence representation |

The ensemble also persists class priors, move-size state, conformal residuals, calibrated stackers,
feature-reference state, HMM regime state, model-feature schema, bundle metadata, and an architecture
version. Bundle completeness tests require all mandatory support artifacts. A version marker is
written last so an interrupted save cannot look complete.

The 30-day smoke does not change the measured research conclusion that endpoint direction is hard
and often near coin-flip. The ensemble must abstain when its probability, calibration, economic,
head-health, or evidence gates do not support a call.

## Specialist-Head Inventory

With no valid 30-day completion marker, `start.bat` sets `BTC_FORCE_HEAD_RETRAIN=1`. The forced,
transactional dry run confirmed that all fourteen requested specialist jobs are scheduled:

| Head | Primary output or purpose |
|---|---|
| `selectivity` | predicts whether a base call is worth retaining |
| `signed_quantile` | signed move/range quantiles and calibrated uncertainty |
| `persistence` | price-to-beat leader-hold probability |
| `path_forecaster` | touch, path, range, and round-trip forecasts |
| `round_state` | flip, shock, opportunity, and round-state heads |
| `bigmove` | probability of a meaningful absolute move |
| `bigdrop` | downside tail-risk specialist |
| `directional` | specialist directional keeper |
| `activity` | market-activity/volatility specialist |
| `champion_meta` | combines admissible specialist evidence |
| `beat` | price-to-beat classifier research/serving artifact |
| `magnitude` | move-magnitude quantiles |
| `path` | historical tick-path label/model artifact |
| `fingerprints` | historical similar-state evidence |

The non-forced dry run correctly skipped a missing legacy `beat` artifact, but the actual first
30-day launch is forced and therefore schedules `beat` as shown above. Optional heads may legally
decline to save when their predeclared noise or data gate fails. That is an abstention, not a
training crash. Mandatory heads may not disappear or fail identity validation.

## Transactional Training And Publication Flow

The launch path is fail-closed and ordered as follows:

1. Resolve 30 or 900 days into historical, model-training, warm-up, backfill, split, and marker
   namespaces.
2. Run disk/data preflight and all startup invariant tests.
3. Acquire the process-backed full-retrain lease so another job cannot rewrite the matrix.
4. Detect any existing frontend/backend immediately before side effects; replace them only under
   the configured launcher policy.
5. Start or deduplicate all enabled recorders.
6. Incrementally update trade-feature, persistence, and cross-venue derived data.
7. Build the requested research matrix and require source coverage, monthly quality, official
   OHLC parity, timestamp validity, and exact manifest/hash identity.
8. Train all specialist heads into a copied staging bundle.
9. Require every mandatory staged artifact to pass integrity and provenance checks.
10. Move the incumbent bundle to a timestamped rollback directory and atomically replace it with
    the complete staged bundle.
11. Release the training lease. If required head training failed, stop before main-ensemble
    training.
12. Start the frontend and backend. The backend performs the forced main-ensemble candidate fit
    because no completion marker exists.
13. Evaluate the candidate on the untouched tail and apply predeclared promotion gates.
14. If the validated-refit flow is admitted, refit the production challenger on all eligible rows,
    retain OOF/calibration evidence, smoke-load it, and keep it under live shadow verification.
15. Write the completion marker only after the required heads and main model have completed their
    publication contract.

Training failures preserve the previous matrix and incumbent serving bundle. They do not stamp
stale artifacts as current, create a false completion marker, or partially swap a model directory.

## Forward Recorder Inventory

`start.bat` calls `backend/start_recorders_once.ps1`. It matches the exact Python executable and
absolute script path, skips an already-running duplicate, starts missing processes hidden, and
redirects each process to its own stdout/stderr log. The ten enabled recorder families are:

| Recorder | Evidence captured |
|---|---|
| Polymarket quote and settlement | market quotes, round identity, official outcomes |
| Polymarket exact L2 and VWAP | depth ladders and size-specific executable prices |
| Fast Binance BTC tick stream | sub-second reference trades on the same host clock |
| Cross-exchange microstructure | synchronized venue microstructure snapshots |
| Multi-venue event-time collector | Binance spot/perp, Bybit, and Coinbase event-time data |
| Binance funding and basis | immutable settled funding plus basis observations |
| Binance sequenced L2 | snapshot/diff order-book reconstruction and gap evidence |
| High-frequency anchor crossings | touch/cross/first-passage evidence |
| Polymarket cross-window observations | synchronized 5m/15m dominance observations |
| Deribit BTC option chain | per-strike implied-volatility and straddle evidence |

These recorders use public market data and do not create trading authority. Recorder liveness,
continuity, timestamps, schema, and gaps remain separate from model correctness.

## Paper Trading Configuration

The validated launcher configures a comparison with two independent paper bankrolls:

| Lane | Configuration |
|---|---|
| Polymarket | `$500`, rule `CHAMPION_DYNAMIC_PAPER_V1` |
| Binance derivatives | `$500`, strategy `model_consensus` |
| Binance paper auto-start | enabled |
| Competition store | `data/binance_paper_competition_500.duckdb` |

Paper accounting, entry/exit cost inclusion, liquidation handling, fixed bankroll isolation,
position persistence, official outcome handling, degraded-model exits, and rollback behavior were
covered by the startup invariant suite. This proves accounting/control behavior against test
fixtures. It does not prove either strategy has positive expected value.

## Exact Validation Evidence

### Research matrix

The real 30-day build covered 2026-07-13 00:00 UTC through 2026-08-11 23:59 UTC. The stale fixed
reference covered only 2026-07-01 through 2026-07-04 and therefore had zero overlap. The corrected
selector chose an overlapping official cache and measured 37,928 matching minutes.

The following properties were directly inspected after publication:

- 43,200 rows and 43,200 unique timestamps;
- timestamps monotonic increasing;
- 47 matrix columns;
- no infinite numeric cells;
- 30 null cells confined to the unresolved five-minute future-label tail;
- 100% trade-feature join coverage;
- 100% cross-venue join coverage;
- two monthly quality partitions passed;
- matrix manifest requested exactly 30 days and described the parquet hash on disk;
- official OHLC median and p99 absolute difference both 0.0 USD.

### Startup invariants

The full post-integration `BTC_SELFTEST_ONLY=1` startup suite passed these groups:

- complete-trade label, M0, builder, execution, and serving contracts;
- durable ledger V2 and evidence completion;
- frozen-artifact and champion promotion behavior;
- feed callback, feed-writer, regime, Kelly, and launcher integrity;
- model registry, artifact bundle, order lifecycle, authority, and task supervision;
- control-plane security and verified deserialization;
- head permissions, quantile safety, window namespaces, marker contract, and startup side effects;
- training lease, artifact readiness, websocket payload, DuckDB retry, and DB health;
- release atomicity, specialist provenance, meta-model, bundle completeness, and training integrity;
- Binance paper engine, probability namespace, period loss, post-fill risk, sizing, and exit cost;
- multi-venue schema, venue admissibility, L2/tick/crossing/funding/recorder health;
- collector D1-D5 evidence integrity;
- strategy registry and documentation consistency;
- challenger gates, quant-platform kernel, research validation, paper evidence, settlement,
  degraded exits, and model rollback.

### Additional checks

- `python -m compileall -q backend`: passed;
- `python backend/build_research_matrix.py --selftest`: passed;
- `python backend/verify_artifact_identity.py --training-only`: `READY TO RETRAIN`;
- forced transactional specialist dry run: all fourteen jobs scheduled, exit 0;
- `npm.cmd run build`: passed with Vite;
- `python -m unittest discover -s capture_app/tests -v`: 21/21 passed;
- changed capture/research Python files: compilation and Pyflakes passed;
- research JSON artifacts: 12/12 parsed;
- `git diff --check`: passed before commit;
- secret-pattern scan over changed source/documentation: no embedded key pattern found;
- repository committed cleanly on `master` as `773b61c`.

## Expected 30-Day Console Flow

The first normal launch should show these broad states:

```text
[mode] No completion marker. Forcing one full 30d retrain.
[preflight] ... mode=SHORT_WINDOW ... OK
[selftest] All invariant selftests passed.
[recorder] ... running or already active
[0/3] a/b/c ... incremental source updates
[0/3] c2 ... research matrix
Official OHLC parity: passed=True overlap>=100
Joined source coverage: trade_features>=98%, crossvenue>=98%
[0/3] d ... specialized heads
[heads] transactional staging=...
[heads] active bundle swapped atomically; rollback=...
[TRAIN] ... main ensemble training
```

The exact model-training messages vary by installed dependencies and by optional heads that
legitimately decline their noise gate. The run is not complete merely because the frontend opens.
Completion is represented by the validated marker and a loadable identity-compatible bundle.

## Post-30-Day Verification

After training finishes, check:

```powershell
Test-Path .\data\saved_models\full_retrain_30d_complete.json
python backend\validate_retrain_marker.py `
  --marker data\saved_models\full_retrain_30d_complete.json --days 30
python backend\verify_artifact_identity.py
python backend\check_feature_contract.py
```

Also confirm in the UI and logs:

- backend boot/model bundle identifiers are current;
- recorder health advances rather than only showing processes alive;
- no model or head is silently serving an old window;
- 5m and 15m predictions resolve into DuckDB;
- Polymarket and Binance paper trades remain isolated at `$500` each;
- action, direction, target error, and realized PnL are reported separately;
- insufficient live sample counts remain warnings rather than confident scorecards.

Do not proceed to the 900-day run if the 30-day marker, strict identity, bundle reload, or forward
recording checks fail.

## Post-900-Day Verification

After launching with `BTC_HISTORICAL_DAYS=900`, require:

```powershell
Test-Path .\data\saved_models\full_retrain_900d_complete.json
python backend\validate_retrain_marker.py `
  --marker data\saved_models\full_retrain_900d_complete.json --days 900
python backend\verify_artifact_identity.py
python backend\check_feature_contract.py
```

Then compare the 900-day candidate with the incumbent using the untouched tail and subsequent
bundle-scoped forward paper evidence. Do not promote it because it used more history. More history
can improve regime coverage while also diluting recent relationships; measured gates decide.

## Tested Versus Not Yet Executed

| Item | Status |
|---|---|
| Stale official-cache defect | fixed and regression-tested |
| Real 30-day research matrix | built and validated |
| Exact 30-day startup configuration | validated |
| Exact 900-day startup configuration | validated |
| 900-day disk/source preflight | passed |
| All startup invariant tests | passed twice, including final post-integration run |
| All fourteen specialist jobs | forced transactional dry run passed |
| Actual 30-day specialist fits | not executed by this audit |
| Actual 30-day main-ensemble fit | not executed by this audit |
| Actual 900-day matrix build | not executed by this audit |
| Actual 900-day specialist/main fit | not executed by this audit |
| Forward profitability proof | not established |
| Real-money authorization | not granted |

## Failure Interpretation

- `Official OHLC parity ... overlap=0`: reference selection/availability problem; do not call it a
  price mismatch without an overlapping comparison.
- `passed=False` with real overlap and excessive median/p99 differences: genuine data-integrity
  failure; stop and preserve the previous matrix.
- source coverage below 98% or monthly gate failure: do not train specialists on the incomplete
  matrix.
- dirty Git tree: commit the intended code or stop; do not bypass strict provenance for a keeper
  artifact.
- optional head exits 0 without an artifact: valid noise/data abstention when explicitly labeled.
- mandatory head missing or nonzero exit: no transactional swap and no completion marker.
- frontend available while training continues: UI availability is not model readiness.
- recorder process alive but timestamps/row counts stale: data is not healthy.
- high backtest accuracy without live cost-adjusted evidence: not promotion evidence.

## Final Readiness Statement

At commit `773b61c`, the repository is mechanically ready for the operator to start the 30-day
full-pipeline smoke. The specific blocker shown in the original log is fixed, the real matrix now
passes, and the launch/training contracts are synchronized. The 900-day path is configuration- and
resource-ready but remains unexecuted.

No finite audit can prove that no undiscovered bug exists. No software validation can guarantee
accuracy or profit. The valid claim is narrower: all exercised code, data, transaction, accounting,
identity, publication, and launcher gates passed, and the expensive model fits are now the next
step rather than another code change.

## Actual 30-Day Attempt And Persistence Fix - 2026-08-13

The first real 30-day full-pipeline attempt progressed beyond every preflight and startup invariant,
reused the validated 43,200-row matrix, and trained multiple specialist heads. It then found a real
bug in the optional persistence keeper's production-refit path.

### What passed before the failure

- startup invariant suite: passed;
- training lease and recorder launch: passed;
- matrix identity: `READY TO RETRAIN`;
- transactional specialist staging: active and isolated from the incumbent bundle;
- selectivity: P(big move) AUC `0.737`, tradability AUC `0.798`, invalidation AUC `0.733`;
- signed 80% bands: 5m raw/CQR coverage `79.3%/76.1%`, 15m `80.3%/81.3%`;
- base P(Hold): test AUC `0.7365` on `726,642` untouched snapshots;
- P(Hold) at calibrated threshold `>=0.93`: `97.5%` realized hold, `17.2%` coverage;
- P(Hold) 5m/15m at `>=0.93`: `96.4%` / `98.5%` realized hold;
- path forecaster: 5m touch AUCs `0.779/0.830`, 15m `0.737/0.784`;
- supported round-state heads kept their predeclared gates; unsupported heads remained shadow/off;
- big-move test AUC: 5m `0.722`, 15m `0.695`;
- big-drop 5m test AUC observed before the supplied log ended: `0.750`.

These are held-out model diagnostics, not proof of executable profit.

### Failure and root cause

The optional keeper challenger measured worse than the base model on the current untouched tail:

```text
overall AUC: base 0.7353, keeper 0.7342, lift -0.0012
late AUC:    base 0.8249, keeper 0.8148, lift -0.0101
```

Despite that regression, the old trainer still attempted to refit the keeper. The base persistence
archive spans roughly 1,305 days, while keeper features come from the current 30-day research
matrix. The refit reused the base archive's time cutoff against the shorter keeper frame, assigning
all joined keeper rows to calibration and zero rows to fit. Scikit-learn correctly rejected the
`(0, 11)` fit matrix.

### Corrections

1. The keeper is now an optional challenger with a hard promotion rule: both overall and
   late-window untouched-tail AUC must improve. Equal, negative, or non-finite lift abstains.
2. A rejected keeper records diagnostics and `keeper_promoted=false` but saves no keeper estimator.
   Serving therefore uses the validated base P(Hold) model.
3. Keeper production refit derives its split from the keeper frame's own time span.
4. Base and keeper production fit rows are purged when their outcomes cross into calibration.
5. Production keeper refit also checks minimum rows and both-class support; invalid input abstains
   instead of crashing the mandatory base artifact.
6. `HEAD_VERSION` was bumped so the corrected persistence artifact must be rebuilt.
7. A new launcher invariant, `test_persistence_keeper_refit.py`, covers the gate, the exact observed
   regression, independent source spans, non-empty fit/cal partitions, and purge arithmetic.
8. Launcher text now distinguishes matrix-backed 30/900-day heads from P(Hold) and round-state,
   whose own archives are hashed explicitly in artifact provenance.

### Safety behavior verified

The original specialist transaction completed with failure, removed its staging directory, wrote
no 30-day completion marker, released the training lease, and did not modify the live persistence
artifact. The incumbent remained dated 2026-08-04. Main-ensemble training did not start against the
incomplete specialist bundle.

### Real-data regression run

The corrected persistence trainer was then executed separately against all `14,658,527` snapshots
with output directed to an isolated disposable directory. It completed with:

```text
base test AUC        0.7364633345
production refit     yes
keeper promoted      false
keeper estimator     absent
integrity reload     passed
exit code             0
```

Python compilation, Pyflakes, the 19-check causal/purged test, specialist provenance, model-serving
risk contract, model-bundle completeness, and the new keeper-refit regression test passed after the
fix. The whole backend and capture app also passed `compileall`; `git diff --check` passed. Finally,
the complete launcher suite was rerun with `BTC_SELFTEST_ONLY=1` and all invariant groups `a` through
`m` passed against the corrected tree.

### Required retry

The failed transaction was deliberately not resumed in place. Commit the exact fix so provenance is
clean, then run `start.bat` again. Cached matrix and backfills will be reused. Require the 30-day
completion marker, strict identity validation, atomic bundle swap, and main-ensemble completion
before considering the smoke successful or changing the requested window to 900 days.

## Completed 30-Day Retry Outcome - 2026-08-13

The corrected retry completed the full specialist-head transaction. The persistence regression
did not recur: the base P(Hold) production refit completed, the inferior keeper was rejected, the
staged specialist bundle passed validation, and the specialist transaction swapped atomically.

The main direction candidate then completed training and holdout evaluation. It was correctly
rejected rather than published:

| Horizon | Holdout rows | Directional calls | Direction precision | Brier | Prior Brier | Result |
|---|---:|---:|---:|---:|---:|---|
| 5m | 2,087 | 475 | 36.63% | 0.5141 | 0.5466 | reject: precision below 50% |
| 15m | 2,077 | 1,571 | 42.01% | 0.6616 | 0.6557 | reject: precision below 50% and no Brier skill |

The 5m overall accuracy of 62.72% is not a contradictory success: it is dominated by NEUTRAL
classification. The economically relevant directional calls were only 36.63% correct. At 15m,
overall accuracy was 39.91% and directional precision was 42.01%. Neither candidate is safe to
serve or use for capital decisions.

The canonical evidence is:

```text
data/saved_models/promotion_reports/eval30_1786641950_b088aa31.json
```

No main bundle manifest and no `full_retrain_30d_complete.json` marker were written. That is the
required fail-closed behavior. Specialist heads remain independently usable according to their own
permissions and gates; the main ensemble remains unavailable. Do not lower the promotion gates to
force a model into service.

### Useful specialist evidence retained

- P(Hold) base test AUC: `0.7365`; P(Hold)>=0.93 realized hold: `97.5%` at `17.2%` coverage.
- path forecaster touch AUC: 5m `0.779/0.830`; 15m `0.737/0.784`.
- big-drop AUC: 5m `0.750`; 15m `0.760`.
- activity AUC: 5m `0.802`; 15m `0.788`.
- champion meta holdout AUC: `0.7471`, with round-cluster lower bound `0.7375`.
- beat-direction classifier: 5m `0.514`, 15m `0.541`; classified as noise and not saved.
- round-state heads below their frozen gates remained unsupported/shadow rather than being forced.

These are prediction diagnostics, not executable-profit proof.

### Post-run correctness fixes

The run exposed two observational issues, both corrected without changing model gates:

1. Protocol C residual, partial-fill and settlement coverage mixed action-row numerators with a
   snapshot denominator, allowing impossible values above 100%. All three now count distinct
   snapshots, and the self-test enforces the closed interval `[0, 1]`.
2. Binance futures `aggTrade` WebSocket subscriptions are silent from this host even though the
   book stream and REST aggregate trades are live. Core futures CVD now uses the working raw
   `trade` WebSocket, while aggregate trade intensity uses a nonblocking, deduplicated REST poll.
   This preserves signed-flow and aggregate-count semantics instead of calling missing data a
   quiet market. The isolated live smoke observed both sources advancing with no poll error.

The missing model-revision database warning now accurately says it is waiting for the first
prediction from an active, serviceable main model. Restarting without such a model cannot create
revision evidence.

### Next training decision

The 30-day run proved the mechanics but did not produce a promotable main ensemble. The next
candidate may use the preflighted 900-day window to improve regime coverage and thin OOF folds,
but 900 days is not a promise of better accuracy. It must pass the same untouched-tail precision,
probability and identity gates. Until then, the app remains paper-only and model-dependent actions
must continue to refuse when the main ensemble is unavailable.
