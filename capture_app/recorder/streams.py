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
import inspect
import json
import math
import time
import urllib.request
import urllib.parse
import uuid
from datetime import datetime, timezone

import pyarrow as pa

from .storage import PartitionWriter, write_status

BINANCE_WS = "wss://stream.binance.com:9443/ws"
PM_CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
PM_GAMMA = "https://gamma-api.polymarket.com"
PM_RTDS_WS = "wss://ws-live-data.polymarket.com"
BINANCE_REST = "https://api.binance.com/api/v3"

DEPTH_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),          # local receive time
    ("event_ms", pa.int64()),       # exchange event time
    ("symbol", pa.string()),
    ("session_id", pa.string()),
    ("transaction_ms", pa.int64()), # futures transaction time; null on spot
    ("first_id", pa.int64()),       # U
    ("final_id", pa.int64()),       # u
    ("prev_final_id", pa.int64()),  # futures pu; null on spot
    ("side", pa.string()),          # bid | ask
    ("price", pa.float64()),
    ("qty", pa.float64()),          # 0 means the level was removed
    ("gap", pa.bool_()),            # True on the row that follows a sequence break
    ("sequence_state", pa.string()),  # APPLIED | STALE | GAP
])

DEPTH_SNAPSHOT_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("request_ms", pa.int64()),
    ("symbol", pa.string()),
    ("session_id", pa.string()),
    ("last_update_id", pa.int64()),
    ("side", pa.string()),
    ("price", pa.float64()),
    ("qty", pa.float64()),
    ("level", pa.int32()),
])

TRADE_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("event_ms", pa.int64()),
    ("symbol", pa.string()),
    ("source", pa.string()),
    ("agg_id", pa.int64()),
    ("trade_ms", pa.int64()),
    ("first_trade_id", pa.int64()),
    ("last_trade_id", pa.int64()),
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
    ("session_id", pa.string()),
    ("event_type", pa.string()),    # book snapshot | price_change delta
    ("side", pa.string()),
    ("price", pa.float64()),
    ("size", pa.float64()),
    ("level", pa.int32()),          # 0 = top of book
    ("hash", pa.string()),
    ("best_bid", pa.float64()),
    ("best_ask", pa.float64()),
])

PM_TRADE_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("event_ms", pa.int64()),
    ("market", pa.string()),
    ("asset_id", pa.string()),
    ("outcome", pa.string()),
    ("session_id", pa.string()),
    ("side", pa.string()),
    ("price", pa.float64()),
    ("size", pa.float64()),
    ("fee_rate_bps", pa.float64()),
    ("transaction_hash", pa.string()),
])

PM_META_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("slug", pa.string()),
    ("condition_id", pa.string()),
    ("horizon", pa.int32()),
    ("anchor_ts", pa.int64()),
    ("asset_id", pa.string()),
    ("outcome", pa.string()),
    ("question", pa.string()),
    ("end_ms", pa.int64()),
    ("tick_size", pa.float64()),
    ("min_order_size", pa.float64()),
    ("neg_risk", pa.bool_()),
    ("payload_json", pa.string()),
])

PM_EVENT_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("event_ms", pa.int64()),
    ("event_type", pa.string()),
    ("session_id", pa.string()),
    ("market", pa.string()),
    ("asset_id", pa.string()),
    ("outcome", pa.string()),
    ("best_bid", pa.float64()),
    ("best_ask", pa.float64()),
    ("spread", pa.float64()),
    ("old_tick_size", pa.float64()),
    ("new_tick_size", pa.float64()),
    ("payload_json", pa.string()),
])

PM_REFERENCE_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()),
    ("event_ms", pa.int64()),
    ("source", pa.string()),
    ("symbol", pa.string()),
    ("session_id", pa.string()),
    ("price", pa.float64()),
    ("topic", pa.string()),
    ("payload_json", pa.string()),
])


def _as_ms(value, fallback: int) -> int:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return int(parsed.timestamp() * 1000)
        except (TypeError, ValueError):
            return fallback
    if not math.isfinite(parsed) or parsed <= 0:
        return fallback
    return int(parsed * 1000 if parsed < 10_000_000_000 else parsed)


def _optional_float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _parse_pm_slug(slug: str) -> tuple[int, int]:
    horizon = anchor = 0
    for part in str(slug).split("-"):
        if part.endswith("m") and part[:-1].isdigit():
            horizon = int(part[:-1])
        elif part.isdigit() and len(part) >= 9:
            anchor = int(part)
    return horizon, anchor


def pm_candidate_slugs(now_s: int, prefix: str = "btc-updown") -> list[str]:
    slugs = []
    for horizon in (5, 15):
        width = horizon * 60
        anchor = now_s // width * width
        slugs.extend(f"{prefix}-{horizon}m-{anchor + offset * width}"
                     for offset in (-1, 0, 1, 2, 3))
    return slugs


def classify_spot_depth(prior: int | None, first_id: int, final_id: int) -> tuple[str, bool]:
    if prior is None:
        return "GAP", True
    if final_id <= prior:
        return "STALE", False
    if first_id > prior + 1:
        return "GAP", True
    return "APPLIED", False


def classify_futures_depth(prior: int | None, snapshot_id: int | None,
                           first_id: int, final_id: int,
                           prev_final_id: int | None) -> tuple[str, bool]:
    if prior is None:
        return "GAP", True
    if prior == snapshot_id:
        if final_id < prior:
            return "STALE", False
        state = "APPLIED" if first_id <= prior <= final_id else "GAP"
    elif final_id <= prior:
        return "STALE", False
    elif prev_final_id is None:
        state = "APPLIED" if first_id <= prior + 1 <= final_id else "GAP"
    else:
        state = "APPLIED" if prev_final_id == prior else "GAP"
    return state, state == "GAP"


async def _call(callback, *args):
    if callback is None:
        return None
    result = callback(*args)
    return await result if inspect.isawaitable(result) else result


async def _ws_forever(
    url, subscribe, on_msg, name, status_root, backoff_max=60.0, *,
    on_connected=None, on_heartbeat=None, application_ping_s: float | None = None,
    protocol_ping_s: float | None = 20.0,
):
    """Reconnect loop. Every disconnect is recorded, because a reconnect is a data gap."""
    import websockets
    backoff, reconnects = 1.0, 0
    while True:
        session_id = uuid.uuid4().hex
        heartbeat = None
        try:
            async with websockets.connect(
                url, ping_interval=protocol_ping_s, ping_timeout=20 if protocol_ping_s else None,
                open_timeout=15, close_timeout=5, max_queue=8192, max_size=8 << 20,
            ) as ws:
                if subscribe:
                    await ws.send(json.dumps(subscribe))
                await _call(on_connected, session_id)
                backoff = 1.0
                write_status(status_root, name, {
                    "connected": True, "reconnects": reconnects,
                    "session_id": session_id, "connected_since_utc": time.time(),
                    "last_error": None,
                })

                async def keepalive():
                    while True:
                        await asyncio.sleep(application_ping_s or 30.0)
                        if application_ping_s:
                            await ws.send("PING")
                        await _call(on_heartbeat)
                        write_status(status_root, name, {
                            "connected": True, "reconnects": reconnects,
                            "session_id": session_id,
                        })

                heartbeat = asyncio.create_task(keepalive())
                async for raw in ws:
                    if raw in ("PONG", b"PONG"):
                        continue
                    await _call(on_msg, raw)
        except asyncio.CancelledError:
            raise
        except Exception as exc:                                   # noqa: BLE001
            reconnects += 1
            write_status(status_root, name,
                         {"connected": False, "reconnects": reconnects,
                          "last_error": str(exc)[:200]})
            await asyncio.sleep(backoff)
            backoff = min(backoff_max, backoff * 2)
        finally:
            if heartbeat is not None:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)


def _validated_snapshot(payload: dict) -> tuple[int, list, list]:
    update_id = int(payload.get("lastUpdateId") or 0)
    bids, asks = payload.get("bids"), payload.get("asks")
    if update_id <= 0 or not isinstance(bids, list) or not isinstance(asks, list):
        raise ValueError("invalid Binance depth snapshot")
    if not bids or not asks:
        raise ValueError("Binance depth snapshot is missing one side")
    for side in (bids, asks):
        for level in side:
            if len(level) < 2 or not math.isfinite(float(level[0])) or not math.isfinite(float(level[1])):
                raise ValueError("non-finite Binance depth snapshot level")
    return update_id, bids, asks


async def record_depth_snapshot(
    url: str, symbol: str, root, stream: str, writer: PartitionWriter, session_id: str,
) -> int:
    request_ms = int(time.time() * 1000)

    def fetch():
        req = urllib.request.Request(url, headers={"User-Agent": "btc-capture/1.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read())

    payload = await asyncio.to_thread(fetch)
    response_ms = int(time.time() * 1000)
    update_id, bids, asks = _validated_snapshot(payload)
    for side, levels in (("bid", bids), ("ask", asks)):
        ordered = sorted(levels, key=lambda row: float(row[0]), reverse=side == "bid")
        for level, row in enumerate(ordered):
            writer.add({
                "ts_ms": response_ms, "request_ms": request_ms, "symbol": symbol,
                "session_id": session_id, "last_update_id": update_id, "side": side,
                "price": float(row[0]), "qty": float(row[1]), "level": level,
            })
    writer.flush()
    write_status(root, stream, {
        "rows": writer.rows_written, "files": writer.files_written,
        "session_id": session_id, "last_update_id": update_id,
        "last_data_utc": time.time(), "last_error": None,
    })
    return update_id


async def binance_depth(symbol: str, root, stop: asyncio.Event) -> None:
    w = PartitionWriter(root, "binance_depth", DEPTH_SCHEMA)
    snapshots = PartitionWriter(
        root, "binance_depth_snapshot", DEPTH_SNAPSHOT_SCHEMA,
        max_rows=20_000, max_seconds=60,
    )
    state = {"last_final": None, "gaps": 0, "rows": 0, "session_id": ""}

    async def on_connected(session_id: str):
        state["session_id"] = session_id
        state["last_final"] = await record_depth_snapshot(
            f"{BINANCE_REST}/depth?symbol={symbol}&limit=5000",
            symbol, root, "binance_depth_snapshot", snapshots, session_id,
        )

    def on_msg(raw):
        m = json.loads(raw)
        if m.get("e") != "depthUpdate":
            return
        now = int(time.time() * 1000)
        U, u = int(m["U"]), int(m["u"])
        prior = state["last_final"]
        sequence_state, gap = classify_spot_depth(prior, U, u)
        if gap:
            state["gaps"] += 1
        if sequence_state != "STALE":
            state["last_final"] = u
        for side, key in (("bid", "b"), ("ask", "a")):
            for lvl in m.get(key, ()):
                w.add({"ts_ms": now, "event_ms": int(m.get("E") or now),
                       "symbol": symbol, "session_id": state["session_id"],
                       "transaction_ms": None,
                       "first_id": U, "final_id": u, "side": side,
                       "prev_final_id": None,
                       "price": float(lvl[0]), "qty": float(lvl[1]), "gap": gap,
                       "sequence_state": sequence_state})
                state["rows"] += 1
                gap = False        # flag only the first row of the break
        write_status(root, "binance_depth",
                      {"rows": state["rows"], "gaps": state["gaps"],
                       "files": w.files_written, "last_final_id": state["last_final"],
                       "last_data_utc": time.time()})
        if sequence_state == "GAP":
            raise RuntimeError("spot depth sequence gap; reconnecting for fresh snapshot")

    def heartbeat():
        w.flush_due()
        snapshots.flush_due()

    task = asyncio.create_task(_ws_forever(
        f"{BINANCE_WS}/{symbol.lower()}@depth@100ms", None, on_msg, "binance_depth", root,
        on_connected=on_connected, on_heartbeat=heartbeat,
    ))
    await stop.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    w.flush()
    snapshots.flush()
    write_status(root, "binance_depth", {
        "rows": state["rows"], "files": w.files_written, "connected": False,
        "stopped_cleanly": True,
    })
    write_status(root, "binance_depth_snapshot", {
        "rows": snapshots.rows_written, "files": snapshots.files_written,
    })


async def binance_trades(symbol: str, root, stop: asyncio.Event) -> None:
    w = PartitionWriter(root, "binance_trades", TRADE_SCHEMA)
    n = {"rows": 0}

    def on_msg(raw):
        m = json.loads(raw)
        if m.get("e") != "aggTrade":
            return
        now = int(time.time() * 1000)
        w.add({"ts_ms": now, "event_ms": int(m.get("E") or now), "symbol": symbol,
               "source": "ws", "agg_id": int(m["a"]),
               "trade_ms": int(m.get("T") or 0),
               "first_trade_id": int(m.get("f") or 0),
               "last_trade_id": int(m.get("l") or 0),
               "price": float(m["p"]), "qty": float(m["q"]),
               "buyer_is_maker": bool(m["m"])})
        n["rows"] += 1
        write_status(root, "binance_trades", {
            "rows": n["rows"], "files": w.files_written, "last_data_utc": time.time(),
        })

    task = asyncio.create_task(_ws_forever(
        f"{BINANCE_WS}/{symbol.lower()}@aggTrade", None, on_msg, "binance_trades", root,
        on_heartbeat=w.flush_due,
    ))
    await stop.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    w.flush()
    write_status(root, "binance_trades", {
        "rows": n["rows"], "files": w.files_written, "connected": False,
        "stopped_cleanly": True,
    })


def discover_pm_markets(slug_contains: str = "btc-updown", limit: int = 500) -> list[dict]:
    """Active BTC up/down markets and their two token ids.

    Uses /events (not /markets) - the repo established that /markets?slug= silently ignores the
    filter and returns unrelated rows.
    """
    del limit  # exact-slug discovery is intentionally independent of global pagination
    now_s = int(time.time())
    slugs = pm_candidate_slugs(now_s, slug_contains)
    events = []
    for slug in slugs:
        url = f"{PM_GAMMA}/events?slug={urllib.parse.quote(slug, safe='')}"
        req = urllib.request.Request(url, headers={"User-Agent": "btc-capture/1.0"})
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read())
        if isinstance(payload, list):
            events.extend(payload)
    out = []
    seen_conditions = set()
    for ev in events:
        if slug_contains not in str(ev.get("slug", "")):
            continue
        for mk in ev.get("markets", []) or []:
            try:
                toks = json.loads(mk.get("clobTokenIds") or "[]")
                outs = json.loads(mk.get("outcomes") or "[]")
            except Exception:
                continue
            condition = str(mk.get("conditionId") or "")
            slug = str(ev.get("slug") or "")
            if (len(toks) == 2 and len(outs) == 2 and condition
                    and condition not in seen_conditions):
                horizon, anchor = _parse_pm_slug(slug)
                if horizon and anchor:
                    seen_conditions.add(condition)
                    out.append({"condition_id": condition, "slug": slug,
                                "horizon": horizon, "anchor_ts": anchor,
                                "tokens": [str(token) for token in toks],
                                "outcomes": [str(outcome).upper() for outcome in outs],
                                "question": str(mk.get("question") or ev.get("title") or ""),
                                "end_ms": _as_ms(mk.get("endDate") or ev.get("endDate"), 0),
                                "tick_size": _optional_float(mk.get("orderPriceMinTickSize")),
                                "min_order_size": _optional_float(mk.get("orderMinSize")),
                                "neg_risk": bool(mk.get("negRisk") or ev.get("negRisk")),
                                "payload_json": json.dumps(
                                    {"event": ev, "market": mk}, separators=(",", ":"),
                                    sort_keys=True,
                                )})
    return out


def parse_pm_events(raw, tok_meta: dict[str, dict], received_ms: int | None = None,
                    session_id: str = "") -> dict:
    """Normalize every documented CLOB market event into durable row families."""
    now = int(received_ms or time.time() * 1000)
    try:
        messages = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {"book": [], "trades": [], "events": []}
    if isinstance(messages, dict):
        messages = [messages]
    if not isinstance(messages, list):
        return {"book": [], "trades": [], "events": []}
    result = {"book": [], "trades": [], "events": []}
    for message in messages:
        if not isinstance(message, dict):
            continue
        event_type = str(message.get("event_type") or message.get("type") or "")
        event_ms = _as_ms(message.get("timestamp"), now)
        asset_id = str(message.get("asset_id") or "")
        meta = tok_meta.get(asset_id, {})
        market = str(message.get("market") or meta.get("condition_id") or "")
        outcome = str(meta.get("outcome") or "")
        if event_type == "book":
            bids = sorted(message.get("bids") or (),
                          key=lambda level: float(level["price"]), reverse=True)
            asks = sorted(message.get("asks") or (), key=lambda level: float(level["price"]))
            best_bid = float(bids[0]["price"]) if bids else None
            best_ask = float(asks[0]["price"]) if asks else None
            for side, levels in (("bid", bids), ("ask", asks)):
                for level, quote in enumerate(levels):
                    result["book"].append({
                        "ts_ms": now, "event_ms": event_ms, "market": market,
                        "asset_id": asset_id, "outcome": outcome, "event_type": "book",
                        "session_id": session_id,
                        "side": side, "price": float(quote["price"]),
                        "size": float(quote["size"]), "level": level,
                        "hash": str(message.get("hash") or ""),
                        "best_bid": best_bid, "best_ask": best_ask,
                    })
        elif event_type == "price_change":
            for change in message.get("price_changes") or ():
                change_asset = str(change.get("asset_id") or asset_id)
                change_meta = tok_meta.get(change_asset, meta)
                side_raw = str(change.get("side") or "").upper()
                side = "bid" if side_raw == "BUY" else "ask" if side_raw == "SELL" else ""
                if not side:
                    continue
                result["book"].append({
                    "ts_ms": now, "event_ms": event_ms,
                    "market": str(message.get("market") or change_meta.get("condition_id") or ""),
                    "asset_id": change_asset, "outcome": str(change_meta.get("outcome") or ""),
                    "session_id": session_id, "event_type": "price_change", "side": side,
                    "price": float(change["price"]), "size": float(change["size"]),
                    "level": -1, "hash": str(change.get("hash") or message.get("hash") or ""),
                    "best_bid": _optional_float(change.get("best_bid")),
                    "best_ask": _optional_float(change.get("best_ask")),
                })
        elif event_type == "last_trade_price":
            result["trades"].append({
                "ts_ms": now, "event_ms": event_ms, "market": market,
                "asset_id": asset_id, "outcome": outcome,
                "session_id": session_id,
                "side": str(message.get("side") or ""),
                "price": float(message.get("price") or 0),
                "size": float(message.get("size") or 0),
                "fee_rate_bps": float(message.get("fee_rate_bps") or 0),
                "transaction_hash": str(message.get("transaction_hash") or ""),
            })
        else:
            best_bid = _optional_float(message.get("best_bid"))
            best_ask = _optional_float(message.get("best_ask"))
            result["events"].append({
                "ts_ms": now, "event_ms": event_ms, "event_type": event_type or "unknown",
                "session_id": session_id,
                "market": market, "asset_id": asset_id, "outcome": outcome,
                "best_bid": best_bid, "best_ask": best_ask,
                "spread": (best_ask - best_bid
                           if best_bid is not None and best_ask is not None else None),
                "old_tick_size": _optional_float(message.get("old_tick_size")),
                "new_tick_size": _optional_float(message.get("new_tick_size")),
                "payload_json": json.dumps(message, separators=(",", ":"), sort_keys=True),
            })
    return result


async def polymarket_books(root, stop: asyncio.Event, refresh_s: int = 120) -> None:
    """Capture PM snapshots, level deltas, trades, metadata and lifecycle events."""
    w = PartitionWriter(root, "polymarket_book", PM_BOOK_SCHEMA)
    wt = PartitionWriter(root, "polymarket_trades", PM_TRADE_SCHEMA, max_rows=2_000)
    wm = PartitionWriter(root, "polymarket_market_meta", PM_META_SCHEMA, max_rows=200)
    we = PartitionWriter(root, "polymarket_market_events", PM_EVENT_SCHEMA, max_rows=2_000)
    n = {"rows": 0, "trades": 0, "events": 0, "markets": 0,
         "started_utc": time.time()}
    tok_meta: dict[str, dict] = {}
    metadata_seen: set[tuple[str, str]] = set()
    session = {"id": ""}

    def on_connected(session_id: str):
        session["id"] = session_id

    def on_msg(raw):
        parsed = parse_pm_events(raw, tok_meta, session_id=session["id"])
        for row in parsed["book"]:
            w.add(row)
        for row in parsed["trades"]:
            wt.add(row)
        for row in parsed["events"]:
            we.add(row)
        n["rows"] += len(parsed["book"])
        n["trades"] += len(parsed["trades"])
        n["events"] += len(parsed["events"])
        write_status(root, "polymarket_book", {
            **n, "files": w.files_written, "last_data_utc": time.time(),
        })
        trade_status = {"rows": n["trades"], "files": wt.files_written}
        if parsed["trades"]:
            trade_status["last_data_utc"] = time.time()
        write_status(root, "polymarket_trades", trade_status)

    def heartbeat():
        for writer in (w, wt, wm, we):
            writer.flush_due()
        now = time.time()
        write_status(root, "polymarket_trades", {
            "connected": True, "rows": n["trades"], "files": wt.files_written,
            "connected_since_utc": n["started_utc"],
        })
        write_status(root, "polymarket_market_meta", {
            "connected": True, "rows": len(metadata_seen), "files": wm.files_written,
            "last_data_utc": now if metadata_seen else None,
            "connected_since_utc": n["started_utc"],
        })
        write_status(root, "polymarket_market_events", {
            "connected": True, "rows": n["events"], "files": we.files_written,
            "connected_since_utc": n["started_utc"],
        })

    async def run():
        current_tokens: tuple[str, ...] = ()
        ws_task = None
        while not stop.is_set():
            try:
                mkts = await asyncio.to_thread(discover_pm_markets)
            except Exception as exc:                               # noqa: BLE001
                write_status(root, "polymarket_book", {"discovery_error": str(exc)[:200]})
                await asyncio.sleep(30)
                continue
            write_status(root, "polymarket_book", {"discovery_error": None})
            tokens = []
            metadata_added = False
            for mk in mkts:
                for tok, oc in zip(mk["tokens"], mk["outcomes"] or ["", ""]):
                    tok_meta[str(tok)] = {"condition_id": mk["condition_id"], "outcome": oc,
                                          "slug": mk["slug"], "horizon": mk["horizon"],
                                          "anchor_ts": mk["anchor_ts"]}
                    tokens.append(str(tok))
                    key = (mk["condition_id"], str(tok))
                    if key not in metadata_seen:
                        wm.add({"ts_ms": int(time.time() * 1000), "slug": mk["slug"],
                                "condition_id": mk["condition_id"],
                                "horizon": mk["horizon"], "anchor_ts": mk["anchor_ts"],
                                "asset_id": str(tok), "outcome": str(oc),
                                "question": mk["question"], "end_ms": mk["end_ms"],
                                "tick_size": mk["tick_size"],
                                "min_order_size": mk["min_order_size"],
                                "neg_risk": mk["neg_risk"],
                                "payload_json": mk["payload_json"]})
                        metadata_seen.add(key)
                        metadata_added = True
            if metadata_added:
                wm.flush()
            n["markets"] = len(mkts)
            token_tuple = tuple(sorted(set(tokens)))
            if token_tuple and token_tuple != current_tokens:
                if ws_task is not None:
                    ws_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await ws_task
                sub = {"assets_ids": list(token_tuple), "type": "market",
                       "custom_feature_enabled": True}
                ws_task = asyncio.create_task(_ws_forever(
                    PM_CLOB_WS, sub, on_msg, "polymarket_book", root,
                    on_connected=on_connected,
                    on_heartbeat=heartbeat, application_ping_s=10, protocol_ping_s=None,
                ))
                current_tokens = token_tuple
            try:
                await asyncio.wait_for(stop.wait(), timeout=refresh_s)
            except asyncio.TimeoutError:
                pass
        if ws_task is not None:
            ws_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ws_task

    await run()
    for writer in (w, wt, wm, we):
        writer.flush()
    for name, writer, rows in (
        ("polymarket_book", w, n["rows"]),
        ("polymarket_trades", wt, n["trades"]),
        ("polymarket_market_meta", wm, len(metadata_seen)),
        ("polymarket_market_events", we, n["events"]),
    ):
        write_status(root, name, {"rows": rows, "files": writer.files_written,
                                  "connected": False, "stopped_cleanly": True})


def parse_pm_references(raw, received_ms: int | None = None,
                        session_id: str = "") -> list[dict]:
    now = int(received_ms or time.time() * 1000)
    try:
        message = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    payload = message.get("payload") if isinstance(message, dict) else None
    if not isinstance(payload, dict):
        return []
    topic = str(message.get("topic") or "")
    source = "chainlink" if "chainlink" in topic.lower() else "binance"
    symbol = str(payload.get("symbol") or ("btc/usd" if source == "chainlink" else "btcusdt"))
    values = payload.get("data") if isinstance(payload.get("data"), list) else [payload]
    rows = []
    for value in values:
        if not isinstance(value, dict):
            continue
        price = _optional_float(value.get("value") or value.get("price"))
        if price is None or price <= 0:
            continue
        event_ms = _as_ms(value.get("timestamp") or message.get("timestamp"), now)
        raw_item = {"topic": topic, "type": message.get("type"), "payload": value,
                    "symbol": symbol}
        rows.append({
            "ts_ms": now, "event_ms": event_ms, "source": source, "symbol": symbol,
            "session_id": session_id, "price": price, "topic": topic,
            "payload_json": json.dumps(raw_item, separators=(",", ":"), sort_keys=True),
        })
    return rows


def parse_pm_reference(raw, received_ms: int | None = None,
                       session_id: str = "") -> dict | None:
    """Compatibility helper for callers expecting one scalar row."""
    rows = parse_pm_references(raw, received_ms, session_id)
    return rows[0] if rows else None


async def polymarket_reference(root, stop: asyncio.Event) -> None:
    """Capture the exact Binance and Chainlink reference feeds published by Polymarket."""
    writer = PartitionWriter(root, "polymarket_reference", PM_REFERENCE_SCHEMA,
                             max_rows=2_000, max_seconds=30)
    stats = {"rows": 0}
    session = {"id": ""}

    def on_connected(session_id: str):
        session["id"] = session_id

    def on_msg(raw):
        rows = parse_pm_references(raw, session_id=session["id"])
        if not rows:
            return
        for row in rows:
            writer.add(row)
        stats["rows"] += len(rows)
        write_status(root, "polymarket_reference", {
            "rows": stats["rows"], "files": writer.files_written,
            "last_data_utc": time.time(),
        })

    subscribe = {
        "action": "subscribe",
        "subscriptions": [
            {"topic": "crypto_prices", "type": "update", "filters": "btcusdt"},
            {"topic": "crypto_prices_chainlink", "type": "*",
             "filters": json.dumps({"symbol": "btc/usd"}, separators=(",", ":"))},
        ],
    }
    task = asyncio.create_task(_ws_forever(
        PM_RTDS_WS, subscribe, on_msg, "polymarket_reference", root,
        on_connected=on_connected,
        on_heartbeat=writer.flush_due, application_ping_s=5, protocol_ping_s=None,
    ))
    await stop.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    writer.flush()
    write_status(root, "polymarket_reference", {
        "rows": stats["rows"], "files": writer.files_written, "connected": False,
        "stopped_cleanly": True,
    })
