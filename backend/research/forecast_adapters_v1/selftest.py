"""Executing DuckDB integration tests for universal forecast adapters."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from backend.quant_platform.forecast_ledger import ForecastLedger
from backend.quant_platform.model_roles import (
    ModelRoleDefinition,
    ModelRoleRegistry,
)
from backend.research.hierarchical_ensemble_v1.report import build_report

from .catalog import SPEC_BY_ID, TARGET_SPECS
from .common import sha256_file
from .repricing import adapt as adapt_repricing
from .run_adapters import ROOT, run


COMMIT = "a" * 40
SHA = "b" * 64
FAIR_PROTOCOL = (
    ROOT
    / "backend"
    / "research"
    / "poly_1h_digital_fair_value_v1"
    / "frozen_protocol.json"
)
REPRICING_PROTOCOL = (
    ROOT
    / "backend"
    / "research"
    / "polymarket_repricing_shadow_v1"
    / "frozen_protocol.json"
)


def _fair_db(path: Path) -> None:
    protocol = json.loads(FAIR_PROTOCOL.read_text(encoding="utf-8"))
    with duckdb.connect(str(path)) as con:
        con.execute(
            """
            CREATE TABLE campaign_meta(
                key VARCHAR PRIMARY KEY,
                value_json VARCHAR,
                updated_ts_ms BIGINT
            );
            CREATE TABLE hourly_snapshots(
                slug VARCHAR,
                observed_ts_ms BIGINT,
                seconds_left DOUBLE,
                p_a_market DOUBLE,
                p_b_distance_time DOUBLE,
                p_c_volatility_mixture DOUBLE,
                invalid_reason VARCHAR,
                valid BOOLEAN
            );
            CREATE TABLE hourly_resolutions(
                slug VARCHAR,
                finalized_kline BOOLEAN,
                binance_side VARCHAR,
                polymarket_side VARCHAR,
                sides_match BOOLEAN,
                resolved_ts_ms BIGINT
            );
            """
        )
        provenance = {
            "protocol_sha256": sha256_file(FAIR_PROTOCOL),
            "code_commit": COMMIT,
            "code_dirty": False,
            "code_sha256": SHA,
        }
        con.executemany(
            "INSERT INTO campaign_meta VALUES (?, ?, ?)",
            [
                ("protocol", json.dumps(protocol), 1),
                ("provenance", json.dumps(provenance), 1),
            ],
        )
        con.execute(
            "INSERT INTO hourly_snapshots VALUES "
            "('hour-1', 1800000000000, 3300, 0.55, 0.60, 0.58, NULL, TRUE)"
        )
        con.execute(
            "INSERT INTO hourly_resolutions VALUES "
            "('hour-1', TRUE, 'UP', 'UP', TRUE, 1800003600000)"
        )


def _repricing_db(path: Path, *, cutoff_ns: int = 1_700_000_000_000_000_000):
    identities = {
        "protocol_sha256": sha256_file(REPRICING_PROTOCOL),
        "code_commit": COMMIT,
        "code_dirty": False,
        "contract_dataset_sha256": SHA,
        "contract_feature_schema_sha256": "c" * 64,
        "training_cutoff_ns": cutoff_ns,
        "up_model_sha256": "d" * 64,
        "down_model_sha256": "e" * 64,
    }
    with duckdb.connect(str(path)) as con:
        con.execute(
            """
            CREATE TABLE repricing_shadow_meta(
                key VARCHAR PRIMARY KEY,
                value VARCHAR
            );
            CREATE TABLE repricing_candidates(
                candidate_id VARCHAR,
                decision_ts DOUBLE,
                market_id VARCHAR,
                selected_side VARCHAR,
                current_ask DOUBLE,
                up_baseline_worsening_probability DOUBLE,
                up_worsening_probability DOUBLE,
                down_baseline_worsening_probability DOUBLE,
                down_worsening_probability DOUBLE,
                quote_age_seconds DOUBLE
            );
            CREATE TABLE repricing_observations(
                candidate_id VARCHAR,
                offset_seconds INTEGER,
                actual_elapsed_seconds DOUBLE,
                observed_ts DOUBLE,
                ask DOUBLE
            );
            """
        )
        con.execute(
            "INSERT INTO repricing_shadow_meta VALUES (?, ?)",
            ["artifact_identities", json.dumps(identities)],
        )
        con.execute(
            "INSERT INTO repricing_candidates VALUES "
            "('repricing-1', 1800000000.0, 'market-1', 'UP', 0.50, "
            "0.40, 0.80, 0.45, 0.55, 0.2)"
        )
        con.execute(
            "INSERT INTO repricing_observations VALUES "
            "('repricing-1', 5, 5.2, 1800000005.2, 0.52)"
        )


def _maker_db(path: Path) -> None:
    with duckdb.connect(str(path)) as con:
        con.execute(
            """
            CREATE TABLE candidates(
                candidate_id VARCHAR,
                decision_ts_ms BIGINT,
                horizon_seconds INTEGER,
                p_direction DOUBLE,
                p_movement DOUBLE,
                p_roundtrip DOUBLE,
                book_age_ms BIGINT,
                model_bundle_hash VARCHAR,
                feature_schema_hash VARCHAR,
                code_commit VARCHAR,
                dataset_sha256 VARCHAR,
                training_cutoff_ns BIGINT,
                source_protocol_hash VARCHAR,
                code_dirty BOOLEAN
            )
            """
        )
        con.execute(
            "INSERT INTO candidates VALUES "
            "('maker-1', 1800000000000, 5, 0.65, 0.70, 0.25, 10, "
            "?, ?, ?, ?, 1700000000000000000, ?, FALSE)",
            ["d" * 64, "e" * 64, COMMIT, SHA, "f" * 64],
        )


def _assert_target_mismatch() -> None:
    registry = ModelRoleRegistry()
    settlement = SPEC_BY_ID["poly_1h_market_prior"]
    repricing = SPEC_BY_ID["repricing_up_5s_evidence"]
    registry.register(
        ModelRoleDefinition(
            settlement.model_id,
            settlement.model_version,
            settlement.contract,
            ("stack",),
        )
    )
    registry.register(
        ModelRoleDefinition(
            repricing.model_id,
            repricing.model_version,
            repricing.contract,
            ("stack",),
        )
    )
    try:
        registry.require_compatible(
            [
                (settlement.model_id, settlement.model_version),
                (repricing.model_id, repricing.model_version),
            ],
            "stack",
        )
        raise AssertionError("target mismatch was accepted")
    except ValueError as exc:
        assert "target_mismatch" in str(exc)


def main() -> int:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        fair_db = root / "fair.duckdb"
        repricing_db = root / "repricing.duckdb"
        maker_db = root / "maker.duckdb"
        ledger_path = root / "ledger.duckdb"
        readiness_path = root / "readiness.json"
        _fair_db(fair_db)
        _repricing_db(repricing_db)
        _maker_db(maker_db)
        first = run(
            ledger_path=ledger_path,
            output_path=readiness_path,
            poly_1h_db=fair_db,
            repricing_db=repricing_db,
            binance_maker_db=maker_db,
            binance_paper_db=root / "missing-paper.duckdb",
        )
        assert first["ledger_counts"] == {"forecasts": 8, "resolved": 5}
        assert len(first["rows"]) == len(TARGET_SPECS)
        with duckdb.connect(str(ledger_path), read_only=True) as con:
            null_economics = int(
                con.execute(
                    "SELECT count(*) FROM model_forecast_outcomes "
                    "WHERE gross_return IS NULL AND net_return IS NULL "
                    "AND fees IS NULL AND slippage IS NULL "
                    "AND fill_quantity IS NULL AND latency_ms IS NULL"
                ).fetchone()[0]
            )
        assert null_economics == 5
        second = run(
            ledger_path=ledger_path,
            output_path=readiness_path,
            poly_1h_db=fair_db,
            repricing_db=repricing_db,
            binance_maker_db=maker_db,
            binance_paper_db=root / "missing-paper.duckdb",
        )
        assert second["ledger_counts"] == {"forecasts": 8, "resolved": 5}
        assert sum(
            int(row["forecasts_inserted"]) for row in second["rows"]
        ) == 0
        report = build_report(ledger_path, readiness_path)
        assert len(report["adapter_readiness"]) == len(TARGET_SPECS)
        assert report["forecasts"] == 8

        with duckdb.connect(str(repricing_db)) as con:
            con.execute(
                "UPDATE repricing_candidates "
                "SET up_worsening_probability = 0.10"
            )
        try:
            run(
                ledger_path=ledger_path,
                output_path=readiness_path,
                poly_1h_db=fair_db,
                repricing_db=repricing_db,
                binance_maker_db=maker_db,
                binance_paper_db=root / "missing-paper.duckdb",
            )
            raise AssertionError("immutable duplicate mutation was accepted")
        except ValueError as exc:
            assert "immutable content" in str(exc)

        leakage_db = root / "leakage.duckdb"
        _repricing_db(
            leakage_db,
            cutoff_ns=1_800_000_000_000_000_000,
        )
        leakage_ledger = ForecastLedger(root / "leakage-ledger.duckdb")
        leakage = adapt_repricing(
            leakage_db, leakage_ledger, REPRICING_PROTOCOL
        )
        assert leakage["repricing_up_5s_evidence"].status == (
            "TIMESTAMP_LEAKAGE_BLOCKED"
        )
        assert leakage_ledger.counts()["forecasts"] == 0

        with duckdb.connect(str(ledger_path)) as con:
            con.execute(
                "UPDATE model_forecasts SET predicted_probability = 0.01 "
                "WHERE forecast_id = (SELECT min(forecast_id) "
                "FROM model_forecasts)"
            )
        integrity_ok, reasons = ForecastLedger(ledger_path).verify_integrity()
        assert not integrity_ok
        assert any("forecast_column_mismatch" in reason for reason in reasons)

    _assert_target_mismatch()
    print("forecast adapters v1: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
