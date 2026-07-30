"""Offline protocol and fail-closed state tests for the app-facing Polymarket client."""
from __future__ import annotations

import sys

from polymarket_client import PolymarketClient


def _market() -> dict:
    return {
        "id": "m1",
        "condition_id": "c1",
        "event_id": "e1",
        "question": "Bitcoin Up or Down",
        "slug": "btc-up-down",
        "yes_token": "UP",
        "no_token": "DOWN",
        "yes_outcome": "Up",
        "no_outcome": "Down",
        "reference_price": None,
        "reference_source": "none",
        "end_date": "2099-01-01T00:00:00Z",
        "tick_size": "0.01",
        "minimum_order_size": "5",
    }


def main() -> int:
    ok = True

    def check(condition: bool, message: str) -> None:
        nonlocal ok
        print(f"  {'PASS' if condition else 'FAIL'}  {message}")
        ok = ok and condition

    client = PolymarketClient()
    market = _market()
    client.markets = {"UP": market, "DOWN": market}

    before = client.handle_ws_message(
        {
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": "UP", "side": "BUY", "price": "0.49", "size": "10"}
            ],
            "timestamp": "1000",
        }
    )
    check(before == 0 and client.increment_before_snapshot == 1,
          "an increment before a snapshot is refused")

    snapshot = client.handle_ws_message(
        {
            "event_type": "book",
            "asset_id": "UP",
            "market": "m1",
            "bids": [{"price": "0.48", "size": "10"}],
            "asks": [{"price": "0.52", "size": "12"}],
            "timestamp": "1001",
        }
    )
    check(snapshot == 1 and client.orderbooks["UP"].valid,
          "a raw CLOB book establishes synchronized state")

    changed = client.handle_ws_message(
        {
            "topic": "market",
            "type": "price_change",
            "payload": {
                "market": "m1",
                "priceChanges": [
                    {
                        "tokenId": "UP",
                        "side": "BUY",
                        "price": "0.49",
                        "size": "8",
                    },
                    {
                        "tokenId": "UP",
                        "side": "BUY",
                        "price": "0.48",
                        "size": "0",
                    },
                ],
                "timestamp": "1002",
            },
        }
    )
    book = client.orderbooks["UP"]
    check(changed == 2 and float(book.best_bid) == 0.49,
          "nested SDK-style price changes add and delete exact levels")
    check(float(book.level_size("BUY", "0.48")) == 0.0,
          "a zero-size change removes the old level")

    camel = client.handle_ws_message(
        {
            "topic": "market",
            "type": "update",
            "payload": {
                "eventType": "price_change",
                "priceChanges": [
                    {
                        "assetId": "UP",
                        "side": "SELL",
                        "price": "0.51",
                        "size": "7",
                    }
                ],
                "timestamp": "1003",
            },
        }
    )
    check(
        camel == 1 and float(client.orderbooks["UP"].best_ask) == 0.51,
        "nested eventType and assetId override a generic SDK wrapper type",
    )

    client.handle_ws_message(
        {
            "event_type": "tick_size_change",
            "asset_id": "UP",
            "new_tick_size": "0.001",
        }
    )
    check(client.markets["UP"]["tick_size"] == "0.001",
          "tick-size changes update market metadata")

    client.handle_ws_message(
        {
            "event_type": "market_resolved",
            "assets_ids": ["UP", "DOWN"],
            "winning_asset_id": "UP",
            "winning_outcome": "Up",
        }
    )
    check(client.pending_remove == {"UP", "DOWN"},
          "resolved markets are queued for dynamic unsubscribe")
    check(client.markets["DOWN"]["winning_token"] == "UP",
          "resolution metadata is retained")

    gamma = PolymarketClient._market_from_gamma(
        {"id": "event", "title": "BTC next round"},
        {
            "id": "market",
            "question": "Will BTC be above $999999?",
            "slug": "btc-test",
            "active": True,
            "closed": False,
            "clobTokenIds": '["YES", "NO"]',
            "outcomes": '["Yes", "No"]',
        },
    )
    check(gamma is not None and gamma["reference_price"] is None,
          "question text can never become a trading reference price")

    client.connected = True
    client.last_valid_book_ts = 0.0
    check("valid_book_stale" in client.status()["blockers"],
          "socket liveness cannot hide stale market content")

    print("POLYMARKET CLIENT PROTOCOL", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
