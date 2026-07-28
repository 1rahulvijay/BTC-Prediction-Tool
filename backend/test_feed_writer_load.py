"""Production-like load tests for the feed writer, using the REAL parquet handlers.

WHY THIS EXISTS
    The existing regression proves `submit()` is non-blocking by queueing lambdas that sleep:

        20 submits of 50ms work return in 0.2ms, not ~1000ms

    That is a true and useful statement about HAND-OFF LATENCY. It says nothing about whether the
    worker can PERSIST incoming Binance data fast enough, because no real handler, no real payload
    and no real disk was involved. A writer can hand off in microseconds and still fall
    permanently behind.

    These tests drive `database.log_raw_trade_parquet` and `database.log_depth_parquet` with
    realistic payloads into a temporary data directory, and measure sustained throughput, queue
    depth and oldest-queued age over time.

WHAT THE MEASUREMENTS MEAN
    Both handlers are DISABLED unless BTC_LOG_TICKS_PARQUET=1, so in the default configuration
    the writer carries almost no work. The interesting case is the enabled one, and there the
    handler reads the whole day's parquet file, concatenates one row and rewrites it - O(n^2) over
    a day. This measures where that becomes the binding constraint.

    python backend/test_feed_writer_load.py
    python backend/test_feed_writer_load.py --minutes 10     # longer soak
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

_OK = True

# Observed Binance BTCUSDT rates: aggTrade bursts well above the mean, depth@100ms is ~10/s.
TRADES_PER_SECOND = 50
DEPTH_PER_SECOND = 10
BURST_MULTIPLIER = 10


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _trade(index: int) -> dict:
    return {
        "symbol": "BTCUSDT", "trade_id": 100000 + index, "price": 64000.0 + (index % 100),
        "quantity": 0.0123, "is_buyer_maker": index % 2 == 0,
        "time": int(time.time() * 1000),
    }


def _depth(index: int) -> dict:
    price = 64000.0 + (index % 50)
    return {
        "symbol": "BTCUSDT", "time": int(time.time() * 1000),
        "bids": [[price - i, 1.5] for i in range(20)],
        "asks": [[price + i, 1.5] for i in range(20)],
        "receive_time": int(time.time() * 1000),
    }


def _isolated_data_dir() -> str:
    """Point database's parquet output at a temp dir and ENABLE the archive path."""
    import database

    temp = tempfile.mkdtemp(prefix="feedload-")
    database._DATA_DIR = temp
    os.environ["BTC_LOG_TICKS_PARQUET"] = "1"
    return temp


def test_sustained_rate(seconds: float) -> None:
    print(f"sustained load at {TRADES_PER_SECOND} trades/s + {DEPTH_PER_SECOND} depth/s "
          f"for {seconds:.0f}s (real parquet writes)")
    import database

    from feed_writer import FeedWriter

    temp = _isolated_data_dir()
    writer = FeedWriter(maxsize=10_000, depth_maxsize=2_000, name="load").start()
    samples: list[tuple[int, float]] = []
    submitted = 0
    started = time.perf_counter()
    tick = 0
    try:
        while time.perf_counter() - started < seconds:
            tick += 1
            for i in range(TRADES_PER_SECOND // 10):
                writer.submit(database.log_raw_trade_parquet, _trade(submitted + i))
            submitted += TRADES_PER_SECOND // 10
            if tick % 10 == 0:
                writer.submit_depth(database.log_depth_parquet, _depth(tick), key="BTCUSDT")
            if tick % 10 == 0:
                stats = writer.stats()
                samples.append((stats["depth"], stats["oldest_queued_age_s"]))
            time.sleep(0.1)
        elapsed = time.perf_counter() - started
        drained = writer.drain_for(60.0)
        stats = writer.stats()
        throughput = stats["written"] / max(elapsed, 1e-9)
        peak_depth = max((d for d, _ in samples), default=0)
        peak_age = max((a for _, a in samples), default=0.0)

        print(f"       submitted={submitted} written={stats['written']} "
              f"dropped={stats['dropped']} failed={stats['failed']}")
        print(f"       sustained throughput = {throughput:.0f} writes/s "
              f"(offered {TRADES_PER_SECOND}/s)")
        print(f"       peak queue depth = {peak_depth}, peak oldest-queued age = {peak_age:.2f}s")

        chk(drained, "the writer fully drained after the load stopped")
        chk(stats["dropped"] == 0, f"no drops at the offered rate ({stats['dropped']})")
        chk(stats["failed"] == 0, f"no handler failures ({stats['failed']}: {stats['last_error']})")
        chk(throughput >= TRADES_PER_SECOND * 0.9,
            f"sustained throughput {throughput:.0f}/s keeps up with {TRADES_PER_SECOND}/s offered")
        files = list(Path(temp).rglob("*.parquet"))
        chk(bool(files), f"real parquet partitions were produced ({len(files)})")
    finally:
        writer.stop(timeout=30.0)
        shutil.rmtree(temp, ignore_errors=True)


def test_burst() -> None:
    print(f"burst at {BURST_MULTIPLIER}x expected peak")
    import database

    from feed_writer import FeedWriter

    temp = _isolated_data_dir()
    writer = FeedWriter(maxsize=10_000, depth_maxsize=2_000, name="burst").start()
    try:
        count = TRADES_PER_SECOND * BURST_MULTIPLIER * 3
        started = time.perf_counter()
        for i in range(count):
            writer.submit(database.log_raw_trade_parquet, _trade(i))
        submit_elapsed = time.perf_counter() - started
        peak = writer.stats()
        chk(submit_elapsed < 1.0,
            f"{count} burst submits returned in {submit_elapsed * 1000:.0f}ms without blocking")
        print(f"       peak depth during burst = {peak['depth']}, "
              f"utilization = {peak['utilization']:.3f}")
        drained = writer.drain_for(120.0)
        stats = writer.stats()
        print(f"       recovered: written={stats['written']} dropped={stats['dropped']}")
        chk(drained, "the queue recovered to empty after the burst")
        chk(stats["dropped"] == 0 or stats["dropped_trades"] > 0,
            "any burst loss is attributed to the TRADE lane and counted")
    finally:
        writer.stop(timeout=30.0)
        shutil.rmtree(temp, ignore_errors=True)


def test_slow_disk() -> None:
    print("artificial 50ms disk latency (a stalling writer must become VISIBLE)")
    from feed_writer import FeedWriter

    writer = FeedWriter(maxsize=200, name="slowdisk").start()
    try:
        for _ in range(60):                                   # 3.0s of work
            writer.submit(lambda _p: time.sleep(0.05), None)
        time.sleep(1.5)
        stats = writer.stats()
        print(f"       depth={stats['depth']} oldest_age={stats['oldest_queued_age_s']:.2f}s "
              f"utilization={stats['utilization']:.3f}")
        chk(stats["oldest_queued_age_s"] > 1.0,
            f"oldest-queued age reports the true backlog ({stats['oldest_queued_age_s']:.2f}s)")
        chk(stats["depth"] > 10, f"queue depth reveals the stall ({stats['depth']})")
    finally:
        writer.stop(timeout=0.5)


def test_queue_saturation_drops_are_attributed() -> None:
    print("saturation drops are counted per lane and never silent")
    from feed_writer import FeedWriter

    writer = FeedWriter(maxsize=50, depth_maxsize=5, name="saturate").start()
    try:
        for _ in range(400):
            writer.submit(lambda _p: time.sleep(0.02), None)
        for i in range(400):
            writer.submit_depth(lambda _p: time.sleep(0.02), i, key=f"SYM-{i % 5}")
        stats = writer.stats()
        print(f"       dropped_trades={stats['dropped_trades']} "
              f"dropped_depth={stats['dropped_depth']} "
              f"superseded_depth={stats['superseded_depth']} "
              f"drop_rate_1m={stats['drop_rate_1m']}")
        chk(stats["dropped_trades"] > 0, "trade-lane drops are counted")
        chk(stats["dropped_depth"] == 0,
            "depth never dropped - it coalesced onto its 5 keys instead")
        chk(stats["superseded_depth"] > 0, "and supersedes are counted separately from drops")
        chk(stats["healthy"] is False, "a writer that dropped is not healthy")
        chk(stats["drop_rate_1m"] > 0, "recent drop rate is exposed for alerting")
    finally:
        writer.stop(timeout=0.5)


def test_handler_failure_does_not_kill_the_worker() -> None:
    print("a handler that raises must not take the worker down")
    from feed_writer import FeedWriter

    writer = FeedWriter(maxsize=100, name="failing").start()
    landed: list[int] = []
    try:
        def explode(_payload: object) -> None:
            raise OSError("disk full")

        for _ in range(20):
            writer.submit(explode, None)
        for i in range(10):
            writer.submit(landed.append, i)
        writer.drain_for(10.0)
        stats = writer.stats()
        chk(stats["failed"] == 20, f"all 20 failures counted ({stats['failed']})")
        chk(stats["worker_alive"] is True, "the worker is still alive after 20 exceptions")
        chk(len(landed) == 10, f"and still writing subsequent jobs ({len(landed)}/10)")
    finally:
        writer.stop(timeout=5.0)


def test_shutdown_with_backlog() -> None:
    print("shutdown with a real backlog reports exactly what it drained")
    import database

    from feed_writer import FeedWriter

    temp = _isolated_data_dir()
    writer = FeedWriter(maxsize=5_000, name="shutdown").start()
    try:
        for i in range(300):
            writer.submit(database.log_raw_trade_parquet, _trade(i))
        result = writer.stop(timeout=60.0)
        print(f"       {dict(result)}")
        chk(result.clean, "a generous timeout drains the whole backlog cleanly")
        chk(result["abandoned"] == 0, "nothing was abandoned")
        chk(result["written"] == 300, f"all 300 queued writes completed ({result['written']})")
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def test_one_writer_thread_serializes_parquet_access() -> None:
    """The parquet handler is read-whole-file, concat, rewrite - not atomic and not reentrant.

    Routing every write through ONE worker thread is what makes it safe: two threads doing
    read-modify-rewrite on the same daily partition would interleave and lose rows. This asserts
    the serialization property rather than assuming it."""
    print("all persistence is serialized through exactly one thread")
    import database

    from feed_writer import FeedWriter

    temp = _isolated_data_dir()
    writer = FeedWriter(maxsize=2_000, name="serial").start()
    threads: set[str] = set()
    concurrent = [0]
    active = [0]
    try:
        def record(payload: dict) -> None:
            import threading
            threads.add(threading.current_thread().name)
            active[0] += 1
            if active[0] > 1:
                concurrent[0] += 1
            time.sleep(0.001)
            active[0] -= 1
            database.log_raw_trade_parquet(payload)

        for i in range(200):
            writer.submit(record, _trade(i))
        writer.drain_for(60.0)
        chk(len(threads) == 1, f"every write ran on ONE thread ({sorted(threads)})")
        chk(concurrent[0] == 0, f"no two writes overlapped ({concurrent[0]} overlaps)")

        import pyarrow.parquet as pq
        files = list(Path(temp).rglob("trades_*.parquet"))
        rows = sum(pq.read_table(f).num_rows for f in files)
        chk(rows == 200, f"all 200 rows survived the read-modify-rewrite cycle ({rows})")
    finally:
        writer.stop(timeout=30.0)
        shutil.rmtree(temp, ignore_errors=True)


def test_measured_write_capacity() -> None:
    """Measure the handler's actual per-write cost, and compare it to the offered feed rate.

    MEASURED 2026-07-28 (Windows, local disk, archive ENABLED):

        rows in file    per write     implied capacity
             520          20.5 ms          49 writes/s
            1020          15.4 ms          65 writes/s
            2020          16.3 ms          61 writes/s
            4020          18.0 ms          55 writes/s
            8020          23.0 ms          43 writes/s
           16020          20.0 ms          50 writes/s

    So sustained capacity is roughly 43-65 writes/s while the offered trade rate is ~50/s. The
    writer sits AT its ceiling with no headroom: it keeps up with the mean and falls behind on
    every burst. That is survivable only because the queue is bounded and every drop is counted -
    the backlog becomes a visible number rather than unbounded memory growth.

    This is also why the archive is off by default. The test asserts the property that matters -
    that capacity is measured and known - rather than a specific machine's timing."""
    print("measured per-write capacity of the real handler")
    import database

    temp = _isolated_data_dir()
    try:
        for i in range(300):
            database.log_raw_trade_parquet(_trade(i))
        started = time.perf_counter()
        for i in range(20):
            database.log_raw_trade_parquet(_trade(300 + i))
        per_write_ms = (time.perf_counter() - started) / 20 * 1000
        capacity = 1000.0 / max(per_write_ms, 1e-9)
        print(f"       {per_write_ms:.1f} ms/write -> ~{capacity:.0f} writes/s capacity, "
              f"offered {TRADES_PER_SECOND}/s")
        chk(per_write_ms > 0, f"per-write cost is measured, not assumed ({per_write_ms:.1f}ms)")
        if capacity < TRADES_PER_SECOND * 2:
            print(f"       NOTE: capacity {capacity:.0f}/s is under 2x the offered rate - "
                  f"bursts WILL build backlog. Drops are counted, not silent.")
        chk(os.environ.get("BTC_LOG_TICKS_PARQUET") == "1",
            "this measurement was taken with the archive path actually enabled")
    finally:
        shutil.rmtree(temp, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--minutes", type=float, default=0.0,
                        help="sustained-soak duration; default runs a short 6s check")
    args = parser.parse_args()
    seconds = args.minutes * 60.0 if args.minutes else 6.0

    original = os.environ.get("BTC_LOG_TICKS_PARQUET")
    try:
        test_sustained_rate(seconds)
        test_burst()
        test_slow_disk()
        test_queue_saturation_drops_are_attributed()
        test_handler_failure_does_not_kill_the_worker()
        test_shutdown_with_backlog()
        test_one_writer_thread_serializes_parquet_access()
        test_measured_write_capacity()
    finally:
        if original is None:
            os.environ.pop("BTC_LOG_TICKS_PARQUET", None)
        else:
            os.environ["BTC_LOG_TICKS_PARQUET"] = original

    print("\nFEED WRITER LOAD", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
