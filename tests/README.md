# Tests

`tests/launchers/` contains manual Windows launchers for offline execution,
replay, and market-response tests. Run them from any working directory; each
launcher resolves the repository root first.

`tests/legacy/` contains retained manual probe scripts that used to live at the
repository root. They continue to write diagnostics to the repository `data/`
directory.

Examples:

```powershell
.\tests\launchers\run_polymarket_l2_execution_test.bat
.\tests\launchers\run_polymarket_market_response_test.bat
```

Automated package-level tests remain beside the Python packages they validate
under `backend/`. Moving those modules would change import paths and the CI
contract without improving runtime isolation.

Run the pytest-compatible suites with:

```powershell
python -m pytest -q
```

`pytest.ini` deliberately limits discovery to `tests/` and `backend/venues/`.
Many files named `test_*.py` elsewhere are standalone research or invariant
executables with their own `main()` entry points, not pytest modules. The
complete launch and evidence-integrity gate remains:

```powershell
python backend/run_ci_locally.py --all
```
