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
from dataclasses import dataclass, field
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

ARCHIVED_MARKER = ".archived"


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
    rows_written: int = 0
    files_written: int = 0

    def add(self, row: dict) -> None:
        with self._lock:
            self._buf.append(row)
            due = (len(self._buf) >= self.max_rows
                   or (time.time() - self._last_flush) >= self.max_seconds)
        if due:
            self.flush()

    def flush(self) -> int:
        with self._lock:
            if not self._buf:
                self._last_flush = time.time()
                return 0
            rows, self._buf = self._buf, []
            self._last_flush = time.time()
        ts = int(rows[0].get("ts_ms") or time.time() * 1000)
        d = hour_dir(self.root, self.stream, ts)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"part-{int(time.time()*1000)}-{os.getpid()}.parquet"
        cols = {f.name: [r.get(f.name) for r in rows] for f in self.schema}
        table = pa.Table.from_pydict(cols, schema=self.schema)
        tmp = path.with_suffix(".tmp")
        pq.write_table(table, tmp, compression="zstd", compression_level=3)
        tmp.replace(path)          # atomic: a reader never sees a half-written part
        self.rows_written += len(rows)
        self.files_written += 1
        return len(rows)


def dir_size_bytes(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def partitions(root: Path) -> list[Path]:
    """Every hour-partition directory, oldest first by path (which sorts chronologically)."""
    out = []
    for stream in sorted(p for p in root.iterdir() if p.is_dir()):
        for date_dir in sorted(d for d in stream.iterdir() if d.is_dir()):
            out.extend(sorted(h for h in date_dir.iterdir() if h.is_dir()))
    return out


def mark_archived(part: Path) -> None:
    (part / ARCHIVED_MARKER).write_text(str(time.time()), encoding="utf-8")


def is_archived(part: Path) -> bool:
    return (part / ARCHIVED_MARKER).exists()


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
                "removed": [], "blocked": False, "over_cap": False}

    parts = partitions(root)
    protected = set(parts[-keep_hours:]) if keep_hours else set()
    for part in parts:
        if used - freed <= cap:
            break
        if part in protected or not is_archived(part):
            continue
        size = dir_size_bytes(part)
        shutil.rmtree(part, ignore_errors=True)
        freed += size
        removed.append(str(part))
    still_over = (used - freed) > cap
    return {"used_bytes": used, "cap_bytes": cap, "freed_bytes": freed,
            "removed": removed, "blocked": still_over, "over_cap": True}


def write_status(root: Path, name: str, payload: dict) -> None:
    d = root.parent / "state"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{name}.json"
    payload["updated_utc"] = time.time()
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    tmp.replace(p)
