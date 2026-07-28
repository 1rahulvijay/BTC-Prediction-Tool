"""Adapter for frozen 1h Polymarket fair-value baselines."""

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
    canonical_sha256,
    deterministic_forecast_id,
    read_json_meta,
    require_clean_commit,
    require_probability,
    require_sha256,
    sha256_file,
    utc_timestamp_ns,
)


SPEC_IDS = (
    "poly_1h_market_prior",
    "poly_1h_distance_time",
    "poly_1h_volatility_mixture",
)
FEATURES = {
    "poly_1h_market_prior": ("up_mid", "down_mid"),
    "poly_1h_distance_time": (
        "binance_open",
        "binance_price",
        "seconds_left",
        "slow_volatility",
    ),
    "poly_1h_volatility_mixture": (
        "binance_open",
        "binance_price",
        "seconds_left",
        "fast_volatility",
        "slow_volatility",
        "jump_volatility",
    ),
}


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
                con, "campaign_meta", value_column="value_json"
            )
            protocol = meta.get("protocol")
            provenance = meta.get("provenance")
            if not isinstance(protocol, dict) or not isinstance(provenance, dict):
                raise ValueError("source_provenance_missing")
            local_protocol_hash = sha256_file(protocol_path)
            protocol_hash = require_sha256(
                "protocol_sha256", provenance.get("protocol_sha256")
            )
            if protocol_hash != local_protocol_hash:
                raise ValueError("protocol_hash_mismatch")
            code_commit = require_clean_commit(provenance.get("code_commit"))
            if bool(provenance.get("code_dirty")):
                raise ValueError("code_commit:dirty_source")
            code_hash = require_sha256(
                "code_sha256", provenance.get("code_sha256")
            )
            training_cutoff_ns = utc_timestamp_ns(
                str(protocol["frozen_at_utc"])
            )
            checkpoints = [
                int(value)
                for value in protocol["reporting"][
                    "checkpoints_seconds_left"
                ]
            ]
            values_sql = ",".join(f"({value})" for value in checkpoints)
            rows = con.execute(
                f"""
                WITH ranked AS (
                    SELECT s.slug, s.observed_ts_ms, s.seconds_left,
                           s.p_a_market, s.p_b_distance_time,
                           s.p_c_volatility_mixture, s.invalid_reason,
                           c.checkpoint,
                           row_number() OVER (
                               PARTITION BY s.slug, c.checkpoint
                               ORDER BY abs(s.seconds_left - c.checkpoint),
                                        s.observed_ts_ms
                           ) AS rank
                    FROM hourly_snapshots s
                    CROSS JOIN (VALUES {values_sql}) c(checkpoint)
                    WHERE s.valid
                      AND abs(s.seconds_left - c.checkpoint) <= 2.0
                )
                SELECT r.slug, r.observed_ts_ms, r.seconds_left,
                       r.p_a_market, r.p_b_distance_time,
                       r.p_c_volatility_mixture, r.checkpoint,
                       x.finalized_kline, x.binance_side,
                       x.polymarket_side, x.sides_match, x.resolved_ts_ms
                FROM ranked r
                LEFT JOIN hourly_resolutions x USING(slug)
                WHERE r.rank = 1
                ORDER BY r.observed_ts_ms, r.slug, r.checkpoint
                """
            ).fetchall()
    except (duckdb.Error, KeyError, TypeError, ValueError) as exc:
        status = (
            "SOURCE_DB_UNAVAILABLE"
            if isinstance(exc, duckdb.Error)
            else "PROVENANCE_BLOCKED"
        )
        return {
            adapter_id: _readiness(adapter_id, status, str(exc))
            for adapter_id in SPEC_IDS
        }

    dataset_hash = canonical_sha256(
        {
            "kind": "analytic_no_training_dataset",
            "campaign": "POLY_1H_DIGITAL_FAIR_VALUE_V1",
            "protocol_sha256": protocol_hash,
        }
    )
    forecasts_by_spec: dict[str, list[ForecastRecord]] = {
        adapter_id: [] for adapter_id in SPEC_IDS
    }
    outcomes_by_spec: dict[str, list[ForecastOutcome]] = {
        adapter_id: [] for adapter_id in SPEC_IDS
    }
    fields = {
        "poly_1h_market_prior": 3,
        "poly_1h_distance_time": 4,
        "poly_1h_volatility_mixture": 5,
    }
    for row in rows:
        observed_ns = int(row[1]) * 1_000_000
        if observed_ns <= training_cutoff_ns:
            return {
                adapter_id: _readiness(
                    adapter_id,
                    "TIMESTAMP_LEAKAGE_BLOCKED",
                    "training cutoff is not before forecast",
                )
                for adapter_id in SPEC_IDS
            }
        candidate_id = f"{row[0]}|left={int(row[6])}"
        resolved = (
            bool(row[7])
            and row[8] in {"UP", "DOWN"}
            and row[9] in {"UP", "DOWN"}
            and row[10] is True
            and row[11] is not None
        )
        for adapter_id in SPEC_IDS:
            spec = SPEC_BY_ID[adapter_id]
            probability = require_probability(
                spec.source_head, row[fields[adapter_id]]
            )
            provisional = ForecastRecord(
                forecast_id="pending",
                forecast_at_ns=observed_ns,
                market_id=str(row[0]),
                candidate_id=candidate_id,
                model_id=spec.model_id,
                model_version=f"{spec.model_version}:{code_hash[:12]}",
                training_cutoff_ns=training_cutoff_ns,
                code_commit=code_commit,
                dataset_sha256=dataset_hash,
                feature_schema_sha256=canonical_sha256(
                    {"ordered_features": FEATURES[adapter_id]}
                ),
                protocol_sha256=protocol_hash,
                contract=spec.contract,
                evidence_kind=spec.evidence_kind,
                predicted_probability=probability,
                regime="UNKNOWN",
                data_quality=1.0,
            )
            forecast = replace(
                provisional,
                forecast_id=deterministic_forecast_id(provisional),
            )
            forecasts_by_spec[adapter_id].append(forecast)
            if resolved:
                outcomes_by_spec[adapter_id].append(
                    ForecastOutcome(
                        forecast_id=forecast.forecast_id,
                        resolved_at_ns=int(row[11]) * 1_000_000,
                        actual_outcome=1.0 if row[8] == "UP" else 0.0,
                        resolution_source=(
                            "finalized_binance_1h_kline_reconciled_polymarket"
                        ),
                    )
                )

    for adapter_id in SPEC_IDS:
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
