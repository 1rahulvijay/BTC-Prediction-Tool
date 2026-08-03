"""Executing Phase-1 integrity tests. No network, models, or production databases."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import time

import duckdb

from ..data_ingestion import BinanceFuturesWebSocketClient
from .config import DEFAULT_DB_PATH, EngineConfig, StrategyRiskConfig
from .fill_simulator import BinancePaperFillSimulator
from .governor import (
    CapitalPreservationGovernor,
    GovernorAccountState,
    GovernorMode,
)
from .metrics import _day_block_lower_bound, _promotion_gate
from .risk_engine import BinancePaperRiskEngine
from .schemas import Action, DataQuality, MarketSnapshot, PositionSide
from .service import BinancePaperService
from .strategies import TrendFollowingStrategy


class FakeFuturesClient:
    def __init__(self):
        self.last_book_receive_ts_ms = None
        self.book_message_count = 0
        self.last_agg_trade_receive_ts_ms = None
        self.agg_trade_message_count = 0
        self.last_perp_bar = None

    def health_snapshot(self, now_ms=None):
        now = int(now_ms if now_ms is not None else time.time() * 1000)

        def age(value):
            return max(0, now - value) if value is not None else None

        return {
            "last_book_receive_ts_ms": self.last_book_receive_ts_ms,
            "book_message_count": self.book_message_count,
            "book_age_ms": age(self.last_book_receive_ts_ms),
            "last_agg_trade_receive_ts_ms": self.last_agg_trade_receive_ts_ms,
            "agg_trade_message_count": self.agg_trade_message_count,
            "agg_trade_age_ms": age(self.last_agg_trade_receive_ts_ms),
            "last_completed_perp_cvd_bar_ts_ms": (
                self.last_perp_bar["ts"] if self.last_perp_bar else None
            ),
        }


def config(path: Path, *, hard_enabled: bool = True, latency_ms: int = 500):
    return EngineConfig(
        hard_enabled=hard_enabled,
        db_path=path,
        starting_cash_usd=10_000.0,
        fee_rate_bps=5.0,
        slippage_bps=1.0,
        latency_ms=latency_ms,
        quote_stale_ms=2_000,
        evaluation_interval_ms=1_000,
        sample_interval_ms=1_000,
    )


def feed(
    service: BinancePaperService,
    client: FakeFuturesClient,
    timestamp_ms: int,
    bid: float,
    ask: float,
    *,
    bid_size: float = 10.0,
    ask_size: float = 10.0,
    agg_increment: int = 30,
):
    client.last_book_receive_ts_ms = timestamp_ms
    client.book_message_count += 1
    if agg_increment:
        client.last_agg_trade_receive_ts_ms = timestamp_ms
        client.agg_trade_message_count += agg_increment
    service.on_book(
        {
            "symbol": "BTCUSDT",
            "best_bid": bid,
            "best_ask": ask,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "event_ts_ms": timestamp_ms - 5,
            "received_at_ms": timestamp_ms,
            "update_id": client.book_message_count,
        }
    )
    return service.latest_snapshot


def manual_decision(service, strategy_id, snapshot, side, suffix=""):
    strategy = service.registry.get(strategy_id)
    sign = 1 if side is PositionSide.LONG else -1
    action = Action.OPEN_LONG if side is PositionSide.LONG else Action.OPEN_SHORT
    decision = strategy._decision(
        snapshot,
        action=action,
        side=side,
        score=float(sign),
        confidence=0.75,
        requested_notional_usd=500.0,
        stop_price=snapshot.mark_price - sign * 100.0,
        take_profit_price=snapshot.mark_price + sign * 150.0,
        maximum_holding_seconds=300,
        features={"manual_test": suffix or side.value, "mark": snapshot.mark_price},
        reason_codes=("selftest",),
    )
    return decision


def queue_manual(service, decision, snapshot):
    assert service.persistence.record_signal(decision)
    service._queue_entry(decision, snapshot, signal_already_seen=False)


def test_default_off() -> None:
    with tempfile.TemporaryDirectory() as directory:
        client = FakeFuturesClient()
        service = BinancePaperService(
            client, config=config(Path(directory) / "paper.duckdb", hard_enabled=False)
        )
        service.initialize()
        assert service.status()["runtime_state"] == "PAPER_ENGINE_DISABLED"
        try:
            service.start_engine()
        except PermissionError:
            pass
        else:
            raise AssertionError("disabled engine started")
        assert service.orders() == []
        service.shutdown()
    print("  PASS  engine defaults off and cannot create orders")


def test_schema_v1_migration() -> None:
    with tempfile.TemporaryDirectory() as directory:
        db_path = Path(directory) / "paper.duckdb"
        connection = duckdb.connect(str(db_path))
        connection.execute(
            """
            CREATE TABLE binance_paper_schema_version (
                version INTEGER PRIMARY KEY,
                applied_at_ms BIGINT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO binance_paper_schema_version VALUES (1, ?)",
            (int(time.time() * 1000),),
        )
        connection.close()
        service = BinancePaperService(
            FakeFuturesClient(),
            config=config(db_path, hard_enabled=False),
        )
        service.initialize()
        assert "binance_paper_funding_events" in service.persistence.table_names()
        version = service.persistence._conn.execute(
            "SELECT MAX(version) FROM binance_paper_schema_version"
        ).fetchone()[0]
        assert version == 3
        service.shutdown()
    print("  PASS  schema v1 migrates transactionally to lifecycle-aware v3")


def test_governor_modes_and_fail_closed_inputs() -> None:
    now = int(time.time() * 1000)
    snapshot = MarketSnapshot(
        "BTCUSDT",
        now - 5,
        now,
        100.0,
        99.9,
        100.1,
        10.0,
        10.0,
        0.2,
        20.0,
        0,
        DataQuality.HEALTHY,
        1,
        None,
        None,
        0,
        100,
        100,
        None,
    )
    governor = CapitalPreservationGovernor(latency_ms=500, quote_stale_ms=2_000)
    risk = StrategyRiskConfig()

    def state(*, equity=10_000.0, daily=0.0, weekly=0.0):
        return GovernorAccountState(
            strategy_id="test",
            starting_cash_usd=10_000.0,
            equity_usd=equity,
            peak_equity_usd=10_000.0,
            daily_net_pnl_usd=daily,
            weekly_net_pnl_usd=weekly,
            risk=risk,
        )

    normal = governor.evaluate(snapshot=snapshot, accounts=(state(),), now_ms=now)
    assert normal.mode is GovernorMode.NORMAL and normal.can_open
    reduced = governor.evaluate(
        snapshot=snapshot,
        accounts=(state(equity=9_500.0),),
        now_ms=now,
    )
    assert reduced.mode is GovernorMode.REDUCED_SIZE
    assert reduced.size_multiplier == 0.5
    close_only = governor.evaluate(
        snapshot=snapshot,
        accounts=(state(equity=9_000.0),),
        now_ms=now,
    )
    assert close_only.mode is GovernorMode.CLOSE_ONLY
    emergency = governor.evaluate(
        snapshot=snapshot,
        accounts=(state(equity=8_400.0),),
        now_ms=now,
    )
    assert emergency.mode is GovernorMode.EMERGENCY_FLATTEN
    assert emergency.must_flatten and not emergency.can_open
    stale = governor.evaluate(
        snapshot=replace(
            snapshot,
            feed_health=DataQuality.STALE,
            feed_age_ms=10_000,
        ),
        accounts=(state(),),
        now_ms=now,
    )
    assert stale.mode is GovernorMode.NO_NEW_ENTRIES
    corrupt = governor.evaluate(
        snapshot=snapshot,
        accounts=(state(equity=float("nan")),),
        now_ms=now,
    )
    assert corrupt.mode is GovernorMode.EMERGENCY_FLATTEN
    overdue = governor.evaluate(
        snapshot=snapshot,
        accounts=(state(),),
        pending_ages_ms=(governor.pending_unknown_ms + 1,),
        now_ms=now,
    )
    assert overdue.mode is GovernorMode.NO_NEW_ENTRIES
    print("  PASS  capital governor modes, stale feed and corrupt inputs fail closed")


def test_signal_expiry_entry_bound_and_order_transitions() -> None:
    with tempfile.TemporaryDirectory() as directory:
        client = FakeFuturesClient()
        service = BinancePaperService(
            client,
            config=config(Path(directory) / "paper.duckdb", latency_ms=500),
        )
        service.initialize()
        service.start_engine()
        base = int(time.time() * 1000)
        snapshot = feed(service, client, base, 99_999.0, 100_001.0)

        expiring = replace(
            manual_decision(
                service,
                "trend_following",
                snapshot,
                PositionSide.LONG,
                "expiry",
            ),
            valid_until_ms=base + 100,
        )
        queue_manual(service, expiring, snapshot)
        feed(service, client, base + 500, 99_999.0, 100_001.0)
        assert service.persistence.open_positions("trend_following") == []
        expiry_event = service.orders(1)[0]
        assert expiry_event["status"] == "CANCELLED"
        assert expiry_event["rejection_reason"] == "signal_expired_before_arrival"

        snapshot = feed(service, client, base + 1_000, 99_999.0, 100_001.0)
        bounded = replace(
            manual_decision(
                service,
                "breakout",
                snapshot,
                PositionSide.LONG,
                "price-bound",
            ),
            valid_until_ms=base + 5_000,
            maximum_entry_price=100_012.0,
        )
        queue_manual(service, bounded, snapshot)
        feed(service, client, base + 1_500, 100_099.0, 100_101.0)
        assert service.persistence.open_positions("breakout") == []
        bound_event = service.orders(1)[0]
        assert bound_event["status"] == "CANCELLED"
        assert (
            bound_event["rejection_reason"]
            == "maximum_entry_price_breached_before_arrival"
        )

        order_id = "transition-test"
        common = {
            "order_id": order_id,
            "signal_id": "transition-signal",
            "strategy_id": "trend_following",
            "operation": "ENTRY",
            "side": "LONG",
            "requested_quantity": 1.0,
            "requested_notional_usd": 100.0,
            "decision_ts_ms": base,
            "simulated_send_ts_ms": base,
            "simulated_arrival_ts_ms": base + 1,
        }
        service.persistence.append_order_event(status="PENDING", **common)
        service.persistence.append_order_event(status="FILLED", **common)
        try:
            service.persistence.append_order_event(status="PENDING", **common)
        except ValueError as exc:
            assert "invalid paper order transition" in str(exc)
        else:
            raise AssertionError("terminal paper order returned to PENDING")

        saved = service.persistence._conn.execute(
            """
            SELECT valid_until_ms, maximum_entry_price, probability_calibrated,
                   uncertainty_status
            FROM binance_paper_signals
            WHERE signal_id = ?
            """,
            (bounded.signal_id,),
        ).fetchone()
        assert saved == (
            bounded.valid_until_ms,
            bounded.maximum_entry_price,
            False,
            "UNMEASURED",
        )
        assert service.status()["capital_governor"]["mode"] == "NORMAL"
        service.shutdown()
    print("  PASS  signal expiry, entry limit, order transitions and audit metadata")


def test_governor_emergency_flatten_integration() -> None:
    with tempfile.TemporaryDirectory() as directory:
        client = FakeFuturesClient()
        service = BinancePaperService(
            client,
            config=config(Path(directory) / "paper.duckdb", latency_ms=0),
        )
        service.initialize()
        service.start_engine()
        base = int(time.time() * 1000)
        snapshot = feed(service, client, base, 99_999.0, 100_001.0)
        decision = manual_decision(
            service,
            "trend_following",
            snapshot,
            PositionSide.LONG,
            "governor-flatten",
        )
        queue_manual(service, decision, snapshot)
        feed(service, client, base + 1, 99_999.0, 100_001.0)
        position = service.persistence.open_positions("trend_following")[0]

        service.persistence._conn.execute(
            """
            UPDATE binance_paper_accounts
            SET peak_equity_usd = 20000
            WHERE strategy_id = 'trend_following'
            """
        )
        snapshot = feed(service, client, base + 1_001, 99_999.0, 100_001.0)
        governor = service._governor_decision(snapshot)
        assert governor.mode is GovernorMode.EMERGENCY_FLATTEN
        pending_exits = [
            intent
            for intent in service._pending.values()
            if intent.operation == "EXIT"
            and intent.position_id == position["position_id"]
        ]
        assert len(pending_exits) == 1

        blocked = manual_decision(
            service,
            "breakout",
            snapshot,
            PositionSide.SHORT,
            "governor-block-entry",
        )
        assert service.persistence.record_signal(blocked)
        service._queue_entry(blocked, snapshot, signal_already_seen=False)
        blocked_event = service.orders(1)[0]
        assert blocked_event["status"] == "RISK_BLOCKED"
        assert "capital_governor" in blocked_event["rejection_reason"]

        feed(service, client, base + 1_002, 99_999.0, 100_001.0)
        assert service.persistence.open_positions("trend_following") == []
        trade = service.persistence.trades(1, "trend_following")[0]
        assert trade["exit_reason"] == "GOVERNOR_EMERGENCY_FLATTEN"
        service.shutdown()
    print("  PASS  governor blocks entries and emergency-flattens paper positions")


def test_long_short_accounting_and_isolation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        client = FakeFuturesClient()
        service = BinancePaperService(client, config=config(Path(directory) / "paper.duckdb"))
        service.initialize()
        service.start_engine()
        base = int(time.time() * 1000)
        snapshot = feed(service, client, base, 99_999.0, 100_001.0)
        breakout_before = service.persistence.account("breakout")
        long_decision = manual_decision(
            service, "trend_following", snapshot, PositionSide.LONG, "long"
        )
        queue_manual(service, long_decision, snapshot)
        feed(service, client, base + 500, 99_999.0, 100_001.0)
        position = service.persistence.open_positions("trend_following")[0]
        assert position["side"] == "LONG"
        assert float(position["entry_price"]) > 100_001.0
        feed(service, client, base + 1_000, 100_199.0, 100_201.0)
        assert service.persistence.account("trend_following")["unrealized_pnl_usd"] > 0
        service.close_position(position["position_id"], confirm=True)
        feed(service, client, base + 1_500, 100_199.0, 100_201.0)
        long_trade = service.persistence.trades(1, "trend_following")[0]
        assert long_trade["gross_pnl_usd"] > 0 and long_trade["net_pnl_usd"] > 0
        assert long_trade["entry_fee_usd"] > 0 and long_trade["exit_fee_usd"] > 0
        assert float(long_trade["exit_price"]) < 100_199.0
        breakout_after = service.persistence.account("breakout")
        assert breakout_after == breakout_before

        snapshot = feed(service, client, base + 2_000, 100_099.0, 100_101.0)
        short_decision = manual_decision(
            service, "breakout", snapshot, PositionSide.SHORT, "short"
        )
        queue_manual(service, short_decision, snapshot)
        feed(service, client, base + 2_500, 100_099.0, 100_101.0)
        short_position = service.persistence.open_positions("breakout")[0]
        assert float(short_position["entry_price"]) < 100_099.0
        feed(service, client, base + 3_000, 99_899.0, 99_901.0)
        assert service.persistence.account("breakout")["unrealized_pnl_usd"] > 0
        service.close_position(short_position["position_id"], confirm=True)
        feed(service, client, base + 3_500, 99_899.0, 99_901.0)
        short_trade = service.persistence.trades(1, "breakout")[0]
        assert short_trade["gross_pnl_usd"] > 0 and short_trade["net_pnl_usd"] > 0
        assert float(short_trade["exit_price"]) > 99_901.0
        service.shutdown()
    print("  PASS  LONG/SHORT accounting, fees, slippage and strategy isolation")


def test_observed_funding_accounting() -> None:
    with tempfile.TemporaryDirectory() as directory:
        client = FakeFuturesClient()
        derivatives = {"funding_rate": None}
        service = BinancePaperService(
            client,
            lambda: derivatives,
            config=config(Path(directory) / "paper.duckdb", latency_ms=0),
        )
        service.initialize()
        service.start_engine()
        base = int(time.time() * 1000)
        snapshot = feed(service, client, base, 99_999.0, 100_001.0)
        decision = manual_decision(
            service, "trend_following", snapshot, PositionSide.LONG, "funding"
        )
        queue_manual(service, decision, snapshot)
        feed(service, client, base + 1, 99_999.0, 100_001.0)
        position = service.persistence.open_positions("trend_following")[0]

        derivatives["funding_rate"] = {"rate": 0.001, "time": base + 2}
        feed(service, client, base + 3, 99_999.0, 100_001.0)
        funding_events = service.funding_events()
        assert len(funding_events) == 1
        funding = float(funding_events[0]["funding_usd"])
        assert funding < 0
        assert service.persistence.account("trend_following")["funding_usd"] == funding

        feed(service, client, base + 4, 99_999.0, 100_001.0)
        assert len(service.funding_events()) == 1
        assert service.persistence.account("trend_following")["funding_usd"] == funding

        service.close_position(position["position_id"], confirm=True)
        feed(service, client, base + 5, 99_999.0, 100_001.0)
        trade = service.trades(1)[0]
        assert abs(float(trade["funding_usd"]) - funding) < 1e-9
        expected_net = (
            float(trade["gross_pnl_usd"])
            - float(trade["entry_fee_usd"])
            - float(trade["exit_fee_usd"])
            + funding
        )
        assert abs(float(trade["net_pnl_usd"]) - expected_net) < 1e-9

        snapshot = feed(service, client, base + 6, 99_999.0, 100_001.0)
        short_decision = manual_decision(
            service, "breakout", snapshot, PositionSide.SHORT, "short-funding"
        )
        queue_manual(service, short_decision, snapshot)
        feed(service, client, base + 7, 99_999.0, 100_001.0)
        derivatives["funding_rate"] = {"rate": 0.001, "time": base + 8}
        feed(service, client, base + 9, 99_999.0, 100_001.0)
        short_funding = [
            row
            for row in service.funding_events()
            if row["strategy_id"] == "breakout"
        ]
        assert len(short_funding) == 1
        assert float(short_funding[0]["funding_usd"]) > 0
        service.shutdown()
    print("  PASS  settled funding is signed, position-linked and idempotent")


def test_opposing_signal_order() -> None:
    with tempfile.TemporaryDirectory() as directory:
        client = FakeFuturesClient()
        service = BinancePaperService(
            client, config=config(Path(directory) / "paper.duckdb", latency_ms=0)
        )
        service.initialize()
        service.registry.update(
            service.persistence,
            "trend_following",
            risk_patch={"cooldown_seconds": 0},
        )
        service.start_engine()
        base = int(time.time() * 1000)
        snapshot = feed(service, client, base, 100_000.0, 100_002.0)
        long_decision = manual_decision(
            service, "trend_following", snapshot, PositionSide.LONG, "reversal-open"
        )
        queue_manual(service, long_decision, snapshot)
        feed(service, client, base + 1, 100_000.0, 100_002.0)
        assert service.persistence.open_positions("trend_following")[0]["side"] == "LONG"

        short_decision = manual_decision(
            service,
            "trend_following",
            feed(service, client, base + 2, 100_000.0, 100_002.0),
            PositionSide.SHORT,
            "reversal-close",
        )
        strategy = service.registry.get("trend_following")
        original = strategy.decide
        strategy.decide = lambda _snapshot: short_decision
        try:
            service._evaluate(service.latest_snapshot)
        finally:
            strategy.decide = original
        feed(service, client, base + 3, 100_000.0, 100_002.0)
        feed(service, client, base + 4, 100_000.0, 100_002.0)
        position = service.persistence.open_positions("trend_following")[0]
        assert position["side"] == "SHORT"
        events = [
            row
            for row in reversed(service.persistence.latest_orders(20))
            if row["strategy_id"] == "trend_following" and row["status"] == "FILLED"
        ]
        operations = [row["operation"] for row in events]
        assert operations[-2:] == ["EXIT", "ENTRY"], operations
        service.shutdown()
    print("  PASS  opposing signal closes before opening the opposite side")


def test_actual_strategies_end_to_end() -> None:
    with tempfile.TemporaryDirectory() as directory:
        client = FakeFuturesClient()
        service = BinancePaperService(
            client,
            config=config(Path(directory) / "paper.duckdb", latency_ms=0),
        )
        service.initialize()
        service.start_engine()
        base = int(time.time() * 1000)
        snapshot = None
        for index in range(35):
            bid = 99_999.0 + index * 10.0
            snapshot = feed(
                service,
                client,
                base + index * 1_000,
                bid,
                bid + 2.0,
                agg_increment=30,
            )
        assert snapshot is not None
        trend = service.registry.get("trend_following").decide(snapshot)
        breakout = service.registry.get("breakout").decide(snapshot)
        for decision in (trend, breakout):
            assert decision.action is Action.OPEN_LONG
            assert decision.side is PositionSide.LONG
            assert decision.stop_price < snapshot.mark_price
            assert decision.take_profit_price > snapshot.mark_price
            assert decision.missing_inputs == ()

        queue_manual(service, trend, snapshot)
        fill_snapshot = feed(
            service,
            client,
            base + 35_000,
            100_349.0,
            100_351.0,
        )
        position = service.persistence.open_positions("trend_following")[0]
        assert position["side"] == "LONG"
        service.close_position(position["position_id"], confirm=True)
        feed(
            service,
            client,
            base + 36_000,
            100_349.0,
            100_351.0,
        )
        assert service.persistence.open_positions("trend_following") == []
        assert service.metrics()["sample_size"] == 1
        assert fill_snapshot.feature_availability["perpetual_trade_intensity"] is True
        service.shutdown()
    print("  PASS  real Trend/Breakout decisions and end-to-end paper cycle")


def test_no_lookahead_stale_missing_and_liquidity() -> None:
    client = FakeFuturesClient()
    cfg = config(Path("unused.duckdb"))
    simulator = BinancePaperFillSimulator(cfg)
    base = int(time.time() * 1000)
    healthy = MarketSnapshot(
        "BTCUSDT",
        base - 5,
        base,
        100.0,
        99.9,
        100.1,
        10.0,
        1.0,
        0.2,
        20.0,
        0,
        DataQuality.HEALTHY,
        1,
        None,
        None,
        None,
        0,
        None,
        None,
    )
    early = simulator.simulate(
        signal_id="s",
        order_id="o",
        strategy_id="trend_following",
        side=PositionSide.LONG,
        operation="ENTRY",
        requested_quantity=2.0,
        decision_ts_ms=base,
        snapshot=healthy,
    )
    assert early.rejection_reason == "latency_not_reached"
    eligible = replace(
        healthy,
        event_ts_ms=base + 495,
        received_at_ms=base + 500,
    )
    partial = simulator.simulate(
        signal_id="s",
        order_id="o2",
        strategy_id="trend_following",
        side=PositionSide.LONG,
        operation="ENTRY",
        requested_quantity=2.0,
        decision_ts_ms=base,
        snapshot=eligible,
    )
    assert partial.filled_quantity == 1.0 and partial.unfilled_quantity == 1.0

    with tempfile.TemporaryDirectory() as directory:
        service = BinancePaperService(
            client, config=config(Path(directory) / "paper.duckdb")
        )
        service.initialize()
        service.start_engine()
        snapshot = feed(service, client, base, 99.9, 100.1, agg_increment=0)
        breakout = service.registry.get("breakout").decide(snapshot)
        assert breakout.action is Action.NO_DATA
        assert "perpetual_trade_intensity" in breakout.missing_inputs
        for index in range(1, 23):
            snapshot = feed(
                service,
                client,
                base + index * 1_000,
                99.9 + index * 0.01,
                100.1 + index * 0.01,
                agg_increment=0,
            )
        trend_available = service.registry.get("trend_following").decide(snapshot)
        breakout_unavailable = service.registry.get("breakout").decide(snapshot)
        assert trend_available.action is not Action.NO_DATA
        assert trend_available.missing_inputs == ()
        assert breakout_unavailable.action is Action.NO_DATA
        assert "perpetual_trade_intensity" in breakout_unavailable.missing_inputs
        stale = replace(snapshot, feed_age_ms=10_000, feed_health=DataQuality.STALE)
        trend = manual_decision(
            service, "trend_following", stale, PositionSide.LONG, "stale"
        )
        account = service.persistence.account("trend_following")
        risk = service.risk_engine.evaluate_entry(
            decision=trend,
            snapshot=stale,
            account=account,
            open_position=None,
            risk=service.registry.get("trend_following").risk,
            persistence=service.persistence,
            runtime_active=True,
            strategy_enabled=True,
            signal_already_seen=False,
            now_ms=base,
        )
        assert not risk.approved and "stale_market_data" in risk.reason_codes
        service.shutdown()
    print("  PASS  latency causality, stale/missing inputs and partial liquidity")


def test_feed_contract_and_pending_entry_cancellation() -> None:
    now = int(time.time() * 1000)
    parsed = BinanceFuturesWebSocketClient._parse_book_ticker(
        {
            "s": "BTCUSDT",
            "b": "99999.0",
            "B": "1.25",
            "a": "100001.0",
            "A": "2.50",
            "E": now - 5,
            "u": 7,
        },
        now,
    )
    assert parsed["best_bid"] == 99_999.0
    assert parsed["best_ask"] == 100_001.0
    try:
        BinanceFuturesWebSocketClient._parse_book_ticker(
            {
                "s": "BTCUSDT",
                "b": "99999.0",
                "B": "1.25",
                "a": "100001.0",
                "A": "2.50",
                "E": 0,
            },
            now,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("bookTicker without an exchange timestamp was accepted")

    with tempfile.TemporaryDirectory() as directory:
        client = FakeFuturesClient()
        service = BinancePaperService(
            client,
            config=config(Path(directory) / "paper.duckdb", latency_ms=500),
        )
        service.initialize()
        service.start_engine()
        snapshot = feed(service, client, now, 99_999.0, 100_001.0)
        decision = manual_decision(
            service, "trend_following", snapshot, PositionSide.LONG, "pause"
        )
        queue_manual(service, decision, snapshot)
        assert service.status()["pending_order_count"] == 1
        service.pause_engine()
        assert service.status()["pending_order_count"] == 0
        feed(service, client, now + 1_000, 99_999.0, 100_001.0)
        assert service.persistence.open_positions("trend_following") == []
        assert service.persistence.latest_orders(1)[0]["status"] == "CANCELLED"

        service.start_engine()
        snapshot = feed(service, client, now + 2_000, 99_999.0, 100_001.0)
        decision = manual_decision(
            service, "breakout", snapshot, PositionSide.SHORT, "disable"
        )
        queue_manual(service, decision, snapshot)
        service.update_strategy("breakout", enabled=False)
        feed(service, client, now + 3_000, 99_999.0, 100_001.0)
        assert service.persistence.open_positions("breakout") == []
        assert service.status()["market"]["book_message_count"] >= 3
        service.shutdown()
    print("  PASS  typed book contract and pause/disable cancel pending entries")


def test_duplicate_restart_and_database_isolation() -> None:
    with tempfile.TemporaryDirectory() as directory:
        db_path = Path(directory) / "binance_paper.duckdb"
        analytics_path = Path(directory) / "analytics.duckdb"
        client = FakeFuturesClient()
        service = BinancePaperService(client, config=config(db_path, latency_ms=0))
        service.initialize()
        service.start_engine()
        base = int(time.time() * 1000)
        snapshot = feed(service, client, base, 100_000.0, 100_002.0)
        decision = manual_decision(
            service, "trend_following", snapshot, PositionSide.LONG, "restart"
        )
        assert service.persistence.record_signal(decision)
        assert not service.persistence.record_signal(decision)
        service._queue_entry(decision, snapshot, signal_already_seen=False)
        feed(service, client, base + 1, 100_000.0, 100_002.0)
        position = service.persistence.open_positions("trend_following")[0]
        service.shutdown()

        recovered_client = FakeFuturesClient()
        recovered = BinancePaperService(
            recovered_client, config=config(db_path, latency_ms=0)
        )
        recovered.initialize()
        recovered_position = recovered.persistence.open_positions("trend_following")[0]
        assert recovered_position["position_id"] == position["position_id"]
        assert recovered.persistence.has_signal(decision.signal_id)
        assert not analytics_path.exists()
        assert all(
            name.startswith("binance_paper_")
            for name in recovered.persistence.table_names()
        )
        recovered.shutdown()
    assert DEFAULT_DB_PATH.name == "binance_paper.duckdb"
    assert DEFAULT_DB_PATH.parent.name == "data"
    print("  PASS  duplicate idempotency, restart recovery and DB isolation")


def test_atomic_fill_accounting() -> None:
    with tempfile.TemporaryDirectory() as directory:
        client = FakeFuturesClient()
        service = BinancePaperService(
            client,
            config=config(Path(directory) / "paper.duckdb", latency_ms=0),
        )
        service.initialize()
        service.start_engine()
        base = int(time.time() * 1000)
        snapshot = feed(service, client, base, 99_999.0, 100_001.0)
        decision = manual_decision(
            service,
            "trend_following",
            snapshot,
            PositionSide.LONG,
            "atomic",
        )
        queue_manual(service, decision, snapshot)
        original_open = service.portfolio.open

        def fail_after_fill(*_args, **_kwargs):
            raise RuntimeError("forced account mutation failure")

        service.portfolio.open = fail_after_fill
        try:
            feed(service, client, base + 1, 99_999.0, 100_001.0)
        except RuntimeError as exc:
            assert "forced account mutation failure" in str(exc)
        else:
            raise AssertionError("forced atomic fill failure did not propagate")
        finally:
            service.portfolio.open = original_open

        assert service.fills() == []
        assert service.persistence.open_positions("trend_following") == []
        assert service.status()["pending_order_count"] == 1
        assert service.orders(1)[0]["status"] == "PENDING"

        feed(service, client, base + 2, 99_999.0, 100_001.0)
        assert len(service.fills()) == 1
        assert len(service.persistence.open_positions("trend_following")) == 1
        assert service.orders(1)[0]["status"] == "FILLED"
        service.shutdown()
    print("  PASS  fill, order and account mutation commit or roll back atomically")


def test_risk_controls() -> None:
    class RiskPersistence:
        def daily_net_pnl(self, *_args):
            return -200.0

        def net_pnl_since(self, *_args):
            return -300.0

        def recent_trade_count(self, *_args):
            return 10

        def last_exit_time_ms(self, *_args):
            return int(time.time() * 1000)

    cfg = config(Path("unused.duckdb"))
    engine = BinancePaperRiskEngine(cfg)
    now = int(time.time() * 1000)
    snapshot = MarketSnapshot(
        "BTCUSDT",
        now - 5,
        now,
        100.0,
        99.9,
        100.1,
        100.0,
        100.0,
        0.2,
        20.0,
        0,
        DataQuality.HEALTHY,
        1,
        None,
        None,
        0,
        100,
        100,
        None,
        feature_availability={
            "perpetual_book": True,
            "perpetual_mid_history": True,
        },
    )
    strategy = TrendFollowingStrategy()
    decision = strategy._decision(
        snapshot,
        action=Action.OPEN_LONG,
        side=PositionSide.LONG,
        score=1,
        confidence=0.8,
        requested_notional_usd=50_000,
        stop_price=99.0,
        take_profit_price=102.0,
        maximum_holding_seconds=300,
        features={"test": 1},
    )
    account = {
        "starting_cash_usd": 10_000.0,
        "available_cash_usd": 10_000.0,
        "equity_usd": 10_000.0,
        "maximum_drawdown_usd": 2_000.0,
    }
    risk = StrategyRiskConfig(
        max_leverage=1.0,
        leverage=2.0,
        maximum_daily_loss_usd=100.0,
        maximum_drawdown_fraction=0.10,
        maximum_trades_per_hour=4,
        cooldown_seconds=60,
    )
    result = engine.evaluate_entry(
        decision=decision,
        snapshot=snapshot,
        account=account,
        open_position=None,
        risk=risk,
        persistence=RiskPersistence(),
        runtime_active=True,
        strategy_enabled=True,
        signal_already_seen=True,
        now_ms=now,
    )
    expected = {
        "duplicate_signal",
        "leverage_limit",
        "maximum_daily_loss_reached",
        "maximum_weekly_loss_reached",
        "maximum_drawdown_reached",
        "maximum_trades_per_hour_reached",
        "cooldown_active",
    }
    assert expected.issubset(set(result.reason_codes)), result.reason_codes

    wrong_long = replace(decision, stop_price=101.0, take_profit_price=99.0)
    wrong_long_result = engine.evaluate_entry(
        decision=wrong_long,
        snapshot=snapshot,
        account=account,
        open_position=None,
        risk=StrategyRiskConfig(),
        persistence=RiskPersistence(),
        runtime_active=True,
        strategy_enabled=True,
        signal_already_seen=False,
        now_ms=now,
    )
    assert {"invalid_long_stop", "invalid_long_target"}.issubset(
        set(wrong_long_result.reason_codes)
    )
    wrong_short = replace(
        decision,
        action=Action.OPEN_SHORT,
        side=PositionSide.SHORT,
        stop_price=99.0,
        take_profit_price=101.0,
    )
    wrong_short_result = engine.evaluate_entry(
        decision=wrong_short,
        snapshot=snapshot,
        account=account,
        open_position=None,
        risk=StrategyRiskConfig(),
        persistence=RiskPersistence(),
        runtime_active=True,
        strategy_enabled=True,
        signal_already_seen=False,
        now_ms=now,
    )
    assert {"invalid_short_stop", "invalid_short_target"}.issubset(
        set(wrong_short_result.reason_codes)
    )
    missing_target = replace(decision, take_profit_price=None)
    missing_target_result = engine.evaluate_entry(
        decision=missing_target,
        snapshot=snapshot,
        account=account,
        open_position=None,
        risk=StrategyRiskConfig(),
        persistence=RiskPersistence(),
        runtime_active=True,
        strategy_enabled=True,
        signal_already_seen=False,
        now_ms=now,
    )
    assert "take_profit_required" in missing_target_result.reason_codes
    print("  PASS  leverage/notional/loss/drawdown/cooldown/duplicate risk gates")


def test_evidence_contract() -> None:
    day_ms = 86_400_000
    four_days = [
        {"exit_time_ms": day * day_ms, "net_pnl_usd": 1.0}
        for day in range(4)
    ]
    assert _day_block_lower_bound(four_days) == (None, 4)
    five_days = [
        {"exit_time_ms": day * day_ms, "net_pnl_usd": 1.0}
        for day in range(5)
    ]
    first = _day_block_lower_bound(five_days)
    second = _day_block_lower_bound(five_days)
    assert first == second
    assert first[0] is not None and first[0] > 0 and first[1] == 5
    promotion = _promotion_gate(
        [],
        observation_days=0,
        n_days=0,
        profit_factor=None,
        lower_bound=None,
    )
    assert promotion["status"] == "BLOCKED_FAILED_GATE"
    assert promotion["checks"]["positive_under_1s_latency"] is None
    assert promotion["real_orders_remain_impossible"] is True
    print("  PASS  frozen day-block bootstrap seed, weighting and five-day gate")


def run() -> None:
    print("Binance paper Phase-1 selftest")
    test_default_off()
    test_schema_v1_migration()
    test_governor_modes_and_fail_closed_inputs()
    test_signal_expiry_entry_bound_and_order_transitions()
    test_governor_emergency_flatten_integration()
    test_long_short_accounting_and_isolation()
    test_observed_funding_accounting()
    test_opposing_signal_order()
    test_actual_strategies_end_to_end()
    test_no_lookahead_stale_missing_and_liquidity()
    test_feed_contract_and_pending_entry_cancellation()
    test_duplicate_restart_and_database_isolation()
    test_atomic_fill_accounting()
    test_risk_controls()
    test_evidence_contract()
    print("binance-paper-phase1: PASS")


if __name__ == "__main__":
    run()
