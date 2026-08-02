"""BUILD_ORACLE_DATA_EVIDENCE_MANIFEST - prove coverage instead of assuming the recorders ran.

WHY
    "The recorders have been running since July 6" is a claim about intent. What matters for
    research is what actually landed: how many rows, over which hours, with what gaps, in which
    clock, and whether the sequence is intact. A campaign built on an unmeasured archive
    inherits every hole in it silently - which is how a study ends up describing trades nobody
    could have made.

    So every future campaign declares its data support, and this manifest is what it declares
    against:

        SUPPORTED_BY_DATA | PARTIALLY_SUPPORTED | BLOCKED_BY_SCHEMA | BLOCKED_BY_COVERAGE

WHAT IS MEASURED, PER TABLE
    first/last timestamp, rows, distinct days and hours, hours with ZERO rows inside the
    observed span, median and P95 inter-arrival, max gap, duplicate-timestamp rate,
    out-of-order rate, and - where a venue stamp exists alongside a receive stamp - the clock
    skew distribution between them.

    Gaps are computed with a window function, which means a full sort. Tables above
    MAX_ROWS_FOR_GAPS report counts and span but skip the gap statistics rather than making
    this script something nobody runs.

SEQUENCE INTEGRITY - the test that decides whether L2 research is possible at all
    A depth feed is only replayable if its sequence is intact. For every table carrying `seq`
    this reports duplicates and missing sequence numbers, and for Polymarket book summaries it
    additionally reports the recorder's own `synchronized` / `valid` / `invalid_reason` fields.
    Top-of-book with no sequence cannot support queue position, cancellation or maker-fill
    work no matter how many rows it has.

    python backend/audit/build_oracle_data_manifest.py
    python backend/audit/build_oracle_data_manifest.py --fast      # skip gap statistics
    python backend/audit/build_oracle_data_manifest.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or REPO / "data")
REPORT_DIR = DATA_DIR / "reports"

ORACLE_START = "2026-07-06"
# Above this, a lag() window means an out-of-core sort of hundreds of millions of values.
# Counts and span are still reported; only the gap statistics are skipped.
MAX_ROWS_FOR_GAPS = 30_000_000

# Preferred timestamp column per table, most specific first. A table with none is reported as
# having no time axis rather than being silently dropped.
TS_CANDIDATES = ("ts_ms", "recv_ts_ns", "recv_ts", "decision_ts", "ts", "timestamp",
                 "snapshot_ts", "created_at", "time", "start_ts", "first_seen_ns",
                 "anchor_ts", "window_start")
# A second stamp from the VENUE's clock, where the recorder captured one.
VENUE_TS_CANDIDATES = ("exchange_ts_ms", "exch_ts", "book_ts", "event_ts_ms", "venue_ts_ms")
# Columns that identify a recorder run, so restarts are countable rather than inferred.
SESSION_CANDIDATES = ("process_start_id", "connection_id", "recorder_session_id",
                      "collector_session", "session_id")


def _unit_divisor(sample: float) -> tuple[float, str]:
    """Infer the epoch unit from magnitude. Returns (divisor to seconds, unit name)."""
    magnitude = abs(float(sample))
    if magnitude > 1e17:
        return 1e9, "ns"
    if magnitude > 1e14:
        return 1e6, "us"
    if magnitude > 1e11:
        return 1e3, "ms"
    return 1.0, "s"


def _iso(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    try:
        return datetime.fromtimestamp(float(seconds), timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _columns(con, table: str) -> dict[str, str]:
    rows = con.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = ? AND table_schema = 'main'", [table]).fetchall()
    return {name: dtype for name, dtype in rows}


def _pick(columns: dict[str, str], candidates) -> str | None:
    for name in candidates:
        if name in columns:
            return name
    return None


def _time_expression(column: str, dtype: str) -> str:
    """SQL that yields epoch SECONDS as a double, whatever the stored representation."""
    quoted = f'"{column}"'
    if "TIMESTAMP" in dtype.upper() or "DATE" in dtype.upper():
        return f"epoch_ms({quoted}) / 1000.0"
    return quoted           # numeric; the caller divides by the inferred unit


def profile_table(con, table: str, *, fast: bool) -> dict:
    columns = _columns(con, table)
    rows = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
    report: dict = {"table": table, "rows": int(rows), "columns": len(columns)}
    if not rows:
        report["status"] = "EMPTY"
        return report

    ts_column = _pick(columns, TS_CANDIDATES)
    if ts_column is None:
        report["status"] = "NO_TIME_AXIS"
        report["note"] = "no recognised timestamp column; coverage cannot be established"
        return report

    dtype = columns[ts_column]
    expression = _time_expression(ts_column, dtype)
    is_temporal = "TIMESTAMP" in dtype.upper() or "DATE" in dtype.upper()
    if is_temporal:
        divisor, unit = 1.0, "timestamp"
    else:
        sample = con.execute(
            f'SELECT max({expression}) FROM "{table}" WHERE {expression} IS NOT NULL'
        ).fetchone()[0]
        if sample is None:
            report["status"] = "ALL_NULL_TIMESTAMPS"
            return report
        divisor, unit = _unit_divisor(sample)
    seconds = f"({expression}) / {divisor}" if not is_temporal else expression

    first, last, nulls, distinct_ts = con.execute(
        f'SELECT min({seconds}), max({seconds}), '
        f'       count(*) FILTER (WHERE {expression} IS NULL), '
        f'       count(DISTINCT {seconds}) FROM "{table}"').fetchone()
    span_hours = (float(last) - float(first)) / 3600.0 if first is not None else 0.0
    observed_hours = con.execute(
        f'SELECT count(DISTINCT floor({seconds} / 3600)) FROM "{table}"').fetchone()[0]
    observed_days = con.execute(
        f'SELECT count(DISTINCT floor({seconds} / 86400)) FROM "{table}"').fetchone()[0]

    report.update({
        "status": "PROFILED",
        "ts_column": ts_column,
        "ts_unit": unit,
        "first_utc": _iso(first),
        "last_utc": _iso(last),
        "span_hours": round(span_hours, 2),
        "distinct_days": int(observed_days),
        "hours_with_rows": int(observed_hours),
        "hours_in_span": int(span_hours) + 1 if span_hours else 0,
        "hours_with_zero_rows": max(0, (int(span_hours) + 1) - int(observed_hours))
        if span_hours else 0,
        "null_timestamps": int(nulls),
        "duplicate_timestamp_rate": round(1.0 - (float(distinct_ts) / float(rows)), 6)
        if rows else 0.0,
        "covers_oracle_window": bool(_iso(last) and _iso(last) >= ORACLE_START),
    })

    if not fast and rows <= MAX_ROWS_FOR_GAPS:
        median_gap, p95_gap, max_gap, backwards = con.execute(
            f'SELECT quantile_cont(d, 0.5), quantile_cont(d, 0.95), max(d), '
            f'       count(*) FILTER (WHERE d < 0) '
            f'FROM (SELECT {seconds} - lag({seconds}) OVER (ORDER BY {seconds}) AS d '
            f'      FROM "{table}") WHERE d IS NOT NULL').fetchone()
        report.update({
            "median_interval_s": round(float(median_gap), 4) if median_gap is not None else None,
            "p95_interval_s": round(float(p95_gap), 4) if p95_gap is not None else None,
            "max_gap_s": round(float(max_gap), 2) if max_gap is not None else None,
            "max_gap_hours": round(float(max_gap) / 3600.0, 2) if max_gap is not None else None,
            "out_of_order_rows": int(backwards or 0),
        })
    else:
        report["gap_statistics"] = ("SKIPPED_TOO_LARGE" if rows > MAX_ROWS_FOR_GAPS
                                   else "SKIPPED_FAST_MODE")

    venue_column = _pick(columns, VENUE_TS_CANDIDATES)
    if venue_column and not is_temporal:
        venue_expression = f'"{venue_column}"'
        venue_sample = con.execute(
            f'SELECT max({venue_expression}) FROM "{table}" '
            f'WHERE {venue_expression} IS NOT NULL AND {venue_expression} > 0').fetchone()[0]
        if venue_sample:
            venue_divisor, venue_unit = _unit_divisor(venue_sample)
            skew = con.execute(
                f'SELECT count(*), quantile_cont(s, 0.5), quantile_cont(s, 0.95), min(s), max(s) '
                f'FROM (SELECT ({venue_expression} / {venue_divisor}) - ({seconds}) AS s '
                f'      FROM "{table}" WHERE {venue_expression} IS NOT NULL '
                f'        AND {venue_expression} > 0) WHERE s IS NOT NULL').fetchone()
            report["clock_skew"] = {
                "venue_column": venue_column, "venue_unit": venue_unit,
                "rows_with_venue_stamp": int(skew[0] or 0),
                "median_s": round(float(skew[1]), 4) if skew[1] is not None else None,
                "p95_s": round(float(skew[2]), 4) if skew[2] is not None else None,
                "min_s": round(float(skew[3]), 4) if skew[3] is not None else None,
                "max_s": round(float(skew[4]), 4) if skew[4] is not None else None,
                "note": "venue clock minus local receive clock; two unsynchronised clocks, so "
                        "this is a skew measurement and not an ordering claim",
            }

    if "seq" in columns:
        lo, hi, distinct_seq = con.execute(
            f'SELECT min(seq), max(seq), count(DISTINCT seq) FROM "{table}"').fetchone()
        expected = (int(hi) - int(lo) + 1) if lo is not None else 0
        absent = max(0, expected - int(distinct_seq))
        # A counter SHARED across sibling tables looks catastrophically gappy when read one
        # table at a time - pm_l2_book_summaries reported 16.8M "missing" purely because its
        # seq is drawn from the same stream as pm_l2_book_levels. Reporting that as data loss
        # would send someone hunting a recorder bug that does not exist.
        shared = absent > int(rows)
        report["sequence"] = {
            "min": int(lo) if lo is not None else None,
            "max": int(hi) if hi is not None else None,
            "distinct": int(distinct_seq),
            "duplicates": int(rows) - int(distinct_seq),
            "absent_in_range": absent,
            "shared_counter_suspected": shared,
            "contiguous": expected == int(distinct_seq) and int(rows) == int(distinct_seq),
            "note": ("absent_in_range exceeds the row count, so this seq is almost certainly a "
                     "counter shared with sibling tables; per-table contiguity is not "
                     "meaningful and this is NOT evidence of dropped events")
            if shared else "table-local counter; absent_in_range is genuine sequence loss",
        }

    session_column = _pick(columns, SESSION_CANDIDATES)
    if session_column:
        sessions = con.execute(
            f'SELECT count(DISTINCT "{session_column}") FROM "{table}"').fetchone()[0]
        report["recorder_sessions"] = {"column": session_column, "distinct": int(sessions)}
    for flag in ("synchronized", "valid", "applied"):
        if flag in columns:
            true_rows = con.execute(
                f'SELECT count(*) FILTER (WHERE "{flag}") FROM "{table}"').fetchone()[0]
            report.setdefault("recorder_flags", {})[flag] = {
                "true": int(true_rows), "rate": round(float(true_rows) / float(rows), 6)}
    if "invalid_reason" in columns:
        reasons = con.execute(
            f'SELECT invalid_reason, count(*) FROM "{table}" '
            f'WHERE invalid_reason IS NOT NULL AND invalid_reason <> \'\' '
            f'GROUP BY 1 ORDER BY 2 DESC LIMIT 5').fetchall()
        report.setdefault("recorder_flags", {})["invalid_reasons"] = {
            str(reason): int(count) for reason, count in reasons}
    return report


def profile_store(path: Path, *, fast: bool) -> dict:
    import duckdb
    try:
        label = path.relative_to(DATA_DIR).as_posix()
    except ValueError:
        label = path.name
    store: dict = {"store": label, "size_bytes": path.stat().st_size, "tables": []}
    try:
        con = duckdb.connect(str(path), read_only=True)
    except Exception as exc:
        store["error"] = f"{type(exc).__name__}: {exc}"
        return store
    try:
        tables = [row[0] for row in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name").fetchall()]
        for table in tables:
            try:
                store["tables"].append(profile_table(con, table, fast=fast))
            except Exception as exc:
                store["tables"].append({"table": table, "status": "ERROR",
                                        "error": f"{type(exc).__name__}: {exc}"})
    finally:
        con.close()
    return store


def headline(manifest: dict) -> dict:
    """The two questions a reader actually opens this file to answer.

    1. WHEN did recording stop? Not when someone remembers it stopping.
    2. Which stores share a NAME but not a span? That ambiguity silently decides which sample
       a study reads, and it is how a manifest ends up profiling a stale copy."""
    profiled = [(store["store"], table) for store in manifest["stores"]
                for table in store["tables"] if table.get("status") == "PROFILED"]
    latest = max((t["last_utc"] for _, t in profiled if t.get("last_utc")), default=None)
    freshest = sorted(
        ((store, t["table"], t["last_utc"]) for store, t in profiled if t.get("last_utc")),
        key=lambda row: row[2], reverse=True)[:5]

    by_basename: dict[str, list] = {}
    for store in manifest["stores"]:
        base = Path(store["store"]).name
        spans = [t["last_utc"] for t in store["tables"] if t.get("last_utc")]
        if spans:
            by_basename.setdefault(base, []).append(
                {"path": store["store"], "last_utc": max(spans),
                 "first_utc": min(t["first_utc"] for t in store["tables"]
                                  if t.get("first_utc"))})
    ambiguous = {name: copies for name, copies in by_basename.items() if len(copies) > 1}
    return {
        "recording_stopped_utc": latest,
        "freshest_tables": [{"store": s, "table": t, "last_utc": u} for s, t, u in freshest],
        "duplicate_store_names": ambiguous,
        "duplicate_store_warning": (
            "Stores sharing a filename hold DIFFERENT spans. Serving resolves the path from "
            "BTC_DB_PATH/BTC_DATA_DIR while some research modules hardcode a different copy, "
            "so 'the database' is not a single object. State which path a study read."
        ) if ambiguous else None,
    }


def render_markdown(manifest: dict) -> str:
    lines = [
        "# Oracle data evidence manifest",
        "",
        f"Generated `{manifest['generated_utc']}`. Oracle serving since `{ORACLE_START}`.",
        "",
        "Coverage is measured, not assumed. Every research campaign must declare its support",
        "against this file: `SUPPORTED_BY_DATA`, `PARTIALLY_SUPPORTED`, `BLOCKED_BY_SCHEMA`",
        "or `BLOCKED_BY_COVERAGE`.",
        "",
        "| store | table | rows | first | last | days | zero-hours | max gap (h) | seq |",
        "|---|---|---:|---|---|---:|---:|---:|---|",
    ]
    head = manifest.get("headline") or {}
    lines[3:3] = [
        f"**Recording stopped `{head.get('recording_stopped_utc')}`** - measured from the data,",
        "not from memory.",
        "",
    ] + ([
        "**Stores share filenames but not spans.** " + str(head.get("duplicate_store_warning")),
        "",
    ] + [f"- `{path['path']}` : `{path['first_utc'][:16]}` -> `{path['last_utc'][:16]}`"
         for copies in head.get("duplicate_store_names", {}).values() for path in copies]
        + [""] if head.get("duplicate_store_names") else [])
    for store in manifest["stores"]:
        for table in store["tables"]:
            if table.get("status") in ("EMPTY",):
                continue
            sequence = table.get("sequence")
            seq_cell = "-"
            if sequence:
                seq_cell = ("contiguous" if sequence["contiguous"]
                            else "shared counter" if sequence["shared_counter_suspected"]
                            else f"{sequence['absent_in_range']:,} absent")
            lines.append(
                f"| `{store['store'].replace('.duckdb', '')}` | `{table['table']}` "
                f"| {table.get('rows', 0):,} "
                f"| {str(table.get('first_utc') or '-')[:16]} "
                f"| {str(table.get('last_utc') or '-')[:16]} "
                f"| {table.get('distinct_days', '-')} "
                f"| {table.get('hours_with_zero_rows', '-')} "
                f"| {table.get('max_gap_hours', '-')} | {seq_cell} |")

    lines += ["", "## Clock skew where a venue stamp exists", "",
              "| store | table | rows | median (s) | p95 (s) | max (s) |", "|---|---|---:|---:|---:|---:|"]
    for store in manifest["stores"]:
        for table in store["tables"]:
            skew = table.get("clock_skew")
            if not skew:
                continue
            lines.append(f"| `{store['store'].replace('.duckdb', '')}` | `{table['table']}` "
                         f"| {skew['rows_with_venue_stamp']:,} | {skew['median_s']} "
                         f"| {skew['p95_s']} | {skew['max_s']} |")
    lines += ["", "Two unsynchronised clocks: this measures skew, it does not establish order.",
              ""]
    return "\n".join(lines) + "\n"


def selftest() -> int:
    """Unit inference and the profiler must both work on a table built to be awkward."""
    import tempfile

    import duckdb
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "probe.duckdb"
        con = duckdb.connect(str(path))
        base = 1_760_000_000_000            # ms
        con.execute("CREATE TABLE probe (ts_ms BIGINT, exchange_ts_ms BIGINT, seq BIGINT)")
        # Two rows one second apart, then a deliberate one-hour hole, and a duplicate stamp.
        values = [(base, base - 200, 1), (base + 1_000, base + 800, 2),
                  (base + 3_601_000, base + 3_600_800, 4), (base + 3_601_000, None, 5)]
        con.executemany("INSERT INTO probe VALUES (?, ?, ?)", values)
        report = profile_table(con, "probe", fast=False)
        con.close()

    assert report["ts_unit"] == "ms", f"ms not inferred, got {report['ts_unit']}"
    assert report["rows"] == 4
    assert report["max_gap_hours"] == 1.0, f"one-hour hole not found: {report['max_gap_hours']}"
    assert report["duplicate_timestamp_rate"] > 0, "duplicate timestamp not detected"
    assert report["sequence"]["absent_in_range"] == 1, "missing seq 3 not detected"
    assert report["sequence"]["shared_counter_suspected"] is False, (
        "a table-local counter must not be excused as a shared one")
    assert report["clock_skew"]["rows_with_venue_stamp"] == 3
    assert report["clock_skew"]["median_s"] is not None
    for sample, expected in ((1.76e18, "ns"), (1.76e15, "us"), (1.76e12, "ms"), (1.76e9, "s")):
        assert _unit_divisor(sample)[1] == expected, f"unit inference wrong for {sample}"
    print("  SELFTEST PASS - gap, duplicate, missing-sequence and skew all detected")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fast", action="store_true", help="skip gap statistics")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    print("=" * 96)
    print("ORACLE DATA EVIDENCE MANIFEST - what the recorders actually captured")
    print("=" * 96)
    if args.selftest:
        return selftest()

    # Recurse. The repository holds THREE analytics.duckdb files with different spans, and
    # serving reads one while research hardcodes another - profiling only data/*.duckdb
    # produced a manifest that said the archive stops on 2026-07-04 when the sample the
    # studies actually used runs to 2026-07-25. A coverage manifest that quietly picks one
    # copy is worse than none.
    stores = sorted(p for p in DATA_DIR.rglob("*.duckdb") if p.is_file())
    if not stores:
        print(f"  no DuckDB stores under {DATA_DIR}")
        return 1

    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "oracle_start": ORACLE_START,
        "data_dir": str(DATA_DIR),
        "fast_mode": bool(args.fast),
        "stores": [],
    }
    for path in stores:
        print(f"  profiling {path.relative_to(DATA_DIR).as_posix()} ({path.stat().st_size / 1e6:.0f} MB) ...", flush=True)
        manifest["stores"].append(profile_store(path, fast=args.fast))

    manifest["headline"] = headline(manifest)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "oracle_data_manifest.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    (REPORT_DIR / "oracle_data_manifest.md").write_text(
        render_markdown(manifest), encoding="utf-8")

    profiled = [t for s in manifest["stores"] for t in s["tables"] if t.get("status") == "PROFILED"]
    stale = [t for t in profiled if not t.get("covers_oracle_window")]
    print()
    print(f"  stores {len(manifest['stores'])} | tables profiled {len(profiled)}")
    print(f"  tables whose LAST row predates {ORACLE_START}: {len(stale)}")
    head = manifest["headline"]
    print(f"  RECORDING STOPPED  : {head['recording_stopped_utc']}")
    if head["duplicate_store_names"]:
        print(f"  duplicate store names: {sorted(head['duplicate_store_names'])}"
              "  <- 'the database' is not one object")
    print(f"  wrote {(REPORT_DIR / 'oracle_data_manifest.json').relative_to(REPO).as_posix()}")
    print(f"  wrote {(REPORT_DIR / 'oracle_data_manifest.md').relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
