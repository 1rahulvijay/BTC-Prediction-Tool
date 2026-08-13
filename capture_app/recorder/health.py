"""Persist collector capacity evidence so VM sizing is based on data, not uptime."""
from __future__ import annotations

import asyncio
import os
import shutil
import time
from pathlib import Path

import pyarrow as pa

from .storage import PartitionWriter, write_status

RUNTIME_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()), ("event_loop_lag_ms", pa.float64()),
    ("process_cpu_percent", pa.float64()), ("rss_mb", pa.float64()),
    ("disk_free_gb", pa.float64()), ("resource_pressure", pa.bool_()),
])


def _rss_mb() -> float | None:
    try:
        pages = int(Path("/proc/self/statm").read_text().split()[1])
        return pages * int(os.sysconf("SC_PAGE_SIZE")) / (1024 * 1024)
    except (OSError, ValueError, IndexError, AttributeError):
        return None


async def runtime_metrics(root: Path, stop: asyncio.Event, interval_s: float = 5.0) -> None:
    writer = PartitionWriter(root, "collector_runtime", RUNTIME_SCHEMA, max_rows=720,
                             max_seconds=60)
    prior_wall, prior_cpu = time.perf_counter(), time.process_time()
    pressure_streak = 0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
            break
        except asyncio.TimeoutError:
            pass
        wall, cpu = time.perf_counter(), time.process_time()
        elapsed = max(wall - prior_wall, 1e-6)
        lag_ms = max(0.0, (elapsed - interval_s) * 1000)
        cpu_percent = max(0.0, (cpu - prior_cpu) / elapsed * 100)
        prior_wall, prior_cpu = wall, cpu
        disk = shutil.disk_usage(root)
        rss = _rss_mb()
        soft_pressure = lag_ms > 500 or (rss is not None and rss > 850)
        pressure_streak = pressure_streak + 1 if soft_pressure else 0
        pressure = bool(pressure_streak >= 3 or disk.free < 2_000_000_000)
        row = {
            "ts_ms": int(time.time() * 1000), "event_loop_lag_ms": lag_ms,
            "process_cpu_percent": cpu_percent, "rss_mb": rss,
            "disk_free_gb": disk.free / 1e9, "resource_pressure": pressure,
        }
        writer.add(row)
        write_status(root, "collector_runtime", {
            "rows": writer.rows_written + writer.buffered_rows, "files": writer.files_written,
            "last_data_utc": time.time(), **{key: value for key, value in row.items()
                                             if key != "ts_ms"},
            "pressure_streak": pressure_streak,
        })
    writer.flush()
    write_status(root, "collector_runtime", {
        "rows": writer.rows_written, "files": writer.files_written, "stopped_cleanly": True,
    })
