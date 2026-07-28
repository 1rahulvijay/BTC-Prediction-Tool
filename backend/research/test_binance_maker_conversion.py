"""Deterministic tests for the forward-only Binance maker research lane."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "backend" / "research"
if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))

from binance_maker_conversion_v1.order_book import (
    BookSequenceGap,
    ConservativeQueue,
    LocalOrderBook,
)
from binance_maker_conversion_v1.report import summarize
from binance_maker_conversion_v1.simulator import ExecutionSimulator
from binance_maker_conversion_v1.store import EvidenceStore

PROTOCOL_PATH = (
    RESEARCH / "binance_maker_conversion_v1" / "frozen_protocol.json"
)


def protocol() -> dict:
    return json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))


def book() -> LocalOrderBook:
    value = LocalOrderBook()
    value.initialize(
        {
            "lastUpdateId": 10,
            "bids": [["100.0", "2.0"], ["99.0", "4.0"]],
            "asks": [["101.0", "3.0"], ["102.0", "5.0"]],
        },
        1000,
    )
    return value


def test_local_order_book_sequence_and_walk() -> None:
    value = book()
    assert value.apply(
        {
            "U": 10,
            "u": 11,
            "pu": 9,
            "E": 1100,
            "b": [["100.0", "1.5"]],
            "a": [["101.0", "0"], ["101.5", "2.0"]],
        },
        1101,
    )
    top = value.top()
    assert top is not None
    assert top.best_bid == 100.0
    assert top.best_ask == 101.5
    assert value.walk(True, 3.0) == ((101.5 * 2.0 + 102.0) / 3.0, 3.0)
    try:
        value.apply(
            {"U": 13, "u": 13, "pu": 12, "E": 1200, "b": [], "a": []},
            1201,
        )
    except BookSequenceGap:
        pass
    else:
        raise AssertionError("sequence gap must force a rebuild")


def test_conservative_queue_never_credits_cancellations() -> None:
    queue = ConservativeQueue(
        buy_order=True,
        price=100.0,
        quantity=1.0,
        queue_ahead=2.0,
        placed_ts_ms=1000,
    )
    assert (
        queue.apply_trade(
            trade_price=100.0,
            trade_quantity=1.5,
            buyer_is_maker=True,
            trade_ts_ms=1100,
        )
        == 0.0
    )
    assert queue.queue_ahead == 0.5
    assert (
        queue.apply_trade(
            trade_price=100.0,
            trade_quantity=1.0,
            buyer_is_maker=True,
            trade_ts_ms=1200,
        )
        == 0.5
    )
    assert queue.fill_fraction == 0.5


def test_routes_preserve_candidate_and_charge_liquidity_specific_fees() -> None:
    value = book()
    simulator = ExecutionSimulator(protocol())
    routes = simulator.create_routes(
        candidate_id="candidate",
        side="LONG",
        horizon_seconds=5,
        decision_ts_ms=1000,
        quantity=1.0,
        book=value,
    )
    assert len(routes) == 5
    assert {route.policy for route in routes} == set(protocol()["execution"]["policies"])
    route_a = routes[0]
    assert route_a.entry_price == 101.0
    assert route_a.entry_taker_quantity == 1.0
    simulator.on_clock(route_a, value, 6000)
    assert route_a.status == "RESOLVED"
    economics = simulator.economics(route_a)
    assert abs(float(economics["fee_bps"]) - 9.950495) < 1e-6
    route_c = next(
        route
        for route in routes
        if route.policy == "C_MAKER_TTL_FALLBACK_TAKER"
    )
    simulator.on_clock(route_c, value, 2000)
    assert route_c.entry_status == "FILLED"
    assert route_c.entry_taker_quantity == 1.0
    route_b = next(route for route in routes if route.policy == "B_MAKER_TAKER")
    assert route_b.entry_ts_ms is None
    simulator.on_trade(
        route_b,
        price=100.0,
        quantity=3.0,
        buyer_is_maker=True,
        trade_ts_ms=1500,
    )
    assert route_b.entry_status == "FILLED"
    assert route_b.entry_ts_ms == 1500
    simulator.on_clock(route_b, value, 6000)
    assert abs(float(simulator.economics(route_b)["fee_bps"]) - 7.0) < 1e-9
    route_e = routes[-1]
    assert route_e.status == "SKIPPED"
    assert route_e.reason == "labels_required_fail_closed"


def test_evidence_store_deduplicates_candidates(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "shadow.duckdb")
    top = book().top()
    assert top is not None
    row = {
        "candidate_id": "abc",
        "decision_second": 1,
        "decision_ts_ms": 1000,
        "horizon_seconds": 5,
        "side": "LONG",
        "p_direction": 0.7,
        "p_movement": 0.8,
        "p_roundtrip": 0.2,
        "model_margin": 0.2,
        "quantity": 1.0,
        "notional_usd": 100.0,
        "best_bid": top.best_bid,
        "best_ask": top.best_ask,
        "bid_quantity": top.bid_quantity,
        "ask_quantity": top.ask_quantity,
        "spread_bps": top.spread_bps,
        "book_update_id": top.update_id,
        "book_event_ts_ms": top.event_ts_ms,
        "book_received_ts_ms": top.received_ts_ms,
        "book_age_ms": 0,
        "protocol_hash": "p",
        "model_bundle_hash": "m",
        "feature_schema_hash": "f",
        "code_commit": "c",
        "created_ts_ms": 1000,
    }
    assert store.candidate(row)
    assert not store.candidate(row)
    routes = ExecutionSimulator(protocol()).create_routes(
        candidate_id="abc",
        side="LONG",
        horizon_seconds=5,
        decision_ts_ms=1000,
        quantity=1.0,
        book=book(),
    )
    simulator = ExecutionSimulator(protocol())
    for route in routes:
        store.route(route, simulator.economics(route))
    assert store.db.execute("SELECT COUNT(*) FROM routes").fetchone()[0] == 5
    store.close()
    report = summarize(tmp_path / "shadow.duckdb")
    assert report["candidates"] == 1
    assert report["promotion_readiness"]["status"] == "NOT_ELIGIBLE"
