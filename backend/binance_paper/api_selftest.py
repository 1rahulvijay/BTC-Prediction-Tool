"""Executing FastAPI contract tests for the isolated Binance paper router."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path
import tempfile

from .routes import configure_service, router
from .selftest import FakeFuturesClient, config
from .service import BinancePaperService


def app_for(service) -> TestClient:
    configure_service(service)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def run() -> None:
    print("Binance paper API selftest")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        disabled = BinancePaperService(
            FakeFuturesClient(),
            config=config(Path(directory) / "disabled.duckdb", hard_enabled=False),
        )
        disabled.initialize()
        with app_for(disabled) as client:
            response = client.get("/api/binance-paper/status")
            assert response.status_code == 200
            body = response.json()
            assert body["paper_only"] is True
            assert body["real_orders_disabled"] is True
            assert body["runtime_state"] == "PAPER_ENGINE_DISABLED"
            assert client.post("/api/binance-paper/start").status_code == 409
            assert client.get("/api/binance-paper/accounts").status_code == 200
            strategy_body = client.get("/api/binance-paper/strategies").json()
            assert len(strategy_body["items"]) == 2
            assert all(item["inactive_reason"] == "Paper engine disabled" for item in strategy_body["items"])
        disabled.shutdown()
    print("  PASS  disabled-engine typed API")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        enabled = BinancePaperService(
            FakeFuturesClient(),
            config=config(Path(directory) / "enabled.duckdb", hard_enabled=True),
        )
        enabled.initialize()
        with app_for(enabled) as client:
            start = client.post("/api/binance-paper/start")
            assert start.status_code == 200 and start.json()["runtime_state"] == "RUNNING"
            patch = client.patch(
                "/api/binance-paper/strategies/trend_following",
                json={"enabled": False, "risk": {"max_position_notional_usd": 250}},
            )
            assert patch.status_code == 200
            assert patch.json()["enabled"] is False
            assert patch.json()["risk"]["max_position_notional_usd"] == 250.0
            assert (
                client.patch(
                    "/api/binance-paper/strategies/trend_following",
                    json={"risk": {"leverage": "not-a-number"}},
                ).status_code
                == 400
            )
            assert (
                client.patch(
                    "/api/binance-paper/strategies/not-real",
                    json={"enabled": True},
                ).status_code
                == 404
            )
            assert (
                client.post(
                    "/api/binance-paper/positions/not-real/close",
                    json={"confirm": False},
                ).status_code
                == 400
            )
            assert (
                client.post(
                    "/api/binance-paper/positions/not-real/close",
                    json={"confirm": True},
                ).status_code
                == 404
            )
            metrics = client.get("/api/binance-paper/metrics")
            assert metrics.status_code == 200
            assert metrics.json()["status"] == "INSUFFICIENT_DATA"
            assert {"sample_size", "n_days"}.issubset(metrics.json())
            for item in metrics.json()["strategies"]:
                assert {
                    "ev_lb_block_c",
                    "median_c",
                    "observation_days",
                    "measurability",
                    "lb_method",
                }.issubset(item)
            assert client.post("/api/binance-paper/pause").json()["runtime_state"] == "PAUSED"
        enabled.shutdown()
    configure_service(None)
    print("  PASS  controls, invalid IDs, config clamps and metrics API")
    print("binance-paper-api: PASS")


if __name__ == "__main__":
    run()
