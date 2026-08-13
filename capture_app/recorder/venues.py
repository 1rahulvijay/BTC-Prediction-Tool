"""Independent cross-venue BTC observations.

The receive clocks are intentionally preserved at nanosecond resolution. Cross-venue lead/lag
research is invalid if exchange publication time is substituted for the time this collector saw
the event. No derived premium or lead/lag label is written here; those remain research outputs.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import math
import time
import urllib.parse
import urllib.request

import pyarrow as pa

from .storage import PartitionWriter, write_status
from .streams import _as_ms, _ws_forever

BYBIT_WS = "wss://stream.bybit.com/v5/public/linear"
COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"

QUOTE_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("recv_ns", pa.int64()),
    ("monotonic_ns", pa.int64()),
    ("event_ms", pa.int64()),
    ("venue", pa.string()),
    ("stream", pa.string()),
    ("symbol", pa.string()),
    ("session_id", pa.string()),
    ("update_id", pa.int64()),
    ("cross_sequence", pa.int64()),
    ("bid", pa.float64()),
    ("bid_size", pa.float64()),
    ("ask", pa.float64()),
    ("ask_size", pa.float64()),
    ("last_price", pa.float64()),
    ("last_size", pa.float64()),
    ("last_side", pa.string()),
    ("open_24h", pa.float64()),
    ("high_24h", pa.float64()),
    ("low_24h", pa.float64()),
    ("volume_24h", pa.float64()),
    ("payload_json", pa.string()),
])

VENUE_TRADE_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("recv_ns", pa.int64()),
    ("monotonic_ns", pa.int64()),
    ("event_ms", pa.int64()),
    ("venue", pa.string()),
    ("stream", pa.string()),
    ("symbol", pa.string()),
    ("session_id", pa.string()),
    ("trade_id", pa.string()),
    ("price", pa.float64()),
    ("qty", pa.float64()),
    ("side", pa.string()),
    ("tick_direction", pa.string()),
    ("payload_json", pa.string()),
])

BYBIT_DERIVATIVE_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()), ("request_ns", pa.int64()), ("recv_ns", pa.int64()),
    ("data_ms", pa.int64()), ("symbol", pa.string()), ("metric", pa.string()),
    ("interval", pa.string()), ("value", pa.float64()), ("unit", pa.string()),
    ("payload_json", pa.string()),
])


def _finite(value, *, positive: bool = False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or (positive and number <= 0):
        return None
    return number


def parse_bybit_quote(raw, received_ns: int, monotonic_ns: int,
                      session_id: str) -> dict | None:
    message = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    if not isinstance(message, dict) or not str(message.get("topic") or "").startswith(
        "orderbook.1."
    ):
        return None
    data = message.get("data") or {}
    bids, asks = data.get("b") or [], data.get("a") or []
    if not bids or not asks or len(bids[0]) < 2 or len(asks[0]) < 2:
        return None
    bid, bid_size = _finite(bids[0][0], positive=True), _finite(bids[0][1])
    ask, ask_size = _finite(asks[0][0], positive=True), _finite(asks[0][1])
    if None in (bid, bid_size, ask, ask_size) or bid > ask:
        return None
    ts_ms = received_ns // 1_000_000
    event_ms = _as_ms(data.get("cts") or message.get("ts"), ts_ms)
    return {
        "ts_ms": ts_ms, "recv_ns": received_ns, "monotonic_ns": monotonic_ns,
        "event_ms": event_ms, "venue": "bybit_perp", "stream": "orderbook.1",
        "symbol": str(data.get("s") or "BTCUSDT"), "session_id": session_id,
        "update_id": int(data.get("u") or 0), "cross_sequence": int(data.get("seq") or 0),
        "bid": bid, "bid_size": bid_size, "ask": ask, "ask_size": ask_size,
        "last_price": None, "last_size": None, "last_side": None,
        "open_24h": None, "high_24h": None, "low_24h": None, "volume_24h": None,
        "payload_json": json.dumps(message, separators=(",", ":"), sort_keys=True),
    }


def parse_bybit_trades(raw, received_ns: int, monotonic_ns: int,
                       session_id: str) -> list[dict]:
    message = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    if not isinstance(message, dict) or not str(message.get("topic") or "").startswith(
        "publicTrade."
    ):
        return []
    ts_ms = received_ns // 1_000_000
    out = []
    for trade in message.get("data") or []:
        price, qty = _finite(trade.get("p"), positive=True), _finite(trade.get("v"))
        side = str(trade.get("S") or "").lower()
        if price is None or qty is None or qty < 0 or side not in {"buy", "sell"}:
            continue
        out.append({
            "ts_ms": ts_ms, "recv_ns": received_ns, "monotonic_ns": monotonic_ns,
            "event_ms": _as_ms(trade.get("T") or message.get("ts"), ts_ms),
            "venue": "bybit_perp", "stream": "publicTrade",
            "symbol": str(trade.get("s") or "BTCUSDT"), "session_id": session_id,
            "trade_id": str(trade.get("i") or ""), "price": price, "qty": qty,
            "side": side, "tick_direction": str(trade.get("L") or "") or None,
            "payload_json": json.dumps(trade, separators=(",", ":"), sort_keys=True),
        })
    return out


def parse_coinbase_ticker(raw, received_ns: int, monotonic_ns: int,
                          session_id: str) -> dict | None:
    message = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) else raw
    if not isinstance(message, dict) or message.get("type") != "ticker":
        return None
    price = _finite(message.get("price"), positive=True)
    if price is None:
        return None
    bid = _finite(message.get("best_bid"), positive=True)
    ask = _finite(message.get("best_ask"), positive=True)
    if bid is not None and ask is not None and bid > ask:
        return None
    ts_ms = received_ns // 1_000_000
    return {
        "ts_ms": ts_ms, "recv_ns": received_ns, "monotonic_ns": monotonic_ns,
        "event_ms": _as_ms(message.get("time"), ts_ms), "venue": "coinbase_spot",
        "stream": "ticker", "symbol": str(message.get("product_id") or "BTC-USD"),
        "session_id": session_id, "update_id": int(message.get("sequence") or 0),
        "cross_sequence": None, "bid": bid,
        "bid_size": _finite(message.get("best_bid_size")), "ask": ask,
        "ask_size": _finite(message.get("best_ask_size")), "last_price": price,
        "last_size": _finite(message.get("last_size")),
        "last_side": str(message.get("side") or "").lower() or None,
        "open_24h": _finite(message.get("open_24h"), positive=True),
        "high_24h": _finite(message.get("high_24h"), positive=True),
        "low_24h": _finite(message.get("low_24h"), positive=True),
        "volume_24h": _finite(message.get("volume_24h")),
        "payload_json": json.dumps(message, separators=(",", ":"), sort_keys=True),
    }


async def _single_stream(root, stop: asyncio.Event, *, name: str, url: str,
                         subscribe: dict, parser, schema: pa.Schema) -> None:
    writer = PartitionWriter(root, name, schema)
    state = {"rows": 0, "session_id": "", "last_sequence": None, "gaps": 0}

    def connected(session_id: str):
        state["session_id"] = session_id
        state["last_sequence"] = None

    def on_msg(raw):
        recv_ns, mono_ns = time.time_ns(), time.perf_counter_ns()
        parsed = parser(raw, recv_ns, mono_ns, state["session_id"])
        rows = parsed if isinstance(parsed, list) else ([parsed] if parsed else [])
        for row in rows:
            sequence = row.get("update_id")
            prior = state["last_sequence"]
            if name == "bybit_quotes" and sequence and prior and sequence < prior:
                state["gaps"] += 1
            if sequence:
                state["last_sequence"] = sequence
            writer.add(row)
            state["rows"] += 1
        if rows:
            write_status(root, name, {
                "rows": state["rows"], "files": writer.files_written,
                "gaps": state["gaps"], "last_data_utc": time.time(),
            })

    task = asyncio.create_task(_ws_forever(
        url, subscribe, on_msg, name, root, on_connected=connected,
        on_heartbeat=writer.flush_due,
    ))
    await stop.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    writer.flush()
    write_status(root, name, {
        "rows": state["rows"], "files": writer.files_written, "connected": False,
        "gaps": state["gaps"], "stopped_cleanly": True,
    })


async def bybit_quotes(root, stop: asyncio.Event) -> None:
    await _single_stream(
        root, stop, name="bybit_quotes", url=BYBIT_WS,
        subscribe={"op": "subscribe", "args": ["orderbook.1.BTCUSDT"]},
        parser=parse_bybit_quote, schema=QUOTE_SCHEMA,
    )


async def bybit_trades(root, stop: asyncio.Event) -> None:
    await _single_stream(
        root, stop, name="bybit_trades", url=BYBIT_WS,
        subscribe={"op": "subscribe", "args": ["publicTrade.BTCUSDT"]},
        parser=parse_bybit_trades, schema=VENUE_TRADE_SCHEMA,
    )


async def coinbase_ticker(root, stop: asyncio.Event) -> None:
    await _single_stream(
        root, stop, name="coinbase_ticker", url=COINBASE_WS,
        subscribe={"type": "subscribe", "product_ids": ["BTC-USD"],
                   "channels": ["ticker"]},
        parser=parse_coinbase_ticker, schema=QUOTE_SCHEMA,
    )


def parse_bybit_metric(payload: dict, metric: str, request_ns: int,
                       recv_ns: int) -> dict | None:
    if not isinstance(payload, dict) or int(payload.get("retCode", -1)) != 0:
        return None
    result = payload.get("result") or {}
    rows = result.get("list") or []
    item = rows[0] if rows and isinstance(rows[0], dict) else None
    if not item:
        return None
    if metric == "open_interest":
        value, raw_time, interval, unit = (
            _finite(item.get("openInterest"), positive=True), item.get("timestamp"), "5min", "BTC",
        )
    elif metric == "funding_rate":
        value, raw_time, interval, unit = (
            _finite(item.get("fundingRate")), item.get("fundingRateTimestamp"), "event", "ratio",
        )
    else:
        return None
    if value is None:
        return None
    ts_ms = recv_ns // 1_000_000
    return {
        "ts_ms": ts_ms, "request_ns": request_ns, "recv_ns": recv_ns,
        "data_ms": _as_ms(raw_time, ts_ms), "symbol": str(item.get("symbol") or "BTCUSDT"),
        "metric": metric, "interval": interval, "value": value, "unit": unit,
        "payload_json": json.dumps(item, separators=(",", ":"), sort_keys=True),
    }


async def _bybit_metric(root, stop: asyncio.Event, *, stream: str, metric: str,
                        path: str, interval_s: int) -> None:
    writer = PartitionWriter(root, stream, BYBIT_DERIVATIVE_SCHEMA, max_rows=2_000,
                             max_seconds=60)
    state = {"rows": 0, "errors": 0, "duplicates": 0}
    last_identity = None

    def fetch():
        request_ns = time.time_ns()
        req = urllib.request.Request(f"https://api.bybit.com{path}",
                                     headers={"User-Agent": "btc-capture/1.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            payload = json.loads(response.read())
        return payload, request_ns, time.time_ns()

    while not stop.is_set():
        try:
            payload, request_ns, recv_ns = await asyncio.to_thread(fetch)
            row = parse_bybit_metric(payload, metric, request_ns, recv_ns)
            if row is None:
                raise ValueError(f"Bybit returned no valid {metric} row")
            identity = (row["data_ms"], row["value"])
            if identity != last_identity:
                writer.add(row)
                state["rows"] += 1
                last_identity = identity
            else:
                state["duplicates"] += 1
            writer.flush_due()
            write_status(root, stream, {
                **state, "files": writer.files_written, "last_data_utc": time.time(),
                "last_source_data_utc": row["data_ms"] / 1000, "last_error": None,
            })
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            state["errors"] += 1
            write_status(root, stream, {**state, "files": writer.files_written,
                                        "last_error": str(exc)[:300]})
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
    writer.flush()
    write_status(root, stream, {**state, "files": writer.files_written,
                                "stopped_cleanly": True})


async def bybit_open_interest(root, stop: asyncio.Event, interval_s: int = 60) -> None:
    query = urllib.parse.urlencode({"category": "linear", "symbol": "BTCUSDT",
                                    "intervalTime": "5min", "limit": 1})
    await _bybit_metric(root, stop, stream="bybit_open_interest", metric="open_interest",
                        path=f"/v5/market/open-interest?{query}", interval_s=interval_s)


async def bybit_funding_history(root, stop: asyncio.Event, interval_s: int = 300) -> None:
    query = urllib.parse.urlencode({"category": "linear", "symbol": "BTCUSDT", "limit": 1})
    await _bybit_metric(root, stop, stream="bybit_funding_history", metric="funding_rate",
                        path=f"/v5/market/funding/history?{query}", interval_s=interval_s)
