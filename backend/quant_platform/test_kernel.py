"""Deterministic tests for venue-neutral quant-platform invariants."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from .audit_ledger import AuditLedger
from .clocks import ManualClock
from .drift_monitor import brier_score, population_stability_index
from .event_bus import EventBus
from .events import EventHealth, MarketEvent
from .feature_contract import FeatureContract
from .feed_health import FeedHealthMonitor
from .model_registry import ModelBundleIdentity, ModelRegistry
from .orchestration import PlatformOrchestrator
from .portfolio_allocator import AllocationCandidate, allocate
from .risk_engine import OrderIntent, RiskAction, RiskEngine, RiskState
from .strategy_registry import StrategyDefinition, StrategyMode, StrategyRegistry


def _event(sequence: int, receive_ns: int = 2_000_000_000) -> MarketEvent:
    return MarketEvent(
        venue="BINANCE",
        instrument="BTCUSDT",
        event_type="BOOK",
        exchange_ts_ns=receive_ns - 1_000_000,
        receive_ts_ns=receive_ns,
        sequence_id=str(sequence),
        source_id="binance-book",
        recording_session_id="session-1",
        payload={"bid": 100.0, "ask": 101.0},
    )


def main() -> None:
    event = _event(1)
    assert event.latency_ms == 1.0
    assert len(event.sha256()) == 64

    seen: list[str] = []
    bus = EventBus()
    bus.subscribe("BOOK", lambda item: seen.append(item.sequence_id))
    assert bus.publish(event) == []
    assert seen == ["1"]

    clock = ManualClock(wall_ns=2_000_000_000)
    monitor = FeedHealthMonitor(stale_after_s=1.0, clock=clock)
    assert monitor.record(event) is EventHealth.HEALTHY
    assert monitor.record(_event(3, 2_100_000_000)) is EventHealth.GAP
    ok, reasons = monitor.healthy(["binance-book"])
    assert not ok and reasons == ["binance-book:gap"]
    assert monitor.record(_event(4, 2_200_000_000)) is EventHealth.HEALTHY
    clock.advance(2.0)
    assert monitor.snapshot()["binance-book"]["status"] == "STALE"

    contract = FeatureContract("test", "1", ("price", "volume"))
    assert contract.validate({"price": 1.0, "volume": 2.0}) == (True, [])
    assert not contract.validate({"price": float("nan")})[0]

    models = ModelRegistry()
    identity = ModelBundleIdentity(
        "direction",
        "bundle-1",
        "a" * 64,
        1.0,
        "b" * 64,
        "c" * 64,
        "d" * 64,
        "e" * 64,
    )
    models.register(identity)
    models.activate("direction", "bundle-1")
    assert models.active("direction") == identity

    strategies = StrategyRegistry()
    strategy = StrategyDefinition(
        "liq-continuation-v1",
        "liquidation_continuation",
        "BINANCE",
        "BTCUSDT",
        "d" * 64,
        "f" * 64,
        StrategyMode.SHADOW,
    )
    strategies.register(strategy)
    assert strategies.get(strategy.strategy_id) == strategy

    engine = RiskEngine()
    intent = OrderIntent("BINANCE", "BTCUSDT", strategy.strategy_id, 100.0, 1.0)
    healthy = RiskState(feed_age_ms=10.0, sequence_healthy=True)
    assert engine.evaluate(intent, healthy).action is RiskAction.ALLOW
    blocked = RiskState(kill_switch=True, feed_age_ms=10.0, sequence_healthy=True)
    assert "kill_switch" in engine.evaluate(intent, blocked).reasons

    allocations = allocate(
        [
            AllocationCandidate(
                strategy.strategy_id, 0.02, 100.0, 0.05, 0.1, 0.9, 0.9
            ),
            AllocationCandidate("no-edge", -0.1, 100.0, 0.1, 0.0, 1.0, 1.0),
        ],
        capital=10_000.0,
    )
    assert set(allocations) == {strategy.strategy_id}
    assert 0 < allocations[strategy.strategy_id] <= 50.0

    assert brier_score([0.8, 0.2], [1, 0]) == 0.039999999999999994
    assert population_stability_index(range(20), range(1, 21), bins=4) >= 0

    orchestrator = PlatformOrchestrator()
    orchestrator.register("feed")
    orchestrator.update("feed", True, "ok")
    orchestrator.set_kill_switch(False, "paper_only")
    assert orchestrator.ready() == (True, [])

    with TemporaryDirectory() as tmp:
        ledger = AuditLedger(Path(tmp) / "audit.duckdb")
        digest = ledger.append("e1", "RISK", "test", {"allow": True}, 1)
        assert ledger.append("e1", "RISK", "test", {"allow": True}, 1) == digest
        try:
            ledger.append("e1", "RISK", "test", {"allow": False}, 1)
            raise AssertionError("immutable collision was accepted")
        except ValueError:
            pass
        assert ledger.verify() == (True, [])

    print("quant-platform kernel: ALL PASS")


if __name__ == "__main__":
    main()
