"""Immutable universal model-forecast and resolved-outcome ledger."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from enum import StrEnum
import hashlib
import json
import math
from pathlib import Path
import re
from threading import RLock
from typing import Iterator

import duckdb

from .model_roles import ModelRole, TargetContract


class EvidenceKind(StrEnum):
    IN_SAMPLE = "IN_SAMPLE"
    OOF = "OOF"
    LOCKED_TEST = "LOCKED_TEST"
    FORWARD = "FORWARD"

    @property
    def meta_training_eligible(self) -> bool:
        return self in {EvidenceKind.OOF, EvidenceKind.FORWARD}


_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class ForecastRecord:
    forecast_id: str
    forecast_at_ns: int
    market_id: str
    candidate_id: str
    model_id: str
    model_version: str
    training_cutoff_ns: int
    code_commit: str
    dataset_sha256: str
    feature_schema_sha256: str
    protocol_sha256: str
    contract: TargetContract
    evidence_kind: EvidenceKind
    predicted_probability: float | None = None
    predicted_mean: float | None = None
    predicted_q10: float | None = None
    predicted_q20: float | None = None
    predicted_q50: float | None = None
    predicted_q80: float | None = None
    predicted_q90: float | None = None
    regime: str = "UNKNOWN"
    data_quality: float = 0.0

    def __post_init__(self) -> None:
        required = (
            self.forecast_id,
            self.market_id,
            self.candidate_id,
            self.model_id,
            self.model_version,
            self.code_commit,
            self.dataset_sha256,
            self.feature_schema_sha256,
            self.protocol_sha256,
            self.regime,
        )
        if any(not str(value).strip() for value in required):
            raise ValueError("forecast identity and provenance fields are required")
        if self.forecast_at_ns <= 0 or self.training_cutoff_ns <= 0:
            raise ValueError("forecast and training-cutoff timestamps must be positive")
        if self.training_cutoff_ns >= self.forecast_at_ns:
            raise ValueError("training_cutoff_ns must precede forecast_at_ns")
        if not _GIT_COMMIT_PATTERN.fullmatch(self.code_commit):
            raise ValueError("code_commit must be a clean 40-character Git commit")
        for name in (
            "dataset_sha256",
            "feature_schema_sha256",
            "protocol_sha256",
        ):
            if not _SHA256_PATTERN.fullmatch(str(getattr(self, name))):
                raise ValueError(f"{name} must be a lowercase SHA-256 digest")
        if not math.isfinite(self.data_quality) or not 0 <= self.data_quality <= 1:
            raise ValueError("data_quality must be in [0, 1]")
        outputs = (
            self.predicted_probability,
            self.predicted_mean,
            self.predicted_q10,
            self.predicted_q20,
            self.predicted_q50,
            self.predicted_q80,
            self.predicted_q90,
        )
        present = [float(value) for value in outputs if value is not None]
        if not present or not all(math.isfinite(value) for value in present):
            raise ValueError("at least one finite forecast output is required")
        if self.predicted_probability is not None and not (
            0 <= self.predicted_probability <= 1
        ):
            raise ValueError("predicted_probability must be in [0, 1]")
        quantiles = (
            self.predicted_q10,
            self.predicted_q20,
            self.predicted_q50,
            self.predicted_q80,
            self.predicted_q90,
        )
        specified = [value is not None for value in quantiles]
        if any(specified) and not all(specified):
            raise ValueError("all forecast quantiles must be supplied together")
        if all(specified):
            values = [float(value) for value in quantiles if value is not None]
            if values != sorted(values):
                raise ValueError("forecast quantiles must be non-decreasing")


@dataclass(frozen=True, slots=True)
class ForecastOutcome:
    forecast_id: str
    resolved_at_ns: int
    actual_outcome: float
    resolution_source: str
    gross_return: float | None = None
    net_return: float | None = None
    fees: float | None = None
    slippage: float | None = None
    fill_quantity: float | None = None
    latency_ms: float | None = None

    def __post_init__(self) -> None:
        if not self.forecast_id.strip() or not self.resolution_source.strip():
            raise ValueError("forecast_id and resolution_source are required")
        if self.resolved_at_ns <= 0:
            raise ValueError("resolved_at_ns must be positive")
        if not math.isfinite(self.actual_outcome):
            raise ValueError("actual_outcome must be finite")
        economics = (
            self.gross_return,
            self.net_return,
            self.fees,
            self.slippage,
            self.fill_quantity,
            self.latency_ms,
        )
        if not all(value is None or math.isfinite(value) for value in economics):
            raise ValueError("supplied outcome economics must be finite")
        nonnegative = (
            self.fees,
            self.slippage,
            self.fill_quantity,
            self.latency_ms,
        )
        if any(value is not None and value < 0 for value in nonnegative):
            raise ValueError("costs, fill quantity, and latency cannot be negative")


_FORECAST_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_forecasts(
    forecast_id VARCHAR PRIMARY KEY,
    forecast_at_ns BIGINT NOT NULL,
    market_id VARCHAR NOT NULL,
    venue VARCHAR NOT NULL,
    instrument VARCHAR NOT NULL,
    horizon_seconds BIGINT NOT NULL,
    candidate_id VARCHAR NOT NULL,
    model_id VARCHAR NOT NULL,
    model_version VARCHAR NOT NULL,
    training_cutoff_ns BIGINT NOT NULL,
    code_commit VARCHAR NOT NULL,
    dataset_sha256 VARCHAR NOT NULL,
    feature_schema_sha256 VARCHAR NOT NULL,
    protocol_sha256 VARCHAR NOT NULL,
    target_name VARCHAR NOT NULL,
    target_role VARCHAR NOT NULL,
    outcome_semantics VARCHAR NOT NULL,
    contract_key VARCHAR NOT NULL,
    evidence_kind VARCHAR NOT NULL,
    predicted_probability DOUBLE,
    predicted_mean DOUBLE,
    predicted_q10 DOUBLE,
    predicted_q20 DOUBLE,
    predicted_q50 DOUBLE,
    predicted_q80 DOUBLE,
    predicted_q90 DOUBLE,
    regime VARCHAR NOT NULL,
    data_quality DOUBLE NOT NULL,
    payload_json VARCHAR NOT NULL,
    payload_sha256 VARCHAR NOT NULL UNIQUE
)
"""

_OUTCOME_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_forecast_outcomes(
    forecast_id VARCHAR PRIMARY KEY,
    resolved_at_ns BIGINT NOT NULL,
    actual_outcome DOUBLE NOT NULL,
    gross_return DOUBLE,
    net_return DOUBLE,
    fees DOUBLE,
    slippage DOUBLE,
    fill_quantity DOUBLE,
    latency_ms DOUBLE,
    resolution_source VARCHAR NOT NULL,
    payload_json VARCHAR NOT NULL,
    payload_sha256 VARCHAR NOT NULL UNIQUE
)
"""

_OUTCOME_COLUMNS = (
    "forecast_id",
    "resolved_at_ns",
    "actual_outcome",
    "gross_return",
    "net_return",
    "fees",
    "slippage",
    "fill_quantity",
    "latency_ms",
    "resolution_source",
    "payload_json",
    "payload_sha256",
)


def _canonical_payload(value: object) -> tuple[str, str]:
    if isinstance(value, ForecastRecord):
        payload = {
            **asdict(value),
            "contract": {
                **asdict(value.contract),
                "role": value.contract.role.value,
            },
            "evidence_kind": value.evidence_kind.value,
        }
    else:
        payload = asdict(value)
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    )
    return encoded, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class ForecastLedger:
    """Append-only prediction records with outcomes stored separately."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        with self._connect() as con:
            con.execute(_FORECAST_SCHEMA)
            con.execute(_OUTCOME_SCHEMA)
            self._migrate_nullable_outcome_economics(con)
            con.execute(
                "CREATE INDEX IF NOT EXISTS model_forecast_training_idx "
                "ON model_forecasts(contract_key, evidence_kind, forecast_at_ns)"
            )

    @staticmethod
    def _migrate_nullable_outcome_economics(
        con: duckdb.DuckDBPyConnection,
    ) -> None:
        info = con.execute(
            "PRAGMA table_info('model_forecast_outcomes')"
        ).fetchall()
        required_nullable = {
            "gross_return",
            "net_return",
            "fees",
            "slippage",
            "fill_quantity",
            "latency_ms",
        }
        nonnullable = {
            str(row[1]) for row in info if bool(row[3])
        }
        if not required_nullable.intersection(nonnullable):
            return
        replacement_schema = _OUTCOME_SCHEMA.replace(
            "model_forecast_outcomes(",
            "model_forecast_outcomes_v2(",
            1,
        )
        con.execute("BEGIN TRANSACTION")
        try:
            con.execute(
                "DROP TABLE IF EXISTS model_forecast_outcomes_v2"
            )
            con.execute(replacement_schema)
            columns = ",".join(_OUTCOME_COLUMNS)
            con.execute(
                f"INSERT INTO model_forecast_outcomes_v2 ({columns}) "
                f"SELECT {columns} FROM model_forecast_outcomes"
            )
            con.execute("DROP TABLE model_forecast_outcomes")
            con.execute(
                "ALTER TABLE model_forecast_outcomes_v2 "
                "RENAME TO model_forecast_outcomes"
            )
        except Exception:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")

    @contextmanager
    def _connect(self) -> Iterator[duckdb.DuckDBPyConnection]:
        con = duckdb.connect(str(self.path))
        try:
            yield con
        finally:
            con.close()

    @staticmethod
    def _forecast_values(
        record: ForecastRecord,
    ) -> tuple[list[object], str]:
        payload_json, digest = _canonical_payload(record)
        contract = record.contract
        values = [
            record.forecast_id,
            record.forecast_at_ns,
            record.market_id,
            contract.venue,
            contract.instrument,
            contract.horizon_seconds,
            record.candidate_id,
            record.model_id,
            record.model_version,
            record.training_cutoff_ns,
            record.code_commit,
            record.dataset_sha256,
            record.feature_schema_sha256,
            record.protocol_sha256,
            contract.target_name,
            contract.role.value,
            contract.outcome_semantics,
            contract.key,
            record.evidence_kind.value,
            record.predicted_probability,
            record.predicted_mean,
            record.predicted_q10,
            record.predicted_q20,
            record.predicted_q50,
            record.predicted_q80,
            record.predicted_q90,
            record.regime,
            record.data_quality,
            payload_json,
            digest,
        ]
        return values, digest

    def append_forecast(self, record: ForecastRecord) -> str:
        values, digest = self._forecast_values(record)
        with self._lock, self._connect() as con:
            existing = con.execute(
                "SELECT payload_sha256 FROM model_forecasts WHERE forecast_id = ?",
                [record.forecast_id],
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != digest:
                    raise ValueError(
                        "forecast_id collision with different immutable content"
                    )
                return digest
            con.execute(
                "INSERT INTO model_forecasts VALUES ("
                + ",".join("?" for _ in values)
                + ")",
                values,
            )
        return digest

    def append_forecasts(self, records: list[ForecastRecord]) -> int:
        """Append one immutable batch using a single database transaction."""
        if not records:
            return 0
        prepared = [self._forecast_values(record) for record in records]
        by_identity: dict[str, str] = {}
        for record, (_values, digest) in zip(records, prepared, strict=True):
            existing = by_identity.setdefault(record.forecast_id, digest)
            if existing != digest:
                raise ValueError(
                    "forecast_id collision inside batch with different content"
                )
        inserted = 0
        with self._lock, self._connect() as con:
            con.execute("BEGIN TRANSACTION")
            try:
                for record, (values, digest) in zip(
                    records, prepared, strict=True
                ):
                    existing = con.execute(
                        "SELECT payload_sha256 FROM model_forecasts "
                        "WHERE forecast_id = ?",
                        [record.forecast_id],
                    ).fetchone()
                    if existing is not None:
                        if str(existing[0]) != digest:
                            raise ValueError(
                                "forecast_id collision with different "
                                "immutable content"
                            )
                        continue
                    con.execute(
                        "INSERT INTO model_forecasts VALUES ("
                        + ",".join("?" for _ in values)
                        + ")",
                        values,
                    )
                    inserted += 1
            except Exception:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")
        return inserted

    def resolve(self, outcome: ForecastOutcome) -> str:
        payload_json, digest = _canonical_payload(outcome)
        with self._lock, self._connect() as con:
            parent = con.execute(
                "SELECT forecast_at_ns FROM model_forecasts WHERE forecast_id = ?",
                [outcome.forecast_id],
            ).fetchone()
            if parent is None:
                raise KeyError(f"unknown forecast_id:{outcome.forecast_id}")
            if outcome.resolved_at_ns <= int(parent[0]):
                raise ValueError("outcome must resolve after its forecast")
            existing = con.execute(
                "SELECT payload_sha256 FROM model_forecast_outcomes "
                "WHERE forecast_id = ?",
                [outcome.forecast_id],
            ).fetchone()
            if existing is not None:
                if str(existing[0]) != digest:
                    raise ValueError(
                        "forecast outcome collision with different immutable content"
                    )
                return digest
            con.execute(
                "INSERT INTO model_forecast_outcomes VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                [
                    outcome.forecast_id,
                    outcome.resolved_at_ns,
                    outcome.actual_outcome,
                    outcome.gross_return,
                    outcome.net_return,
                    outcome.fees,
                    outcome.slippage,
                    outcome.fill_quantity,
                    outcome.latency_ms,
                    outcome.resolution_source,
                    payload_json,
                    digest,
                ],
            )
        return digest

    def resolve_many(self, outcomes: list[ForecastOutcome]) -> int:
        """Append resolved labels separately from forecasts in one transaction."""
        if not outcomes:
            return 0
        prepared = [
            (outcome, *_canonical_payload(outcome)) for outcome in outcomes
        ]
        by_identity: dict[str, str] = {}
        for outcome, _payload_json, digest in prepared:
            existing = by_identity.setdefault(outcome.forecast_id, digest)
            if existing != digest:
                raise ValueError(
                    "forecast outcome collision inside batch with different content"
                )
        inserted = 0
        with self._lock, self._connect() as con:
            con.execute("BEGIN TRANSACTION")
            try:
                for outcome, payload_json, digest in prepared:
                    parent = con.execute(
                        "SELECT forecast_at_ns FROM model_forecasts "
                        "WHERE forecast_id = ?",
                        [outcome.forecast_id],
                    ).fetchone()
                    if parent is None:
                        raise KeyError(
                            f"unknown forecast_id:{outcome.forecast_id}"
                        )
                    if outcome.resolved_at_ns <= int(parent[0]):
                        raise ValueError(
                            "outcome must resolve after its forecast"
                        )
                    existing = con.execute(
                        "SELECT payload_sha256 FROM model_forecast_outcomes "
                        "WHERE forecast_id = ?",
                        [outcome.forecast_id],
                    ).fetchone()
                    if existing is not None:
                        if str(existing[0]) != digest:
                            raise ValueError(
                                "forecast outcome collision with different "
                                "immutable content"
                            )
                        continue
                    con.execute(
                        "INSERT INTO model_forecast_outcomes VALUES "
                        "(?,?,?,?,?,?,?,?,?,?,?,?)",
                        [
                            outcome.forecast_id,
                            outcome.resolved_at_ns,
                            outcome.actual_outcome,
                            outcome.gross_return,
                            outcome.net_return,
                            outcome.fees,
                            outcome.slippage,
                            outcome.fill_quantity,
                            outcome.latency_ms,
                            outcome.resolution_source,
                            payload_json,
                            digest,
                        ],
                    )
                    inserted += 1
            except Exception:
                con.execute("ROLLBACK")
                raise
            con.execute("COMMIT")
        return inserted

    def training_rows(
        self,
        contract: TargetContract,
        *,
        minimum_data_quality: float = 0.0,
        evidence_kinds: tuple[EvidenceKind, ...] = (
            EvidenceKind.OOF,
            EvidenceKind.FORWARD,
        ),
    ) -> list[dict[str, object]]:
        if (
            not math.isfinite(minimum_data_quality)
            or not 0 <= minimum_data_quality <= 1
        ):
            raise ValueError("minimum_data_quality must be in [0, 1]")
        if not evidence_kinds or any(
            not kind.meta_training_eligible for kind in evidence_kinds
        ):
            raise ValueError("meta-training accepts OOF/FORWARD evidence only")
        placeholders = ",".join("?" for _ in evidence_kinds)
        query = (
            "SELECT f.forecast_id, f.forecast_at_ns, f.market_id, f.candidate_id, "
            "f.model_id, f.model_version, f.evidence_kind, "
            "f.predicted_probability, f.predicted_mean, f.predicted_q10, "
            "f.predicted_q20, f.predicted_q50, f.predicted_q80, f.predicted_q90, "
            "f.regime, f.data_quality, o.actual_outcome, o.net_return "
            "FROM model_forecasts f JOIN model_forecast_outcomes o USING(forecast_id) "
            f"WHERE f.contract_key = ? AND f.evidence_kind IN ({placeholders}) "
            "AND f.data_quality >= ? ORDER BY f.forecast_at_ns, f.forecast_id"
        )
        params: list[object] = [
            contract.key,
            *(kind.value for kind in evidence_kinds),
            minimum_data_quality,
        ]
        with self._connect() as con:
            cursor = con.execute(query, params)
            names = [item[0] for item in cursor.description]
            return [dict(zip(names, row, strict=True)) for row in cursor.fetchall()]

    def counts(self) -> dict[str, int]:
        with self._connect() as con:
            forecasts = int(
                con.execute("SELECT count(*) FROM model_forecasts").fetchone()[0]
            )
            resolved = int(
                con.execute(
                    "SELECT count(*) FROM model_forecast_outcomes"
                ).fetchone()[0]
            )
        return {"forecasts": forecasts, "resolved": resolved}

    def verify_integrity(self) -> tuple[bool, list[str]]:
        reasons: list[str] = []
        with self._connect() as con:
            forecast_cursor = con.execute(
                "SELECT * "
                "FROM model_forecasts ORDER BY forecast_at_ns, forecast_id"
            )
            forecast_names = [item[0] for item in forecast_cursor.description]
            forecasts = [
                dict(zip(forecast_names, row, strict=True))
                for row in forecast_cursor.fetchall()
            ]
            outcome_cursor = con.execute(
                "SELECT * "
                "FROM model_forecast_outcomes ORDER BY resolved_at_ns, forecast_id"
            )
            outcome_names = [item[0] for item in outcome_cursor.description]
            outcomes = [
                dict(zip(outcome_names, row, strict=True))
                for row in outcome_cursor.fetchall()
            ]
        for row in forecasts:
            identity = str(row["forecast_id"])
            payload_json = str(row["payload_json"])
            expected_digest = hashlib.sha256(
                payload_json.encode("utf-8")
            ).hexdigest()
            if expected_digest != row["payload_sha256"]:
                reasons.append(f"forecast_hash_mismatch:{identity}")
                continue
            try:
                payload = json.loads(payload_json)
                contract_payload = payload["contract"]
                contract = TargetContract(
                    target_name=str(contract_payload["target_name"]),
                    role=ModelRole(str(contract_payload["role"])),
                    venue=str(contract_payload["venue"]),
                    instrument=str(contract_payload["instrument"]),
                    horizon_seconds=int(contract_payload["horizon_seconds"]),
                    outcome_semantics=str(contract_payload["outcome_semantics"]),
                )
                expected = {
                    "forecast_id": payload["forecast_id"],
                    "forecast_at_ns": payload["forecast_at_ns"],
                    "market_id": payload["market_id"],
                    "venue": contract.venue,
                    "instrument": contract.instrument,
                    "horizon_seconds": contract.horizon_seconds,
                    "candidate_id": payload["candidate_id"],
                    "model_id": payload["model_id"],
                    "model_version": payload["model_version"],
                    "training_cutoff_ns": payload["training_cutoff_ns"],
                    "code_commit": payload["code_commit"],
                    "dataset_sha256": payload["dataset_sha256"],
                    "feature_schema_sha256": payload["feature_schema_sha256"],
                    "protocol_sha256": payload["protocol_sha256"],
                    "target_name": contract.target_name,
                    "target_role": contract.role.value,
                    "outcome_semantics": contract.outcome_semantics,
                    "contract_key": contract.key,
                    "evidence_kind": payload["evidence_kind"],
                    "predicted_probability": payload["predicted_probability"],
                    "predicted_mean": payload["predicted_mean"],
                    "predicted_q10": payload["predicted_q10"],
                    "predicted_q20": payload["predicted_q20"],
                    "predicted_q50": payload["predicted_q50"],
                    "predicted_q80": payload["predicted_q80"],
                    "predicted_q90": payload["predicted_q90"],
                    "regime": payload["regime"],
                    "data_quality": payload["data_quality"],
                }
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                reasons.append(f"forecast_payload_invalid:{identity}")
                continue
            if any(row[key] != value for key, value in expected.items()):
                reasons.append(f"forecast_column_mismatch:{identity}")
        for row in outcomes:
            identity = str(row["forecast_id"])
            payload_json = str(row["payload_json"])
            expected_digest = hashlib.sha256(
                payload_json.encode("utf-8")
            ).hexdigest()
            if expected_digest != row["payload_sha256"]:
                reasons.append(f"outcome_hash_mismatch:{identity}")
                continue
            try:
                payload = json.loads(payload_json)
                expected = {
                    key: payload[key]
                    for key in (
                        "forecast_id",
                        "resolved_at_ns",
                        "actual_outcome",
                        "gross_return",
                        "net_return",
                        "fees",
                        "slippage",
                        "fill_quantity",
                        "latency_ms",
                        "resolution_source",
                    )
                }
            except (KeyError, TypeError, json.JSONDecodeError):
                reasons.append(f"outcome_payload_invalid:{identity}")
                continue
            if any(row[key] != value for key, value in expected.items()):
                reasons.append(f"outcome_column_mismatch:{identity}")
        return not reasons, reasons


__all__ = [
    "EvidenceKind",
    "ForecastLedger",
    "ForecastOutcome",
    "ForecastRecord",
    "ModelRole",
    "TargetContract",
]
