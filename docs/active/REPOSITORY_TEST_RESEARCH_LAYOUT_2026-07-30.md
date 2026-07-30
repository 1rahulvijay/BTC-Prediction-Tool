# Repository Test And Research Layout

Date: 2026-07-30

## Purpose

Keep the production application root small and operational while preserving every
standalone test, experiment, research report, and research-training launcher.
This is an organization-only change. It does not change prediction, paper-trade,
execution, model-training, recorder, or application-startup logic.

## Root Application Controls

Only operational controls remain at the repository root:

```text
backfill.bat
frontend.bat
run_backend.bat
run_polymarket_l2_recorder.bat
start.bat
start_instant.bat
start_microstructure_recorder.bat
start_recorder.bat
```

`run_polymarket_l2_recorder.bat` remains at the root because it is a continuous
data-collection control, not an offline test.

## New Locations

| Location | Contents |
|---|---|
| `research/launchers/` | Offline experiments, model bakeoffs, research training, reports, and shadow evaluators |
| `research/tools/` | Manual research probes |
| `tests/launchers/` | Manual offline test/replay launchers |
| `tests/legacy/` | Retained ad-hoc test probes |
| `tests/test_repository_layout.py` | Automated layout, path, target, and control-byte invariant |

Python package tests remain under `backend/` beside the modules they validate.
Moving them would change imports and CI module paths without isolating runtime
behavior any further.

## Path Preservation

Every moved Batch launcher computes the repository root from its own location:

```bat
for %%I in ("%~dp0..\..") do set "PROJECT_ROOT=%%~fI\"
cd /d "%PROJECT_ROOT%"
```

Therefore existing relative references still resolve to the same:

- `backend/` source tree
- `data/` databases and research outputs
- model and artifact directories
- Python module namespace
- `start.bat` for explicit overnight train-then-start workflows

The moved probe scripts explicitly resolve `data/` from the repository root, so
the move does not split or reset their output history.

## Validation Contract

The layout test fails when:

- an unapproved Batch file appears at the application root;
- a moved launcher lacks the repository-root bootstrap;
- a moved launcher retains a second location-relative `%~dp0` path;
- a launcher references a missing repository Python script or module;
- a launcher contains a forbidden control byte;
- the old ad-hoc root probe files reappear.

The test runs in both Linux and Windows CI jobs. Normal application startup is
still validated separately by `backend/test_launcher_integrity.py` and
`BTC_VALIDATE_STARTUP=1`.

## Commands

Examples from the repository root:

```powershell
.\research\launchers\run_profit_campaign_v1.bat
.\research\launchers\run_180d_sequence_only.bat
.\tests\launchers\run_polymarket_l2_execution_test.bat
python tests\test_repository_layout.py
```
