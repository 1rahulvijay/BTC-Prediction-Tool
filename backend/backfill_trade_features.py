"""
Historical backfill of trade-derived features (V3 N1b)
======================================================
Downloads Binance **SPOT** BTCUSDT aggTrades from data.binance.vision and computes per-1m-bar
CVD / delta / divergence / VPIN using the SAME shared functions as the live recorder
(`trade_features.py`) — so the next retrain sees full history on features the base models
currently lack, with train/serve consistency guaranteed.

  SPOT (matches the live btcusdt@aggTrade feed — NOT futures):
    https://data.binance.vision/data/spot/daily/aggTrades/BTCUSDT/BTCUSDT-aggTrades-YYYY-MM-DD.zip
  Funding velocity (inherently futures; matches the live futures funding feed):
    GET https://fapi.binance.com/fapi/v1/premiumIndexKlines  (1m premium-index → velocity)

Output: a parquet keyed by 1m candle OPEN time (ms) — the same key signal_history uses —
with columns: cvd_change, cvd_1m, cvd_5m, delta, vpin, cvd_divergence, funding_velocity.
The training pipeline (N4) merges this into the per-bar signal history for the retrain.

USAGE (run with the backend CLOSED so the 16GB box has RAM headroom):
    python backend/backfill_trade_features.py --start 2026-05-10 --end 2026-06-08
    python backend/backfill_trade_features.py --validate 2026-06-08   # one-day sanity run

Bar-key convention: candle OPEN time T (ms). Features for bar T use the trade window
ending at T+60_000 (candle close), mirroring the live recorder which snapshots at close.
"""

import argparse
import io
import os
import sys
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
import trade_features as tf  # shared keystone

SPOT_AGG_URL = ("https://data.binance.vision/data/spot/daily/aggTrades/BTCUSDT/"
                "BTCUSDT-aggTrades-{date}.zip")
PREMIUM_KLINES_URL = ("https://fapi.binance.com/fapi/v1/premiumIndexKlines"
                      "?symbol=BTCUSDT&interval=1m&startTime={start}&endTime={end}&limit=1500")

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
CACHE_DIR = os.path.join(DATA_DIR, "backfill_cache")
MIN_MS = 60_000


def _daterange(start: str, end: str):
    s = datetime.strptime(start, "%Y-%m-%d").date()
    e = datetime.strptime(end, "%Y-%m-%d").date()
    d = s
    while d <= e:
        yield d.strftime("%Y-%m-%d")
        d += timedelta(days=1)


def download_day(date: str) -> str:
    """Download+extract one day's SPOT aggTrades CSV (cached). Returns CSV path."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    csv_path = os.path.join(CACHE_DIR, f"BTCUSDT-aggTrades-{date}.csv")
    if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
        return csv_path
    url = SPOT_AGG_URL.format(date=date)
    print(f"  downloading {url} ...", flush=True)
    with urllib.request.urlopen(url, timeout=120) as r:
        blob = r.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = z.namelist()[0]
        with z.open(name) as f, open(csv_path, "wb") as out:
            out.write(f.read())
    print(f"  extracted -> {csv_path} ({os.path.getsize(csv_path):,} bytes)", flush=True)
    return csv_path


def load_aggtrades(csv_path: str, nrows: int | None = None):
    """Load (ts_ms, price, qty, is_buyer_maker) from a SPOT aggTrades CSV.
    SPOT columns: aggId, price, qty, firstId, lastId, transactTime(ms), isBuyerMaker, isBestMatch.
    Handles both headered and headerless files. Uses light dtypes to bound memory.
    """
    import pandas as pd
    # Detect header: peek first byte of the price column.
    with open(csv_path, "r") as f:
        first = f.readline().split(",")
    has_header = not _is_float(first[1] if len(first) > 1 else "")
    df = pd.read_csv(
        csv_path,
        header=0 if has_header else None,
        usecols=[1, 2, 5, 6],
        names=None if has_header else ["price", "qty", "ts", "m"],
        nrows=nrows,
        dtype={1: np.float64, 2: np.float64, 5: np.int64, 6: "boolean"}
        if has_header else {"price": np.float64, "qty": np.float64, "ts": np.int64, "m": "boolean"},
    )
    df.columns = ["price", "qty", "ts", "m"]
    # Some files store is_buyer_maker as true/false strings; pandas "boolean" handles it.
    ts = df["ts"].to_numpy(dtype=np.int64)
    price = df["price"].to_numpy(dtype=np.float64)
    qty = df["qty"].to_numpy(dtype=np.float64)
    m = df["m"].to_numpy(dtype=bool)
    # Normalize timestamps to MILLISECONDS. Binance switched aggTrades to MICROSECONDS in
    # 2025+ data (16-digit ts), but the live WS aggTrade feed is milliseconds (13-digit).
    # The live recorder is ms, so the backfill MUST be ms too (train/serve consistency).
    if len(ts):
        med = int(np.median(ts[: min(1000, len(ts))]))
        if med > 10**14:        # microseconds -> ms
            ts = ts // 1000
        elif med < 10**11:      # seconds -> ms
            ts = ts * 1000
    return ts, price, qty, m


def _is_float(s: str) -> bool:
    try:
        float(s)
        return True
    except Exception:
        return False


def compute_day_bars(ts, price, qty, m, *, bucket_volume_btc=None):
    """Compute per-1m-bar trade features for one day's trades. Returns dict keyed by
    bar-open ms -> {cvd_change, cvd_1m, cvd_5m, delta, vpin}."""
    if len(ts) == 0:
        return {}
    sq = tf.signed_quantity(qty, m)
    # 1m bar opens spanning the data.
    t0 = (int(ts.min()) // MIN_MS) * MIN_MS
    t1 = (int(ts.max()) // MIN_MS) * MIN_MS
    bar_opens = np.arange(t0, t1 + MIN_MS, MIN_MS, dtype=np.int64)
    bar_closes = bar_opens + MIN_MS  # window ends at close

    cvd = tf.per_bar_cvd(ts, sq, bar_closes)

    # VPIN — FIXED shared bucket volume (trade_features.DEFAULT_BUCKET_VOLUME_BTC), the same
    # constant the live streaming recorder uses. (Was calibrated per-run, which would have
    # made historical VPIN a different feature than live VPIN = train/serve skew.)
    if bucket_volume_btc is None:
        bucket_volume_btc = tf.DEFAULT_BUCKET_VOLUME_BTC
    vb = tf.vpin_buckets(ts, sq, qty, bucket_volume=bucket_volume_btc,
                         rolling_buckets=tf.DEFAULT_ROLLING_BUCKETS)
    vpin_bar = tf.map_vpin_to_bars(vb["bucket_end_ts"], vb["vpin"], bar_closes)

    # Large-trade flow — SAME shared fn/constants as the live recorder (order_flow).
    lt = tf.large_trade_per_bar(ts, price, qty, m, bar_closes)

    out = {}
    for i, t in enumerate(bar_opens):
        out[int(t)] = {
            "cvd_change": float(cvd["cvd_change"][i]),
            "cvd_1m": float(cvd["cvd_1m"][i]),
            "cvd_5m": float(cvd["cvd_5m"][i]),
            "delta": float(cvd["delta"][i]),
            "vpin": float(vpin_bar[i]),
            "large_trade_delta": float(lt["large_trade_delta"][i]),
            "large_trade_imbalance": float(lt["large_trade_imbalance"][i]),
        }
    return out, bucket_volume_btc


def fetch_funding_velocity(start_ms: int, end_ms: int) -> dict:
    """Per-1m funding velocity = Δ(premium-index close). Keyed by bar-open ms."""
    import json
    out = {}
    cursor = start_ms
    prev_close = None
    while cursor < end_ms:
        url = PREMIUM_KLINES_URL.format(start=cursor, end=end_ms)
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                batch = json.loads(r.read())
        except Exception as e:
            print(f"  premium-index fetch failed: {e}", flush=True)
            break
        if not batch:
            break
        for k in batch:
            open_t = int(k[0])
            close = float(k[4])
            vel = 0.0 if prev_close is None else (close - prev_close)
            out[open_t] = vel
            prev_close = close
        cursor = int(batch[-1][0]) + MIN_MS
        if len(batch) < 1500:
            break
    return out


OUT_PATH = os.path.join(DATA_DIR, "trade_features_backfill.parquet")


def _last_covered_date(out_path: str):
    """Date (UTC) of the newest bar in an existing parquet, or None."""
    if not os.path.exists(out_path):
        return None
    try:
        import pandas as pd
        df = pd.read_parquet(out_path, columns=["candle_ts"])
        if df.empty:
            return None
        return datetime.fromtimestamp(int(df["candle_ts"].max()) / 1000.0,
                                      tz=timezone.utc).date()
    except Exception:
        return None


def _first_covered_date(out_path: str):
    """Date (UTC) of the OLDEST bar in an existing parquet, or None."""
    if not os.path.exists(out_path):
        return None
    try:
        import pandas as pd
        df = pd.read_parquet(out_path, columns=["candle_ts"])
        if df.empty:
            return None
        return datetime.fromtimestamp(int(df["candle_ts"].min()) / 1000.0,
                                      tz=timezone.utc).date()
    except Exception:
        return None


def run(start: str, end: str, validate_nrows: int | None = None, keep_cache: bool = False,
        merge_existing: bool = False):
    import pandas as pd
    all_bars = {}
    bv = None
    for date in _daterange(start, end):
        print(f"[{date}]", flush=True)
        try:
            csv = download_day(date)
        except urllib.error.HTTPError as e:
            # Binance publishes each day's file with a lag — a 404 for the most
            # recent day is NORMAL, not an error. Skip the day instead of aborting
            # the whole run (an abort threw away every other day's work and exited
            # nonzero, which looked like a failure at app startup).
            if e.code == 404:
                print(f"  not published yet on data.binance.vision (404) — skipping {date}", flush=True)
                continue
            raise
        ts, price, qty, m = load_aggtrades(csv, nrows=validate_nrows)
        print(f"  {len(ts):,} trades", flush=True)
        bars, bv = compute_day_bars(ts, price, qty, m, bucket_volume_btc=bv)
        all_bars.update(bars)
        del ts, price, qty, m  # free memory between days
        # Delete the extracted CSV after processing so a 30-day run uses ~1GB of transient
        # disk instead of ~30GB. Pass keep_cache=True to retain them for re-runs.
        if not keep_cache and validate_nrows is None:
            try:
                os.remove(csv)
            except Exception:
                pass

    if not all_bars:
        print("No bars computed."); return

    keys = sorted(all_bars)
    # Funding velocity over the span.
    fv = fetch_funding_velocity(keys[0], keys[-1] + MIN_MS)

    # CVD divergence is NOT computed here — it needs closes, which live with the
    # training klines. The per-bar cvd_1m column below is what the merge step (N4)
    # derives divergence from, via the same shared `cvd_divergence` fn.

    rows = []
    for k in keys:
        b = all_bars[k]
        rows.append({
            "candle_ts": k,
            "cvd_change": b["cvd_change"],
            "cvd_1m": b["cvd_1m"],
            "cvd_5m": b["cvd_5m"],
            "delta": b["delta"],
            "vpin": b["vpin"],
            "large_trade_delta": b["large_trade_delta"],
            "large_trade_imbalance": b["large_trade_imbalance"],
            "funding_velocity": float(fv.get(k, 0.0)),
        })
    df = pd.DataFrame(rows)
    os.makedirs(CACHE_DIR, exist_ok=True)
    out_path = OUT_PATH
    # Incremental mode: append the new days onto the existing parquet (dedupe by candle_ts,
    # new rows win) so --auto runs only download what's missing instead of 30 days every time.
    if merge_existing and os.path.exists(out_path):
        try:
            old = pd.read_parquet(out_path)
            df = (pd.concat([old, df], ignore_index=True)
                    .drop_duplicates(subset="candle_ts", keep="last")
                    .sort_values("candle_ts").reset_index(drop=True))
            print(f"  merged with existing parquet ({len(old):,} old rows)")
        except Exception as e:
            print(f"  merge with existing parquet failed ({e}) — writing new data only")
    df.to_parquet(out_path, index=False)
    print(f"\nWrote {len(df):,} bars -> {out_path}")
    print(f"bucket_volume_btc used: {bv:.2f}")
    print("Sanity (should be non-zero, varied):")
    print(df[["cvd_1m", "cvd_5m", "vpin", "funding_velocity"]].describe().to_string())


def _default_range(days: int = 30):
    """Last `days` full UTC days ending YESTERDAY (today's file isn't published yet)."""
    end = datetime.now(timezone.utc).date() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def main():
    ap = argparse.ArgumentParser(description="Backfill trade-derived features from data.binance.vision")
    ap.add_argument("--start", help="YYYY-MM-DD (default: 30 days ago)")
    ap.add_argument("--end", help="YYYY-MM-DD (default: yesterday)")
    ap.add_argument("--days", type=int, default=30, help="window length when start/end omitted")
    ap.add_argument("--keep-cache", action="store_true", help="keep extracted CSVs (default: delete to save disk)")
    ap.add_argument("--validate", help="single date YYYY-MM-DD, capped read for a quick sanity check")
    ap.add_argument("--auto", action="store_true",
                    help="incremental: only download days missing since the last run "
                         "(full default window on first run; instant no-op when current). "
                         "Used by start.bat so the app always boots with fresh backfill data.")
    args = ap.parse_args()
    if args.validate:
        # Cap to ~1.2M trades (~a few hours) so it runs fast and light.
        run(args.validate, args.validate, validate_nrows=1_200_000)
    elif args.auto:
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        last = _last_covered_date(OUT_PATH)
        first = _first_covered_date(OUT_PATH)
        ds_want, de_want = _default_range(args.days)
        want_start = datetime.strptime(ds_want, "%Y-%m-%d").date()
        if last is None:
            ds, de = ds_want, de_want
            print(f"[auto] no existing parquet — full backfill {ds} .. {de} "
                  f"(first run: multi-GB download, can take a while)", flush=True)
            run(ds, de, keep_cache=args.keep_cache)
        elif first is not None and first > want_start:
            # The existing parquet does not reach back far enough for the requested
            # --days window (e.g. the operator bumped 50 -> 60). --auto only ever tops
            # up the FORWARD end, so without this a wider window is silently ignored.
            # Rebuild the full window (re-extracts from cache if present — no re-download).
            print(f"[auto] parquet starts {first} but --days {args.days} wants {want_start} "
                  f"— REBUILDING full window {ds_want} .. {de_want} (backward extend).", flush=True)
            run(ds_want, de_want, keep_cache=args.keep_cache)
        elif last >= yesterday:
            print(f"[auto] backfill is current (covers {first} through {last}) — nothing to do.")
        else:
            ds = (last + timedelta(days=1)).strftime("%Y-%m-%d")
            de = yesterday.strftime("%Y-%m-%d")
            print(f"[auto] updating backfill {ds} .. {de} (last covered: {last})", flush=True)
            run(ds, de, keep_cache=args.keep_cache, merge_existing=True)
    else:
        start, end = (args.start, args.end)
        if not (start and end):
            ds, de = _default_range(args.days)
            start = start or ds
            end = end or de
        print(f"Backfilling {start} .. {end} (keep_cache={args.keep_cache})", flush=True)
        run(start, end, keep_cache=args.keep_cache)


if __name__ == "__main__":
    main()
