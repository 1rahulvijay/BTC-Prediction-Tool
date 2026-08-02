"""Read-only Phase 5B loaders with explicit timestamp and identity contracts."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd

from research.phase5_standalone.common.causal_loader import (
    DataUnavailable,
    LoadedData,
    SchemaUnavailable,
    load_source,
)


ALLOWED_DATABASES = {
    "analytics.duckdb",
    "microstructure.duckdb",
    "multi_venue.duckdb",
    "polymarket_l2.duckdb",
    "opportunity_ledger.duckdb",
}


def _quote(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum():
        raise SchemaUnavailable(f"unsafe SQL identifier: {identifier!r}")
    return f'"{identifier}"'


def timestamp_ms(values: pd.Series, source: str) -> np.ndarray:
    if pd.api.types.is_datetime64_any_dtype(values):
        return (pd.to_datetime(values, utc=True).astype("int64") // 1_000_000).to_numpy(np.int64)
    numeric = pd.to_numeric(values, errors="coerce").to_numpy(float)
    finite = numeric[np.isfinite(numeric)]
    if not len(finite):
        raise SchemaUnavailable(f"{source} has no finite timestamps")
    magnitude = float(np.nanmedian(np.abs(finite)))
    if magnitude > 1e17:
        numeric /= 1e6
    elif magnitude > 1e14:
        numeric /= 1e3
    elif magnitude < 1e11:
        numeric *= 1e3
    if not np.isfinite(numeric).all():
        raise SchemaUnavailable(f"{source} contains invalid timestamps")
    return np.rint(numeric).astype(np.int64)


def _frame_hash(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps(list(frame.columns), separators=(",", ":")).encode("utf-8"))
    digest.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    return digest.hexdigest()


def load_db_table(
    data_dir: str | Path,
    *,
    database: str,
    table: str,
    columns: list[str],
    timestamp: str,
    maximum_rows: int,
    where: str | None = None,
) -> LoadedData:
    if database not in ALLOWED_DATABASES:
        raise SchemaUnavailable(f"database is not allow-listed: {database}")
    path = Path(data_dir).resolve() / database
    if not path.is_file():
        raise DataUnavailable(f"required source does not exist: {path}")
    con = duckdb.connect(str(path), read_only=True)
    try:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
        if table not in tables:
            raise SchemaUnavailable(f"{database} has no table {table}")
        available = {row[1] for row in con.execute(
            f"PRAGMA table_info({_quote(table)})").fetchall()}
        required = list(dict.fromkeys([timestamp, *columns]))
        missing = sorted(set(required) - available)
        if missing:
            raise SchemaUnavailable(f"{database}.{table} missing columns: {missing}")
        projection = ", ".join(_quote(column) for column in required)
        predicate = f" AND ({where})" if where else ""
        limit = f" LIMIT {int(maximum_rows)}" if maximum_rows > 0 else ""
        order = _quote(timestamp)
        query = (
            f"SELECT * FROM (SELECT {projection} FROM {_quote(table)} "
            f"WHERE {order} IS NOT NULL{predicate} ORDER BY {order} DESC{limit}) q "
            f"ORDER BY {order}"
        )
        frame = con.execute(query).fetchdf()
    finally:
        con.close()
    if frame.empty:
        raise DataUnavailable(f"required source is empty: {database}.{table}")
    ts = timestamp_ms(frame[timestamp], f"{database}.{table}.{timestamp}")
    if np.any(ts[1:] < ts[:-1]):
        raise SchemaUnavailable(f"{database}.{table} is not chronological")
    frame.insert(0, "_ts_ms", ts)
    stat = path.stat()
    identity: dict[str, Any] = {
        "source": f"{database}:{table}",
        "path": str(path),
        "table": table,
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "loaded_rows": int(len(frame)),
        "loaded_frame_sha256": _frame_hash(frame),
        "columns": required,
    }
    causal = {
        "rows": int(len(frame)),
        "first_ts_ms": int(ts[0]),
        "last_ts_ms": int(ts[-1]),
        "span_days": float((ts[-1] - ts[0]) / 86_400_000),
        "monotonic": True,
        "read_only": True,
    }
    return LoadedData(frame, identity, causal)


def load_contract(data_dir: str | Path, contract: dict[str, Any], maximum_rows: int) -> LoadedData:
    source = contract.get("source")
    if source == "multi_venue_events" and contract.get("where"):
        return load_db_table(
            data_dir,
            database="multi_venue.duckdb",
            table="venue_events",
            columns=list(contract.get("required_columns", [])),
            timestamp=str(contract.get("timestamp", "recv_ts")),
            maximum_rows=maximum_rows,
            where=str(contract["where"]),
        )
    if source in {
        "btc_matrix", "poly_checkpoints", "crossvenue", "binance_l2",
        "multi_venue_events", "polymarket_l2", "polymarket_l2_trades",
        "opportunity_ledger",
    }:
        return load_source(data_dir, contract, maximum_rows=maximum_rows)
    if source == "analytics_table":
        return load_db_table(
            data_dir,
            database="analytics.duckdb",
            table=str(contract["table"]),
            columns=list(contract.get("required_columns", [])),
            timestamp=str(contract["timestamp"]),
            maximum_rows=maximum_rows,
            where=contract.get("where"),
        )
    if source == "multi_venue_episodes":
        return load_db_table(
            data_dir,
            database="multi_venue.duckdb",
            table="venue_episodes",
            columns=list(contract.get("required_columns", [])),
            timestamp=str(contract.get("timestamp", "episode_start")),
            maximum_rows=maximum_rows,
        )
    if source == "polymarket_l2_updates":
        return load_db_table(
            data_dir,
            database="polymarket_l2.duckdb",
            table="pm_l2_level_updates",
            columns=list(contract.get("required_columns", [])),
            timestamp=str(contract.get("timestamp", "recv_ts_ns")),
            maximum_rows=maximum_rows,
            where=contract.get("where"),
        )
    raise SchemaUnavailable(f"unknown Phase 5B source: {source!r}")


def selftest(tmp_path: Path) -> None:
    path = tmp_path / "analytics.duckdb"
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE model_predictions(timestamp BIGINT, model VARCHAR, hit BOOLEAN)")
    con.execute("INSERT INTO model_predictions VALUES (1000, 'a', true), (2000, 'b', false)")
    con.close()
    loaded = load_db_table(
        tmp_path,
        database="analytics.duckdb",
        table="model_predictions",
        columns=["model", "hit"],
        timestamp="timestamp",
        maximum_rows=1,
    )
    assert len(loaded.frame) == 1
    assert loaded.frame.iloc[0]["model"] == "b"
