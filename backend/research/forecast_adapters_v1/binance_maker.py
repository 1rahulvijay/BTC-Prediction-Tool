"""Adapter for target-specific Binance event forecasts used by maker shadowing."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import duckdb

from backend.quant_platform.forecast_ledger import ForecastLedger, ForecastRecord

from .catalog import SPEC_BY_ID
from .common import (
    AdapterReadiness,
    deterministic_forecast_id,
    require_clean_commit,
    require_probability,
    require_sha256,
    require_training_cutoff,
)


SPEC_IDS = tuple(
    f"binance_event_{head}_{horizon}s"
    for horizon in (5, 15)
    for head in ("direction", "movement", "roundtrip")
)


def _readiness(adapter_id: str, status: str, blocker: str = ""):
    spec = SPEC_BY_ID[adapter_id]
    return AdapterReadiness(
        adapter_id=spec.adapter_id,
        source_campaign=spec.source_campaign,
        source_head=spec.source_head,
        model_id=spec.model_id,
        contract_key=spec.contract.key,
        target_name=spec.contract.target_name,
        target_role=spec.contract.role.value,
        venue=spec.contract.venue,
        instrument=spec.contract.instrument,
        horizon_seconds=spec.contract.horizon_seconds,
        adapter_implemented=True,
        status=status,
        blocker=blocker,
    )


def adapt(
    source_db: Path,
    ledger: ForecastLedger,
) -> dict[str, AdapterReadiness]:
    results = {
        adapter_id: _readiness(adapter_id, "SOURCE_DB_MISSING")
        for adapter_id in SPEC_IDS
    }
    if not source_db.is_file():
        return results
    try:
        with duckdb.connect(str(source_db), read_only=True) as con:
            columns = {
                str(row[1])
                for row in con.execute("PRAGMA table_info('candidates')").fetchall()
            }
            required = {
                "dataset_sha256",
                "training_cutoff_ns",
                "source_protocol_hash",
                "code_dirty",
            }
            missing = sorted(required - columns)
            if missing:
                raise ValueError(
                    "candidate_provenance_columns_missing:" + ",".join(missing)
                )
            rows = con.execute(
                """
                SELECT candidate_id, decision_ts_ms, horizon_seconds,
                       p_direction, p_movement, p_roundtrip, book_age_ms,
                       model_bundle_hash, feature_schema_hash, code_commit,
                       dataset_sha256, training_cutoff_ns,
                       source_protocol_hash, code_dirty
                FROM candidates
                ORDER BY decision_ts_ms, candidate_id
                """
            ).fetchall()
    except (duckdb.Error, ValueError) as exc:
        status = (
            "SOURCE_DB_UNAVAILABLE"
            if isinstance(exc, duckdb.Error)
            else "PROVENANCE_BLOCKED"
        )
        return {
            adapter_id: _readiness(adapter_id, status, str(exc))
            for adapter_id in SPEC_IDS
        }

    forecasts_by_spec: dict[str, list[ForecastRecord]] = {
        adapter_id: [] for adapter_id in SPEC_IDS
    }
    for row in rows:
        horizon = int(row[2])
        if horizon not in {5, 15}:
            continue
        forecast_ns = int(row[1]) * 1_000_000
        try:
            code_commit = require_clean_commit(row[9])
            if bool(row[13]):
                raise ValueError("code_commit:dirty_source")
            dataset_hash = require_sha256("dataset_sha256", row[10])
            training_cutoff_ns = require_training_cutoff(row[11])
            protocol_hash = require_sha256("source_protocol_hash", row[12])
            model_hash = require_sha256("model_bundle_hash", row[7])
            feature_hash = require_sha256("feature_schema_hash", row[8])
        except ValueError as exc:
            for head in ("direction", "movement", "roundtrip"):
                adapter_id = f"binance_event_{head}_{horizon}s"
                results[adapter_id] = _readiness(
                    adapter_id, "PROVENANCE_BLOCKED", str(exc)
                )
            continue
        if training_cutoff_ns >= forecast_ns:
            for head in ("direction", "movement", "roundtrip"):
                adapter_id = f"binance_event_{head}_{horizon}s"
                results[adapter_id] = _readiness(
                    adapter_id,
                    "TIMESTAMP_LEAKAGE_BLOCKED",
                    "training cutoff is not before forecast",
                )
            continue
        for head, position in (
            ("direction", 3),
            ("movement", 4),
            ("roundtrip", 5),
        ):
            if row[position] is None:
                continue
            adapter_id = f"binance_event_{head}_{horizon}s"
            spec = SPEC_BY_ID[adapter_id]
            provisional = ForecastRecord(
                forecast_id="pending",
                forecast_at_ns=forecast_ns,
                market_id="BINANCE_SPOT:BTCUSDT",
                candidate_id=str(row[0]),
                model_id=spec.model_id,
                model_version=f"{spec.model_version}:{model_hash[:12]}",
                training_cutoff_ns=training_cutoff_ns,
                code_commit=code_commit,
                dataset_sha256=dataset_hash,
                feature_schema_sha256=feature_hash,
                protocol_sha256=protocol_hash,
                contract=spec.contract,
                evidence_kind=spec.evidence_kind,
                predicted_probability=require_probability(
                    spec.source_head, row[position]
                ),
                regime="UNKNOWN",
                data_quality=max(
                    0.0, min(1.0, 1.0 - float(row[6]) / 1000.0)
                ),
            )
            forecasts_by_spec[adapter_id].append(
                replace(
                    provisional,
                    forecast_id=deterministic_forecast_id(provisional),
                )
            )

    for adapter_id in SPEC_IDS:
        if results[adapter_id].status in {
            "PROVENANCE_BLOCKED",
            "TIMESTAMP_LEAKAGE_BLOCKED",
        }:
            continue
        forecasts = forecasts_by_spec[adapter_id]
        inserted = ledger.append_forecasts(forecasts)
        result = _readiness(
            adapter_id,
            "FORWARD_OUTCOME_COLLECTION_BLOCKED"
            if forecasts
            else "SOURCE_NO_ELIGIBLE_ROWS",
            (
                "exact spot-path target outcomes are not persisted by the "
                "current maker recorder"
                if forecasts
                else ""
            ),
        )
        result.source_rows = len(rows)
        result.forecasts_seen = len(forecasts)
        result.forecasts_inserted = inserted
        results[adapter_id] = result
    return results
