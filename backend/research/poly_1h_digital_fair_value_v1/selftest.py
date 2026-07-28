#!/usr/bin/env python
"""Deterministic tests for the one-hour fair-value campaign."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.research.poly_1h_digital_fair_value_v1.core import (
    RoundPathState,
    digital_up_probability,
    fee_per_share,
    mixture_probability,
    normalized_market_probability,
    parse_book,
    settled_side,
    vwap,
    vwap_with_fee,
)
from backend.research.poly_1h_digital_fair_value_v1.live_shadow import (
    load_protocol,
    parse_hourly_event,
)
from backend.research.poly_1h_digital_fair_value_v1.report import build_report
from backend.research.poly_1h_digital_fair_value_v1.store import FairValueStore


def fixture_event() -> dict:
    description = (
        'This market will resolve to "Up" if the close price is greater than or equal '
        "to the open price for the BTC/USDT 1 hour candle that begins on the time and "
        'date specified in the title. The close « C » and open « O » for the "1H" candle.'
    )
    return {
        "id": "event",
        "seriesSlug": "btc-up-or-down-hourly",
        "markets": [
            {
                "id": "market",
                "slug": "bitcoin-up-or-down-july-28-2026-4am-et",
                "conditionId": "condition",
                "outcomes": '["Down","Up"]',
                "clobTokenIds": '["down-token","up-token"]',
                "eventStartTime": "2026-07-28T08:00:00Z",
                "endDate": "2026-07-28T09:00:00Z",
                "startDate": "2026-07-26T10:30:30Z",
                "description": description,
                "resolutionSource": "https://www.binance.com/en/trade/BTC_USDT",
                "feeSchedule": {
                    "rate": 0.07,
                    "exponent": 1,
                    "takerOnly": True,
                },
            }
        ],
    }


def snapshot_row() -> dict:
    names = [
        "seconds_elapsed",
        "seconds_left",
        "binance_open",
        "binance_price",
        "binance_distance_bps",
        "slow_volatility",
        "fast_volatility",
        "jump_volatility",
        "p_a_market",
        "p_b_distance_time",
        "p_c_volatility_mixture",
        "up_bid",
        "up_ask",
        "up_mid",
        "up_spread",
        "up_bid_size",
        "up_ask_size",
        "down_bid",
        "down_ask",
        "down_mid",
        "down_spread",
        "down_bid_size",
        "down_ask_size",
        "up_receive_latency_ms",
        "down_receive_latency_ms",
        "up_fee_rate",
        "down_fee_rate",
        "up_fee_at_ask",
        "down_fee_at_ask",
        "fraction_above",
        "fraction_below",
        "crossing_rate_per_minute",
        "seconds_since_crossing",
        "average_residence_above",
        "average_residence_below",
        "longest_residence_above",
        "longest_residence_below",
        "maximum_above_bps",
        "minimum_below_bps",
        "drawdown_from_side_extreme_bps",
        "velocity_15s_bps_per_second",
        "velocity_60s_bps_per_second",
    ]
    row = {name: 0.0 for name in names}
    row.update(
        {
            "slug": "test",
            "observed_second": 100,
            "observed_ts_ms": 100_000,
            "candle_open_ts_ms": 0,
            "candle_close_ts_ms": 3_600_000,
            "seconds_left": 3300.0,
            "binance_open": 100.0,
            "binance_price": 101.0,
            "binance_price_source": "fixture",
            "binance_source_ts_ms": 99_900,
            "binance_age_ms": 100,
            "p_a_market": 0.5,
            "p_b_distance_time": 0.6,
            "p_c_volatility_mixture": 0.61,
            "up_bid": 0.49,
            "up_ask": 0.51,
            "up_mid": 0.50,
            "down_bid": 0.49,
            "down_ask": 0.51,
            "down_mid": 0.50,
            "up_book_ts_ms": 99_900,
            "down_book_ts_ms": 99_900,
            "pair_receive_skew_ms": 0,
            "up_ladder_json": "{}",
            "down_ladder_json": "{}",
            "vwap_json": json.dumps(
                {
                    "up_5": {
                        "order_size_eligible": True,
                        "buy_vwap": 0.51,
                        "buy_fill": 5.0,
                        "buy_fee_per_share": 0.02499,
                    },
                    "down_5": {
                        "order_size_eligible": True,
                        "buy_vwap": 0.51,
                        "buy_fill": 5.0,
                        "buy_fee_per_share": 0.02499,
                    },
                }
            ),
            "crossing_count": 0,
            "valid": True,
            "invalid_reason": None,
        }
    )
    return row


def main() -> int:
    protocol = load_protocol()
    assert protocol["promotion_status"] == "research_only"
    assert not protocol["boundaries"]["may_submit_orders"]
    parsed = parse_hourly_event(fixture_event(), protocol, 1)
    assert parsed is not None
    assert parsed["up_token_id"] == "up-token"
    assert parsed["down_token_id"] == "down-token"
    assert parsed["candle_close_ts_ms"] - parsed["candle_open_ts_ms"] == 3_600_000

    assert settled_side(100.0, 100.0) == "UP"
    assert settled_side(100.0, 99.99) == "DOWN"
    symmetric = digital_up_probability(100.0, 100.0, 1800, 0.60)
    assert abs(symmetric - 0.5) < 1e-12
    assert digital_up_probability(101.0, 100.0, 1800, 0.60) > symmetric
    assert digital_up_probability(99.0, 100.0, 1800, 0.60) < symmetric
    mixed = mixture_probability(101.0, 100.0, 1800, [0.4, 0.8], [0.5, 0.5])
    assert 0.5 < mixed < 1.0
    assert abs(normalized_market_probability(0.52, 0.48) - 0.52) < 1e-12
    assert fee_per_share(0.5, 1000) == 0.025

    book = parse_book(
        {
            "bids": [{"price": "0.49", "size": "10"}],
            "asks": [
                {"price": "0.51", "size": "3"},
                {"price": "0.52", "size": "10"},
            ],
            "timestamp": "100000",
            "hash": "hash",
            "min_order_size": "5",
            "tick_size": "0.01",
            "neg_risk": False,
        },
        100_100,
        12.0,
    )
    price, filled = vwap(book.asks, 5)
    assert filled == 5.0 and abs(float(price) - 0.514) < 1e-12
    fee_price, fee_fill, exact_fee = vwap_with_fee(book.asks, 5, 1000)
    expected_fee = (3 * fee_per_share(0.51, 1000) + 2 * fee_per_share(0.52, 1000)) / 5
    assert fee_fill == 5.0 and fee_price == price
    assert abs(float(exact_fee) - expected_fee) < 1e-12

    path = RoundPathState(100.0)
    path.update(0.0, 101.0)
    path.update(1.0, 99.0)
    features = path.update(2.0, 101.0)
    assert features["crossing_count"] == 2
    assert features["fraction_above"] == 2 / 3
    assert features["fraction_below"] == 1 / 3

    with tempfile.TemporaryDirectory() as directory:
        database = Path(directory) / "test.duckdb"
        store = FairValueStore(database)
        try:
            market = parsed.copy()
            market["up_base_fee_bps"] = 1000
            market["down_base_fee_bps"] = 1000
            store.market(market)
            assert store.counts()["hourly_markets"] == 1
            row = snapshot_row()
            assert store.snapshot(row)
            assert not store.snapshot(row)
            assert store.path_samples("test") == [(100.0, 101.0)]
            store.resolution(
                {
                    "slug": "test",
                    "candle_open_ts_ms": 0,
                    "candle_close_ts_ms": 3_600_000,
                    "finalized_open": 100.0,
                    "finalized_high": 102.0,
                    "finalized_low": 99.0,
                    "finalized_close": 101.0,
                    "finalized_volume": 10.0,
                    "binance_side": "UP",
                    "polymarket_side": "UP",
                    "polymarket_resolution_source": "fixture",
                    "sides_match": True,
                    "finalized_kline": True,
                    "resolved_ts_ms": 3_600_001,
                }
            )
            assert store.counts()["hourly_resolutions"] == 1
        finally:
            store.close()
        report = build_report(database, Path(directory) / "report")
        assert report["counts"]["hourly_resolutions"] == 1
        assert report["probability_metrics_by_checkpoint"]["3300"]["p_a"]["n"] == 1
        assert not report["promotion_ready"]
        assert (Path(directory) / "report" / "path_targets.csv").exists()
    print("poly-1h fair-value self-test: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
