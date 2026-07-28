"""Adapter for forward-only Polymarket ask-worsening forecasts."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import duckdb

from backend.quant_platform.forecast_ledger import (
    ForecastLedger,
    ForecastOutcome,
    ForecastRecord,
)

from .catalog import SPEC_BY_ID
from .common import (
    AdapterReadiness,
    deterministic_forecast_id,
    read_json_meta,
    require_clean_commit,
    require_probability,
    require_sha256,
    require_training_cutoff,
    sha256_file,
)


SPEC_IDS = tuple(
    f"repricing_{side}_5s_{family}"
    for side in ("up", "down")
    for family in ("baseline", "evidence")
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
    protocol_path: Path,
) -> dict[str, AdapterReadiness]:
    results = {
        adapter_id: _readiness(adapter_id, "SOURCE_DB_MISSING")
        for adapter_id in SPEC_IDS
    }
    if not source_db.is_file():
        return results
    try:
        with duckdb.connect(str(source_db), read_only=True) as con:
            meta = read_json_meta(
                con,
                "repricing_shadow_meta",
                value_column="value",
            )
            identities = meta.get("artifact_identities")
            if not isinstance(identities, dict):
                raise ValueError("source_provenance_missing")
            protocol_hash = require_sha256(
                "protocol_sha256", identities.get("protocol_sha256")
            )
            if protocol_hash != sha256_file(protocol_path):
                raise ValueError("protocol_hash_mismatch")
            code_commit = require_clean_commit(identities.get("code_commit"))
            if bool(identities.get("code_dirty")):
                raise ValueError("code_commit:dirty_source")
            dataset_hash = require_sha256(
                "dataset_sha256",
                identities.get("contract_dataset_sha256"),
            )
            feature_schema_hash = require_sha256(
                "feature_schema_sha256",
                identities.get("contract_feature_schema_sha256"),
            )
            training_cutoff_ns = require_training_cutoff(
                identities.get("training_cutoff_ns")
            )
            up_hash = require_sha256(
                "up_model_sha256", identities.get("up_model_sha256")
            )
            down_hash = require_sha256(
                "down_model_sha256", identities.get("down_model_sha256")
            )
            rows = con.execute(
                """
                SELECT c.candidate_id, c.decision_ts, c.market_id,
                       c.selected_side, c.current_ask,
                       c.up_baseline_worsening_probability,
                       c.up_worsening_probability,
                       c.down_baseline_worsening_probability,
                       c.down_worsening_probability,
                       c.quote_age_seconds,
                       o.actual_elapsed_seconds, o.observed_ts, o.ask
                FROM repricing_candidates c
                LEFT JOIN repricing_observations o
                  ON o.candidate_id = c.candidate_id
                 AND o.offset_seconds = 5
                ORDER BY c.decision_ts, c.candidate_id
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
    outcomes_by_spec: dict[str, list[ForecastOutcome]] = {
        adapter_id: [] for adapter_id in SPEC_IDS
    }
    for row in rows:
        side = str(row[3]).lower()
        if side not in {"up", "down"}:
            continue
        forecast_ns = int(float(row[1]) * 1_000_000_000)
        if training_cutoff_ns >= forecast_ns:
            adapter_ids = (
                f"repricing_{side}_5s_baseline",
                f"repricing_{side}_5s_evidence",
            )
            for adapter_id in adapter_ids:
                results[adapter_id] = _readiness(
                    adapter_id,
                    "TIMESTAMP_LEAKAGE_BLOCKED",
                    "training cutoff is not before forecast",
                )
            continue
        positions = (
            (f"repricing_{side}_5s_baseline", 5 if side == "up" else 7),
            (f"repricing_{side}_5s_evidence", 6 if side == "up" else 8),
        )
        model_hash = up_hash if side == "up" else down_hash
        quality = max(0.0, min(1.0, 1.0 - float(row[9]) / 5.0))
        for adapter_id, position in positions:
            spec = SPEC_BY_ID[adapter_id]
            probability = require_probability(spec.source_head, row[position])
            provisional = ForecastRecord(
                forecast_id="pending",
                forecast_at_ns=forecast_ns,
                market_id=str(row[2]),
                candidate_id=str(row[0]),
                model_id=spec.model_id,
                model_version=f"{spec.model_version}:{model_hash[:12]}",
                training_cutoff_ns=training_cutoff_ns,
                code_commit=code_commit,
                dataset_sha256=dataset_hash,
                feature_schema_sha256=feature_schema_hash,
                protocol_sha256=protocol_hash,
                contract=spec.contract,
                evidence_kind=spec.evidence_kind,
                predicted_probability=probability,
                regime="UNKNOWN",
                data_quality=quality,
            )
            forecast = replace(
                provisional,
                forecast_id=deterministic_forecast_id(provisional),
            )
            forecasts_by_spec[adapter_id].append(forecast)
            elapsed = row[10]
            observed_ts = row[11]
            observed_ask = row[12]
            if (
                elapsed is not None
                and observed_ts is not None
                and observed_ask is not None
                and 5.0 <= float(elapsed) <= 6.0
                and float(observed_ts) > float(row[1])
            ):
                outcomes_by_spec[adapter_id].append(
                    ForecastOutcome(
                        forecast_id=forecast.forecast_id,
                        resolved_at_ns=int(
                            float(observed_ts) * 1_000_000_000
                        ),
                        actual_outcome=(
                            1.0
                            if float(observed_ask) - float(row[4])
                            >= 0.01 - 1e-12
                            else 0.0
                        ),
                        resolution_source=(
                            "repricing_selected_side_first_valid_5s_quote"
                        ),
                    )
                )

    for adapter_id in SPEC_IDS:
        if results[adapter_id].status == "TIMESTAMP_LEAKAGE_BLOCKED":
            continue
        forecasts = forecasts_by_spec[adapter_id]
        outcomes = outcomes_by_spec[adapter_id]
        inserted = ledger.append_forecasts(forecasts)
        resolved = ledger.resolve_many(outcomes)
        result = _readiness(
            adapter_id,
            "READY_RESOLVED"
            if outcomes
            else "FORWARD_COLLECTING"
            if forecasts
            else "SOURCE_NO_ELIGIBLE_ROWS",
        )
        result.source_rows = len(rows)
        result.forecasts_seen = len(forecasts)
        result.forecasts_inserted = inserted
        result.outcomes_seen = len(outcomes)
        result.outcomes_inserted = resolved
        results[adapter_id] = result
    return results

