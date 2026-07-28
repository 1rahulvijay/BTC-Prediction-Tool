"""Append-preserving DuckDB evidence store for the 1h fair-value campaign."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import duckdb


class FairValueStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = duckdb.connect(str(self.path))
        self._create_schema()

    def _create_schema(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS campaign_meta(
                key VARCHAR PRIMARY KEY,
                value_json VARCHAR NOT NULL,
                updated_ts_ms BIGINT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS health_events(
                ts_ms BIGINT NOT NULL,
                component VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                message VARCHAR,
                age_ms BIGINT,
                latency_ms DOUBLE
            );
            CREATE TABLE IF NOT EXISTS hourly_markets(
                slug VARCHAR PRIMARY KEY,
                market_id VARCHAR NOT NULL,
                event_id VARCHAR,
                condition_id VARCHAR NOT NULL,
                up_token_id VARCHAR NOT NULL,
                down_token_id VARCHAR NOT NULL,
                candle_open_ts_ms BIGINT NOT NULL,
                candle_close_ts_ms BIGINT NOT NULL,
                gamma_start_ts_ms BIGINT,
                resolution_source VARCHAR NOT NULL,
                rule_text VARCHAR NOT NULL,
                rule_sha256 VARCHAR NOT NULL,
                gamma_fee_rate DOUBLE,
                gamma_fee_exponent DOUBLE,
                gamma_taker_only BOOLEAN,
                up_base_fee_bps INTEGER,
                down_base_fee_bps INTEGER,
                first_seen_ts_ms BIGINT NOT NULL,
                last_seen_ts_ms BIGINT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS hourly_snapshots(
                slug VARCHAR NOT NULL,
                observed_second BIGINT NOT NULL,
                observed_ts_ms BIGINT NOT NULL,
                candle_open_ts_ms BIGINT NOT NULL,
                candle_close_ts_ms BIGINT NOT NULL,
                seconds_elapsed DOUBLE NOT NULL,
                seconds_left DOUBLE NOT NULL,
                binance_open DOUBLE NOT NULL,
                binance_price DOUBLE NOT NULL,
                binance_distance_bps DOUBLE NOT NULL,
                binance_price_source VARCHAR NOT NULL,
                binance_source_ts_ms BIGINT,
                binance_age_ms BIGINT,
                slow_volatility DOUBLE NOT NULL,
                fast_volatility DOUBLE NOT NULL,
                jump_volatility DOUBLE NOT NULL,
                p_a_market DOUBLE,
                p_b_distance_time DOUBLE NOT NULL,
                p_c_volatility_mixture DOUBLE NOT NULL,
                up_bid DOUBLE NOT NULL,
                up_ask DOUBLE NOT NULL,
                up_mid DOUBLE NOT NULL,
                up_spread DOUBLE NOT NULL,
                up_bid_size DOUBLE NOT NULL,
                up_ask_size DOUBLE NOT NULL,
                down_bid DOUBLE NOT NULL,
                down_ask DOUBLE NOT NULL,
                down_mid DOUBLE NOT NULL,
                down_spread DOUBLE NOT NULL,
                down_bid_size DOUBLE NOT NULL,
                down_ask_size DOUBLE NOT NULL,
                up_book_ts_ms BIGINT,
                down_book_ts_ms BIGINT,
                up_receive_latency_ms DOUBLE NOT NULL,
                down_receive_latency_ms DOUBLE NOT NULL,
                pair_receive_skew_ms BIGINT NOT NULL,
                up_fee_rate DOUBLE NOT NULL,
                down_fee_rate DOUBLE NOT NULL,
                up_fee_at_ask DOUBLE NOT NULL,
                down_fee_at_ask DOUBLE NOT NULL,
                up_ladder_json VARCHAR NOT NULL,
                down_ladder_json VARCHAR NOT NULL,
                vwap_json VARCHAR NOT NULL,
                fraction_above DOUBLE NOT NULL,
                fraction_below DOUBLE NOT NULL,
                crossing_count INTEGER NOT NULL,
                crossing_rate_per_minute DOUBLE NOT NULL,
                seconds_since_crossing DOUBLE NOT NULL,
                average_residence_above DOUBLE NOT NULL,
                average_residence_below DOUBLE NOT NULL,
                longest_residence_above DOUBLE NOT NULL,
                longest_residence_below DOUBLE NOT NULL,
                maximum_above_bps DOUBLE NOT NULL,
                minimum_below_bps DOUBLE NOT NULL,
                drawdown_from_side_extreme_bps DOUBLE NOT NULL,
                velocity_15s_bps_per_second DOUBLE NOT NULL,
                velocity_60s_bps_per_second DOUBLE NOT NULL,
                valid BOOLEAN NOT NULL,
                invalid_reason VARCHAR,
                PRIMARY KEY(slug, observed_second)
            );
            CREATE TABLE IF NOT EXISTS hourly_resolutions(
                slug VARCHAR PRIMARY KEY,
                candle_open_ts_ms BIGINT NOT NULL,
                candle_close_ts_ms BIGINT NOT NULL,
                finalized_open DOUBLE,
                finalized_high DOUBLE,
                finalized_low DOUBLE,
                finalized_close DOUBLE,
                finalized_volume DOUBLE,
                binance_side VARCHAR,
                polymarket_side VARCHAR,
                polymarket_resolution_source VARCHAR,
                sides_match BOOLEAN,
                finalized_kline BOOLEAN NOT NULL,
                resolved_ts_ms BIGINT NOT NULL
            );
            """
        )

    def close(self) -> None:
        self.db.close()

    def set_meta(self, key: str, value: Any) -> None:
        self.db.execute(
            """
            INSERT INTO campaign_meta VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json=excluded.value_json,
                updated_ts_ms=excluded.updated_ts_ms
            """,
            [
                key,
                json.dumps(value, sort_keys=True, default=str),
                int(time.time() * 1000),
            ],
        )

    def health(
        self,
        component: str,
        status: str,
        message: str = "",
        *,
        age_ms: int | None = None,
        latency_ms: float | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO health_events VALUES (?, ?, ?, ?, ?, ?)",
            [
                int(time.time() * 1000),
                str(component),
                str(status),
                str(message)[:1000],
                age_ms,
                latency_ms,
            ],
        )

    def market(self, row: dict[str, Any]) -> None:
        self.db.execute(
            """
            INSERT INTO hourly_markets VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            ON CONFLICT(slug) DO UPDATE SET
                market_id=excluded.market_id,
                event_id=excluded.event_id,
                condition_id=excluded.condition_id,
                up_token_id=excluded.up_token_id,
                down_token_id=excluded.down_token_id,
                candle_open_ts_ms=excluded.candle_open_ts_ms,
                candle_close_ts_ms=excluded.candle_close_ts_ms,
                gamma_start_ts_ms=excluded.gamma_start_ts_ms,
                resolution_source=excluded.resolution_source,
                rule_text=excluded.rule_text,
                rule_sha256=excluded.rule_sha256,
                gamma_fee_rate=excluded.gamma_fee_rate,
                gamma_fee_exponent=excluded.gamma_fee_exponent,
                gamma_taker_only=excluded.gamma_taker_only,
                up_base_fee_bps=excluded.up_base_fee_bps,
                down_base_fee_bps=excluded.down_base_fee_bps,
                last_seen_ts_ms=excluded.last_seen_ts_ms
            """,
            [
                row["slug"],
                row["market_id"],
                row.get("event_id"),
                row["condition_id"],
                row["up_token_id"],
                row["down_token_id"],
                row["candle_open_ts_ms"],
                row["candle_close_ts_ms"],
                row.get("gamma_start_ts_ms"),
                row["resolution_source"],
                row["rule_text"],
                row["rule_sha256"],
                row.get("gamma_fee_rate"),
                row.get("gamma_fee_exponent"),
                row.get("gamma_taker_only"),
                row.get("up_base_fee_bps"),
                row.get("down_base_fee_bps"),
                row["first_seen_ts_ms"],
                row["last_seen_ts_ms"],
            ],
        )

    def snapshot(self, row: dict[str, Any]) -> bool:
        columns = [
            "slug",
            "observed_second",
            "observed_ts_ms",
            "candle_open_ts_ms",
            "candle_close_ts_ms",
            "seconds_elapsed",
            "seconds_left",
            "binance_open",
            "binance_price",
            "binance_distance_bps",
            "binance_price_source",
            "binance_source_ts_ms",
            "binance_age_ms",
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
            "up_book_ts_ms",
            "down_book_ts_ms",
            "up_receive_latency_ms",
            "down_receive_latency_ms",
            "pair_receive_skew_ms",
            "up_fee_rate",
            "down_fee_rate",
            "up_fee_at_ask",
            "down_fee_at_ask",
            "up_ladder_json",
            "down_ladder_json",
            "vwap_json",
            "fraction_above",
            "fraction_below",
            "crossing_count",
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
            "valid",
            "invalid_reason",
        ]
        result = self.db.execute(
            f"""
            INSERT INTO hourly_snapshots ({", ".join(columns)})
            VALUES ({", ".join("?" for _ in columns)})
            ON CONFLICT DO NOTHING
            RETURNING observed_second
            """,
            [row.get(column) for column in columns],
        ).fetchone()
        return result is not None

    def resolution(self, row: dict[str, Any]) -> None:
        self.db.execute(
            """
            INSERT INTO hourly_resolutions VALUES (
                ?,?,?,?,?,?,?,?,?,?,?,?,?,?
            )
            ON CONFLICT(slug) DO UPDATE SET
                finalized_open=excluded.finalized_open,
                finalized_high=excluded.finalized_high,
                finalized_low=excluded.finalized_low,
                finalized_close=excluded.finalized_close,
                finalized_volume=excluded.finalized_volume,
                binance_side=excluded.binance_side,
                polymarket_side=excluded.polymarket_side,
                polymarket_resolution_source=excluded.polymarket_resolution_source,
                sides_match=excluded.sides_match,
                finalized_kline=excluded.finalized_kline,
                resolved_ts_ms=excluded.resolved_ts_ms
            """,
            [
                row["slug"],
                row["candle_open_ts_ms"],
                row["candle_close_ts_ms"],
                row.get("finalized_open"),
                row.get("finalized_high"),
                row.get("finalized_low"),
                row.get("finalized_close"),
                row.get("finalized_volume"),
                row.get("binance_side"),
                row.get("polymarket_side"),
                row.get("polymarket_resolution_source"),
                row.get("sides_match"),
                bool(row.get("finalized_kline")),
                row["resolved_ts_ms"],
            ],
        )

    def unresolved_markets(self, before_ts_ms: int) -> list[dict[str, Any]]:
        rows = self.db.execute(
            """
            SELECT m.slug, m.condition_id, m.candle_open_ts_ms,
                   m.candle_close_ts_ms
            FROM hourly_markets m
            LEFT JOIN hourly_resolutions r USING(slug)
            WHERE m.candle_close_ts_ms <= ?
              AND (r.slug IS NULL OR NOT r.finalized_kline)
            ORDER BY m.candle_close_ts_ms
            """,
            [int(before_ts_ms)],
        ).fetchall()
        return [
            {
                "slug": row[0],
                "condition_id": row[1],
                "candle_open_ts_ms": int(row[2]),
                "candle_close_ts_ms": int(row[3]),
            }
            for row in rows
        ]

    def path_samples(self, slug: str) -> list[tuple[float, float]]:
        rows = self.db.execute(
            """
            SELECT observed_ts_ms, binance_price
            FROM hourly_snapshots
            WHERE slug=?
            ORDER BY observed_ts_ms
            """,
            [str(slug)],
        ).fetchall()
        return [(float(row[0]) / 1000.0, float(row[1])) for row in rows]

    def counts(self) -> dict[str, int]:
        tables = (
            "hourly_markets",
            "hourly_snapshots",
            "hourly_resolutions",
            "health_events",
        )
        return {
            table: int(self.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
            for table in tables
        }
