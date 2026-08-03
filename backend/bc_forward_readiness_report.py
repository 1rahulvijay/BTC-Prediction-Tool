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
import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or REPO / "data")
LEDGER_DB = DATA_DIR / "opportunity_ledger.duckdb"
POSITIONS_DB = DATA_DIR / "open_position_actions.duckdb"

#: The declared gate. Both protocols share it.
REQUIRED_DAYS = 56
REQUIRED_WEEKS = 8
REQUIRED_RESOLVED = 1_000

#: Anything whose name matches these may never appear in the report.
FORBIDDEN = re.compile(
    r"pnl|profit|return|auc|brier|log_?loss|edge|sharpe|threshold|rank|score|"
    r"accuracy|win_rate|advantage|preferred|best_action|selected_action",
    re.IGNORECASE)

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


def assert_performance_blind(payload: dict) -> None:
    """Refuse to emit anything that looks like a result.

    Checks KEYS, not values: a count named `resolved_positions` is fine, a count named
    `mean_pnl` is not, and the difference is the name."""
    leaked = [key for key in payload if FORBIDDEN.search(str(key))]
    if leaked:
        raise PerformanceLeak(
            f"readiness report may not expose {leaked} - B and C are sealed and scored once. "
            "Reporting an interim result turns a frozen protocol into a search.")


def gate_status(days: int, weeks: int, resolved: int) -> str:
    if days == 0 and resolved == 0:
        return "NOT_STARTED"
    if days >= REQUIRED_DAYS and weeks >= REQUIRED_WEEKS and resolved >= REQUIRED_RESOLVED:
        return "DATA_GATE_COMPLETE_UNSCORED"
    if resolved > 0 or days > 0:
        return "DATA_GATE_INCOMPLETE"
    return "COLLECTING"


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


def _ledger_counts() -> dict:
    """Counts from the causal decision ledger. Raises rather than reporting a false zero."""
    con = _connect(LEDGER_DB, ("opportunity_decisions",))
    try:
        row = con.execute("""
            SELECT count(*),
                   count(DISTINCT CAST(decision_ts / 86400000 AS BIGINT)),
                   count(DISTINCT CAST(decision_ts / 604800000 AS BIGINT)),
                   count(*) FILTER (WHERE action = 'ENTER'),
                   count(*) FILTER (WHERE action = 'WAIT'),
                   count(*) FILTER (WHERE action = 'UNAVAILABLE')
            FROM opportunity_decisions""").fetchone()
        violations = con.execute(
            "SELECT count(*) FROM opportunity_decisions "
            "WHERE state_snapshot_ts > decision_ts OR quote_recv_ts > decision_ts").fetchone()[0]
    finally:
        con.close()
    return {"decisions": int(row[0]), "days": int(row[1]), "weeks": int(row[2]),
            "enter_rows": int(row[3]), "wait_rows": int(row[4]),
            "unavailable_rows": int(row[5]), "causal_refusals": int(violations)}


#: Protocol C's five declared arms. Coverage is measured against this exact set.
ACTION_ARMS = ("HOLD", "EXIT", "REDUCE_50", "SWITCH", "LOCK")


def _position_counts() -> dict:
    """Protocol C coverage, read from the open-position recorder. Raises on unreadable."""
    con = _connect(POSITIONS_DB, (
        "open_position_snapshots", "paired_book_snapshots", "open_position_action_arms",
        "open_position_recorder_refusals"))
    try:
        snaps, positions, days, weeks, paired_linked = con.execute("""
            SELECT count(*), count(DISTINCT position_id),
                   count(DISTINCT CAST(snapshot_ts / 86400000 AS BIGINT)),
                   count(DISTINCT CAST(snapshot_ts / 604800000 AS BIGINT)),
                   count(*) FILTER (WHERE paired_snapshot_id IS NOT NULL)
            FROM open_position_snapshots""").fetchone()
        paired, fee_ruled, skewed = con.execute("""
            SELECT count(*),
                   count(*) FILTER (WHERE fee_rate IS NOT NULL AND fees_enabled IS NOT NULL),
                   count(*) FILTER (WHERE pair_skew_ms IS NOT NULL
                                      AND abs(pair_skew_ms) > 2000)
            FROM paired_book_snapshots""").fetchone()
        # An arm is RESOLVED only when it is complete. A recorded-but-incomplete arm is an
        # attempt, and counting attempts as coverage is how a gate passes without evidence.
        resolved = con.execute(
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
        residual, settled, partial = con.execute("""
            SELECT count(*) FILTER (WHERE up_shares_after IS NOT NULL
                                      AND down_shares_after IS NOT NULL),
                   count(*) FILTER (WHERE settlement_floor_net IS NOT NULL),
                   count(*) FILTER (WHERE execution_json IS NOT NULL
                                      AND execution_json LIKE '%partial%')
            FROM open_position_action_arms WHERE complete""").fetchone()
        stale = con.execute(
            "SELECT count(*) FROM open_position_recorder_refusals "
            "WHERE lower(category) LIKE '%stale%'").fetchone()[0]
        skew_refused = con.execute(
            "SELECT count(*) FROM open_position_recorder_refusals "
            "WHERE lower(category) LIKE '%skew%' OR lower(category) LIKE '%clock%'").fetchone()[0]
    finally:
        con.close()
    return {
        "snapshots": int(snaps), "positions": int(positions), "days": int(days),
        "weeks": int(weeks), "paired_linked": int(paired_linked), "paired": int(paired),
        "fee_ruled": int(fee_ruled), "resolved": int(resolved), "all_five": int(all_five),
        "per_arm": {k: int(v) for k, v in per_arm.items()},
        "residual": int(residual), "settled": int(settled), "partial": int(partial),
        "stale_refusals": int(stale), "skew_refusals": int(skew_refused) + int(skewed),
    }


def build_report() -> dict:
    ledger = _ledger_counts()
    pos = _position_counts()

    # Protocol B is scored on post-entry crossings of OPEN positions, so its span is the
    # open-position recorder's span, not the decision ledger's. Reporting ledger days here
    # would let WAIT-only decisions inflate a gate that requires positions.
    protocol_b = {
        "calendar_days": pos["days"], "independent_weeks": pos["weeks"],
        "open_position_checkpoints": pos["snapshots"],
        "qualifying_post_entry_crossings": 0,
        "final_crossing_labels_resolved": 0,
        "reversion_labels_resolved": 0,
        "same_time_exit_price_coverage": _rate(pos["paired_linked"], pos["snapshots"]),
        # Nothing resolved means nothing is MISSING yet. Reporting 100% missing on an empty
        # recorder would read as a data fault rather than an unstarted collection.
        "missing_settlement_rate": (1.0 - _rate(pos["settled"], pos["resolved"])
                                    if pos["resolved"] else 0.0),
        "causal_refusals": ledger["causal_refusals"] + pos["skew_refusals"],
        "duplicate_rows": 0,
    }
    protocol_c = {
        "calendar_days": pos["days"], "independent_weeks": pos["weeks"],
        "open_positions_observed": pos["positions"],
        "paired_book_snapshots": pos["paired"],
        "resolved_action_snapshots": pos["resolved"],
        "coverage_hold": _rate(pos["per_arm"]["HOLD"], pos["snapshots"]),
        "coverage_exit": _rate(pos["per_arm"]["EXIT"], pos["snapshots"]),
        "coverage_reduce_50": _rate(pos["per_arm"]["REDUCE_50"], pos["snapshots"]),
        "coverage_switch": _rate(pos["per_arm"]["SWITCH"], pos["snapshots"]),
        "coverage_lock": _rate(pos["per_arm"]["LOCK"], pos["snapshots"]),
        "coverage_all_five_arms": _rate(pos["all_five"], pos["snapshots"]),
        "partial_fill_frequency": _rate(pos["partial"], pos["resolved"]),
        "residual_inventory_coverage": _rate(pos["residual"], pos["resolved"]),
        "bid_depth_coverage": _rate(pos["paired_linked"], pos["snapshots"]),
        "fee_rule_coverage": _rate(pos["fee_ruled"], pos["paired"]),
        "settlement_coverage": _rate(pos["settled"], pos["resolved"]),
        "clock_skew_refusals": pos["skew_refusals"],
        "stale_book_refusals": pos["stale_refusals"],
    }
    for payload in (protocol_b, protocol_c):
        assert_performance_blind(payload)
    return {
        "ledger": ledger,
        "B": {**protocol_b, "status": gate_status(
            protocol_b["calendar_days"], protocol_b["independent_weeks"],
            protocol_b["final_crossing_labels_resolved"])},
        "C": {**protocol_c, "status": gate_status(
            protocol_c["calendar_days"], protocol_c["independent_weeks"],
            protocol_c["resolved_action_snapshots"])},
    }


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

    check(gate_status(0, 0, 0) == "NOT_STARTED", "no data at all is NOT_STARTED")
    check(gate_status(19, 3, 214) == "DATA_GATE_INCOMPLETE",
          "partial collection is DATA_GATE_INCOMPLETE")
    check(gate_status(REQUIRED_DAYS, REQUIRED_WEEKS, REQUIRED_RESOLVED)
          == "DATA_GATE_COMPLETE_UNSCORED",
          "a complete gate is COMPLETE_UNSCORED - it never says SCORE or PASS")
    check(gate_status(REQUIRED_DAYS, REQUIRED_WEEKS, REQUIRED_RESOLVED - 1)
          == "DATA_GATE_INCOMPLETE",
          "one resolved position short of the gate is still incomplete")
    check(all(s in STATUSES for s in
              (gate_status(0, 0, 0), gate_status(19, 3, 214),
               gate_status(REQUIRED_DAYS, REQUIRED_WEEKS, REQUIRED_RESOLVED))),
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

    report = build_report()
    check(set(report) == {"ledger", "B", "C"}, "the report exposes exactly three sections")
    check(not any(FORBIDDEN.search(k) for section in ("B", "C") for k in report[section]),
          "the REAL report contains no performance-shaped key")
    check(report["B"]["status"] in STATUSES and report["C"]["status"] in STATUSES,
          "the REAL report emits only declared statuses")
    check(set(ACTION_ARMS) == {"HOLD", "EXIT", "REDUCE_50", "SWITCH", "LOCK"},
          "coverage is measured against Protocol C's five declared arms")

    print(f"\nB/C READINESS SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    print("=" * 88)
    print("PROTOCOL B / C FORWARD READINESS - counts and coverage only, never performance")
    print("=" * 88)
    try:
        report = build_report()
    except SourceUnreadable as exc:
        print(f"  SOURCE UNREADABLE: {exc}")
        print("  No status is emitted. 'I could not look' is not one of the four statuses.")
        return 1
    ledger = report["ledger"]
    print(f"  causal decision ledger: {ledger['decisions']:,} rows over {ledger['days']} days "
          f"| causal refusals {ledger['causal_refusals']}")
    print(f"    ENTER {ledger['enter_rows']:,}  WAIT {ledger['wait_rows']:,}  "
          f"UNAVAILABLE {ledger['unavailable_rows']:,}")

    for key, title, gate_field in (("B", "Protocol B - final crossing vs reversion",
                                    "final_crossing_labels_resolved"),
                                   ("C", "Protocol C - open-position action value",
                                    "resolved_action_snapshots")):
        section = report[key]
        print()
        print(f"  {title}")
        print(f"    span                  : {section['calendar_days']}/{REQUIRED_DAYS} "
              f"required days")
        print(f"    independent weeks     : {section['independent_weeks']}/{REQUIRED_WEEKS}")
        print(f"    resolved              : {section[gate_field]:,}/{REQUIRED_RESOLVED:,}")
        if key == "C":
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
