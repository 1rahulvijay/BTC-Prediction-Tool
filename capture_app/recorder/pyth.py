"""Independent Pyth BTC/USD source with publication time and confidence interval."""
from __future__ import annotations

import asyncio
import json
import math
import os
import time
import urllib.parse
import urllib.request

import pyarrow as pa

from .storage import PartitionWriter, write_status

PYTH_BTC_ID = "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43"
PYTH_URL = "https://pyth.dourolabs.app/hermes/v2/updates/price/latest"

PYTH_SCHEMA = pa.schema([
    ("ts_ms", pa.int64()), ("request_ns", pa.int64()), ("recv_ns", pa.int64()),
    ("publish_ms", pa.int64()), ("feed_id", pa.string()), ("price", pa.float64()),
    ("confidence", pa.float64()), ("expo", pa.int32()), ("raw_price", pa.string()),
    ("raw_conf", pa.string()), ("ema_publish_ms", pa.int64()),
    ("ema_price", pa.float64()), ("ema_confidence", pa.float64()),
    ("payload_json", pa.string()),
])


def parse_pyth(payload: dict, request_ns: int, recv_ns: int,
               feed_id: str = PYTH_BTC_ID) -> dict | None:
    parsed = payload.get("parsed") if isinstance(payload, dict) else None
    if not isinstance(parsed, list):
        return None
    item = next((row for row in parsed if str(row.get("id") or "").lower().removeprefix("0x")
                 == feed_id.lower().removeprefix("0x")), None)
    if not item:
        return None
    price, ema = item.get("price") or {}, item.get("ema_price") or {}
    try:
        expo = int(price["expo"])
        value = float(price["price"]) * (10 ** expo)
        confidence = float(price["conf"]) * (10 ** expo)
        publish_ms = int(price["publish_time"]) * 1000
        ema_expo = int(ema.get("expo", expo))
        ema_value = float(ema["price"]) * (10 ** ema_expo)
        ema_conf = float(ema["conf"]) * (10 ** ema_expo)
        ema_publish_ms = int(ema["publish_time"]) * 1000
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(x) for x in (value, confidence, ema_value, ema_conf)):
        return None
    return {
        "ts_ms": recv_ns // 1_000_000, "request_ns": request_ns, "recv_ns": recv_ns,
        "publish_ms": publish_ms, "feed_id": feed_id.lower().removeprefix("0x"),
        "price": value, "confidence": confidence, "expo": expo,
        "raw_price": str(price["price"]), "raw_conf": str(price["conf"]),
        "ema_publish_ms": ema_publish_ms, "ema_price": ema_value,
        "ema_confidence": ema_conf,
        "payload_json": json.dumps(item, separators=(",", ":"), sort_keys=True),
    }


async def pyth_reference(root, stop: asyncio.Event, interval_s: float = 2.0,
                         endpoint: str = PYTH_URL) -> None:
    writer = PartitionWriter(root, "pyth_reference", PYTH_SCHEMA, max_rows=5_000,
                             max_seconds=60)
    state = {"rows": 0, "errors": 0}

    def fetch():
        request_ns = time.time_ns()
        query = urllib.parse.urlencode({"ids[]": PYTH_BTC_ID})
        req = urllib.request.Request(f"{endpoint}?{query}",
                                     headers={"User-Agent": "btc-capture/1.0"})
        api_key = os.environ.get("PYTH_API_KEY")
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = json.loads(response.read())
        return payload, request_ns, time.time_ns()

    while not stop.is_set():
        try:
            payload, request_ns, recv_ns = await asyncio.to_thread(fetch)
            row = parse_pyth(payload, request_ns, recv_ns)
            if row is None:
                raise ValueError("Pyth response did not contain a valid BTC/USD observation")
            writer.add(row)
            state["rows"] += 1
            write_status(root, "pyth_reference", {
                **state, "files": writer.files_written, "last_data_utc": time.time(),
                "publish_age_seconds": max(0.0, time.time() - row["publish_ms"] / 1000),
                "last_error": None,
            })
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            state["errors"] += 1
            write_status(root, "pyth_reference", {**state, "files": writer.files_written,
                                                   "last_error": str(exc)[:300]})
        writer.flush_due()
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            pass
    writer.flush()
    write_status(root, "pyth_reference", {
        **state, "files": writer.files_written, "stopped_cleanly": True,
    })
