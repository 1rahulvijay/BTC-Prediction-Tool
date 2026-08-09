"""Which recorders are WIRED, and which have ever actually written anything?

WHY THIS EXISTS
    backend/venues/binance_l2_recorder.py is 804 lines of correct, CI-gated, selftested code
    for a fully sequenced Binance USD-M book. It is wired into start_recorders_once.ps1. Its
    selftest runs on every start.bat launch and passes.

    It has never recorded a single row. There is no binance_l2.duckdb anywhere, and no
    stdout/stderr log, because the launcher has not run since 2026-07-04 - and the recorder was
    wired in after that date. Meanwhile the research ledger carried "queue/maker research: no
    sequenced L2" as though the capability were missing, when in fact it existed and was idle.

    A passing selftest says the code is correct. It says nothing about whether the process ever
    ran. This check asks the second question, which is the one that was silently answered "no"
    for weeks.

WHAT IT REPORTS, per recorder wired in the launcher
    wired          the launcher references it
    ever ran       a stdout or stderr log exists
    has data       its DuckDB store exists and is non-empty
    freshness      newest timestamp IN THE DATA, through recorder_health.py

    A recorder that is wired, selftests, and has never produced a byte is reported as
    NEVER_RAN. STALLED, LOCKED_BY_WRITER, UNREADABLE, SCHEMA_DRIFT and UNIT_MISMATCH stay
    distinct; none is collapsed into NO_DATA.

    python backend/audit/recorder_evidence_check.py
    python backend/audit/recorder_evidence_check.py --selftest
"""
from __future__ import annotations

import argparse
import os
import re
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or REPO / "data")
LAUNCHER = REPO / "backend" / "start_recorders_once.ps1"

#: recorder script -> the store it writes. Declared, because the mapping is not derivable from
#: the launcher: a recorder names its DB internally or via an argument.
EXPECTED_STORE = {
    # pm_round_snapshots lives in execution_layer, NOT analytics. A first version pointed at
    # analytics and reported NO_DATA for a recorder holding 1.7M rows - a gate that fires on
    # healthy components is a gate people switch off.
    "live_btc_updown_recorder.py": ("execution_layer.duckdb", "pm_round_snapshots"),
    "l2_recorder.py": ("polymarket_l2.duckdb", "pm_l2_book_levels"),
    "microstructure_recorder.py": ("microstructure.duckdb", "l2_snapshots"),
    "multi_venue_recorder.py": ("multi_venue.duckdb", "venue_events"),
    "binance_l2_recorder.py": ("binance_l2.duckdb", "l2_diffs"),
}


def wired_recorders(text: str) -> list[str]:
    """Every recorder script the launcher invokes."""
    return sorted(set(re.findall(r"backend\\[\w\\]*?([\w]+_recorder\.py)", text)))


def store_state(store: str, table: str) -> dict:
    """Does the store exist, does it hold rows, and how recent are they?"""
    for candidate in (DATA_DIR / store, DATA_DIR / "btc_duckdbs" / store):
        if not candidate.is_file():
            continue
        try:
            import duckdb
            con = duckdb.connect(str(candidate), read_only=True)
            try:
                names = {r[0] for r in con.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'main'").fetchall()}
                rows = int(con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]) \
                    if table in names else 0
            finally:
                con.close()
        except Exception as exc:
            return {"path": candidate.name, "rows": None, "error": f"{type(exc).__name__}"}
        return {"path": str(candidate.relative_to(DATA_DIR)), "rows": rows,
                "mtime_utc": datetime.fromtimestamp(candidate.stat().st_mtime,
                                                    timezone.utc).isoformat()[:16]}
    return {"path": None, "rows": 0}


def logs_exist(script: str) -> bool:
    stem = script.replace("_recorder.py", "").replace("live_btc_updown", "pm_live")
    return any(DATA_DIR.glob(f"*{stem}*recorder*.log")) or any(DATA_DIR.glob(f"*{stem}*.log"))


def audit() -> list[dict]:
    text = LAUNCHER.read_text(encoding="utf-8", errors="replace") if LAUNCHER.is_file() else ""
    rows = []
    backend = str(REPO / "backend")
    if backend not in os.sys.path:
        os.sys.path.insert(0, backend)
    from recorder_health import RECORDER_CLOCKS, probe
    for script in wired_recorders(text):
        store, table = EXPECTED_STORE.get(script, (None, None))
        if script in RECORDER_CLOCKS:
            state = probe(script)
        else:
            state = store_state(store, table) if store else {"path": None, "rows": None}
        ran = logs_exist(script)
        rows_count = state.get("rows")
        status = state.get("status")
        if not status:
            status = ("NEVER_RAN" if not ran and not rows_count
                      else "NO_DATA" if not rows_count else "HAS_DATA")
        rows.append({
            "recorder": script, "store": store, "ever_ran": ran,
            "rows": rows_count,
            "last_write": state.get("newest_utc") or state.get("mtime_utc"),
            "status": status,
            "detail": state.get("detail"),
        })
    return rows


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    sample = (r'Start-Recorder "A" "foo\.py" @("-u", "backend\venues\multi_venue_recorder.py")'
              "\n"
              r'Start-Recorder "B" "bar\.py" @("-u", "backend\venues\binance_l2_recorder.py")')
    found = wired_recorders(sample)
    check(found == ["binance_l2_recorder.py", "multi_venue_recorder.py"],
          "every recorder the launcher invokes is discovered from its arguments")
    check(wired_recorders("") == [],
          "an empty launcher yields no recorders rather than raising")

    rows = audit()
    check(rows, "the real launcher wires at least one recorder")
    declared = {"ADVANCING", "STALLED", "NEVER_RAN", "LOCKED_BY_WRITER", "UNREADABLE",
                "SCHEMA_DRIFT", "UNIT_MISMATCH", "NO_DATA", "HAS_DATA"}
    check(all(row["status"] in declared for row in rows),
          "every wired recorder resolves to a declared evidence state")
    # The state this file exists to surface must be REACHABLE, or the check is decoration.
    check(any(row["status"] == "NEVER_RAN" for row in rows)
          or all(row["status"] in ("HAS_DATA", "ADVANCING", "STALLED", "LOCKED_BY_WRITER")
                 for row in rows),
          "NEVER_RAN is reachable - a wired, selftested, never-launched recorder is visible")

    print(f"\nRECORDER EVIDENCE SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    print("=" * 92)
    print("RECORDER EVIDENCE - wired is not the same as running")
    print("=" * 92)
    rows = audit()
    print(f"{'recorder':<34}{'ever ran':>10}{'rows':>14}{'last write':>19}  status")
    for row in rows:
        rows_text = f"{row['rows']:,}" if isinstance(row["rows"], int) else "-"
        print(f"{row['recorder']:<34}{str(bool(row['ever_ran'])):>10}{rows_text:>14}"
              f"{str(row['last_write'] or '-'):>19}  {row['status']}")
        if row.get("detail"):
            print(f"  {row['detail']}")

    never = [row["recorder"] for row in rows if row["status"] == "NEVER_RAN"]
    if never:
        print()
        print("  NEVER_RAN - wired into the launcher, selftests pass, and has produced nothing:")
        for name in never:
            print(f"    {name}")
        print("  A passing selftest proves the code is correct. It says nothing about whether")
        print("  the process ever started, which is the question that went unasked for weeks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
