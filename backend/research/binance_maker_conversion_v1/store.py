"""Append-preserving DuckDB evidence store for maker-conversion shadowing."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import duckdb

from binance_maker_conversion_v1.simulator import Route


class EvidenceStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = duckdb.connect(str(self.path))
        self._create_schema()
        self._recover_interrupted()

    def _create_schema(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS campaign_meta (
                key VARCHAR PRIMARY KEY,
                value_json VARCHAR NOT NULL,
                updated_ts_ms BIGINT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS health_events (
                ts_ms BIGINT NOT NULL,
                component VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                message VARCHAR,
                sequence_gap_count BIGINT,
                clock_drift_ms BIGINT,
                book_age_ms BIGINT
            );
            CREATE TABLE IF NOT EXISTS candidates (
                candidate_id VARCHAR PRIMARY KEY,
                decision_second BIGINT NOT NULL,
                decision_ts_ms BIGINT NOT NULL,
                horizon_seconds INTEGER NOT NULL,
                side VARCHAR NOT NULL,
                p_direction DOUBLE NOT NULL,
                p_movement DOUBLE NOT NULL,
                p_roundtrip DOUBLE,
                model_margin DOUBLE NOT NULL,
                quantity DOUBLE NOT NULL,
                notional_usd DOUBLE NOT NULL,
                best_bid DOUBLE NOT NULL,
                best_ask DOUBLE NOT NULL,
                bid_quantity DOUBLE NOT NULL,
                ask_quantity DOUBLE NOT NULL,
                spread_bps DOUBLE NOT NULL,
                book_update_id BIGINT NOT NULL,
                book_event_ts_ms BIGINT NOT NULL,
                book_received_ts_ms BIGINT NOT NULL,
                book_age_ms BIGINT NOT NULL,
                protocol_hash VARCHAR NOT NULL,
                source_protocol_hash VARCHAR NOT NULL,
                model_bundle_hash VARCHAR NOT NULL,
                dataset_sha256 VARCHAR NOT NULL,
                training_cutoff_ns BIGINT NOT NULL,
                feature_schema_hash VARCHAR NOT NULL,
                code_commit VARCHAR NOT NULL,
                code_dirty BOOLEAN NOT NULL,
                created_ts_ms BIGINT NOT NULL,
                resolution_status VARCHAR NOT NULL DEFAULT 'OPEN',
                UNIQUE(decision_second, horizon_seconds)
            );
            CREATE TABLE IF NOT EXISTS routes (
                candidate_id VARCHAR NOT NULL,
                policy VARCHAR NOT NULL,
                side VARCHAR NOT NULL,
                horizon_seconds INTEGER NOT NULL,
                status VARCHAR NOT NULL,
                reason VARCHAR,
                quantity DOUBLE NOT NULL,
                entry_status VARCHAR NOT NULL,
                entry_price DOUBLE,
                entry_ts_ms BIGINT,
                entry_liquidity VARCHAR,
                entry_filled_quantity DOUBLE NOT NULL,
                entry_maker_quantity DOUBLE NOT NULL DEFAULT 0,
                entry_taker_quantity DOUBLE NOT NULL DEFAULT 0,
                entry_maker_notional DOUBLE NOT NULL DEFAULT 0,
                entry_taker_notional DOUBLE NOT NULL DEFAULT 0,
                entry_queue_ahead DOUBLE,
                entry_traded_through DOUBLE,
                exit_status VARCHAR NOT NULL,
                exit_price DOUBLE,
                exit_ts_ms BIGINT,
                exit_liquidity VARCHAR,
                exit_filled_quantity DOUBLE NOT NULL,
                exit_maker_quantity DOUBLE NOT NULL DEFAULT 0,
                exit_taker_quantity DOUBLE NOT NULL DEFAULT 0,
                exit_maker_notional DOUBLE NOT NULL DEFAULT 0,
                exit_taker_notional DOUBLE NOT NULL DEFAULT 0,
                exit_queue_ahead DOUBLE,
                exit_traded_through DOUBLE,
                gross_bps DOUBLE,
                fee_bps DOUBLE,
                net_bps DOUBLE,
                candidate_weighted_net_bps DOUBLE,
                updated_ts_ms BIGINT NOT NULL,
                PRIMARY KEY(candidate_id, policy)
            );
            CREATE TABLE IF NOT EXISTS queue_checkpoints (
                candidate_id VARCHAR NOT NULL,
                policy VARCHAR NOT NULL,
                leg VARCHAR NOT NULL,
                checkpoint_ms INTEGER NOT NULL,
                observed_ts_ms BIGINT NOT NULL,
                order_price DOUBLE,
                queue_ahead DOUBLE,
                traded_through DOUBLE,
                filled_quantity DOUBLE,
                fill_fraction DOUBLE,
                best_bid DOUBLE,
                best_ask DOUBLE,
                mid DOUBLE,
                book_update_id BIGINT,
                PRIMARY KEY(candidate_id, policy, leg, checkpoint_ms)
            );
            CREATE TABLE IF NOT EXISTS post_fill_marks (
                candidate_id VARCHAR NOT NULL,
                policy VARCHAR NOT NULL,
                leg VARCHAR NOT NULL,
                offset_ms INTEGER NOT NULL,
                fill_ts_ms BIGINT NOT NULL,
                observed_ts_ms BIGINT NOT NULL,
                fill_price DOUBLE NOT NULL,
                mid DOUBLE NOT NULL,
                signed_move_bps DOUBLE NOT NULL,
                PRIMARY KEY(candidate_id, policy, leg, offset_ms)
            );
            CREATE TABLE IF NOT EXISTS candidate_book_checkpoints (
                candidate_id VARCHAR NOT NULL,
                offset_ms INTEGER NOT NULL,
                observed_ts_ms BIGINT NOT NULL,
                best_bid DOUBLE,
                best_ask DOUBLE,
                mid DOUBLE,
                spread_bps DOUBLE,
                long_vwap_1x DOUBLE,
                long_fill_1x DOUBLE,
                short_vwap_1x DOUBLE,
                short_fill_1x DOUBLE,
                long_vwap_2x DOUBLE,
                long_fill_2x DOUBLE,
                short_vwap_2x DOUBLE,
                short_fill_2x DOUBLE,
                book_update_id BIGINT,
                book_age_ms BIGINT,
                PRIMARY KEY(candidate_id, offset_ms)
            );
            """
        )
        for name in (
            "entry_maker_quantity",
            "entry_taker_quantity",
            "entry_maker_notional",
            "entry_taker_notional",
            "exit_maker_quantity",
            "exit_taker_quantity",
            "exit_maker_notional",
            "exit_taker_notional",
        ):
            self.db.execute(
                f"ALTER TABLE routes ADD COLUMN IF NOT EXISTS {name} "
                "DOUBLE DEFAULT 0"
            )
        candidate_migrations = {
            "source_protocol_hash": "VARCHAR",
            "dataset_sha256": "VARCHAR",
            "training_cutoff_ns": "BIGINT",
            "code_dirty": "BOOLEAN",
        }
        for name, definition in candidate_migrations.items():
            self.db.execute(
                f"ALTER TABLE candidates ADD COLUMN IF NOT EXISTS "
                f"{name} {definition}"
            )

    def _recover_interrupted(self) -> None:
        now = int(time.time() * 1000)
        self.db.execute(
            """
            UPDATE candidates
            SET resolution_status = 'INTERRUPTED'
            WHERE resolution_status = 'OPEN'
            """
        )
        self.db.execute(
            """
            UPDATE routes
            SET status = 'INTERRUPTED',
                reason = COALESCE(reason, 'process_restarted_before_resolution'),
                updated_ts_ms = ?
            WHERE status = 'ACTIVE'
            """,
            [now],
        )

    def set_meta(self, key: str, value: Any) -> None:
        self.db.execute(
            """
            INSERT INTO campaign_meta VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET
                value_json = excluded.value_json,
                updated_ts_ms = excluded.updated_ts_ms
            """,
            [key, json.dumps(value, sort_keys=True, default=str), int(time.time() * 1000)],
        )

    def health(
        self,
        component: str,
        status: str,
        message: str = "",
        *,
        sequence_gap_count: int | None = None,
        clock_drift_ms: int | None = None,
        book_age_ms: int | None = None,
    ) -> None:
        self.db.execute(
            "INSERT INTO health_events VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                int(time.time() * 1000),
                component,
                status,
                message[:1000],
                sequence_gap_count,
                clock_drift_ms,
                book_age_ms,
            ],
        )

    def candidate(self, row: dict[str, Any]) -> bool:
        result = self.db.execute(
            """
            INSERT INTO candidates (
                candidate_id, decision_second, decision_ts_ms, horizon_seconds,
                side, p_direction, p_movement, p_roundtrip, model_margin,
                quantity, notional_usd, best_bid, best_ask, bid_quantity,
                ask_quantity, spread_bps, book_update_id, book_event_ts_ms,
                book_received_ts_ms, book_age_ms, protocol_hash,
                source_protocol_hash, model_bundle_hash, dataset_sha256,
                training_cutoff_ns, feature_schema_hash, code_commit, code_dirty,
                created_ts_ms, resolution_status
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN'
            )
            ON CONFLICT DO NOTHING
            RETURNING candidate_id
            """,
            [
                row["candidate_id"],
                row["decision_second"],
                row["decision_ts_ms"],
                row["horizon_seconds"],
                row["side"],
                row["p_direction"],
                row["p_movement"],
                row.get("p_roundtrip"),
                row["model_margin"],
                row["quantity"],
                row["notional_usd"],
                row["best_bid"],
                row["best_ask"],
                row["bid_quantity"],
                row["ask_quantity"],
                row["spread_bps"],
                row["book_update_id"],
                row["book_event_ts_ms"],
                row["book_received_ts_ms"],
                row["book_age_ms"],
                row["protocol_hash"],
                row["source_protocol_hash"],
                row["model_bundle_hash"],
                row["dataset_sha256"],
                row["training_cutoff_ns"],
                row["feature_schema_hash"],
                row["code_commit"],
                row["code_dirty"],
                row["created_ts_ms"],
            ],
        ).fetchone()
        return result is not None

    def route(self, route: Route, economics: dict[str, Any]) -> None:
        entry_queue = route.entry_queue
        exit_queue = route.exit_queue
        self.db.execute(
            """
            INSERT INTO routes (
                candidate_id, policy, side, horizon_seconds, status, reason,
                quantity, entry_status, entry_price, entry_ts_ms,
                entry_liquidity, entry_filled_quantity, entry_maker_quantity,
                entry_taker_quantity, entry_maker_notional,
                entry_taker_notional, entry_queue_ahead,
                entry_traded_through, exit_status, exit_price, exit_ts_ms,
                exit_liquidity, exit_filled_quantity, exit_maker_quantity,
                exit_taker_quantity, exit_maker_notional,
                exit_taker_notional, exit_queue_ahead,
                exit_traded_through, gross_bps, fee_bps, net_bps,
                candidate_weighted_net_bps, updated_ts_ms
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            ON CONFLICT(candidate_id, policy) DO UPDATE SET
                status = excluded.status,
                reason = excluded.reason,
                entry_status = excluded.entry_status,
                entry_price = excluded.entry_price,
                entry_ts_ms = excluded.entry_ts_ms,
                entry_liquidity = excluded.entry_liquidity,
                entry_filled_quantity = excluded.entry_filled_quantity,
                entry_maker_quantity = excluded.entry_maker_quantity,
                entry_taker_quantity = excluded.entry_taker_quantity,
                entry_maker_notional = excluded.entry_maker_notional,
                entry_taker_notional = excluded.entry_taker_notional,
                entry_queue_ahead = excluded.entry_queue_ahead,
                entry_traded_through = excluded.entry_traded_through,
                exit_status = excluded.exit_status,
                exit_price = excluded.exit_price,
                exit_ts_ms = excluded.exit_ts_ms,
                exit_liquidity = excluded.exit_liquidity,
                exit_filled_quantity = excluded.exit_filled_quantity,
                exit_maker_quantity = excluded.exit_maker_quantity,
                exit_taker_quantity = excluded.exit_taker_quantity,
                exit_maker_notional = excluded.exit_maker_notional,
                exit_taker_notional = excluded.exit_taker_notional,
                exit_queue_ahead = excluded.exit_queue_ahead,
                exit_traded_through = excluded.exit_traded_through,
                gross_bps = excluded.gross_bps,
                fee_bps = excluded.fee_bps,
                net_bps = excluded.net_bps,
                candidate_weighted_net_bps = excluded.candidate_weighted_net_bps,
                updated_ts_ms = excluded.updated_ts_ms
            """,
            [
                route.candidate_id,
                route.policy,
                route.side,
                route.horizon_seconds,
                route.status,
                route.reason,
                route.quantity,
                route.entry_status,
                route.entry_price,
                route.entry_ts_ms,
                route.entry_liquidity,
                route.entry_filled_quantity,
                route.entry_maker_quantity,
                route.entry_taker_quantity,
                route.entry_maker_notional,
                route.entry_taker_notional,
                entry_queue.queue_ahead if entry_queue else None,
                entry_queue.traded_through_quantity if entry_queue else None,
                route.exit_status,
                route.exit_price,
                route.exit_ts_ms,
                route.exit_liquidity,
                route.exit_filled_quantity,
                route.exit_maker_quantity,
                route.exit_taker_quantity,
                route.exit_maker_notional,
                route.exit_taker_notional,
                exit_queue.queue_ahead if exit_queue else None,
                exit_queue.traded_through_quantity if exit_queue else None,
                economics.get("gross_bps"),
                economics.get("fee_bps"),
                economics.get("net_bps"),
                economics.get("candidate_weighted_net_bps"),
                int(time.time() * 1000),
            ],
        )

    def checkpoint(
        self,
        route: Route,
        leg: str,
        checkpoint_ms: int,
        observed_ts_ms: int,
        book: Any,
    ) -> None:
        queue = route.entry_queue if leg == "ENTRY" else route.exit_queue
        if queue is None:
            return
        top = book.top()
        self.db.execute(
            """
            INSERT INTO queue_checkpoints VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            ) ON CONFLICT DO NOTHING
            """,
            [
                route.candidate_id,
                route.policy,
                leg,
                checkpoint_ms,
                observed_ts_ms,
                queue.price,
                queue.queue_ahead,
                queue.traded_through_quantity,
                queue.filled_quantity,
                queue.fill_fraction,
                top.best_bid if top else None,
                top.best_ask if top else None,
                top.mid if top else None,
                top.update_id if top else None,
            ],
        )

    def post_fill_mark(
        self,
        route: Route,
        leg: str,
        offset_ms: int,
        observed_ts_ms: int,
        mid: float,
    ) -> None:
        fill_ts = route.entry_ts_ms if leg == "ENTRY" else route.exit_ts_ms
        fill_price = route.entry_price if leg == "ENTRY" else route.exit_price
        if fill_ts is None or fill_price is None:
            return
        buy = route.long if leg == "ENTRY" else not route.long
        direction = 1.0 if buy else -1.0
        signed = direction * (mid / fill_price - 1.0) * 10_000.0
        self.db.execute(
            """
            INSERT INTO post_fill_marks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            [
                route.candidate_id,
                route.policy,
                leg,
                offset_ms,
                fill_ts,
                observed_ts_ms,
                fill_price,
                mid,
                signed,
            ],
        )

    def candidate_book_checkpoint(
        self,
        candidate_id: str,
        offset_ms: int,
        observed_ts_ms: int,
        quantity: float,
        book: Any,
    ) -> None:
        top = book.top()
        if top is None:
            return
        long_1x, long_fill_1x = book.walk(True, quantity)
        short_1x, short_fill_1x = book.walk(False, quantity)
        long_2x, long_fill_2x = book.walk(True, quantity * 2.0)
        short_2x, short_fill_2x = book.walk(False, quantity * 2.0)
        self.db.execute(
            """
            INSERT INTO candidate_book_checkpoints VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            ) ON CONFLICT DO NOTHING
            """,
            [
                candidate_id,
                offset_ms,
                observed_ts_ms,
                top.best_bid,
                top.best_ask,
                top.mid,
                top.spread_bps,
                long_1x,
                long_fill_1x,
                short_1x,
                short_fill_1x,
                long_2x,
                long_fill_2x,
                short_2x,
                short_fill_2x,
                top.update_id,
                max(0, observed_ts_ms - top.received_ts_ms),
            ],
        )

    def resolve_candidate(self, candidate_id: str, status: str) -> None:
        self.db.execute(
            "UPDATE candidates SET resolution_status = ? WHERE candidate_id = ?",
            [status, candidate_id],
        )

    def close(self) -> None:
        self.db.close()
