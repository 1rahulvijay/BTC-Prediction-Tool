"""Binance USD-M FUTURES streams. Separate host from spot, and the instrument actually traded.

WHY THIS MODULE EXISTS
    `stream.binance.com` is SPOT. The paper lane trades the PERPETUAL, and five blocked research
    lanes need data that exists only on the futures venue:

        LIQUIDATION_EXHAUSTION_V1        forced-order feed
        POSITIONING_STATE_MACHINE_V1     open interest
        FUNDING_OI_CROWDING_V1           open interest + funding
        FUNDING_BASIS_CARRY_V1           the ACTUAL paid rate and its timestamp
        MARK_INDEX_LAST_DISLOCATION_V1   mark and index as separate series

    Recording spot alone would have left all five exactly as blocked as they are today, while
    looking like progress. Basis also needs BOTH legs - a perp-spot spread cannot be computed
    from one venue.

FUNDING IS READ, NOT ASSUMED
    `markPrice@1s` carries the funding rate and the NEXT funding time. The interval is not
    always 8h and Binance says so; a carry study that hardcodes 8h is measuring a fiction. The
    schedule is recorded as reported so the analysis can read it rather than assume it.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
import urllib.request

import pyarrow as pa
import pyarrow.parquet as pq

from .storage import PartitionWriter, write_status
from .streams import (
    DEPTH_SCHEMA, DEPTH_SNAPSHOT_SCHEMA, TRADE_SCHEMA, _ws_forever,
    classify_futures_depth, record_depth_snapshot,
)

FUT_WS = "wss://fstream.binance.com/ws"
FUT_REST = "https://fapi.binance.com/fapi/v1"

MARK_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("event_ms", pa.int64()),
    ("symbol", pa.string()),
    ("source", pa.string()),
    ("mark_price", pa.float64()),
    ("index_price", pa.float64()),
    ("settlement_price", pa.float64()),
    ("funding_rate", pa.float64()),      # the rate for the NEXT settlement
    ("next_funding_ms", pa.int64()),     # read, never assumed to be +8h
])

LIQ_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("event_ms", pa.int64()),
    ("trade_ms", pa.int64()),
    ("symbol", pa.string()),
    ("side", pa.string()),               # side of the LIQUIDATION order
    ("price", pa.float64()),
    ("orig_qty", pa.float64()),
    ("filled_qty", pa.float64()),
    ("avg_price", pa.float64()),
    ("status", pa.string()),
])

OI_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("symbol", pa.string()),
    ("open_interest", pa.float64()),     # contracts
])

FUNDING_HISTORY_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("funding_time_ms", pa.int64()),
    ("symbol", pa.string()),
    ("funding_rate", pa.float64()),
    ("mark_price", pa.float64()),
])

POSITIONING_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("data_ms", pa.int64()),
    ("symbol", pa.string()),
    ("period", pa.string()),
    ("global_long_short_ratio", pa.float64()),
    ("top_account_long_short_ratio", pa.float64()),
    ("top_position_long_short_ratio", pa.float64()),
    ("taker_buy_sell_ratio", pa.float64()),
    ("taker_buy_volume", pa.float64()),
    ("taker_sell_volume", pa.float64()),
])


async def futures_depth(symbol: str, root, stop: asyncio.Event) -> None:
    """Perp diff depth with a REST baseline for every websocket session."""
    w = PartitionWriter(root, "futures_depth", DEPTH_SCHEMA)
    snapshots = PartitionWriter(
        root, "futures_depth_snapshot", DEPTH_SNAPSHOT_SCHEMA,
        max_rows=5_000, max_seconds=60,
    )
    st = {"last_final": None, "snapshot_id": None, "gaps": 0, "rows": 0,
          "session_id": ""}

    async def on_connected(session_id: str):
        st["session_id"] = session_id
        st["last_final"] = await record_depth_snapshot(
            f"{FUT_REST}/depth?symbol={symbol}&limit=1000",
            symbol, root, "futures_depth_snapshot", snapshots, session_id,
        )
        st["snapshot_id"] = st["last_final"]

    def on_msg(raw):
        m = json.loads(raw)
        if m.get("e") != "depthUpdate":
            return
        now = int(time.time() * 1000)
        U, u = int(m["U"]), int(m["u"])
        pu = m.get("pu")
        prior = st["last_final"]
        awaiting_first_event = prior == st["snapshot_id"]
        sequence_state, gap = classify_futures_depth(
            prior, st["snapshot_id"], U, u, int(pu) if pu is not None else None,
        )
        if gap:
            st["gaps"] += 1
        if sequence_state != "STALE":
            st["last_final"] = u
        if awaiting_first_event and sequence_state == "APPLIED":
            st["snapshot_id"] = None
        for side, key in (("bid", "b"), ("ask", "a")):
            for lvl in m.get(key, ()):
                w.add({"ts_ms": now, "event_ms": int(m.get("E") or now), "symbol": symbol,
                       "session_id": st["session_id"],
                       "transaction_ms": int(m.get("T") or 0),
                       "first_id": U, "final_id": u, "side": side,
                       "prev_final_id": int(pu) if pu is not None else None,
                       "price": float(lvl[0]), "qty": float(lvl[1]), "gap": gap,
                       "sequence_state": sequence_state})
                st["rows"] += 1
                gap = False
        write_status(root, "futures_depth",
                     {"rows": st["rows"], "gaps": st["gaps"], "files": w.files_written,
                      "last_final_id": st["last_final"], "last_data_utc": time.time()})
        if sequence_state == "GAP":
            raise RuntimeError("futures depth sequence gap; reconnecting for fresh snapshot")

    def heartbeat():
        w.flush_due()
        snapshots.flush_due()

    t = asyncio.create_task(_ws_forever(
        f"{FUT_WS}/{symbol.lower()}@depth@100ms", None, on_msg, "futures_depth", root,
        on_connected=on_connected, on_heartbeat=heartbeat,
    ))
    await stop.wait()
    t.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await t
    w.flush()
    snapshots.flush()
    write_status(root, "futures_depth", {
        "rows": st["rows"], "files": w.files_written, "connected": False,
        "stopped_cleanly": True,
    })
    write_status(root, "futures_depth_snapshot", {
        "rows": snapshots.rows_written, "files": snapshots.files_written,
    })


async def futures_trades(symbol: str, root, stop: asyncio.Event) -> None:
    w = PartitionWriter(root, "futures_trades", TRADE_SCHEMA)
    n = {"rows": 0, "gaps": 0, "last_id": None, "last_ws_utc": time.time(),
         "fallback_active": False}

    def add_trade(m: dict, source: str):
        agg_id = int(m["a"])
        prior = n["last_id"]
        if prior is not None and agg_id <= prior:
            return
        if prior is not None and agg_id > prior + 1:
            n["gaps"] += 1
        now = int(time.time() * 1000)
        trade_ms = int(m.get("T") or now)
        w.add({"ts_ms": now, "event_ms": int(m.get("E") or trade_ms), "symbol": symbol,
               "source": source, "agg_id": agg_id, "trade_ms": trade_ms,
               "first_trade_id": int(m.get("f") or 0),
               "last_trade_id": int(m.get("l") or 0),
               "price": float(m["p"]), "qty": float(m["q"]),
               "buyer_is_maker": bool(m["m"])})
        n["last_id"] = agg_id
        n["rows"] += 1
        write_status(root, "futures_trades", {
            **n, "files": w.files_written, "last_data_utc": time.time(),
        })

    def on_msg(raw):
        m = json.loads(raw)
        if m.get("e") != "aggTrade":
            return
        n["last_ws_utc"] = time.time()
        n["fallback_active"] = False
        add_trade(m, "ws")

    def fetch(from_id):
        query = f"symbol={symbol}&limit=1000"
        if from_id is not None:
            query += f"&fromId={from_id}"
        req = urllib.request.Request(f"{FUT_REST}/aggTrades?{query}",
                                     headers={"User-Agent": "btc-capture/1.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read())

    async def rest_fallback():
        while not stop.is_set():
            if time.time() - n["last_ws_utc"] > 10:
                try:
                    n["fallback_active"] = True
                    for _ in range(5):
                        rows = await asyncio.to_thread(
                            fetch, n["last_id"] + 1 if n["last_id"] is not None else None,
                        )
                        for row in sorted(rows, key=lambda item: int(item["a"])):
                            add_trade(row, "rest")
                        if len(rows) < 1000:
                            break
                    n["last_error"] = None
                except Exception as exc:  # noqa: BLE001
                    n["last_error"] = str(exc)[:200]
                    write_status(root, "futures_trades", n)
            w.flush_due()
            try:
                await asyncio.wait_for(stop.wait(), timeout=2)
            except asyncio.TimeoutError:
                pass

    t = asyncio.create_task(_ws_forever(
        f"{FUT_WS}/{symbol.lower()}@aggTrade", None, on_msg, "futures_trades", root,
        on_heartbeat=w.flush_due,
    ))
    fallback = asyncio.create_task(rest_fallback())
    await stop.wait()
    t.cancel()
    fallback.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await t
    with contextlib.suppress(asyncio.CancelledError):
        await fallback
    w.flush()
    write_status(root, "futures_trades", {
        **n, "files": w.files_written, "connected": False, "stopped_cleanly": True,
    })


async def mark_funding(symbol: str, root, stop: asyncio.Event) -> None:
    """Mark, index, funding rate and next funding time - one stream, four series."""
    w = PartitionWriter(root, "futures_mark", MARK_SCHEMA, max_rows=2_000, max_seconds=60)
    n = {"rows": 0, "last_ws_utc": time.time(), "fallback_active": False}

    def add_mark(m: dict, source: str):
        now = int(time.time() * 1000)
        w.add({"ts_ms": now, "event_ms": int(m.get("E") or m.get("time") or now),
               "symbol": symbol, "source": source,
               "mark_price": float(m.get("p") or m.get("markPrice") or 0),
               "index_price": float(m.get("i") or m.get("indexPrice") or 0),
               "settlement_price": float(m.get("P") or m.get("estimatedSettlePrice") or 0),
               "funding_rate": float(m.get("r") or m.get("lastFundingRate") or 0),
               "next_funding_ms": int(m.get("T") or m.get("nextFundingTime") or 0)})
        n["rows"] += 1
        write_status(root, "futures_mark", {
            **n, "files": w.files_written, "last_data_utc": time.time(),
        })

    def on_msg(raw):
        m = json.loads(raw)
        if m.get("e") != "markPriceUpdate":
            return
        n["last_ws_utc"] = time.time()
        n["fallback_active"] = False
        add_mark(m, "ws")

    def fetch():
        req = urllib.request.Request(f"{FUT_REST}/premiumIndex?symbol={symbol}",
                                     headers={"User-Agent": "btc-capture/1.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read())

    async def rest_fallback():
        while not stop.is_set():
            if time.time() - n["last_ws_utc"] > 10:
                try:
                    n["fallback_active"] = True
                    add_mark(await asyncio.to_thread(fetch), "rest")
                    n["last_error"] = None
                except Exception as exc:  # noqa: BLE001
                    n["last_error"] = str(exc)[:200]
                    write_status(root, "futures_mark", n)
            w.flush_due()
            try:
                await asyncio.wait_for(stop.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass

    t = asyncio.create_task(_ws_forever(
        f"{FUT_WS}/{symbol.lower()}@markPrice@1s", None, on_msg, "futures_mark", root,
        on_heartbeat=w.flush_due,
    ))
    fallback = asyncio.create_task(rest_fallback())
    await stop.wait()
    t.cancel()
    fallback.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await t
    with contextlib.suppress(asyncio.CancelledError):
        await fallback
    w.flush()
    write_status(root, "futures_mark", {
        **n, "files": w.files_written, "connected": False, "stopped_cleanly": True,
    })


async def liquidations(root, stop: asyncio.Event, symbol_filter: str | None = None) -> None:
    """Forced orders across the venue. Filter is optional - cascades elsewhere carry information."""
    w = PartitionWriter(root, "futures_liquidations", LIQ_SCHEMA,
                        max_rows=500, max_seconds=120)
    n = {"rows": 0}

    def on_msg(raw):
        m = json.loads(raw)
        if m.get("e") != "forceOrder":
            return
        o = m.get("o") or {}
        sym = str(o.get("s") or "")
        if symbol_filter and sym != symbol_filter:
            return
        now = int(time.time() * 1000)
        w.add({"ts_ms": now, "event_ms": int(m.get("E") or now),
               "trade_ms": int(o.get("T") or 0), "symbol": sym,
               "side": str(o.get("S") or ""), "price": float(o.get("p") or 0),
               "orig_qty": float(o.get("q") or 0), "filled_qty": float(o.get("z") or 0),
               "avg_price": float(o.get("ap") or 0), "status": str(o.get("X") or "")})
        n["rows"] += 1
        write_status(root, "futures_liquidations",
                     {"rows": n["rows"], "files": w.files_written,
                      "last_data_utc": time.time()})

    t = asyncio.create_task(_ws_forever(
        f"{FUT_WS}/!forceOrder@arr", None, on_msg, "futures_liquidations", root,
        on_heartbeat=w.flush_due,
    ))
    await stop.wait()
    t.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await t
    w.flush()
    write_status(root, "futures_liquidations", {
        "rows": n["rows"], "files": w.files_written, "connected": False,
        "stopped_cleanly": True,
    })


async def open_interest(symbol: str, root, stop: asyncio.Event, interval_s: int = 60) -> None:
    """Open interest has no websocket - REST poll. One row per poll, no interpolation."""
    w = PartitionWriter(root, "futures_open_interest", OI_SCHEMA,
                        max_rows=200, max_seconds=300)
    n = {"rows": 0, "errors": 0}

    def fetch():
        req = urllib.request.Request(f"{FUT_REST}/openInterest?symbol={symbol}",
                                     headers={"User-Agent": "btc-capture/1.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    if interval_s <= 0:
        raise ValueError("open-interest poll interval must be positive")
    while not stop.is_set():
        try:
            d = await asyncio.to_thread(fetch)
            w.add({"ts_ms": int(time.time() * 1000), "symbol": symbol,
                   "open_interest": float(d.get("openInterest") or 0)})
            n["rows"] += 1
            n["last_data_utc"] = time.time()
            n["last_error"] = None
        except Exception as exc:                                   # noqa: BLE001
            n["errors"] += 1
            n["last_error"] = str(exc)[:200]
        write_status(root, "futures_open_interest", {**n, "files": w.files_written})
        w.flush_due()
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
    w.flush()
    write_status(root, "futures_open_interest", {**n, "files": w.files_written,
                                                   "stopped_cleanly": True})


def _rest_json(path: str):
    req = urllib.request.Request(f"https://fapi.binance.com{path}",
                                 headers={"User-Agent": "btc-capture/1.0"})
    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read())


async def funding_history(symbol: str, root, stop: asyncio.Event,
                          interval_s: int = 300) -> None:
    """Persist actual funding settlements, deduplicated by funding timestamp."""
    if interval_s <= 0:
        raise ValueError("funding-history poll interval must be positive")
    writer = PartitionWriter(root, "futures_funding_history", FUNDING_HISTORY_SCHEMA,
                             max_rows=100, max_seconds=300)
    seen = set()
    existing = list((root / "futures_funding_history").glob("**/*.parquet"))
    for path in existing:
        try:
            seen.update(int(value) for value in
                        pq.read_table(path, columns=["funding_time_ms"])
                        .column("funding_time_ms").to_pylist())
        except Exception:
            raise RuntimeError(f"cannot read existing funding history: {path}")
    writer.files_written = len(existing)
    writer.rows_written = len(seen)
    stats = {"rows": len(seen), "errors": 0}
    while not stop.is_set():
        try:
            rows = await asyncio.to_thread(
                _rest_json, f"/fapi/v1/fundingRate?symbol={symbol}&limit=100",
            )
            added = []
            for item in sorted(rows, key=lambda value: int(value["fundingTime"])):
                funding_ms = int(item["fundingTime"])
                if funding_ms in seen:
                    continue
                writer.add({"ts_ms": int(time.time() * 1000),
                            "funding_time_ms": funding_ms, "symbol": symbol,
                            "funding_rate": float(item["fundingRate"]),
                            "mark_price": float(item.get("markPrice") or 0)})
                added.append(funding_ms)
            if added:
                writer.flush()
                seen.update(added)
            stats.update({"rows": len(seen), "files": writer.files_written,
                          "last_data_utc": time.time(), "last_error": None})
        except Exception as exc:  # noqa: BLE001
            stats["errors"] += 1
            stats["last_error"] = str(exc)[:200]
        write_status(root, "futures_funding_history", stats)
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
    writer.flush()
    write_status(root, "futures_funding_history", {
        **stats, "files": writer.files_written, "stopped_cleanly": True,
    })


def parse_positioning(global_rows, top_account_rows, top_position_rows,
                      taker_rows, symbol: str, period: str, ts_ms: int) -> dict:
    def latest(rows):
        if not isinstance(rows, list) or not rows:
            raise ValueError("missing Binance positioning response")
        return max(rows, key=lambda row: int(row.get("timestamp") or 0))

    global_row = latest(global_rows)
    account_row = latest(top_account_rows)
    position_row = latest(top_position_rows)
    taker_row = latest(taker_rows)
    data_ms = min(int(row.get("timestamp") or 0)
                  for row in (global_row, account_row, position_row, taker_row))
    if data_ms <= 0:
        raise ValueError("positioning response has no timestamp")
    return {
        "ts_ms": ts_ms, "data_ms": data_ms, "symbol": symbol, "period": period,
        "global_long_short_ratio": float(global_row["longShortRatio"]),
        "top_account_long_short_ratio": float(account_row["longShortRatio"]),
        "top_position_long_short_ratio": float(position_row["longShortRatio"]),
        "taker_buy_sell_ratio": float(taker_row["buySellRatio"]),
        "taker_buy_volume": float(taker_row["buyVol"]),
        "taker_sell_volume": float(taker_row["sellVol"]),
    }


async def positioning(symbol: str, root, stop: asyncio.Event, *, period: str = "5m",
                      interval_s: int = 300) -> None:
    """Persist global/top-trader crowding and taker-flow ratios once per exchange bucket."""
    if interval_s <= 0:
        raise ValueError("positioning poll interval must be positive")
    writer = PartitionWriter(root, "futures_positioning", POSITIONING_SCHEMA,
                             max_rows=100, max_seconds=300)
    existing = list((root / "futures_positioning").glob("**/*.parquet"))
    existing_rows = 0
    last_data_ms = 0
    for path in existing:
        try:
            table = pq.read_table(path, columns=["data_ms"])
        except Exception:
            raise RuntimeError(f"cannot read existing positioning history: {path}")
        values = [int(value) for value in table.column("data_ms").to_pylist()]
        existing_rows += len(values)
        last_data_ms = max([last_data_ms, *values])
    writer.files_written = len(existing)
    writer.rows_written = existing_rows
    stats = {"rows": existing_rows, "errors": 0, "last_data_ms": last_data_ms}
    endpoints = (
        "/futures/data/globalLongShortAccountRatio",
        "/futures/data/topLongShortAccountRatio",
        "/futures/data/topLongShortPositionRatio",
        "/futures/data/takerlongshortRatio",
    )
    while not stop.is_set():
        try:
            responses = await asyncio.gather(*(asyncio.to_thread(
                _rest_json, f"{endpoint}?symbol={symbol}&period={period}&limit=2",
            ) for endpoint in endpoints))
            row = parse_positioning(*responses, symbol, period, int(time.time() * 1000))
            if row["data_ms"] > stats["last_data_ms"]:
                writer.add(row)
                stats["rows"] += 1
                stats["last_data_ms"] = row["data_ms"]
            stats.update({"files": writer.files_written, "last_data_utc": time.time(),
                          "last_error": None})
        except Exception as exc:  # noqa: BLE001
            stats["errors"] += 1
            stats["last_error"] = str(exc)[:200]
        write_status(root, "futures_positioning", stats)
        writer.flush_due()
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
    writer.flush()
    write_status(root, "futures_positioning", {
        **stats, "files": writer.files_written, "stopped_cleanly": True,
    })
