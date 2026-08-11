"""Offline regression test for the production HTTP/WebSocket boundary."""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import os
import sys

os.environ.update({
    "BTC_DEPLOYMENT_ENV": "production",
    "BTC_REQUIRE_ADMIN_TOKEN": "1",
    "BTC_ADMIN_TOKEN": "a" * 32,
    "BTC_CONTROL_TOKEN": "b" * 32,
    "BTC_ALLOWED_ORIGINS": "https://btc.example",
    "BTC_EVIDENCE_MODE": "1",
    "BTC_REQUIRE_COMPLETE_TRADE": "1",
    "BTC_FREEZE_MODEL": "1",
    "BTC_RUN_STARTUP_BACKTEST": "0",
    "BTC_SERVE_FRONTEND": "0",
})

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402
from starlette.websockets import WebSocketDisconnect  # noqa: E402

import server  # noqa: E402


def main() -> int:
    ok = True

    def check(condition: bool, message: str) -> None:
        nonlocal ok
        print(f"  {'PASS' if condition else 'FAIL'}  {message}")
        ok = ok and condition

    client = TestClient(server.app)
    health = client.get("/healthz")
    check(health.status_code == 200 and health.json()["status"] == "alive",
          "liveness is a mechanical HTTP 200")
    ready = client.get("/readyz")
    check(ready.status_code == 503 and ready.json()["status"] == "not_ready",
          "readiness fails closed before boot/models/feeds are ready")
    check(client.get("/docs").status_code == 404,
          "interactive API docs are disabled in production")
    check(health.headers.get("x-content-type-options") == "nosniff",
          "security headers are attached")
    check("frame-ancestors 'none'" in health.headers.get("content-security-policy", ""),
          "CSP prevents framing")
    replay = client.post("/api/historical-replay/run")
    check(replay.status_code == 403, "admin mutation without token is refused")
    evidence = client.get("/api/evidence-health")
    evidence_payload = evidence.json()
    check(evidence.status_code == 200 and evidence_payload.get("capital_authority") is False,
          "performance-blind evidence health is readable and grants no capital authority")

    rejected = False
    try:
        with client.websocket_connect(
            "/ws", headers={"origin": "https://evil.example"}
        ):
            pass
    except Exception:
        rejected = True
    check(rejected, "unlisted browser WebSocket origin is refused")

    allowed = False
    try:
        with client.websocket_connect(
            "/ws", headers={"origin": "https://btc.example"}
        ) as websocket:
            allowed = websocket.receive_json().get("type") == "connected"
    except WebSocketDisconnect:
        allowed = False
    check(allowed, "explicitly allowed WebSocket origin connects")

    health_snapshot = server._system_health_snapshot()
    check(health_snapshot["live_execution"]["available"] is False,
          "real execution remains unavailable")
    print("PRODUCTION HTTP SURFACE", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
