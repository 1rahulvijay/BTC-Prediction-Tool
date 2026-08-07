"""A delayed or out-of-order book may not become the executable quote. (scan-4 item 4.1)

    python backend/test_book_freshness_and_ordering.py

THE DEFECT, IN TWO HALVES

1. `_last_book` was overwritten on EVERY message, with no monotonic check. A delayed or
   out-of-order event replaced a NEWER one and became the book the fill simulator quoted from.

2. Freshness was judged on `feed_age_ms = now - received_at_ms` - how long ago THIS PROCESS saw
   the message. A delayed old exchange event received *now* therefore scores ~0 and reads as
   perfectly fresh. The exchange-event age was never a rejection condition, and
   `fill_simulator` even computed the transport lag as `quote_age` on the line above the
   rejection chain and then discarded it.

WHY THIS ONE IS WORTH THE EFFORT

Two published arbitrage post-mortems (docs/active/POLYMARKET_ARB_EVIDENCE_2026-08-07.md) name
adverse selection from stale quotes as their single largest loss cause - "my 60c bid is now a
gift sitting on the book" - with -$3,185 of unhedged residual against +$8,293 of arb profit.
That is the same failure: acting on a quote whose SOURCE age nobody checked.

THREE AGES, NEVER ONE

    local     now - received_at_ms          how long since WE saw it
    source    now - event_ts_ms             how long since the EXCHANGE stamped it
    transport received_at_ms - event_ts_ms  how long it took to reach us
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


class _Health:
    @staticmethod
    def health_snapshot(_now):
        return {"agg_trade_message_count": 0, "agg_trade_age_ms": None}


def _book(*, event_ts_ms, received_at_ms, bid=60000.0, ask=None, update_id=None):
    # ask DERIVED from bid unless given: the adapter rejects ask <= bid, and an earlier version
    # of this fixture moved the bid while leaving a stale default ask, which made a valid book
    # look invalid.
    ask = bid + 1.0 if ask is None else ask
    row = {
        "symbol": "BTCUSDT", "best_bid": bid, "best_ask": ask,
        "bid_size": 5.0, "ask_size": 5.0,
        "event_ts_ms": event_ts_ms, "received_at_ms": received_at_ms,
    }
    if update_id is not None:
        row["update_id"] = update_id
    return row


def main() -> int:
    from binance_paper.config import EngineConfig
    from binance_paper.market_adapter import BinancePaperMarketAdapter

    cfg = EngineConfig(
        hard_enabled=True, db_path=Path("unused.duckdb"), starting_cash_usd=10_000.0,
        fee_rate_bps=5.0, slippage_bps=1.0, latency_ms=500, quote_stale_ms=2_000,
        source_stale_ms=3_000, max_transport_lag_ms=2_000,
        evaluation_interval_ms=1_000, sample_interval_ms=1_000,
    )

    print("1. an out-of-order exchange event cannot replace a newer book")
    ad = BinancePaperMarketAdapter(_Health(), None, cfg)
    ad.ingest_book(_book(event_ts_ms=10_000, received_at_ms=10_050, bid=60000.0, ask=60001.0))
    # A message stamped EARLIER by the exchange, delivered LATER by the network.
    ad.ingest_book(_book(event_ts_ms=9_000, received_at_ms=10_090, bid=59000.0, ask=59001.0))
    snap = ad.snapshot(10_100)
    chk(snap is not None and abs(snap.best_bid - 60000.0) < 1e-9,
        f"the newer book survives (bid {snap.best_bid}) - the delayed 9,000ms event did NOT "
        f"overwrite it, which is what made a stale price executable")
    chk(ad.stale_book_drops == 1,
        f"and the drop is COUNTED ({ad.stale_book_drops}) - a rising number means the feed is "
        f"reordering, which is itself a health signal rather than a silent discard")

    print("   update_id wins over the timestamp when the venue supplies it")
    ad2 = BinancePaperMarketAdapter(_Health(), None, cfg)
    ad2.ingest_book(_book(event_ts_ms=10_000, received_at_ms=10_050, bid=60000.0, update_id=99))
    # SAME millisecond, LOWER sequence - two events can share a timestamp.
    ad2.ingest_book(_book(event_ts_ms=10_000, received_at_ms=10_060, bid=58000.0, update_id=98))
    s2 = ad2.snapshot(10_100)
    chk(abs(s2.best_bid - 60000.0) < 1e-9 and ad2.stale_book_drops == 1,
        "a lower update_id at an identical timestamp is rejected - timestamps alone cannot "
        "order two events inside one millisecond")

    print("   and a genuinely newer book IS accepted")
    ad2.ingest_book(_book(event_ts_ms=10_500, received_at_ms=10_540, bid=61000.0, update_id=100))
    chk(abs(ad2.snapshot(10_600).best_bid - 61000.0) < 1e-9,
        "the guard rejects only backwards motion, not forward motion")

    print("2. the three ages are computed separately")
    ad3 = BinancePaperMarketAdapter(_Health(), None, cfg)
    # Stamped long ago by the exchange, received THIS instant: the exact shape that read fresh.
    ad3.ingest_book(_book(event_ts_ms=1_000, received_at_ms=10_000))
    s3 = ad3.snapshot(10_010)
    chk(s3.feed_age_ms <= 10,
        f"LOCAL age is ~0 ({s3.feed_age_ms}ms) - this alone is what freshness used to mean, "
        f"and by it this quote is pristine")
    chk(s3.source_age_ms >= 9_000,
        f"SOURCE age is {s3.source_age_ms}ms - the exchange stamped this event nine seconds ago")
    chk(s3.transport_lag_ms >= 9_000,
        f"and TRANSPORT lag is {s3.transport_lag_ms}ms")

    print("3. the fill simulator REJECTS on source age, not just local age")
    from binance_paper.fill_simulator import BinancePaperFillSimulator
    from binance_paper.schemas import PositionSide

    sim = BinancePaperFillSimulator(cfg)

    def _fill(snapshot):
        return sim.simulate(
            signal_id="s1", order_id="o1", strategy_id="st", side=PositionSide.LONG,
            operation="ENTRY", requested_quantity=0.01, snapshot=snapshot,
            # The engine derives arrival as decision + latency_ms, so a decision this far back
            # means arrival has already been reached and `latency_not_reached` cannot mask the
            # freshness rejection under test.
            decision_ts_ms=snapshot.received_at_ms - cfg.latency_ms,
        )

    stale_source = _fill(s3)
    chk(stale_source.filled_quantity == 0
        and "stale_source_event" in str(stale_source.rejection_reason),
        f"a book that is locally fresh but nine seconds old at the exchange is REFUSED "
        f"({stale_source.rejection_reason}) - previously it filled")

    ad4 = BinancePaperMarketAdapter(_Health(), None, cfg)
    ad4.ingest_book(_book(event_ts_ms=10_000, received_at_ms=10_020))
    good = ad4.snapshot(10_030)
    ok_fill = _fill(good)
    chk(ok_fill.filled_quantity > 0,
        f"while a genuinely fresh book still fills ({ok_fill.filled_quantity}) - this tightened "
        f"the gate, it did not close the engine")

    print("4. the gates are REQUIRED, never defaulted")
    import dataclasses
    fields = {f.name: f for f in dataclasses.fields(EngineConfig)}
    for name in ("source_stale_ms", "max_transport_lag_ms"):
        chk(name in fields, f"{name} is part of the engine config")
        chk(fields[name].default is dataclasses.MISSING,
            f"and {name} has NO default - a defaulted gate is a gate that disappears when a "
            f"caller forgets it, which is the fill-engine lesson (fd46d51)")

    print("\nBOOK FRESHNESS AND ORDERING:", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
