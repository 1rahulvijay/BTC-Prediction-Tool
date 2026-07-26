"""Dedicated DuckDB ledger for complete-trade shadow forecasts and outcomes."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

import duckdb


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
# Separate from analytics.duckdb and the recorder-owned execution_layer.duckdb.
DB_PATH = Path(
    os.environ.get("BTC_COMPLETE_TRADE_DB")
    or DATA / "complete_trade_forecast.duckdb"
)


def connect(path: Path | str | None = None) -> duckdb.DuckDBPyConnection:
    target = Path(path or DB_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(target))


def init_schema(conn: duckdb.DuckDBPyConnection | None = None):
    own = conn is None
    conn = conn or connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS complete_trade_forecasts (
            forecast_id VARCHAR PRIMARY KEY,
            snapshot_id VARCHAR,
            decision_ts BIGINT,
            round_id VARCHAR,
            horizon INTEGER,
            price_to_beat DOUBLE,
            current_btc DOUBLE,
            side VARCHAR,
            seconds_left DOUBLE,
            requested_qty DOUBLE,
            entry_ask DOUBLE,
            predicted_entry_vwap DOUBLE,
            predicted_entry_fee DOUBLE,
            break_even_bid DOUBLE,
            target_bid DOUBLE,
            stop_bid DOUBLE,
            p_ever_profitable DOUBLE,
            p_lockable_profit DOUBLE,
            p_target_before_stop DOUBLE,
            p_settlement_win DOUBLE,
            predicted_mfe DOUBLE,
            predicted_mae DOUBLE,
            predicted_first_profitable_s DOUBLE,
            pnl_q10 DOUBLE,
            pnl_q25 DOUBLE,
            pnl_q50 DOUBLE,
            pnl_q75 DOUBLE,
            pnl_q90 DOUBLE,
            expected_pnl DOUBLE,
            cvar DOUBLE,
            recommended_action VARCHAR,
            recommended_exit_plan VARCHAR,
            reason_codes_json VARCHAR,
            model_hash VARCHAR,
            feature_hash VARCHAR,
            policy_hash VARCHAR,
            mode VARCHAR,
            evidence_status VARCHAR,
            created_at BIGINT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS complete_trade_path_predictions (
            forecast_id VARCHAR,
            offset_seconds INTEGER,
            btc_q10 DOUBLE,
            btc_q25 DOUBLE,
            btc_q50 DOUBLE,
            btc_q75 DOUBLE,
            btc_q90 DOUBLE,
            share_bid_q10 DOUBLE,
            share_bid_q25 DOUBLE,
            share_bid_q50 DOUBLE,
            share_bid_q75 DOUBLE,
            share_bid_q90 DOUBLE,
            p_break_even_cross DOUBLE,
            p_target_cross DOUBLE,
            p_stop_cross DOUBLE,
            PRIMARY KEY(forecast_id, offset_seconds)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS complete_trade_checkpoints (
            forecast_id VARCHAR,
            checkpoint_ts BIGINT,
            actual_btc DOUBLE,
            actual_bid DOUBLE,
            actual_full_qty_vwap DOUBLE,
            actual_net_pnl DOUBLE,
            updated_p_profit DOUBLE,
            updated_expected_pnl DOUBLE,
            updated_cvar DOUBLE,
            updated_action VARCHAR,
            action_changed BOOLEAN,
            change_reason VARCHAR,
            PRIMARY KEY(forecast_id, checkpoint_ts)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS complete_trade_outcomes (
            forecast_id VARCHAR PRIMARY KEY,
            actual_entry_vwap DOUBLE,
            actual_entry_latency DOUBLE,
            actual_entry_fee DOUBLE,
            actual_first_profitable_s DOUBLE,
            actual_mfe DOUBLE,
            actual_mae DOUBLE,
            actual_exit_vwap DOUBLE,
            actual_exit_fee DOUBLE,
            actual_holding_s DOUBLE,
            actual_net_pnl DOUBLE,
            predicted_error DOUBLE,
            target_hit BOOLEAN,
            stop_hit BOOLEAN,
            settlement_outcome VARCHAR,
            official_resolution_source VARCHAR,
            failure_component VARCHAR,
            error_details_json VARCHAR,
            resolved_at BIGINT
        )
        """
    )
    return conn if own else None


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def log_forecast(
    forecast: dict[str, Any],
    paths: list[dict[str, Any]] | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        init_schema(conn)
        columns = [
            "forecast_id",
            "snapshot_id",
            "decision_ts",
            "round_id",
            "horizon",
            "price_to_beat",
            "current_btc",
            "side",
            "seconds_left",
            "requested_qty",
            "entry_ask",
            "predicted_entry_vwap",
            "predicted_entry_fee",
            "break_even_bid",
            "target_bid",
            "stop_bid",
            "p_ever_profitable",
            "p_lockable_profit",
            "p_target_before_stop",
            "p_settlement_win",
            "predicted_mfe",
            "predicted_mae",
            "predicted_first_profitable_s",
            "pnl_q10",
            "pnl_q25",
            "pnl_q50",
            "pnl_q75",
            "pnl_q90",
            "expected_pnl",
            "cvar",
            "recommended_action",
            "recommended_exit_plan",
            "reason_codes_json",
            "model_hash",
            "feature_hash",
            "policy_hash",
            "mode",
            "evidence_status",
            "created_at",
        ]
        values = [
            _json(forecast.get("reason_codes") or [])
            if column == "reason_codes_json"
            else forecast.get(column)
            for column in columns
        ]
        conn.execute("BEGIN TRANSACTION")
        conn.execute(
            f"INSERT OR REPLACE INTO complete_trade_forecasts "
            f"({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            values,
        )
        path_columns = [
            "forecast_id",
            "offset_seconds",
            "btc_q10",
            "btc_q25",
            "btc_q50",
            "btc_q75",
            "btc_q90",
            "share_bid_q10",
            "share_bid_q25",
            "share_bid_q50",
            "share_bid_q75",
            "share_bid_q90",
            "p_break_even_cross",
            "p_target_cross",
            "p_stop_cross",
        ]
        for path in paths or []:
            row = {**path, "forecast_id": forecast["forecast_id"]}
            conn.execute(
                f"INSERT OR REPLACE INTO complete_trade_path_predictions "
                f"({','.join(path_columns)}) VALUES ({','.join('?' for _ in path_columns)})",
                [row.get(column) for column in path_columns],
            )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        if own:
            conn.close()


def log_checkpoint(
    checkpoint: dict[str, Any],
    conn: duckdb.DuckDBPyConnection | None = None,
) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        init_schema(conn)
        columns = [
            "forecast_id",
            "checkpoint_ts",
            "actual_btc",
            "actual_bid",
            "actual_full_qty_vwap",
            "actual_net_pnl",
            "updated_p_profit",
            "updated_expected_pnl",
            "updated_cvar",
            "updated_action",
            "action_changed",
            "change_reason",
        ]
        conn.execute(
            f"INSERT OR REPLACE INTO complete_trade_checkpoints "
            f"({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            [checkpoint.get(column) for column in columns],
        )
    finally:
        if own:
            conn.close()


def log_outcome(
    outcome: dict[str, Any],
    conn: duckdb.DuckDBPyConnection | None = None,
) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        init_schema(conn)
        columns = [
            "forecast_id",
            "actual_entry_vwap",
            "actual_entry_latency",
            "actual_entry_fee",
            "actual_first_profitable_s",
            "actual_mfe",
            "actual_mae",
            "actual_exit_vwap",
            "actual_exit_fee",
            "actual_holding_s",
            "actual_net_pnl",
            "predicted_error",
            "target_hit",
            "stop_hit",
            "settlement_outcome",
            "official_resolution_source",
            "failure_component",
            "error_details_json",
            "resolved_at",
        ]
        values = [
            _json(outcome.get("error_details") or {})
            if column == "error_details_json"
            else outcome.get(column)
            for column in columns
        ]
        conn.execute(
            f"INSERT OR REPLACE INTO complete_trade_outcomes "
            f"({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            values,
        )
    finally:
        if own:
            conn.close()


def status(conn: duckdb.DuckDBPyConnection | None = None) -> dict[str, int]:
    own = conn is None
    conn = conn or connect()
    try:
        init_schema(conn)
        return {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "complete_trade_forecasts",
                "complete_trade_path_predictions",
                "complete_trade_checkpoints",
                "complete_trade_outcomes",
            )
        }
    finally:
        if own:
            conn.close()


def selftest() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "test.duckdb"
        conn = connect(path)
        init_schema(conn)
        forecast = {
            "forecast_id": "f1",
            "decision_ts": 1,
            "round_id": "r1",
            "horizon": 5,
            "side": "UP",
            "requested_qty": 10,
            "reason_codes": ["shadow"],
        }
        log_forecast(forecast, [{"offset_seconds": 5, "share_bid_q50": 0.6}], conn)
        log_checkpoint({"forecast_id": "f1", "checkpoint_ts": 2}, conn)
        log_outcome({"forecast_id": "f1", "actual_net_pnl": 0.01}, conn)
        counts = status(conn)
        assert counts["complete_trade_forecasts"] == 1
        assert counts["complete_trade_path_predictions"] == 1
        assert counts["complete_trade_checkpoints"] == 1
        assert counts["complete_trade_outcomes"] == 1
        conn.close()
    print("trade_forecast_logger self-test: ALL PASS")


if __name__ == "__main__":
    selftest()
