#!/usr/bin/env python
"""Durable, sequenced Binance USD-M BTCUSDT L2 recorder.

This process is public-data-only and has no credential or order path. It records
the REST snapshot and every diff-depth message needed to reconstruct the local
book. A sequence gap closes the current session and forces a fresh snapshot.

Usage:
    python backend/venues/binance_l2_recorder.py
    python backend/venues/binance_l2_recorder.py --report
    python backend/venues/binance_l2_recorder.py --selftest
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import aiohttp
import duckdb
import websockets

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "backend" / "research"
if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))

from binance_maker_conversion_v1.order_book import (  # noqa: E402
    BookSequenceGap,
    LocalOrderBook,
)

DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_DB = DATA / "binance_l2.duckdb"
DEFAULT_WS = "wss://fstream.binance.com/public/ws/btcusdt@depth@100ms"
DEFAULT_REST = (
    "https://fapi.binance.com/fapi/v1/depth?symbol=BTCUSDT&limit=1000"
)
BATCH_SIZE = 250
FLUSH_SECONDS = 2.0


def _now_ms() -> int:
    return int(time.time() * 1000)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _levels(value: Any, name: str) -> list[list[str]]:
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list")
    output: list[list[str]] = []
    for row in value:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            raise ValueError(f"{name} contains an invalid level")
        price = float(row[0])
        quantity = float(row[1])
        if (
            not math.isfinite(price)
            or not math.isfinite(quantity)
            or price <= 0.0
            or quantity < 0.0
        ):
            raise ValueError(f"{name} contains a non-finite or negative level")
        output.append([str(row[0]), str(row[1])])
    return output


def normalize_snapshot(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("depth snapshot must be an object")
    update_id = int(payload["lastUpdateId"])
    if update_id <= 0:
        raise ValueError("snapshot lastUpdateId must be positive")
    bids = _levels(payload.get("bids"), "snapshot bids")
    asks = _levels(payload.get("asks"), "snapshot asks")
    if not bids or not asks:
        raise ValueError("depth snapshot must contain both sides")
    return {"lastUpdateId": update_id, "bids": bids, "asks": asks}


def normalize_diff(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict) or payload.get("e") != "depthUpdate":
        raise ValueError("message is not a Binance depthUpdate")
    if str(payload.get("s") or "").upper() != "BTCUSDT":
        raise ValueError("unexpected depth symbol")
    first = int(payload["U"])
    final = int(payload["u"])
    previous = int(payload.get("pu") or 0)
    event_ts = int(payload.get("E") or 0)
    transaction_ts = int(payload.get("T") or 0)
    if first <= 0 or final < first or previous < 0 or event_ts <= 0:
        raise ValueError("invalid depth sequence or event timestamp")
    return {
        "e": "depthUpdate",
        "E": event_ts,
        "T": transaction_ts,
        "s": "BTCUSDT",
        "U": first,
        "u": final,
        "pu": previous,
        "b": _levels(payload.get("b") or [], "diff bids"),
        "a": _levels(payload.get("a") or [], "diff asks"),
    }


def book_top_hash(book: LocalOrderBook, levels: int = 20) -> str:
    bids = sorted(book.bids.items(), reverse=True)[:levels]
    asks = sorted(book.asks.items())[:levels]
    return _sha256(_json({"u": book.last_update_id, "b": bids, "a": asks}))


class L2Store:
    """Single-writer append-preserving store with batched diff inserts."""

    def __init__(self, path: Path | str):
        self.path = Path(path) if str(path) != ":memory:" else Path(":memory:")
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = duckdb.connect(str(self.path))
        self.pending: list[list[Any]] = []
        self.last_flush = time.monotonic()
        self._schema()

    def _schema(self) -> None:
        self.db.execute(
            """
            CREATE TABLE IF NOT EXISTS l2_sessions (
                session_id VARCHAR PRIMARY KEY,
                started_ts_ms BIGINT NOT NULL,
                ended_ts_ms BIGINT,
                venue VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                websocket_url VARCHAR NOT NULL,
                snapshot_url VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                snapshot_update_id BIGINT,
                final_update_id BIGINT,
                applied_diffs BIGINT NOT NULL DEFAULT 0,
                stale_diffs BIGINT NOT NULL DEFAULT 0,
                gap_count BIGINT NOT NULL DEFAULT 0,
                error VARCHAR
            );
            CREATE TABLE IF NOT EXISTS l2_snapshots (
                session_id VARCHAR NOT NULL,
                received_ts_ms BIGINT NOT NULL,
                last_update_id BIGINT NOT NULL,
                bids_json VARCHAR NOT NULL,
                asks_json VARCHAR NOT NULL,
                payload_sha256 VARCHAR NOT NULL,
                PRIMARY KEY(session_id, last_update_id)
            );
            CREATE TABLE IF NOT EXISTS l2_diffs (
                session_id VARCHAR NOT NULL,
                ordinal BIGINT NOT NULL,
                received_ts_ms BIGINT NOT NULL,
                event_ts_ms BIGINT NOT NULL,
                transaction_ts_ms BIGINT,
                first_update_id BIGINT NOT NULL,
                final_update_id BIGINT NOT NULL,
                previous_update_id BIGINT,
                bids_json VARCHAR NOT NULL,
                asks_json VARCHAR NOT NULL,
                applied BOOLEAN NOT NULL,
                disposition VARCHAR NOT NULL,
                payload_sha256 VARCHAR NOT NULL,
                book_top_sha256 VARCHAR,
                PRIMARY KEY(session_id, ordinal)
            );
            CREATE TABLE IF NOT EXISTS l2_gaps (
                session_id VARCHAR NOT NULL,
                detected_ts_ms BIGINT NOT NULL,
                local_update_id BIGINT NOT NULL,
                first_update_id BIGINT,
                final_update_id BIGINT,
                previous_update_id BIGINT,
                reason VARCHAR NOT NULL
            );
            """
        )
        # Repair the pre-fix archive shape where synchronization-time failures set
        # l2_sessions.status='GAP' but never wrote l2_gaps or incremented gap_count. The terminal
        # session row already contains the authoritative timestamp, local update id and reason;
        # venue U/u/pu remain NULL because no overlapping diff existed.
        self.db.execute(
            """
            INSERT INTO l2_gaps
            SELECT s.session_id,
                   COALESCE(s.ended_ts_ms, s.started_ts_ms),
                   COALESCE(s.final_update_id, s.snapshot_update_id, 0),
                   NULL, NULL, NULL,
                   COALESCE(s.error, 'historical GAP session')
            FROM l2_sessions AS s
            WHERE s.status = 'GAP'
              AND NOT EXISTS (
                  SELECT 1 FROM l2_gaps AS g WHERE g.session_id = s.session_id
              )
            """
        )
        self.db.execute(
            """
            UPDATE l2_sessions AS s
            SET gap_count = (
                SELECT COUNT(*) FROM l2_gaps AS g WHERE g.session_id = s.session_id
            )
            WHERE s.status = 'GAP'
            """
        )

    def start_session(self, session_id: str, ws_url: str, rest_url: str) -> None:
        self.db.execute(
            """
            INSERT INTO l2_sessions (
                session_id, started_ts_ms, venue, symbol, websocket_url,
                snapshot_url, status
            ) VALUES (?, ?, 'binance_usdm', 'BTCUSDT', ?, ?, 'BUFFERING')
            """,
            [session_id, _now_ms(), ws_url, rest_url],
        )

    def snapshot(
        self, session_id: str, received_ts_ms: int, payload: dict[str, Any]
    ) -> None:
        bids = _json(payload["bids"])
        asks = _json(payload["asks"])
        digest = _sha256(
            _json(
                {
                    "lastUpdateId": payload["lastUpdateId"],
                    "bids": payload["bids"],
                    "asks": payload["asks"],
                }
            )
        )
        self.db.execute(
            "INSERT INTO l2_snapshots VALUES (?, ?, ?, ?, ?, ?)",
            [
                session_id,
                received_ts_ms,
                payload["lastUpdateId"],
                bids,
                asks,
                digest,
            ],
        )
        self.db.execute(
            """
            UPDATE l2_sessions
            SET status = 'SYNCING', snapshot_update_id = ?
            WHERE session_id = ?
            """,
            [payload["lastUpdateId"], session_id],
        )

    def diff(
        self,
        *,
        session_id: str,
        ordinal: int,
        received_ts_ms: int,
        event: dict[str, Any],
        applied: bool,
        disposition: str,
        top_hash: str | None,
    ) -> None:
        bids = _json(event["b"])
        asks = _json(event["a"])
        self.pending.append(
            [
                session_id,
                ordinal,
                received_ts_ms,
                event["E"],
                event.get("T") or None,
                event["U"],
                event["u"],
                event.get("pu") or None,
                bids,
                asks,
                applied,
                disposition,
                _sha256(_json(event)),
                top_hash,
            ]
        )
        if (
            len(self.pending) >= BATCH_SIZE
            or time.monotonic() - self.last_flush >= FLUSH_SECONDS
        ):
            self.flush()

    def flush(self) -> None:
        if not self.pending:
            return
        rows = self.pending
        self.pending = []
        self.db.executemany(
            "INSERT INTO l2_diffs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self.last_flush = time.monotonic()

    def progress(
        self,
        session_id: str,
        update_id: int,
        applied: int,
        stale: int,
        *,
        status: str = "SYNCED",
    ) -> None:
        self.flush()
        self.db.execute(
            """
            UPDATE l2_sessions
            SET status = ?, final_update_id = ?, applied_diffs = ?,
                stale_diffs = ?
            WHERE session_id = ?
            """,
            [status, update_id, applied, stale, session_id],
        )

    def gap(
        self,
        session_id: str,
        local_update_id: int,
        event: dict[str, Any] | None,
        reason: str,
    ) -> None:
        self.flush()
        # A sequence failure closes the session, so there is exactly one terminal gap record.
        # The live-diff path records the venue IDs before re-raising; the synchronization path
        # reaches the outer handler without an event. Both converge here, and a second call from
        # the outer handler must not double-count the same failure.
        if self.db.execute(
            "SELECT 1 FROM l2_gaps WHERE session_id = ? LIMIT 1", [session_id]
        ).fetchone():
            return
        self.db.execute(
            "INSERT INTO l2_gaps VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                session_id,
                _now_ms(),
                local_update_id,
                event.get("U") if event else None,
                event.get("u") if event else None,
                event.get("pu") if event else None,
                reason[:1000],
            ],
        )
        self.db.execute(
            """
            UPDATE l2_sessions
            SET status = 'GAP', ended_ts_ms = ?, final_update_id = ?,
                gap_count = gap_count + 1, error = ?
            WHERE session_id = ?
            """,
            [_now_ms(), local_update_id, reason[:1000], session_id],
        )

    def finish(
        self,
        session_id: str,
        status: str,
        update_id: int,
        applied: int,
        stale: int,
        error: str | None = None,
    ) -> None:
        self.flush()
        self.db.execute(
            """
            UPDATE l2_sessions
            SET ended_ts_ms = ?, status = ?, final_update_id = ?,
                applied_diffs = ?, stale_diffs = ?, error = ?
            WHERE session_id = ?
            """,
            [
                _now_ms(),
                status,
                update_id,
                applied,
                stale,
                error[:1000] if error else None,
                session_id,
            ],
        )

    def close(self) -> None:
        self.flush()
        self.db.close()


def apply_and_record(
    store: L2Store,
    book: LocalOrderBook,
    session_id: str,
    ordinal: int,
    event: dict[str, Any],
    received_ts_ms: int,
) -> bool:
    try:
        applied = book.apply(event, received_ts_ms)
    except BookSequenceGap as exc:
        store.diff(
            session_id=session_id,
            ordinal=ordinal,
            received_ts_ms=received_ts_ms,
            event=event,
            applied=False,
            disposition="SEQUENCE_GAP",
            top_hash=None,
        )
        store.gap(session_id, book.last_update_id, event, str(exc))
        raise
    store.diff(
        session_id=session_id,
        ordinal=ordinal,
        received_ts_ms=received_ts_ms,
        event=event,
        applied=applied,
        disposition="APPLIED" if applied else "STALE",
        top_hash=book_top_hash(book),
    )
    return applied


def finish_gap_session(
    store: L2Store,
    session_id: str,
    update_id: int,
    applied: int,
    stale: int,
    reason: str,
) -> None:
    """Persist one gap fact and close the affected reconstruction session."""
    store.gap(session_id, update_id, None, reason)
    store.finish(session_id, "GAP", update_id, applied, stale, reason)


async def fetch_snapshot(url: str) -> tuple[dict[str, Any], int]:
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            response.raise_for_status()
            return normalize_snapshot(await response.json()), _now_ms()


def _database_bytes(path: Path) -> int:
    total = 0
    for candidate in (path, Path(str(path) + ".wal")):
        if candidate.exists():
            total += candidate.stat().st_size
    return total


async def collect_session(
    store: L2Store,
    *,
    ws_url: str,
    rest_url: str,
    max_db_bytes: int,
    duration_seconds: float | None = None,
) -> None:
    session_id = str(uuid.uuid4())
    store.start_session(session_id, ws_url, rest_url)
    book = LocalOrderBook()
    applied = 0
    stale = 0
    ordinal = 0
    started = time.monotonic()
    try:
        async with websockets.connect(
            ws_url,
            ping_interval=20,
            ping_timeout=20,
            open_timeout=15,
            close_timeout=5,
            max_queue=8192,
        ) as socket:
            snapshot_task = asyncio.create_task(fetch_snapshot(rest_url))
            buffered: list[tuple[dict[str, Any], int]] = []
            while not snapshot_task.done():
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=0.25)
                except asyncio.TimeoutError:
                    continue
                buffered.append((normalize_diff(json.loads(raw)), _now_ms()))
            snapshot, snapshot_received = await snapshot_task
            book.initialize(snapshot, snapshot_received)
            store.snapshot(session_id, snapshot_received, snapshot)
            for event, received in buffered:
                ordinal += 1
                if apply_and_record(
                    store, book, session_id, ordinal, event, received
                ):
                    applied += 1
                else:
                    stale += 1
            if book._first_event:  # noqa: SLF001 - synchronization is the invariant here
                raise BookSequenceGap("buffer did not overlap the REST snapshot")
            store.progress(
                session_id, book.last_update_id, applied, stale
            )
            print(
                f"[binance-l2] synced session={session_id[:8]} "
                f"snapshot={snapshot['lastUpdateId']} u={book.last_update_id} "
                f"buffered={len(buffered)}",
                flush=True,
            )
            async for raw in socket:
                received = _now_ms()
                event = normalize_diff(json.loads(raw))
                ordinal += 1
                if apply_and_record(
                    store, book, session_id, ordinal, event, received
                ):
                    applied += 1
                else:
                    stale += 1
                if ordinal % BATCH_SIZE == 0:
                    store.progress(
                        session_id, book.last_update_id, applied, stale
                    )
                if ordinal % 1000 == 0:
                    top = book.top()
                    print(
                        f"[binance-l2] diffs={ordinal:,} u={book.last_update_id} "
                        f"bid={top.best_bid if top else None} "
                        f"ask={top.best_ask if top else None}",
                        flush=True,
                    )
                if _database_bytes(store.path) >= max_db_bytes:
                    raise RuntimeError("database_size_limit_reached")
                if (
                    duration_seconds is not None
                    and time.monotonic() - started >= duration_seconds
                ):
                    store.finish(
                        session_id,
                        "COMPLETED",
                        book.last_update_id,
                        applied,
                        stale,
                    )
                    return
    except BookSequenceGap as exc:
        # apply_and_record() already records event IDs for a live-diff gap. A sync-time
        # "buffer did not overlap" gap bypasses that function, so this idempotent call is the
        # only place that guarantees every GAP session has forensic evidence and gap_count=1.
        finish_gap_session(
            store, session_id, book.last_update_id, applied, stale, str(exc)
        )
        raise
    except Exception as exc:
        store.finish(
            session_id,
            "ERROR",
            book.last_update_id,
            applied,
            stale,
            f"{type(exc).__name__}: {exc}",
        )
        raise
    finally:
        store.flush()


async def run(args: argparse.Namespace) -> int:
    store = L2Store(args.db)
    backoff = 1.0
    try:
        while True:
            try:
                await collect_session(
                    store,
                    ws_url=args.ws_url,
                    rest_url=args.rest_url,
                    max_db_bytes=int(args.max_db_gb * 1024**3),
                    duration_seconds=args.duration,
                )
                return 0
            except asyncio.CancelledError:
                raise
            except KeyboardInterrupt:
                return 0
            except Exception as exc:  # feed must rebuild after any uncertain state
                print(
                    f"[binance-l2] {type(exc).__name__}: {exc}; "
                    f"fresh snapshot in {backoff:.1f}s",
                    flush=True,
                )
                if "database_size_limit_reached" in str(exc):
                    return 2
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 1.7)
    finally:
        store.close()


def replay_session(db_path: Path | str, session_id: str) -> dict[str, Any]:
    db = duckdb.connect(str(db_path), read_only=True)
    try:
        row = db.execute(
            """
            SELECT received_ts_ms, last_update_id, bids_json, asks_json
            FROM l2_snapshots WHERE session_id = ?
            ORDER BY received_ts_ms LIMIT 1
            """,
            [session_id],
        ).fetchone()
        if row is None:
            raise ValueError("session has no snapshot")
        book = LocalOrderBook()
        book.initialize(
            {
                "lastUpdateId": int(row[1]),
                "bids": json.loads(row[2]),
                "asks": json.loads(row[3]),
            },
            int(row[0]),
        )
        applied = 0
        for diff in db.execute(
            """
            SELECT received_ts_ms, event_ts_ms, transaction_ts_ms,
                   first_update_id, final_update_id, previous_update_id,
                   bids_json, asks_json, applied, book_top_sha256
            FROM l2_diffs WHERE session_id = ?
            ORDER BY ordinal
            """,
            [session_id],
        ).fetchall():
            event = {
                "e": "depthUpdate",
                "E": int(diff[1]),
                "T": int(diff[2] or 0),
                "s": "BTCUSDT",
                "U": int(diff[3]),
                "u": int(diff[4]),
                "pu": int(diff[5] or 0),
                "b": json.loads(diff[6]),
                "a": json.loads(diff[7]),
            }
            if not bool(diff[8]):
                continue
            if not book.apply(event, int(diff[0])):
                raise AssertionError("recorded applied diff replayed as stale")
            applied += 1
            if book_top_hash(book) != diff[9]:
                raise AssertionError("replayed book checksum mismatch")
        top = book.top()
        return {
            "session_id": session_id,
            "applied_diffs": applied,
            "last_update_id": book.last_update_id,
            "best_bid": top.best_bid if top else None,
            "best_ask": top.best_ask if top else None,
            "book_top_sha256": book_top_hash(book),
        }
    finally:
        db.close()


def report(db_path: Path | str) -> int:
    path = Path(db_path)
    if not path.exists():
        print(f"Binance L2 archive not found: {path}")
        return 0
    db = duckdb.connect(str(path), read_only=True)
    try:
        sessions = db.execute(
            """
            SELECT COUNT(*), COUNT(*) FILTER (WHERE snapshot_update_id IS NOT NULL),
                   COALESCE(SUM(applied_diffs), 0), COALESCE(SUM(gap_count), 0),
                   MIN(started_ts_ms), MAX(COALESCE(ended_ts_ms, started_ts_ms))
            FROM l2_sessions
            """
        ).fetchone()
        diffs = db.execute(
            """
            SELECT COUNT(*), COUNT(*) FILTER (WHERE applied),
                   COUNT(*) FILTER (WHERE disposition = 'SEQUENCE_GAP')
            FROM l2_diffs
            """
        ).fetchone()
        print("BINANCE SEQUENCED L2 ARCHIVE")
        print(f"  path: {path}")
        print(f"  size_mb: {_database_bytes(path) / 1024**2:.1f}")
        print(
            f"  sessions={sessions[0]} snapshots={sessions[1]} "
            f"applied_session_total={sessions[2]} gaps={sessions[3]}"
        )
        print(
            f"  raw_diffs={diffs[0]} applied={diffs[1]} "
            f"gap_rows={diffs[2]}"
        )
        print(
            "  exact queue priority: NOT OBSERVABLE; aggregate L2 supports "
            "book replay and conservative queue research only"
        )
        return 0
    finally:
        db.close()


def selftest() -> int:
    snapshot = normalize_snapshot(
        {
            "lastUpdateId": 10,
            "bids": [["100", "2"], ["99", "4"]],
            "asks": [["101", "3"], ["102", "5"]],
        }
    )
    event1 = normalize_diff(
        {
            "e": "depthUpdate",
            "E": 1100,
            "T": 1099,
            "s": "BTCUSDT",
            "U": 10,
            "u": 11,
            "pu": 9,
            "b": [["100", "1.5"]],
            "a": [["101", "0"], ["101.5", "2"]],
        }
    )
    event2 = normalize_diff(
        {
            "e": "depthUpdate",
            "E": 1200,
            "T": 1199,
            "s": "BTCUSDT",
            "U": 12,
            "u": 12,
            "pu": 11,
            "b": [["99.5", "1"]],
            "a": [],
        }
    )
    gap = normalize_diff(
        {
            "e": "depthUpdate",
            "E": 1300,
            "T": 1299,
            "s": "BTCUSDT",
            "U": 14,
            "u": 14,
            "pu": 13,
            "b": [],
            "a": [],
        }
    )
    try:
        normalize_diff(
            {
                "e": "depthUpdate",
                "E": 1,
                "s": "BTCUSDT",
                "U": 1,
                "u": 1,
                "pu": 0,
                "b": [["100", "-1"]],
                "a": [],
            }
        )
    except ValueError:
        pass
    else:
        raise AssertionError("negative depth quantity must be rejected")

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "l2.duckdb"
        store = L2Store(path)
        session = "selftest"
        store.start_session(session, "ws", "rest")
        store.snapshot(session, 1000, snapshot)
        book = LocalOrderBook()
        book.initialize(snapshot, 1000)
        assert apply_and_record(store, book, session, 1, event1, 1101)
        assert book.top().best_ask == 101.5
        assert apply_and_record(store, book, session, 2, event2, 1201)
        store.progress(session, book.last_update_id, 2, 0)
        try:
            apply_and_record(store, book, session, 3, gap, 1301)
        except BookSequenceGap:
            # Mirror collect_session's outer handler. The event-aware write above and this
            # event-less finalizer describe one failure, not two.
            finish_gap_session(store, session, book.last_update_id, 2, 0, "sequence gap")
        else:
            raise AssertionError("sequence gap must force a rebuild")

        sync_session = "selftest-sync-gap"
        store.start_session(sync_session, "ws", "rest")
        store.snapshot(sync_session, 1400, snapshot)
        finish_gap_session(
            store,
            sync_session,
            snapshot["lastUpdateId"],
            0,
            0,
            "buffer did not overlap the REST snapshot",
        )
        legacy_session = "selftest-legacy-gap"
        store.start_session(legacy_session, "ws", "rest")
        store.snapshot(legacy_session, 1500, snapshot)
        # Reproduce the old incomplete persistence shape. Reopening L2Store must reconcile it.
        store.finish(
            legacy_session,
            "GAP",
            snapshot["lastUpdateId"],
            0,
            0,
            "legacy buffer did not overlap",
        )
        store.close()
        migrated = L2Store(path)
        migrated.close()
        replayed = replay_session(path, session)
        assert replayed["applied_diffs"] == 2
        assert replayed["last_update_id"] == 12
        assert replayed["best_bid"] == 100.0
        assert replayed["best_ask"] == 101.5
        db = duckdb.connect(str(path), read_only=True)
        assert db.execute("SELECT COUNT(*) FROM l2_gaps").fetchone()[0] == 3
        assert db.execute(
            "SELECT gap_count FROM l2_sessions WHERE session_id = ?", [session]
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT gap_count FROM l2_sessions WHERE session_id = ?", [sync_session]
        ).fetchone()[0] == 1
        assert db.execute(
            "SELECT gap_count FROM l2_sessions WHERE session_id = ?", [legacy_session]
        ).fetchone()[0] == 1
        assert db.execute(
            """
            SELECT first_update_id, final_update_id, previous_update_id
            FROM l2_gaps WHERE session_id = ?
            """,
            [sync_session],
        ).fetchone() == (None, None, None)
        assert (
            db.execute(
                "SELECT disposition FROM l2_diffs WHERE ordinal = 3"
            ).fetchone()[0]
            == "SEQUENCE_GAP"
        )
        db.close()

    # The synchronization-time exception bypasses apply_and_record(), so the outer handler's
    # call to the shared finalizer is a required reachability edge, not an implementation detail.
    import ast as _ast
    import inspect as _inspect

    collect_tree = _ast.parse(_inspect.getsource(collect_session))
    assert any(
        isinstance(node, _ast.Call)
        and getattr(node.func, "id", "") == "finish_gap_session"
        for node in _ast.walk(collect_tree)
    )
    print("BINANCE SEQUENCED L2 SELFTEST PASS")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--ws-url", default=DEFAULT_WS)
    parser.add_argument("--rest-url", default=DEFAULT_REST)
    parser.add_argument("--max-db-gb", type=float, default=10.0)
    parser.add_argument("--duration", type=float)
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.selftest:
        return selftest()
    if args.report:
        return report(args.db)
    if args.max_db_gb <= 0:
        raise ValueError("--max-db-gb must be positive")
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
