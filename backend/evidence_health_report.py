"""Daily integrity and coverage report for the Phase 5 forward-evidence ledgers.

This is recorder diagnostics, not an alpha test. Missing databases and empty tables are reported
as WAITING_FOR_DATA. They are never interpreted as healthy evidence.

    python backend/evidence_health_report.py
    python backend/evidence_health_report.py --expect-live --strict
    python backend/evidence_health_report.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import time
import zlib
from pathlib import Path
from typing import Any

import numpy as np

from model_revision_ledger import ModelRevisionLedger, RevisionRefusal


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_REVISION_DB = DATA_DIR / "model_revision_ledger.duckdb"
DEFAULT_OPPORTUNITY_DB = DATA_DIR / "opportunity_ledger.duckdb"
DEFAULT_REPORT = DATA_DIR / "reports" / "EVIDENCE_HEALTH_REPORT_V1.json"
DEFAULT_HISTORY_DB = DATA_DIR / "evidence_health.duckdb"
REPORT_VERSION = "evidence-health-report-v1"
MARKOUT_OFFSETS_MS = {
    "MARKOUT_1000MS": 1_000,
    "MARKOUT_5000MS": 5_000,
    "MARKOUT_15000MS": 15_000,
    "MARKOUT_30000MS": 30_000,
    "MARKOUT_60000MS": 60_000,
    "MARKOUT_120000MS": 120_000,
}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _rate(numerator: int, denominator: int) -> float | None:
    return float(numerator) / float(denominator) if denominator else None


def _table_names(con: Any) -> set[str]:
    return {str(row[0]) for row in con.execute("SHOW TABLES").fetchall()}


def _json_dict(rows: list[tuple[Any, Any]]) -> dict[str, int]:
    return {str(key): int(value) for key, value in rows}


def _decode_state_row(row: tuple[Any, ...]) -> list[str]:
    _, names_json, shape_json, blob, stored_hash = row
    problems: list[str] = []
    try:
        names = json.loads(str(names_json))
        shape = tuple(int(value) for value in json.loads(str(shape_json)))
        if len(shape) != 2 or any(value <= 0 for value in shape):
            problems.append(f"invalid shape {shape!r}")
            return problems
        if not isinstance(names, list) or len(names) != shape[1]:
            problems.append("feature-name count does not match state width")
        raw = zlib.decompress(bytes(blob))
        expected = int(np.prod(shape)) * np.dtype(np.float32).itemsize
        if len(raw) != expected:
            problems.append(f"decoded bytes {len(raw)} != expected {expected}")
            return problems
        expected_hash = hashlib.sha256(
            str(names_json).encode("utf-8") + b"\0" + raw
        ).hexdigest()
        if expected_hash != str(stored_hash):
            problems.append("state hash mismatch")
        values = np.frombuffer(raw, dtype=np.float32)
        if not np.all(np.isfinite(values)):
            problems.append("state contains non-finite values")
    except Exception as exc:
        problems.append(f"decode failed: {type(exc).__name__}: {exc}")
    return problems


def _revision_report(
    db_path: Path,
    *,
    now_ms: int,
    outcome_grace_ms: int,
    late_threshold_ms: int,
    minimum_eligible: int,
    full_state_scan: bool,
    state_scan_limit: int,
    expect_live: bool,
    stale_after_ms: int,
) -> dict[str, Any]:
    section: dict[str, Any] = {
        "status": "WAITING_FOR_DATA",
        "database": str(db_path),
        "database_exists": db_path.exists(),
        "integrity_failures": [],
        "warnings": [],
    }
    if not db_path.exists():
        section["warnings"].append("revision database does not exist; restart the backend")
        return section

    import duckdb

    try:
        con = duckdb.connect(str(db_path), read_only=True)
    except Exception as exc:
        section["status"] = "FAIL"
        section["integrity_failures"].append(f"cannot open revision database: {exc}")
        return section
    try:
        required = {
            "model_state_snapshots", "model_revisions", "model_revision_outcomes",
        }
        tables = _table_names(con)
        missing_tables = sorted(required - tables)
        if missing_tables:
            section["status"] = "FAIL"
            section["integrity_failures"].append(
                f"missing required tables: {', '.join(missing_tables)}"
            )
            return section

        revisions = int(con.execute("SELECT count(*) FROM model_revisions").fetchone()[0])
        section["revision_rows"] = revisions
        section["state_snapshots"] = int(con.execute(
            "SELECT count(*) FROM model_state_snapshots"
        ).fetchone()[0])
        section["revisions_by_horizon"] = _json_dict(con.execute(
            "SELECT horizon_min, count(*) FROM model_revisions GROUP BY 1 ORDER BY 1"
        ).fetchall())
        release_rows = con.execute(
            "SELECT release_id, count(*) FROM model_revisions GROUP BY 1 ORDER BY 2 DESC, 1"
        ).fetchall()
        section["unique_release_ids"] = len(release_rows)
        section["release_rows"] = _json_dict(release_rows)
        latest = con.execute("SELECT max(prediction_ts) FROM model_revisions").fetchone()[0]
        section["latest_prediction_ts"] = int(latest) if latest is not None else None
        section["latest_prediction_age_ms"] = (
            max(0, now_ms - int(latest)) if latest is not None else None
        )

        duplicate_ids = int(con.execute(
            "SELECT count(*) FROM (SELECT revision_id FROM model_revisions "
            "GROUP BY 1 HAVING count(*) > 1)"
        ).fetchone()[0])
        causal = int(con.execute(
            """
            SELECT count(*) FROM model_revisions r
            JOIN model_state_snapshots s USING (state_snapshot_id)
            WHERE s.snapshot_ts > r.prediction_ts
               OR s.feature_cutoff_ts > r.prediction_ts
            """
        ).fetchone()[0])
        broken_links = int(con.execute(
            """
            SELECT count(*) FROM model_revisions r
            LEFT JOIN model_revisions p ON p.revision_id = r.previous_revision_id
            WHERE r.previous_revision_id IS NOT NULL
              AND (p.revision_id IS NULL OR p.prediction_ts >= r.prediction_ts
                   OR p.release_id != r.release_id OR p.model_id != r.model_id
                   OR p.horizon_min != r.horizon_min)
            """
        ).fetchone()[0])
        section["stored_duplicate_revision_ids"] = duplicate_ids
        section["stored_causal_violations"] = causal
        section["broken_predecessor_links"] = broken_links
        if duplicate_ids:
            section["integrity_failures"].append(
                f"{duplicate_ids} duplicate revision IDs are stored"
            )
        if causal:
            section["integrity_failures"].append(
                f"{causal} stored revisions use future-dated state"
            )
        if broken_links:
            section["integrity_failures"].append(
                f"{broken_links} predecessor links are missing or misordered"
            )

        if "model_revision_refusals" in tables:
            refusals = _json_dict(con.execute(
                "SELECT category, count(*) FROM model_revision_refusals GROUP BY 1 ORDER BY 1"
            ).fetchall())
            section["refusal_tracking_available"] = True
            section["refusals_by_category"] = refusals
            section["duplicate_refusal_count"] = refusals.get("DUPLICATE_CONFLICT", 0)
            section["causal_refusal_count"] = refusals.get("CAUSAL", 0)
        else:
            section["refusal_tracking_available"] = False
            section["refusals_by_category"] = {}
            section["duplicate_refusal_count"] = None
            section["causal_refusal_count"] = None
            section["warnings"].append(
                "refusal telemetry table is absent; restart once to apply the additive schema"
            )

        outcome_rows = int(con.execute(
            "SELECT count(*) FROM model_revision_outcomes"
        ).fetchone()[0])
        section["outcome_rows"] = outcome_rows
        outcome_kinds = _json_dict(con.execute(
            "SELECT outcome_kind, count(*) FROM model_revision_outcomes GROUP BY 1 ORDER BY 1"
        ).fetchall())
        section["resolved_by_outcome_kind"] = outcome_kinds
        coverage: dict[str, Any] = {}
        total_eligible = 0
        total_resolved = 0
        maturity_cutoff = now_ms - outcome_grace_ms
        for kind, offset in MARKOUT_OFFSETS_MS.items():
            eligible = int(con.execute(
                "SELECT count(*) FROM model_revisions WHERE prediction_ts + ? <= ?",
                [offset, maturity_cutoff],
            ).fetchone()[0])
            resolved = int(con.execute(
                "SELECT count(*) FROM model_revision_outcomes WHERE outcome_kind = ?",
                [kind],
            ).fetchone()[0])
            resolved_eligible = min(eligible, resolved)
            missing = max(0, eligible - resolved_eligible)
            coverage[kind] = {
                "eligible": eligible,
                "resolved": resolved,
                "missing": missing,
                "missing_rate": _rate(missing, eligible),
            }
            total_eligible += eligible
            total_resolved += resolved_eligible
        section["markout_coverage"] = coverage
        total_missing = max(0, total_eligible - total_resolved)
        section["eligible_markouts"] = total_eligible
        section["resolved_eligible_markouts"] = total_resolved
        section["missing_outcomes"] = total_missing
        section["missing_outcome_rate"] = _rate(total_missing, total_eligible)

        horizon_coverage: dict[str, Any] = {}
        for horizon, count in con.execute(
            "SELECT horizon_min, count(*) FROM model_revisions GROUP BY 1 ORDER BY 1"
        ).fetchall():
            offset = int(horizon) * 60_000
            kind = f"HORIZON_{int(horizon)}M"
            eligible = int(con.execute(
                "SELECT count(*) FROM model_revisions "
                "WHERE horizon_min = ? AND prediction_ts + ? <= ?",
                [int(horizon), offset, maturity_cutoff],
            ).fetchone()[0])
            resolved = int(con.execute(
                "SELECT count(*) FROM model_revision_outcomes WHERE outcome_kind = ?",
                [kind],
            ).fetchone()[0])
            missing = max(0, eligible - min(eligible, resolved))
            horizon_coverage[str(int(horizon))] = {
                "revisions": int(count), "eligible": eligible, "resolved": resolved,
                "missing": missing, "missing_rate": _rate(missing, eligible),
            }
        section["horizon_outcome_coverage"] = horizon_coverage

        latency = con.execute(
            """
            SELECT count(*),
                   sum(CASE WHEN observation_latency_ms > ? THEN 1 ELSE 0 END),
                   median(observation_latency_ms),
                   quantile_cont(observation_latency_ms, 0.95),
                   max(observation_latency_ms)
            FROM model_revision_outcomes
            """,
            [late_threshold_ms],
        ).fetchone()
        latency_count = int(latency[0] or 0)
        late_count = int(latency[1] or 0)
        section["late_threshold_ms"] = late_threshold_ms
        section["late_observation_count"] = late_count
        section["late_observation_rate"] = _rate(late_count, latency_count)
        section["observation_latency_ms"] = {
            "count": latency_count,
            "median": float(latency[2]) if latency[2] is not None else None,
            "p95": float(latency[3]) if latency[3] is not None else None,
            "max": int(latency[4]) if latency[4] is not None else None,
        }

        query = (
            "SELECT state_snapshot_id, feature_names_json, feature_shape_json, "
            "feature_values_zlib, feature_values_hash FROM model_state_snapshots "
            "ORDER BY snapshot_ts DESC"
        )
        if not full_state_scan:
            query += f" LIMIT {max(1, int(state_scan_limit))}"
        state_rows = con.execute(query).fetchall()
        state_failures: list[dict[str, Any]] = []
        for row in state_rows:
            problems = _decode_state_row(row)
            if problems:
                state_failures.append({"state_snapshot_id": str(row[0]), "problems": problems})
        section["state_scan_mode"] = "full" if full_state_scan else "recent"
        section["state_snapshots_checked"] = len(state_rows)
        section["state_decode_failures"] = len(state_failures)
        section["state_failure_examples"] = state_failures[:10]
        if state_failures:
            section["integrity_failures"].append(
                f"{len(state_failures)} checked state snapshots failed decode/hash validation"
            )

        if section["integrity_failures"]:
            section["status"] = "FAIL"
        elif revisions == 0:
            section["warnings"].append("no revision rows; wait for trained live predictions")
        elif total_eligible < minimum_eligible:
            section["status"] = "COLLECTING"
            section["warnings"].append(
                f"only {total_eligible} eligible markouts; need {minimum_eligible} for health rates"
            )
        elif section["missing_outcome_rate"] is not None and section[
            "missing_outcome_rate"
        ] > 0.05:
            section["status"] = "DEGRADED"
            section["warnings"].append("missing-outcome rate exceeds the declared 5% threshold")
        elif latency_count >= minimum_eligible and section["late_observation_rate"] > 0.10:
            section["status"] = "DEGRADED"
            section["warnings"].append("late-observation rate exceeds the declared 10% threshold")
        elif expect_live and section["latest_prediction_age_ms"] > stale_after_ms:
            section["status"] = "DEGRADED"
            section["warnings"].append(
                f"latest revision is older than the declared {stale_after_ms} ms live threshold"
            )
        else:
            section["status"] = "HEALTHY"
    except Exception as exc:
        section["status"] = "FAIL"
        section["integrity_failures"].append(
            f"revision health query failed: {type(exc).__name__}: {exc}"
        )
    finally:
        con.close()
    return section


def _opportunity_report(db_path: Path, *, now_ms: int, expect_live: bool,
                        stale_after_ms: int) -> dict[str, Any]:
    section: dict[str, Any] = {
        "status": "WAITING_FOR_DATA",
        "database": str(db_path),
        "database_exists": db_path.exists(),
        "integrity_failures": [],
        "warnings": [],
    }
    if not db_path.exists():
        section["warnings"].append("opportunity database does not exist")
        return section

    import duckdb

    try:
        con = duckdb.connect(str(db_path), read_only=True)
    except Exception as exc:
        section["status"] = "FAIL"
        section["integrity_failures"].append(f"cannot open opportunity database: {exc}")
        return section
    try:
        required = {"opportunity_decisions", "opportunity_outcomes"}
        missing = sorted(required - _table_names(con))
        if missing:
            section["status"] = "FAIL"
            section["integrity_failures"].append(
                f"missing required tables: {', '.join(missing)}"
            )
            return section
        decisions = int(con.execute(
            "SELECT count(*) FROM opportunity_decisions"
        ).fetchone()[0])
        outcomes = int(con.execute(
            "SELECT count(*) FROM opportunity_outcomes"
        ).fetchone()[0])
        section["decision_rows"] = decisions
        section["outcome_rows"] = outcomes
        section["decisions_by_action"] = _json_dict(con.execute(
            "SELECT action, count(*) FROM opportunity_decisions GROUP BY 1 ORDER BY 1"
        ).fetchall())
        section["outcomes_by_kind"] = _json_dict(con.execute(
            "SELECT outcome_kind, count(*) FROM opportunity_outcomes GROUP BY 1 ORDER BY 1"
        ).fetchall())
        section["resolved_decisions"] = int(con.execute(
            "SELECT count(DISTINCT decision_id) FROM opportunity_outcomes"
        ).fetchone()[0])
        section["unresolved_decisions"] = max(
            0, decisions - int(section["resolved_decisions"])
        )
        causal = int(con.execute(
            """
            SELECT count(*) FROM opportunity_decisions
            WHERE state_snapshot_ts > decision_ts
               OR quote_recv_ts > decision_ts
               OR feature_cutoff_ts > decision_ts
            """
        ).fetchone()[0])
        unprovable = int(con.execute(
            """
            SELECT count(*) FROM opportunity_decisions
            WHERE action IN ('ENTER','WAIT')
              AND (model_artifact_hash IS NULL OR calibrator_hash IS NULL
                   OR policy_hash IS NULL OR feature_values_hash IS NULL
                   OR decision_context_json IS NULL)
            """
        ).fetchone()[0])
        duplicate_outcomes = int(con.execute(
            """
            SELECT count(*) FROM (
              SELECT decision_id, outcome_kind FROM opportunity_outcomes
              GROUP BY 1,2 HAVING count(*) > 1
            )
            """
        ).fetchone()[0])
        orphan_outcomes = int(con.execute(
            """
            SELECT count(*) FROM opportunity_outcomes o
            LEFT JOIN opportunity_decisions d USING (decision_id)
            WHERE d.decision_id IS NULL
            """
        ).fetchone()[0])
        section["stored_causal_violations"] = causal
        section["stored_unreproducible_evaluations"] = unprovable
        section["duplicate_outcome_keys"] = duplicate_outcomes
        section["orphan_outcomes"] = orphan_outcomes
        latest = con.execute("SELECT max(decision_ts) FROM opportunity_decisions").fetchone()[0]
        section["latest_decision_ts"] = int(latest) if latest is not None else None
        section["latest_decision_age_ms"] = (
            max(0, now_ms - int(latest)) if latest is not None else None
        )
        for count, label in (
            (causal, "stored decisions use future-dated inputs"),
            (unprovable, "evaluated decisions lack complete provenance"),
            (duplicate_outcomes, "duplicate decision/outcome keys are stored"),
            (orphan_outcomes, "outcomes have no source decision"),
        ):
            if count:
                section["integrity_failures"].append(f"{count} {label}")
        if section["integrity_failures"]:
            section["status"] = "FAIL"
        elif decisions == 0:
            section["warnings"].append(
                "no opportunity decisions; candidate economics remain blocked"
            )
        elif expect_live and section["latest_decision_age_ms"] > stale_after_ms:
            section["status"] = "DEGRADED"
            section["warnings"].append(
                f"latest decision is older than the declared {stale_after_ms} ms live threshold"
            )
        elif outcomes == 0:
            section["status"] = "COLLECTING"
            section["warnings"].append("decisions exist but no outcomes have resolved")
        else:
            section["status"] = "HEALTHY"
    except Exception as exc:
        section["status"] = "FAIL"
        section["integrity_failures"].append(
            f"opportunity health query failed: {type(exc).__name__}: {exc}"
        )
    finally:
        con.close()
    return section


def build_report(
    revision_db: Path,
    opportunity_db: Path,
    *,
    now_ms: int | None = None,
    outcome_grace_ms: int = 10_000,
    late_threshold_ms: int = 5_000,
    minimum_eligible: int = 20,
    full_state_scan: bool = False,
    state_scan_limit: int = 1_000,
    expect_live: bool = False,
    stale_after_ms: int = 120_000,
) -> dict[str, Any]:
    generated_ts = int(_now_ms() if now_ms is None else now_ms)
    revision = _revision_report(
        Path(revision_db), now_ms=generated_ts, outcome_grace_ms=outcome_grace_ms,
        late_threshold_ms=late_threshold_ms, minimum_eligible=minimum_eligible,
        full_state_scan=full_state_scan, state_scan_limit=state_scan_limit,
        expect_live=expect_live, stale_after_ms=stale_after_ms,
    )
    opportunity = _opportunity_report(
        Path(opportunity_db), now_ms=generated_ts, expect_live=expect_live,
        stale_after_ms=stale_after_ms,
    )
    statuses = {revision["status"], opportunity["status"]}
    if "FAIL" in statuses:
        overall = "FAIL"
    elif "DEGRADED" in statuses:
        overall = "DEGRADED"
    elif revision["status"] == "WAITING_FOR_DATA":
        overall = "WAITING_FOR_DATA"
    elif "WAITING_FOR_DATA" in statuses or "COLLECTING" in statuses:
        overall = "COLLECTING"
    else:
        overall = "HEALTHY"
    return {
        "report_version": REPORT_VERSION,
        "generated_ts": generated_ts,
        "status": overall,
        "purpose": "recorder integrity and coverage only; not an alpha or promotion test",
        "thresholds": {
            "outcome_grace_ms": int(outcome_grace_ms),
            "late_observation_ms": int(late_threshold_ms),
            "maximum_missing_outcome_rate": 0.05,
            "maximum_late_observation_rate": 0.10,
            "minimum_eligible_for_rates": int(minimum_eligible),
            "live_stale_after_ms": int(stale_after_ms),
        },
        "model_revisions": revision,
        "opportunities": opportunity,
        "capital_authority": False,
        "promotion_candidates": 0,
    }


def write_report(report: dict[str, Any], output_path: Path,
                 history_db: Path | None = None) -> None:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True) + "\n"
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, output_path)
    if history_db is None:
        return

    import duckdb

    history_db = Path(history_db)
    history_db.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(history_db))
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS evidence_health_reports (
                report_id VARCHAR PRIMARY KEY,
                report_version VARCHAR NOT NULL,
                generated_ts BIGINT NOT NULL,
                status VARCHAR NOT NULL,
                report_json VARCHAR NOT NULL
            )
        """)
        report_id = hashlib.sha256(
            f"{report['report_version']}\0{report['generated_ts']}\0{payload}".encode("utf-8")
        ).hexdigest()
        con.execute(
            "INSERT INTO evidence_health_reports VALUES (?,?,?,?,?) "
            "ON CONFLICT (report_id) DO NOTHING",
            [report_id, report["report_version"], report["generated_ts"],
             report["status"], payload],
        )
    finally:
        con.close()


def _base_revision(prediction_ts: int) -> dict[str, Any]:
    return {
        "release_id": "release-test", "model_id": "main_ensemble", "horizon_min": 5,
        "prediction_ts": prediction_ts, "prediction": "UP",
        "calibrated_probability": 0.62, "probability_up": 0.62,
        "probability_down": 0.28, "probability_neutral": 0.10,
        "reference_price": 100.0,
        "market_quote": {"venue": "BINANCE", "bid": 99.9, "ask": 100.1},
        "model_outputs": {"raw": "UP", "final": "UP"},
    }


def selftest() -> int:
    checks = 0

    def check(condition: bool, label: str) -> None:
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        revision_db = root / "revisions.duckdb"
        opportunity_db = root / "opportunities.duckdb"
        ledger = ModelRevisionLedger(revision_db)
        state = np.arange(8, dtype=np.float32).reshape(2, 4)
        base = _base_revision(1_000_000)
        ledger.record_batch(
            [base], feature_values=state, feature_names=["a", "b", "c", "d"],
            snapshot_ts=999_900, feature_cutoff_ts=999_000, now_ms=1_000_001,
        )
        ledger.resolve_due(observed_price=101.0, observed_ts=1_001_000,
                           maximum_lateness_ms=0)
        try:
            ledger.record_batch(
                [{**base, "probability_up": 0.61, "probability_down": 0.29}],
                feature_values=state, feature_names=["a", "b", "c", "d"],
                snapshot_ts=999_900, feature_cutoff_ts=999_000, now_ms=1_000_002,
            )
        except RevisionRefusal:
            pass
        try:
            ledger.record_batch(
                [{**base, "prediction_ts": 1_010_000}], feature_values=state,
                feature_names=["a", "b", "c", "d"], snapshot_ts=1_010_001,
                feature_cutoff_ts=1_009_000, now_ms=1_010_002,
            )
        except RevisionRefusal:
            pass

        from opportunity_ledger.ledger import OpportunityLedger

        OpportunityLedger(opportunity_db)
        report = build_report(
            revision_db, opportunity_db, now_ms=1_011_000,
            minimum_eligible=1, state_scan_limit=10,
        )
        revision = report["model_revisions"]
        check(revision["status"] == "HEALTHY", "one mature resolved markout is healthy")
        check(revision["revisions_by_horizon"] == {"5": 1},
              "revision rows are grouped by horizon")
        check(revision["unique_release_ids"] == 1, "release IDs are counted")
        check(revision["markout_coverage"]["MARKOUT_1000MS"]["resolved"] == 1,
              "the resolved one-second outcome is reported")
        check(revision["missing_outcome_rate"] == 0.0,
              "fresh ineligible markouts are not mislabeled missing")
        check(revision["duplicate_refusal_count"] == 1,
              "changed duplicate refusals are persisted and counted")
        check(revision["causal_refusal_count"] == 1,
              "future-state refusals are persisted and counted")
        check(revision["state_decode_failures"] == 0,
              "compressed model state passes decode and hash validation")
        check(report["opportunities"]["status"] == "WAITING_FOR_DATA",
              "an empty opportunity ledger is not called healthy")
        check(report["status"] == "COLLECTING",
              "overall evidence waits for opportunity decisions without failing revision health")

        output = root / "report.json"
        history = root / "history.duckdb"
        write_report(report, output, history)
        check(output.exists() and history.exists(), "JSON report and append-only history are written")

        import duckdb

        con = duckdb.connect(str(revision_db))
        try:
            con.execute(
                "UPDATE model_state_snapshots SET feature_values_zlib = ?",
                [b"not-zlib"],
            )
        finally:
            con.close()
        broken = build_report(
            revision_db, opportunity_db, now_ms=1_011_000,
            minimum_eligible=1, full_state_scan=True,
        )
        check(broken["status"] == "FAIL" and
              broken["model_revisions"]["state_decode_failures"] == 1,
              "state corruption fails the report closed")

    print(f"\nEVIDENCE HEALTH REPORT SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision-db", type=Path, default=DEFAULT_REVISION_DB)
    parser.add_argument("--opportunity-db", type=Path, default=DEFAULT_OPPORTUNITY_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--history-db", type=Path, default=DEFAULT_HISTORY_DB)
    parser.add_argument("--no-history", action="store_true")
    parser.add_argument("--full-state-scan", action="store_true")
    parser.add_argument("--state-scan-limit", type=int, default=1_000)
    parser.add_argument("--minimum-eligible", type=int, default=20)
    parser.add_argument("--late-threshold-ms", type=int, default=5_000)
    parser.add_argument("--outcome-grace-ms", type=int, default=10_000)
    parser.add_argument("--expect-live", action="store_true")
    parser.add_argument("--stale-after-ms", type=int, default=120_000)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()

    report = build_report(
        args.revision_db, args.opportunity_db,
        outcome_grace_ms=max(0, args.outcome_grace_ms),
        late_threshold_ms=max(0, args.late_threshold_ms),
        minimum_eligible=max(1, args.minimum_eligible),
        full_state_scan=bool(args.full_state_scan),
        state_scan_limit=max(1, args.state_scan_limit),
        expect_live=bool(args.expect_live),
        stale_after_ms=max(1, args.stale_after_ms),
    )
    write_report(
        report, args.output, None if args.no_history else args.history_db,
    )
    print(f"Evidence health: {report['status']}")
    print(f"  revisions: {report['model_revisions']['status']}")
    print(f"  opportunities: {report['opportunities']['status']}")
    print(f"  report: {args.output}")
    if args.strict and report["status"] != "HEALTHY":
        return 1
    return 1 if report["status"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
