# Universal Forecast Adapters V1

Date: 2026-07-28

Status: **implemented and validated as a research-only evidence bridge; no
training, serving, paper decision, promotion, allocation, or order path**

## Purpose

The universal ledger can combine evidence only after each model output has a
precise economic target and trustworthy provenance. This implementation
connects existing research campaigns without relabeling one output as another.

```text
source campaign DuckDB
    -> read-only target adapter
    -> exact TargetContract
    -> immutable FORWARD forecast
    -> separately resolved outcome
    -> readiness report
```

Source campaign databases retain one writer each. The adapter opens them
read-only and writes only:

```text
data/research/model_forecast_ledger_v1.duckdb
data/research/model_forecast_adapters_v1/readiness.json
```

The research boundary is frozen in:

```text
backend/research/forecast_adapters_v1/frozen_protocol.json
```

## Invariants

Every admitted forecast requires:

- a clean 40-character Git commit;
- a lowercase SHA-256 dataset identity;
- a lowercase SHA-256 ordered feature-schema identity;
- a lowercase SHA-256 frozen protocol identity;
- a positive training cutoff strictly before the forecast;
- one exact venue, instrument, horizon, role and outcome definition;
- one finite probability or distribution output;
- `FORWARD` evidence from these live shadows.

Forecast IDs are deterministic hashes of the contract, forecast timestamp,
candidate, model, version and evidence kind. Re-running an adapter is
idempotent. Reusing an ID with changed content raises an immutable-collision
error.

Outcomes are separate records. Missing fill, fee, slippage, latency or return
data remains SQL `NULL`; it is never converted to zero.

## Implemented Adapters

### Polymarket 1-Hour Settlement

One contract:

```text
role: SETTLEMENT
venue: POLYMARKET
instrument: BTC_UP_DOWN_1H
horizon: 3600 seconds
label: finalized Binance close >= open, reconciled with Polymarket
```

Three model rows are written independently:

1. Normalized Polymarket midpoint.
2. Distance/time slow-volatility probability.
3. Fast/slow/jump volatility-mixture probability.

Only the six checkpoints frozen in the campaign protocol are imported. This
creates aligned three-model panels without copying every one-second snapshot.
The analytic baselines have no training dataset, so their dataset provenance is
the canonical hash of an explicit `analytic_no_training_dataset` manifest.
Their training cutoff is the frozen protocol timestamp.

### Polymarket 5-Second Repricing

Two side-specific contracts:

```text
UP ask worsens by at least 1c within 5s
DOWN ask worsens by at least 1c within 5s
```

For the selected candidate side, the adapter writes baseline and
event-evidence model probabilities separately. It resolves them only from the
first stored +5s observation whose actual elapsed time is from 5.0 through 6.0
seconds.

The adapter reads the contract campaign input-manifest hash, development
cutoff, ordered model features, model artifact hashes and clean recorder commit
from recorder-owned metadata. A source database created before those fields
were recorded is blocked rather than backfilled with present-day values.

The source campaign has no 15-second ask-worsening models. The UP and DOWN 15s
contracts therefore appear in readiness as `SOURCE_HEAD_NOT_IMPLEMENTED`.

### Binance Event Heads

Six forecast contracts are preserved separately:

```text
5s upper barrier before lower
5s either barrier touched
5s both barriers touched
15s upper barrier before lower
15s either barrier touched
15s both barriers touched
```

These are Binance spot-path targets even though the maker campaign consumes
them to select Binance USD-M paper routes. They are not relabeled as perp
returns or fills.

New maker candidates store per-row model dataset hash, training cutoff, source
protocol hash, feature schema, artifact hash, clean commit and dirty flag.
Current exact spot-path outcomes are not persisted by the maker recorder, so
the readiness state is `FORWARD_OUTCOME_COLLECTION_BLOCKED`. The forecasts may
be audited but cannot train a meta-model.

### Binance Paper Audit

The paper database is inspected for available signal, fill and trade rows, but
zero forecast rows are written:

- strategy confidence is not a calibrated direction probability;
- strategy score is not a magnitude distribution;
- maximum holding time is a limit, not a holding-time forecast;
- fills and trades are realized outcomes, not ex-ante forecasts;
- complete model dataset/cutoff/protocol provenance is unavailable.

The report states these blockers target by target.

## Recorder Provenance Changes

Future 1-hour fair-value observations record:

```text
protocol SHA-256
package code SHA-256
Git commit
dirty-worktree flag
```

Future repricing observations additionally record:

```text
contract input-manifest SHA-256
ordered contract feature-schema SHA-256
development training cutoff
UP and DOWN model artifact SHA-256
source run ID
```

Future event-bundle training embeds:

```text
content hash of every named raw event array used to derive features and labels
maximum calibration label timestamp as training cutoff
feature-schema SHA-256
Git commit and dirty flag
```

This trainer is not invoked by the adapter or report.

## Blocked Target Families

The catalog intentionally includes targets with no admissible source forecast:

| Family | Blocker |
|---|---|
| Polymarket 15s ask repricing | Source heads do not exist |
| Polymarket maker fill | No queue-authoritative probability head |
| Polymarket maker toxicity | No defensible post-fill specialist |
| Polymarket taker cost | Target defined; no ex-ante cost model |
| Binance maker fill/cost/return | Outcomes exist; specialist forecasts do not |
| Binance paper direction/magnitude/fill/cost/carry/return | Outputs are uncalibrated rules or realized outcomes with incomplete model provenance |

No model or horizon substitutes for a blocked target.

## Commands

Populate admissible rows and write readiness:

```powershell
python -m backend.research.forecast_adapters_v1.run_adapters
```

Populate and produce the hierarchical report:

```powershell
.\report_hierarchical_ensemble.bat
```

Outputs:

```text
data/research/model_forecast_adapters_v1/readiness.json
data/research/hierarchical_ensemble_v1/report/summary.json
data/research/hierarchical_ensemble_v1/report/adapter_readiness.csv
data/research/hierarchical_ensemble_v1/report/target_readiness.csv
```

## Executable Validation

```powershell
python -m backend.research.forecast_adapters_v1.selftest
python -m backend.research.hierarchical_ensemble_v1.selftest
```

The DuckDB integration test proves:

- real forecast/outcome tables are created;
- one-hour, repricing and Binance-event targets remain separate;
- reruns append no duplicates;
- a changed forecast under the same ID is rejected;
- training-cutoff leakage is rejected;
- settlement and repricing cannot share an ensemble;
- direct database tampering is detected;
- null execution economics remain null;
- every requested adapter appears in readiness, including zero-row targets.

Both tests run in Linux and Windows CI.

## Honest Status

This change makes aligned evidence collection possible. It does not prove that
any model is accurate, profitable, executable, or promotable. Meta-training
still requires enough resolved, aligned `OOF` or `FORWARD` candidates under one
identical target contract.
