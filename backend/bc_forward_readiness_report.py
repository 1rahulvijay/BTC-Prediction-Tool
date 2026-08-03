"""Forward-evidence readiness for sealed Protocols B and C. PERFORMANCE-BLIND, by construction.

WHY IT EXPOSES NO PERFORMANCE
    B and C are sealed protocols scored ONCE, after their data gate passes. Any interim view of
    PnL, AUC, model ranking, threshold or action preference is a look at the evidence - and a
    protocol that has been peeked at is no longer a protocol. Repeatedly checking "how is it
    doing?" is exactly how a frozen study becomes a search.

    So this reports COUNTS and COVERAGE only. `assert_performance_blind()` scans the emitted
    payload for forbidden keys and raises rather than printing one, so the guarantee is a
    runtime property and not a promise in a docstring.

    Reaching a complete gate does NOT trigger scoring. The one-time scoring command stays
    separate and deliberate.

    python backend/bc_forward_readiness_report.py
    python backend/bc_forward_readiness_report.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or REPO / "data")
LEDGER_DB = DATA_DIR / "opportunity_ledger.duckdb"
POSITIONS_DB = DATA_DIR / "open_position_actions.duckdb"

#: The declared gate. Both protocols share it.
REQUIRED_DAYS = 56
REQUIRED_WEEKS = 8
REQUIRED_RESOLVED = 1_000

#: Result-shaped words. Matched as whole TOKENS of a snake_case key, never as substrings:
#: "ledger" contains "edge", and a matcher that rejects it teaches people to rename the
#: field rather than to respect the rule.
FORBIDDEN_TOKENS = frozenset({
    "pnl", "profit", "profits", "return", "returns", "auc", "roc", "brier", "logloss",
    "edge", "sharpe", "threshold", "thresholds", "rank", "ranked", "ranking", "score",
    "scores", "scored", "accuracy", "advantage", "preferred", "calibration", "loss",
})
#: Two-token terms that are only result-shaped together ("win rate", "best action").
FORBIDDEN_PAIRS = frozenset({
    ("win", "rate"), ("log", "loss"), ("best", "action"), ("selected", "action"),
    ("hit", "rate"), ("model", "ranking"),
})
_TOKEN = re.compile(r"[a-z0-9]+")


def forbidden_reason(key: str) -> str | None:
    """The forbidden token in `key`, or None. Whole-token matching on the LAST path segment."""
    tokens = _TOKEN.findall(key.rsplit(".", 1)[-1].lower())
    for token in tokens:
        if token in FORBIDDEN_TOKENS:
            return token
    for left, right in zip(tokens, tokens[1:]):
        if (left, right) in FORBIDDEN_PAIRS:
            return f"{left}_{right}"
    return None

STATUSES = ("NOT_STARTED", "COLLECTING", "DATA_GATE_INCOMPLETE", "DATA_GATE_COMPLETE_UNSCORED")


class PerformanceLeak(Exception):
    """Raised when a readiness payload would expose an interim result."""


class SourceUnreadable(Exception):
    """Raised when a required source cannot be read.

    There are exactly four allowed statuses and none of them means "I could not look". A
    swallowed read error would print NOT_STARTED - indistinguishable from an honest empty
    table, and wrong in the one direction that matters, because it says "collection has not
    begun" when the truth is "collection may be fine and I failed to check". A locked live
    writer, a missing duckdb, a renamed table: all must stop the report, not decorate it."""


class MeasurementNotWired(SourceUnreadable):
    """Raised when a gate field has no measurement behind it yet.

    Distinct from SourceUnreadable because the remedy is different: not "fix the database"
    but "build the recorder". A hardcoded 0 for an unwired field is the same defect as a
    swallowed read error, only deferred - it stays 0 after the evidence arrives, so
    "not yet implemented" and "measured and found zero" print identically."""


#: Protocol B is scored on post-entry crossings. Older stores without this table are explicitly
#: reported as unwired; current recorder schemas create and populate it causally.
CROSSING_TABLE = "post_entry_crossing_outcomes"
CROSSING_COLUMNS = (
    "position_id", "round_id", "position_snapshot_id", "crossing_ts", "crossing_direction",
    "is_final_crossing", "reverted_5s", "reverted_15s", "reverted_30s", "reverted_60s",
    "settlement_resolved", "label_version")


def iter_keys(value, path: str = ""):
    """Every key in a nested payload, dotted. Top-level-only scanning misses the ones that
    matter: a leak would arrive inside a nested section or a list of per-day entries, and
    `status` keys are added AFTER the sections are built."""
    if isinstance(value, dict):
        for key, child in value.items():
            current = f"{path}.{key}" if path else str(key)
            yield current
            yield from iter_keys(child, current)
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            yield from iter_keys(child, f"{path}[{index}]")


def assert_performance_blind(payload: dict) -> None:
    """Refuse to emit anything that looks like a result.

    Walks the WHOLE payload recursively, including the ledger section and anything nested,
    and is applied to the final assembled report rather than to sections in isolation.

    Checks KEYS, not values: a count named `resolved_positions` is fine, a count named
    `mean_pnl` is not, and the difference is the name."""
    leaked = sorted({f"{key} ({forbidden_reason(key)})" for key in iter_keys(payload)
                     if forbidden_reason(key)})
    if leaked:
        raise PerformanceLeak(
            f"readiness report may not expose {leaked} - B and C are sealed and scored once. "
            "Reporting an interim result turns a frozen protocol into a search.")


def gate_status(days: int, weeks: int, resolved: int, *, writer_active: bool) -> str:
    """One of the four declared statuses.

    NOT_STARTED and COLLECTING are BOTH zero-evidence states; counts alone cannot separate
    them, which is why `writer_active` is required rather than optional. Without it the
    report cannot distinguish "nothing is running" from "running and nothing qualifies yet",
    and those call for opposite responses from the operator."""
    if days >= REQUIRED_DAYS and weeks >= REQUIRED_WEEKS and resolved >= REQUIRED_RESOLVED:
        return "DATA_GATE_COMPLETE_UNSCORED"
    if resolved > 0 or days > 0:
        return "DATA_GATE_INCOMPLETE"
    return "COLLECTING" if writer_active else "NOT_STARTED"


def _connect(db: Path, required_tables: tuple[str, ...]):
    """Read-only connection, or SourceUnreadable. Never a silent zero.

    Read-only matters twice over: one DuckDB writer is the standing rule, and a readiness
    report must not be able to alter the evidence it is reporting on."""
    if not db.is_file():
        raise SourceUnreadable(f"{db} does not exist - cannot report readiness from it")
    try:
        import duckdb
    except ImportError as exc:                                    # pragma: no cover
        raise SourceUnreadable(f"duckdb unavailable: {exc}") from exc
    try:
        con = duckdb.connect(str(db), read_only=True)
    except Exception as exc:
        raise SourceUnreadable(f"cannot open {db.name} read-only: {exc}") from exc
    present = {r[0] for r in con.execute(
        "SELECT table_name FROM information_schema.tables "
        "WHERE table_schema='main'").fetchall()}
    missing = [t for t in required_tables if t not in present]
    if missing:
        con.close()
        raise SourceUnreadable(f"{db.name} is missing table(s) {missing} - schema drifted")
    return con


def _rate(numerator: int, denominator: int) -> float:
    """Coverage with an explicit empty case: 0 of 0 is 0.0 coverage, not a division error."""
    return (numerator / denominator) if denominator else 0.0


def _database_bytes(path: Path) -> int:
    """Current database footprint including its active WAL, without opening the store."""
    return sum(
        candidate.stat().st_size
        for candidate in (path, Path(f"{path}.wal"))
        if candidate.exists()
    )


def _ledger_counts(db: Path | None = None) -> dict:
    """Counts from the causal decision ledger. Raises rather than reporting a false zero."""
    target = db or LEDGER_DB
    con = _connect(target, ("opportunity_decisions",))
    try:
        import time
        today_ms = (int(time.time() * 1000) // 86_400_000) * 86_400_000
        row = con.execute("""
            SELECT count(*),
                   count(DISTINCT CAST(decision_ts / 86400000 AS BIGINT)),
                   count(DISTINCT CAST(decision_ts / 604800000 AS BIGINT)),
                   count(*) FILTER (WHERE action = 'ENTER'),
                   count(*) FILTER (WHERE action = 'WAIT'),
                   count(*) FILTER (WHERE action = 'UNAVAILABLE'),
                   max(decision_ts),
                   count(*) FILTER (WHERE decision_ts >= ?)
            FROM opportunity_decisions""", [today_ms]).fetchone()
        columns = {str(item[0]) for item in con.execute(
            "DESCRIBE opportunity_decisions"
        ).fetchall()}
        rounds_today = 0
        if "round_id" in columns:
            rounds_today = int(con.execute(
                "SELECT count(DISTINCT round_id) FROM opportunity_decisions "
                "WHERE decision_ts >= ?", [today_ms]
            ).fetchone()[0])
        violations = con.execute(
            "SELECT count(*) FROM opportunity_decisions "
            "WHERE state_snapshot_ts > decision_ts OR quote_recv_ts > decision_ts").fetchone()[0]
    finally:
        con.close()
    return {"decisions": int(row[0]), "days": int(row[1]), "weeks": int(row[2]),
            "enter_rows": int(row[3]), "wait_rows": int(row[4]),
            "unavailable_rows": int(row[5]),
            "last_decision_ms": int(row[6]) if row[6] is not None else 0,
            "rows_today": int(row[7]), "independent_rounds_today": rounds_today,
            "database_bytes": _database_bytes(Path(target)),
            "causal_refusals": int(violations)}


#: Protocol C's five declared arms. Coverage is measured against this exact set.
ACTION_ARMS = ("HOLD", "EXIT", "REDUCE_50", "SWITCH", "LOCK")


#: A paired book whose two sides are further apart than this is not one observation.
MAX_PAIR_SKEW_MS = 2000

#: How recently the recorder must have attempted a capture to count as alive.
HEARTBEAT_WINDOW_MS = 30 * 60 * 1000


def _position_counts(db: Path | None = None) -> dict:
    """Protocol C coverage, read from the open-position recorder. Raises on unreadable."""
    target = db or POSITIONS_DB
    con = _connect(target, (
        "open_position_snapshots", "paired_book_snapshots", "open_position_action_arms",
        "open_position_recorder_refusals", "open_position_capture_attempts",
        "open_position_recorder_heartbeats", "open_position_action_outcomes"))
    try:
        import time
        today_ms = (int(time.time() * 1000) // 86_400_000) * 86_400_000
        snaps, positions, days, weeks, paired_linked, latest_snapshot, snapshots_today, rounds_today = con.execute("""
            SELECT count(*), count(DISTINCT position_id),
                   count(DISTINCT CAST(snapshot_ts / 86400000 AS BIGINT)),
                   count(DISTINCT CAST(snapshot_ts / 604800000 AS BIGINT)),
                   count(*) FILTER (WHERE paired_snapshot_id IS NOT NULL),
                   max(recorded_ts),
                   count(*) FILTER (WHERE recorded_ts >= ?),
                   count(DISTINCT round_id) FILTER (WHERE recorded_ts >= ?)
            FROM open_position_snapshots""", [today_ms, today_ms]).fetchone()
        paired, fee_ruled, skewed, paired_today = con.execute("""
            SELECT count(*),
                   count(*) FILTER (WHERE fee_rate IS NOT NULL AND fees_enabled IS NOT NULL),
                   count(*) FILTER (WHERE pair_skew_ms IS NOT NULL
                                      AND abs(pair_skew_ms) > 2000),
                   count(*) FILTER (WHERE written_ts >= ?)
            FROM paired_book_snapshots""", [today_ms]).fetchone()
        # DIAGNOSTIC ONLY - a snapshot with at least one complete arm. This must never be the
        # gate: 1,000 snapshots carrying a complete HOLD and nothing else would satisfy the
        # count while the five-arm comparison that DEFINES Protocol C does not exist.
        any_arm = con.execute(
            "SELECT count(DISTINCT position_snapshot_id) FROM open_position_action_arms "
            "WHERE complete").fetchone()[0]
        per_arm = {arm: con.execute(
            "SELECT count(DISTINCT position_snapshot_id) FROM open_position_action_arms "
            "WHERE complete AND action = ?", [arm]).fetchone()[0] for arm in ACTION_ARMS}
        all_five = con.execute(f"""
            SELECT count(*) FROM (
                SELECT position_snapshot_id FROM open_position_action_arms
                WHERE complete AND action IN ({','.join('?' * len(ACTION_ARMS))})
                GROUP BY position_snapshot_id
                HAVING count(DISTINCT action) = {len(ACTION_ARMS)})""",
            list(ACTION_ARMS)).fetchone()[0]
        residual, partial = con.execute("""
            SELECT count(*) FILTER (WHERE up_shares_after IS NOT NULL
                                      AND down_shares_after IS NOT NULL),
                   count(*) FILTER (WHERE execution_json IS NOT NULL
                                      AND execution_json LIKE '%partial%')
            FROM open_position_action_arms WHERE complete""").fetchone()
        settled = con.execute("""
            SELECT count(DISTINCT position_snapshot_id || ':' || action)
            FROM open_position_action_outcomes
            WHERE lower(settlement_source) LIKE 'official:%'
        """).fetchone()[0]
        arms_today = con.execute("""
            SELECT count(*) FROM open_position_action_arms a
            JOIN open_position_snapshots s USING (position_snapshot_id)
            WHERE s.recorded_ts >= ?
        """, [today_ms]).fetchone()[0]
        outcomes_today = con.execute(
            "SELECT count(*) FROM open_position_action_outcomes WHERE recorded_ts >= ?",
            [today_ms],
        ).fetchone()[0]

        # THE ACTUAL PROTOCOL C GATE. A qualifying unit is a distinct ROUND - not a snapshot,
        # not an arm row - in which all five arms are complete FROM THE SAME causal snapshot
        # (guaranteed by grouping on position_snapshot_id), settlement is resolved, the paired
        # book carries a fee rule, its two sides are within the skew bound, and the book was
        # received before the snapshot it informed. Rounds, because Protocol C's frozen
        # requirement counts independent resolved opportunities, and several snapshots of one
        # round are the same opportunity observed repeatedly.
        placeholders = ",".join("?" * len(ACTION_ARMS))
        five_arm_rounds = con.execute(f"""
            SELECT count(DISTINCT s.round_id)
            FROM open_position_snapshots s
            JOIN paired_book_snapshots p
              ON p.paired_snapshot_id = s.paired_snapshot_id
            JOIN (SELECT position_snapshot_id
                  FROM open_position_action_arms
                  WHERE complete AND action IN ({placeholders})
                  GROUP BY position_snapshot_id
                  HAVING count(DISTINCT action) = {len(ACTION_ARMS)}) a
              ON a.position_snapshot_id = s.position_snapshot_id
            JOIN (SELECT position_snapshot_id
                  FROM open_position_action_outcomes
                  WHERE action IN ({placeholders})
                    AND lower(settlement_source) LIKE 'official:%'
                  GROUP BY position_snapshot_id
                  HAVING count(DISTINCT action) = {len(ACTION_ARMS)}) o
              ON o.position_snapshot_id = s.position_snapshot_id
            WHERE p.fee_rate IS NOT NULL AND p.fees_enabled IS NOT NULL
              AND abs(coalesce(p.pair_skew_ms, 0)) <= {MAX_PAIR_SKEW_MS}
              AND p.up_recv_ts <= s.snapshot_ts AND p.down_recv_ts <= s.snapshot_ts
              AND s.recorded_ts >= s.snapshot_ts
              AND s.round_id IS NOT NULL""", list(ACTION_ARMS) + list(ACTION_ARMS)).fetchone()[0]
        all_five_official = con.execute(f"""
            SELECT count(DISTINCT position_snapshot_id)
            FROM open_position_action_outcomes
            WHERE action IN ({placeholders})
              AND lower(settlement_source) LIKE 'official:%'
            GROUP BY position_snapshot_id
            HAVING count(DISTINCT action) = {len(ACTION_ARMS)}
        """, list(ACTION_ARMS)).fetchall()
        # The gate's own span must come from the qualifying rounds, not from every snapshot
        # ever written - otherwise a long dark stretch of unusable captures inflates it.
        gate_days, gate_weeks = con.execute(f"""
            SELECT count(DISTINCT CAST(s.snapshot_ts / 86400000 AS BIGINT)),
                   count(DISTINCT CAST(s.snapshot_ts / 604800000 AS BIGINT))
            FROM open_position_snapshots s
            JOIN paired_book_snapshots p
              ON p.paired_snapshot_id = s.paired_snapshot_id
            JOIN (SELECT position_snapshot_id
                  FROM open_position_action_arms
                  WHERE complete AND action IN ({placeholders})
                  GROUP BY position_snapshot_id
                  HAVING count(DISTINCT action) = {len(ACTION_ARMS)}) a
              ON a.position_snapshot_id = s.position_snapshot_id
            JOIN (SELECT position_snapshot_id
                  FROM open_position_action_outcomes
                  WHERE action IN ({placeholders})
                    AND lower(settlement_source) LIKE 'official:%'
                  GROUP BY position_snapshot_id
                  HAVING count(DISTINCT action) = {len(ACTION_ARMS)}) o
              ON o.position_snapshot_id = s.position_snapshot_id
            WHERE p.fee_rate IS NOT NULL AND p.fees_enabled IS NOT NULL
              AND abs(coalesce(p.pair_skew_ms, 0)) <= {MAX_PAIR_SKEW_MS}
              AND p.up_recv_ts <= s.snapshot_ts AND p.down_recv_ts <= s.snapshot_ts
              AND s.recorded_ts >= s.snapshot_ts
              AND s.round_id IS NOT NULL""", list(ACTION_ARMS) + list(ACTION_ARMS)).fetchone()

        # RECORDER LIVENESS. Capture ATTEMPTS, not successes: a recorder that runs and rejects
        # everything is alive and collecting nothing, which is COLLECTING, not NOT_STARTED.
        attempts, _last_attempt, accepted, refused = con.execute("""
            SELECT count(*), max(attempted_ts),
                   count(*) FILTER (WHERE upper(status) IN ('OK', 'ACCEPTED', 'RECORDED')),
                   count(*) FILTER (WHERE upper(status) NOT IN ('OK', 'ACCEPTED', 'RECORDED'))
            FROM open_position_capture_attempts""").fetchone()
        heartbeats, last_heartbeat = con.execute("""
            SELECT count(*), max(heartbeat_ts)
            FROM open_position_recorder_heartbeats""").fetchone()
        stale = con.execute(
            "SELECT count(*) FROM open_position_recorder_refusals "
            "WHERE lower(category) LIKE '%stale%'").fetchone()[0]
        skew_refused = con.execute(
            "SELECT count(*) FROM open_position_recorder_refusals "
            "WHERE lower(category) LIKE '%skew%' OR lower(category) LIKE '%clock%'").fetchone()[0]
    finally:
        con.close()
    import time
    now_ms = int(time.time() * 1000)
    alive = (last_heartbeat is not None
             and (now_ms - int(last_heartbeat)) <= HEARTBEAT_WINDOW_MS)
    return {
        "snapshots": int(snaps), "positions": int(positions), "days": int(days),
        "weeks": int(weeks), "paired_linked": int(paired_linked), "paired": int(paired),
        "fee_ruled": int(fee_ruled), "any_arm": int(any_arm), "all_five": int(all_five),
        "five_arm_rounds": int(five_arm_rounds),
        "gate_days": int(gate_days), "gate_weeks": int(gate_weeks),
        "per_arm": {k: int(v) for k, v in per_arm.items()},
        "residual": int(residual), "settled": int(settled), "partial": int(partial),
        "stale_refusals": int(stale), "skew_refusals": int(skew_refused) + int(skewed),
        "capture_attempts": int(attempts),
        "heartbeats": int(heartbeats),
        "last_heartbeat_ms": int(last_heartbeat) if last_heartbeat is not None else 0,
        "captures_accepted": int(accepted), "captures_refused": int(refused),
        "latest_snapshot_ms": int(latest_snapshot) if latest_snapshot is not None else 0,
        "snapshots_today": int(snapshots_today),
        "independent_rounds_today": int(rounds_today),
        "paired_today": int(paired_today), "arms_today": int(arms_today),
        "outcomes_today": int(outcomes_today),
        "five_arm_snapshots_missing_official_outcomes": max(
            0, int(all_five) - len(all_five_official)
        ),
        "database_bytes": _database_bytes(Path(target)),
        "writer_active": bool(alive),
    }


def _crossing_counts(db: Path | None = None) -> dict:
    """Protocol B crossing labels. Raises MeasurementNotWired until a recorder writes them.

    These were previously hardcoded to 0. That is the same fabrication as a swallowed read
    error, only delayed: the numbers were right today and would have stayed 0 forever, so a
    B gate could never move and nobody would learn why."""
    target = db or POSITIONS_DB
    con = _connect(target, ())
    try:
        present = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main'").fetchall()}
        if CROSSING_TABLE not in present:
            raise MeasurementNotWired(
                f"Protocol B needs table '{CROSSING_TABLE}' in {target.name} with columns "
                f"{list(CROSSING_COLUMNS)}. This store predates the crossing recorder, so readiness "
                f"is UNMEASURED - it is not zero.")
        columns = {r[0] for r in con.execute(f"DESCRIBE {CROSSING_TABLE}").fetchall()}
        missing = [c for c in CROSSING_COLUMNS if c not in columns]
        if missing:
            raise MeasurementNotWired(
                f"'{CROSSING_TABLE}' exists but lacks {missing} - Protocol B labels cannot be "
                f"counted from it")
        rounds, crossings, final_labels, reversions, settled, dupes = con.execute("""
            SELECT count(DISTINCT round_id) FILTER (
                       WHERE settlement_resolved AND is_final_crossing = TRUE),
                   count(*),
                   count(*) FILTER (WHERE settlement_resolved
                                      AND is_final_crossing IS NOT NULL),
                   count(*) FILTER (WHERE reverted_5s IS NOT NULL AND reverted_15s IS NOT NULL
                                      AND reverted_30s IS NOT NULL AND reverted_60s IS NOT NULL
                                      AND settlement_resolved),
                   count(*) FILTER (WHERE settlement_resolved),
                   count(*) - count(DISTINCT (position_snapshot_id, crossing_ts))
            FROM post_entry_crossing_outcomes""").fetchone()
    finally:
        con.close()
    return {"rounds": int(rounds), "crossings": int(crossings),
            "final_labels": int(final_labels), "reversions": int(reversions),
            "settled": int(settled), "duplicates": int(dupes)}


def build_report(*, positions_db: Path | None = None,
                 ledger_db: Path | None = None) -> dict:
    ledger = _ledger_counts(ledger_db)
    pos = _position_counts(positions_db)
    active = pos["writer_active"]

    # Protocol B is scored on post-entry crossings of OPEN positions, so its span is the
    # open-position recorder's span, not the decision ledger's. Reporting ledger days here
    # would let WAIT-only decisions inflate a gate that requires positions.
    try:
        cross = _crossing_counts(positions_db)
        protocol_b = {
            "calendar_days": pos["days"], "independent_weeks": pos["weeks"],
            "open_position_checkpoints": pos["snapshots"],
            "qualifying_post_entry_crossings": cross["crossings"],
            # The GATE unit is a resolved ROUND, matching Protocol B's frozen requirement of
            # independent resolved opportunities - not a count of crossing rows, of which one
            # round can produce many.
            "final_crossing_labels_resolved": cross["rounds"],
            "final_crossing_label_rows": cross["final_labels"],
            "reversion_labels_resolved": cross["reversions"],
            "same_time_exit_price_coverage": _rate(pos["paired_linked"], pos["snapshots"]),
            "missing_settlement_rate": (1.0 - _rate(cross["settled"], cross["crossings"])
                                        if cross["crossings"] else 0.0),
            "causal_refusals": ledger["causal_refusals"] + pos["skew_refusals"],
            "duplicate_rows": cross["duplicates"],
            "measurement": "WIRED",
        }
        protocol_b["status"] = gate_status(
            protocol_b["calendar_days"], protocol_b["independent_weeks"],
            protocol_b["final_crossing_labels_resolved"], writer_active=active)
    except MeasurementNotWired as exc:
        # No fabricated zeros. The section reports what IS measured and names what is not.
        protocol_b = {
            "calendar_days": pos["days"], "independent_weeks": pos["weeks"],
            "open_position_checkpoints": pos["snapshots"],
            "same_time_exit_price_coverage": _rate(pos["paired_linked"], pos["snapshots"]),
            "causal_refusals": ledger["causal_refusals"] + pos["skew_refusals"],
            "measurement": "NOT_WIRED",
            "unmeasured_reason": str(exc),
            "unmeasured_fields": ["qualifying_post_entry_crossings",
                                  "final_crossing_labels_resolved",
                                  "reversion_labels_resolved", "missing_settlement_rate",
                                  "duplicate_rows"],
            "status": None,
        }

    protocol_c = {
        "calendar_days": pos["gate_days"], "independent_weeks": pos["gate_weeks"],
        "open_positions_observed": pos["positions"],
        "paired_book_snapshots": pos["paired"],
        # THE GATE. Fully resolved five-arm rounds - see _position_counts for the conditions.
        "full_resolved_five_arm_rounds": pos["five_arm_rounds"],
        # Diagnostic coverage. Deliberately NOT the gate.
        "any_arm_complete_snapshots": pos["any_arm"],
        "coverage_hold": _rate(pos["per_arm"]["HOLD"], pos["snapshots"]),
        "coverage_exit": _rate(pos["per_arm"]["EXIT"], pos["snapshots"]),
        "coverage_reduce_50": _rate(pos["per_arm"]["REDUCE_50"], pos["snapshots"]),
        "coverage_switch": _rate(pos["per_arm"]["SWITCH"], pos["snapshots"]),
        "coverage_lock": _rate(pos["per_arm"]["LOCK"], pos["snapshots"]),
        "coverage_all_five_arms": _rate(pos["all_five"], pos["snapshots"]),
        "partial_fill_frequency": _rate(pos["partial"], pos["any_arm"]),
        "residual_inventory_coverage": _rate(pos["residual"], pos["any_arm"]),
        "bid_depth_coverage": _rate(pos["paired_linked"], pos["snapshots"]),
        "fee_rule_coverage": _rate(pos["fee_ruled"], pos["paired"]),
        "settlement_coverage": _rate(pos["settled"], pos["any_arm"]),
        "clock_skew_refusals": pos["skew_refusals"],
        "stale_book_refusals": pos["stale_refusals"],
        "measurement": "WIRED",
    }
    protocol_c["status"] = gate_status(
        protocol_c["calendar_days"], protocol_c["independent_weeks"],
        protocol_c["full_resolved_five_arm_rounds"], writer_active=active)

    report = {
        "requirements": {
            "required_days": REQUIRED_DAYS,
            "required_weeks": REQUIRED_WEEKS,
            "required_resolved_rounds": REQUIRED_RESOLVED,
        },
        "daily_collection": {
            "last_successful_position_write_ms": pos["latest_snapshot_ms"],
            "last_recorder_heartbeat_ms": pos["last_heartbeat_ms"],
            "opportunity_rows_today": ledger["rows_today"],
            "opportunity_rounds_today": ledger["independent_rounds_today"],
            "position_snapshots_today": pos["snapshots_today"],
            "position_rounds_today": pos["independent_rounds_today"],
            "paired_books_today": pos["paired_today"],
            "action_arms_today": pos["arms_today"],
            "action_outcomes_today": pos["outcomes_today"],
            "five_arm_snapshots_missing_official_outcomes": pos[
                "five_arm_snapshots_missing_official_outcomes"
            ],
            "paired_book_coverage": _rate(pos["paired_linked"], pos["snapshots"]),
            "five_arm_coverage": _rate(pos["all_five"], pos["snapshots"]),
            "stale_book_refusals": pos["stale_refusals"],
            "clock_skew_refusals": pos["skew_refusals"],
            "opportunity_database_bytes": ledger["database_bytes"],
            "position_database_bytes": pos["database_bytes"],
        },
        "recorder": {
            "writer_active": active,
            "capture_attempts": pos["capture_attempts"],
            "heartbeats": pos["heartbeats"],
            "last_heartbeat_ms": pos["last_heartbeat_ms"],
            "captures_accepted": pos["captures_accepted"],
            "captures_refused": pos["captures_refused"],
            "heartbeat_window_ms": HEARTBEAT_WINDOW_MS,
        },
        "ledger": ledger, "B": protocol_b, "C": protocol_c,
    }
    # Applied to the FINAL assembled payload, recursively - after statuses and nested
    # sections exist, because that is when a leak would actually be present.
    assert_performance_blind(report)
    return report


def _plant_positions(db: Path, *, arms, snapshots: int, days: int,
                     pair_skew_ms: int = 0, book_after: bool = False,
                     settled: bool = True) -> None:
    """Build a synthetic recorder database for negative testing.

    Fixtures exist so the gate can be shown to REFUSE a planted offender. A gate that has
    only ever been run against empty tables has not been tested; it has been assumed."""
    import duckdb
    con = duckdb.connect(str(db))
    con.execute("""CREATE TABLE open_position_snapshots(
        position_snapshot_id BIGINT, schema_version INTEGER, position_id BIGINT,
        round_id BIGINT, strategy_id VARCHAR, horizon_min INTEGER, opened_ts BIGINT,
        paired_snapshot_id BIGINT, snapshot_ts BIGINT, recorded_ts BIGINT,
        up_shares DOUBLE, down_shares DOUBLE, net_cost_basis DOUBLE, entry_fees DOUBLE,
        inventory_source VARCHAR, position_state_json VARCHAR, context_json VARCHAR,
        payload_hash VARCHAR)""")
    con.execute("""CREATE TABLE paired_book_snapshots(
        paired_snapshot_id BIGINT, schema_version INTEGER, quote_ts BIGINT,
        up_recv_ts BIGINT, down_recv_ts BIGINT, pair_skew_ms BIGINT, fee_rate DOUBLE,
        fees_enabled BOOLEAN, up_book_hash VARCHAR, down_book_hash VARCHAR,
        payload_json VARCHAR, written_ts BIGINT)""")
    con.execute("""CREATE TABLE open_position_action_arms(
        position_snapshot_id BIGINT, action VARCHAR, research_only BOOLEAN,
        executable BOOLEAN, complete BOOLEAN, reject_reason VARCHAR, cash_flow DOUBLE,
        fees DOUBLE, up_shares_after DOUBLE, down_shares_after DOUBLE,
        settlement_floor DOUBLE, settlement_floor_net DOUBLE, execution_json VARCHAR)""")
    con.execute("""CREATE TABLE open_position_recorder_refusals(
        refusal_id BIGINT, schema_version INTEGER, refused_ts BIGINT, category VARCHAR,
        reason VARCHAR, raw_position_json VARCHAR)""")
    con.execute("""CREATE TABLE open_position_capture_attempts(
        attempt_id BIGINT, schema_version INTEGER, position_id BIGINT, round_id BIGINT,
        strategy_id VARCHAR, attempted_ts BIGINT, status VARCHAR, reason VARCHAR)""")
    con.execute("""CREATE TABLE open_position_recorder_heartbeats(
        heartbeat_id BIGINT, schema_version INTEGER, heartbeat_ts BIGINT, round_id BIGINT,
        open_position_count INTEGER, status VARCHAR, reason VARCHAR)""")
    con.execute("""CREATE TABLE open_position_action_outcomes(
        outcome_id VARCHAR, position_snapshot_id BIGINT, action VARCHAR, round_id BIGINT,
        settled_ts BIGINT, settled_side VARCHAR, realized_gross DOUBLE, realized_net DOUBLE,
        settlement_source VARCHAR, recorded_ts BIGINT, label_version VARCHAR)""")
    con.execute("CREATE TEMP TABLE fixture_arms(action VARCHAR)")
    con.executemany("INSERT INTO fixture_arms VALUES (?)", [(arm,) for arm in arms])
    con.execute(
        """CREATE TEMP TABLE fixture_rows AS
           SELECT i,
                  ((i % ?) * 86400000 + floor(i / ?) * 60000 + 86400000)::BIGINT AS ts
           FROM range(?) AS rows(i)""",
        [days, days, snapshots],
    )
    con.execute(
        """INSERT INTO open_position_snapshots
           SELECT i, 1, i, i, 's', 5, ts - 60000, i, ts, ts + 100,
                  1.0, 1.0, 0.7, 0.01, 'live', '{}', '{}', 'h'
           FROM fixture_rows"""
    )
    recv_offset = 5_000 if book_after else -5_000
    con.execute(
        """INSERT INTO paired_book_snapshots
           SELECT i, 1, ts, ts + ?, ts + ?, ?, 0.02, true, 'u', 'd', '{}', ts
           FROM fixture_rows""",
        [recv_offset, recv_offset, pair_skew_ms],
    )
    con.execute(
        """INSERT INTO open_position_action_arms
           SELECT i, action, false, true, true, NULL, 0.0, 0.0,
                  1.0, 1.0, 0.5, 0.5, '{}'
           FROM fixture_rows CROSS JOIN fixture_arms"""
    )
    if settled:
        con.execute(
            """INSERT INTO open_position_action_outcomes
               SELECT concat(i, ':', action), i, action, i, ts + 60000,
                      'UP', 1.0, 0.3, 'official:test', ts + 60000, 'v1'
               FROM fixture_rows CROSS JOIN fixture_arms"""
        )
    con.execute(
        """INSERT INTO open_position_capture_attempts
           SELECT i, 1, i, i, 's', ts, 'OK', NULL FROM fixture_rows"""
    )
    con.execute(
        """INSERT INTO open_position_recorder_heartbeats
           SELECT i, 1, ts, i, 1, 'CAPTURE_CYCLE', 'test' FROM fixture_rows"""
    )
    con.close()


def _plant_crossings(db: Path, *, rows: int) -> None:
    """Add a post-entry crossing table, so the WIRED path is exercised too."""
    import duckdb
    con = duckdb.connect(str(db))
    con.execute(f"""CREATE TABLE {CROSSING_TABLE}(
        position_id BIGINT, round_id BIGINT, position_snapshot_id BIGINT,
        crossing_ts BIGINT, crossing_direction VARCHAR, is_final_crossing BOOLEAN,
        reverted_5s BOOLEAN, reverted_15s BOOLEAN, reverted_30s BOOLEAN,
        reverted_60s BOOLEAN, settlement_resolved BOOLEAN, label_version VARCHAR)""")
    for i in range(rows):
        con.execute(f"INSERT INTO {CROSSING_TABLE} VALUES "
                    "(?,?,?,?,'UP',true,false,false,true,true,true,'v1')",
                    [i, i, i, 1_000 + i])
    con.close()


def _plant_ledger(db: Path) -> None:
    import duckdb
    con = duckdb.connect(str(db))
    con.execute("""CREATE TABLE opportunity_decisions(
        decision_ts BIGINT, action VARCHAR, state_snapshot_ts BIGINT, quote_recv_ts BIGINT)""")
    con.close()


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    assert_performance_blind({"resolved_positions": 214, "coverage_all_five_arms": 0.61})
    check(True, "counts and coverage are permitted")

    for forbidden in ("mean_pnl", "auc", "brier_score", "best_action", "preferred_threshold",
                      "action_advantage", "win_rate"):
        try:
            assert_performance_blind({forbidden: 1.0})
            check(False, "unreachable")
        except PerformanceLeak:
            pass
    check(True, "every performance-shaped key is REFUSED, not merely omitted")

    nested = {"C": {"per_day": [{"day": 1, "mean_pnl": 0.4}]}}
    try:
        assert_performance_blind(nested)
        check(False, "unreachable")
    except PerformanceLeak:
        pass
    check(True, "a leak NESTED inside a list of dicts is caught, not just top-level keys")

    # Substring matching rejected "ledger" for containing "edge". A blindness rule that
    # fires on honest field names gets worked around, not obeyed.
    for innocent in ("ledger", "hedge_count", "ledger.decisions", "reversion_labels_resolved",
                     "captures_accepted", "settlement_coverage"):
        check(forbidden_reason(innocent) is None,
              f"'{innocent}' is not a leak - tokens are matched whole, not as substrings")
    for guilty, token in (("mean_pnl", "pnl"), ("model_auc", "auc"), ("win_rate", "win_rate"),
                          ("informational_edge", "edge"), ("log_loss", "loss")):
        check(forbidden_reason(guilty) == token,
              f"'{guilty}' is refused, and the report names WHY ({token})")

    check(gate_status(0, 0, 0, writer_active=False) == "NOT_STARTED",
          "zero evidence with a DEAD writer is NOT_STARTED")
    check(gate_status(0, 0, 0, writer_active=True) == "COLLECTING",
          "zero evidence with a LIVE writer is COLLECTING - the status is reachable")
    check(gate_status(19, 3, 214, writer_active=True) == "DATA_GATE_INCOMPLETE",
          "partial collection is DATA_GATE_INCOMPLETE")
    check(gate_status(REQUIRED_DAYS, REQUIRED_WEEKS, REQUIRED_RESOLVED, writer_active=True)
          == "DATA_GATE_COMPLETE_UNSCORED",
          "a complete gate is COMPLETE_UNSCORED - it never says SCORE or PASS")
    check(gate_status(REQUIRED_DAYS, REQUIRED_WEEKS, REQUIRED_RESOLVED - 1, writer_active=True)
          == "DATA_GATE_INCOMPLETE",
          "one resolved round short of the gate is still incomplete")
    check(gate_status(REQUIRED_DAYS, REQUIRED_WEEKS, REQUIRED_RESOLVED, writer_active=False)
          == "DATA_GATE_COMPLETE_UNSCORED",
          "a complete gate stands even if the writer has since stopped")
    check({gate_status(d, w, r, writer_active=a)
           for d, w, r in ((0, 0, 0), (19, 3, 214),
                           (REQUIRED_DAYS, REQUIRED_WEEKS, REQUIRED_RESOLVED))
           for a in (True, False)} <= set(STATUSES),
          "only the four declared statuses are ever emitted")

    check(_rate(0, 0) == 0.0, "0 of 0 is 0.0 coverage, not a division error")
    check(_rate(3, 4) == 0.75, "coverage is a plain proportion")

    # THE VACUOUS-PASS CHECK. A swallowed read error would print NOT_STARTED, which is
    # indistinguishable from an honest empty table and wrong in the direction that matters.
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _connect(Path(tmp) / "absent.duckdb", ("t",))
            check(False, "unreachable")
        except SourceUnreadable:
            pass
    check(True, "a MISSING source raises SourceUnreadable - it never becomes NOT_STARTED")

    with tempfile.TemporaryDirectory() as tmp:
        import duckdb
        drifted = Path(tmp) / "drifted.duckdb"
        con = duckdb.connect(str(drifted))
        con.execute("CREATE TABLE unrelated(x INTEGER)")
        con.close()
        try:
            _connect(drifted, ("open_position_snapshots",))
            check(False, "unreachable")
        except SourceUnreadable:
            pass
    check(True, "a RENAMED/dropped table raises rather than reporting zero coverage")

    # THE GATE-DENOMINATOR CHECK. Plant exactly the state that would have passed the old
    # gate: 1,200 snapshots over 60 days, every one carrying a complete HOLD arm and nothing
    # else. Protocol C is defined by the FIVE-arm comparison, so this must not be complete.
    with tempfile.TemporaryDirectory() as tmp:
        fixture = Path(tmp) / "hold_only.duckdb"
        _plant_positions(fixture, arms=("HOLD",), snapshots=1_200, days=60)
        counts = _position_counts(fixture)
        check(counts["any_arm"] == 1_200,
              "the HOLD-only fixture does have 1,200 snapshots with a complete arm")
        check(counts["five_arm_rounds"] == 0,
              "...and ZERO fully resolved five-arm rounds - the gate denominator is not fooled")
        check(gate_status(counts["gate_days"], counts["gate_weeks"],
                          counts["five_arm_rounds"], writer_active=True) != "DATA_GATE_COMPLETE_UNSCORED",
              "1,200 HOLD-only snapshots do NOT complete Protocol C's gate")

        full = Path(tmp) / "five_arm.duckdb"
        _plant_positions(full, arms=ACTION_ARMS, snapshots=1_200, days=60)
        counts = _position_counts(full)
        check(counts["five_arm_rounds"] == 1_200,
              "a genuine five-arm fixture DOES count - the gate is not merely always zero")
        check(gate_status(counts["gate_days"], counts["gate_weeks"],
                          counts["five_arm_rounds"], writer_active=True)
              == "DATA_GATE_COMPLETE_UNSCORED",
              "...and completes the gate, still UNSCORED")

        skewed = Path(tmp) / "skewed.duckdb"
        _plant_positions(skewed, arms=ACTION_ARMS, snapshots=1_200, days=60, pair_skew_ms=9_000)
        check(_position_counts(skewed)["five_arm_rounds"] == 0,
              "five complete arms on a SKEWED paired book do not qualify")

        acausal = Path(tmp) / "acausal.duckdb"
        _plant_positions(acausal, arms=ACTION_ARMS, snapshots=1_200, days=60, book_after=True)
        check(_position_counts(acausal)["five_arm_rounds"] == 0,
              "five complete arms priced off a book received AFTER the snapshot do not qualify")

        unsettled = Path(tmp) / "unsettled.duckdb"
        _plant_positions(unsettled, arms=ACTION_ARMS, snapshots=1_200, days=60, settled=False)
        check(_position_counts(unsettled)["five_arm_rounds"] == 0,
              "five complete arms without a resolved settlement do not qualify")

        # A legacy store without the new crossing table must refuse rather than report zero.
        try:
            _crossing_counts(full)
            check(False, "unreachable")
        except MeasurementNotWired:
            pass
        check(True, "a pre-crossing-recorder store raises MeasurementNotWired - never a false 0")

        _plant_crossings(full, rows=3)
        cross = _crossing_counts(full)
        check(cross["crossings"] == 3 and cross["rounds"] == 3,
              "...and once the table exists, the counts are READ rather than assumed")

    with tempfile.TemporaryDirectory() as tmp:
        positions = Path(tmp) / "report_positions.duckdb"
        ledger_db = Path(tmp) / "report_ledger.duckdb"
        _plant_positions(positions, arms=ACTION_ARMS, snapshots=3, days=3)
        _plant_crossings(positions, rows=3)
        _plant_ledger(ledger_db)
        report = build_report(positions_db=positions, ledger_db=ledger_db)
    check(set(report) == {"requirements", "daily_collection", "recorder", "ledger", "B", "C"},
          "the report exposes only requirements, collection health and sealed protocols")
    check(report["requirements"] == {
        "required_days": REQUIRED_DAYS,
        "required_weeks": REQUIRED_WEEKS,
        "required_resolved_rounds": REQUIRED_RESOLVED,
    }, "the UI receives the same frozen gate constants used by backend scoring")
    check(report["daily_collection"]["five_arm_snapshots_missing_official_outcomes"] == 0,
          "daily health reports official action-outcome completeness without performance")
    check(not any(forbidden_reason(k) for k in iter_keys(report)),
          "the REAL report contains no performance-shaped key at ANY depth")
    check(report["C"]["status"] in STATUSES, "Protocol C emits a declared status")
    check(report["B"]["measurement"] in {"WIRED", "NOT_WIRED"},
          "Protocol B reports the measured schema state rather than fabricating counts")
    check(set(ACTION_ARMS) == {"HOLD", "EXIT", "REDUCE_50", "SWITCH", "LOCK"},
          "coverage is measured against Protocol C's five declared arms")

    print(f"\nB/C READINESS SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument(
        "--api-url",
        default=os.environ.get("BTC_READINESS_API_URL", ""),
        help="read the live process endpoint instead of opening its DuckDB files",
    )
    args = parser.parse_args()
    if args.selftest:
        return selftest()

    print("=" * 88)
    print("PROTOCOL B / C FORWARD READINESS - counts and coverage only, never performance")
    print("=" * 88)
    try:
        if args.api_url:
            with urllib.request.urlopen(args.api_url, timeout=5.0) as response:
                report = json.loads(response.read().decode("utf-8"))
            assert_performance_blind(report)
        else:
            report = build_report()
    except SourceUnreadable as exc:
        fallback = "http://127.0.0.1:8000/api/evidence-readiness"
        try:
            with urllib.request.urlopen(fallback, timeout=3.0) as response:
                report = json.loads(response.read().decode("utf-8"))
            assert_performance_blind(report)
            print(f"  live-safe source: {fallback} (direct DuckDB read unavailable)")
        except (OSError, urllib.error.URLError, json.JSONDecodeError, PerformanceLeak) as api_exc:
            print(f"  SOURCE UNREADABLE: {exc}")
            print(f"  LIVE API UNAVAILABLE: {api_exc}")
            print("  No status is emitted. 'I could not look' is not one of the four statuses.")
            return 1
    rec, ledger = report["recorder"], report["ledger"]
    daily = report.get("daily_collection") or {}
    requirements = report.get("requirements") or {}
    print(f"  recorder    : {'ALIVE' if rec['writer_active'] else 'NOT RUNNING'} "
          f"| {rec['capture_attempts']:,} capture attempts "
          f"({rec['captures_accepted']:,} accepted, {rec['captures_refused']:,} refused)")
    print(f"  ledger      : {ledger['decisions']:,} rows over {ledger['days']} days "
          f"| causal refusals {ledger['causal_refusals']}")
    print(f"                ENTER {ledger['enter_rows']:,}  WAIT {ledger['wait_rows']:,}  "
          f"UNAVAILABLE {ledger['unavailable_rows']:,}")
    print("  today       : "
          f"opportunities {int(daily.get('opportunity_rows_today', 0)):,} | "
          f"position snapshots {int(daily.get('position_snapshots_today', 0)):,} | "
          f"paired books {int(daily.get('paired_books_today', 0)):,} | "
          f"action arms {int(daily.get('action_arms_today', 0)):,} | "
          f"outcomes {int(daily.get('action_outcomes_today', 0)):,}")
    print("  coverage    : "
          f"paired books {float(daily.get('paired_book_coverage', 0.0)):.1%} | "
          f"five arms {float(daily.get('five_arm_coverage', 0.0)):.1%} | "
          f"missing official action sets "
          f"{int(daily.get('five_arm_snapshots_missing_official_outcomes', 0)):,}")
    print("  refusals    : "
          f"stale book {int(daily.get('stale_book_refusals', 0)):,} | "
          f"clock skew {int(daily.get('clock_skew_refusals', 0)):,}")

    for key, title, gate_field, unit in (
            ("B", "Protocol B - final crossing vs reversion",
             "final_crossing_labels_resolved", "resolved rounds"),
            ("C", "Protocol C - open-position action value",
             "full_resolved_five_arm_rounds", "five-arm rounds")):
        section = report[key]
        print()
        print(f"  {title}")
        if section["measurement"] == "NOT_WIRED":
            print("    measurement           : NOT_WIRED")
            print(f"    unmeasured fields     : {', '.join(section['unmeasured_fields'])}")
            print(f"    reason                : {section['unmeasured_reason']}")
            print("    status                : (none - an unmeasured gate has no status)")
            continue
        required_days = int(requirements.get("required_days", REQUIRED_DAYS))
        required_weeks = int(requirements.get("required_weeks", REQUIRED_WEEKS))
        required_rounds = int(requirements.get("required_resolved_rounds", REQUIRED_RESOLVED))
        print(f"    span                  : {section['calendar_days']}/{required_days} "
              f"qualifying days")
        print(f"    independent weeks     : {section['independent_weeks']}/{required_weeks}")
        print(f"    {unit:<22}: {section[gate_field]:,}/{required_rounds:,}")
        if key == "C":
            print(f"    (diagnostic) any-arm  : "
                  f"{section['any_arm_complete_snapshots']:,} snapshots - NOT the gate")
            print(f"    five-arm coverage     : {section['coverage_all_five_arms']:.1%}")
            print(f"    partial-fill coverage : {section['partial_fill_frequency']:.1%}")
        print(f"    causal violations     : "
              f"{section.get('causal_refusals', section.get('clock_skew_refusals', 0))}")
        print(f"    status                : {section['status']}")

    print()
    print("  Reaching DATA_GATE_COMPLETE_UNSCORED does NOT trigger scoring. The one-time")
    print("  scoring command is separate and deliberate, and a protocol may be scored once.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
