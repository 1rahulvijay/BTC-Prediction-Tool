"""Validate exact depth VWAP and run public-L2 queue scenario research.

The self-test is deterministic and network-free.  The default report also reads the
forward L2 recorder DB, writes latest-size VWAP and hypothetical maker queue CSVs,
and refuses to call queue output exact because public L2 has no order-level rank.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import os
import sys
import tempfile
from pathlib import Path

import duckdb


ROOT = Path(__file__).resolve().parents[2]
POLYMARKET_DIR = ROOT / "backend" / "polymarket"
if str(POLYMARKET_DIR) not in sys.path:
    sys.path.insert(0, str(POLYMARKET_DIR))

from l2_book import L2Book, QueueEvent, simulate_queue  # noqa: E402
from l2_recorder import L2Recorder, L2Store  # noqa: E402


DATA = Path(os.environ.get("BTC_DATA_DIR", ROOT / "data"))
DEFAULT_DB = Path(os.environ.get("BTC_PM_L2_DB", DATA / "polymarket_l2.duckdb"))
DEFAULT_OUTPUT = DATA / "research" / "polymarket_l2_execution"


def _close(actual, expected, tolerance=1e-10):
    assert math.isclose(float(actual), float(expected), rel_tol=tolerance, abs_tol=tolerance), (
        actual, expected)


def selftest() -> None:
    book = L2Book("UP")
    assert book.load_snapshot(
        bids=[{"price": ".48", "size": "30"}, {"price": ".47", "size": "70"}],
        asks=[{"price": ".54", "size": "10"}, {"price": ".52", "size": "25"},
              {"price": ".53", "size": "60"}],
        market="m1", exchange_ts_ms=1000, recv_ts_ns=1_000_000_000,
    )
    buy = book.execution_vwap("BUY", 50)
    assert buy.complete and buy.levels_consumed == 2
    _close(buy.vwap, (25 * .52 + 25 * .53) / 50)
    _close(buy.worst_price, .53)
    assert buy.all_in_unit_price > buy.vwap
    sell = book.execution_vwap("SELL", 50)
    assert sell.complete and sell.levels_consumed == 2
    _close(sell.vwap, (30 * .48 + 20 * .47) / 50)
    assert sell.all_in_unit_price < sell.vwap
    partial = book.execution_vwap("BUY", 500)
    assert not partial.complete and partial.filled_size == 95 and partial.reject_reason == "insufficient_depth"

    assert book.apply_price_change("SELL", ".52", "0", exchange_ts_ms=1001)
    _close(book.best_ask, .53)
    assert not book.apply_price_change("BUY", ".60", "1", exchange_ts_ms=1002)
    assert book.invalid_reason == "crossed_book"
    rejected = book.execution_vwap("BUY", 1)
    assert rejected.reject_reason == "crossed_book"

    events = [
        QueueEvent(1_100_000_000, "LEVEL", .48, side="BUY", new_level_size=80),
        QueueEvent(1_200_000_000, "TRADE", .48, size=100, side="SELL"),
    ]
    conservative = simulate_queue(order_side="BUY", price=.48, size=25, displayed_size=100,
                                  decision_ts_ns=1_000_000_000, events=events,
                                  mode="conservative")
    base = simulate_queue(order_side="BUY", price=.48, size=25, displayed_size=100,
                          decision_ts_ns=1_000_000_000, events=events, mode="base")
    optimistic = simulate_queue(order_side="BUY", price=.48, size=25, displayed_size=100,
                                decision_ts_ns=1_000_000_000, events=events,
                                mode="optimistic")
    _close(conservative.filled_size, 0)
    _close(base.filled_size, 10)
    _close(optimistic.filled_size, 20)
    assert conservative.fill_ratio <= base.fill_ratio <= optimistic.fill_ratio

    latency_events = [
        QueueEvent(1_050_000_000, "LEVEL", .48, side="BUY", new_level_size=70),
        QueueEvent(1_200_000_000, "TRADE", .48, size=80, side="SELL"),
    ]
    delayed = simulate_queue(order_side="BUY", price=.48, size=20, displayed_size=100,
                             decision_ts_ns=1_000_000_000, submit_latency_ms=100,
                             events=latency_events, mode="conservative")
    _close(delayed.filled_size, 10)
    _close(delayed.time_to_first_fill_ms, 100)

    temp_path = Path(tempfile.gettempdir()) / f"pm_l2_selftest_{os.getpid()}.duckdb"
    for suffix in ("", ".wal"):
        try:
            Path(str(temp_path) + suffix).unlink()
        except FileNotFoundError:
            pass
    store = L2Store(temp_path)
    recorder = L2Recorder(store, sizes=(10, 50))
    recorder.process({
        "event_type": "book", "asset_id": "UP", "market": "m1", "timestamp": "1000",
        "hash": "h1", "bids": [{"price": ".48", "size": "100"}],
        "asks": [{"price": ".52", "size": "25"}, {"price": ".53", "size": "50"}],
    }, 1_000_000_000)
    recorder.process({
        "event_type": "price_change", "market": "m1", "timestamp": "1001",
        "price_changes": [{"asset_id": "UP", "price": ".52", "size": "20",
                           "side": "SELL", "best_bid": ".48", "best_ask": ".52", "hash": "h2"}],
    }, 1_001_000_000)
    recorder.process({
        "event_type": "last_trade_price", "asset_id": "UP", "market": "m1",
        "timestamp": "1002", "price": ".48", "size": "10", "side": "SELL",
        "fee_rate_bps": "0",
    }, 1_002_000_000)
    counts = store.report()["counts"]
    assert counts["pm_l2_raw_events"] == 2
    assert counts["pm_l2_book_levels"] == 3
    assert counts["pm_l2_level_updates"] == 1
    assert counts["pm_l2_trades"] == 1
    assert counts["pm_l2_execution_snapshots"] == 1
    stored_json = store.conn.execute(
        "SELECT results_json FROM pm_l2_execution_snapshots WHERE seq=1").fetchone()[0]
    stored = [row for row in json.loads(stored_json)
              if row["side"] == "BUY" and row["requested_size"] == 50][0]
    _close(stored["vwap"], (25 * .52 + 25 * .53) / 50)
    store.close()
    temp_path.unlink(missing_ok=True)
    print("L2 EXECUTION SELFTEST PASS: exact VWAP, integrity gates, queue bounds, latency, persistence")


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("status\nno_rows\n", encoding="ascii")
        return
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _latest_vwap(conn) -> list[dict]:
    records = conn.execute("""SELECT m.slug, m.horizon, m.outcome, v.asset_id,
            v.recv_ts_ns, v.results_json
        FROM pm_l2_execution_snapshots v LEFT JOIN pm_l2_markets m USING(asset_id)
        QUALIFY row_number() OVER (PARTITION BY v.asset_id ORDER BY v.recv_ts_ns DESC) = 1
        ORDER BY m.horizon, m.outcome""").fetchall()
    rows = []
    for slug, horizon, outcome, asset, recv_ns, payload in records:
        for result in json.loads(payload):
            rows.append({"slug": slug, "horizon": horizon, "outcome": outcome,
                         "asset_id": asset, "recv_ts_ns": recv_ns, **result})
    return rows


def _queue_replay(conn, max_orders: int, order_size: float, window_seconds: float,
                  submit_latency_ms: float) -> list[dict]:
    candidates = conn.execute("""SELECT s.seq, s.recv_ts_ns, s.asset_id, s.best_bid,
            s.best_ask, s.best_bid_size, s.best_ask_size, m.slug, m.horizon, m.outcome
        FROM pm_l2_book_summaries s LEFT JOIN pm_l2_markets m USING(asset_id)
        WHERE s.event_type='book' AND s.valid AND s.best_bid IS NOT NULL AND s.best_ask IS NOT NULL
        QUALIFY row_number() OVER (PARTITION BY s.asset_id ORDER BY s.recv_ts_ns) % 5 = 1
        ORDER BY s.recv_ts_ns DESC LIMIT ?""", [int(max_orders)]).fetchall()
    updates = conn.execute("""SELECT recv_ts_ns, asset_id, side, price, new_size
        FROM pm_l2_level_updates WHERE applied ORDER BY asset_id, recv_ts_ns""").fetchall()
    trades = conn.execute("""SELECT recv_ts_ns, asset_id, aggressor_side, price, size
        FROM pm_l2_trades ORDER BY asset_id, recv_ts_ns""").fetchall()
    boundaries = [int(row[0]) for row in conn.execute("""SELECT recv_ts_ns
        FROM pm_l2_raw_events WHERE event_type IN ('connection_start', 'connection_end')
        ORDER BY recv_ts_ns""").fetchall()]
    grouped: dict[str, list[QueueEvent]] = {}
    for ts, asset, side, price, new_size in updates:
        grouped.setdefault(str(asset), []).append(
            QueueEvent(int(ts), "LEVEL", float(price), side=str(side), new_level_size=float(new_size)))
    for ts, asset, side, price, size in trades:
        grouped.setdefault(str(asset), []).append(
            QueueEvent(int(ts), "TRADE", float(price), size=float(size), side=str(side)))
    event_times = {}
    for asset, events in grouped.items():
        events.sort(key=lambda event: event.ts_ns)
        event_times[asset] = [event.ts_ns for event in events]

    rows = []
    for seq, decision_ns, asset, bid, ask, bid_size, ask_size, slug, horizon, outcome in candidates:
        asset = str(asset)
        all_events = grouped.get(asset, [])
        times = event_times.get(asset, [])
        requested_finish = int(decision_ns + window_seconds * 1e9)
        boundary_index = bisect.bisect_right(boundaries, int(decision_ns))
        next_boundary = boundaries[boundary_index] if boundary_index < len(boundaries) else None
        finish = min(requested_finish, next_boundary) if next_boundary else requested_finish
        left = bisect.bisect_left(times, int(decision_ns))
        right = bisect.bisect_right(times, finish)
        events = all_events[left:right]
        for order_side, price, displayed in (("BUY", bid, bid_size), ("SELL", ask, ask_size)):
            for mode in ("conservative", "base", "optimistic"):
                result = simulate_queue(
                    order_side=order_side, price=price, size=order_size,
                    displayed_size=displayed, decision_ts_ns=int(decision_ns), events=events,
                    mode=mode, submit_latency_ms=submit_latency_ms, expiry_ts_ns=finish,
                )
                row = result.to_dict()
                row.update({"snapshot_seq": int(seq), "slug": slug, "horizon": horizon,
                            "outcome": outcome, "events_seen": len(events),
                            "window_complete": finish == requested_finish})
                rows.append(row)
    return rows


def report(db_path: Path, output_dir: Path, max_orders: int, order_size: float,
           window_seconds: float, submit_latency_ms: float) -> int:
    if not db_path.exists():
        print(f"No L2 database yet: {db_path}")
        print("Start run_polymarket_l2_recorder.bat, then rerun this report after data accrues.")
        return 2
    try:
        conn = duckdb.connect(str(db_path), read_only=True)
    except Exception as exc:
        print(f"Cannot read {db_path}: {exc}")
        print("Stop the standalone L2 recorder briefly and rerun; its DB is restart-safe.")
        return 2
    try:
        latest = _latest_vwap(conn)
        queue = _queue_replay(conn, max_orders, order_size, window_seconds, submit_latency_ms)
        raw_count = conn.execute("SELECT count(*) FROM pm_l2_raw_events").fetchone()[0]
        valid = conn.execute("SELECT count(*) FROM pm_l2_book_summaries WHERE valid").fetchone()[0]
        invalid = conn.execute("SELECT count(*) FROM pm_l2_book_summaries WHERE NOT valid").fetchone()[0]
    finally:
        conn.close()
    _write_csv(output_dir / "latest_exact_depth_vwap.csv", latest)
    _write_csv(output_dir / "maker_queue_scenarios.csv", queue)
    summary = {
        "database": str(db_path), "raw_events": int(raw_count),
        "valid_book_states": int(valid), "invalid_book_states": int(invalid),
        "latest_vwap_rows": len(latest), "queue_scenario_rows": len(queue),
        "queue_order_size": order_size, "queue_window_seconds": window_seconds,
        "submit_latency_ms": submit_latency_ms,
        "queue_truth": "scenario_only_until_calibrated_with_authenticated user-channel fills",
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print(f"Wrote {output_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-orders", type=int, default=200)
    parser.add_argument("--order-size", type=float, default=10.0)
    parser.add_argument("--window-seconds", type=float, default=30.0)
    parser.add_argument("--submit-latency-ms", type=float, default=250.0)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    selftest()
    if args.selftest:
        return 0
    return report(args.db, args.output_dir, args.max_orders, args.order_size,
                  args.window_seconds, args.submit_latency_ms)


if __name__ == "__main__":
    raise SystemExit(main())
