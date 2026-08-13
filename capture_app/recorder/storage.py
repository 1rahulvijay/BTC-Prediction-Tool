"""Partitioned parquet writer and disk-cap enforcement. Imports nothing from the trading app.

WHY PARTITIONED PARQUET AND NOT ONE DUCKDB
    The main app keeps one growing DuckDB per recorder. That is fine on a workstation and wrong
    for a 30 GB box you intend to rotate:

      - a single file cannot be partially uploaded or partially deleted
      - it is held open by its writer, so a copy mid-write is unsafe
      - one corruption loses the whole history

    Hour-partitioned parquet makes "swap old data out" a directory move. It also makes a GAP
    VISIBLE AS A MISSING DIRECTORY rather than as an absence you discover months later during
    analysis - which is exactly how this project lost 35 days of Polymarket capture.

DELETION RULE
    The disk guard NEVER deletes a partition that has not been marked archived. Silently
    dropping un-uploaded data to stay under a cap would manufacture the same silent hole from a
    different direction. If the cap is hit and nothing is archivable, it stops writing and says
    so loudly.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ARCHIVED_MARKER = ".archived"


def status_dir(root: Path) -> Path:
    override = os.environ.get("CAPTURE_STATE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return root.parent / ("state" if root.name == "data" else f"{root.name}_state")


def hour_dir(root: Path, stream: str, ts_ms: int) -> Path:
    t = time.gmtime(ts_ms / 1000.0)
    return root / stream / f"date={time.strftime('%Y-%m-%d', t)}" / f"hour={t.tm_hour:02d}"


@dataclass
class PartitionWriter:
    """Buffers rows and flushes one parquet file per (stream, hour, flush).

    Flushing on BOTH row count and elapsed time matters: a quiet stream that only flushed on
    row count could hold hours of data in memory and lose it on restart, and the gap would look
    like a recorder outage rather than a buffer loss.
    """

    root: Path
    stream: str
    schema: pa.Schema
    max_rows: int = 20_000
    max_seconds: float = 60.0
    _buf: list[dict] = field(default_factory=list)
    _last_flush: float = field(default_factory=time.time)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _flush_lock: threading.Lock = field(default_factory=threading.Lock)
    _background_lock: threading.Lock = field(default_factory=threading.Lock)
    _background_thread: threading.Thread | None = None
    _background_error: BaseException | None = None
    rows_written: int = 0
    files_written: int = 0

    @property
    def buffered_rows(self) -> int:
        with self._lock:
            return len(self._buf)

    def add(self, row: dict) -> None:
        self._raise_background_error()
        with self._lock:
            self._buf.append(row)
            due = (len(self._buf) >= self.max_rows
                   or (time.time() - self._last_flush) >= self.max_seconds)
        if due:
            self.flush_in_background()

    def _raise_background_error(self) -> None:
        with self._background_lock:
            error, self._background_error = self._background_error, None
        if error is not None:
            raise RuntimeError(f"background parquet flush failed for {self.stream}") from error

    def flush_in_background(self) -> bool:
        """Schedule compression without blocking websocket receive callbacks.

        A single worker drains this writer. Rows arriving while it writes stay in the active
        buffer and trigger another pass if the size/time threshold is still due. Final `flush()`
        remains synchronous, so clean shutdown cannot exit ahead of its writer thread.
        """
        self._raise_background_error()
        with self._background_lock:
            if self._background_thread is not None and self._background_thread.is_alive():
                return False
            thread = threading.Thread(
                target=self._background_worker,
                name=f"parquet-{self.stream}", daemon=True,
            )
            self._background_thread = thread
            thread.start()
        return True

    def _background_worker(self) -> None:
        try:
            while True:
                self.flush()
                with self._lock:
                    due = bool(self._buf) and (
                        len(self._buf) >= self.max_rows
                        or (time.time() - self._last_flush) >= self.max_seconds
                    )
                if not due:
                    break
        except BaseException as exc:  # surfaced on the owning stream's next operation
            with self._background_lock:
                self._background_error = exc
        finally:
            with self._background_lock:
                if self._background_thread is threading.current_thread():
                    self._background_thread = None

    def flush(self) -> int:
        # Only one flush may detach a buffer at a time. This also makes recovery after a write
        # failure deterministic if a future caller invokes add() from another thread.
        with self._flush_lock:
            with self._lock:
                if not self._buf:
                    self._last_flush = time.time()
                    return 0
                rows, self._buf = self._buf, []
                self._last_flush = time.time()

            # A timed flush can straddle an hour boundary. Partition every row by its own event
            # receive hour instead of putting the entire buffer in the first row's directory.
            grouped: dict[Path, list[dict]] = {}
            for row in rows:
                ts = int(row.get("ts_ms") or time.time() * 1000)
                grouped.setdefault(hour_dir(self.root, self.stream, ts), []).append(row)

            groups = list(grouped.items())
            written = 0
            for position, (directory, group) in enumerate(groups):
                directory.mkdir(parents=True, exist_ok=True)
                with partition_guard(directory):
                    # A very late source event can legitimately target a partition that was
                    # already archived. Invalidate deletion eligibility atomically with the new
                    # file write; archive finalization uses this same partition lock.
                    clear_archived(directory)
                    path = directory / (
                        f"part-{time.time_ns()}-{os.getpid()}-{uuid.uuid4().hex}.parquet"
                    )
                    tmp = path.with_suffix(".tmp")
                    try:
                        cols = {field.name: [row.get(field.name) for row in group]
                                for field in self.schema}
                        table = pa.Table.from_pydict(cols, schema=self.schema)
                        pq.write_table(table, tmp, compression="zstd", compression_level=3)
                        # The rename is atomic. fsync the completed temporary file first so a
                        # power loss cannot leave status claiming a file that never reached disk.
                        with tmp.open("r+b") as handle:
                            os.fsync(handle.fileno())
                        tmp.replace(path)
                    except Exception:
                        tmp.unlink(missing_ok=True)
                        remaining = [row for _, pending in groups[position:] for row in pending]
                        with self._lock:
                            self._buf = remaining + self._buf
                        raise
                written += len(group)
                self.rows_written += len(group)
                self.files_written += 1
            return written

    def flush_due(self) -> int:
        """Flush a quiet stream once its time limit expires, even without another row."""
        self._raise_background_error()
        with self._lock:
            due = bool(self._buf) and (time.time() - self._last_flush) >= self.max_seconds
        self.flush_in_background() if due else None
        return 0


def dir_size_bytes(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def partitions(root: Path) -> list[Path]:
    """Every valid hour partition, globally oldest first across all streams."""
    if not root.exists():
        return []
    out = []
    for stream in sorted(p for p in root.iterdir() if p.is_dir()):
        for date_dir in sorted(d for d in stream.iterdir() if d.is_dir()):
            out.extend(h for h in date_dir.iterdir() if h.is_dir() and h.name.startswith("hour="))
    return sorted(out, key=lambda part: (_partition_epoch(part), str(part)))


def _partition_epoch(part: Path) -> float:
    try:
        value = f"{part.parent.name.removeprefix('date=')} {part.name.removeprefix('hour=')}"
        return datetime.strptime(value, "%Y-%m-%d %H").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError):
        return float("inf")


def mark_archived(part: Path, metadata: dict | None = None) -> None:
    payload = {"archived_utc": time.time(), **(metadata or {})}
    marker = part / ARCHIVED_MARKER
    tmp = marker.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    tmp.replace(marker)


def is_archived(part: Path) -> bool:
    return (part / ARCHIVED_MARKER).exists()


def archived_metadata(part: Path) -> dict:
    marker = part / ARCHIVED_MARKER
    if not marker.exists():
        return {}
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, TypeError, ValueError):
        # Legacy/manual markers contained only a timestamp. They still protect/delete exactly as
        # before, but archive verification reports them as unverifiable rather than inventing
        # remote evidence.
        return {}


def clear_archived(part: Path) -> None:
    (part / ARCHIVED_MARKER).unlink(missing_ok=True)


@contextmanager
def partition_guard(part: Path):
    """Serialize writer/archive/deletion mutations for one partition without global stalls."""
    key = str(part.resolve())
    with _PARTITION_LOCKS_GUARD:
        lock = _PARTITION_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PARTITION_LOCKS[key] = lock
    with lock:
        yield


def enforce_cap(root: Path, cap_gb: float, keep_hours: int = 6) -> dict:
    """Delete the oldest ARCHIVED partitions until under the cap.

    keep_hours protects the most recent partitions from deletion even if archived - they may
    still be being appended to, and reclaiming 30 minutes of disk is never worth truncating
    the live stream.

    Returns a report. `blocked` is the important field: it means the cap was hit but nothing
    could be safely removed, which is an operator problem, not something to solve by deleting
    unarchived data.
    """
    cap = int(cap_gb * 1024 ** 3)
    used = dir_size_bytes(root)
    freed, removed = 0, []
    if used <= cap:
        return {"used_bytes": used, "cap_bytes": cap, "freed_bytes": 0,
                "removed": [], "deletion_errors": [],
                "blocked": False, "over_cap": False}

    parts = partitions(root)
    cutoff = time.time() - keep_hours * 3600
    protected = {
        part for part in parts
        if keep_hours and _partition_epoch(part) + 3600 > cutoff
    }
    errors = []
    for part in parts:
        if used - freed <= cap:
            break
        if part in protected:
            continue
        with partition_guard(part):
            if not is_archived(part):
                continue
            size = dir_size_bytes(part)
            try:
                shutil.rmtree(part)
            except OSError as exc:
                errors.append({"partition": str(part), "error": str(exc)[:200]})
                continue
            if part.exists():
                errors.append({"partition": str(part), "error": "directory still exists"})
                continue
        freed += size
        removed.append(str(part))
    still_over = (used - freed) > cap
    return {"used_bytes": used, "cap_bytes": cap, "freed_bytes": freed,
            "removed": removed, "deletion_errors": errors,
            "blocked": still_over, "over_cap": True}


def write_status(root: Path, name: str, payload: dict) -> None:
    d = status_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.json"
    key = str(p.resolve())
    now = time.time()
    with _STATUS_LOCK:
        prior = _STATUS_CACHE.get(key, {})
        if not prior and p.exists():
            try:
                prior = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError, TypeError):
                prior = {}
        current = {**prior, **payload, "updated_utc": now}
        _STATUS_CACHE[key] = current
        urgent = (
            "connected" in payload or payload.get("last_error")
            or payload.get("stopped_cleanly") or payload.get("blocked")
            or payload.get("resource_pressure")
        )
        if not urgent and now - _STATUS_LAST_WRITE.get(key, 0.0) < 1.0:
            return
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(current, indent=2), encoding="utf-8")
        tmp.replace(p)
        _STATUS_LAST_WRITE[key] = now


_STATUS_LOCK = threading.Lock()
_STATUS_CACHE: dict[str, dict] = {}
_STATUS_LAST_WRITE: dict[str, float] = {}
_PARTITION_LOCKS_GUARD = threading.Lock()
_PARTITION_LOCKS: weakref.WeakValueDictionary[str, threading.RLock] = (
    weakref.WeakValueDictionary()
)
