"""The P(hold) probability and executable quote must describe the same contract."""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND.parent))


def test_side_quote_uses_requested_btc_side_and_exact_receive_time():
    import price_to_beat as ptb

    old = ptb._PM_QUOTES
    try:
        ptb._PM_QUOTES = {
            "markets": {
                "5": {
                    "anchor_ts": 1_000,
                    "ts": 1_025.5,
                    # Polymarket currently leads DOWN even though BTC is above its anchor.
                    "up_bid": 0.39,
                    "up_ask": 0.41,
                    "down_bid": 0.59,
                    "down_ask": 0.61,
                    "up_top_ask_size": 12.0,
                    "down_top_ask_size": 15.0,
                    "up_quote_recv_ts": 1_025.321,
                    "down_quote_recv_ts": 1_025.456,
                    "up_book_ts": 1_025.300,
                    "down_book_ts": 1_025.430,
                    "fees_enabled": True,
                    "fee_rate": 0.07,
                }
            }
        }
        round_data = {"horizon": 5, "window_start": 1_000_000,
                      "current_position": "UP"}
        quote = ptb._side_quote(round_data, 1_026_000, "UP")

        assert quote is not None
        assert quote["side"] == "UP"
        assert quote["ask"] == 0.41
        assert quote["quote_ts_ms"] == 1_025_321
        assert quote["quote_exchange_ts_ms"] == 1_025_300
        assert quote["depth"] == 12.0

        market_leader = ptb._leader_quote(round_data, 1_026_000)
        assert market_leader is not None and market_leader["side"] == "DOWN"
        assert market_leader["quote_ts_ms"] == 1_025_456
        assert ptb._side_quote(round_data, 1_026_000, "INVALID") is None
    finally:
        ptb._PM_QUOTES = old
