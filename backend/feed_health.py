"""
feed_health.py — is the live microstructure data actually reaching the model? (no-train)
=========================================================================================
The dead-feature scan (§5bw) found 78/136 features zero-variance even live — the entire
microstructure half (CVD, OBI, walls, vpin, liquidations, funding, coinbase premium). This
tool isolates WHY, in two parts:

  PART A — feed reachability: connect DIRECTLY to each exchange stream and see if data flows.
           (On 2026-06-13 from this box: spot aggTrade/depth = OK; futures + Coinbase = BLOCKED,
            i.e. geo-blocked here — so perp CVD / liquidations / coinbase premium are genuinely
            unavailable without a proxy/VPN, NOT a code bug.)
  PART B — app population: if the backend is up, read its live /ws payload and check whether
           order_flow (cvd/obi/vpin) is actually populated. If the spot streams flow (Part A)
           but order_flow is zero here → an app wiring/connectivity bug to fix. If order_flow
           is populated → the overnight zeros were a transient spot-WS disconnect.

Run it while the app is UP. No DB, no app changes, no restart.
Usage:  python backend/feed_health.py
"""
import asyncio
import json
import urllib.request

STREAMS = [
    ("SPOT aggTrade (CVD/vpin)",   "wss://stream.binance.com:9443/ws/btcusdt@aggTrade", "p"),
    ("SPOT depth20 (OBI/walls)",   "wss://stream.binance.com:9443/ws/btcusdt@depth20@100ms", None),
    ("FUTURES aggTrade (perp/A4)", "wss://fstream.binance.com/ws/btcusdt@aggTrade", "p"),
    ("FUTURES forceOrder (liq)",   "wss://fstream.binance.com/ws/btcusdt@forceOrder", None),
    ("Coinbase (premium)",         "wss://ws-feed.exchange.coinbase.com", None),
]


async def _test_stream(name, uri, field):
    import websockets
    try:
        async with websockets.connect(uri, ping_interval=None, open_timeout=10) as ws:
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            sample = msg.get(field) if field else list(msg.keys())[:4]
            return f"  [OK]    {name:30s} data flowing  (sample: {sample})"
    except Exception as e:
        return f"  [BLOCK] {name:30s} {str(e)[:70] or 'connection refused / geo-blocked'}"


async def part_a():
    print("PART A — exchange stream reachability (direct):")
    for name, uri, field in STREAMS:
        print(await _test_stream(name, uri, field))


async def part_b():
    print("\nPART B — is the live app populating order_flow?")
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/runtime-status", timeout=6):
            pass
    except Exception:
        print("  backend not up (port 8000) — start the app, then re-run to check live order_flow.")
        return
    import websockets
    try:
        async with websockets.connect("ws://127.0.0.1:8000/ws", ping_interval=None, open_timeout=10) as ws:
            for _ in range(25):
                try:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
                except Exception:
                    break
                d = msg.get("order_flow") or (msg.get("data") or {}).get("order_flow")
                if isinstance(d, dict) and d:
                    live = {k: d.get(k) for k in ("cvd_1m", "cvd_5m", "obi_5", "obi_10",
                                                  "book_imbalance", "vpin", "trade_intensity")}
                    nonzero = sum(1 for v in live.values() if v not in (None, 0, 0.0))
                    print(f"  live order_flow: {live}")
                    print(f"  -> {nonzero}/{len(live)} microstructure fields non-zero",
                          "= FEEDS ALIVE (overnight zeros were a transient disconnect)" if nonzero >= 3
                          else "= STILL ZERO despite spot streams working -> app wiring/connectivity BUG")
                    return
            print("  order_flow not found in the /ws payload (check the broadcast schema).")
    except Exception as e:
        print(f"  app WS probe failed: {str(e)[:90]}")


async def main():
    await part_a()
    await part_b()
    print("\nReading: spot OK + app order_flow non-zero -> healthy. spot OK + app zero -> fixable bug.")
    print("futures/coinbase BLOCK -> geo-blocked here; perp CVD/liq/coinbase premium need a proxy.")


if __name__ == "__main__":
    asyncio.run(main())
