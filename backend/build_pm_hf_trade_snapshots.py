"""
build_pm_hf_trade_snapshots.py - canonical per-round HF trade snapshots + P(Hold) backfill.
============================================================================================
Builds the trades-based mispricing table (NOT executable-ask; a trade price is not a resting ask).
For each clean-mapped BTC 5m/15m round, at fixed seconds-left checkpoints, records:
  UP/DOWN executed trade price (last trade + VWAP over the trailing window), trade count/volume,
  BTC state vs the round anchor, and the BACKFILLED P(Hold) (the exact live 5-feature persistence head:
  abs_distance_pct, seconds_left, vol_60s_pct, horizon, dist_vol_ratio -> clf -> iso), + the settled side.

Output: data/hf_trades_cache/pm_hf_trade_snapshots.parquet  (feeds analyze_pm_hf_trade_edge.py).

Usage: python backend/build_pm_hf_trade_snapshots.py            # all cached trade days
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.request
from datetime import date

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
CACHE = os.path.join(DATA, "hf_trades_cache")
MATRIX = os.path.join(DATA, "research_matrix_1m.parquet")
REPO = "obadiaha/polymarket-crypto-5m-15m"
CHECKPOINTS = {5: (240, 180, 120, 60, 30), 15: (720, 540, 360, 180, 60)}   # seconds_left per horizon
OUT = os.path.join(CACHE, "pm_hf_trade_snapshots.parquet")


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def _dl(path):
    os.makedirs(CACHE, exist_ok=True)
    local = os.path.join(CACHE, path.replace("/", "_"))
    if os.path.exists(local):
        return pd.read_parquet(local)
    df = pd.read_parquet(io.BytesIO(_get(f"https://huggingface.co/datasets/{REPO}/resolve/main/{path}")))
    df.to_parquet(local, index=False)
    return df


def _phold_fn():
    import joblib
    m = joblib.load(os.path.join(DATA, "saved_models", "persistence_model.pkl"))
    feats, clf, iso = m["features"], m["clf"], m["iso"]   # base 5-feature head

    def phold(abs_dist_pct, secs_left, vol_60s_pct, horizon):
        dvr = abs_dist_pct / (vol_60s_pct + 1e-6)
        row = {"abs_distance_pct": abs_dist_pct, "seconds_left": float(secs_left),
               "vol_60s_pct": vol_60s_pct, "horizon": float(horizon), "dist_vol_ratio": dvr}
        raw = clf.predict_proba([[row[k] for k in feats]])[:, 1]
        return float(iso.predict(raw)[0])
    return phold, m.get("version")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--days", type=int, default=0); a = ap.parse_args()
    tmap = pd.read_parquet(os.path.join(CACHE, "token_map.parquet"))
    tmap = tmap[~tmap["quarantine"]].copy()
    up_of = dict(zip(tmap["market_id"], tmap["up_token"].astype(str)))
    dn_of = dict(zip(tmap["market_id"], tmap["down_token"].astype(str)))
    out_of = dict(zip(tmap["market_id"], tmap["outcome"].astype(str)))
    # BTC 1m path (Jan-Mar within the 360d matrix); vol_60s_pct proxied by a short rolling std
    mx = pd.read_parquet(MATRIX, columns=["ts_ms", "close"]).sort_values("ts_ms").reset_index(drop=True)
    mx["sec"] = mx["ts_ms"] // 1000
    mx["vol3m"] = mx["close"].rolling(3, min_periods=2).std()
    btc_sec = mx["sec"].to_numpy(); btc_px = mx["close"].to_numpy(); btc_vol = mx["vol3m"].to_numpy()

    def btc_at(sec):
        i = int(np.searchsorted(btc_sec, sec, "right")) - 1
        return (float(btc_px[i]), float(btc_vol[i])) if 0 <= i < len(btc_px) else (np.nan, np.nan)

    phold, pver = _phold_fn()
    tree = json.loads(_get(f"https://huggingface.co/api/datasets/{REPO}/tree/main?recursive=true").decode())
    tfiles = sorted(f["path"] for f in tree if f.get("type") == "file" and f["path"].startswith("trades/"))
    if a.days:
        tfiles = tfiles[:a.days]
    rows = []
    for tf in tfiles:
        t = _dl(tf)
        t = t[(t["asset"].astype(str).str.upper() == "BTC") &
              (t["market_id"].astype(str).isin(up_of))].copy()
        if not len(t):
            continue
        t["sec"] = pd.to_datetime(t["timestamp"], utc=True).astype("int64") // 10**9
        t["price"] = t["price"].astype(float); t["size"] = t["size"].astype(float)
        t["tok"] = t["token_id"].astype(str)
        for mid, g in t.groupby("market_id"):
            hz = 5 if "updown-5m" in mid else 15
            try:
                anc_ts = int(str(mid).rsplit("-", 1)[1])
            except Exception:
                continue
            end_ts = anc_ts + hz * 60
            anc_btc, _ = btc_at(anc_ts)
            if not np.isfinite(anc_btc) or anc_btc <= 0:
                continue
            up_t = g[g["tok"] == up_of[mid]].sort_values("sec"); dn_t = g[g["tok"] == dn_of[mid]].sort_values("sec")
            up_won = 1 if out_of[mid] == "Up" else 0
            for sl in CHECKPOINTS[hz]:
                ck = end_ts - sl                                   # checkpoint time
                bp, bv = btc_at(ck)
                if not np.isfinite(bp):
                    continue
                dist = bp - anc_btc; adp = abs(dist) / anc_btc * 100.0
                vol_pct = (bv / anc_btc * 100.0) if np.isfinite(bv) and bv > 0 else 0.02
                ph = phold(adp, sl, vol_pct, hz)                   # P(leading side holds)
                p_up = ph if dist > 0 else (1 - ph)                # P(UP wins)
                # executed price = last trade <= ck, and 30s trailing VWAP
                def px(tt):
                    w = tt[tt["sec"] <= ck]
                    if not len(w):
                        return (np.nan, np.nan, 0, 0.0)
                    last = float(w["price"].iloc[-1])
                    v = w[w["sec"] >= ck - 30]
                    vwap = float((v["price"] * v["size"]).sum() / (v["size"].sum() + 1e-9)) if len(v) else last
                    return (last, vwap, int(len(w)), float(w["size"].sum()))
                ul, uv, un, uvol = px(up_t); dl, dv, dn, dvol = px(dn_t)
                if not (np.isfinite(ul) or np.isfinite(dl)):
                    continue
                rows.append({"market_id": mid, "horizon": hz, "seconds_left": sl, "anchor_btc": round(anc_btc, 2),
                             "btc_now": round(bp, 2), "distance": round(dist, 2), "abs_dist_pct": round(adp, 4),
                             "vol_60s_pct": round(vol_pct, 4), "p_hold": round(ph, 4),
                             "p_win_up": round(p_up, 4), "p_win_down": round(1 - p_up, 4),
                             "up_last": ul, "up_vwap30": uv, "up_trades": un,
                             "down_last": dl, "down_vwap30": dv, "down_trades": dn,
                             "up_won": up_won})
        print(f"  {tf.split('/')[-1]}: {len(rows):,} snapshots so far", flush=True)
    df = pd.DataFrame(rows)
    if not len(df):
        print("0 snapshots — no trade-file markets overlapped the clean token_map (check --days coverage / rebuild "
              "token_map on all days).")
        return
    df.to_parquet(OUT, index=False)
    print(f"\nBUILT {len(df):,} snapshots over {df['market_id'].nunique():,} rounds "
          f"({int((df.horizon==5).sum()):,} 5m / {int((df.horizon==15).sum()):,} 15m rows). P(Hold) head {pver}.")
    print(f"Wrote {OUT}")


if __name__ == "__main__":
    main()
