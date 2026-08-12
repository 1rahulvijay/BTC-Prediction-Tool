"""DuckDB retry sleeps must never block the live asyncio event loop."""
from __future__ import annotations

import asyncio
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

        original_connect = database.duckdb.connect
        original_sleep = database.time.sleep
        calls: list[int] = []
        sleeps: list[float] = []

        def fail(*_args, **_kwargs):
            calls.append(1)
            raise RuntimeError("locked")

        database.duckdb.connect = fail
        database.time.sleep = lambda delay: sleeps.append(delay)
        try:
            async def on_loop() -> None:
                try:
                    database._connect(retries=4, backoff=0.1)
                except RuntimeError:
                    pass

            asyncio.run(on_loop())
            assert len(calls) == 1, calls
            assert not sleeps, sleeps

            calls.clear()
            try:
                database._connect(retries=4, backoff=0.1)
            except RuntimeError:
                pass
            assert len(calls) == 4, calls
            assert sleeps == [0.1, 0.2, 0.30000000000000004, 0.4], sleeps
        finally:
            database.duckdb.connect = original_connect
            database.time.sleep = original_sleep

    print("database-async-retry-contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
