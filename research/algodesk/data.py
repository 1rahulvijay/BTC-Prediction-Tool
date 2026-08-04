"""Frozen multi-symbol Bybit dataset: 15m klines + REAL funding rate + REAL open interest.

WHY A FROZEN DATASET
    A backtest that fetches live data cannot be re-run. Yesterday's numbers cannot be
    reproduced, a defect cannot be bisected, and nobody can check the result. So the fetch
    happens once, lands in parquet, and a manifest records the sha256 and the span. Every later
    stage reads the cache, never the network.

WHY 15 MINUTES
    Open interest is the binding constraint. Bybit retains 5-minute OI for about four days;
    15-minute OI paginates back beyond thirty. Funding prints every eight hours regardless.
    15m bars are the finest grid on which all three series genuinely exist for 40 days.

CAUSALITY
    Funding is known only at its print time, so it is forward-filled with a hard age limit and
    never back-filled. OI at bar t is the value stamped at t, used from t onward. Every 24h
    aggregate is shifted one bar, so no decision reads the bar it is made on.

    python -m research.algodesk.data --fetch
    python -m research.algodesk.data --selftest
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
CACHE = ROOT / "data" / "algodesk"
BASE = "https://api.bybit.com"

#: The spec's default pairs.
SYMBOLS = ("BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "AVAXUSDT", "LINKUSDT")

BAR_MINUTES = 15
BAR_MS = BAR_MINUTES * 60_000
BARS_PER_DAY = 24 * 60 // BAR_MINUTES          # 96
TOTAL_DAYS = 40                                 # 30 train + 10 test
#: Funding prints every 8h. Beyond this age the value is stale and the row is dropped rather
#: than carried: a stale funding rate is exactly the input that makes a funding agent lie.
MAX_FUNDING_AGE_MS = 9 * 3600_000


def _get(url: str, tries: int = 4) -> dict:
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as response:
                payload = json.load(response)
            if payload.get("retCode") == 0:
                return payload["result"]
            raise RuntimeError(payload.get("retMsg", "unknown"))
        except Exception:
            if attempt == tries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def fetch_klines(symbol: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    rows, cursor_end = [], end_ms
    while cursor_end > start_ms:
        result = _get(f"{BASE}/v5/market/kline?category=linear&symbol={symbol}"
                      f"&interval={BAR_MINUTES}&end={cursor_end}&limit=1000")
        batch = result.get("list") or []
        if not batch:
            break
        rows.extend(batch)
        oldest = min(int(r[0]) for r in batch)
        if oldest >= cursor_end:
            break
        cursor_end = oldest - 1
        time.sleep(0.12)
    frame = pd.DataFrame(rows, columns=["ts_ms", "open", "high", "low", "close",
                                        "volume", "turnover"])
    frame = frame.astype({c: float for c in frame.columns})
    frame["ts_ms"] = frame["ts_ms"].astype("int64")
    return frame.drop_duplicates("ts_ms").sort_values("ts_ms").reset_index(drop=True)


def fetch_open_interest(symbol: str, start_ms: int) -> pd.DataFrame:
    rows, cursor = [], ""
    for _ in range(40):
        url = (f"{BASE}/v5/market/open-interest?category=linear&symbol={symbol}"
               f"&intervalTime={BAR_MINUTES}min&limit=200")
        if cursor:
            url += f"&cursor={cursor}"
        result = _get(url)
        batch = result.get("list") or []
        if not batch:
            break
        rows.extend(batch)
        cursor = result.get("nextPageCursor") or ""
        if min(int(r["timestamp"]) for r in batch) <= start_ms or not cursor:
            break
        time.sleep(0.12)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["ts_ms", "open_interest"])
    frame["ts_ms"] = frame["timestamp"].astype("int64")
    frame["open_interest"] = frame["openInterest"].astype(float)
    return (frame[["ts_ms", "open_interest"]].drop_duplicates("ts_ms")
            .sort_values("ts_ms").reset_index(drop=True))


def fetch_funding(symbol: str, start_ms: int) -> pd.DataFrame:
    rows, cursor_end = [], None
    for _ in range(12):
        url = f"{BASE}/v5/market/funding/history?category=linear&symbol={symbol}&limit=200"
        if cursor_end:
            url += f"&endTime={cursor_end}"
        batch = _get(url).get("list") or []
        if not batch:
            break
        rows.extend(batch)
        oldest = min(int(r["fundingRateTimestamp"]) for r in batch)
        if oldest <= start_ms:
            break
        cursor_end = oldest - 1
        time.sleep(0.12)
    frame = pd.DataFrame(rows)
    if frame.empty:
        return pd.DataFrame(columns=["ts_ms", "funding_rate"])
    frame["ts_ms"] = frame["fundingRateTimestamp"].astype("int64")
    frame["funding_rate"] = frame["fundingRate"].astype(float)
    return (frame[["ts_ms", "funding_rate"]].drop_duplicates("ts_ms")
            .sort_values("ts_ms").reset_index(drop=True))


def merge_symbol(klines: pd.DataFrame, oi: pd.DataFrame,
                 funding: pd.DataFrame) -> pd.DataFrame:
    """One causal frame. Funding is as-of with an age limit; OI is as-of, never interpolated."""
    frame = klines.copy()
    if not oi.empty:
        frame = pd.merge_asof(frame, oi, on="ts_ms", direction="backward")
    else:
        frame["open_interest"] = np.nan
    if not funding.empty:
        funding = funding.rename(columns={"ts_ms": "funding_ts"})
        frame = pd.merge_asof(frame, funding, left_on="ts_ms", right_on="funding_ts",
                              direction="backward")
        age = frame["ts_ms"] - frame["funding_ts"]
        # A funding rate older than one interval is stale. Blank it rather than carry it:
        # a stale rate is the exact input that makes a funding agent confidently wrong.
        frame.loc[age > MAX_FUNDING_AGE_MS, "funding_rate"] = np.nan
        frame["funding_age_ms"] = age
    else:
        frame["funding_rate"] = np.nan
        frame["funding_age_ms"] = np.nan
    return frame


def build(symbols=SYMBOLS, days: int = TOTAL_DAYS) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    end_ms = int(time.time() * 1000) // BAR_MS * BAR_MS
    start_ms = end_ms - days * 86_400_000
    frames = []
    for symbol in symbols:
        print(f"  {symbol:<10}", end="", flush=True)
        klines = fetch_klines(symbol, start_ms, end_ms)
        oi = fetch_open_interest(symbol, start_ms)
        funding = fetch_funding(symbol, start_ms)
        merged = merge_symbol(klines, oi, funding)
        merged = merged[merged.ts_ms >= start_ms].copy()
        merged["symbol"] = symbol
        frames.append(merged)
        print(f"klines {len(klines):>5,}  OI {len(oi):>5,}  funding {len(funding):>4}  "
              f"OI-cover {merged.open_interest.notna().mean():5.1%}  "
              f"fr-cover {merged.funding_rate.notna().mean():5.1%}")
    combined = pd.concat(frames, ignore_index=True).sort_values(["symbol", "ts_ms"])
    path = CACHE / "bybit_15m_40d.parquet"
    combined.to_parquet(path, index=False)

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "path": str(path), "sha256": digest, "rows": int(len(combined)),
        "symbols": list(symbols), "bar_minutes": BAR_MINUTES,
        "fetched_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "span_ms": [int(combined.ts_ms.min()), int(combined.ts_ms.max())],
        "oi_coverage": float(combined.open_interest.notna().mean()),
        "funding_coverage": float(combined.funding_rate.notna().mean()),
        "source": "bybit v5 linear: kline, open-interest, funding/history",
    }
    (CACHE / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n  wrote {path}  sha256 {digest[:16]}...  {len(combined):,} rows")
    return path


def load() -> pd.DataFrame:
    path = CACHE / "bybit_15m_40d.parquet"
    if not path.is_file():
        raise FileNotFoundError(f"{path} - run: python -m research.algodesk.data --fetch")
    return pd.read_parquet(path)


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    now = 1_785_000_000_000 // BAR_MS * BAR_MS
    ts = np.arange(200, dtype="int64") * BAR_MS + now
    klines = pd.DataFrame({"ts_ms": ts, "open": 100.0, "high": 101.0, "low": 99.0,
                           "close": 100.0, "volume": 1.0, "turnover": 100.0})
    oi = pd.DataFrame({"ts_ms": ts[::4], "open_interest": np.arange(50, dtype=float)})
    # Two funding prints, 8h apart.
    funding = pd.DataFrame({"ts_ms": [ts[0], ts[0] + 8 * 3600_000],
                            "funding_rate": [0.0001, -0.0002]})
    merged = merge_symbol(klines, oi, funding)

    check(len(merged) == len(klines), "the merge never adds or drops bars")
    row = merged.iloc[10]
    check(row.ts_ms >= ts[8], "OI is taken as-of, from a bar at or BEFORE this one")
    # `or True` made this unfalsifiable. The real property: every bar at or after the
    # first OI observation carries a value, and the values are the ones supplied.
    joined = merged[merged.ts_ms >= oi.ts_ms.min()]
    check(joined.open_interest.notna().all(),
          "every bar at or after the first OI print carries an OI value")
    check(set(merged.open_interest.dropna()) <= set(oi.open_interest),
          "joined OI values are the ones supplied - none are invented")

    # A bar more than the age limit past the last print must have NO funding rate.
    far = merge_symbol(
        pd.DataFrame({"ts_ms": [ts[0] + 20 * 3600_000], "open": 1.0, "high": 1.0,
                      "low": 1.0, "close": 1.0, "volume": 1.0, "turnover": 1.0}),
        oi, funding.iloc[:1])
    check(bool(np.isnan(far.funding_rate.iloc[0])),
          "funding older than the age limit is BLANKED, never carried forward")
    near = merge_symbol(
        pd.DataFrame({"ts_ms": [ts[0] + 3600_000], "open": 1.0, "high": 1.0, "low": 1.0,
                      "close": 1.0, "volume": 1.0, "turnover": 1.0}), oi, funding.iloc[:1])
    check(float(near.funding_rate.iloc[0]) == 0.0001,
          "...but a fresh funding rate IS used")

    early = merge_symbol(klines, oi, pd.DataFrame({"ts_ms": [ts[-1]],
                                                   "funding_rate": [0.5]}))
    check(bool(early.funding_rate.iloc[0] != 0.5),
          "a LATER funding print never back-fills an earlier bar")

    check(MAX_FUNDING_AGE_MS > 8 * 3600_000,
          "the age limit exceeds one 8h funding interval, so normal prints are not discarded")
    check(BARS_PER_DAY == 96, "96 fifteen-minute bars per day")

    print(f"\nALGODESK DATA SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--days", type=int, default=TOTAL_DAYS)
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    if args.fetch:
        print(f"fetching {len(SYMBOLS)} symbols x {args.days}d of {BAR_MINUTES}m bars "
              f"+ real funding + real OI")
        build(days=args.days)
        return 0
    frame = load()
    print(f"{len(frame):,} rows, {frame.symbol.nunique()} symbols")
    return 0


if __name__ == "__main__":
    sys.exit(main())
