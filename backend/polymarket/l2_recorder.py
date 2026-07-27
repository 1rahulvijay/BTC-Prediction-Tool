"""Forward-only Polymarket L2 recorder for exact depth and queue research.

This process subscribes to the public market WebSocket for current/next BTC 5m and
15m rounds.  It persists full snapshots, incremental price-level changes, trades,
book health, and exact taker VWAP at standard sizes into its own DuckDB.

It never places orders and never reads API credentials.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import duckdb
import websockets

try:
    from l2_book import L2Book
    from live_btc_updown_recorder import discover_rounds
except ImportError:  # module execution from the repository root
    from backend.polymarket.l2_book import L2Book
    from backend.polymarket.live_btc_updown_recorder import discover_rounds


ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("BTC_DATA_DIR", ROOT / "data"))
DEFAULT_DB = Path(os.environ.get("BTC_PM_L2_DB", DATA / "polymarket_l2.duckdb"))
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
DEFAULT_SIZES = (1.0, 10.0, 50.0, 100.0, 500.0)


def _int_timestamp(value) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


class L2Store:
    def __init__(self, path: str | Path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = duckdb.connect(self.path)
        self._init_schema()
        row = self.conn.execute("""SELECT coalesce(max(seq), 0) FROM (
            SELECT seq FROM pm_l2_raw_events UNION ALL
            SELECT seq FROM pm_l2_level_updates UNION ALL
            SELECT seq FROM pm_l2_trades UNION ALL
            SELECT seq FROM pm_l2_book_summaries UNION ALL
            SELECT seq FROM pm_l2_execution_snapshots)""").fetchone()
        self.next_seq = int(row[0]) + 1

    def close(self) -> None:
        self.conn.close()

    def disk_bytes(self) -> int:
        """Approximate active storage including DuckDB's write-ahead log."""
        return sum(Path(candidate).stat().st_size for candidate in (self.path, f"{self.path}.wal")
                   if Path(candidate).exists())

    def _init_schema(self) -> None:
        self.conn.execute("""CREATE TABLE IF NOT EXISTS pm_l2_markets(
            asset_id VARCHAR PRIMARY KEY, market VARCHAR, condition_id VARCHAR, slug VARCHAR,
            horizon INTEGER, outcome VARCHAR, start_ts BIGINT, end_ts BIGINT,
            first_seen_ns BIGINT, last_seen_ns BIGINT)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS pm_l2_raw_events(
            seq BIGINT PRIMARY KEY, recv_ts_ns BIGINT, exchange_ts_ms BIGINT,
            event_type VARCHAR, market VARCHAR, asset_id VARCHAR, payload_json VARCHAR)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS pm_l2_book_levels(
            seq BIGINT, recv_ts_ns BIGINT, exchange_ts_ms BIGINT, asset_id VARCHAR,
            side VARCHAR, level_index INTEGER, price DOUBLE, size DOUBLE)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS pm_l2_level_updates(
            seq BIGINT, recv_ts_ns BIGINT, exchange_ts_ms BIGINT, asset_id VARCHAR,
            side VARCHAR, price DOUBLE, previous_size DOUBLE, new_size DOUBLE,
            best_bid DOUBLE, best_ask DOUBLE, update_hash VARCHAR, applied BOOLEAN)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS pm_l2_trades(
            seq BIGINT, recv_ts_ns BIGINT, exchange_ts_ms BIGINT, asset_id VARCHAR,
            market VARCHAR, aggressor_side VARCHAR, price DOUBLE, size DOUBLE,
            fee_rate_bps DOUBLE)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS pm_l2_book_summaries(
            seq BIGINT, recv_ts_ns BIGINT, exchange_ts_ms BIGINT, event_type VARCHAR,
            asset_id VARCHAR, market VARCHAR, synchronized BOOLEAN, valid BOOLEAN,
            invalid_reason VARCHAR, best_bid DOUBLE, best_ask DOUBLE, best_bid_size DOUBLE,
            best_ask_size DOUBLE, spread DOUBLE, bid_levels INTEGER, ask_levels INTEGER,
            bid_depth DOUBLE, ask_depth DOUBLE, book_hash VARCHAR)""")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS pm_l2_execution_snapshots(
            seq BIGINT, recv_ts_ns BIGINT, exchange_ts_ms BIGINT, asset_id VARCHAR,
            results_json VARCHAR)""")

    def sequence(self) -> int:
        seq = self.next_seq
        self.next_seq += 1
        return seq

    def register_market(self, row: dict, now_ns: int) -> None:
        for outcome, key in (("UP", "up"), ("DOWN", "down")):
            self.conn.execute("""INSERT INTO pm_l2_markets VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(asset_id) DO UPDATE SET market=excluded.market,
                condition_id=excluded.condition_id, slug=excluded.slug, horizon=excluded.horizon,
                outcome=excluded.outcome, start_ts=excluded.start_ts, end_ts=excluded.end_ts,
                last_seen_ns=excluded.last_seen_ns""", [
                str(row[key]), str(row.get("condition_id", "")), str(row.get("condition_id", "")),
                str(row["slug"]), int(row["horizon"]), outcome, int(row["start_ts"]),
                int(row["end_ts"]), int(now_ns), int(now_ns),
            ])

    def raw_event(self, seq: int, recv_ns: int, exchange_ms: int, event_type: str,
                  market: str, asset_id: str, payload: dict) -> None:
        self.conn.execute("INSERT INTO pm_l2_raw_events VALUES (?,?,?,?,?,?,?)", [
            seq, recv_ns, exchange_ms, event_type, market, asset_id,
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
        ])

    def control_event(self, event_type: str, payload: dict | None = None) -> int:
        seq = self.sequence()
        now_ns = time.time_ns()
        self.raw_event(seq, now_ns, 0, event_type, "", "", payload or {})
        return seq

    def full_levels(self, seq: int, recv_ns: int, exchange_ms: int, book: L2Book) -> None:
        rows = []
        for side in ("BUY", "SELL"):
            for index, (price, size) in enumerate(book.sorted_levels(side)):
                rows.append((seq, recv_ns, exchange_ms, book.asset_id, side, index,
                             float(price), float(size)))
        if rows:
            self.conn.executemany("INSERT INTO pm_l2_book_levels VALUES (?,?,?,?,?,?,?,?)", rows)

    def level_update(self, values: tuple) -> None:
        self.conn.execute("INSERT INTO pm_l2_level_updates VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", values)

    def trade(self, values: tuple) -> None:
        self.conn.execute("INSERT INTO pm_l2_trades VALUES (?,?,?,?,?,?,?,?,?)", values)

    def book_outputs(self, seq: int, recv_ns: int, exchange_ms: int, event_type: str,
                     book: L2Book, sizes: tuple[float, ...]) -> None:
        summary = book.summary()
        self.conn.execute("INSERT INTO pm_l2_book_summaries VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
            seq, recv_ns, exchange_ms, event_type, book.asset_id, summary["market"],
            summary["synchronized"], summary["valid"], summary["invalid_reason"],
            summary["best_bid"], summary["best_ask"], summary["best_bid_size"],
            summary["best_ask_size"], summary["spread"], summary["bid_levels"],
            summary["ask_levels"], summary["bid_depth"], summary["ask_depth"], summary["book_hash"],
        ])
        results = []
        for action in ("BUY", "SELL"):
            for requested in sizes:
                result = book.execution_vwap(action, requested)
                results.append(result.to_dict())
        self.conn.execute("INSERT INTO pm_l2_execution_snapshots VALUES (?,?,?,?,?)", [
            seq, recv_ns, exchange_ms, book.asset_id,
            json.dumps(results, separators=(",", ":"), sort_keys=True),
        ])

    def report(self) -> dict:
        tables = ("pm_l2_raw_events", "pm_l2_book_levels", "pm_l2_level_updates",
                  "pm_l2_trades", "pm_l2_book_summaries", "pm_l2_execution_snapshots")
        counts = {table: int(self.conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
                  for table in tables}
        latest = self.conn.execute("""SELECT recv_ts_ns, asset_id, valid, best_bid, best_ask,
            spread FROM pm_l2_book_summaries ORDER BY recv_ts_ns DESC LIMIT 1""").fetchone()
        return {"counts": counts, "latest": latest}


class L2Recorder:
    def __init__(self, store: L2Store, *, sizes: tuple[float, ...] = DEFAULT_SIZES,
                 refresh_seconds: float = 15.0, sample_ms: float = 1000.0,
                 max_db_gb: float = 10.0):
        self.store = store
        self.sizes = tuple(sorted(set(float(value) for value in sizes if value > 0)))
        self.refresh_seconds = max(5.0, float(refresh_seconds))
        self.sample_ns = int(max(100.0, float(sample_ms)) * 1e6)
        self.books: dict[str, L2Book] = {}
        self.metadata: dict[str, dict] = {}
        self.tracked: set[str] = set()
        self.messages = 0
        self.last_output_ns: dict[str, int] = {}
        self.max_db_bytes = int(max(0.0, float(max_db_gb)) * 1024 ** 3)
        self.disk_limit_reached = False

    def emit_outputs(self, seq: int, recv_ns: int, exchange_ms: int, event_type: str,
                     book: L2Book) -> None:
        previous = self.last_output_ns.get(book.asset_id, 0)
        if previous and recv_ns - previous < self.sample_ns:
            return
        self.store.book_outputs(seq, recv_ns, exchange_ms, event_type, book, self.sizes)
        self.last_output_ns[book.asset_id] = recv_ns

    async def discover(self) -> set[str]:
        rounds = await asyncio.to_thread(discover_rounds)
        now = time.time()
        selected = [row for row in rounds
                    if row["start_ts"] <= now + 120 and row["end_ts"] >= now - 30]
        now_ns = time.time_ns()
        assets: set[str] = set()
        for row in selected:
            self.store.register_market(row, now_ns)
            for outcome, key in (("UP", "up"), ("DOWN", "down")):
                asset = str(row[key])
                assets.add(asset)
                self.metadata[asset] = {**row, "outcome": outcome}
        return assets

    async def refresh_subscriptions(self, ws) -> None:
        while True:
            await asyncio.sleep(self.refresh_seconds)
            wanted = await self.discover()
            add = sorted(wanted - self.tracked)
            remove = sorted(self.tracked - wanted)
            if add:
                await ws.send(json.dumps({"assets_ids": add, "operation": "subscribe",
                                          "custom_feature_enabled": True}))
            if remove:
                await ws.send(json.dumps({"assets_ids": remove, "operation": "unsubscribe"}))
            if add or remove:
                print(f"[l2] subscriptions +{len(add)} -{len(remove)} active={len(wanted)}", flush=True)
            self.tracked = wanted

    async def heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(10.0)
            await ws.send("PING")

    def process(self, payload, recv_ns: int | None = None) -> int:
        recv_ns = int(recv_ns or time.time_ns())
        if isinstance(payload, list):
            return sum(self.process(item, recv_ns) for item in payload if isinstance(item, dict))
        if not isinstance(payload, dict):
            return 0
        event_type = str(payload.get("event_type", "unknown"))
        exchange_ms = _int_timestamp(payload.get("timestamp"))
        market = str(payload.get("market", ""))
        asset = str(payload.get("asset_id", ""))
        seq = None
        if event_type != "price_change":
            seq = self.store.sequence()
            self.store.raw_event(seq, recv_ns, exchange_ms, event_type, market, asset, payload)

        if event_type == "book" and asset:
            assert seq is not None
            book = self.books.setdefault(asset, L2Book(asset))
            if exchange_ms and book.last_exchange_ts_ms and exchange_ms < book.last_exchange_ts_ms:
                return 0
            book.load_snapshot(payload.get("bids", []), payload.get("asks", []), market=market,
                               exchange_ts_ms=exchange_ms, recv_ts_ns=recv_ns,
                               book_hash=str(payload.get("hash", "")))
            self.store.full_levels(seq, recv_ns, exchange_ms, book)
            self.emit_outputs(seq, recv_ns, exchange_ms, event_type, book)
            return 1

        if event_type == "price_change":
            changed = 0
            for update in payload.get("price_changes", []):
                update_seq = self.store.sequence()
                update_asset = str(update.get("asset_id", ""))
                if not update_asset:
                    continue
                book = self.books.setdefault(update_asset, L2Book(update_asset))
                side = str(update.get("side", "")).upper()
                price = update.get("price")
                previous = float(book.level_size(side, price)) if side in {"BUY", "SELL"} else 0.0
                was_applicable = (book.synchronized and side in {"BUY", "SELL"} and
                                  (not exchange_ms or exchange_ms >= book.last_exchange_ts_ms))
                try:
                    book.apply_price_change(side, price, update.get("size", 0),
                                            exchange_ts_ms=exchange_ms, recv_ts_ns=recv_ns)
                except ValueError:
                    was_applicable = False
                self.store.level_update((
                    update_seq, recv_ns, exchange_ms, update_asset, side, float(price), previous,
                    float(update.get("size", 0)), float(update.get("best_bid") or 0),
                    float(update.get("best_ask") or 0), str(update.get("hash", "")), was_applicable,
                ))
                if was_applicable:
                    self.emit_outputs(update_seq, recv_ns, exchange_ms, event_type, book)
                changed += 1
            return changed

        if event_type == "last_trade_price" and asset:
            assert seq is not None
            self.store.trade((
                seq, recv_ns, exchange_ms, asset, market, str(payload.get("side", "")).upper(),
                float(payload.get("price", 0)), float(payload.get("size", 0)),
                float(payload.get("fee_rate_bps", 0) or 0),
            ))
            return 1
        return 0

    async def session(self, duration: float = 0.0) -> None:
        wanted = await self.discover()
        if not wanted:
            raise RuntimeError("no current or next BTC 5m/15m token IDs discovered")
        self.tracked = wanted
        async with websockets.connect(WS_URL, ping_interval=None, ping_timeout=None,
                                      open_timeout=15, close_timeout=5, max_size=8 * 1024 * 1024) as ws:
            self.books.clear()
            self.last_output_ns.clear()
            self.store.control_event("connection_start", {"assets": len(wanted)})
            await ws.send(json.dumps({"assets_ids": sorted(wanted), "type": "market",
                                      "custom_feature_enabled": True}))
            print(f"[l2] connected assets={len(wanted)} db={self.store.path}", flush=True)
            heartbeat = asyncio.create_task(self.heartbeat(ws))
            refresh = asyncio.create_task(self.refresh_subscriptions(ws))
            started = time.monotonic()
            try:
                while not duration or time.monotonic() - started < duration:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    except asyncio.TimeoutError:
                        continue
                    if raw in ("PONG", b"PONG"):
                        continue
                    payload = json.loads(raw)
                    self.messages += 1
                    self.process(payload)
                    if self.messages % 100 == 0:
                        report = self.store.report()
                        print(f"[l2] messages={self.messages} rows={report['counts']}", flush=True)
                    if self.messages % 1000 == 0 and self.max_db_bytes:
                        used = self.store.disk_bytes()
                        if used >= self.max_db_bytes:
                            self.disk_limit_reached = True
                            print(f"[l2] disk safety cap reached: {used / 1024**3:.2f} GB; "
                                  "stopping exact-L2 recorder cleanly", flush=True)
                            return
            finally:
                heartbeat.cancel()
                refresh.cancel()
                await asyncio.gather(heartbeat, refresh, return_exceptions=True)
                self.store.control_event("connection_end", {"messages": self.messages})


async def record_forever(recorder: L2Recorder, duration: float = 0.0) -> None:
    backoff = 2.0
    started = time.monotonic()
    while True:
        remaining = max(0.0, duration - (time.monotonic() - started)) if duration else 0.0
        if duration and remaining <= 0:
            return
        try:
            await recorder.session(remaining)
            if duration or recorder.disk_limit_reached:
                return
            backoff = 2.0
        except KeyboardInterrupt:
            return
        except Exception as exc:
            print(f"[l2] disconnected: {type(exc).__name__}: {exc}; retry in {backoff:.0f}s", flush=True)
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 1.7)


def _parse_sizes(value: str) -> tuple[float, ...]:
    values = tuple(float(item.strip()) for item in value.split(",") if item.strip())
    if not values or any(item <= 0 for item in values):
        raise argparse.ArgumentTypeError("sizes must be a comma-separated list of positive numbers")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--sizes", type=_parse_sizes, default=DEFAULT_SIZES)
    parser.add_argument("--refresh-seconds", type=float, default=15.0)
    parser.add_argument("--sample-ms", type=float, default=1000.0,
                        help="minimum interval between calculated book/VWAP states per token")
    parser.add_argument("--duration", type=float, default=0.0,
                        help="stop after N seconds; zero records continuously")
    parser.add_argument("--report", action="store_true")
    parser.add_argument("--max-db-gb", type=float,
                        default=float(os.environ.get("BTC_PM_L2_MAX_GB", "10")),
                        help="stop cleanly when DB+WAL reaches this size; zero disables the guard")
    args = parser.parse_args()
    store = L2Store(args.db)
    try:
        if args.report:
            print(json.dumps(store.report(), indent=2, default=str))
            return 0
        recorder = L2Recorder(store, sizes=args.sizes, refresh_seconds=args.refresh_seconds,
                              sample_ms=args.sample_ms, max_db_gb=args.max_db_gb)
        asyncio.run(record_forever(recorder, max(0.0, args.duration)))
        print(json.dumps(store.report(), indent=2, default=str))
        return 0
    finally:
        store.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[l2] stopped", flush=True)
