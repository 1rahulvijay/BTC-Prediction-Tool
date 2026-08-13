"""Cheap parquet continuity and schema audit for unattended capture."""
from __future__ import annotations

import json
import math
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pyarrow.parquet as pq


def _file_time_bounds(metadata) -> tuple[int | None, int | None]:
    try:
        index = metadata.schema.names.index("ts_ms")
    except ValueError:
        return None, None
    low, high = [], []
    for group in range(metadata.num_row_groups):
        stats = metadata.row_group(group).column(index).statistics
        if stats and stats.has_min_max:
            low.append(int(stats.min)); high.append(int(stats.max))
    return (min(low), max(high)) if low else (None, None)


def quality_report(root: Path, expected: list[str], stale_seconds: int) -> dict:
    now_ms = int(time.time() * 1000)
    report = {"generated_utc": time.time(), "root": str(root), "streams": {},
              "errors": [], "warnings": []}
    for stream in expected:
        files = sorted((root / stream).glob("date=*/hour=*/*.parquet"))
        item = {"files": len(files), "rows": 0, "bytes": 0, "first_ts_ms": None,
                "last_ts_ms": None, "schemas": [], "corrupt_files": [],
                "partition_hours": 0, "missing_hours": []}
        schema_fingerprints = set()
        for path in files:
            try:
                metadata = pq.read_metadata(path)
                item["rows"] += metadata.num_rows
                item["bytes"] += path.stat().st_size
                schema_fingerprints.add(str(metadata.schema.to_arrow_schema()))
                first, last = _file_time_bounds(metadata)
                if first is not None:
                    item["first_ts_ms"] = first if item["first_ts_ms"] is None else min(
                        item["first_ts_ms"], first)
                    item["last_ts_ms"] = last if item["last_ts_ms"] is None else max(
                        item["last_ts_ms"], last)
            except Exception as exc:  # noqa: BLE001
                item["corrupt_files"].append({"path": str(path), "error": str(exc)[:200]})
        item["schemas"] = sorted(schema_fingerprints)
        hours = set()
        for path in files:
            try:
                date = path.parent.parent.name.removeprefix("date=")
                hour = path.parent.name.removeprefix("hour=")
                hours.add(datetime.strptime(f"{date} {hour}", "%Y-%m-%d %H")
                          .replace(tzinfo=timezone.utc))
            except ValueError:
                report["warnings"].append(f"{stream}: malformed partition path {path}")
        item["partition_hours"] = len(hours)
        if hours:
            cursor, final = min(hours), max(hours)
            while cursor <= final:
                if cursor not in hours:
                    item["missing_hours"].append(cursor.isoformat())
                cursor += timedelta(hours=1)
        item["age_seconds"] = ((now_ms - item["last_ts_ms"]) / 1000
                               if item["last_ts_ms"] is not None else None)
        report["streams"][stream] = item
        quiet = {"polymarket_settlement", "futures_funding_history",
                 "futures_liquidations", "polymarket_trades", "polymarket_market_events",
                 "bybit_funding_history"}
        if not files and stream in quiet:
            report["warnings"].append(f"{stream}: no event rows yet")
        elif not files:
            report["errors"].append(f"{stream}: no parquet files")
        if item["corrupt_files"]:
            report["errors"].append(f"{stream}: {len(item['corrupt_files'])} corrupt files")
        if len(schema_fingerprints) > 1:
            report["warnings"].append(f"{stream}: {len(schema_fingerprints)} schemas")
        continuous = stream not in {"polymarket_trades", "polymarket_market_events",
                                    "futures_liquidations", "futures_funding_history",
                                    "bybit_funding_history"}
        if continuous and item["missing_hours"]:
            report["errors"].append(
                f"{stream}: {len(item['missing_hours'])} missing UTC hour partitions"
            )
        if item["age_seconds"] is not None and math.isfinite(item["age_seconds"]):
            # Quiet settlement, funding and liquidation streams are not expected every few minutes.
            if stream not in {"polymarket_settlement", "futures_funding_history",
                              "futures_liquidations"} and item["age_seconds"] > stale_seconds:
                report["errors"].append(
                    f"{stream}: newest parquet row is {item['age_seconds']:.0f}s old"
                )
    report["ok"] = not report["errors"]
    return report


def write_quality_report(root: Path, report: dict) -> Path:
    state = root.parent / ("state" if root.name == "data" else f"{root.name}_state")
    state.mkdir(parents=True, exist_ok=True)
    path, tmp = state / "quality.json", state / "quality.tmp"
    tmp.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)
    return path
