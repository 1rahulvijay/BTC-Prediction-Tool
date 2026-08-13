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

from .storage import PartitionWriter, write_status
from .streams import DEPTH_SCHEMA, TRADE_SCHEMA, _ws_forever

FUT_WS = "wss://fstream.binance.com/ws"
FUT_REST = "https://fapi.binance.com/fapi/v1"

MARK_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("event_ms", pa.int64()),
    ("symbol", pa.string()),
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


async def futures_depth(symbol: str, root, stop: asyncio.Event) -> None:
    """Perp diff depth. Same sequencing contract as spot: U/u checked, gaps flagged."""
    w = PartitionWriter(root, "futures_depth", DEPTH_SCHEMA)
    st = {"last_final": None, "gaps": 0, "rows": 0}

    def on_msg(raw):
        m = json.loads(raw)
        if m.get("e") != "depthUpdate":
            return
        now = int(time.time() * 1000)
        U, u = int(m["U"]), int(m["u"])
        # FUTURES CONTINUITY IS `pu`, NOT THE SPOT RULE.
        #
        # On spot, consecutive events satisfy U == prev_u + 1. On USD-M futures they do NOT -
        # the stream carries an explicit `pu` (previous final id) and the contract is
        # pu == prev_u. Applying the spot rule here flagged ~1 in 75 events as a break: a live
        # 95-second capture reported 962 gaps that did not exist, which would have made every
        # futures book reconstruction look unusable.
        #
        # Measured on a live pair of messages:
        #     U == prev_u + 1  ->  False
        #     pu == prev_u     ->  True
        pu = m.get("pu")
        if pu is None:                      # absent => treat as spot-style sequencing
            gap = st["last_final"] is not None and U != st["last_final"] + 1
        else:
            gap = st["last_final"] is not None and int(pu) != st["last_final"]
        if gap:
            st["gaps"] += 1
        st["last_final"] = u
        for side, key in (("bid", "b"), ("ask", "a")):
            for lvl in m.get(key, ()):
                w.add({"ts_ms": now, "event_ms": int(m.get("E") or now), "symbol": symbol,
                       "first_id": U, "final_id": u, "side": side,
                       "price": float(lvl[0]), "qty": float(lvl[1]), "gap": gap})
                st["rows"] += 1
                gap = False
        write_status(root, "futures_depth",
                     {"rows": st["rows"], "gaps": st["gaps"], "files": w.files_written,
                      "last_final_id": u})

    t = asyncio.create_task(_ws_forever(
        f"{FUT_WS}/{symbol.lower()}@depth@100ms", None, on_msg, "futures_depth", root))
    await stop.wait()
    t.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await t
    w.flush()


async def futures_trades(symbol: str, root, stop: asyncio.Event) -> None:
    w = PartitionWriter(root, "futures_trades", TRADE_SCHEMA)
    n = {"rows": 0}

    def on_msg(raw):
        m = json.loads(raw)
        if m.get("e") != "aggTrade":
            return
        now = int(time.time() * 1000)
        w.add({"ts_ms": now, "event_ms": int(m.get("E") or now), "symbol": symbol,
               "agg_id": int(m["a"]), "price": float(m["p"]), "qty": float(m["q"]),
               "buyer_is_maker": bool(m["m"])})
        n["rows"] += 1
        write_status(root, "futures_trades", {"rows": n["rows"], "files": w.files_written})

    t = asyncio.create_task(_ws_forever(
        f"{FUT_WS}/{symbol.lower()}@aggTrade", None, on_msg, "futures_trades", root))
    await stop.wait()
    t.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await t
    w.flush()


async def mark_funding(symbol: str, root, stop: asyncio.Event) -> None:
    """Mark, index, funding rate and next funding time - one stream, four series."""
    w = PartitionWriter(root, "futures_mark", MARK_SCHEMA, max_rows=2_000, max_seconds=60)
    n = {"rows": 0}

    def on_msg(raw):
        m = json.loads(raw)
        if m.get("e") != "markPriceUpdate":
            return
        now = int(time.time() * 1000)
        w.add({"ts_ms": now, "event_ms": int(m.get("E") or now), "symbol": symbol,
               "mark_price": float(m.get("p") or 0), "index_price": float(m.get("i") or 0),
               "settlement_price": float(m.get("P") or 0),
               "funding_rate": float(m.get("r") or 0),
               "next_funding_ms": int(m.get("T") or 0)})
        n["rows"] += 1
        write_status(root, "futures_mark", {"rows": n["rows"], "files": w.files_written})

    t = asyncio.create_task(_ws_forever(
        f"{FUT_WS}/{symbol.lower()}@markPrice@1s", None, on_msg, "futures_mark", root))
    await stop.wait()
    t.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await t
    w.flush()


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
                     {"rows": n["rows"], "files": w.files_written})

    t = asyncio.create_task(_ws_forever(
        f"{FUT_WS}/!forceOrder@arr", None, on_msg, "futures_liquidations", root))
    await stop.wait()
    t.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await t
    w.flush()


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

    while not stop.is_set():
        try:
            d = await asyncio.to_thread(fetch)
            w.add({"ts_ms": int(time.time() * 1000), "symbol": symbol,
                   "open_interest": float(d.get("openInterest") or 0)})
            n["rows"] += 1
        except Exception as exc:                                   # noqa: BLE001
            n["errors"] += 1
            n["last_error"] = str(exc)[:200]
        write_status(root, "futures_open_interest", {**n, "files": w.files_written})
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
    w.flush()
