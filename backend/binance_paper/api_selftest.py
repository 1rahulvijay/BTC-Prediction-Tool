"""Executing FastAPI contract tests for the isolated Binance paper router."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pathlib import Path
import tempfile

from .routes import configure_service, router
from .selftest import FakeFuturesClient, config
from .service import BinancePaperService
from .strategy_registry import StrategyRegistry


# Control endpoints are authenticated. This harness supplies the token on every request via the
# client's default headers, so the selftest exercises the REAL gated routes rather than an
# unprotected copy of them - and a regression that drops the gate still shows up here as a 401.
_TEST_TOKEN = "s" * 40


def app_for(service) -> TestClient:
    configure_service(service)
    app = FastAPI()
    app.include_router(router)
    import os

    os.environ["BTC_CONTROL_TOKEN"] = _TEST_TOKEN
    return TestClient(app, headers={"X-Control-Token": _TEST_TOKEN})


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
            # Compared against the registry rather than a literal. This assertion read `== 2`
            # and broke the moment the registry grew to four; a hardcoded count tests the
            # constant, not the API, and has to be edited every time the registry changes.
            assert len(strategy_body["items"]) == len(StrategyRegistry().all())
            assert {item["strategy_id"] for item in strategy_body["items"]} == {
                strategy.strategy_id for strategy in StrategyRegistry().all()
            }
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
                    "promotion_gate",
                }.issubset(item)
            assert client.get("/api/binance-paper/funding").json()["items"] == []
            assert client.post("/api/binance-paper/pause").json()["runtime_state"] == "PAUSED"
        enabled.shutdown()
    configure_service(None)
    print("  PASS  controls, invalid IDs, config clamps and metrics API")
    print("binance-paper-api: PASS")


if __name__ == "__main__":
    run()
