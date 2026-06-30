"""
microstructure_recorder.py — RECORD-FORWARD L2 + cross-exchange clock (the ceiling-break data).
================================================================================================
The candle/OHLCV model is at its information ceiling (direction is a coin-flip every way we've sliced
it). The only lever that can move that ceiling is DATA the model can't currently see — and the two
highest-value sources, L2 order book and cross-exchange lead-lag, have NO reliable free history, so they
must be recorded FORWARD. This script does exactly that, into its OWN DuckDB (`data/microstructure.duckdb`)
so it never contends with the app's `analytics.duckdb` write lock.

What it captures every --interval seconds (default 1.0):
  • l2_snapshots — from Binance depth(20): mid, microprice, spread_bps, book imbalance (top-20 & near-mid),
    bid/ask depth (USD), depth slope, and **OFI** (Cont order-flow imbalance vs the previous snapshot).
  • crossvenue_snapshots — mid from Binance / Coinbase / Bybit / OKX, max cross-venue spread (bps),
    per-venue 1s returns, and which venue LED (largest move) — the raw material for a lead-lag feature.

It does NOT model anything yet — it accrues the data so that, in 2–4 weeks, a retrain (or a probe) can
test whether L2/cross-venue beats candle-only on the TOP buckets (per the master strategy, Exp 2 proper).

Crash-safe: one venue failing never stops the others or the loop. Runs until Ctrl+C.

Usage:
  python backend/microstructure_recorder.py            # record continuously
  python backend/microstructure_recorder.py --once     # one snapshot (smoke test) + exit
  python backend/microstructure_recorder.py --report    # row counts + recent coverage, then exit
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import duckdb

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
DB = os.path.join(DATA, "microstructure.duckdb")
_UA = {"User-Agent": "btc-microstructure-recorder/1.0"}

# venue ticker endpoints (public, no key). Each returns a (bid, ask) mid via _parse.
VENUES = {
    "binance":  "https://api.binance.com/api/v3/ticker/bookTicker?symbol=BTCUSDT",
    "coinbase": "https://api.exchange.coinbase.com/products/BTC-USD/ticker",
    "bybit":    "https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT",
    "okx":      "https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT",
}
DEPTH_URL = "https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=20"


def _get_json(url, timeout=3.0):
    try:
        import requests
        return requests.get(url, headers=_UA, timeout=timeout).json()
    except ImportError:
        import urllib.request
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())


def _venue_mid(name, js):
    """Return mid price from each venue's ticker payload, or None."""
    try:
        if name == "binance":
            return (float(js["bidPrice"]) + float(js["askPrice"])) / 2
        if name == "coinbase":
            return (float(js["bid"]) + float(js["ask"])) / 2
        if name == "bybit":
            d = js["result"]["list"][0]
            return (float(d["bid1Price"]) + float(d["ask1Price"])) / 2
        if name == "okx":
            d = js["data"][0]
            return (float(d["bidPx"]) + float(d["askPx"])) / 2
    except Exception:
        return None
    return None


def _ofi(bp, bs, ap, az, p):
    """Cont et al. order-flow imbalance vs previous best (bid price/size, ask price/size)."""
    if not p:
        return 0.0
    pbp, pbs, pap, paz = p
    e = 0.0
    e += bs if bp > pbp else (bs - pbs if bp == pbp else -pbs)         # bid side
    e -= az if ap < pap else (az - paz if ap == pap else -paz)         # ask side
    return e


def _l2_features(depth, prev_best):
    bids = [(float(p), float(s)) for p, s in depth.get("bids", [])]
    asks = [(float(p), float(s)) for p, s in depth.get("asks", [])]
    if not bids or not asks:
        return None, prev_best
    bp, bs = bids[0]; ap, az = asks[0]
    mid = (bp + ap) / 2
    sum_bs = sum(s for _, s in bids); sum_as = sum(s for _, s in asks)
    band = mid * 5 / 1e4  # within 5 bps of mid
    near_b = sum(s for p, s in bids if p >= mid - band)
    near_a = sum(s for p, s in asks if p <= mid + band)
    f = {
        "mid": mid,
        "microprice": (bp * az + ap * bs) / (bs + az) if (bs + az) else mid,
        "spread_bps": (ap - bp) / mid * 1e4,
        "obi_20": (sum_bs - sum_as) / (sum_bs + sum_as) if (sum_bs + sum_as) else 0.0,
        "obi_near": (near_b - near_a) / (near_b + near_a) if (near_b + near_a) else 0.0,
        "bid_usd": sum(p * s for p, s in bids),
        "ask_usd": sum(p * s for p, s in asks),
        "depth_slope": (sum_bs + sum_as) / max(1e-9, (asks[-1][0] - bids[-1][0]) / mid * 1e4),  # size per bps span
        "ofi": _ofi(bp, bs, ap, az, prev_best),
    }
    return f, (bp, bs, ap, az)


def _init(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS l2_snapshots (
        ts_ms BIGINT, mid DOUBLE, microprice DOUBLE, spread_bps DOUBLE,
        obi_20 DOUBLE, obi_near DOUBLE, bid_usd DOUBLE, ask_usd DOUBLE,
        depth_slope DOUBLE, ofi DOUBLE)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS crossvenue_snapshots (
        ts_ms BIGINT, binance DOUBLE, coinbase DOUBLE, bybit DOUBLE, okx DOUBLE,
        max_spread_bps DOUBLE, lead_venue VARCHAR,
        ret_binance DOUBLE, ret_coinbase DOUBLE, ret_bybit DOUBLE, ret_okx DOUBLE)""")


def _snapshot(conn, ex, state):
    now = int(time.time() * 1000)
    # cross-venue tickers, fetched concurrently
    def fetch(name):
        return name, _venue_mid(name, _get_json(VENUES[name]))
    mids = {}
    try:
        for name, mid in ex.map(lambda n: fetch(n), list(VENUES)):
            if mid:
                mids[name] = mid
    except Exception:
        pass
    if mids:
        prev = state.get("mids", {})
        rets = {v: ((mids[v] - prev[v]) / prev[v] if v in prev and prev[v] else 0.0) for v in mids}
        lead = max(rets, key=lambda v: abs(rets[v])) if rets else None
        vals = list(mids.values())
        max_spread = (max(vals) - min(vals)) / (sum(vals) / len(vals)) * 1e4 if len(vals) > 1 else 0.0
        conn.execute("INSERT INTO crossvenue_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
            now, mids.get("binance"), mids.get("coinbase"), mids.get("bybit"), mids.get("okx"),
            round(max_spread, 3), lead,
            rets.get("binance"), rets.get("coinbase"), rets.get("bybit"), rets.get("okx")))
        state["mids"] = mids
    # L2 from Binance depth
    try:
        f, state["prev_best"] = _l2_features(_get_json(DEPTH_URL), state.get("prev_best"))
        if f:
            conn.execute("INSERT INTO l2_snapshots VALUES (?,?,?,?,?,?,?,?,?,?)", (
                now, f["mid"], f["microprice"], f["spread_bps"], f["obi_20"], f["obi_near"],
                f["bid_usd"], f["ask_usd"], f["depth_slope"], f["ofi"]))
            return f
    except Exception as e:
        print(f"  l2 fetch failed: {str(e)[:60]}")
    return None


def report(conn):
    for t in ("l2_snapshots", "crossvenue_snapshots"):
        try:
            n = conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            rng = conn.execute(f"SELECT min(ts_ms), max(ts_ms) FROM {t}").fetchone()
            span = ""
            if rng[0]:
                span = (f"{datetime.fromtimestamp(rng[0]/1000, timezone.utc):%Y-%m-%d %H:%M} → "
                        f"{datetime.fromtimestamp(rng[1]/1000, timezone.utc):%H:%M} UTC "
                        f"(~{(rng[1]-rng[0])/3.6e6:.1f}h)")
            print(f"  {t:24s} rows={n:>8,}  {span}")
        except Exception as e:
            print(f"  {t}: {str(e)[:60]}")
    try:
        row = conn.execute("SELECT * FROM l2_snapshots ORDER BY ts_ms DESC LIMIT 1").fetchone()
        cols = [c[1] for c in conn.execute("PRAGMA table_info('l2_snapshots')").fetchall()]
        if row:
            print("  latest L2:", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in zip(cols, row)})
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    os.makedirs(DATA, exist_ok=True)
    conn = duckdb.connect(DB)
    _init(conn)
    if a.report:
        print(f"microstructure recorder DB: {DB}")
        report(conn); conn.close(); return

    state: dict = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        if a.once:
            f = _snapshot(conn, ex, state)
            print("one snapshot recorded." + (f"  L2 mid={f['mid']:.2f} spread={f['spread_bps']:.2f}bps "
                  f"obi20={f['obi_20']:+.3f} ofi={f['ofi']:+.3f}" if f else "  (L2 unavailable)"))
            report(conn); conn.close(); return
        print(f"Recording L2 + cross-venue every {a.interval}s → {DB}\n  Ctrl+C to stop. "
              "Runs alongside the app (separate DB, no lock conflict).")
        n = 0; t0 = time.time()
        try:
            while True:
                start = time.time()
                _snapshot(conn, ex, state)
                n += 1
                if n % 60 == 0:
                    print(f"  [{datetime.now():%H:%M:%S}] {n} snapshots ({n/((time.time()-t0)/60):.0f}/min)")
                time.sleep(max(0.0, a.interval - (time.time() - start)))
        except KeyboardInterrupt:
            print(f"\nStopped after {n} snapshots.")
            report(conn)
        finally:
            conn.close()


if __name__ == "__main__":
    main()
