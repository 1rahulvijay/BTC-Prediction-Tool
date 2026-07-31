"""Audit whether recorded market data can support execution research.

The repository now has two different archives:

* `multi_venue.duckdb`: top-of-book cross-venue timing. It supports taker
  execution and touch-level lead/lag, but never exact queue reconstruction.
* `binance_l2.duckdb`: REST snapshot plus sequenced USD-M diff depth. It can
  support deterministic local-book replay only after real rows have accrued.

Aggregate L2 still does not expose individual order priority. Therefore exact
passive-fill labels and production execution RL remain refused even when the
L2 archive is replayable. Conservative queue models are research estimates.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
MULTI_VENUE_SOURCE = Path(__file__).with_name("multi_venue_recorder.py")
L2_SOURCE = Path(__file__).with_name("binance_l2_recorder.py")
L2_DB = Path(os.environ.get("BTC_BINANCE_L2_DB") or DATA / "binance_l2.duckdb")

TOP_OF_BOOK_ONLY = {
    "binance_spot/bookTicker": "best bid/ask + aggregate size, 1 level per side",
    "binance_perp/bookTicker": "best bid/ask + aggregate size, 1 level per side",
    "bybit_perp/orderbook.1": "depth ONE - the name states the level count",
}

# Minimum evidence for declaring that at least one recorded session can be
# replayed. This is an operational smoke threshold, not a model-training gate.
MIN_REPLAY_DIFFS = 1_000


def _declared_streams(source: str) -> list[str]:
    import re

    match = re.search(r"^EXPECTED\s*=\s*\((.*?)\)", source, re.S | re.M)
    if not match:
        return []
    return re.findall(r'"([^"]+)"', match.group(1))


def _l2_code_contract() -> dict[str, Any]:
    if not L2_SOURCE.exists():
        return {"present": False, "missing": ["recorder_source"]}
    source = L2_SOURCE.read_text(encoding="utf-8")
    required = {
        "rest_snapshot": "lastUpdateId",
        "diff_first_id": '"U"',
        "diff_final_id": '"u"',
        "previous_update_id": '"pu"',
        "gap_rebuild": "BookSequenceGap",
        "raw_snapshot_table": "l2_snapshots",
        "raw_diff_table": "l2_diffs",
        "replay": "replay_session",
    }
    missing = [name for name, marker in required.items() if marker not in source]
    return {"present": not missing, "missing": missing}


def _l2_evidence() -> dict[str, Any]:
    if not L2_DB.exists():
        return {
            "path": str(L2_DB),
            "exists": False,
            "sessions": 0,
            "snapshots": 0,
            "applied_diffs": 0,
            "replayable_sessions": 0,
            "ready": False,
            "reason": "archive_missing",
        }
    try:
        import duckdb

        db = duckdb.connect(str(L2_DB), read_only=True)
        row = db.execute(
            """
            SELECT
                COUNT(*) AS sessions,
                COUNT(*) FILTER (WHERE snapshot_update_id IS NOT NULL) AS snapshots,
                COALESCE(SUM(applied_diffs), 0) AS applied_diffs,
                COUNT(*) FILTER (
                    WHERE snapshot_update_id IS NOT NULL
                      AND applied_diffs >= ?
                      AND gap_count = 0
                      AND status IN ('SYNCED', 'COMPLETED', 'INTERRUPTED')
                ) AS replayable_sessions
            FROM l2_sessions
            """,
            [MIN_REPLAY_DIFFS],
        ).fetchone()
        db.close()
        ready = int(row[3]) > 0
        return {
            "path": str(L2_DB),
            "exists": True,
            "sessions": int(row[0]),
            "snapshots": int(row[1]),
            "applied_diffs": int(row[2]),
            "replayable_sessions": int(row[3]),
            "ready": ready,
            "reason": "ready" if ready else "insufficient_gap_free_recorded_diffs",
        }
    except Exception as exc:
        return {
            "path": str(L2_DB),
            "exists": True,
            "sessions": 0,
            "snapshots": 0,
            "applied_diffs": 0,
            "replayable_sessions": 0,
            "ready": False,
            "reason": f"archive_unreadable:{type(exc).__name__}:{exc}",
        }


def audit() -> dict[str, Any]:
    multi_source = (
        MULTI_VENUE_SOURCE.read_text(encoding="utf-8")
        if MULTI_VENUE_SOURCE.exists()
        else ""
    )
    streams = _declared_streams(multi_source)
    code = _l2_code_contract()
    evidence = _l2_evidence()
    replay_ready = bool(code["present"] and evidence["ready"])
    return {
        "declared_top_of_book_streams": streams,
        "top_of_book_only": sorted(set(streams) & set(TOP_OF_BOOK_ONLY)),
        "l2_collector_code": code,
        "l2_evidence": evidence,
        "capability": {
            "top_of_book_microstructure": True,
            "trade_intensity_and_signing": True,
            "cross_venue_leadlag_on_touch": True,
            "immediate_taker_execution": True,
            "sequenced_l2_collection": bool(code["present"]),
            "l2_book_replay": replay_ready,
            "conservative_queue_research": replay_ready,
            "exact_passive_fill_simulation": False,
            "exact_queue_position_reconstruction": False,
            "production_execution_rl_training": False,
        },
    }


def main() -> int:
    report = audit()
    ok = True

    def check(condition: object, message: str) -> None:
        nonlocal ok
        print(f"  {'OK  ' if condition else 'FAIL'} {message}")
        ok = ok and bool(condition)

    print("=" * 78)
    print("EXECUTION DATA READINESS")
    print("=" * 78)
    print("\ntop-of-book archive")
    for stream in report["declared_top_of_book_streams"]:
        note = TOP_OF_BOOK_ONLY.get(stream, "")
        print(f"    {stream:<34}{note}")

    code = report["l2_collector_code"]
    evidence = report["l2_evidence"]
    print("\nsequenced L2 collector")
    check(code["present"], "snapshot + diff + gap + replay code contract exists")
    if code["missing"]:
        print(f"       missing markers: {', '.join(code['missing'])}")
    print(f"       archive: {evidence['path']}")
    print(
        f"       sessions={evidence['sessions']} snapshots={evidence['snapshots']} "
        f"applied_diffs={evidence['applied_diffs']} "
        f"replayable_sessions={evidence['replayable_sessions']}"
    )
    print(f"       evidence status: {evidence['reason']}")

    print("\ncapability")
    for name, value in report["capability"].items():
        print(f"    {'YES' if value else 'NO ':<4} {name}")

    check(
        report["capability"]["exact_queue_position_reconstruction"] is False,
        "individual queue priority is never inferred from aggregate public L2",
    )
    check(
        report["capability"]["production_execution_rl_training"] is False,
        "production execution RL remains refused without defensible fill labels",
    )
    check(
        report["capability"]["l2_book_replay"]
        == bool(code["present"] and evidence["ready"]),
        "book replay becomes ready from code plus recorded evidence, not code alone",
    )

    print("\nverdict")
    if report["capability"]["l2_book_replay"]:
        print("    Gap-detectable local-book replay is available for research.")
        print("    Passive fills remain conservative estimates, never observed truth.")
    else:
        print("    Collector code is installed, but replay evidence has not yet met the")
        print(f"    operational smoke threshold of {MIN_REPLAY_DIFFS:,} applied diffs")
        print("    in one gap-free session. Run start_binance_l2_recorder.bat.")

    print("\nEXECUTION DATA READINESS", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
