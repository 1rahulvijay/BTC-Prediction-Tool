#!/usr/bin/env python3
"""
Polymarket BTC 5m "Price to Beat" PROBE — STANDALONE diagnostic. NOT part of the app.
=====================================================================================
Purpose: find the cheapest reliable way to get Polymarket's EXACT 5m price-to-beat from
YOUR network, and measure how far it is from Binance (the app's current reference). Run it
for a few hours, then we decide how (or whether) to integrate.

It tries, lightest-first, each 5-minute window:
  1. GAMMA API event JSON   (plain HTTPS; if this works you need NO browser and NO VPN)
       → dumps the full event JSON to data/gamma_event_sample.json ONCE so we can see
         exactly which field holds the price-to-beat.
  2. PYTH BTC/USD oracle     (plain HTTPS; the likely settlement feed — a direct number)
  3. BINANCE live + boundary kline  (what the app uses today, for the gap comparison)
  4. PLAYWRIGHT page scrape  (your original method; needs the site reachable / VPN)
       → OFF by default. Add --scrape to enable (and: pip install playwright && playwright install chromium)

Output:
  • a clean table each window, and
  • appended rows in data/polymarket_probe_log.csv  (open in Excel later)

Usage:
       python research/tools/polymarket_price_probe.py            # light: gamma + pyth + binance
       python research/tools/polymarket_price_probe.py --scrape   # also run the Playwright scrape
       python research/tools/polymarket_price_probe.py --once     # one window then exit

Requires: requests   (playwright optional)
"""
import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

# Force UTF-8 stdout so box/marker chars don't crash the Windows cp1252 console.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import requests
except ImportError:
    raise SystemExit("pip install requests")

ET = timezone(timedelta(hours=-4))  # display only; ET is UTC-4/-5, exact offset not critical here
GAMMA = "https://gamma-api.polymarket.com"
PYTH_BTC_ID = "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43"  # BTC/USD
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.path.join(REPO_ROOT, "data")
LOG_CSV = os.path.join(DATA_DIR, "polymarket_probe_log.csv")
GAMMA_SAMPLE = os.path.join(DATA_DIR, "gamma_event_sample.json")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")


def _now_window_start() -> int:
    return int(time.time()) // 300 * 300


def _sleep_to_next_boundary() -> int:
    return 300 - (int(time.time()) % 300) + 3  # +3s so the window/market exists


# ── Method 1: Gamma API ──────────────────────────────────────────────────────
def _find_price_candidates(obj, path=""):
    """Recursively pull numbers/strings that look like a BTC price-to-beat."""
    out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower()
            if isinstance(v, (int, float)) and 1000 < float(v) < 10_000_000:
                if any(t in kl for t in ("strike", "beat", "ref", "price", "line", "target")):
                    out.append((f"{path}.{k}", float(v)))
            elif isinstance(v, str):
                m = re.search(r"\$?\s*([0-9]{2,3}(?:,[0-9]{3})+(?:\.[0-9]+)?)", v)
                if m and any(t in kl for t in ("strike", "beat", "ref", "price", "desc",
                                               "question", "title", "rules")):
                    try:
                        out.append((f"{path}.{k}", float(m.group(1).replace(",", ""))))
                    except ValueError:
                        pass
            out += _find_price_candidates(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj[:20]):
            out += _find_price_candidates(v, f"{path}[{i}]")
    return out


def try_gamma(window_start: int) -> dict:
    slug = f"btc-updown-5m-{window_start}"
    r = {"status": "?", "slug": slug, "price": None, "candidates": []}
    try:
        t = time.time()
        resp = requests.get(f"{GAMMA}/events", params={"slug": slug},
                            headers={"User-Agent": UA}, timeout=15)
        r["ms"] = int((time.time() - t) * 1000)
        if resp.status_code != 200:
            r["status"] = f"HTTP {resp.status_code}"
            return r
        events = resp.json()
        if not events:
            r["status"] = "no-event-yet"
            return r
        ev = events[0]
        if not os.path.exists(GAMMA_SAMPLE):
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(GAMMA_SAMPLE, "w", encoding="utf-8") as f:
                json.dump(ev, f, indent=2)
            print(f"   [gamma] full event JSON saved -> {GAMMA_SAMPLE} (inspect for the price field)")
        cands = _find_price_candidates(ev)
        r["candidates"] = cands
        r["price"] = cands[0][1] if cands else None
        r["status"] = "OK" if cands else "OK-no-price-field"
    except requests.exceptions.RequestException as e:
        r["status"] = f"BLOCKED/ERR: {str(e)[:50]}"
    except Exception as e:
        r["status"] = f"ERR: {str(e)[:50]}"
    return r


# ── Method 2: Pyth oracle ────────────────────────────────────────────────────
def try_pyth() -> dict:
    r = {"status": "?", "price": None}
    try:
        t = time.time()
        resp = requests.get("https://hermes.pyth.network/v2/updates/price/latest",
                            params={"ids[]": PYTH_BTC_ID}, timeout=12)
        r["ms"] = int((time.time() - t) * 1000)
        if resp.status_code != 200:
            r["status"] = f"HTTP {resp.status_code}"
            return r
        d = resp.json()
        p = d["parsed"][0]["price"]
        r["price"] = float(p["price"]) * (10 ** int(p["expo"]))
        r["status"] = "OK"
    except Exception as e:
        r["status"] = f"BLOCKED/ERR: {str(e)[:50]}"
    return r


# ── Method 2b: Chainlink BTC/USD on-chain (the ACTUAL oracle family Polymarket uses) ──
# Polymarket settles on the Chainlink BTC/USD *data stream*. The sub-second stream itself
# needs credentials, but the on-chain Chainlink BTC/USD Data Feed on Polygon is publicly
# readable and tracks the same oracle (heartbeat/deviation updates). This is the closest
# FREE, no-VPN, no-scrape reference to the real price-to-beat. Raw JSON-RPC eth_call —
# no web3 dependency.
POLYGON_RPCS = ["https://polygon-rpc.com", "https://rpc.ankr.com/polygon",
                "https://polygon.llamarpc.com"]
CHAINLINK_BTCUSD_POLYGON = "0xc907E116054Ad103354f2D350FD2514433D57F6f"


def try_chainlink() -> dict:
    r = {"status": "?", "price": None}
    payload = {"jsonrpc": "2.0", "id": 1, "method": "eth_call",
               "params": [{"to": CHAINLINK_BTCUSD_POLYGON, "data": "0x50d25bcd"}, "latest"]}  # latestAnswer()
    for rpc in POLYGON_RPCS:
        try:
            t = time.time()
            resp = requests.post(rpc, json=payload, timeout=12)
            r["ms"] = int((time.time() - t) * 1000)
            res = resp.json().get("result")
            if res and res != "0x":
                r["price"] = int(res, 16) / 1e8  # BTC/USD feed = 8 decimals
                r["status"] = "OK"
                r["rpc"] = rpc.split("//")[1]
                return r
        except Exception as e:
            r["status"] = f"BLOCKED/ERR: {str(e)[:40]}"
    return r


# ── Method 3: Binance ────────────────────────────────────────────────────────
def try_binance(window_start: int) -> dict:
    r = {"status": "?", "live": None, "boundary_open": None}
    try:
        live = requests.get("https://api.binance.com/api/v3/ticker/price",
                            params={"symbol": "BTCUSDT"}, timeout=10).json()
        r["live"] = float(live["price"])
        k = requests.get("https://api.binance.com/api/v3/klines",
                         params={"symbol": "BTCUSDT", "interval": "1m",
                                 "startTime": window_start * 1000, "limit": 1}, timeout=10).json()
        if k:
            r["boundary_open"] = float(k[0][1])
        r["status"] = "OK"
    except Exception as e:
        r["status"] = f"ERR: {str(e)[:50]}"
    return r


# ── Method 4: Playwright scrape (optional) ───────────────────────────────────
def try_scrape(window_start: int) -> dict:
    r = {"status": "?", "price": None}
    slug = f"btc-updown-5m-{window_start}"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        r["status"] = "playwright-not-installed"
        return r
    try:
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True,
                                  args=["--disable-blink-features=AutomationControlled"])
            pg = b.new_page(user_agent=UA)
            pg.goto(f"https://polymarket.com/event/{slug}",
                    wait_until="domcontentloaded", timeout=45000)
            pg.wait_for_timeout(5000)
            text = pg.inner_text("body")
            b.close()
        m = re.search(r"Price\s*to\s*Beat\s*\$?\s*([0-9,]+(?:\.[0-9]+)?)", text, re.IGNORECASE)
        r["price"] = float(m.group(1).replace(",", "")) if m else None
        r["status"] = "OK" if r["price"] else "loaded-no-price"
    except Exception as e:
        r["status"] = f"BLOCKED/ERR: {str(e)[:60]}"
    return r


def reachability_check():
    print("=" * 70)
    print("REACHABILITY (does your network reach these WITHOUT a VPN?)")
    for name, url in [("gamma-api", f"{GAMMA}/events?slug=test"),
                      ("pyth", "https://hermes.pyth.network/v2/updates/price/latest?ids[]=" + PYTH_BTC_ID),
                      ("binance", "https://api.binance.com/api/v3/ping"),
                      ("polymarket.com", "https://polymarket.com")]:
        try:
            t = time.time()
            sc = requests.get(url, headers={"User-Agent": UA}, timeout=12).status_code
            print(f"  {name:<16} reachable  (HTTP {sc}, {int((time.time()-t)*1000)}ms)")
        except Exception as e:
            print(f"  {name:<16} BLOCKED    ({str(e)[:55]})")
    print("=" * 70)


def run_window(window_start: int, do_scrape: bool):
    et = datetime.fromtimestamp(window_start, ET)
    print(f"\n== 5m window {et:%H:%M} ET  (slug btc-updown-5m-{window_start}) ==")
    g = try_gamma(window_start)
    py = try_pyth()
    bi = try_binance(window_start)
    sc = try_scrape(window_start) if do_scrape else {"status": "skipped", "price": None}

    print(f"  GAMMA   : {g['status']:<22} price={g['price']}  cands={g['candidates'][:3]}")
    print(f"  PYTH    : {py['status']:<22} price={py['price']}")
    print(f"  BINANCE : {bi['status']:<22} live={bi['live']} boundary_open={bi['boundary_open']}")
    print(f"  SCRAPE  : {sc['status']:<22} price={sc['price']}")

    # the "truth" = scrape if we got it, else gamma
    truth = sc.get("price") or g.get("price")
    if truth and bi.get("boundary_open"):
        print(f"  >> price-to-beat={truth}  vs  Binance boundary={bi['boundary_open']}  "
              f"GAP={truth - bi['boundary_open']:+.2f}")
    if truth and py.get("price"):
        print(f"  >> price-to-beat={truth}  vs  Pyth={py['price']:.2f}  "
              f"GAP={truth - py['price']:+.2f}")

    os.makedirs(DATA_DIR, exist_ok=True)
    new = not os.path.exists(LOG_CSV)
    with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["utc", "window_start", "et", "gamma_status", "gamma_price",
                        "pyth_status", "pyth_price", "binance_live", "binance_boundary",
                        "scrape_status", "scrape_price", "truth", "gap_vs_binance", "gap_vs_pyth"])
        w.writerow([datetime.now(timezone.utc).isoformat(), window_start, f"{et:%H:%M}",
                    g["status"], g["price"], py["status"], py.get("price"),
                    bi.get("live"), bi.get("boundary_open"), sc["status"], sc.get("price"),
                    truth,
                    (truth - bi["boundary_open"]) if (truth and bi.get("boundary_open")) else "",
                    (truth - py["price"]) if (truth and py.get("price")) else ""])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scrape", action="store_true", help="also run Playwright page scrape")
    ap.add_argument("--once", action="store_true", help="run one window then exit")
    args = ap.parse_args()

    print("Polymarket price-to-beat PROBE (standalone — does NOT touch the app)")
    print(f"Logging to: {LOG_CSV}")
    reachability_check()

    if args.once:
        run_window(_now_window_start(), args.scrape)
        return
    print("\nRunning each 5m window. Ctrl+C to stop. Review the CSV anytime.\n")
    while True:
        try:
            run_window(_now_window_start(), args.scrape)
        except Exception as e:
            print("window error:", e)
        s = _sleep_to_next_boundary()
        print(f"  ...sleeping {s}s to next boundary")
        time.sleep(s)


if __name__ == "__main__":
    main()
