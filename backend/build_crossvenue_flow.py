"""
build_crossvenue_flow.py — A4 cross-source FLOW divergence OFFLINE builder.
===========================================================================
Per-1m-bar Binance SPOT-vs-PERP order-flow divergence — a real sub-15m edge (perp leads
spot; funding/basis tension precedes mean-reversion). Both legs are archived on
data.binance.vision, so this is fully OFFLINE (no uptime), same pattern as the other builders.

WHY spot-vs-perp and not Coinbase/Bybit: Coinbase publishes NO bulk trade history (only a
recent-trades REST endpoint), so a Coinbase cross-venue feature could never be backfilled and
would re-create the train/serve gap. Binance perp (USDⓈ-M) aggTrades ARE archived AND a live
perp-trade feed can be wired for parity — so this is the backfillable, parity-safe choice.

Output: data/crossvenue_flow.parquet
Columns per 1m bar: ts_ms, cvd_spot, cvd_perp, cvd_divergence, perp_spot_basis_bps, vol_spot, vol_perp

PARITY NOTE (read before adding as a model feature): these columns need a matching LIVE recorder
(a Binance futures aggTrade stream feeding the same per-bar CVD) before they go into
features.FEATURE_NAMES — otherwise they'd be constant in serving. Backfill first, wire live, THEN
add the feature slots. (Same keystone discipline as trade_features.py.)

Usage:
  python backend/build_crossvenue_flow.py --validate 2026-06-12
  python backend/build_crossvenue_flow.py --start 2026-03-15 --end 2026-06-13
"""
import argparse
import io
import os
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone

import numpy as np

from backfill_trade_features import download_day, load_aggtrades, _daterange as daterange

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data"
)
CACHE_DIR = os.path.join(DATA_DIR, "backfill_cache")
OUT_PATH = os.path.join(DATA_DIR, "crossvenue_flow.parquet")
PERP_URL = ("https://data.binance.vision/data/futures/um/daily/aggTrades/BTCUSDT/"
            "BTCUSDT-aggTrades-{date}.zip")


def download_perp_day(date: str) -> str:
    """Download+extract one day's Binance USDⓈ-M PERP aggTrades CSV (cached)."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    csv_path = os.path.join(CACHE_DIR, f"BTCUSDT-perp-aggTrades-{date}.csv")
    if os.path.exists(csv_path) and os.path.getsize(csv_path) > 0:
        return csv_path
    url = PERP_URL.format(date=date)
    print(f"  downloading PERP {url} ...", flush=True)
    with urllib.request.urlopen(url, timeout=180) as r:
        blob = r.read()
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = z.namelist()[0]
        with z.open(name) as f, open(csv_path, "wb") as out:
            out.write(f.read())
    print(f"  extracted -> {csv_path} ({os.path.getsize(csv_path):,} bytes)", flush=True)
    return csv_path


def _per_bar(ts, price, qty, m, day_start, nb, bar_ms=60_000):
    """Per-bar signed CVD (taker-buy positive), total volume, and last price. m=is_buyer_maker
    → taker SELL, so aggressive-buy = ~m. Same sign convention as trade_features (parity)."""
    signed = np.where(m, -qty, qty)
    idx = ((ts - day_start) // bar_ms).astype(np.int64)
    keep = (idx >= 0) & (idx < nb)
    idx, signed_k, qty_k, price_k = idx[keep], signed[keep], qty[keep], price[keep]
    cvd = np.zeros(nb); vol = np.zeros(nb); last = np.full(nb, np.nan)
    np.add.at(cvd, idx, signed_k)
    np.add.at(vol, idx, qty_k)
    # last price per bar: ts is sorted, so the final write per index wins
    last[idx] = price_k
    return cvd, vol, last


def build_crossflow_for_day(spot, perp) -> list:
    """spot/perp = (ts, price, qty, m) tick arrays → list of per-1m-bar divergence dicts."""
    s_ts, s_p, s_q, s_m = spot
    p_ts, p_p, p_q, p_m = perp
    if len(s_ts) < 2 or len(p_ts) < 2:
        return []
    day_start = (int(min(s_ts[0], p_ts[0])) // 60_000) * 60_000
    day_end = (int(max(s_ts[-1], p_ts[-1])) // 60_000 + 1) * 60_000
    nb = int((day_end - day_start) // 60_000)
    cs, vs, ls = _per_bar(s_ts, s_p, s_q, s_m, day_start, nb)
    cp, vp, lp = _per_bar(p_ts, p_p, p_q, p_m, day_start, nb)
    out = []
    for b in range(nb):
        if np.isnan(ls[b]) or np.isnan(lp[b]) or ls[b] <= 0:
            continue  # bar with no trades on one venue — skip
        basis_bps = (lp[b] - ls[b]) / ls[b] * 1e4   # perp - spot, in basis points
        out.append({
            "ts_ms": int(day_start + b * 60_000),
            "cvd_spot": round(float(cs[b]), 4),
            "cvd_perp": round(float(cp[b]), 4),
            "cvd_divergence": round(float(cp[b] - cs[b]), 4),   # perp-led flow imbalance
            "perp_spot_basis_bps": round(float(basis_bps), 4),
            "vol_spot": round(float(vs[b]), 4),
            "vol_perp": round(float(vp[b]), 4),
        })
    return out


def _last_covered():
    if not os.path.exists(OUT_PATH):
        return None
    try:
        import pandas as pd
        df = pd.read_parquet(OUT_PATH, columns=["ts_ms"])
        if df.empty:
            return None
        return datetime.fromtimestamp(int(df["ts_ms"].max()) / 1000.0, tz=timezone.utc).date()
    except Exception:
        return None


def _first_covered():
    if not os.path.exists(OUT_PATH):
        return None
    try:
        import pandas as pd
        df = pd.read_parquet(OUT_PATH, columns=["ts_ms"])
        if df.empty:
            return None
        return datetime.fromtimestamp(int(df["ts_ms"].min()) / 1000.0, tz=timezone.utc).date()
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start"); ap.add_argument("--end")
    ap.add_argument("--validate")
    ap.add_argument("--auto", action="store_true",
                    help="incremental: only build days missing since the last run. Used by start.bat.")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args()
    import pandas as pd

    merge = False
    if args.validate:
        dates, write = [args.validate], False
    elif args.auto:
        last = _last_covered()
        first = _first_covered()
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        want_start = yesterday - timedelta(days=args.days - 1)
        if first is not None and first > want_start:
            # Parquet doesn't reach back far enough for --days (e.g. window bumped
            # 50 -> 60). --auto only tops up FORWARD, so rebuild the full window
            # (merge stays False → overwrites). Without this a wider --days is silent.
            ds = want_start.strftime("%Y-%m-%d")
            print(f"[auto] parquet starts {first} but --days {args.days} wants {want_start} "
                  f"— REBUILDING full window {ds} .. {yesterday} (backward extend).", flush=True)
        elif last is not None and last >= yesterday:
            print(f"[auto] cross-venue flow current (through {last}) — nothing to do.")
            return
        elif last is None:
            ds = want_start.strftime("%Y-%m-%d")
            print(f"[auto] no parquet — full build {ds} .. {yesterday} (first run can be slow)", flush=True)
        else:
            ds = (last + timedelta(days=1)).strftime("%Y-%m-%d")
            merge = True
            print(f"[auto] updating cross-venue {ds} .. {yesterday} (last covered: {last})", flush=True)
        dates, write = list(daterange(ds, yesterday.strftime("%Y-%m-%d"))), True
    else:
        if not (args.start and args.end):
            ap.error("provide --start and --end (or --validate DATE / --auto)")
        dates, write = list(daterange(args.start, args.end)), True

    rows = []
    for d in dates:
        try:
            spot = load_aggtrades(download_day(d))
            perp = load_aggtrades(download_perp_day(d))
        except Exception as e:
            print(f"[{d}] download failed ({str(e)[:80]}) — skipping", flush=True)
            continue
        # ensure sorted by ts (compute order ONCE, then apply to all arrays)
        so = np.argsort(spot[0], kind="stable"); spot = tuple(a[so] for a in spot)
        po = np.argsort(perp[0], kind="stable"); perp = tuple(a[po] for a in perp)
        day_rows = build_crossflow_for_day(spot, perp)
        rows.extend(day_rows)
        print(f"[{d}] spot {len(spot[0]):,} + perp {len(perp[0]):,} trades -> {len(day_rows)} bars",
              flush=True)

    if not rows:
        print("No bars produced.")
        return
    df = pd.DataFrame(rows)
    print("\nSanity (should be non-zero, varied):")
    print(df[["cvd_divergence", "perp_spot_basis_bps", "cvd_spot", "cvd_perp"]].describe().round(3))
    if write:
        if merge and os.path.exists(OUT_PATH):
            old = pd.read_parquet(OUT_PATH)
            df = pd.concat([old, df], ignore_index=True).drop_duplicates(
                subset=["ts_ms"], keep="last")
        os.makedirs(DATA_DIR, exist_ok=True)
        df.to_parquet(OUT_PATH, index=False)
        print(f"\nWrote {len(df):,} bars -> {OUT_PATH}")
    else:
        print(f"\n[validate] {len(df):,} bars (not written)")


if __name__ == "__main__":
    main()
