"""Append-only DuckDB ledger owned exclusively by the repricing shadow."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from polymarket_repricing_shadow_v1.routing import Candidate, RouteState

DDL = """
CREATE TABLE IF NOT EXISTS repricing_shadow_meta(
    key VARCHAR PRIMARY KEY,
    value VARCHAR
);
CREATE TABLE IF NOT EXISTS repricing_candidates(
    candidate_id VARCHAR PRIMARY KEY,
    decision_ts DOUBLE,
    market_id VARCHAR,
    condition_id VARCHAR,
    selected_side VARCHAR,
    quantity DOUBLE,
    current_bid DOUBLE,
    current_ask DOUBLE,
    spread DOUBLE,
    top_ask_depth DOUBLE,
    baseline_settlement_probability DOUBLE,
    baseline_expected_value DOUBLE,
    up_baseline_worsening_probability DOUBLE,
    up_worsening_probability DOUBLE,
    down_baseline_worsening_probability DOUBLE,
    down_worsening_probability DOUBLE,
    selected_worsening_probability DOUBLE,
    seconds_left DOUBLE,
    quote_age_seconds DOUBLE,
    event_probabilities_json VARCHAR,
    feature_values_json VARCHAR,
    ladder_json VARCHAR
);
CREATE TABLE IF NOT EXISTS repricing_routes(
    candidate_id VARCHAR,
    policy VARCHAR,
    routing_decision VARCHAR,
    proposed_limit DOUBLE,
    ttl_seconds INTEGER,
    requested_quantity DOUBLE,
    status VARCHAR,
    filled_quantity DOUBLE,
    average_price DOUBLE,
    fee DOUBLE,
    fill_time_seconds DOUBLE,
    fallback_used BOOLEAN,
    updated_at DOUBLE,
    PRIMARY KEY(candidate_id, policy)
);
CREATE TABLE IF NOT EXISTS repricing_observations(
    candidate_id VARCHAR,
    offset_seconds INTEGER,
    actual_elapsed_seconds DOUBLE,
    observed_ts DOUBLE,
    bid DOUBLE,
    ask DOUBLE,
    spread DOUBLE,
    top_ask_depth DOUBLE,
    ladder_json VARCHAR,
    PRIMARY KEY(candidate_id, offset_seconds)
);
CREATE TABLE IF NOT EXISTS repricing_settlements(
    candidate_id VARCHAR PRIMARY KEY,
    settled_side VARCHAR,
    resolution_source VARCHAR,
    resolved_at DOUBLE
);
"""

MIGRATIONS = (
    (
        "ALTER TABLE repricing_candidates ADD COLUMN IF NOT EXISTS "
        "up_baseline_worsening_probability DOUBLE"
    ),
    (
        "ALTER TABLE repricing_candidates ADD COLUMN IF NOT EXISTS "
        "down_baseline_worsening_probability DOUBLE"
    ),
    (
        "ALTER TABLE repricing_observations ADD COLUMN IF NOT EXISTS "
        "actual_elapsed_seconds DOUBLE"
    ),
)


class ShadowStore:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.conn = duckdb.connect(str(path))
        self.conn.execute(DDL)
        for statement in MIGRATIONS:
            self.conn.execute(statement)

    def close(self) -> None:
        self.conn.close()

    def set_meta(self, key: str, value: Any) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO repricing_shadow_meta VALUES (?, ?)",
            [key, json.dumps(value, sort_keys=True, default=str)],
        )

    def has_market(self, market_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM repricing_candidates WHERE market_id=? LIMIT 1", [market_id]
        ).fetchone()
        return row is not None

    def candidate(
        self,
        value: Candidate,
        up_baseline_probability: float,
        up_probability: float,
        down_baseline_probability: float,
        down_probability: float,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO repricing_candidates (
                candidate_id, decision_ts, market_id, condition_id, selected_side,
                quantity, current_bid, current_ask, spread, top_ask_depth,
                baseline_settlement_probability, baseline_expected_value,
                up_baseline_worsening_probability, up_worsening_probability,
                down_baseline_worsening_probability, down_worsening_probability,
                selected_worsening_probability, seconds_left, quote_age_seconds,
                event_probabilities_json, feature_values_json, ladder_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                value.candidate_id,
                value.timestamp,
                value.market_id,
                value.condition_id,
                value.selected_side,
                value.quantity,
                value.bid,
                value.ask,
                value.spread,
                value.top_ask_depth,
                value.baseline_probability,
                value.baseline_edge,
                up_baseline_probability,
                up_probability,
                down_baseline_probability,
                down_probability,
                value.worsening_probability,
                value.seconds_left,
                value.quote_age_seconds,
                json.dumps(value.event_probabilities, sort_keys=True),
                json.dumps(value.feature_values, sort_keys=True),
                json.dumps(value.ladder, separators=(",", ":"), sort_keys=True),
            ],
        )

    def route(self, value: RouteState, timestamp: float) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO repricing_routes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                value.candidate_id,
                value.policy,
                value.decision,
                value.proposed_limit,
                value.ttl_seconds,
                value.requested_quantity,
                value.status,
                value.filled_quantity,
                value.average_price,
                value.fee,
                value.fill_time_seconds,
                value.fallback_used,
                timestamp,
            ],
        )

    def observation(
        self,
        candidate_id: str,
        offset: int,
        elapsed: float,
        timestamp: float,
        bid: float,
        ask: float,
        spread: float,
        depth: float,
        ladder: dict[str, Any],
    ) -> None:
        self.conn.execute(
            """
            INSERT OR IGNORE INTO repricing_observations (
                candidate_id, offset_seconds, actual_elapsed_seconds,
                observed_ts, bid, ask, spread, top_ask_depth, ladder_json
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            [
                candidate_id,
                offset,
                elapsed,
                timestamp,
                bid,
                ask,
                spread,
                depth,
                json.dumps(ladder, separators=(",", ":"), sort_keys=True),
            ],
        )
