"""Two-exchange, seven-pair, 180-day frozen dataset: Bybit 15m + real OI + funding, Binance 15m.

WHY TWO EXCHANGES
    Bybit supplies the perpetual state that matters - open interest and funding - which the
    local archive lacks entirely. Binance supplies an independent price for the same instrument,
    which makes cross-exchange basis and lead/lag computable. Neither alone gives both.

WHY 1h OPEN INTEREST
    Bybit retains 15-minute OI for about 30 days and 1-hour OI for over 250. At a 180-day window
    the hourly series is the only real one; it is joined as-of, never interpolated. A coarser
    real series beats a finer invented one.

    python -m research.multihorizon.fetch --fetch
    python -m research.multihorizon.fetch --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "multihorizon"
BYBIT = "https://api.bybit.com"
BINANCE = "https://fapi.binance.com"

SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT", "LINKUSDT")
BAR_MIN = 15
BAR_MS = BAR_MIN * 60_000
DAYS = 180
MAX_FUNDING_AGE_MS = 9 * 3600_000
MAX_OI_AGE_MS = 2 * 3600_000          # 1h series; older than two hours is stale


def _get(url: str, tries: int = 4) -> dict:
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=40) as response:
                return json.load(response)
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def bybit_klines(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    rows, cursor = [], end_ms
    while cursor > start_ms:
        payload = _get(f"{BYBIT}/v5/market/kline?category=linear&symbol={symbol}"
                       f"&interval={BAR_MIN}&end={cursor}&limit=1000")
        batch = payload.get("result", {}).get("list") or []
        if not batch:
            break
        rows.extend(batch)
        oldest = min(int(r[0]) for r in batch)
        if oldest >= cursor:
            break
        cursor = oldest - 1
        time.sleep(0.08)
    frame = pd.DataFrame(rows, columns=["ts_ms", "open", "high", "low", "close",
                                        "volume", "turnover"]).astype(float)
    frame["ts_ms"] = frame["ts_ms"].astype("int64")
    return frame.drop_duplicates("ts_ms").sort_values("ts_ms").reset_index(drop=True)


def bybit_oi(symbol: str, start_ms: int) -> pd.DataFrame:
    rows, cursor = [], ""
    for _ in range(60):
        url = (f"{BYBIT}/v5/market/open-interest?category=linear&symbol={symbol}"
               f"&intervalTime=1h&limit=200") + (f"&cursor={cursor}" if cursor else "")
        result = _get(url).get("result", {})
        batch = result.get("list") or []
        if not batch:
            break
        rows.extend(batch)
        cursor = result.get("nextPageCursor") or ""
        if min(int(r["timestamp"]) for r in batch) <= start_ms or not cursor:
            break
        time.sleep(0.08)
    if not rows:
        return pd.DataFrame(columns=["ts_ms", "open_interest"])
    frame = pd.DataFrame(rows)
    frame["ts_ms"] = frame["timestamp"].astype("int64")
    frame["open_interest"] = frame["openInterest"].astype(float)
    return (frame[["ts_ms", "open_interest"]].drop_duplicates("ts_ms")
            .sort_values("ts_ms").reset_index(drop=True))


def bybit_funding(symbol: str, start_ms: int) -> pd.DataFrame:
    rows, end = [], None
    for _ in range(20):
        url = f"{BYBIT}/v5/market/funding/history?category=linear&symbol={symbol}&limit=200"
        if end:
            url += f"&endTime={end}"
        batch = _get(url).get("result", {}).get("list") or []
        if not batch:
            break
        rows.extend(batch)
        oldest = min(int(r["fundingRateTimestamp"]) for r in batch)
        if oldest <= start_ms:
            break
        end = oldest - 1
        time.sleep(0.08)
    if not rows:
        return pd.DataFrame(columns=["ts_ms", "funding_rate"])
    frame = pd.DataFrame(rows)
    frame["ts_ms"] = frame["fundingRateTimestamp"].astype("int64")
    frame["funding_rate"] = frame["fundingRate"].astype(float)
    return (frame[["ts_ms", "funding_rate"]].drop_duplicates("ts_ms")
            .sort_values("ts_ms").reset_index(drop=True))


def binance_klines(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Independent price for the SAME instrument, for cross-exchange basis and lead/lag."""
    rows, cursor = [], start_ms
    while cursor < end_ms:
        payload = _get(f"{BINANCE}/fapi/v1/klines?symbol={symbol}&interval={BAR_MIN}m"
                       f"&startTime={cursor}&limit=1500")
        if not isinstance(payload, list) or not payload:
            break
        rows.extend(payload)
        newest = max(int(r[0]) for r in payload)
        if newest <= cursor:
            break
        cursor = newest + 1
        time.sleep(0.08)
    if not rows:
        return pd.DataFrame(columns=["ts_ms", "bin_close", "bin_volume"])
    frame = pd.DataFrame(rows).iloc[:, :6]
    frame.columns = ["ts_ms", "o", "h", "l", "bin_close", "bin_volume"]
    frame = frame.astype(float)
    frame["ts_ms"] = frame["ts_ms"].astype("int64")
    return (frame[["ts_ms", "bin_close", "bin_volume"]].drop_duplicates("ts_ms")
            .sort_values("ts_ms").reset_index(drop=True))


def merge_symbol(klines, oi, funding, binance) -> pd.DataFrame:
    frame = klines.copy()
    for other, column, max_age in ((oi, "open_interest", MAX_OI_AGE_MS),
                                   (funding, "funding_rate", MAX_FUNDING_AGE_MS)):
        if other.empty:
            frame[column] = np.nan
            continue
        stamped = other.rename(columns={"ts_ms": f"{column}_ts"})
        frame = pd.merge_asof(frame, stamped, left_on="ts_ms", right_on=f"{column}_ts",
                              direction="backward")
        # Stale is BLANK, never carried. A stale funding rate or OI level is exactly the input
        # that makes a perpetual-state feature confidently wrong.
        age = frame["ts_ms"] - frame[f"{column}_ts"]
        frame.loc[age > max_age, column] = np.nan
    if binance.empty:
        frame["bin_close"] = np.nan
        frame["bin_volume"] = np.nan
    else:
        frame = pd.merge_asof(frame, binance, on="ts_ms", direction="backward")
    return frame


def build(days: int = DAYS) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    start_ms = end_ms - days * 86_400_000
    frames = []
    for symbol in SYMBOLS:
        print(f"  {symbol:<10}", end="", flush=True)
        k = bybit_klines(symbol, start_ms, end_ms)
        o = bybit_oi(symbol, start_ms)
        f = bybit_funding(symbol, start_ms)
        try:
            b = binance_klines(symbol, start_ms, end_ms)
        except Exception as exc:
            print(f"binance failed ({str(exc)[:30]}) ", end="")
            b = pd.DataFrame(columns=["ts_ms", "bin_close", "bin_volume"])
        merged = merge_symbol(k, o, f, b)
        merged = merged[merged.ts_ms >= start_ms].copy()
        merged["symbol"] = symbol
        frames.append(merged)
        print(f"bars {len(merged):>6,}  OI {merged.open_interest.notna().mean():5.1%}  "
              f"fr {merged.funding_rate.notna().mean():5.1%}  "
              f"binance {merged.bin_close.notna().mean():5.1%}")
    combined = pd.concat(frames, ignore_index=True).sort_values(["symbol", "ts_ms"])
    path = CACHE / f"multi_{BAR_MIN}m_{days}d.parquet"
    combined.to_parquet(path, index=False)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    (CACHE / "manifest.json").write_text(json.dumps({
        "path": str(path), "sha256": digest, "rows": int(len(combined)),
        "symbols": list(SYMBOLS), "bar_minutes": BAR_MIN, "days": days,
        "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "span_ms": [int(combined.ts_ms.min()), int(combined.ts_ms.max())],
        "oi_coverage": float(combined.open_interest.notna().mean()),
        "funding_coverage": float(combined.funding_rate.notna().mean()),
        "binance_coverage": float(combined.bin_close.notna().mean()),
        "sources": ["bybit v5 linear kline/open-interest/funding", "binance fapi klines"],
    }, indent=2), encoding="utf-8")
    print(f"\n  wrote {path}  sha256 {digest[:16]}...  {len(combined):,} rows")
    return path


def load() -> pd.DataFrame:
    path = CACHE / f"multi_{BAR_MIN}m_{DAYS}d.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"{path} - run: python -m research.multihorizon.fetch --fetch")
    return pd.read_parquet(path)


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    check(len(SYMBOLS) == 7, "seven pairs are declared")
    now = 1_785_000_000_000 // BAR_MS * BAR_MS
    ts = np.arange(400, dtype="int64") * BAR_MS + now
    k = pd.DataFrame({"ts_ms": ts, "open": 100.0, "high": 101.0, "low": 99.0,
                      "close": 100.0, "volume": 1.0, "turnover": 100.0})
    oi = pd.DataFrame({"ts_ms": ts[::4], "open_interest": np.arange(100, dtype=float)})
    fr = pd.DataFrame({"ts_ms": [ts[0]], "funding_rate": [0.0001]})
    bn = pd.DataFrame({"ts_ms": ts[::2], "bin_close": 100.5, "bin_volume": 2.0})
    merged = merge_symbol(k, oi, fr, bn)
    check(len(merged) == len(k), "the merge never adds or drops bars")
    check(merged.open_interest.notna().any(), "open interest joins")
    check(merged.bin_close.notna().any(), "the second exchange joins")

    # A bar far past the END of both series: each must go blank on its own age limit. The
    # series must STOP early, or there is a fresh observation nearby and nothing is stale.
    stale_bar = pd.DataFrame({"ts_ms": [ts[0] + 30 * 3600_000], "open": 1.0, "high": 1.0,
                              "low": 1.0, "close": 1.0, "volume": 1.0, "turnover": 1.0})
    far = merge_symbol(stale_bar, oi.iloc[:2], fr, bn)
    check(bool(np.isnan(far.funding_rate.iloc[0])),
          "funding older than its age limit is BLANKED, never carried")
    check(bool(np.isnan(far.open_interest.iloc[0])),
          "open interest older than its age limit is BLANKED too")
    fresh = merge_symbol(
        pd.DataFrame({"ts_ms": [ts[8]], "open": 1.0, "high": 1.0, "low": 1.0,
                      "close": 1.0, "volume": 1.0, "turnover": 1.0}), oi, fr, bn)
    check(np.isfinite(fresh.open_interest.iloc[0]),
          "...but a RECENT open interest observation is used - the limit is not blanket refusal")
    late = merge_symbol(k, pd.DataFrame({"ts_ms": [ts[-1]], "open_interest": [999.0]}), fr, bn)
    check(not (late.open_interest.iloc[0] == 999.0),
          "a LATER observation never back-fills an earlier bar")
    print(f"\nMULTIHORIZON FETCH SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--days", type=int, default=DAYS)
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.fetch:
        print(f"fetching {len(SYMBOLS)} pairs x {args.days}d of {BAR_MIN}m bars "
              f"from Bybit (klines, 1h OI, funding) and Binance (klines)")
        build(args.days)
        return 0
    print(load().shape)
    return 0


if __name__ == "__main__":
    sys.exit(main())
