"""Compare the app's live price-to-beat anchor vs the TRUE Binance boundary price."""
import asyncio, json, urllib.request

async def grab_payload():
    import websockets
    async with websockets.connect("ws://127.0.0.1:8000/ws", max_size=20_000_000) as ws:
        for _ in range(40):                       # wait up to ~40 messages for a full update
            msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            if msg.get("type") == "update" and msg.get("payload", {}).get("price_to_beat"):
                return msg["payload"]
    return None

p = asyncio.get_event_loop().run_until_complete(grab_payload())
if not p:
    print("no payload"); raise SystemExit

ptb = (p.get("price_to_beat") or {}).get("latest") or {}
r5 = ptb.get(5) or ptb.get("5") or {}
print("OUR 5m round:")
print("  window:", r5.get("window_label"), "| status:", r5.get("status"))
print("  price_to_beat:", r5.get("price_to_beat"),
      "| captured_late_ms:", r5.get("ref_captured_late_ms"))
print("  current_price (app):", r5.get("current_price"), "| seconds_left:", r5.get("seconds_left"))
ws_ms = r5.get("window_start")
print("  window_start ms:", ws_ms)

cur = p.get("current_price") or p.get("price")
print("app payload current price:", cur)

# Ground truth from Binance REST: the 1m candle OPEN at the window boundary + live price
if ws_ms:
    u = (f"https://api.binance.com/api/v3/klines?symbol=BTCUSDT&interval=1m"
         f"&startTime={int(ws_ms)}&limit=1")
    k = json.loads(urllib.request.urlopen(u, timeout=10).read())
    true_open = float(k[0][1]) if k else None
    print("TRUE Binance open at boundary:", true_open)
    if true_open and r5.get("price_to_beat"):
        diff = float(r5["price_to_beat"]) - true_open
        print(f"  -> anchor error vs truth: {diff:+.2f} USD")
live = json.loads(urllib.request.urlopen(
    "https://api.binance.com/api/v3/ticker/price?symbol=BTCUSDT", timeout=10).read())
print("Binance live now:", live["price"])
