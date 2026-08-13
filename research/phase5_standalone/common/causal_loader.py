"""Narrow, read-only loaders with explicit causal timestamp contracts."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import duckdb
import numpy as np
import pandas as pd


class DataUnavailable(RuntimeError):
    pass


class SchemaUnavailable(RuntimeError):
    pass


def connect_read_only(path: Path):
    """Open an evidence database or fail closed when its live writer owns the file.

    DuckDB uses an exclusive process lock on Windows. A research campaign must not crash, stop
    the recorder, or copy a potentially inconsistent database when that lock is active.
    """
    try:
        return duckdb.connect(str(path), read_only=True)
    except duckdb.IOException as exc:
        raise DataUnavailable(
            f"evidence database is live-locked; use an immutable snapshot or rerun when its "
            f"recorder is stopped: {path}"
        ) from exc


@dataclass(slots=True)
class LoadedData:
    frame: pd.DataFrame
    identity: dict[str, Any]
    causal_summary: dict[str, Any]


SOURCE_MAP = {
    "btc_matrix": ("research_matrix_1m.parquet", None, "ts_ms"),
    "poly_checkpoints": ("research/causal_checkpoints_v1.parquet", None, "snapshot_ts"),
    "crossvenue": ("microstructure.duckdb", "crossvenue_snapshots", "ts_ms"),
    "binance_l2": ("microstructure.duckdb", "l2_snapshots", "ts_ms"),
    "multi_venue_events": ("multi_venue.duckdb", "venue_events", "recv_ts"),
    "polymarket_l2": ("polymarket_l2.duckdb", "pm_l2_book_summaries", "recv_ts_ns"),
    "polymarket_l2_trades": ("polymarket_l2.duckdb", "pm_l2_trades", "recv_ts_ns"),
    "opportunity_ledger": ("opportunity_ledger.duckdb", "opportunity_decisions", "decision_ts"),
}


def _quote(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"unsafe identifier: {name!r}")
    return f'"{name}"'


def _frame_hash(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    digest.update(json.dumps(list(frame.columns), separators=(",", ":")).encode("utf-8"))
    digest.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    return digest.hexdigest()


def _source_columns(path: Path, table: str | None) -> list[str]:
    if table is None:
        con = duckdb.connect()
        try:
            return [row[0] for row in con.execute(
                "DESCRIBE SELECT * FROM read_parquet(?)", [str(path)]).fetchall()]
        finally:
            con.close()
    con = connect_read_only(path)
    try:
        return [row[1] for row in con.execute(f"PRAGMA table_info({_quote(table)})").fetchall()]
    finally:
        con.close()


def _load_query(
    path: Path,
    table: str | None,
    columns: list[str],
    timestamp: str,
    maximum_rows: int,
) -> pd.DataFrame:
    projection = ", ".join(_quote(column) for column in columns)
    order = _quote(timestamp)
    limit = f" LIMIT {int(maximum_rows)}" if maximum_rows > 0 else ""
    if table is None:
        con = duckdb.connect()
        relation = "read_parquet(?)"
        params = [str(path)]
    else:
        con = connect_read_only(path)
        relation = _quote(table)
        params = []
    try:
        # Limit newest rows before restoring chronological order. The untouched test is recent.
        query = (f"SELECT * FROM (SELECT {projection} FROM {relation} "
                 f"WHERE {order} IS NOT NULL ORDER BY {order} DESC{limit}) q ORDER BY {order}")
        return con.execute(query, params).fetchdf()
    finally:
        con.close()


def _timestamp_ms(values: pd.Series, source: str, column: str) -> np.ndarray:
    if pd.api.types.is_datetime64_any_dtype(values):
        return (pd.to_datetime(values, utc=True).astype("int64") // 1_000_000).to_numpy(np.int64)
    numeric = pd.to_numeric(values, errors="coerce").to_numpy(float)
    finite = numeric[np.isfinite(numeric)]
    if not len(finite):
        raise SchemaUnavailable(f"{source}.{column} has no finite timestamps")
    magnitude = float(np.nanmedian(np.abs(finite)))
    if magnitude > 1e17:
        numeric /= 1e6
    elif magnitude > 1e14:
        numeric /= 1e3
    elif magnitude < 1e11:
        numeric *= 1e3
    return np.rint(numeric).astype(np.int64)


def _validate_causality(source: str, frame: pd.DataFrame, ts_ms: np.ndarray) -> dict[str, Any]:
    if np.any(ts_ms[1:] < ts_ms[:-1]):
        raise SchemaUnavailable(f"{source} is not chronological after loading")
    summary: dict[str, Any] = {
        "rows": int(len(frame)),
        "first_ts_ms": int(ts_ms[0]),
        "last_ts_ms": int(ts_ms[-1]),
        "monotonic": True,
    }
    if source == "poly_checkpoints":
        required = {"checkpoint_age_s", "seconds_left", "checkpoint_s", "horizon", "eligible"}
        missing = required - set(frame.columns)
        if missing:
            raise SchemaUnavailable(f"causal checkpoint proof missing columns: {sorted(missing)}")
        age = pd.to_numeric(frame["checkpoint_age_s"], errors="coerce")
        if age.isna().any() or (age < 0).any():
            raise SchemaUnavailable("checkpoint dataset contains unprovable/non-causal rows")
        late = pd.to_numeric(frame["seconds_left"]) < pd.to_numeric(frame["checkpoint_s"])
        if late.any():
            raise SchemaUnavailable("checkpoint dataset reaches forward past its grid point")
        summary.update({
            "atomic_row_contract": True,
            "maximum_checkpoint_age_s": float(age.max()),
            "eligible_rows": int(pd.Series(frame["eligible"]).fillna(False).astype(bool).sum()),
        })
    if source == "opportunity_ledger":
        for column in ("state_snapshot_ts", "feature_cutoff_ts", "quote_recv_ts"):
            if column in frame:
                candidate = pd.to_numeric(frame[column], errors="coerce")
                decision = pd.to_numeric(frame["decision_ts"], errors="coerce")
                if ((candidate.notna()) & (candidate > decision)).any():
                    raise SchemaUnavailable(f"ledger violates {column} <= decision_ts")
        summary["atomic_decision_contract"] = True
    return summary


def load_source(
    data_dir: str | Path,
    contract: dict[str, Any],
    *,
    maximum_rows: int,
) -> LoadedData:
    source = str(contract.get("source", ""))
    if source not in SOURCE_MAP:
        raise SchemaUnavailable(f"unknown source contract: {source!r}")
    relative, table, default_ts = SOURCE_MAP[source]
    path = Path(data_dir).resolve() / relative
    if not path.is_file():
        raise DataUnavailable(f"required source does not exist: {path}")
    timestamp = str(contract.get("timestamp") or default_ts)
    required = list(dict.fromkeys([timestamp, *contract.get("required_columns", [])]))
    available = _source_columns(path, table)
    missing = sorted(set(required) - set(available))
    if missing:
        raise SchemaUnavailable(f"{source} missing columns: {missing}")
    frame = _load_query(path, table, required, timestamp, maximum_rows)
    if frame.empty:
        raise DataUnavailable(f"required source is empty: {source}")
    ts_ms = _timestamp_ms(frame[timestamp], source, timestamp)
    frame.insert(0, "_ts_ms", ts_ms)
    causal = _validate_causality(source, frame, ts_ms)
    stat = path.stat()
    identity = {
        "source": source,
        "path": str(path),
        "table": table,
        "size_bytes": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "loaded_rows": int(len(frame)),
        "loaded_frame_sha256": _frame_hash(frame),
        "columns": required,
    }
    return LoadedData(frame=frame, identity=identity, causal_summary=causal)


def selftest(tmp_path: Path) -> None:
    frame = pd.DataFrame({"ts_ms": np.arange(100) + 1_700_000_000_000,
                          "close": np.arange(100, dtype=float)})
    target = tmp_path / "research_matrix_1m.parquet"
    frame.to_parquet(target, index=False)
    loaded = load_source(tmp_path, {"source": "btc_matrix", "required_columns": ["close"]},
                         maximum_rows=20)
    assert len(loaded.frame) == 20 and loaded.causal_summary["monotonic"]
