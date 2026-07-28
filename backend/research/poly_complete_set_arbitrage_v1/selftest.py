#!/usr/bin/env python
"""Deterministic package tests for complete-set economics and persistence."""

from __future__ import annotations

import tempfile
import time
from pathlib import Path

from backend.polymarket.l2_book import L2Book
from backend.research.poly_complete_set_arbitrage_v1.economics import (
    selftest as economics_selftest,
)
from backend.research.poly_complete_set_arbitrage_v1.live_shadow import (
    CompleteSetShadow,
    FeeQuote,
    load_protocol,
)
from backend.research.poly_complete_set_arbitrage_v1.report import build_report
from backend.research.poly_complete_set_arbitrage_v1.shadow_store import (
    CompleteSetStore,
)


def main() -> int:
    economics_selftest()
    protocol = load_protocol()
    assert protocol["promotion_status"] == "research_only"
    assert not protocol["boundaries"]["may_submit_orders"]
    assert not protocol["boundaries"]["may_read_credentials"]
    with tempfile.TemporaryDirectory() as directory:
        store = CompleteSetStore(Path(directory) / "shadow.duckdb")
        try:
            store.set_meta("test", {"ok": True})
            now = time.time()
            store.market(
                {
                    "slug": "test",
                    "condition_id": "condition",
                    "horizon": 5,
                    "start_ts": now,
                    "end_ts": now + 300,
                    "up": "up",
                    "down": "down",
                    "up_base_fee_bps": 1000,
                    "down_base_fee_bps": 1000,
                    "up_min_order_size": 5.0,
                    "down_min_order_size": 5.0,
                    "up_tick_size": 0.01,
                    "down_tick_size": 0.01,
                    "neg_risk": False,
                    "fee_fetched_at": now,
                    "last_seen_at": now,
                }
            )
            assert store.counts()["complete_set_markets"] == 1
        finally:
            store.close()

    # A gap that closes before 250 ms must still receive all delayed grades.
    with tempfile.TemporaryDirectory() as directory:
        delay_db = Path(directory) / "delay.duckdb"
        store = CompleteSetStore(delay_db)
        try:
            shadow = CompleteSetShadow(protocol, store)
            now_ns = time.time_ns()
            now = now_ns / 1e9
            up_fee = FeeQuote("up", 1000, 0.10, 5.0, 0.01, False, now)
            down_fee = FeeQuote("down", 1000, 0.10, 5.0, 0.01, False, now)
            row = {
                "slug": "delay-test",
                "condition_id": "condition",
                "horizon": 5,
                "start_ts": now - 1,
                "end_ts": now + 300,
                "up": "up",
                "down": "down",
                "up_fee": up_fee,
                "down_fee": down_fee,
                "up_base_fee_bps": 1000,
                "down_base_fee_bps": 1000,
            }
            shadow.rounds[row["slug"]] = row
            up = L2Book("up")
            down = L2Book("down")
            up.load_snapshot(
                [{"price": 0.46, "size": 100}],
                [{"price": 0.47, "size": 100}],
                exchange_ts_ms=int(now * 1000),
                recv_ts_ns=now_ns,
                book_hash="up-0",
            )
            down.load_snapshot(
                [{"price": 0.46, "size": 100}],
                [{"price": 0.47, "size": 100}],
                exchange_ts_ms=int(now * 1000),
                recv_ts_ns=now_ns,
                book_hash="down-0",
            )
            shadow.books = {"up": up, "down": down}
            shadow.evaluate_slug(row["slug"], now_ns)
            assert len(shadow.open_by_key) == 3

            for elapsed_ms, hash_value in (
                (100, "down-1"),
                (300, "down-2"),
                (600, "down-3"),
                (1100, "down-4"),
            ):
                observed_ns = now_ns + int(elapsed_ms * 1e6)
                down.load_snapshot(
                    [{"price": 0.39, "size": 100}],
                    [{"price": 0.60, "size": 100}],
                    exchange_ts_ms=int(now * 1000 + elapsed_ms),
                    recv_ts_ns=observed_ns,
                    book_hash=hash_value,
                )
                shadow.evaluate_slug(row["slug"], observed_ns)
            counts = store.counts()
            assert counts["complete_set_opportunities"] == 3, counts
            assert counts["complete_set_delay_stress"] == 9, counts
            assert not shadow.open_by_key and not shadow.active
        finally:
            store.close()
        report = build_report(delay_db, Path(directory) / "report")
        assert report["counts"]["independent_opportunities"] == 3, report
        assert set(report["delay_stress"]) == {250, 500, 1000}, report
        assert not report["promotion_ready"]
    print("complete-set package self-test: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
