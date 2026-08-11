"""Exercise the real price-to-beat boundary that invokes action evidence recording."""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import price_to_beat


class _FakeRecorder:
    def __init__(self):
        self.calls = []

    def record_positions(self, positions, **kwargs):
        self.calls.append((positions, kwargs))
        return {"positions": len(positions), "normalized_positions": len(positions),
                "snapshots": len(positions), "arms": 5 * len(positions), "refused": 0}


def test_open_position_action_wiring() -> None:
    original_fetch = price_to_beat.database.fetch_open_rule_paper_positions
    original_recorder = price_to_beat.open_position_action_recorder
    original_quotes = price_to_beat._PM_QUOTES
    fake = _FakeRecorder()
    position = {
        "round_id": "ptb_5m_1", "rule": "RULE", "ts": 1, "horizon": 5,
        "side": "UP", "ask": 0.55, "fee": 0.01, "state": {"side": "UP"},
    }
    try:
        price_to_beat.database.fetch_open_rule_paper_positions = lambda round_id: [position]
        price_to_beat.open_position_action_recorder = lambda: fake
        round_data = {
            "id": "ptb_5m_1", "horizon": 5, "current_price": 70_000.0,
            "p_hold": 0.8, "champion": {"action": "WAIT"},
            "share_prices": {"paired": "exact"}, "_round_state_composed_ms": 999,
        }
        result = price_to_beat._capture_open_position_action_evidence(
            round_data, 10_000, 45, "UP",
        )
        assert result["snapshots"] == 1
        assert len(fake.calls) == 1
        positions, kwargs = fake.calls[0]
        assert positions == [position]
        assert kwargs["market_snapshot"] is round_data["share_prices"]
        assert kwargs["recorded_ts"] == 10_000
        assert kwargs["context"]["mode"] == "PAPER_RESEARCH_ONLY"
        assert kwargs["context"]["btc_side"] == "UP"
        assert kwargs["context"]["champion_action"] == "WAIT"
        assert round_data["_open_action_capture_ms"] == 10_000
        assert price_to_beat._capture_open_position_action_evidence(
            round_data, 10_001, 44, "UP",
        ) is None
        assert len(fake.calls) == 1

        price_to_beat._PM_QUOTES = {"markets": {"5": {
            "ts": 1_000.0, "anchor_ts": 900, "fee_rate": 0.07,
            "fees_enabled": True, "slug": "btc-test",
            "up_bid": 0.49, "up_ask": 0.51, "up_spread": 0.02,
            "up_top_ask_size": 1.0, "up_top_bid_size": 1.0,
            "up_b1": 1.0, "up_b5": 2.0, "up_quote_recv_ts": 1_000.0,
            "up_book_ts": 1_000.0, "up_book_hash": "up",
            "up_ladder": {"b": [[0.49, 1.0]], "a": [[0.51, 1.0]]},
            "up_full_ladder": {
                "b": [[0.49, 1.0], [0.48, 3.0]],
                "a": [[0.51, 1.0], [0.52, 3.0]],
            },
            "down_bid": 0.48, "down_ask": 0.52, "down_spread": 0.04,
            "down_top_ask_size": 1.0, "down_top_bid_size": 1.0,
            "down_b1": 1.0, "down_b5": 2.0, "down_quote_recv_ts": 1_000.0,
            "down_book_ts": 1_000.0, "down_book_hash": "down",
            "down_ladder": {"b": [[0.48, 1.0]], "a": [[0.52, 1.0]]},
            "down_full_ladder": {
                "b": [[0.48, 1.0], [0.47, 3.0]],
                "a": [[0.52, 1.0], [0.53, 3.0]],
            },
        }}}
        paired = price_to_beat._live_share_prices_for_round(
            {"horizon": 5, "window_start": 900_000}, 1_000_100,
        )
        assert paired is not None
        assert len(paired["up"]["bid_ladder"]) == 2
        assert len(paired["down"]["ask_ladder"]) == 2
        assert paired["up"]["quote_recv_ts"] == 1_000.0
        assert paired["fee_rate"] == 0.07
        tracker = price_to_beat.PriceToBeatTracker(horizons=(5,), persist=False)
        tracker.latest_round[5] = {"share_prices": paired}
        public = tracker.latest()[5]
        assert "bid_ladder" not in public["share_prices"]["up"]
        assert "ask_ladder" not in public["share_prices"]["down"]
        assert "bid_ladder" in tracker.latest_round[5]["share_prices"]["up"]
    finally:
        price_to_beat.database.fetch_open_rule_paper_positions = original_fetch
        price_to_beat.open_position_action_recorder = original_recorder
        price_to_beat._PM_QUOTES = original_quotes


if __name__ == "__main__":
    test_open_position_action_wiring()
    print("OPEN POSITION ACTION WIRING: PASS")
