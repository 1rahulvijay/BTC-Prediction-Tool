"""Append-only DuckDB evidence ledger for complete-set arbitrage shadowing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

DDL = """
CREATE TABLE IF NOT EXISTS complete_set_meta(
    key VARCHAR PRIMARY KEY,
    value_json VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS complete_set_markets(
    slug VARCHAR PRIMARY KEY,
    condition_id VARCHAR,
    horizon INTEGER,
    start_ts DOUBLE,
    end_ts DOUBLE,
    up_asset_id VARCHAR,
    down_asset_id VARCHAR,
    up_base_fee_bps INTEGER,
    down_base_fee_bps INTEGER,
    up_min_order_size DOUBLE,
    down_min_order_size DOUBLE,
    up_tick_size DOUBLE,
    down_tick_size DOUBLE,
    neg_risk BOOLEAN,
    fee_fetched_at DOUBLE,
    last_seen_at DOUBLE
);
CREATE TABLE IF NOT EXISTS complete_set_snapshots(
    snapshot_id VARCHAR PRIMARY KEY,
    observed_ts_ns BIGINT,
    slug VARCHAR,
    condition_id VARCHAR,
    horizon INTEGER,
    seconds_left DOUBLE,
    qualified BOOLEAN,
    reject_reason VARCHAR,
    up_recv_age_ms DOUBLE,
    down_recv_age_ms DOUBLE,
    receive_skew_ms DOUBLE,
    exchange_skew_ms DOUBLE,
    up_book_hash VARCHAR,
    down_book_hash VARCHAR,
    up_base_fee_bps INTEGER,
    down_base_fee_bps INTEGER,
    raw_buy_gap_usd DOUBLE,
    raw_sell_gap_usd DOUBLE,
    size_results_json VARCHAR,
    buy_capacity_json VARCHAR,
    sell_capacity_json VARCHAR
);
CREATE TABLE IF NOT EXISTS complete_set_opportunities(
    opportunity_id VARCHAR PRIMARY KEY,
    direction VARCHAR,
    quantity DOUBLE,
    slug VARCHAR,
    condition_id VARCHAR,
    horizon INTEGER,
    started_ts_ns BIGINT,
    ended_ts_ns BIGINT,
    duration_ms DOUBLE,
    first_snapshot_id VARCHAR,
    last_snapshot_id VARCHAR,
    entry_raw_net_usd DOUBLE,
    entry_conservative_net_usd DOUBLE,
    maximum_conservative_net_usd DOUBLE,
    execution_class VARCHAR,
    promotion_eligible BOOLEAN,
    status VARCHAR,
    close_reason VARCHAR,
    entry_evaluation_json VARCHAR
);
CREATE TABLE IF NOT EXISTS complete_set_delay_stress(
    opportunity_id VARCHAR,
    target_delay_ms INTEGER,
    actual_delay_ms DOUBLE,
    snapshot_id VARCHAR,
    qualified BOOLEAN,
    reject_reason VARCHAR,
    current_pair_net_usd DOUBLE,
    survives_positive BOOLEAN,
    up_first_net_usd DOUBLE,
    down_first_net_usd DOUBLE,
    failed_leg_worst_net_usd DOUBLE,
    evaluation_json VARCHAR,
    PRIMARY KEY(opportunity_id, target_delay_ms)
);
"""

MIGRATIONS = (
    "ALTER TABLE complete_set_markets ADD COLUMN IF NOT EXISTS up_min_order_size DOUBLE",
    "ALTER TABLE complete_set_markets ADD COLUMN IF NOT EXISTS down_min_order_size DOUBLE",
    "ALTER TABLE complete_set_markets ADD COLUMN IF NOT EXISTS up_tick_size DOUBLE",
    "ALTER TABLE complete_set_markets ADD COLUMN IF NOT EXISTS down_tick_size DOUBLE",
    "ALTER TABLE complete_set_markets ADD COLUMN IF NOT EXISTS neg_risk BOOLEAN",
)


class CompleteSetStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = duckdb.connect(str(path))
        self.conn.execute(DDL)
        for statement in MIGRATIONS:
            self.conn.execute(statement)

    def close(self) -> None:
        self.conn.close()

    def disk_bytes(self) -> int:
        return sum(
            Path(candidate).stat().st_size
            for candidate in (self.path, f"{self.path}.wal")
            if Path(candidate).exists()
        )

    def set_meta(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO complete_set_meta VALUES (?, ?)",
            [key, json.dumps(value, sort_keys=True, default=str)],
        )

    def market(self, row: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO complete_set_markets (
                slug, condition_id, horizon, start_ts, end_ts,
                up_asset_id, down_asset_id,
                up_base_fee_bps, down_base_fee_bps,
                up_min_order_size, down_min_order_size,
                up_tick_size, down_tick_size, neg_risk,
                fee_fetched_at, last_seen_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(slug) DO UPDATE SET
                condition_id=excluded.condition_id,
                horizon=excluded.horizon,
                start_ts=excluded.start_ts,
                end_ts=excluded.end_ts,
                up_asset_id=excluded.up_asset_id,
                down_asset_id=excluded.down_asset_id,
                up_base_fee_bps=excluded.up_base_fee_bps,
                down_base_fee_bps=excluded.down_base_fee_bps,
                up_min_order_size=excluded.up_min_order_size,
                down_min_order_size=excluded.down_min_order_size,
                up_tick_size=excluded.up_tick_size,
                down_tick_size=excluded.down_tick_size,
                neg_risk=excluded.neg_risk,
                fee_fetched_at=excluded.fee_fetched_at,
                last_seen_at=excluded.last_seen_at
            """,
            [
                row["slug"],
                row["condition_id"],
                row["horizon"],
                row["start_ts"],
                row["end_ts"],
                row["up"],
                row["down"],
                row.get("up_base_fee_bps"),
                row.get("down_base_fee_bps"),
                row.get("up_min_order_size"),
                row.get("down_min_order_size"),
                row.get("up_tick_size"),
                row.get("down_tick_size"),
                row.get("neg_risk"),
                row.get("fee_fetched_at"),
                row["last_seen_at"],
            ],
        )

    def snapshot(self, row: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO complete_set_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                row["snapshot_id"],
                row["observed_ts_ns"],
                row["slug"],
                row["condition_id"],
                row["horizon"],
                row["seconds_left"],
                row["qualified"],
                row.get("reject_reason"),
                row["up_recv_age_ms"],
                row["down_recv_age_ms"],
                row["receive_skew_ms"],
                row["exchange_skew_ms"],
                row.get("up_book_hash"),
                row.get("down_book_hash"),
                row.get("up_base_fee_bps"),
                row.get("down_base_fee_bps"),
                row.get("raw_buy_gap_usd"),
                row.get("raw_sell_gap_usd"),
                json.dumps(row["size_results"], separators=(",", ":"), sort_keys=True),
                json.dumps(row["buy_capacity"], separators=(",", ":"), sort_keys=True),
                json.dumps(row["sell_capacity"], separators=(",", ":"), sort_keys=True),
            ],
        )

    def open_opportunity(self, row: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT INTO complete_set_opportunities VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                row["opportunity_id"],
                row["direction"],
                row["quantity"],
                row["slug"],
                row["condition_id"],
                row["horizon"],
                row["started_ts_ns"],
                None,
                None,
                row["first_snapshot_id"],
                row["first_snapshot_id"],
                row["entry_raw_net_usd"],
                row["entry_conservative_net_usd"],
                row["entry_conservative_net_usd"],
                row["execution_class"],
                row["promotion_eligible"],
                "OPEN",
                None,
                json.dumps(
                    row["entry_evaluation"],
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ],
        )

    def touch_opportunity(
        self,
        opportunity_id: str,
        snapshot_id: str,
        conservative_net_usd: float,
    ) -> None:
        self.conn.execute(
            """
            UPDATE complete_set_opportunities
            SET last_snapshot_id=?,
                maximum_conservative_net_usd=greatest(
                    maximum_conservative_net_usd, ?
                )
            WHERE opportunity_id=?
            """,
            [snapshot_id, conservative_net_usd, opportunity_id],
        )

    def close_opportunity(
        self,
        opportunity_id: str,
        ended_ts_ns: int,
        snapshot_id: str,
        reason: str,
    ) -> None:
        self.conn.execute(
            """
            UPDATE complete_set_opportunities
            SET ended_ts_ns=?,
                duration_ms=(? - started_ts_ns) / 1000000.0,
                last_snapshot_id=?,
                status='CLOSED',
                close_reason=?
            WHERE opportunity_id=? AND status='OPEN'
            """,
            [ended_ts_ns, ended_ts_ns, snapshot_id, reason, opportunity_id],
        )

    def delay_stress(self, row: dict[str, Any]) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO complete_set_delay_stress VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                row["opportunity_id"],
                row["target_delay_ms"],
                row["actual_delay_ms"],
                row["snapshot_id"],
                row["qualified"],
                row.get("reject_reason"),
                row.get("current_pair_net_usd"),
                row["survives_positive"],
                row.get("up_first_net_usd"),
                row.get("down_first_net_usd"),
                row.get("failed_leg_worst_net_usd"),
                json.dumps(
                    row["evaluation"], separators=(",", ":"), sort_keys=True
                ),
            ],
        )

    def counts(self) -> dict[str, int]:
        names = (
            "complete_set_markets",
            "complete_set_snapshots",
            "complete_set_opportunities",
            "complete_set_delay_stress",
        )
        return {
            name: int(self.conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0])
            for name in names
        }
