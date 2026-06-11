#!/usr/bin/env python3

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


# ============================================================
# CONFIG
# ============================================================

ET = ZoneInfo("America/New_York")

GAMMA = "https://gamma-api.polymarket.com"

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
LOG_CSV = os.path.join(DATA_DIR, "polymarket_price_to_beat_log.csv")
GAMMA_SAMPLE = os.path.join(DATA_DIR, "gamma_event_sample.json")

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Pyth BTC/USD price feed ID
PYTH_BTC_ID = "e62df6c8b4a85fe1a67db44dc12de5db330f7ac66b72dc658afedf0f4a415b43"

# Public Polygon RPCs
POLYGON_RPCS = [
    "https://polygon-bor-rpc.publicnode.com",
    "https://polygon.drpc.org",
    "https://polygon-rpc.com",
    "https://1rpc.io/matic",
    "https://rpc.ankr.com/polygon",
    "https://polygon.llamarpc.com",
]

# Chainlink BTC/USD feed on Polygon
CHAINLINK_BTCUSD_POLYGON = "0xc907E116054Ad103354f2D350FD2514433D57F6f"


# ============================================================
# TIME HELPERS
# ============================================================


def current_5m_window_start_unix() -> int:
    return int(time.time()) // 300 * 300


def seconds_until_next_5m_boundary() -> int:
    now = int(time.time())
    return 300 - (now % 300) + 3


def get_et_window_label(window_start_unix: int):
    start_et = datetime.fromtimestamp(window_start_unix, ET)
    end_et = start_et + timedelta(minutes=5)
    return start_et, end_et


# ============================================================
# GAMMA API
# ============================================================


def find_price_candidates(obj, path=""):
    out = []

    if isinstance(obj, dict):
        for k, v in obj.items():
            key_lower = str(k).lower()

            if isinstance(v, (int, float)) and 1000 < float(v) < 10_000_000:
                if any(
                    x in key_lower
                    for x in ("strike", "beat", "ref", "price", "line", "target")
                ):
                    out.append((f"{path}.{k}", float(v)))

            elif isinstance(v, str):
                match = re.search(
                    r"\$?\s*([0-9]{2,3}(?:,[0-9]{3})+(?:\.[0-9]+)?)",
                    v,
                )
                if match and any(
                    x in key_lower
                    for x in (
                        "strike",
                        "beat",
                        "ref",
                        "price",
                        "desc",
                        "question",
                        "title",
                        "rules",
                    )
                ):
                    try:
                        out.append(
                            (f"{path}.{k}", float(match.group(1).replace(",", "")))
                        )
                    except ValueError:
                        pass

            out += find_price_candidates(v, f"{path}.{k}")

    elif isinstance(obj, list):
        for i, v in enumerate(obj[:50]):
            out += find_price_candidates(v, f"{path}[{i}]")

    return out


def try_gamma(window_start: int) -> dict:
    slug = f"btc-updown-5m-{window_start}"

    result = {
        "status": "?",
        "slug": slug,
        "price": None,
        "candidates": [],
        "title": None,
        "ms": None,
    }

    try:
        start = time.time()

        response = requests.get(
            f"{GAMMA}/events",
            params={"slug": slug},
            headers={"User-Agent": UA},
            timeout=20,
        )

        result["ms"] = int((time.time() - start) * 1000)

        if response.status_code != 200:
            result["status"] = f"HTTP {response.status_code}"
            return result

        events = response.json()

        if not events:
            result["status"] = "no-event-yet"
            return result

        event = events[0]

        result["title"] = (
            event.get("title") or event.get("question") or event.get("slug") or "N/A"
        )

        if not os.path.exists(GAMMA_SAMPLE):
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(GAMMA_SAMPLE, "w", encoding="utf-8") as f:
                json.dump(event, f, indent=2)

        candidates = find_price_candidates(event)
        result["candidates"] = candidates
        result["price"] = candidates[0][1] if candidates else None
        result["status"] = "OK" if candidates else "OK-no-price-field"

    except Exception as e:
        result["status"] = f"ERR: {str(e)[:120]}"

    return result


# ============================================================
# CHAINLINK POLYGON ON-CHAIN FALLBACK
# ============================================================


def try_chainlink_polygon() -> dict:
    result = {
        "status": "?",
        "price": None,
        "rpc": None,
        "ms": None,
        "raw_answer": None,
    }

    # Chainlink AggregatorV3Interface latestRoundData()
    # selector: 0xfeaf968c
    # returns:
    # roundId, answer, startedAt, updatedAt, answeredInRound
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "eth_call",
        "params": [
            {
                "to": CHAINLINK_BTCUSD_POLYGON,
                "data": "0xfeaf968c",
            },
            "latest",
        ],
    }

    errors = []

    for rpc in POLYGON_RPCS:
        try:
            start = time.time()

            response = requests.post(
                rpc,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": UA,
                },
                timeout=20,
            )

            result["ms"] = int((time.time() - start) * 1000)

            if response.status_code != 200:
                errors.append(f"{rpc} HTTP {response.status_code}")
                continue

            data = response.json()

            if "error" in data:
                errors.append(f"{rpc} RPC error: {data['error']}")
                continue

            raw = data.get("result")

            if not raw or raw == "0x":
                errors.append(f"{rpc} empty result")
                continue

            hex_data = raw[2:]

            # latestRoundData returns 5 ABI-encoded slots, each 32 bytes.
            # answer is the second slot.
            if len(hex_data) < 64 * 2:
                errors.append(f"{rpc} result too short")
                continue

            answer_hex = hex_data[64:128]
            answer_int = int(answer_hex, 16)

            # signed int256 handling
            if answer_int >= 2**255:
                answer_int -= 2**256

            price = answer_int / 1e8

            if price <= 1000:
                errors.append(f"{rpc} invalid price {price}")
                continue

            result["price"] = price
            result["raw_answer"] = answer_int
            result["status"] = "OK"
            result["rpc"] = rpc
            return result

        except Exception as e:
            errors.append(f"{rpc}: {str(e)[:100]}")

    result["status"] = "ERR all RPCs: " + " | ".join(errors)[:300]
    return result


# ============================================================
# PYTH FALLBACK
# ============================================================


def try_pyth() -> dict:
    result = {
        "status": "?",
        "price": None,
        "ms": None,
    }

    try:
        start = time.time()

        response = requests.get(
            "https://hermes.pyth.network/v2/updates/price/latest",
            params={"ids[]": PYTH_BTC_ID},
            timeout=15,
        )

        result["ms"] = int((time.time() - start) * 1000)

        if response.status_code != 200:
            result["status"] = f"HTTP {response.status_code}"
            return result

        data = response.json()
        price_obj = data["parsed"][0]["price"]

        result["price"] = float(price_obj["price"]) * (10 ** int(price_obj["expo"]))
        result["status"] = "OK"

    except Exception as e:
        result["status"] = f"ERR: {str(e)[:120]}"

    return result


# ============================================================
# BINANCE COMPARISON ONLY
# ============================================================


def try_binance(window_start: int) -> dict:
    result = {
        "status": "?",
        "live": None,
        "boundary_open": None,
    }

    try:
        live_response = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": "BTCUSDT"},
            timeout=10,
        )
        live_response.raise_for_status()
        live_data = live_response.json()
        result["live"] = float(live_data["price"])

        kline_response = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={
                "symbol": "BTCUSDT",
                "interval": "1m",
                "startTime": window_start * 1000,
                "limit": 1,
            },
            timeout=10,
        )
        kline_response.raise_for_status()
        kline_data = kline_response.json()

        if kline_data:
            result["boundary_open"] = float(kline_data[0][1])

        result["status"] = "OK"

    except Exception as e:
        result["status"] = f"ERR: {str(e)[:120]}"

    return result


# ============================================================
# POLYMARKET PAGE SCRAPE
# ============================================================


def extract_price_to_beat_from_text(text: str):
    patterns = [
        r"Price\s*To\s*Beat\s*\$?\s*([0-9,]+(?:\.[0-9]+)?)",
        r"Price\s*to\s*Beat\s*\$?\s*([0-9,]+(?:\.[0-9]+)?)",
        r"Price\s*To\s*Beat\s*\n\s*\$?\s*([0-9,]+(?:\.[0-9]+)?)",
        r"Price\s*to\s*Beat\s*\n\s*\$?\s*([0-9,]+(?:\.[0-9]+)?)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return float(match.group(1).replace(",", ""))

    return None


def try_scrape(window_start: int, retries: int = 3) -> dict:
    result = {
        "status": "?",
        "price": None,
        "url": None,
    }

    slug = f"btc-updown-5m-{window_start}"
    url = f"https://polymarket.com/event/{slug}"
    result["url"] = url

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        result["status"] = "playwright-not-installed"
        return result

    last_error = None

    for attempt in range(1, retries + 1):
        browser = None

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-dev-shm-usage",
                        "--no-sandbox",
                    ],
                )

                page = browser.new_page(
                    user_agent=UA,
                    viewport={"width": 1365, "height": 900},
                )

                page.set_default_timeout(90000)
                page.set_default_navigation_timeout(90000)

                print(f"SCRAPE opening attempt {attempt}/{retries}: {url}")

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=90000)
                except Exception:
                    page.goto(url, wait_until="commit", timeout=90000)

                page.wait_for_timeout(7000)

                text = page.inner_text("body")
                price = extract_price_to_beat_from_text(text)

                if price is None:
                    os.makedirs(DATA_DIR, exist_ok=True)
                    debug_txt = os.path.join(DATA_DIR, f"polymarket_debug_{slug}.txt")
                    debug_png = os.path.join(DATA_DIR, f"polymarket_debug_{slug}.png")

                    with open(debug_txt, "w", encoding="utf-8") as f:
                        f.write(text)

                    try:
                        page.screenshot(path=debug_png, full_page=True)
                    except Exception:
                        pass

                    raise RuntimeError("loaded-no-price")

                result["price"] = price
                result["status"] = "OK"

                try:
                    browser.close()
                except Exception:
                    pass

                return result

        except Exception as e:
            last_error = e
            result["status"] = f"ERR attempt {attempt}: {str(e)[:120]}"
            time.sleep(5)

        finally:
            try:
                if browser:
                    browser.close()
            except Exception:
                pass

    result["status"] = f"FAILED: {str(last_error)[:120]}"
    return result


# ============================================================
# REACHABILITY CHECK
# ============================================================


def reachability_check():
    print("=" * 80)
    print("REACHABILITY CHECK")

    targets = [
        ("gamma-api", f"{GAMMA}/events?slug=test"),
        (
            "pyth",
            "https://hermes.pyth.network/v2/updates/price/latest?ids[]=" + PYTH_BTC_ID,
        ),
        ("binance", "https://api.binance.com/api/v3/ping"),
        ("polymarket.com", "https://polymarket.com"),
        ("polygon-rpc", "https://polygon-rpc.com"),
        ("polygon-publicnode", "https://polygon-bor-rpc.publicnode.com"),
    ]

    for name, url in targets:
        try:
            start = time.time()
            response = requests.get(url, headers={"User-Agent": UA}, timeout=12)
            ms = int((time.time() - start) * 1000)
            print(f"{name:<20} reachable HTTP {response.status_code} {ms}ms")
        except Exception as e:
            print(f"{name:<20} BLOCKED/ERR {str(e)[:100]}")

    print("=" * 80)


# ============================================================
# CSV LOGGING
# ============================================================


def append_log(row: dict):
    os.makedirs(DATA_DIR, exist_ok=True)

    new_file = not os.path.exists(LOG_CSV)

    columns = [
        "utc_now",
        "window_start_unix",
        "et_date",
        "et_start",
        "et_end",
        "slug",
        "event_title",
        "selected_source",
        "selected_price_to_beat",
        "scrape_status",
        "scrape_price",
        "gamma_status",
        "gamma_price",
        "chainlink_status",
        "chainlink_price",
        "chainlink_rpc",
        "pyth_status",
        "pyth_price",
        "binance_status",
        "binance_live",
        "binance_boundary_open",
        "gap_selected_vs_binance_boundary",
        "gap_selected_vs_chainlink",
        "gap_selected_vs_pyth",
        "polymarket_url",
    ]

    with open(LOG_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)

        if new_file:
            writer.writeheader()

        writer.writerow(row)


# ============================================================
# ONE WINDOW
# ============================================================


def run_window(window_start: int, do_scrape: bool):
    start_et, end_et = get_et_window_label(window_start)
    slug = f"btc-updown-5m-{window_start}"

    print()
    print("=" * 80)
    print(f"BTC 5m Polymarket Window: {start_et:%H:%M} to {end_et:%H:%M} ET")
    print(f"Slug: {slug}")
    print("=" * 80)

    gamma = try_gamma(window_start)
    chainlink = try_chainlink_polygon()
    pyth = try_pyth()
    binance = try_binance(window_start)

    if do_scrape:
        scrape = try_scrape(window_start)
    else:
        scrape = {
            "status": "skipped",
            "price": None,
            "url": f"https://polymarket.com/event/{slug}",
        }

    event_title = gamma.get("title") or "N/A"

    # Priority:
    # 1. Scrape = exact displayed Polymarket UI value
    # 2. Gamma = if Polymarket ever exposes price field
    # 3. Chainlink Polygon on-chain = best free non-scrape fallback
    # 4. Pyth = fallback
    # 5. Binance boundary = comparison/final fallback only
    selected_source = None
    selected_price = None

    if scrape.get("price") is not None:
        selected_source = "SCRAPE_POLYMARKET_UI"
        selected_price = scrape["price"]
    elif gamma.get("price") is not None:
        selected_source = "GAMMA"
        selected_price = gamma["price"]
    elif chainlink.get("price") is not None:
        selected_source = "CHAINLINK_POLYGON_ONCHAIN"
        selected_price = chainlink["price"]
    elif pyth.get("price") is not None:
        selected_source = "PYTH"
        selected_price = pyth["price"]
    elif binance.get("boundary_open") is not None:
        selected_source = "BINANCE_BOUNDARY_OPEN"
        selected_price = binance["boundary_open"]

    print(f"Event Title : {event_title}")
    print(f"ET Window   : {start_et:%Y-%m-%d %H:%M:%S %Z} to {end_et:%H:%M:%S %Z}")
    print(f"URL         : https://polymarket.com/event/{slug}")
    print("-" * 80)

    print(
        f"GAMMA       : {gamma['status']:<32} "
        f"price={gamma.get('price')} candidates={gamma.get('candidates')[:3]}"
    )
    print(f"SCRAPE      : {scrape['status']:<32} price={scrape.get('price')}")
    print(
        f"CHAINLINK   : {chainlink['status']:<32} "
        f"price={chainlink.get('price')} rpc={chainlink.get('rpc')}"
    )
    print(f"PYTH        : {pyth['status']:<32} price={pyth.get('price')}")
    print(
        f"BINANCE     : {binance['status']:<32} "
        f"live={binance.get('live')} boundary_open={binance.get('boundary_open')}"
    )
    print("-" * 80)

    if selected_price is not None:
        print(f"SELECTED Price To Beat : ${selected_price:,.2f}")
        print(f"SELECTED Source        : {selected_source}")
    else:
        print("SELECTED Price To Beat : None")
        print("SELECTED Source        : None")

    gap_vs_binance = ""
    gap_vs_chainlink = ""
    gap_vs_pyth = ""

    if selected_price is not None and binance.get("boundary_open") is not None:
        gap_vs_binance = selected_price - binance["boundary_open"]
        print(f"Gap vs Binance boundary: {gap_vs_binance:+.2f}")

    if selected_price is not None and chainlink.get("price") is not None:
        gap_vs_chainlink = selected_price - chainlink["price"]
        print(f"Gap vs Chainlink       : {gap_vs_chainlink:+.2f}")

    if selected_price is not None and pyth.get("price") is not None:
        gap_vs_pyth = selected_price - pyth["price"]
        print(f"Gap vs Pyth            : {gap_vs_pyth:+.2f}")

    print("=" * 80)

    append_log(
        {
            "utc_now": datetime.now(timezone.utc).isoformat(),
            "window_start_unix": window_start,
            "et_date": start_et.strftime("%Y-%m-%d"),
            "et_start": start_et.strftime("%H:%M:%S %Z"),
            "et_end": end_et.strftime("%H:%M:%S %Z"),
            "slug": slug,
            "event_title": event_title,
            "selected_source": selected_source,
            "selected_price_to_beat": selected_price,
            "scrape_status": scrape.get("status"),
            "scrape_price": scrape.get("price"),
            "gamma_status": gamma.get("status"),
            "gamma_price": gamma.get("price"),
            "chainlink_status": chainlink.get("status"),
            "chainlink_price": chainlink.get("price"),
            "chainlink_rpc": chainlink.get("rpc"),
            "pyth_status": pyth.get("status"),
            "pyth_price": pyth.get("price"),
            "binance_status": binance.get("status"),
            "binance_live": binance.get("live"),
            "binance_boundary_open": binance.get("boundary_open"),
            "gap_selected_vs_binance_boundary": gap_vs_binance,
            "gap_selected_vs_chainlink": gap_vs_chainlink,
            "gap_selected_vs_pyth": gap_vs_pyth,
            "polymarket_url": scrape.get("url"),
        }
    )


# ============================================================
# MAIN LOOP
# ============================================================


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scrape",
        action="store_true",
        help="Enable Playwright scrape of Polymarket page. Requires polymarket.com reachable.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run once and exit.",
    )
    parser.add_argument(
        "--no-reachability",
        action="store_true",
        help="Skip reachability check.",
    )

    args = parser.parse_args()

    print("Polymarket BTC 5m Price-To-Beat Tracker")
    print(f"Logging to: {LOG_CSV}")
    print(f"Scrape enabled: {args.scrape}")
    print()

    if not args.no_reachability:
        reachability_check()

    if args.once:
        run_window(current_5m_window_start_unix(), args.scrape)
        return

    print("Running every 5-minute ET window. Press Ctrl+C to stop.")
    print()

    while True:
        try:
            sleep_seconds = seconds_until_next_5m_boundary()
            print(f"Sleeping {sleep_seconds}s until next 5-minute boundary...")
            time.sleep(sleep_seconds)

            window_start = current_5m_window_start_unix()
            run_window(window_start, args.scrape)

        except KeyboardInterrupt:
            print("\nStopped by user.")
            break

        except Exception as e:
            print(f"Window error: {e}")
            print("Continuing to next 5-minute window...")


if __name__ == "__main__":
    main()
