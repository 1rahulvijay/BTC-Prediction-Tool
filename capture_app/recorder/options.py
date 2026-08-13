"""Periodic full public Deribit BTC option-chain snapshots."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pyarrow as pa

from .storage import PartitionWriter, write_status

DERIBIT_URL = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"

DERIBIT_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()), ("request_ns", pa.int64()), ("recv_ns", pa.int64()),
    ("batch_id", pa.string()), ("response_sha256", pa.string()),
    ("exchange_ms", pa.int64()), ("instrument_name", pa.string()),
    ("expiry_ms", pa.int64()), ("strike", pa.float64()), ("option_type", pa.string()),
    ("underlying_index", pa.string()), ("underlying_price", pa.float64()),
    ("bid_price", pa.float64()), ("ask_price", pa.float64()),
    ("mid_price", pa.float64()), ("mark_price", pa.float64()),
    ("mark_iv_pct", pa.float64()), ("bid_iv_pct", pa.float64()),
    ("ask_iv_pct", pa.float64()), ("open_interest", pa.float64()),
    ("volume", pa.float64()), ("interest_rate", pa.float64()),
    ("estimated_delivery_price", pa.float64()), ("base_currency", pa.string()),
    ("quote_currency", pa.string()), ("payload_json", pa.string()),
])


def _finite(value, *, nonnegative: bool = False):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or (nonnegative and number < 0):
        return None
    return number


def parse_instrument(name: str) -> tuple[int, float, str] | None:
    parts = str(name).split("-")
    if len(parts) != 4 or parts[0].upper() != "BTC" or parts[3].upper() not in {"C", "P"}:
        return None
    try:
        expiry = datetime.strptime(parts[1].title(), "%d%b%y").replace(
            tzinfo=timezone.utc, hour=8,
        )
        strike = float(parts[2])
    except (TypeError, ValueError):
        return None
    return (int(expiry.timestamp() * 1000), strike, parts[3].upper()) if strike > 0 else None


def normalize_deribit(payload: dict, request_ns: int, recv_ns: int) -> list[dict]:
    canonical = json.dumps(payload, separators=(",", ":"), sort_keys=True,
                           ensure_ascii=True, default=str).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    batch_id = hashlib.sha256(f"{request_ns}:{recv_ns}:{digest}".encode()).hexdigest()[:24]
    rows = []
    for item in payload.get("result") or []:
        if not isinstance(item, dict):
            continue
        parsed = parse_instrument(str(item.get("instrument_name") or ""))
        if parsed is None:
            continue
        expiry_ms, strike, option_type = parsed
        exchange_ms = item.get("creation_timestamp")
        try:
            exchange_ms = int(exchange_ms) if exchange_ms is not None else None
        except (TypeError, ValueError):
            exchange_ms = None
        rows.append({
            "ts_ms": recv_ns // 1_000_000, "request_ns": request_ns, "recv_ns": recv_ns,
            "batch_id": batch_id, "response_sha256": digest,
            "exchange_ms": exchange_ms if exchange_ms and exchange_ms > 0 else None,
            "instrument_name": str(item.get("instrument_name")), "expiry_ms": expiry_ms,
            "strike": strike, "option_type": option_type,
            "underlying_index": str(item.get("underlying_index") or "") or None,
            "underlying_price": _finite(item.get("underlying_price"), nonnegative=True),
            "bid_price": _finite(item.get("bid_price"), nonnegative=True),
            "ask_price": _finite(item.get("ask_price"), nonnegative=True),
            "mid_price": _finite(item.get("mid_price"), nonnegative=True),
            "mark_price": _finite(item.get("mark_price"), nonnegative=True),
            "mark_iv_pct": _finite(item.get("mark_iv"), nonnegative=True),
            "bid_iv_pct": _finite(item.get("bid_iv"), nonnegative=True),
            "ask_iv_pct": _finite(item.get("ask_iv"), nonnegative=True),
            "open_interest": _finite(item.get("open_interest"), nonnegative=True),
            "volume": _finite(item.get("volume"), nonnegative=True),
            "interest_rate": _finite(item.get("interest_rate")),
            "estimated_delivery_price": _finite(item.get("estimated_delivery_price"),
                                                nonnegative=True),
            "base_currency": str(item.get("base_currency") or "") or None,
            "quote_currency": str(item.get("quote_currency") or "") or None,
            "payload_json": json.dumps(item, separators=(",", ":"), sort_keys=True),
        })
    return rows


async def deribit_options(root, stop: asyncio.Event, interval_s: int = 60) -> None:
    writer = PartitionWriter(root, "deribit_options", DERIBIT_SCHEMA, max_rows=20_000,
                             max_seconds=60)
    state = {"rows": 0, "batches": 0, "errors": 0}

    def fetch():
        request_ns = time.time_ns()
        query = urllib.parse.urlencode({"currency": "BTC", "kind": "option"})
        req = urllib.request.Request(f"{DERIBIT_URL}?{query}",
                                     headers={"User-Agent": "btc-capture/1.0"})
        with urllib.request.urlopen(req, timeout=20) as response:
            payload = json.loads(response.read())
        return payload, request_ns, time.time_ns()

    while not stop.is_set():
        try:
            payload, request_ns, recv_ns = await asyncio.to_thread(fetch)
            if not isinstance(payload, dict) or payload.get("error"):
                raise ValueError(f"Deribit RPC error: {payload.get('error') if isinstance(payload, dict) else 'invalid JSON'}")
            rows = normalize_deribit(payload, request_ns, recv_ns)
            if not rows:
                raise ValueError("Deribit returned no valid BTC option rows")
            for row in rows:
                writer.add(row)
            writer.flush()
            state["rows"] += len(rows)
            state["batches"] += 1
            write_status(root, "deribit_options", {
                **state, "files": writer.files_written, "latest_batch_rows": len(rows),
                "last_data_utc": time.time(), "last_error": None,
            })
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            state["errors"] += 1
            write_status(root, "deribit_options", {**state, "files": writer.files_written,
                                                    "last_error": str(exc)[:300]})
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
    writer.flush()
