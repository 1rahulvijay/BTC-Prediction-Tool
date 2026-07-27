"""Regression coverage for durable logging, eligibility, and own-L2 outcomes."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import duckdb

from .evaluate_complete_trade_m0_v2_forward import causal_selection
from .l2_outcome_reconstruction import (
    recording_sha256,
    reconstruct_v2_outcomes,
)
from .trade_forecast_logger import (
    OUTCOME_V2_SCHEMA_VERSION,
    _spool_v2_failure,
    connect,
    ensure_v2_schema,
    log_forward_prediction_v2,
    read_resolved_outcomes,
    replay_pending_v2,
)
from .trade_outcome_resolver import resolve_v2
from .trade_schema import (
    LEDGER_V2_SCHEMA_VERSION,
    M0_V2,
    evidence_eligibility_hash,
)


def _forecast(
    forecast_id: str,
    side: str,
    *,
    score: float = 0.9,
    run_id: str = "evidence-run",
) -> dict:
    row = {
        "forecast_id": forecast_id,
        "round_id": "round-1",
        "exposure_id": "round-1@120",
        "horizon": 5,
        "seconds_left": 120,
        "side": side,
        "requested_qty": 5.0,
        "ledger_schema_version": LEDGER_V2_SCHEMA_VERSION,
        "candidate_valid": True,
        "candidate_reasons_json": "[]",
        "all_features_finite": True,
        "decision_entry_complete": True,
        "decision_book_age_s": 0.1,
        "conservative_capacity_q10": 10.0,
        "cost_q80": 0.01,
        "eligibility_passed": True,
        "prediction_ts_ms": 1_000_000,
        "prediction_ts_s": 1000.0,
        "model_bundle_sha256": "m" * 64,
        "bundle_manifest_sha256": "b" * 64,
        "feature_schema_sha256": "f" * 64,
        "policy_sha256": "p" * 64,
        "threshold_sha256": "t" * 64,
        "prereg_sha256": M0_V2["prereg_sha256"],
        "clarification_sha256": M0_V2["clarification_001_sha256"],
        "feature_values_sha256": "v" * 64,
        "prereg_frozen_at_s": 900.0,
        "model_frozen_at_s": 900.0,
        "threshold_frozen_at_s": 900.0,
        "entry_threshold": 0.7,
        "score": score,
        "action": f"BUY_{side}",
        "predicted_entry_vwap": 0.5,
        "exit_plan": "TAKE_3C_OR_STOP_3C",
        "reason_codes_json": "[]",
        "evidence_source": "l2_recorder",
        "evidence_run_id": run_id,
    }
    row["eligibility_sha256"] = evidence_eligibility_hash(row)
    return row


def _ladder(bid: float, ask: float) -> str:
    return json.dumps(
        {"b": [[bid, 20.0]], "a": [[ask, 20.0]]},
        separators=(",", ":"),
    )


def _build_recorder(path: Path) -> None:
    conn = duckdb.connect(str(path))
    conn.execute(
        """
        CREATE TABLE pm_round_snapshots(
            ts DOUBLE, decision_ts DOUBLE, slug VARCHAR, horizon INTEGER,
            book_age_s DOUBLE, up_ladder VARCHAR, down_ladder VARCHAR
        )
        """
    )
    conn.executemany(
        "INSERT INTO pm_round_snapshots VALUES (?,?,?,?,?,?,?)",
        [
            (1000.6, 1000.6, "round-1", 5, 0.1, _ladder(0.51, 0.50),
             _ladder(0.51, 0.50)),
            (1001.1, 1001.1, "round-1", 5, 0.1, _ladder(0.53, 0.52),
             _ladder(0.47, 0.51)),
            (1002.0, 1002.0, "round-1", 5, 0.1, _ladder(0.60, 0.61),
             _ladder(0.38, 0.39)),
            # The 0.90 bid is after round-2 expiry and must never become an exit.
            (2000.6, 2000.6, "round-2", 5, 0.1, _ladder(0.50, 0.50),
             _ladder(0.50, 0.50)),
            (2002.0, 2002.0, "round-2", 5, 0.1, _ladder(0.90, 0.91),
             _ladder(0.08, 0.09)),
            # The only round-3 quote arrives more than five seconds after the target latency.
            (3010.0, 3010.0, "round-3", 5, 0.1, _ladder(0.90, 0.91),
             _ladder(0.08, 0.09)),
        ],
    )
    conn.execute(
        """
        CREATE TABLE pm_round_settlements(
            slug VARCHAR, settled_side INTEGER, resolution_source VARCHAR,
            resolved_at DOUBLE
        )
        """
    )
    conn.execute(
        "INSERT INTO pm_round_settlements VALUES (?,?,?,?)",
        ["round-1", 1, "polymarket_clob", 1300.0],
    )
    conn.executemany(
        "INSERT INTO pm_round_settlements VALUES (?,?,?,?)",
        [
            ("round-2", 0, "polymarket_clob", 2001.0),
            ("round-3", 1, "polymarket_clob", 3120.0),
        ],
    )
    conn.close()


def run() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger_path = root / "ledger.duckdb"
        pending = root / "pending"
        conn = connect(ledger_path)
        ensure_v2_schema(conn)
        up = _forecast("up", "UP")
        down = _forecast("down", "DOWN")
        assert log_forward_prediction_v2(up, conn=conn)
        assert log_forward_prediction_v2(down, conn=conn)
        assert log_forward_prediction_v2(up, conn=conn)
        expires = _forecast("expires", "UP")
        expires.update({
            "round_id": "round-2",
            "exposure_id": "round-2@1",
            "seconds_left": 1,
            "prediction_ts_ms": 2_000_000,
            "prediction_ts_s": 2000.0,
        })
        assert log_forward_prediction_v2(expires, conn=conn)
        delayed = _forecast("delayed", "UP")
        delayed.update({
            "round_id": "round-3",
            "exposure_id": "round-3@120",
            "prediction_ts_ms": 3_000_000,
            "prediction_ts_s": 3000.0,
        })
        assert log_forward_prediction_v2(delayed, conn=conn)
        conn.close()

        # A high score with insufficient q10 capacity is excluded before threshold selection.
        bad = _forecast("bad", "UP", score=0.99)
        bad["conservative_capacity_q10"] = 1.0
        bad["eligibility_passed"] = False
        bad["eligibility_sha256"] = evidence_eligibility_hash(bad)
        selected = causal_selection([bad, up], 0.7)
        assert [row["forecast_id"] for row in selected] == ["up"]

        # Failed rows survive process memory in an atomic durable spool and replay exactly once.
        spooled = _forecast("spooled", "UP", run_id="spool-run")
        _spool_v2_failure(
            spooled, [], None, RuntimeError("injected"), pending_dir=pending
        )
        conn = connect(ledger_path)
        replay = replay_pending_v2(conn=conn, pending_dir=pending)
        assert replay["recovered"] == 1 and replay["remaining"] == 0
        conn.close()

        recorder_path = root / "execution_layer.duckdb"
        _build_recorder(recorder_path)
        source_without_wal = recording_sha256(recorder_path)
        recorder_wal = Path(str(recorder_path) + ".wal")
        recorder_wal.write_bytes(b"source-wal-fixture")
        assert recording_sha256(recorder_path) != source_without_wal
        recorder_wal.unlink()
        result = reconstruct_v2_outcomes(
            evidence_run_id="evidence-run",
            recorder_db=recorder_path,
            ledger_db=ledger_path,
        )
        assert result["written"] == 3, result
        conn = connect(ledger_path)
        outcomes = read_resolved_outcomes(conn)
        conn.close()
        assert set(outcomes) == {"up", "down", "expires"}
        assert outcomes["up"]["outcome_schema_version"] == OUTCOME_V2_SCHEMA_VERSION
        assert outcomes["up"]["reconstruction_source"] == "OWN_L2_RECONSTRUCTION"
        assert outcomes["up"]["plan_net"] > 0.03
        assert outcomes["down"]["plan_net"] < -0.03
        assert outcomes["expires"]["plan_exit_kind"] != "TARGET"
        assert outcomes["expires"]["plan_net"] < 0.0
        assert "delayed" not in outcomes
        assert json.loads(outcomes["up"]["candidate_pnls_json"]) == [
            outcomes["down"]["plan_net"]
        ]

        try:
            resolve_v2({}, test_only=False)
            raise AssertionError("production accepted caller-supplied economics")
        except RuntimeError:
            pass

    print("evidence completion test: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
