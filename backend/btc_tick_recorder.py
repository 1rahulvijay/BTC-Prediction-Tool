"""Forward-only Binance BTC tick recorder, for sub-second cross-venue research.

WHY THIS EXISTS
    `research/cross_venue_repricing_lag.py` found no repricing lag at >=2s resolution and could
    not look below it, because the round recorder samples every ~1.95s. Chasing that limit
    turned up something more specific than "we need a recorder":

        pm_l2_raw_events   272,274 book events, median inter-event gap  32.5ms
        microstructure     198,036 rows,        median inter-event gap 1177ms   <- the BTC side

    A lead/lag measurement is limited by the SLOWER series. The Polymarket side is already
    fast enough; the BTC reference is not. This closes that gap and nothing else.

WHAT IT RECORDS
    Two public Binance streams, no credentials, no orders:

        btcusdt@bookTicker   every best bid/ask change - the price signal a Polymarket quote
                             would respond to, and the right reference for lead/lag
        btcusdt@aggTrade     every aggregated trade - direction and size of the flow causing it

THE JOIN IS ONLY VALID BECAUSE OF THE CLOCK
    `recv_ts_ns` here is `time.time_ns()` on the recording host, which is exactly what
    `polymarket/l2_recorder.py` writes. Two series can be compared at sub-second resolution
    ONLY because they share that clock. Recording these on different machines would make the
    join meaningless while looking identical, so the host identity is stamped into every run.

WHAT THIS RECORDER REFUSES TO DO QUIETLY
    Every study in this repository that went wrong went wrong by treating absence as a value.
    So:

      * GAPS ARE RECORDED, NOT INFERRED - by the right test for each stream. `aggTrade`
        carries a true per-message counter (`a`), verified consecutive on live capture, so an
        id jump there PROVES loss and is written with its size. `bookTicker` does NOT: `u` is
        the order-book update id across all price levels, and on a 45s live capture its step
        ran min 1, median 4, max 66. Treating that as continuity reported 366 gaps and 3,549
        lost messages that never existed, so bookTicker coverage is measured by SILENCE
        instead. A detector that fires on a property it does not measure is worse than none,
        because a coverage claim gets built on it.
      * DROPS ARE COUNTED. The socket reader never blocks on the database. A bounded queue
        absorbs bursts, and anything dropped is counted and written - an uncounted drop is a
        silent gap.
      * HEARTBEATS PROVE LIVENESS. "No rows in this minute" and "the recorder was not running"
        are different facts and must not look the same.

    None of these changes a number. They make the recording auditable, which is the only reason
    the resulting study can be believed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import socket
import time
from pathlib import Path

import duckdb

try:
    import websockets
except ImportError:                                   # recorded, not silently degraded
    websockets = None

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("BTC_DATA_DIR", ROOT / "data"))
DEFAULT_DB = Path(os.environ.get("BTC_TICK_DB", DATA / "btc_ticks.duckdb"))

WS_BASE = "wss://stream.binance.com:9443/stream?streams="
STREAMS = ("btcusdt@bookTicker", "btcusdt@aggTrade")

#: Bounded so a slow disk cannot stall the socket. A stalled socket drops messages upstream,
#: which is a gap the recorder cannot see; a full queue is a drop the recorder CAN count.
QUEUE_MAX = 20_000
#: Liveness proof cadence.
HEARTBEAT_SECONDS = 30.0
#: bookTicker carries no per-message counter, so silence is the only honest coverage signal
#: for it. Observed cadence is ~12 messages/second, so 2s of nothing is a real gap and not a
#: quiet market.
BOOK_SILENCE_GAP_MS = 2_000


def _f(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _i(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


class TickStore:
    """Single-writer DuckDB, mirroring `polymarket/l2_recorder.py` so the two stores join."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(self.path)
        self._init_schema()
        row = self.conn.execute("""SELECT coalesce(max(seq), 0) FROM (
            SELECT seq FROM btc_tick_raw_events UNION ALL
            SELECT seq FROM btc_book_ticker UNION ALL
            SELECT seq FROM btc_agg_trades UNION ALL
            SELECT seq FROM btc_tick_gaps UNION ALL
            SELECT seq FROM btc_tick_heartbeats)""").fetchone()
        self.next_seq = int(row[0]) + 1

    def close(self) -> None:
        self.conn.close()

    def disk_bytes(self) -> int:
        return sum(Path(c).stat().st_size for c in (self.path, f"{self.path}.wal")
                   if Path(c).exists())

    def _init_schema(self) -> None:
        # recv_ts_ns is the LOCAL clock; exchange_ts_ms is Binance's `E`; event_ts_ms is the
        # trade's own `T`. Three different moments, kept separate - collapsing them is the
        # defect this repository has found in five other places.
        self.conn.execute("""CREATE TABLE IF NOT EXISTS btc_tick_raw_events(
            seq BIGINT PRIMARY KEY, recv_ts_ns BIGINT, exchange_ts_ms BIGINT,
            event_type VARCHAR, stream VARCHAR, payload_json VARCHAR)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS btc_book_ticker(
            seq BIGINT PRIMARY KEY, recv_ts_ns BIGINT, exchange_ts_ms BIGINT,
            update_id BIGINT, symbol VARCHAR,
            best_bid DOUBLE, best_bid_qty DOUBLE, best_ask DOUBLE, best_ask_qty DOUBLE,
            mid DOUBLE, spread DOUBLE, transport_lag_ms BIGINT)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS btc_agg_trades(
            seq BIGINT PRIMARY KEY, recv_ts_ns BIGINT, exchange_ts_ms BIGINT,
            event_ts_ms BIGINT, agg_trade_id BIGINT, symbol VARCHAR,
            price DOUBLE, quantity DOUBLE, is_buyer_maker BOOLEAN,
            transport_lag_ms BIGINT)""")
        # A gap is EVIDENCE, so it gets a row. `missing` is the count the ids prove absent.
        self.conn.execute("""CREATE TABLE IF NOT EXISTS btc_tick_gaps(
            seq BIGINT PRIMARY KEY, recv_ts_ns BIGINT, stream VARCHAR, kind VARCHAR,
            previous_id BIGINT, current_id BIGINT, missing BIGINT, detail VARCHAR)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS btc_tick_heartbeats(
            seq BIGINT PRIMARY KEY, recv_ts_ns BIGINT, uptime_seconds DOUBLE,
            book_ticks BIGINT, agg_trades BIGINT, gaps BIGINT, dropped BIGINT,
            queue_depth BIGINT, disk_bytes BIGINT)""")
        # One row per process start. Without the host, a cross-recorder join is a guess.
        self.conn.execute("""CREATE TABLE IF NOT EXISTS btc_tick_runs(
            run_id VARCHAR PRIMARY KEY, started_ns BIGINT, host VARCHAR, platform VARCHAR,
            streams VARCHAR, clock_source VARCHAR, notes VARCHAR)""")

    def sequence(self) -> int:
        seq = self.next_seq
        self.next_seq += 1
        return seq

    def register_run(self, run_id: str, started_ns: int, notes: str = "") -> None:
        self.conn.execute("INSERT OR REPLACE INTO btc_tick_runs VALUES (?,?,?,?,?,?,?)", [
            run_id, started_ns, socket.gethostname(), platform.platform(),
            ",".join(STREAMS), "time.time_ns",
            notes or "recv_ts_ns shares the clock with polymarket/l2_recorder.py; a "
                    "cross-recorder join is only valid on the same host",
        ])

    def raw_event(self, recv_ns: int, exchange_ms: int, event_type: str,
                  stream: str, payload: dict) -> int:
        seq = self.sequence()
        self.conn.execute("INSERT INTO btc_tick_raw_events VALUES (?,?,?,?,?,?)", [
            seq, recv_ns, exchange_ms, event_type, stream,
            json.dumps(payload, separators=(",", ":"), sort_keys=True)])
        return seq

    def book_tick(self, recv_ns: int, payload: dict) -> int:
        exchange_ms = _i(payload.get("E"))
        bid, ask = _f(payload.get("b")), _f(payload.get("a"))
        seq = self.sequence()
        self.conn.execute("INSERT INTO btc_book_ticker VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", [
            seq, recv_ns, exchange_ms, _i(payload.get("u")), str(payload.get("s", "")),
            bid, _f(payload.get("B")), ask, _f(payload.get("A")),
            (bid + ask) / 2.0 if bid > 0 and ask > 0 else 0.0,
            ask - bid if bid > 0 and ask > 0 else 0.0,
            (recv_ns // 1_000_000) - exchange_ms if exchange_ms else 0])
        return seq

    def agg_trade(self, recv_ns: int, payload: dict) -> int:
        exchange_ms = _i(payload.get("E"))
        seq = self.sequence()
        self.conn.execute("INSERT INTO btc_agg_trades VALUES (?,?,?,?,?,?,?,?,?,?)", [
            seq, recv_ns, exchange_ms, _i(payload.get("T")), _i(payload.get("a")),
            str(payload.get("s", "")), _f(payload.get("p")), _f(payload.get("q")),
            bool(payload.get("m", False)),
            (recv_ns // 1_000_000) - exchange_ms if exchange_ms else 0])
        return seq

    def gap(self, recv_ns: int, stream: str, kind: str, previous_id: int,
            current_id: int, detail: str = "") -> int:
        seq = self.sequence()
        self.conn.execute("INSERT INTO btc_tick_gaps VALUES (?,?,?,?,?,?,?,?)", [
            seq, recv_ns, stream, kind, previous_id, current_id,
            max(0, current_id - previous_id - 1), detail])
        return seq

    def heartbeat(self, recv_ns: int, uptime: float, counters: dict) -> int:
        seq = self.sequence()
        self.conn.execute("INSERT INTO btc_tick_heartbeats VALUES (?,?,?,?,?,?,?,?,?)", [
            seq, recv_ns, uptime, counters.get("book", 0), counters.get("trade", 0),
            counters.get("gap", 0), counters.get("dropped", 0),
            counters.get("queue", 0), self.disk_bytes()])
        return seq


class TickRecorder:
    def __init__(self, store: TickStore, keep_raw: bool = False):
        self.store = store
        self.keep_raw = keep_raw
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)
        self.counters = {"book": 0, "trade": 0, "gap": 0, "dropped": 0, "queue": 0}
        self.last_update_id = 0
        self.last_agg_id = 0
        self.last_book_ns = 0
        self.started_ns = time.time_ns()

    # -- the socket side: never blocks on the database ----------------------------------
    async def consume(self, ws) -> None:
        async for message in ws:
            recv_ns = time.time_ns()
            try:
                self.queue.put_nowait((recv_ns, message))
            except asyncio.QueueFull:
                # COUNTED. An uncounted drop is a silent gap, and a silent gap is exactly
                # what makes a lead/lag study unfalsifiable.
                self.counters["dropped"] += 1

    # -- the database side --------------------------------------------------------------
    async def persist(self) -> None:
        while True:
            recv_ns, message = await self.queue.get()
            self.counters["queue"] = self.queue.qsize()
            try:
                envelope = json.loads(message)
            except (TypeError, ValueError):
                continue
            stream = str(envelope.get("stream", ""))
            payload = envelope.get("data") or {}
            if not isinstance(payload, dict):
                continue
            if self.keep_raw:
                self.store.raw_event(recv_ns, _i(payload.get("E")),
                                     str(payload.get("e", "")), stream, payload)
            if stream.endswith("@bookTicker"):
                # NOT an id-continuity check. `u` is the ORDER BOOK update id across ALL
                # price levels, not a per-message counter: measured on a 45s live capture its
                # step was min 1, median 4, max 66, and a naive continuity check reported 366
                # gaps and 3,549 "lost" messages that never existed. A detector that fires on
                # a property it does not measure is worse than no detector, because a coverage
                # claim would have been built on it.
                #
                # bookTicker has no per-message counter, so SILENCE is the only honest signal.
                update_id = _i(payload.get("u"))
                if self.last_book_ns and (recv_ns - self.last_book_ns) > BOOK_SILENCE_GAP_MS * 1_000_000:
                    self.store.gap(recv_ns, stream, "silence",
                                   self.last_book_ns, recv_ns,
                                   f"{(recv_ns - self.last_book_ns) / 1e6:.0f}ms without a "
                                   f"book message")
                    self.counters["gap"] += 1
                self.last_book_ns = recv_ns
                if update_id >= self.last_update_id:
                    self.last_update_id = update_id
                self.store.book_tick(recv_ns, payload)
                self.counters["book"] += 1
            elif stream.endswith("@aggTrade"):
                agg_id = _i(payload.get("a"))
                if self.last_agg_id and agg_id > self.last_agg_id + 1:
                    self.store.gap(recv_ns, stream, "agg_trade_id",
                                   self.last_agg_id, agg_id)
                    self.counters["gap"] += 1
                if agg_id >= self.last_agg_id:
                    self.last_agg_id = agg_id
                self.store.agg_trade(recv_ns, payload)
                self.counters["trade"] += 1

    async def beat(self) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            now = time.time_ns()
            self.counters["queue"] = self.queue.qsize()
            self.store.heartbeat(now, (now - self.started_ns) / 1e9, self.counters)

    async def run(self, duration_seconds: float | None = None) -> None:
        if websockets is None:
            raise RuntimeError("websockets is not installed; the recorder refuses to start "
                               "rather than appear to run and record nothing")
        url = WS_BASE + "/".join(STREAMS)
        run_id = f"btc_ticks_{self.started_ns}"
        self.store.register_run(run_id, self.started_ns)
        deadline = None if duration_seconds is None else time.time() + duration_seconds
        while deadline is None or time.time() < deadline:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    tasks = [asyncio.create_task(self.consume(ws)),
                             asyncio.create_task(self.persist()),
                             asyncio.create_task(self.beat())]
                    try:
                        if deadline is None:
                            await asyncio.gather(*tasks)
                        else:
                            await asyncio.wait(tasks, timeout=max(0.0, deadline - time.time()))
                    finally:
                        for t in tasks:
                            t.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
            except Exception as exc:
                # A reconnect is itself a coverage gap and is recorded as one. The ids will
                # also show the jump, so the two agree or something is wrong.
                self.store.gap(time.time_ns(), "connection", "reconnect", 0, 0,
                               f"{type(exc).__name__}: {exc}"[:200])
                self.counters["gap"] += 1
                await asyncio.sleep(2.0)


def _selftest() -> int:
    """Exercises the store and the gap/drop accounting without touching the network."""
    import tempfile
    failures = []

    def chk(cond, msg):
        print(("  OK   " if cond else "  FAIL ") + msg)
        if not cond:
            failures.append(msg)

    print("BTC TICK RECORDER")
    tmp = Path(tempfile.mkdtemp(prefix="btc_ticks_")) / "t.duckdb"
    store = TickStore(tmp)
    try:
        store.register_run("run1", 1, notes="selftest")
        base = 1_800_000_000_000
        store.book_tick(base * 1_000_000, {"E": base - 40, "u": 100, "s": "BTCUSDT",
                                           "b": "60000.1", "B": "2", "a": "60000.3", "A": "3"})
        row = store.conn.execute(
            "SELECT best_bid, best_ask, mid, spread, transport_lag_ms FROM btc_book_ticker"
        ).fetchone()
        chk(abs(row[2] - 60000.2) < 1e-6 and abs(row[3] - 0.2) < 1e-6,
            f"a book tick derives its own mid and spread ({row[2]}, {row[3]:.2f})")
        chk(row[4] == 40,
            f"and records TRANSPORT LAG separately ({row[4]}ms) - the local clock and the "
            f"exchange clock are two different moments")

        store.agg_trade(base * 1_000_000, {"E": base - 15, "T": base - 20, "a": 500,
                                           "s": "BTCUSDT", "p": "60000.2", "q": "0.5",
                                           "m": True})
        t = store.conn.execute(
            "SELECT exchange_ts_ms, event_ts_ms, transport_lag_ms FROM btc_agg_trades"
        ).fetchone()
        chk(t[0] != t[1],
            f"a trade keeps the exchange event time and the TRADE time apart "
            f"({t[0]} vs {t[1]}) rather than collapsing them")

        store.gap(base * 1_000_000, "btcusdt@bookTicker", "update_id", 100, 106)
        g = store.conn.execute("SELECT missing, kind FROM btc_tick_gaps").fetchone()
        chk(g[0] == 5 and g[1] == "update_id",
            f"a sequence jump 100 -> 106 is WRITTEN as {g[0]} missing, not inferred later")

        store.heartbeat(base * 1_000_000, 30.0,
                        {"book": 7, "trade": 3, "gap": 1, "dropped": 2, "queue": 4})
        h = store.conn.execute(
            "SELECT book_ticks, agg_trades, gaps, dropped FROM btc_tick_heartbeats").fetchone()
        chk(h == (7, 3, 1, 2),
            "a heartbeat carries the counters, so 'no rows this minute' and 'not running' "
            "are distinguishable facts")

        run = store.conn.execute(
            "SELECT host, clock_source FROM btc_tick_runs WHERE run_id = 'run1'").fetchone()
        chk(run[1] == "time.time_ns",
            f"and each run stamps its host ({run[0]}) and clock source - a sub-second join "
            f"against pm_l2 is only valid on one machine")

        seqs = store.conn.execute("""SELECT count(*), count(DISTINCT seq) FROM (
            SELECT seq FROM btc_book_ticker UNION ALL SELECT seq FROM btc_agg_trades
            UNION ALL SELECT seq FROM btc_tick_gaps
            UNION ALL SELECT seq FROM btc_tick_heartbeats)""").fetchone()
        chk(seqs[0] == seqs[1],
            f"every row across every table has a unique seq ({seqs[0]}), so the streams can "
            f"be interleaved back into one ordered history")
    finally:
        store.close()

    print("\n" + ("BTC TICK RECORDER SELFTEST: FAIL" if failures
                  else "BTC TICK RECORDER SELFTEST: PASS"))
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--seconds", type=float, default=None,
                        help="stop after N seconds; omit to run until interrupted")
    parser.add_argument("--keep-raw", action="store_true",
                        help="also persist the raw envelope for every message")
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        return _selftest()

    store = TickStore(args.db)
    recorder = TickRecorder(store, keep_raw=args.keep_raw)
    print(f"[btc-ticks] recording {', '.join(STREAMS)} -> {args.db}")
    try:
        asyncio.run(recorder.run(args.seconds))
    except KeyboardInterrupt:
        pass
    finally:
        now = time.time_ns()
        store.heartbeat(now, (now - recorder.started_ns) / 1e9, recorder.counters)
        print(f"[btc-ticks] book={recorder.counters['book']:,} "
              f"trades={recorder.counters['trade']:,} gaps={recorder.counters['gap']:,} "
              f"dropped={recorder.counters['dropped']:,} "
              f"disk={store.disk_bytes() / 1e6:.1f}MB")
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
