"""Deterministic checks for feed content health and parse quarantine."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import data_ingestion


def main() -> int:
    with TemporaryDirectory() as tmp:
        data_ingestion._QUARANTINE_DIR = Path(tmp)
        health = data_ingestion._ProtocolHealth("test_public_feed")

        initial = health.snapshot(stale_after_ms=1_000)
        assert "socket_disconnected" in initial["blockers"]
        assert "valid_content_stale" in initial["blockers"]

        health.connected = True
        health.message()
        health.valid()
        good = health.snapshot(stale_after_ms=1_000)
        assert good["healthy"] is True
        assert good["recent_parse_error_rate"] == 0.0

        for _ in range(2):
            health.message()
            health.error("book", "{bad-json", ValueError("bad public frame"))
        failed = health.snapshot(stale_after_ms=1_000)
        assert failed["healthy"] is False
        assert "parse_error_rate" in failed["blockers"]
        assert failed["errors_by_stream"] == {"book": 2}

        rows = [
            json.loads(line)
            for line in (Path(tmp) / "test_public_feed.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert len(rows) == 2
        assert all(row["source"] == "test_public_feed" for row in rows)
        assert all(row["stream"] == "book" for row in rows)

    spot = data_ingestion.BinanceWebSocketClient().health_snapshot()
    futures = data_ingestion.BinanceFuturesWebSocketClient().health_snapshot()
    coinbase = data_ingestion.CoinbaseWebSocketClient().health_snapshot()
    assert spot["source"] == "binance_spot_ws"
    assert futures["source"] == "binance_futures_ws"
    assert coinbase["source"] == "coinbase_ws"
    print("FEED PROTOCOL HEALTH PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
