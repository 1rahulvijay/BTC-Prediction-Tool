"""Isolated regression tests for paper execution, restart, and settlement integrity.

Runs against a temporary DuckDB and never touches the live analytics database.
Usage: python backend/tests/test_paper_trading_integrity.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import tempfile
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

import database
import decision_champion
import open_position_action_recorder as action_recorder_module
from price_to_beat import PriceToBeatTracker, _predict_path_plan


def _entry(round_id: str, start: int, verify_at: int) -> dict:
    return {
        "id": round_id, "timestamp": start, "horizon": 5,
        "price_to_beat": 60_000.0, "our_direction": "UP", "signal": "LONG",
        "conviction": 0.7, "actionable": True, "kronos_direction": "NONE",
        "target_price": 60_100.0, "verify_at": verify_at, "lean_source": "model",
        "regime": "RANGE", "source": "pyth",
    }


def run() -> None:
    with tempfile.TemporaryDirectory(prefix="btc-paper-integrity-") as td:
        old_path = database.DB_PATH
        old_anchor = database._ANCHOR_CONN
        old_action_recorder = action_recorder_module._RECORDER
        database.DB_PATH = str(Path(td) / "test.duckdb")
        database._ANCHOR_CONN = None
        action_recorder_module._RECORDER = action_recorder_module.OpenPositionActionRecorder(
            Path(td) / "open_position_actions.duckdb"
        )
        database._OFFICIAL_SETTLEMENT_MTIME = -1.0
        try:
            database.init_db()
            now = int(time.time() * 1000)
            start = now - now % 300_000

            # A future open round restores its exact anchor and disables new entries.
            future_id = f"ptb_5m_{start}"
            database.log_price_to_beat(_entry(future_id, start, start + 300_000))
            tracker = PriceToBeatTracker(horizons=(5,), source="pyth")
            rows = database.fetch_open_price_to_beat("pyth", now)
            assert tracker.restore_pending(rows, now) == 1
            assert tracker.pending[0]["_paper_entries_disabled"] is True
            assert tracker.current_window[5] == start

            # Adding a staged second leg updates cumulative cost atomically.
            state1 = {
                "up": {"entry": 0.25, "fee_in": 0.01, "exit_bid": None, "exit_fee": 0.0},
                "dn": None, "second_added": False,
            }
            database.log_rule_paper_trade(
                future_id, "SEQ", now, 5, "UP", .25, .24, .01, .01, 1, "ENTER",
                state=state1)
            state2 = {
                "up": state1["up"],
                "dn": {"entry": .30, "fee_in": .01, "exit_bid": None, "exit_fee": 0.0},
                "second_added": True,
            }
            assert database.add_rule_paper_leg(future_id, "SEQ", .30, .29, .01, state2)
            conn = database._connect()
            side, ask, fee = conn.execute(
                "SELECT side, ask, fee FROM rule_paper_trades WHERE round_id=? AND rule='SEQ'",
                (future_id,)).fetchone()
            conn.close()
            assert side == "BOTH" and abs(ask - .55) < 1e-9 and abs(fee - .02) < 1e-9

            # An unbought/missing leg never receives settlement value.
            one_id = "ptb_5m_1000000"
            one = {"up": state1["up"], "dn": None}
            database.log_rule_paper_trade(
                one_id, "ONE", now, 5, "BOTH", .25, .24, .01, .01, 1, "ENTER", state=one)
            database.settle_rule_paper_trades(one_id, "DOWN", now, settlement_source="test")
            conn = database._connect()
            one_pnl = conn.execute(
                "SELECT pnl FROM rule_paper_trades WHERE round_id=?", (one_id,)).fetchone()[0]
            conn.close()
            assert abs(one_pnl - (-.26)) < 1e-9

            # Official outcome replaces proxy hold P/L but preserves a completed bid exit.
            anchor = 1_700_000_000
            official_id = f"ptb_5m_{anchor * 1000}"
            database.log_price_to_beat(_entry(
                official_id, anchor * 1000, anchor * 1000 + 300_000))
            database.log_rule_paper_trade(
                official_id, "HOLD", now, 5, "UP", .60, .59, .0168, .01, 1, "ENTER")
            database.settle_rule_paper_trades(
                official_id, "UP", now, 60_100, settlement_source="pyth_proxy")
            database.log_rule_paper_trade(
                official_id, "EARLY", now, 5, "UP", .50, .49, .0175, .01, 1, "ENTER")
            database.close_rule_paper_trade(
                official_id, "EARLY", .05, now, "TP", btc_exit=60_020,
                exit_gross=.60, exit_fee=.0168, settlement_source="live_bid")
            settlement_path = Path(td) / "settlements.parquet"
            pq.write_table(pa.table({
                "slug": ["x"], "horizon": [5], "anchor_ts": [anchor],
                "anchor_price": [60_000.0], "expiry_btc": [59_990.0],
                "settled_side": [0], "up_win": [0], "down_win": [1],
                "resolution_source": ["polymarket_clob"], "resolved_at": [anchor + 310.0],
            }), settlement_path)
            result = database.reconcile_official_polymarket_settlements(str(settlement_path))
            assert result["rounds"] == 1 and result["trades"] == 2
            conn = database._connect()
            values = dict(conn.execute(
                "SELECT rule, pnl FROM rule_paper_trades WHERE round_id=?", (official_id,)
            ).fetchall())
            official = conn.execute(
                "SELECT actual_direction, hit, settlement_source FROM price_to_beat WHERE id=?",
                (official_id,)).fetchone()
            conn.close()
            assert abs(values["HOLD"] - (-.6168)) < 1e-9
            assert abs(values["EARLY"] - (.60 - .0168 - .50 - .0175)) < 1e-9
            assert official == ("DOWN", False, "official:polymarket_clob")

            # Serving never substitutes a 5m path model for a missing 15m head.
            assert _predict_path_plan(
                {"horizons": {5: {}}, "threshold_units": "usd"}, 15, {}, 60_000
            ) is None

            # One-share paper candidates require at least one displayed share.
            round_data = {
                "current_price": 60_010, "current_position": "UP", "current_move": 20,
                "seconds_left": 30, "live_lean": "UP", "lean_source": "model",
                "p_hold": .95, "big_drop_risk": "LOW", "big_move_tier": "moderate",
                "activity_tier": "moderate", "regime": "RANGE", "tier": "T3",
            }
            verdict = decision_champion.champion_decision(round_data, {
                "ask": .50, "spread": .01, "depth": .5,
                "fees_enabled": True, "fee_rate": .07,
            })
            assert verdict["action"] == "NO_EDGE" and "depth" in verdict["label"].lower()
        finally:
            if database._ANCHOR_CONN is not None:
                database._ANCHOR_CONN.close()
            database._ANCHOR_CONN = old_anchor
            database.DB_PATH = old_path
            action_recorder_module._RECORDER = old_action_recorder
    print("paper-trading-integrity: PASS")


if __name__ == "__main__":
    run()
