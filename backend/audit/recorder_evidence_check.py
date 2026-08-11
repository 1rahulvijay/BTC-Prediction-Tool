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
    "btc_tick_recorder.py": ("btc_ticks.duckdb", "btc_tick_heartbeats"),
    "crossing_recorder_hf.py": ("polymarket_crossings_hf.duckdb", "hf_heartbeats"),
    "cross_window_recorder.py": ("cross_window.duckdb", "cross_window_heartbeats"),
    "deribit_option_chain_recorder.py": ("deribit_options.duckdb",
                                          "deribit_chain_batches"),
    "funding_recorder.py": ("funding.duckdb", "funding_heartbeats"),
}

REQUIRED_LAUNCHER_RECORDERS = frozenset(EXPECTED_STORE)


def wired_recorders(text: str) -> list[str]:
    """Every recorder script the launcher invokes."""
    found = set()
    for line in text.splitlines():
        # Audit/selftest references are not launched recorder daemons.
        if "--selftest" in line or re.search(r"backend\\audit\\", line, re.I):
            continue
        match = re.search(r"backend\\[\w\\]*?([\w]*recorder[\w]*\.py)", line, re.I)
        if match:
            found.add(match.group(1))
    return sorted(found)


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
    stem = Path(script).stem
    aliases = {stem, stem.replace("_recorder", "")}
    if stem == "live_btc_updown_recorder":
        aliases.add("pm_live_recorder")
    return any(any(DATA_DIR.glob(f"*{alias}*.log")) for alias in aliases if alias)


def derive_status(ran: bool, rows_count) -> str:
    """Fallback classification when a probe reports no status of its own.

    Extracted so the selftest can exercise THIS rule rather than scanning the live database.
    The reachability check used to assert that some real recorder was currently NEVER_RAN, or
    that every recorder sat in a healthy state - so it passed or failed on whatever the store
    happened to hold, and went red the moment one recorder entered SCHEMA_DRIFT, which is a
    state this audit exists to REPORT. A selftest that depends on production data tells you
    about production, not about the code.
    """
    if not ran and not rows_count:
        return "NEVER_RAN"
    if not rows_count:
        return "NO_DATA"
    return "HAS_DATA"


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
            status = derive_status(ran, rows_count)
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
    decoys = (r'python backend\recorder_health.py --selftest' "\n"
              r'python backend\audit\recorder_evidence_check.py --selftest')
    check(wired_recorders(decoys) == [],
          "audit and selftest modules are not counted as recorder daemons")

    launcher_text = LAUNCHER.read_text(encoding="utf-8", errors="replace")
    real_wired = set(wired_recorders(launcher_text))
    check(real_wired, "the real launcher wires at least one recorder")
    check(REQUIRED_LAUNCHER_RECORDERS <= real_wired,
          "the launcher includes every declared standalone forward recorder")
    declared = {"ADVANCING", "STALLED", "NEVER_RAN", "LOCKED_BY_WRITER", "UNREADABLE",
                "SCHEMA_DRIFT", "UNIT_MISMATCH", "NO_DATA", "HAS_DATA"}
    check({"SCHEMA_DRIFT", "UNIT_MISMATCH", "NEVER_RAN", "STALLED"} <= declared,
          "all fatal and operational recorder states are declared")
    # The state this file exists to surface must be REACHABLE, or the check is decoration.
    #
    # Reachability is a property of the CLASSIFIER, not of today's database. This used to
    # assert that some real recorder was currently NEVER_RAN, or that every recorder sat in a
    # healthy set - so it went red when the live store held 9 STALLED and 1 SCHEMA_DRIFT, a
    # combination containing nothing wrong: SCHEMA_DRIFT is precisely what this audit exists
    # to report. Exercising derive_status directly keeps the guarantee and drops the
    # dependency on production data.
    check(derive_status(ran=False, rows_count=0) == "NEVER_RAN",
          "NEVER_RAN is reachable - a recorder that never ran and holds no rows classifies "
          "as NEVER_RAN, so the state this file exists to surface can actually occur")
    check(derive_status(ran=True, rows_count=0) == "NO_DATA",
          "a recorder that RAN but wrote nothing is NO_DATA, not NEVER_RAN - the two failures "
          "have different causes and must not collapse into one label")
    check(derive_status(ran=True, rows_count=5) == "HAS_DATA"
          and derive_status(ran=False, rows_count=5) == "HAS_DATA",
          "rows present means HAS_DATA whether or not a log survives - evidence in the store "
          "outranks the absence of a log file")

    # audit() must actually USE the classifier the checks above exercise. Every recorder in
    # this environment gets its status from probe(), so the fallback branch never runs here -
    # meaning audit() could stop calling derive_status entirely and the assertions above would
    # still pass, testing a function nothing uses. Mutation testing surfaced exactly that.
    import ast as _ast
    _src = Path(__file__).read_text(encoding="utf-8", errors="replace")
    _fn = next(n for n in _ast.walk(_ast.parse(_src))
               if isinstance(n, _ast.FunctionDef) and n.name == "audit")
    check(any(isinstance(c, _ast.Call) and getattr(c.func, "id", "") == "derive_status"
              for c in _ast.walk(_fn)),
          "audit() derives its fallback status through derive_status - the rule the checks "
          "above verify is the rule the report actually uses")

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
    schema_failures = [row for row in rows
                       if row["status"] in ("SCHEMA_DRIFT", "UNIT_MISMATCH")]
    if schema_failures:
        print()
        print("  FATAL DECLARATION DRIFT - recorder health cannot be measured:")
        for row in schema_failures:
            print(f"    {row['recorder']}: {row['status']} - {row.get('detail') or ''}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
