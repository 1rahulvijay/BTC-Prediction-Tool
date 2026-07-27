"""Dedicated DuckDB ledger for complete-trade shadow forecasts and outcomes."""
from __future__ import annotations

import json
import hashlib
import os
import tempfile
import time
from collections import deque
from pathlib import Path
from typing import Any

import duckdb

from .trade_schema import OFFICIAL_RESOLUTION_SOURCES


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
# Separate from analytics.duckdb and the recorder-owned execution_layer.duckdb.
DB_PATH = Path(
    os.environ.get("BTC_COMPLETE_TRADE_DB")
    or DATA / "complete_trade_forecast.duckdb"
)
PENDING_V2_DIR = DATA / "complete_trade_forecast_pending_v2"
OUTCOME_V2_SCHEMA_VERSION = "2026-07-27-l2-reconstruction-v1"


def connect(path: Path | str | None = None) -> duckdb.DuckDBPyConnection:
    target = Path(path or DB_PATH)
    target.parent.mkdir(parents=True, exist_ok=True)
    return duckdb.connect(str(target))


def init_schema(conn: duckdb.DuckDBPyConnection | None = None):
    own = conn is None
    conn = conn or connect()
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS complete_trade_forecasts (
            forecast_id VARCHAR PRIMARY KEY,
            snapshot_id VARCHAR,
            decision_ts BIGINT,
            round_id VARCHAR,
            horizon INTEGER,
            price_to_beat DOUBLE,
            current_btc DOUBLE,
            side VARCHAR,
            seconds_left DOUBLE,
            requested_qty DOUBLE,
            entry_ask DOUBLE,
            predicted_entry_vwap DOUBLE,
            predicted_entry_fee DOUBLE,
            break_even_bid DOUBLE,
            target_bid DOUBLE,
            stop_bid DOUBLE,
            p_ever_profitable DOUBLE,
            p_lockable_profit DOUBLE,
            p_target_before_stop DOUBLE,
            p_settlement_win DOUBLE,
            predicted_mfe DOUBLE,
            predicted_mae DOUBLE,
            predicted_first_profitable_s DOUBLE,
            pnl_q10 DOUBLE,
            pnl_q25 DOUBLE,
            pnl_q50 DOUBLE,
            pnl_q75 DOUBLE,
            pnl_q90 DOUBLE,
            expected_pnl DOUBLE,
            cvar DOUBLE,
            recommended_action VARCHAR,
            recommended_exit_plan VARCHAR,
            reason_codes_json VARCHAR,
            model_hash VARCHAR,
            feature_hash VARCHAR,
            policy_hash VARCHAR,
            mode VARCHAR,
            evidence_status VARCHAR,
            created_at BIGINT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS complete_trade_path_predictions (
            forecast_id VARCHAR,
            offset_seconds INTEGER,
            btc_q10 DOUBLE,
            btc_q25 DOUBLE,
            btc_q50 DOUBLE,
            btc_q75 DOUBLE,
            btc_q90 DOUBLE,
            share_bid_q10 DOUBLE,
            share_bid_q25 DOUBLE,
            share_bid_q50 DOUBLE,
            share_bid_q75 DOUBLE,
            share_bid_q90 DOUBLE,
            p_break_even_cross DOUBLE,
            p_target_cross DOUBLE,
            p_stop_cross DOUBLE,
            PRIMARY KEY(forecast_id, offset_seconds)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS complete_trade_checkpoints (
            forecast_id VARCHAR,
            checkpoint_ts BIGINT,
            actual_btc DOUBLE,
            actual_bid DOUBLE,
            actual_full_qty_vwap DOUBLE,
            actual_net_pnl DOUBLE,
            updated_p_profit DOUBLE,
            updated_expected_pnl DOUBLE,
            updated_cvar DOUBLE,
            updated_action VARCHAR,
            action_changed BOOLEAN,
            change_reason VARCHAR,
            PRIMARY KEY(forecast_id, checkpoint_ts)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS complete_trade_outcomes (
            forecast_id VARCHAR PRIMARY KEY,
            actual_entry_vwap DOUBLE,
            actual_entry_latency DOUBLE,
            actual_entry_fee DOUBLE,
            actual_first_profitable_s DOUBLE,
            actual_mfe DOUBLE,
            actual_mae DOUBLE,
            actual_exit_vwap DOUBLE,
            actual_exit_fee DOUBLE,
            actual_holding_s DOUBLE,
            actual_net_pnl DOUBLE,
            predicted_error DOUBLE,
            target_hit BOOLEAN,
            stop_hit BOOLEAN,
            settlement_outcome VARCHAR,
            official_resolution_source VARCHAR,
            failure_component VARCHAR,
            error_details_json VARCHAR,
            resolved_at BIGINT
        )
        """
    )
    return conn if own else None


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


# ===================================================================================
# LEDGER V2 - the immutable evidence table the forward evaluator reads
# ===================================================================================
# V1 could not support a forward-evidence manifest:
#   * `decision_ts` is written from now_ms (MILLISECONDS) but read as seconds, so a 2-hour
#     evidence set would measure as 8 weeks;
#   * `feature_hash` is a hash of each prediction's feature VALUES, so it differs on every row -
#     it can never satisfy a singleton check, and it is not the schema hash the contract means;
#   * there is no threshold hash, no prereg hash and no freeze timestamps at all.
#
# V2 is a SEPARATE table. The V1 columns keep their existing meaning; nothing is silently
# redefined underneath rows that were already written. Units are in the column names, and the
# two different feature hashes have two different names.
FORECASTS_V2_DDL = """
CREATE TABLE IF NOT EXISTS complete_trade_forecasts_v2(
    forecast_id VARCHAR PRIMARY KEY,
    round_id VARCHAR,
    exposure_id VARCHAR,
    horizon INTEGER,
    seconds_left INTEGER,
    side VARCHAR,
    requested_qty DOUBLE,
    ledger_schema_version VARCHAR,

    -- Clarification-001 eligibility, frozen at decision time.
    candidate_valid BOOLEAN,
    candidate_reasons_json VARCHAR,
    all_features_finite BOOLEAN,
    decision_entry_complete BOOLEAN,
    decision_book_age_s DOUBLE,
    conservative_capacity_q10 DOUBLE,
    cost_q80 DOUBLE,
    eligibility_passed BOOLEAN,
    eligibility_sha256 VARCHAR,

    -- Units are explicit in the name. Both are stored; neither is inferred from the other.
    prediction_ts_ms BIGINT,
    prediction_ts_s DOUBLE,

    -- Identity of the frozen policy. Each of these MUST be a singleton across an evidence set.
    model_bundle_sha256 VARCHAR,
    bundle_manifest_sha256 VARCHAR,
    feature_schema_sha256 VARCHAR,
    policy_sha256 VARCHAR,
    threshold_sha256 VARCHAR,
    prereg_sha256 VARCHAR,
    clarification_sha256 VARCHAR,

    -- Per-row, deliberately NOT a singleton: the values this one prediction was computed from.
    feature_values_sha256 VARCHAR,

    -- Freeze boundaries, so admissibility is checkable from the row alone.
    prereg_frozen_at_s DOUBLE,
    model_frozen_at_s DOUBLE,
    threshold_frozen_at_s DOUBLE,

    -- The decision itself.
    entry_threshold DOUBLE,
    score DOUBLE,
    action VARCHAR,
    predicted_entry_vwap DOUBLE,
    exit_plan VARCHAR,
    reason_codes_json VARCHAR,

    -- One immutable evidence run. Rows written before every freeze existed carry NULL and can
    -- never join a promotion set; the evaluator selects exactly ONE run id.
    evidence_run_id VARCHAR,

    -- Provenance class. Third-party historical archives (PMXT, Resolved Markets, Polyfun, HF)
    -- are genuinely useful for development but cannot carry THIS host's recv_ts, gaps or
    -- outages, so they have kill-only authority. Stamped per row so a mixed set is detectable
    -- rather than merely discouraged; see forward_evidence.classify_source.
    evidence_source VARCHAR
)
"""

FORECASTS_V2_COLUMNS = (
    "forecast_id", "round_id", "exposure_id", "horizon", "seconds_left", "side",
    "requested_qty", "ledger_schema_version",
    "candidate_valid", "candidate_reasons_json", "all_features_finite",
    "decision_entry_complete", "decision_book_age_s", "conservative_capacity_q10",
    "cost_q80", "eligibility_passed", "eligibility_sha256",
    "prediction_ts_ms", "prediction_ts_s",
    "model_bundle_sha256", "bundle_manifest_sha256", "feature_schema_sha256",
    "policy_sha256", "threshold_sha256", "prereg_sha256", "clarification_sha256",
    "feature_values_sha256",
    "prereg_frozen_at_s", "model_frozen_at_s", "threshold_frozen_at_s",
    "entry_threshold", "score", "action", "predicted_entry_vwap", "exit_plan",
    "reason_codes_json", "evidence_source", "evidence_run_id",
)


# Resolved official outcomes for V2 evidence. Separate table so the prediction ledger stays
# append-only and immutable: an outcome arriving later never rewrites the forecast row.
OUTCOMES_V2_DDL = """
CREATE TABLE IF NOT EXISTS complete_trade_outcomes_v2(
    forecast_id VARCHAR PRIMARY KEY,
    round_id VARCHAR,
    outcome_schema_version VARCHAR,
    resolved_at_s DOUBLE,
    resolution_source VARCHAR,
    reconstruction_source VARCHAR,
    source_recording_sha256 VARCHAR,
    settled_side INTEGER,
    entry_filled BOOLEAN,
    entry_vwap DOUBLE,
    entry_snapshot_ts_s DOUBLE,
    entry_latency_ms DOUBLE,
    plan_net DOUBLE,
    plan_exit_kind VARCHAR,
    plan_holding_s DOUBLE,
    stress_1000ms_plan_net DOUBLE,
    stress_entry_vwap DOUBLE,
    candidate_pnls_json VARCHAR
)
"""

OUTCOMES_V2_COLUMNS = (
    "forecast_id", "round_id", "outcome_schema_version", "resolved_at_s",
    "resolution_source", "reconstruction_source", "source_recording_sha256",
    "settled_side", "entry_filled", "entry_vwap", "entry_snapshot_ts_s",
    "entry_latency_ms", "plan_net", "plan_exit_kind", "plan_holding_s",
    "stress_1000ms_plan_net", "stress_entry_vwap", "candidate_pnls_json",
)


def ensure_v2_schema(conn: Any) -> None:
    """Create or additively migrate Ledger V2 without rewriting immutable rows."""
    conn.execute(FORECASTS_V2_DDL)
    conn.execute(OUTCOMES_V2_DDL)
    forecast_types = {
        "horizon": "INTEGER",
        "ledger_schema_version": "VARCHAR",
        "candidate_valid": "BOOLEAN",
        "candidate_reasons_json": "VARCHAR",
        "all_features_finite": "BOOLEAN",
        "decision_entry_complete": "BOOLEAN",
        "decision_book_age_s": "DOUBLE",
        "conservative_capacity_q10": "DOUBLE",
        "cost_q80": "DOUBLE",
        "eligibility_passed": "BOOLEAN",
        "eligibility_sha256": "VARCHAR",
        "bundle_manifest_sha256": "VARCHAR",
        "clarification_sha256": "VARCHAR",
    }
    outcome_types = {
        "outcome_schema_version": "VARCHAR",
        "reconstruction_source": "VARCHAR",
        "source_recording_sha256": "VARCHAR",
        "entry_snapshot_ts_s": "DOUBLE",
        "entry_latency_ms": "DOUBLE",
        "stress_entry_vwap": "DOUBLE",
    }
    for name, kind in forecast_types.items():
        conn.execute(
            f"ALTER TABLE complete_trade_forecasts_v2 "
            f"ADD COLUMN IF NOT EXISTS {name} {kind}"
        )
    for name, kind in outcome_types.items():
        conn.execute(
            f"ALTER TABLE complete_trade_outcomes_v2 "
            f"ADD COLUMN IF NOT EXISTS {name} {kind}"
        )


def _spool_path(forecast_id: str, directory: Path = PENDING_V2_DIR) -> Path:
    digest = hashlib.sha256(forecast_id.encode("utf-8")).hexdigest()
    return directory / f"{digest}.json"


def _spool_v2_failure(
    v2_row: dict[str, Any],
    paths: list[dict[str, Any]] | None,
    legacy_forecast: dict[str, Any] | None,
    exc: BaseException,
    *,
    pending_dir: Path | None = None,
) -> Path:
    """Atomically persist a failed append for deterministic replay.

    The filename is stable per forecast id. Repeated failures replace only the dead-letter
    envelope for that same immutable row; replay still refuses to overwrite a different row
    already stored under the forecast id.
    """
    forecast_id = str(v2_row.get("forecast_id") or "")
    if not forecast_id:
        raise ValueError("cannot spool V2 row without forecast_id")
    directory = Path(pending_dir or PENDING_V2_DIR)
    directory.mkdir(parents=True, exist_ok=True)
    target = _spool_path(forecast_id, directory)
    if target.is_file():
        existing = json.loads(target.read_text(encoding="utf-8"))
        if existing.get("v2_row") != v2_row:
            raise ValueError(
                "durable spool forecast_id collision with a different immutable payload"
            )
        return target
    payload = {
        "spool_version": 1,
        "spooled_at_s": time.time(),
        "error": f"{type(exc).__name__}: {exc}",
        "v2_row": v2_row,
        "paths": paths or [],
        "legacy_forecast": legacy_forecast,
    }
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.stem}.",
        suffix=".tmp",
        dir=str(directory),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True, separators=(",", ":"), default=str)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def _same_value(left: Any, right: Any) -> bool:
    if left is None or right is None:
        return left is right
    if isinstance(left, bool) or isinstance(right, bool):
        return bool(left) is bool(right)
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return float(left) == float(right)
    return str(left) == str(right)


def _stored_forecast_matches(
    conn: Any,
    row: dict[str, Any],
) -> bool:
    stored = conn.execute(
        "SELECT " + ",".join(FORECASTS_V2_COLUMNS)
        + " FROM complete_trade_forecasts_v2 WHERE forecast_id = ?",
        [row["forecast_id"]],
    ).fetchone()
    if stored is None:
        return False
    return all(
        _same_value(stored[index], row[column])
        for index, column in enumerate(FORECASTS_V2_COLUMNS)
    )


def replay_pending_v2(
    *,
    conn: Any = None,
    limit: int = 100,
    pending_dir: Path | None = None,
) -> dict[str, Any]:
    """Replay durable V2 dead letters without ever overwriting immutable evidence."""
    own = conn is None
    conn = conn or connect()
    directory = Path(pending_dir or PENDING_V2_DIR)
    report = {
        "attempted": 0,
        "recovered": 0,
        "exact_duplicates": 0,
        "failed": 0,
        "remaining": 0,
        "errors": [],
    }
    try:
        ensure_v2_schema(conn)
        files = sorted(directory.glob("*.json"))[: max(0, int(limit))] if directory.exists() else []
        for path in files:
            report["attempted"] += 1
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                row = payload["v2_row"]
                missing = [column for column in FORECASTS_V2_COLUMNS if column not in row]
                if missing:
                    raise ValueError(f"spooled row missing columns: {missing}")
                existing = conn.execute(
                    "SELECT 1 FROM complete_trade_forecasts_v2 WHERE forecast_id = ?",
                    [row["forecast_id"]],
                ).fetchone()
                if existing:
                    if not _stored_forecast_matches(conn, row):
                        raise ValueError(
                            "immutable forecast_id collision with different stored payload"
                        )
                    report["exact_duplicates"] += 1
                else:
                    conn.execute("BEGIN TRANSACTION")
                    try:
                        log_forecast_v2(row, conn)
                        conn.execute("COMMIT")
                    except Exception:
                        try:
                            conn.execute("ROLLBACK")
                        except Exception:
                            pass
                        raise
                    legacy = payload.get("legacy_forecast")
                    if legacy is not None:
                        try:
                            log_forecast(legacy, payload.get("paths") or [], conn)
                        except Exception:
                            # V1 is a compatibility mirror, never evidence authority.
                            pass
                    report["recovered"] += 1
                    LOG_HEALTH.record_recovery()
                path.unlink()
            except Exception as exc:  # noqa: BLE001
                report["failed"] += 1
                report["errors"].append(
                    {"file": path.name, "error": f"{type(exc).__name__}: {exc}"}
                )
        report["remaining"] = (
            len(list(directory.glob("*.json"))) if directory.exists() else 0
        )
        return report
    finally:
        if own:
            conn.close()


def log_outcome_v2(row: dict[str, Any], conn: Any = None) -> None:
    """Append one resolved outcome. INSERT only; a duplicate raises."""
    own = conn is None
    conn = conn or connect()
    try:
        if own:
            ensure_v2_schema(conn)
        missing = [c for c in OUTCOMES_V2_COLUMNS if c not in row]
        if missing:
            raise ValueError(f"outcome v2 row missing required columns: {missing}")
        conn.execute(
            "INSERT INTO complete_trade_outcomes_v2 (" + ",".join(OUTCOMES_V2_COLUMNS)
            + ") VALUES (" + ",".join("?" * len(OUTCOMES_V2_COLUMNS)) + ")",
            [row[c] for c in OUTCOMES_V2_COLUMNS],
        )
    finally:
        if own:
            conn.close()


def read_resolved_outcomes(
    conn: Any = None,
    *,
    include_test_fixtures: bool = False,
) -> dict[str, dict[str, Any]]:
    """Resolved outcomes keyed by forecast_id. OFFICIAL provenance only.

    The evaluator claims to read official outcomes, so the filter belongs here rather than in a
    caller that might forget it. Anything whose resolution_source is not on the frozen allowlist
    is not ground truth and is excluded."""
    own = conn is None
    conn = conn or connect()
    try:
        if own:
            ensure_v2_schema(conn)
        reconstruction_filter = (
            ""
            if include_test_fixtures
            else " AND reconstruction_source = 'OWN_L2_RECONSTRUCTION'"
        )
        cursor = conn.execute(
            "SELECT " + ",".join(OUTCOMES_V2_COLUMNS)
            + " FROM complete_trade_outcomes_v2 WHERE resolution_source IN ("
            + ",".join("?" * len(OFFICIAL_RESOLUTION_SOURCES)) + ")"
            + reconstruction_filter,
            list(OFFICIAL_RESOLUTION_SOURCES),
        )
        names = [d[0] for d in cursor.description]
        return {r[0]: dict(zip(names, r)) for r in cursor.fetchall()}
    finally:
        if own:
            conn.close()


def log_forward_prediction_v2(
    v2_row: dict[str, Any],
    paths: list[dict[str, Any]] | None = None,
    legacy_forecast: dict[str, Any] | None = None,
    conn: Any = None,
) -> bool:
    """ONE transactional write of the immutable V2 prediction plus its path diagnostics.

    Returns True only when the V2 row is durably written. The legacy V1 row is best-effort and
    its failure never masks a V2 success or failure - V2 is the evidence, V1 is compatibility."""
    # A caller may supply a connection - bulk paths open thousands of writes and one connection
    # per row is pathologically slow. The live path still passes None and gets its own.
    own = conn is None
    conn = conn or connect()
    try:
        if own:
            ensure_v2_schema(conn)
        conn.execute("BEGIN TRANSACTION")
        try:
            log_forecast_v2(v2_row, conn)
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except Exception:
                pass
            raise
        if legacy_forecast is not None:
            try:
                log_forecast(legacy_forecast, paths, conn)
            except Exception as exc:                       # noqa: BLE001
                print(f"[trade-forecast] legacy V1 mirror failed (V2 is intact): {exc}",
                      flush=True)
        LOG_HEALTH.record_success()
        return True
    except Exception as exc:                               # noqa: BLE001
        # A controlled restart can re-emit the same checkpoint before its in-memory de-dup set
        # is rebuilt. Exact immutable duplicates are idempotent success; a same-id/different-row
        # collision still falls through to the durable failure path below.
        try:
            if _stored_forecast_matches(conn, v2_row):
                LOG_HEALTH.record_exact_duplicate()
                return True
        except Exception:
            pass
        LOG_HEALTH.record_failure(exc, v2_row)
        try:
            _spool_v2_failure(v2_row, paths, legacy_forecast, exc)
        except Exception as spool_exc:                          # noqa: BLE001
            LOG_HEALTH.record_spool_failure(spool_exc)
        print(f"[trade-forecast] V2 EVIDENCE WRITE FAILED: {type(exc).__name__}: {exc}",
              flush=True)
        return False
    finally:
        if own:
            conn.close()


def log_forecast_v2(row: dict[str, Any], conn: Any = None) -> None:
    """Append one immutable V2 evidence row. INSERT only - a duplicate raises."""
    own = conn is None
    conn = conn or connect()
    try:
        if own:
            ensure_v2_schema(conn)
        missing = [c for c in FORECASTS_V2_COLUMNS if c not in row]
        if missing:
            raise ValueError(f"ledger v2 row missing required columns: {missing}")
        conn.execute(
            "INSERT INTO complete_trade_forecasts_v2 ("
            + ",".join(FORECASTS_V2_COLUMNS) + ") VALUES ("
            + ",".join("?" * len(FORECASTS_V2_COLUMNS)) + ")",
            [row[c] for c in FORECASTS_V2_COLUMNS],
        )
    finally:
        if own:
            conn.close()


def read_forward_rows(conn: Any = None, evidence_run_id: str | None = None) -> list[dict[str, Any]]:
    """Rows in the shape build_forward_manifest() expects. Seconds, schema hash, no aliasing."""
    own = conn is None
    conn = conn or connect()
    try:
        ensure_v2_schema(conn)
        # EVERY field the policy executes on. The first version returned only hashes and
        # timestamps, so causal_selection() read seconds_left/score as missing, defaulted them
        # to 0, and no database-loaded row could ever clear a positive threshold. The synthetic
        # selftest hid it by hand-building complete dictionaries instead of reading this.
        cursor = conn.execute(
            "SELECT forecast_id, round_id, exposure_id, horizon, seconds_left, side, "
            "requested_qty, ledger_schema_version, candidate_valid, candidate_reasons_json, "
            "all_features_finite, decision_entry_complete, decision_book_age_s, "
            "conservative_capacity_q10, cost_q80, eligibility_passed, eligibility_sha256, "
            "prediction_ts_s AS prediction_ts, prediction_ts_ms, "
            "score, action, entry_threshold, predicted_entry_vwap, exit_plan, "
            "model_bundle_sha256 AS model_sha256, bundle_manifest_sha256, "
            "feature_schema_sha256, "
            "feature_values_sha256, policy_sha256, threshold_sha256, prereg_sha256, "
            "clarification_sha256, "
            "prereg_frozen_at_s, model_frozen_at_s, threshold_frozen_at_s, "
            "evidence_source, evidence_run_id "
            "FROM complete_trade_forecasts_v2 "
            "WHERE evidence_run_id = COALESCE(?, evidence_run_id) "
            "ORDER BY prediction_ts_s"
        , [evidence_run_id])
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, r)) for r in cursor.fetchall()]
    finally:
        if own:
            conn.close()


def log_forecast(
    forecast: dict[str, Any],
    paths: list[dict[str, Any]] | None = None,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        init_schema(conn)
        columns = [
            "forecast_id",
            "snapshot_id",
            "decision_ts",
            "round_id",
            "horizon",
            "price_to_beat",
            "current_btc",
            "side",
            "seconds_left",
            "requested_qty",
            "entry_ask",
            "predicted_entry_vwap",
            "predicted_entry_fee",
            "break_even_bid",
            "target_bid",
            "stop_bid",
            "p_ever_profitable",
            "p_lockable_profit",
            "p_target_before_stop",
            "p_settlement_win",
            "predicted_mfe",
            "predicted_mae",
            "predicted_first_profitable_s",
            "pnl_q10",
            "pnl_q25",
            "pnl_q50",
            "pnl_q75",
            "pnl_q90",
            "expected_pnl",
            "cvar",
            "recommended_action",
            "recommended_exit_plan",
            "reason_codes_json",
            "model_hash",
            "feature_hash",
            "policy_hash",
            "mode",
            "evidence_status",
            "created_at",
        ]
        values = [
            _json(forecast.get("reason_codes") or [])
            if column == "reason_codes_json"
            else forecast.get(column)
            for column in columns
        ]
        conn.execute("BEGIN TRANSACTION")
        conn.execute(
            f"INSERT INTO complete_trade_forecasts "
            f"({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            values,
        )
        path_columns = [
            "forecast_id",
            "offset_seconds",
            "btc_q10",
            "btc_q25",
            "btc_q50",
            "btc_q75",
            "btc_q90",
            "share_bid_q10",
            "share_bid_q25",
            "share_bid_q50",
            "share_bid_q75",
            "share_bid_q90",
            "p_break_even_cross",
            "p_target_cross",
            "p_stop_cross",
        ]
        for path in paths or []:
            row = {**path, "forecast_id": forecast["forecast_id"]}
            conn.execute(
                f"INSERT INTO complete_trade_path_predictions "
                f"({','.join(path_columns)}) VALUES ({','.join('?' for _ in path_columns)})",
                [row.get(column) for column in path_columns],
            )
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        raise
    finally:
        if own:
            conn.close()


class LogHealth:
    """Observable state for the evidence writer.

    A logging failure is never invisible. The ticker may continue - a forecast that fails to
    persist must not take the price feed down - but the failure is counted, timestamped, kept in a
    dead-letter buffer and exposed, because silently losing evidence looks exactly like a healthy
    run that happened to produce fewer forecasts."""

    MAX_DEAD_LETTERS = 200

    def __init__(self) -> None:
        self.attempted = 0
        self.written = 0
        self.failed = 0
        self.duplicates = 0
        self.spool_failures = 0
        self.recovered = 0
        self.last_success_ts: float | None = None
        self.last_error: str | None = None
        self.last_error_ts: float | None = None
        self.dead_letters: deque = deque(maxlen=self.MAX_DEAD_LETTERS)

    def record_success(self) -> None:
        self.attempted += 1
        self.written += 1
        self.last_success_ts = time.time()

    def record_failure(self, exc: BaseException, payload: dict[str, Any]) -> None:
        self.attempted += 1
        self.failed += 1
        text = f"{type(exc).__name__}: {exc}"
        if any(t in text.lower() for t in ("duplicate", "primary key", "unique")):
            self.duplicates += 1
        self.last_error = text
        self.last_error_ts = time.time()
        self.dead_letters.append(
            {"ts": self.last_error_ts, "error": text,
             "forecast_id": payload.get("forecast_id")}
        )

    def record_exact_duplicate(self) -> None:
        self.attempted += 1
        self.duplicates += 1

    def record_spool_failure(self, exc: BaseException) -> None:
        self.spool_failures += 1
        self.last_error = f"spool {type(exc).__name__}: {exc}"
        self.last_error_ts = time.time()

    def record_recovery(self) -> None:
        self.recovered += 1

    def snapshot(self) -> dict[str, Any]:
        stale_s = (
            round(time.time() - self.last_success_ts, 1)
            if self.last_success_ts is not None
            else None
        )
        return {
            "attempted": self.attempted,
            "written": self.written,
            "failed": self.failed,
            "duplicate_rejections": self.duplicates,
            "spool_failures": self.spool_failures,
            "recovered_from_spool": self.recovered,
            "durable_pending": (
                len(list(PENDING_V2_DIR.glob("*.json")))
                if PENDING_V2_DIR.exists()
                else 0
            ),
            "last_success_ts": self.last_success_ts,
            "seconds_since_last_write": stale_s,
            "last_error": self.last_error,
            "dead_letters": len(self.dead_letters),
            "healthy": self.failed == 0 and self.spool_failures == 0,
            "alert": (
                None
                if self.failed == 0 and self.spool_failures == 0
                else (
                    f"{self.failed} forecast write(s) failed; "
                    f"{self.spool_failures} spool failure(s); last: {self.last_error}"
                )
            ),
        }


LOG_HEALTH = LogHealth()


def log_forecast_monitored(
    forecast: dict[str, Any],
    paths: list[dict[str, Any]] | None = None,
    conn: Any = None,
) -> bool:
    """Append-only write with retry. Returns True on success; never raises into the ticker.

    The previous call site swallowed every exception with `pass`, so a broken evidence table
    produced a perfectly normal-looking run that simply contained no forecasts."""
    for attempt in range(3):
        try:
            log_forecast(forecast, paths, conn)
            LOG_HEALTH.record_success()
            return True
        except Exception as exc:                       # noqa: BLE001 - reported, never hidden
            text = f"{type(exc).__name__}: {exc}"
            duplicate = any(
                token in text.lower() for token in ("duplicate", "primary key", "unique")
            )
            if duplicate or attempt == 2:
                LOG_HEALTH.record_failure(exc, forecast)
                print(
                    "[trade-forecast] EVIDENCE WRITE FAILED "
                    f"({'duplicate' if duplicate else 'retries exhausted'}): {text}",
                    flush=True,
                )
                return False
            time.sleep(0.05 * (attempt + 1))
    return False


def log_checkpoint(
    checkpoint: dict[str, Any],
    conn: duckdb.DuckDBPyConnection | None = None,
) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        init_schema(conn)
        columns = [
            "forecast_id",
            "checkpoint_ts",
            "actual_btc",
            "actual_bid",
            "actual_full_qty_vwap",
            "actual_net_pnl",
            "updated_p_profit",
            "updated_expected_pnl",
            "updated_cvar",
            "updated_action",
            "action_changed",
            "change_reason",
        ]
        conn.execute(
            f"INSERT INTO complete_trade_checkpoints "
            f"({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            [checkpoint.get(column) for column in columns],
        )
    finally:
        if own:
            conn.close()


def log_outcome(
    outcome: dict[str, Any],
    conn: duckdb.DuckDBPyConnection | None = None,
) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        init_schema(conn)
        columns = [
            "forecast_id",
            "actual_entry_vwap",
            "actual_entry_latency",
            "actual_entry_fee",
            "actual_first_profitable_s",
            "actual_mfe",
            "actual_mae",
            "actual_exit_vwap",
            "actual_exit_fee",
            "actual_holding_s",
            "actual_net_pnl",
            "predicted_error",
            "target_hit",
            "stop_hit",
            "settlement_outcome",
            "official_resolution_source",
            "failure_component",
            "error_details_json",
            "resolved_at",
        ]
        values = [
            _json(outcome.get("error_details") or {})
            if column == "error_details_json"
            else outcome.get(column)
            for column in columns
        ]
        conn.execute(
            f"INSERT INTO complete_trade_outcomes "
            f"({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
            values,
        )
    finally:
        if own:
            conn.close()


def status(conn: duckdb.DuckDBPyConnection | None = None) -> dict[str, int]:
    own = conn is None
    conn = conn or connect()
    try:
        init_schema(conn)
        return {
            table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            for table in (
                "complete_trade_forecasts",
                "complete_trade_path_predictions",
                "complete_trade_checkpoints",
                "complete_trade_outcomes",
            )
        }
    finally:
        if own:
            conn.close()


def selftest() -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "test.duckdb"
        conn = connect(path)
        init_schema(conn)
        forecast = {
            "forecast_id": "f1",
            "decision_ts": 1,
            "round_id": "r1",
            "horizon": 5,
            "side": "UP",
            "requested_qty": 10,
            "reason_codes": ["shadow"],
        }
        log_forecast(forecast, [{"offset_seconds": 5, "share_bid_q50": 0.6}], conn)
        log_checkpoint({"forecast_id": "f1", "checkpoint_ts": 2}, conn)
        log_outcome({"forecast_id": "f1", "actual_net_pnl": 0.01}, conn)
        counts = status(conn)
        assert counts["complete_trade_forecasts"] == 1
        assert counts["complete_trade_path_predictions"] == 1
        assert counts["complete_trade_checkpoints"] == 1
        assert counts["complete_trade_outcomes"] == 1
        conn.close()
    print("trade_forecast_logger self-test: ALL PASS")


if __name__ == "__main__":
    selftest()
