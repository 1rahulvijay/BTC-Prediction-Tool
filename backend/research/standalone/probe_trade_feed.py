"""
probe_trade_feed.py - is the live SPOT aggTrade stream actually delivering trades to THIS box?
===============================================================================================
probe_feature_parity.py proved cvd/vpin/large_trade are DEAD-IN-LIVE: the OrderFlowAnalyzer has no
trade data. The analyzer is fed by data_ingestion's SPOT WS (btcusdt@aggTrade -> "trade" event ->
handle_trade -> process_trade). This probe connects to the SAME endpoint and counts aggTrades, to
split the two possible root causes:
  * ZERO trades arrive  -> the endpoint is NOT delivering to this box (geo/network/endpoint). The
    depth stream keeps order_flow "alive" (false green) while trade features stay zero. Root = feed.
  * Trades DO arrive     -> the endpoint works; the bug is in the app's emit/handle/process chain.

Makes its OWN connection (does not touch the app's WS or DB), so run it WITH the app up.

Usage:
  python backend/research/standalone/probe_trade_feed.py            # ~20s live count (run on the box that runs the app)
  python backend/research/standalone/probe_trade_feed.py --secs 30
  python backend/research/standalone/probe_trade_feed.py --selftest # validates the message parser offline
"""
from __future__ import annotations

try:
    from . import _bootstrap as _research_bootstrap  # noqa: F401
except ImportError:
    import _bootstrap as _research_bootstrap  # noqa: F401

del _research_bootstrap


import argparse
import json
import sys
import time

# Same endpoint/stream the app's order-flow feed uses (data_ingestion.py:26-27).
URL = "wss://stream.binance.com:9443/stream?streams=btcusdt@aggTrade"


def parse_aggtrade(raw: str):
    """Return the aggTrade payload dict if `raw` is one, else None. Handles combined-stream
    ({"stream":..,"data":{..}}) and raw ({"e":"aggTrade",..}) shapes."""
    try:
        d = json.loads(raw)
    except Exception:
        return None
    data = d.get("data", d) if isinstance(d, dict) else None
    if isinstance(data, dict) and data.get("e") == "aggTrade":
        return data
    return None


def _run_live(secs: int):
    try:
        import asyncio
        import websockets
    except Exception as e:
        print(f"need the websockets package (same as the app): {e}")
        return

    async def go():
        n = 0
        sample = None
        print(f"connecting: {URL}")
        try:
            async with websockets.connect(URL, ping_interval=20, ping_timeout=20) as ws:
                print(f"connected. counting aggTrades for {secs}s ...")
                t0 = time.time()
                while time.time() - t0 < secs:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=6)
                    except asyncio.TimeoutError:
                        print("  (no message in 6s)")
                        continue
                    tr = parse_aggtrade(msg)
                    if tr is not None:
                        n += 1
                        if sample is None:
                            sample = tr
        except Exception as e:
            print(f"CONNECT/RECV ERROR: {type(e).__name__}: {str(e)[:160]}")
            print("  -> could not receive from the spot aggTrade endpoint on this box.")
            return
        print()
        print("=" * 72)
        print(f"aggTrades received in {secs}s: {n}  (~{n/max(1,secs):.1f}/s)")
        if sample is not None:
            print(f"  sample trade: price={sample.get('p')} qty={sample.get('q')} "
                  f"is_buyer_maker={sample.get('m')}")
        print("=" * 72)
        if n == 0:
            print("VERDICT: ZERO spot aggTrades reached this box. The TRADE feed is the root cause —")
            print("  the OrderFlowAnalyzer never gets trades, so cvd/vpin/large_trade are dead-in-live,")
            print("  while the depth stream keeps the §5bw 'feed alive' guard falsely green.")
            print("  Fix at the FEED: wrong/blocked endpoint or a silently-failed subscription.")
        else:
            print("VERDICT: spot aggTrades DO arrive on this box. The endpoint works ->")
            print("  the bug is in the APP's chain: data_ingestion emit 'trade' -> server.handle_trade")
            print("  -> order_flow.process_trade (one of these isn't accumulating). Trace there next.")

    asyncio.run(go())


def selftest():
    combined = json.dumps({"stream": "btcusdt@aggTrade",
                           "data": {"e": "aggTrade", "p": "63000.1", "q": "0.5", "m": False}})
    raw = json.dumps({"e": "aggTrade", "p": "63000.1", "q": "0.5", "m": True})
    depth = json.dumps({"stream": "btcusdt@depth20@100ms", "data": {"e": "depthUpdate", "bids": []}})
    assert parse_aggtrade(combined) is not None, "combined aggTrade not parsed"
    assert parse_aggtrade(raw) is not None, "raw aggTrade not parsed"
    assert parse_aggtrade(depth) is None, "depth wrongly parsed as trade"
    assert parse_aggtrade("not json") is None
    print("probe_trade_feed self-test: aggTrade parser OK (combined+raw recognized, depth ignored). ALL PASS")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        ap = argparse.ArgumentParser()
        ap.add_argument("--secs", type=int, default=20)
        a = ap.parse_args()
        _run_live(a.secs)
