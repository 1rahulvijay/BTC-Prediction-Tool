"""
build_persistence_dataset.py — A1 (late-entry / T3 persistence) OFFLINE dataset builder.
=========================================================================================
Reconstructs intra-window persistence snapshots from historical SPOT aggTrades
(data.binance.vision) at TICK fidelity — the exact signal the live A1 recorder captures
(`persistence_snapshot` table): "price is `distance` past the line with `seconds_left`
seconds left, on `position` side — does it HOLD to close?" — but generated OFFLINE over
60-90 days with ZERO laptop uptime.

Why this exists: the persistence question is PURELY price-path based, so unlike the live
microstructure (L2 depth) it can be fully reconstructed from archived trades. This collapses
"weeks of live collection" for the A1 engine into one offline job. The live recorder still
runs (it adds live-feed fidelity + the L2 context via B1's feature_outcome_log join), but it
is no longer the critical path.

Output: data/persistence_dataset.parquet  (UNION-compatible with the live persistence_snapshot
rows: same horizon/seconds_left/distance/position/label semantics).

Usage:
  python backend/build_persistence_dataset.py --start 2026-03-15 --end 2026-06-13
  python backend/build_persistence_dataset.py --validate 2026-06-12   # one-day sanity run
"""
import argparse
import os
from datetime import datetime, timedelta, timezone

import numpy as np

# Reuse the proven aggTrade download/loader (same source + ms-normalization as the
# feature backfill — train/serve consistent).
from backfill_trade_features import download_day, load_aggtrades, _daterange as daterange

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data"
)
OUT_PATH = os.path.join(DATA_DIR, "persistence_dataset.parquet")

SNAP_INTERVAL_MS = 15_000        # one snapshot per 15s per window (matches the live recorder)
HORIZONS = (1, 3, 5, 7, 10, 15)  # 5m/15m are the bettable ones; others are practice mirrors


def build_snapshots_for_day(ts: np.ndarray, price: np.ndarray, horizons=HORIZONS) -> list:
    """Tick arrays (sorted ms, price) -> list of persistence-snapshot dicts.

    For each clock-aligned window: anchor = first in-window tick, close = last in-window
    tick, actual_direction = UP if close>=anchor else DOWN (strict, Polymarket rule). Then
    a snapshot every 15s: (seconds_left, distance, position, trailing-60s vol), labelled
    `held` = (position == actual_direction). This IS the late-entry edge: snapshots taken
    with little time left and price already ahead label 1 far more often.
    """
    out = []
    n = len(ts)
    if n < 2:
        return out
    t0, tN = int(ts[0]), int(ts[-1])
    for h in horizons:
        wl = h * 60_000
        w = (t0 // wl) * wl
        while w < tN:
            we = w + wl
            lo = int(np.searchsorted(ts, w, side="left"))
            hi = int(np.searchsorted(ts, we, side="left"))
            if hi - lo < 2:           # window with no/too-few trades (gap) — skip
                w += wl
                continue
            anchor = float(price[lo])
            close = float(price[hi - 1])
            actual = "UP" if close >= anchor else "DOWN"
            k = 0
            while True:
                snap_t = w + k * SNAP_INTERVAL_MS
                if snap_t >= we:
                    break
                j = int(np.searchsorted(ts, snap_t, side="right")) - 1
                if j < lo:            # before the first in-window tick
                    k += 1
                    continue
                p_now = float(price[j])
                dist = p_now - anchor
                pos = "UP" if p_now >= anchor else "DOWN"
                secs_left = int((we - snap_t) // 1000)
                v0 = int(np.searchsorted(ts, snap_t - 60_000, side="left"))
                seg = price[max(v0, lo):j + 1]
                vol = float(np.std(seg) / anchor * 100.0) if len(seg) > 2 and anchor > 0 else 0.0
                out.append({
                    "horizon": h,
                    "window_start_ms": int(w),
                    "seconds_left": secs_left,
                    "seconds_elapsed": int(h * 60 - secs_left),
                    "distance": round(dist, 2),
                    "distance_pct": round(dist / anchor * 100.0, 5) if anchor else 0.0,
                    "position": pos,
                    "vol_60s_pct": round(vol, 5),
                    "anchor": round(anchor, 2),
                    "close": round(close, 2),
                    "actual_direction": actual,
                    "label": 1 if pos == actual else 0,
                })
                k += 1
            w += wl
    return out


def _last_covered(col: str):
    """Date (UTC) of the newest row in the existing parquet, or None."""
    if not os.path.exists(OUT_PATH):
        return None
    try:
        import pandas as pd
        df = pd.read_parquet(OUT_PATH, columns=[col])
        if df.empty:
            return None
        return datetime.fromtimestamp(int(df[col].max()) / 1000.0, tz=timezone.utc).date()
    except Exception:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", help="YYYY-MM-DD")
    ap.add_argument("--end", help="YYYY-MM-DD")
    ap.add_argument("--validate", help="single YYYY-MM-DD sanity run (no write)")
    ap.add_argument("--auto", action="store_true",
                    help="incremental: only build days missing since the last run "
                         "(full --days window on first run). Used by start.bat.")
    ap.add_argument("--days", type=int, default=30, help="window when start/end omitted")
    args = ap.parse_args()

    import pandas as pd

    merge = False
    if args.validate:
        dates, write = [args.validate], False
    elif args.auto:
        last = _last_covered("window_start_ms")
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        if last is not None and last >= yesterday:
            print(f"[auto] persistence dataset current (through {last}) — nothing to do.")
            return
        if last is None:
            ds = (yesterday - timedelta(days=args.days - 1)).strftime("%Y-%m-%d")
            print(f"[auto] no parquet — full build {ds} .. {yesterday} (first run can be slow)", flush=True)
        else:
            ds = (last + timedelta(days=1)).strftime("%Y-%m-%d")
            merge = True
            print(f"[auto] updating persistence {ds} .. {yesterday} (last covered: {last})", flush=True)
        dates, write = list(daterange(ds, yesterday.strftime("%Y-%m-%d"))), True
    else:
        if not (args.start and args.end):
            ap.error("provide --start and --end (or --validate DATE / --auto)")
        dates, write = list(daterange(args.start, args.end)), True

    all_rows = []
    for d in dates:
        try:
            csv = download_day(d)
        except Exception as e:
            print(f"[{d}] download failed ({str(e)[:80]}) — skipping", flush=True)
            continue
        ts, price, _qty, _m = load_aggtrades(csv)
        order = np.argsort(ts, kind="stable")   # ensure sorted for searchsorted
        ts, price = ts[order], price[order]
        rows = build_snapshots_for_day(ts, price)
        all_rows.extend(rows)
        print(f"[{d}] {len(ts):,} trades -> {len(rows):,} snapshots", flush=True)

    if not all_rows:
        print("No snapshots produced.")
        return
    df = pd.DataFrame(all_rows)
    print("\nSummary by horizon (held-to-close rate, and the late-entry edge):")
    for h in sorted(df["horizon"].unique()):
        sub = df[df["horizon"] == h]
        late = sub[(sub["seconds_left"] <= 60) & (sub["distance"].abs() >= 10)]
        lr = f"{late['label'].mean()*100:.1f}% (n={len(late)})" if len(late) else "—"
        print(f"  {h:>2}m: n={len(sub):>7}  base-hold={sub['label'].mean()*100:.1f}%  "
              f"late(<=60s,>=$10 ahead)={lr}")
    if write:
        if merge and os.path.exists(OUT_PATH):
            old = pd.read_parquet(OUT_PATH)
            df = pd.concat([old, df], ignore_index=True).drop_duplicates(
                subset=["horizon", "window_start_ms", "seconds_left"], keep="last")
        os.makedirs(DATA_DIR, exist_ok=True)
        df.to_parquet(OUT_PATH, index=False)
        print(f"\nWrote {len(df):,} snapshots -> {OUT_PATH}")
    else:
        print(f"\n[validate] {len(df):,} snapshots (not written)")


if __name__ == "__main__":
    main()
