"""Capture streams. Read-only market data. No credentials, no orders, ever.

WHAT IS RECORDED AND WHY EACH FIELD EARNS ITS SPACE

  binance_depth   SEQUENCED diff updates (U/u ids), not depth20 snapshots. A snapshot shows a
                  level went 50 -> 30 but cannot say whether 20 was cancelled or traded. Queue
                  position only advances on the second, so snapshots structurally cannot answer
                  "would my resting order have filled" - the one open question in this project.
                  Diffs plus trades can. You can always derive snapshots from diffs; never the
                  reverse.

  binance_trades  aggTrade. Pairs with depth to decide cancel-vs-execute at a price level.

  polymarket_book Full CLOB book for both outcome tokens of each BTC up/down market, with a
                  local receive timestamp. Both tokens matter: UP+DOWN parity is a mechanical
                  check needing no forecast, and the maker lane needs both sides.

GAP HANDLING IS THE POINT
    Binance diff streams carry first/final update ids. If final_id(prev) + 1 != first_id(next),
    the book is no longer reconstructable and every downstream queue calculation is wrong. That
    is recorded as an explicit gap row rather than silently continued - a reconstructed book
    with an unnoticed hole is worse than an admitted gap.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time

import pyarrow as pa

from .storage import PartitionWriter, write_status

BINANCE_WS = "wss://stream.binance.com:9443/ws"
PM_CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PM_GAMMA = "https://gamma-api.polymarket.com"

DEPTH_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),          # local receive time
    ("event_ms", pa.int64()),       # exchange event time
    ("symbol", pa.string()),
    ("first_id", pa.int64()),       # U
    ("final_id", pa.int64()),       # u
    ("side", pa.string()),          # bid | ask
    ("price", pa.float64()),
    ("qty", pa.float64()),          # 0 means the level was removed
    ("gap", pa.bool_()),            # True on the row that follows a sequence break
])

TRADE_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("event_ms", pa.int64()),
    ("symbol", pa.string()),
    ("agg_id", pa.int64()),
    ("price", pa.float64()),
    ("qty", pa.float64()),
    ("buyer_is_maker", pa.bool_()),  # False => aggressive BUY lifted the ask
])

PM_BOOK_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("event_ms", pa.int64()),
    ("market", pa.string()),        # condition id
    ("asset_id", pa.string()),      # token id (one per outcome)
    ("outcome", pa.string()),
    ("side", pa.string()),
    ("price", pa.float64()),
    ("size", pa.float64()),
    ("level", pa.int32()),          # 0 = top of book
    ("hash", pa.string()),
])


async def _ws_forever(url, subscribe, on_msg, name, status_root, backoff_max=60.0):
    """Reconnect loop. Every disconnect is recorded, because a reconnect is a data gap."""
    import websockets
    backoff, reconnects = 1.0, 0
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, max_size=8 << 20) as ws:
                if subscribe:
                    await ws.send(json.dumps(subscribe))
                backoff = 1.0
                write_status(status_root, name, {"connected": True, "reconnects": reconnects})
                async for raw in ws:
                    on_msg(raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                   # noqa: BLE001
            reconnects += 1
            write_status(status_root, name,
                         {"connected": False, "reconnects": reconnects,
                          "last_error": str(exc)[:200]})
            await asyncio.sleep(backoff)
            backoff = min(backoff_max, backoff * 2)


async def binance_depth(symbol: str, root, stop: asyncio.Event) -> None:
    w = PartitionWriter(root, "binance_depth", DEPTH_SCHEMA)
    state = {"last_final": None, "gaps": 0, "rows": 0}

    def on_msg(raw):
        m = json.loads(raw)
        if m.get("e") != "depthUpdate":
            return
        now = int(time.time() * 1000)
        U, u = int(m["U"]), int(m["u"])
        gap = state["last_final"] is not None and U != state["last_final"] + 1
        if gap:
            state["gaps"] += 1
        state["last_final"] = u
        for side, key in (("bid", "b"), ("ask", "a")):
            for lvl in m.get(key, ()):
                w.add({"ts_ms": now, "event_ms": int(m.get("E") or now),
                       "symbol": symbol, "first_id": U, "final_id": u, "side": side,
                       "price": float(lvl[0]), "qty": float(lvl[1]), "gap": gap})
                state["rows"] += 1
                gap = False        # flag only the first row of the break
        write_status(root, "binance_depth",
                     {"rows": state["rows"], "gaps": state["gaps"],
                      "files": w.files_written, "last_final_id": u})

    task = asyncio.create_task(_ws_forever(
        f"{BINANCE_WS}/{symbol.lower()}@depth@100ms", None, on_msg, "binance_depth", root))
    await stop.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    w.flush()


async def binance_trades(symbol: str, root, stop: asyncio.Event) -> None:
    w = PartitionWriter(root, "binance_trades", TRADE_SCHEMA)
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
        write_status(root, "binance_trades", {"rows": n["rows"], "files": w.files_written})

    task = asyncio.create_task(_ws_forever(
        f"{BINANCE_WS}/{symbol.lower()}@aggTrade", None, on_msg, "binance_trades", root))
    await stop.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    w.flush()


def discover_pm_markets(slug_contains: str = "btc-updown", limit: int = 200) -> list[dict]:
    """Active BTC up/down markets and their two token ids.

    Uses /events (not /markets) - the repo established that /markets?slug= silently ignores the
    filter and returns unrelated rows.
    """
    import urllib.request
    url = f"{PM_GAMMA}/events?active=true&closed=false&limit={limit}"
    req = urllib.request.Request(url, headers={"User-Agent": "btc-capture/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        events = json.loads(r.read())
    out = []
    for ev in events:
        if slug_contains not in str(ev.get("slug", "")):
            continue
        for mk in ev.get("markets", []) or []:
            try:
                toks = json.loads(mk.get("clobTokenIds") or "[]")
                outs = json.loads(mk.get("outcomes") or "[]")
            except Exception:
                continue
            if len(toks) == 2:
                out.append({"condition_id": mk.get("conditionId"), "slug": ev.get("slug"),
                            "tokens": toks, "outcomes": outs})
    return out


async def polymarket_books(root, stop: asyncio.Event, refresh_s: int = 120) -> None:
    """Subscribe to every active BTC up/down token book, refreshing the market list as rounds roll."""
    w = PartitionWriter(root, "polymarket_book", PM_BOOK_SCHEMA)
    n = {"rows": 0, "markets": 0}
    tok_meta: dict[str, dict] = {}

    def on_msg(raw):
        try:
            msgs = json.loads(raw)
        except Exception:
            return
        if isinstance(msgs, dict):
            msgs = [msgs]
        now = int(time.time() * 1000)
        for m in msgs:
            if m.get("event_type") != "book":
                continue
            aid = str(m.get("asset_id") or "")
            meta = tok_meta.get(aid, {})
            for side in ("bids", "asks"):
                for i, lvl in enumerate(m.get(side) or ()):
                    w.add({"ts_ms": now, "event_ms": int(m.get("timestamp") or now),
                           "market": str(m.get("market") or meta.get("condition_id") or ""),
                           "asset_id": aid, "outcome": str(meta.get("outcome") or ""),
                           "side": "bid" if side == "bids" else "ask",
                           "price": float(lvl["price"]), "size": float(lvl["size"]),
                           "level": i, "hash": str(m.get("hash") or "")})
                    n["rows"] += 1
        write_status(root, "polymarket_book",
                     {"rows": n["rows"], "markets": n["markets"], "files": w.files_written})

    async def run():
        while not stop.is_set():
            try:
                mkts = await asyncio.to_thread(discover_pm_markets)
            except Exception as exc:                               # noqa: BLE001
                write_status(root, "polymarket_book", {"discovery_error": str(exc)[:200]})
                await asyncio.sleep(30)
                continue
            tokens = []
            for mk in mkts:
                for tok, oc in zip(mk["tokens"], mk["outcomes"] or ["", ""]):
                    tok_meta[str(tok)] = {"condition_id": mk["condition_id"], "outcome": oc}
                    tokens.append(str(tok))
            n["markets"] = len(mkts)
            if not tokens:
                await asyncio.sleep(30)
                continue
            sub = {"assets_ids": tokens, "type": "market"}
            t = asyncio.create_task(_ws_forever(PM_CLOB_WS, sub, on_msg,
                                                "polymarket_book", root))
            try:
                await asyncio.wait_for(stop.wait(), timeout=refresh_s)
            except asyncio.TimeoutError:
                pass
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t

    await run()
    w.flush()
