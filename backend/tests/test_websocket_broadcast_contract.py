"""Concurrent producers may not overlap writes or be held hostage by a dead client."""
from __future__ import annotations

import asyncio
import os
import runpy as _bootstrap_runpy
import tempfile
from pathlib import Path

_bootstrap_runpy.run_path(str(Path(__file__).with_name("_bootstrap.py")))


class FakeSocket:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.active = 0
        self.maximum_active = 0
        self.messages: list[str] = []

    async def send_text(self, value: str) -> None:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            if self.fail:
                raise ConnectionError("closed")
            await asyncio.sleep(0.02)
            self.messages.append(value)
        finally:
            self.active -= 1


async def exercise(server) -> None:
    good = FakeSocket()
    dead = FakeSocket(fail=True)
    server.clients[:] = [good, dead]
    await asyncio.gather(
        server.broadcast({"sequence": 1}),
        server.broadcast({"sequence": 2}),
    )
    assert good.maximum_active == 1, good.maximum_active
    assert len(good.messages) == 2, good.messages
    assert dead not in server.clients
    assert good in server.clients
    server.clients.clear()


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        os.environ["BTC_DATA_DIR"] = raw
        os.environ["BTC_DB_PATH"] = str(Path(raw) / "analytics.duckdb")
        import server

        asyncio.run(exercise(server))
    print("websocket-broadcast-contract: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
