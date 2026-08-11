# Repository Test And Research Layout

Date: 2026-07-30
Updated: 2026-08-11

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
start_binance_l2_recorder.bat
start.bat
start_instant.bat
start_microstructure_recorder.bat
start_production.bat
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
| `backend/tests/` | Backend regression, invariant, and direct-script tests |
| `backend/tests/run_ci_locally.py` | Local runner for every GitHub workflow gate |
| `backend/research/standalone/` | Offline backend probes, audits, scorecards, bakeoffs, and analyses |

Package-specific tests that are already inside a package, such as
`backend/binance_paper/`, `backend/trade_forecast/`, and `backend/venues/`, stay
beside that package. The former top-level `backend/test_*.py` files now live in
`backend/tests/`, with a bootstrap that preserves their direct-script imports.

Research helpers imported by serving or startup training remain in `backend/`.
For example, `edge_probe.py`, `model_bakeoff.py`, `probe_fade_entry_exit.py`,
`probe_roundtrip_and_timing.py`, and `probe_ta_matrix.py` are shared modules, not
standalone entry points. Moving them would make core startup depend on the
standalone research namespace.

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

The Python bootstraps in `backend/tests/` and
`backend/research/standalone/` add the unchanged backend and repository roots to
`sys.path`. Direct invocation and pytest therefore resolve the same application
modules and data paths as before the move.

## Validation Contract

The layout test fails when:

- an unapproved Batch file appears at the application root;
- a moved launcher lacks the repository-root bootstrap;
- a moved launcher retains a second location-relative `%~dp0` path;
- a launcher references a missing repository Python script or module;
- a launcher contains a forbidden control byte;
- the old ad-hoc root probe files reappear.
- a top-level `backend/test_*.py` file reappears;
- a standalone analysis or HF audit reappears at the backend root;
- either organized backend directory is empty.

The test runs in both Linux and Windows CI jobs. Normal application startup is
still validated separately by `backend/tests/test_launcher_integrity.py` and
`BTC_VALIDATE_STARTUP=1`.

## Commands

Examples from the repository root:

```powershell
.\research\launchers\run_profit_campaign_v1.bat
.\research\launchers\run_180d_sequence_only.bat
.\tests\launchers\run_polymarket_l2_execution_test.bat
python tests\test_repository_layout.py
python backend\tests\test_launcher_integrity.py
python backend\tests\run_ci_locally.py --all
python backend\research\standalone\probe_direction_tilt.py --selftest
```
