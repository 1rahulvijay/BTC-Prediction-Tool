"""Shared provenance, identity, and readiness helpers for forecast adapters."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
from typing import Any

import duckdb

from backend.quant_platform.forecast_ledger import ForecastRecord


SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_timestamp_ns(value: str) -> int:
    return int(
        datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
        * 1_000_000_000
    )


def require_sha256(name: str, value: object) -> str:
    text = str(value or "").strip().lower()
    if not SHA256_RE.fullmatch(text):
        raise ValueError(f"{name}:missing_or_invalid_sha256")
    return text


def require_clean_commit(value: object) -> str:
    text = str(value or "").strip().lower()
    if not COMMIT_RE.fullmatch(text):
        raise ValueError("code_commit:missing_dirty_or_invalid")
    return text


def require_training_cutoff(value: object) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("training_cutoff_ns:missing_or_invalid") from exc
    if result <= 0:
        raise ValueError("training_cutoff_ns:missing_or_invalid")
    return result


def require_probability(name: str, value: object) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name}:not_numeric") from exc
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{name}:outside_probability_range")
    return result


def deterministic_forecast_id(record: ForecastRecord) -> str:
    return canonical_sha256(
        {
            "contract_key": record.contract.key,
            "forecast_at_ns": record.forecast_at_ns,
            "candidate_id": record.candidate_id,
            "model_id": record.model_id,
            "model_version": record.model_version,
            "evidence_kind": record.evidence_kind.value,
        }
    )


def read_json_meta(
    con: duckdb.DuckDBPyConnection,
    table: str,
    *,
    key_column: str = "key",
    value_column: str,
) -> dict[str, Any]:
    tables = {str(row[0]) for row in con.execute("SHOW TABLES").fetchall()}
    if table not in tables:
        raise ValueError(f"source_table_missing:{table}")
    rows = con.execute(
        f"SELECT {key_column}, {value_column} FROM {table}"
    ).fetchall()
    output: dict[str, Any] = {}
    for key, value in rows:
        try:
            output[str(key)] = json.loads(str(value))
        except json.JSONDecodeError as exc:
            raise ValueError(f"source_meta_invalid_json:{key}") from exc
    return output


@dataclass(slots=True)
class AdapterReadiness:
    adapter_id: str
    source_campaign: str
    source_head: str
    model_id: str
    contract_key: str
    target_name: str
    target_role: str
    venue: str
    instrument: str
    horizon_seconds: int
    adapter_implemented: bool
    status: str
    blocker: str = ""
    source_rows: int = 0
    forecasts_seen: int = 0
    forecasts_inserted: int = 0
    outcomes_seen: int = 0
    outcomes_inserted: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

