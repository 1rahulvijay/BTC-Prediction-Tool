"""Row-progress health for every recorder: ADVANCING / STALLED / NEVER_RAN, from the DATA.

WHY THIS EXISTS ALONGSIDE recorder_evidence_check
    That audit answers "was this recorder ever wired, and does its store hold rows?" using file
    mtime and log-file existence. Both are proxies, and both are wrong here:

      - multi_venue_recorder reports ever_ran=False while holding 20,085,631 rows, because no
        log file matched its name;
      - a store's mtime moves for reasons unrelated to a recorder writing rows.

    This module normally asks the strongest question: what is the newest TIMESTAMP IN THE DATA,
    and how old is it? On Windows, DuckDB may deny a second process while the writer holds its
    lock. In that case the explicitly labelled fallback tracks byte/mtime progress of that
    recorder's isolated DB + WAL; a held lock without continuing writes becomes STALLED.

THE UNIT TRAP, WHICH IS REAL HERE
    Four recorders use THREE different time units:

        pm_round_snapshots.ts        SECONDS
        venue_events.recv_ts         SECONDS
        l2_snapshots.ts_ms           MILLISECONDS
        pm_l2_book_levels.recv_ts_ns NANOSECONDS

    A single hardcoded divisor is wrong for three of the four. Treating seconds as milliseconds
    is what previously sent 56,467 rows to 1970. So the unit is DECLARED per recorder, and then
    VERIFIED: if the declared unit yields a date outside a plausible window the module refuses
    rather than reporting a confident wrong age.

    python backend/recorder_health.py
    python backend/recorder_health.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

REPO = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or REPO / "data")

#: MULTIPLIER to milliseconds. Written the wrong way round first (seconds dividing by
#: 1000 instead of multiplying) and the selftest caught it on the first line - which is
#: the argument for asserting conversions rather than eyeballing them.
UNITS = {"SECONDS": 1e3, "MILLIS": 1.0, "MICROS": 1e-3, "NANOS": 1e-6}

#: recorder -> (store, table, timestamp column, declared unit).
#: The store/table half is kept identical to audit/recorder_evidence_check.EXPECTED_STORE; the
#: column and unit are new, because row progress needs them and mtime does not.
RECORDER_CLOCKS = {
    "live_btc_updown_recorder.py": ("execution_layer.duckdb", "pm_round_snapshots",
                                    "ts", "SECONDS"),
    "l2_recorder.py": ("polymarket_l2.duckdb", "pm_l2_book_levels",
                       "recv_ts_ns", "NANOS"),
    "microstructure_recorder.py": ("microstructure.duckdb", "l2_snapshots",
                                   "ts_ms", "MILLIS"),
    "multi_venue_recorder.py": ("multi_venue.duckdb", "venue_events",
                                "recv_ts", "SECONDS"),
    "binance_l2_recorder.py": ("binance_l2.duckdb", "l2_diffs", "ts_ms", "MILLIS"),
    "btc_tick_recorder.py": ("btc_ticks.duckdb", "btc_tick_heartbeats",
                             "recv_ts_ns", "NANOS"),
    "crossing_recorder_hf.py": ("polymarket_crossings_hf.duckdb", "hf_heartbeats",
                                "beat_ts", "MILLIS"),
    "cross_window_recorder.py": ("cross_window.duckdb", "cross_window_heartbeats",
                                 "beat_ts_ms", "MILLIS"),
    "deribit_option_chain_recorder.py": ("deribit_options.duckdb",
                                         "deribit_chain_batches",
                                         "response_receive_ns", "NANOS"),
    "funding_recorder.py": ("funding.duckdb", "funding_heartbeats",
                            "recv_ts_ns", "NANOS"),
}

_LOCKED_STORE_PROGRESS: dict[str, dict] = {}

#: A recorder writing continuously should never be quiet this long.
STALL_AFTER_MS = 15 * 60_000
#: Any converted timestamp must land here or the declared unit is wrong.
PLAUSIBLE_MS = (1_577_836_800_000, 1_893_456_000_000)      # 2020-01-01 .. 2030-01-01


def to_ms(value: float, unit: str) -> float:
    return float(value) * UNITS[unit]


def unit_is_plausible(value: float, unit: str) -> bool:
    """A declared unit that yields a date outside 2020-2030 is wrong, and saying so beats
    reporting an age of fifty-six years with a straight face."""
    try:
        ms = to_ms(value, unit)
    except Exception:
        return False
    return PLAUSIBLE_MS[0] <= ms <= PLAUSIBLE_MS[1]


def classify(newest_ms: float | None, now_ms: float, rows: int) -> str:
    if not rows or newest_ms is None:
        return "NEVER_RAN"
    return "ADVANCING" if (now_ms - newest_ms) <= STALL_AFTER_MS else "STALLED"


def _locked_store_progress(path: Path, now_ms: float) -> dict:
    """Progress fallback for an isolated DuckDB whose active writer denies read-only opens."""
    components = [candidate for candidate in (path, Path(f"{path}.wal")) if candidate.exists()]
    if not components:
        return {"status": "LOCKED_BY_WRITER", "age_ms": None,
                "detail": "writer lock observed but DB/WAL files are unavailable"}
    signature = tuple(
        (str(candidate), candidate.stat().st_size, candidate.stat().st_mtime_ns)
        for candidate in components
    )
    key = str(path.resolve())
    previous = _LOCKED_STORE_PROGRESS.get(key)
    newest_file_ms = max(item[2] for item in signature) / 1_000_000.0
    if previous is None:
        last_progress_ms = newest_file_ms
    elif previous["signature"] != signature:
        last_progress_ms = now_ms
    else:
        last_progress_ms = float(previous["last_progress_ms"])
    _LOCKED_STORE_PROGRESS[key] = {
        "signature": signature,
        "last_progress_ms": last_progress_ms,
    }
    age_ms = max(0.0, now_ms - last_progress_ms)
    return {
        "status": "ADVANCING" if age_ms <= STALL_AFTER_MS else "STALLED",
        "age_ms": age_ms,
        "newest_ms": last_progress_ms,
        "newest_utc": datetime.fromtimestamp(
            last_progress_ms / 1000.0, timezone.utc
        ).isoformat()[:19],
        "method": "locked_writer_db_wal_progress",
        "detail": "DuckDB writer lock; freshness measured from isolated DB/WAL progress",
    }


def _resolve(store: str) -> Path | None:
    for candidate in (DATA_DIR / store, DATA_DIR / "btc_duckdbs" / store):
        if candidate.is_file():
            return candidate
    return None


def probe(recorder: str, now_ms: float | None = None) -> dict:
    store, table, column, unit = RECORDER_CLOCKS[recorder]
    now_ms = now_ms if now_ms is not None else datetime.now(timezone.utc).timestamp() * 1000
    path = _resolve(store)
    # `rows` starts as None, not 0. A count of zero is a MEASUREMENT ("the table is empty");
    # every path that returns before the count runs - absent store, writer lock, schema drift -
    # knows nothing about it, and reporting 0 there states a fact never established. The locked
    # path is the one that bites: a healthy recorder mid-write returned ADVANCING with rows=0.
    out = {"recorder": recorder, "store": store, "table": table, "column": column,
           "unit": unit, "rows": None, "rows_known": False, "newest_ms": None,
           "status": "NEVER_RAN"}
    if path is None:
        out["detail"] = f"{store} does not exist"
        return out
    out["path"] = str(path.relative_to(DATA_DIR))
    try:
        import duckdb
        con = duckdb.connect(str(path), read_only=True)
    except Exception as exc:
        # A lock proves a writer exists, but not that it is still advancing. Track the isolated
        # DB/WAL across probes so a hung writer becomes STALLED instead of looking healthy.
        detail = str(exc)
        locked = "used by another process" in detail or "lock" in detail.lower()
        if locked:
            out.update(_locked_store_progress(path, now_ms))
        else:
            out["status"] = "UNREADABLE"
            out["detail"] = detail[:120]
        return out
    try:
        names = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='main'").fetchall()}
        if table not in names:
            # A DATABASE THAT EXISTS WITHOUT ITS TABLE IS SCHEMA DRIFT, NOT "NEVER RAN".
            # `status` was left at its NEVER_RAN initial value here while the very next branch
            # correctly calls a missing COLUMN drift - so the more severe fault reported the
            # milder diagnosis. Live example: cross_window_recorder shows "table
            # cross_window_heartbeats absent" against a database that is present on disk, which
            # sends an operator looking for a process that never started instead of a schema
            # that no longer matches.
            out["status"] = "SCHEMA_DRIFT"
            out["detail"] = f"table {table} absent"
            return out
        columns = {c[0] for c in con.execute(f'DESCRIBE "{table}"').fetchall()}
        if column not in columns:
            out["status"] = "SCHEMA_DRIFT"
            out["detail"] = f"column {column} absent from {table}"
            return out
        rows, newest = con.execute(
            f'SELECT count(*), max("{column}") FROM "{table}"').fetchone()
    finally:
        con.close()
    out["rows"] = int(rows or 0)       # counted for real, so now it is known
    out["rows_known"] = True
    if newest is None:
        out["detail"] = "no timestamped rows"
        return out
    if not unit_is_plausible(newest, unit):
        out["status"] = "UNIT_MISMATCH"
        out["detail"] = (f"declared {unit} puts newest row outside 2020-2030 - the unit is "
                         f"wrong and every age computed from it would be too")
        return out
    out["newest_ms"] = to_ms(newest, unit)
    out["age_ms"] = now_ms - out["newest_ms"]
    out["status"] = classify(out["newest_ms"], now_ms, out["rows"])
    out["newest_utc"] = datetime.fromtimestamp(out["newest_ms"] / 1000,
                                               timezone.utc).isoformat()[:19]
    return out


def report(now_ms: float | None = None) -> list[dict]:
    return [probe(name, now_ms) for name in sorted(RECORDER_CLOCKS)]


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    check(to_ms(1_783_162_007.8, "SECONDS") == 1_783_162_007_800.0,
          "seconds convert to milliseconds")
    check(to_ms(1_783_157_137_199, "MILLIS") == 1_783_157_137_199,
          "milliseconds pass through unchanged")
    check(abs(to_ms(1_783_157_137_199_000_000, "NANOS") - 1_783_157_137_199) < 1,
          "nanoseconds convert to milliseconds")

    # THE UNIT TRAP. A seconds timestamp read as milliseconds lands in 1970.
    seconds_value = 1_783_162_007.8
    check(unit_is_plausible(seconds_value, "SECONDS"),
          "the CORRECT unit yields a plausible date")
    check(not unit_is_plausible(seconds_value, "MILLIS"),
          "the SAME value read as MILLIS is refused - this is the 1970 defect, caught")
    check(not unit_is_plausible(1_783_157_137_199, "SECONDS"),
          "and a millis value read as SECONDS is refused too - the check works both ways")

    check(len(set(u for _, _, _, u in RECORDER_CLOCKS.values())) >= 3,
          "the registry really does span three or more different units")

    now = 1_785_000_000_000
    check(classify(now - 1000, now, 10) == "ADVANCING", "a fresh row is ADVANCING")
    check(classify(now - STALL_AFTER_MS - 1, now, 10) == "STALLED",
          "a row older than the stall threshold is STALLED")
    check(classify(None, now, 0) == "NEVER_RAN", "no rows at all is NEVER_RAN")
    check(classify(now, now, 0) == "NEVER_RAN",
          "zero rows is NEVER_RAN even if a timestamp somehow exists")

    import tempfile
    import time
    with tempfile.TemporaryDirectory(prefix="recorder_health_") as tmp:
        locked = Path(tmp) / "locked.duckdb"
        locked.write_bytes(b"first")
        base_now = time.time() * 1000.0
        first = _locked_store_progress(locked, base_now)
        stale = _locked_store_progress(locked, base_now + STALL_AFTER_MS + 1)
        locked.write_bytes(b"second-write")
        resumed = _locked_store_progress(locked, base_now + STALL_AFTER_MS + 2)
        check(first["status"] == "ADVANCING",
              "a freshly changing locked DB is ADVANCING, not blindly trusted from its lock")
        check(stale["status"] == "STALLED",
              "a held writer lock without DB/WAL progress eventually becomes STALLED")
        check(resumed["status"] == "ADVANCING",
              "new DB/WAL progress restores health after a stall")
        check(all("rows" not in p for p in (first, stale, resumed)),
              "and the locked-store fallback never reports a row COUNT - it reads file "
              "progress, so the count stays unknown rather than becoming 0")

        # A DATABASE PRESENT WITHOUT ITS TABLE IS DRIFT, NOT "NEVER RAN". Probed for real
        # against a temporary registry entry rather than asserted on source text.
        import duckdb
        drifted = Path(tmp) / "drifted.duckdb"
        con = duckdb.connect(str(drifted))
        con.execute("CREATE TABLE something_else(x BIGINT)")
        con.close()
        _saved_dir = globals()["DATA_DIR"]
        globals()["DATA_DIR"] = Path(tmp)
        RECORDER_CLOCKS["__probe_selftest__"] = ("drifted.duckdb", "expected_table",
                                                 "recv_ts_ns", "NANOS")
        try:
            drift = probe("__probe_selftest__")
            check(drift["status"] == "SCHEMA_DRIFT",
                  "a store that EXISTS but lacks its table is SCHEMA_DRIFT - reporting "
                  "NEVER_RAN sends an operator hunting a process that did in fact run")
            check(drift["rows"] is None and drift["rows_known"] is False,
                  "and its row count is UNKNOWN, not 0 - nothing counted it")
        finally:
            RECORDER_CLOCKS.pop("__probe_selftest__", None)
            globals()["DATA_DIR"] = _saved_dir

    # The registry must agree with the audit's store mapping - two registries that can drift
    # is a defect this repository has already had once.
    sys.path.insert(0, str(Path(__file__).resolve().parent / "audit"))
    from recorder_evidence_check import EXPECTED_STORE
    for name, (store, table, _c, _u) in RECORDER_CLOCKS.items():
        if name in EXPECTED_STORE:
            check((store, table) == EXPECTED_STORE[name],
                  f"{name} maps to the same store/table as the evidence audit")

    live = report()
    check(len(live) == len(RECORDER_CLOCKS), "every declared recorder is probed")
    check(all(r["status"] in ("ADVANCING", "STALLED", "NEVER_RAN", "LOCKED_BY_WRITER",
                              "UNREADABLE", "SCHEMA_DRIFT", "UNIT_MISMATCH")
              for r in live),
          "every probe returns a declared status, never a bare exception")

    print(f"\nRECORDER HEALTH SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--strict", action="store_true",
                        help="exit 1 unless every recorder is ADVANCING")
    args = parser.parse_args()
    if args.selftest:
        return selftest()

    print("=" * 96)
    print("RECORDER ROW-PROGRESS HEALTH - from the newest timestamp IN THE DATA")
    print("=" * 96)
    print(f"  {'recorder':<30}{'rows':>14}{'newest row (UTC)':>22}{'age':>12}  status")
    print("  " + "-" * 92)
    rows = report()
    for r in rows:
        age = (f"{r['age_ms'] / 60000:.0f}m" if r.get("age_ms") is not None else "-")
        print(f"  {r['recorder']:<30}{r['rows']:>14,}{r.get('newest_utc', '-'):>22}"
              f"{age:>12}  {r['status']}")
        if r.get("detail"):
            print(f"    {r['detail']}")

    advancing = [r for r in rows if r["status"] == "ADVANCING"]
    print()
    print(f"  {len(advancing)}/{len(rows)} recorders are ADVANCING.")
    print("  A recorder that is wired, selftests, and has never written a row is NEVER_RAN -")
    print("  the distinction file mtime and log files cannot make.")
    if args.strict:
        return 0 if len(advancing) == len(rows) else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
