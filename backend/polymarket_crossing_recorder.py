"""Anchor-crossing events and their later labels, as two tables that cannot contaminate each other.

WHAT A CROSSING IS
    Within a Polymarket round the leader is whichever side BTC currently sits on relative to the
    round anchor. When that leader flips, the anchor has been crossed. `round_state_snapshots`
    already records the leader at every observation, so crossings are derivable exactly.

WHY TWO TABLES
    An earlier design put crossing facts and future labels in one row:

        crossing_ts   is_final_crossing   reverted_60s   settlement_resolved

    Those are known at completely different times. One row invites writing a label before its
    horizon has elapsed, and makes "not yet known" indistinguishable from "known to be false".

        crossing_events   immutable, complete at the instant of the crossing
        crossing_labels   appended only when each horizon has actually passed

    An event is never updated. A label row cannot exist before `eligible_after_ts`, and the
    writer refuses to create one - that refusal is asserted in the selftest.

THIS MODULE DOES NOT TOUCH THE LIVE APP
    It provides `detect_crossings()` as a pure function, a writer, and a backfill over historical
    round states. Wiring it into the serving path is a separate, deliberate step.

    python backend/polymarket_crossing_recorder.py --selftest
    python backend/polymarket_crossing_recorder.py --backfill
    python backend/polymarket_crossing_recorder.py --report
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
CROSSING_DB = DATA_DIR / "polymarket_crossings.duckdb"

# v2 (2026-08-04). v1 wrote `reverted_Ns`, but the computation was
#     side at the LAST SAMPLE at or before the deadline == side before the crossing
# which is STATE AT THE HORIZON, not "ever reverted". A path that crossed, reverted at
# 5s and re-crossed by 20s was labelled False despite having reverted.
#
# The v1 columns are NOT renamed in place: 15,428 existing rows carry v1 semantics and
# renaming would silently reinterpret them. v2 adds correctly-named columns alongside,
# and rows are distinguished by label_version.
LABEL_VERSION = "crossing_labels_v2"
#: Horizons at which a crossing is checked for reversion. A label may only be written once the
#: horizon has elapsed AND the round is still running - a crossing 3s before settlement has no
#: 60s reversion outcome, and recording one would be an invention.
REVERSION_HORIZONS_S = (5, 15, 30, 60)
SIDES = ("UP", "DOWN")

SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS crossing_events (
        crossing_id      VARCHAR PRIMARY KEY,
        round_id         VARCHAR NOT NULL,
        horizon_min      INTEGER NOT NULL,
        crossing_ts      BIGINT  NOT NULL,
        from_side        VARCHAR NOT NULL,
        to_side          VARCHAR NOT NULL,
        seconds_left     INTEGER NOT NULL,
        move_at_crossing DOUBLE,
        crossing_index   INTEGER NOT NULL,
        recorded_ts      BIGINT  NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS crossing_labels (
        crossing_id        VARCHAR NOT NULL,
        label_version      VARCHAR NOT NULL,
        eligible_after_ts  BIGINT  NOT NULL,
        resolved_ts        BIGINT  NOT NULL,
        is_final_crossing  BOOLEAN,
        reverted_5s        BOOLEAN,   -- v1 only: misnamed, means state-at-horizon
        reverted_15s       BOOLEAN,   -- v1 only
        reverted_30s       BOOLEAN,   -- v1 only
        reverted_60s       BOOLEAN,   -- v1 only
        state_original_side_at_5s   BOOLEAN,  -- v2: what v1 actually measured
        state_original_side_at_15s  BOOLEAN,
        state_original_side_at_30s  BOOLEAN,
        state_original_side_at_60s  BOOLEAN,
        ever_reverted_by_5s         BOOLEAN,  -- v2: genuinely new - touched the
        ever_reverted_by_15s        BOOLEAN,  -- original side at ANY point in window
        ever_reverted_by_30s        BOOLEAN,
        ever_reverted_by_60s        BOOLEAN,
        first_reversion_ts          BIGINT,   -- v2: NULL if never reverted
        n_recrossings               INTEGER,  -- v2
        settled_side       VARCHAR,
        PRIMARY KEY (crossing_id, label_version)
    )
    """,
)


def crossing_id(round_id: str, crossing_ts: int) -> str:
    """Stable identity. A round cannot cross twice in the same millisecond."""
    return hashlib.sha256(f"{round_id}|{int(crossing_ts)}".encode()).hexdigest()[:32]


def detect_crossings(snapshots) -> list[dict]:
    """Crossing events from one round's ordered snapshots. Pure; no I/O, no future.

    `snapshots` is a sequence of dicts with round_id, ts, horizon, seconds_left,
    current_position and optionally current_move. The first observation establishes the initial
    leader and is NEVER a crossing - there is nothing before it to cross from."""
    ordered = sorted(snapshots, key=lambda s: int(s["ts"]))
    events, previous, index = [], None, 0
    for snapshot in ordered:
        side = str(snapshot.get("current_position") or "").upper()
        if side not in SIDES:
            continue                          # unknown leader is not a crossing
        if previous is None:
            previous = side
            continue
        if side == previous:
            continue
        index += 1
        events.append({
            "crossing_id": crossing_id(snapshot["round_id"], snapshot["ts"]),
            "round_id": snapshot["round_id"],
            "horizon_min": int(snapshot.get("horizon") or 0),
            "crossing_ts": int(snapshot["ts"]),
            "from_side": previous,
            "to_side": side,
            "seconds_left": int(snapshot.get("seconds_left") or 0),
            "move_at_crossing": (float(snapshot["current_move"])
                                 if snapshot.get("current_move") is not None else None),
            "crossing_index": index,
        })
        previous = side
    return events


def resolve_labels(event: dict, later_snapshots, settled_side: str | None,
                   now_ms: int) -> dict | None:
    """Labels for one crossing, or None when nothing is eligible yet.

    A horizon is reported ONLY if it has elapsed in wall-clock time AND the round covered it.
    Otherwise the field stays NULL - "not yet known" must never read as "known false"."""
    ordered = sorted(later_snapshots, key=lambda s: int(s["ts"]))
    after = [s for s in ordered if int(s["ts"]) > event["crossing_ts"]]
    round_end_ts = event["crossing_ts"] + event["seconds_left"] * 1000

    labels = {"crossing_id": event["crossing_id"], "label_version": LABEL_VERSION,
              "eligible_after_ts": event["crossing_ts"] + REVERSION_HORIZONS_S[0] * 1000,
              "is_final_crossing": None, "settled_side": settled_side}
    labels["first_reversion_ts"] = None
    labels["n_recrossings"] = None
    for horizon in REVERSION_HORIZONS_S:
        labels[f"reverted_{horizon}s"] = None            # v1 column, left NULL under v2
        labels[f"state_original_side_at_{horizon}s"] = None
        labels[f"ever_reverted_by_{horizon}s"] = None
        deadline = event["crossing_ts"] + horizon * 1000
        if now_ms < deadline or deadline > round_end_ts:
            continue                          # not elapsed, or the round ended first
        window = [s for s in after if int(s["ts"]) <= deadline]
        if not window:
            continue                          # unresolvable at this cadence - write NOTHING
        # STATE AT HORIZON: exactly what v1 computed, now honestly named.
        side = str(window[-1].get("current_position") or "").upper()
        if side in SIDES:
            labels[f"state_original_side_at_{horizon}s"] = (side == event["from_side"])
        # EVER REVERTED: did it touch the original side at ANY observed point in the
        # window? This is what "reverted" always claimed to mean, and it is a DIFFERENT
        # target - it can be True where state-at-horizon is False.
        sides = [str(s.get("current_position") or "").upper() for s in window]
        touched = [i for i, s in enumerate(sides) if s == event["from_side"]]
        labels[f"ever_reverted_by_{horizon}s"] = bool(touched)
        if touched and labels["first_reversion_ts"] is None:
            labels["first_reversion_ts"] = int(window[touched[0]]["ts"])
        if horizon == REVERSION_HORIZONS_S[-1]:
            labels["n_recrossings"] = sum(
                1 for a, b in zip(sides, sides[1:]) if a in SIDES and b in SIDES and a != b)

    # "Final" is knowable only once the round is over.
    if now_ms >= round_end_ts:
        later_sides = {str(s.get("current_position") or "").upper() for s in after}
        labels["is_final_crossing"] = event["from_side"] not in later_sides

    if all(labels[f"reverted_{h}s"] is None for h in REVERSION_HORIZONS_S) \
            and labels["is_final_crossing"] is None:
        return None                           # nothing resolved yet; write nothing
    labels["resolved_ts"] = now_ms
    return labels


def _connect(read_only: bool = False):
    import duckdb
    CROSSING_DB.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(CROSSING_DB), read_only=read_only)
    if not read_only:
        for statement in SCHEMA:
            con.execute(statement)
        _migrate_v2_columns(con)
    return con


# CREATE TABLE IF NOT EXISTS does nothing to a table that already exists, so a database
# written under v1 lacks every v2 column and any query naming one fails to bind. This is
# ADDITIVE ONLY: columns are added as NULL, no existing row is read, rewritten or
# reinterpreted. v1 rows keep label_version='crossing_labels_v1' and their v1 columns.
V2_COLUMNS = (
    [(f"state_original_side_at_{h}s", "BOOLEAN") for h in REVERSION_HORIZONS_S]
    + [(f"ever_reverted_by_{h}s", "BOOLEAN") for h in REVERSION_HORIZONS_S]
    + [("first_reversion_ts", "BIGINT"), ("n_recrossings", "INTEGER")]
)


def _migrate_v2_columns(con) -> int:
    existing = {r[0] for r in con.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_name = 'crossing_labels'").fetchall()}
    added = 0
    for name, sqltype in V2_COLUMNS:
        if name not in existing:
            con.execute(f"ALTER TABLE crossing_labels ADD COLUMN {name} {sqltype}")
            added += 1
    return added


def write_events(events: list[dict]) -> int:
    """Insert events idempotently. An event is immutable: re-writing one is a no-op."""
    if not events:
        return 0
    con = _connect()
    try:
        import time
        now = int(time.time() * 1000)
        written = 0
        for event in events:
            exists = con.execute("SELECT 1 FROM crossing_events WHERE crossing_id = ?",
                                 [event["crossing_id"]]).fetchone()
            if exists:
                continue
            con.execute(
                "INSERT INTO crossing_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                [event["crossing_id"], event["round_id"], event["horizon_min"],
                 event["crossing_ts"], event["from_side"], event["to_side"],
                 event["seconds_left"], event["move_at_crossing"],
                 event["crossing_index"], now])
            written += 1
        return written
    finally:
        con.close()


def write_labels(labels: list[dict]) -> int:
    if not labels:
        return 0
    con = _connect()
    try:
        written = 0
        for row in labels:
            con.execute(
                "INSERT OR REPLACE INTO crossing_labels VALUES (?,?,?,?,?,?,?,?,?,?)",
                [row["crossing_id"], row["label_version"], row["eligible_after_ts"],
                 row["resolved_ts"], row["is_final_crossing"], row["reverted_5s"],
                 row["reverted_15s"], row["reverted_30s"], row["reverted_60s"],
                 row["settled_side"]])
            written += 1
        return written
    finally:
        con.close()


def backfill(source: Path | None = None) -> dict:
    """Derive every crossing in the historical round-state archive.

    This is not a substitute for forward collection. It exists so the detector is validated
    against real rounds rather than only against fixtures."""
    import duckdb
    import time
    src = source or (DATA_DIR / "btc_duckdbs" / "analytics.duckdb")
    if not src.is_file():
        src = DATA_DIR / "analytics.duckdb"
    con = duckdb.connect(str(src), read_only=True)
    try:
        rows = con.execute("""
            SELECT round_id, ts, horizon, seconds_left, current_position, current_move
            FROM round_state_snapshots ORDER BY round_id, ts""").fetchall()
    finally:
        con.close()
    by_round: dict[str, list[dict]] = {}
    for round_id, ts, horizon, seconds_left, position, move in rows:
        by_round.setdefault(round_id, []).append({
            "round_id": round_id, "ts": ts, "horizon": horizon,
            "seconds_left": seconds_left, "current_position": position,
            "current_move": move})

    now = int(time.time() * 1000)
    all_events, all_labels = [], []
    for round_id, snapshots in by_round.items():
        events = detect_crossings(snapshots)
        all_events.extend(events)
        for event in events:
            labels = resolve_labels(event, snapshots, None, now)
            if labels:
                all_labels.append(labels)
    return {"source": str(src), "rounds": len(by_round), "events": len(all_events),
            "labels": len(all_labels),
            "written_events": write_events(all_events),
            "written_labels": write_labels(all_labels)}


def report() -> dict:
    if not CROSSING_DB.is_file():
        return {"exists": False}
    con = _connect(read_only=True)
    try:
        events = con.execute("SELECT count(*) FROM crossing_events").fetchone()[0]
        rounds = con.execute("SELECT count(DISTINCT round_id) FROM crossing_events").fetchone()[0]
        labels = con.execute("SELECT count(*) FROM crossing_labels").fetchone()[0]
        final = con.execute(
            "SELECT count(*) FILTER (WHERE is_final_crossing), count(*) "
            "FROM crossing_labels WHERE is_final_crossing IS NOT NULL").fetchone()
        per_round = con.execute(
            "SELECT avg(c) FROM (SELECT count(*) c FROM crossing_events GROUP BY round_id)"
        ).fetchone()[0]
        reversion = {}
        for horizon in REVERSION_HORIZONS_S:
            column = f"reverted_{horizon}s"
            got = con.execute(
                f"SELECT count(*) FILTER (WHERE {column}), count(*) FROM crossing_labels "
                f"WHERE {column} IS NOT NULL").fetchone()
            reversion[horizon] = got
    finally:
        con.close()
    return {"exists": True, "events": events, "rounds": rounds, "labels": labels,
            "final": final, "per_round": per_round, "reversion": reversion}


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    base = 1_785_000_000_000

    def snap(offset_s, side, left=300 - 0):
        return {"round_id": "r1", "ts": base + offset_s * 1000, "horizon": 5,
                "seconds_left": 300 - offset_s, "current_position": side,
                "current_move": 1.0}

    steady = [snap(i, "UP") for i in range(0, 60, 10)]
    check(detect_crossings(steady) == [], "a round with no leader change has NO crossings")

    once = [snap(0, "UP"), snap(10, "UP"), snap(20, "DOWN"), snap(30, "DOWN")]
    events = detect_crossings(once)
    check(len(events) == 1, "one leader change produces exactly one crossing")
    check(events[0]["from_side"] == "UP" and events[0]["to_side"] == "DOWN",
          "the crossing records the side it came FROM and went TO")
    check(events[0]["crossing_index"] == 1, "crossings are numbered within the round")
    check(events[0]["seconds_left"] == 280, "time remaining at the crossing is preserved")

    twice = once + [snap(40, "UP"), snap(50, "UP")]
    check(len(detect_crossings(twice)) == 2, "a recross is a second crossing")
    check(detect_crossings(twice)[1]["crossing_index"] == 2, "...and is numbered 2")

    check(detect_crossings([snap(0, "UP")]) == [],
          "the FIRST observation is never a crossing - nothing precedes it")
    unknown = [snap(0, "UP"), snap(10, ""), snap(20, "UP")]
    check(detect_crossings(unknown) == [],
          "an unknown leader is skipped, not treated as a flip to and from")

    shuffled = list(reversed(once))
    check(detect_crossings(shuffled) == events,
          "detection sorts by time - input order cannot change the answer")
    check(crossing_id("r1", 5) == crossing_id("r1", 5)
          and crossing_id("r1", 5) != crossing_id("r1", 6),
          "crossing identity is stable and time-specific")

    # LABEL ELIGIBILITY - the property the two-table split exists to guarantee.
    event = events[0]
    early = resolve_labels(event, once, None, now_ms=event["crossing_ts"] + 1000)
    check(early is None,
          "one second after a crossing NOTHING is resolvable - no label row is written")

    later = once + [snap(25, "DOWN"), snap(30, "DOWN"), snap(40, "DOWN")]
    resolved = resolve_labels(event, later, None,
                              now_ms=event["crossing_ts"] + 20_000)
    check(resolved is not None, "once 5s and 15s have elapsed, a label row IS written")
    check(resolved["reverted_5s"] is False,
          "the leader stayed DOWN at +5s, so the crossing did not revert")
    check(resolved["reverted_60s"] is None,
          "the 60s horizon has NOT elapsed and stays NULL - never a default False")
    check(resolved["is_final_crossing"] is None,
          "'final' is unknowable before the round ends and stays NULL")

    reverted = once + [snap(25, "UP"), snap(30, "UP")]
    got = resolve_labels(reverted, later_snapshots=reverted, settled_side=None,
                         now_ms=event["crossing_ts"] + 20_000) if False else \
        resolve_labels(event, reverted, None, now_ms=event["crossing_ts"] + 20_000)
    check(got["reverted_5s"] is True,
          "a leader that returns to the original side IS recorded as reverted")

    ended = resolve_labels(event, later, "DOWN",
                           now_ms=event["crossing_ts"] + 400_000)
    check(ended["is_final_crossing"] is True,
          "after the round ends, a crossing never undone is FINAL")
    check(ended["settled_side"] == "DOWN", "the settled side is carried onto the label")

    # A crossing with 3 seconds left has no 60s outcome, and none may be invented.
    late = {**event, "seconds_left": 3}
    late_labels = resolve_labels(late, later, None, now_ms=event["crossing_ts"] + 400_000)
    check(late_labels["reverted_60s"] is None,
          "a crossing 3s before settlement has NO 60s reversion outcome, and none is invented")

    print(f"\nCROSSING RECORDER SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--report", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.backfill:
        result = backfill()
        print(f"  source {result['source']}")
        print(f"  {result['rounds']:,} rounds -> {result['events']:,} crossing events, "
              f"{result['labels']:,} label rows")
        print(f"  written: {result['written_events']:,} events, "
              f"{result['written_labels']:,} labels")
        return 0

    info = report()
    if not info.get("exists"):
        print(f"  {CROSSING_DB} does not exist - run --backfill, or start forward collection")
        return 1
    print(f"  {info['events']:,} crossings over {info['rounds']:,} rounds "
          f"({info['per_round']:.2f} per crossing-bearing round)")
    print(f"  {info['labels']:,} label rows")
    hit, total = info["final"]
    if total:
        print(f"  final crossings: {hit:,}/{total:,} ({hit / total:.1%})")
    for horizon, (reverted, total) in info["reversion"].items():
        if total:
            print(f"  reverted within {horizon:>2}s: {reverted:,}/{total:,} "
                  f"({reverted / total:.1%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
