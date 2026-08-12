"""Health reports prove DuckDB query/schema health, not directory writability."""
from __future__ import annotations

import os
import runpy as _bootstrap_runpy
import tempfile
from pathlib import Path

_bootstrap_runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        os.environ["BTC_DATA_DIR"] = raw
        os.environ["BTC_DB_PATH"] = str(Path(raw) / "analytics.duckdb")
        import database

        before = database.health_status()
        assert before["healthy"] is False and "missing required tables" in before["reason"]
        database.init_db()
        try:
            after = database.health_status()
            assert after["healthy"] is True, after
        finally:
            database.close_db()
    print("database-health-contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
