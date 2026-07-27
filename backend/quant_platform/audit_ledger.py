"""Append-only hash-chained audit ledger for shared platform decisions."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
from threading import RLock
import time
from typing import Iterator, Mapping

import duckdb


SCHEMA = """
CREATE TABLE IF NOT EXISTS quant_audit_events(
    event_id VARCHAR PRIMARY KEY,
    created_at_ns BIGINT NOT NULL,
    category VARCHAR NOT NULL,
    source_id VARCHAR NOT NULL,
    payload_json VARCHAR NOT NULL,
    previous_sha256 VARCHAR NOT NULL,
    event_sha256 VARCHAR NOT NULL UNIQUE
)
"""


class AuditLedger:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with self._connect() as con:
            con.execute(SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(str(self.path))
        try:
            yield con
        finally:
            con.close()

    def append(
        self,
        event_id: str,
        category: str,
        source_id: str,
        payload: Mapping[str, object],
        created_at_ns: int | None = None,
    ) -> str:
        if not event_id or not category or not source_id:
            raise ValueError("event_id, category, and source_id are required")
        payload_json = json.dumps(
            dict(payload), sort_keys=True, separators=(",", ":"), default=str
        )
        created_at_ns = created_at_ns or time.time_ns()
        with self._lock, self._connect() as con:
            con.execute("BEGIN TRANSACTION")
            try:
                existing = con.execute(
                    "SELECT category, source_id, payload_json, created_at_ns, "
                    "previous_sha256, event_sha256 "
                    "FROM quant_audit_events WHERE event_id = ?",
                    [event_id],
                ).fetchone()
                if existing is not None:
                    comparable = (
                        category,
                        source_id,
                        payload_json,
                        created_at_ns,
                    )
                    if tuple(existing[:4]) != comparable:
                        raise ValueError(
                            "event_id collision with different immutable content"
                        )
                    con.execute("COMMIT")
                    return str(existing[5])
                last = con.execute(
                    "SELECT event_sha256 FROM quant_audit_events "
                    "ORDER BY created_at_ns DESC, event_id DESC LIMIT 1"
                ).fetchone()
                previous = str(last[0]) if last else "GENESIS"
                raw = json.dumps(
                    {
                        "event_id": event_id,
                        "created_at_ns": created_at_ns,
                        "category": category,
                        "source_id": source_id,
                        "payload_json": payload_json,
                        "previous_sha256": previous,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
                digest = hashlib.sha256(raw).hexdigest()
                con.execute(
                    "INSERT INTO quant_audit_events VALUES (?, ?, ?, ?, ?, ?, ?)",
                    [
                        event_id,
                        created_at_ns,
                        category,
                        source_id,
                        payload_json,
                        previous,
                        digest,
                    ],
                )
                con.execute("COMMIT")
                return digest
            except Exception:
                con.execute("ROLLBACK")
                raise

    def verify(self) -> tuple[bool, list[str]]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT event_id, created_at_ns, category, source_id, payload_json, "
                "previous_sha256, event_sha256 FROM quant_audit_events "
                "ORDER BY created_at_ns, event_id"
            ).fetchall()
        previous = "GENESIS"
        reasons: list[str] = []
        for row in rows:
            event_id, created_at_ns, category, source_id, payload_json, stored_prev, digest = row
            if stored_prev != previous:
                reasons.append(f"chain_break:{event_id}")
            raw = json.dumps(
                {
                    "event_id": event_id,
                    "created_at_ns": created_at_ns,
                    "category": category,
                    "source_id": source_id,
                    "payload_json": payload_json,
                    "previous_sha256": stored_prev,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            expected = hashlib.sha256(raw).hexdigest()
            if expected != digest:
                reasons.append(f"hash_mismatch:{event_id}")
            previous = digest
        return not reasons, reasons
