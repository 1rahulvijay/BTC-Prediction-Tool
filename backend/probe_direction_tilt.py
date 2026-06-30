"""
probe_direction_tilt.py - is the frozen direction ensemble structurally biased toward one side?
================================================================================================
A coin-flip model is expected; a coin-flip model that LEANS one way ~80% of the time is not -- it
loses systematically whenever the market trends the OTHER way (the "wall of UP -> LOST in a
downtrend" the live panel showed). This probe quantifies the tilt:

    tilt = (model's UP-lean rate)  -  (actual UP base rate)

If the model calls UP far more often than UP actually happens, that's a structural tilt to correct
at the NEXT deliberate retrain (class weights / decision threshold). It does NOT add edge --
direction is a coin-flip either way -- it makes the leans HONEST so the model stops systematically
fading trends. Read-only, no training, no serving change.

Inputs:
  * base rate  -> research_matrix_1m.parquet (a FILE -> safe while the app holds the DB)
  * model lean -> the live app's /api/scorecard (model-era filtered); degrade gracefully if down.

Usage:
  python backend/probe_direction_tilt.py
  python backend/probe_direction_tilt.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

import numpy as np
import pandas as pd

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
TILT_FLAG = 0.15   # |model lean rate - base rate| >= 15pts == a tilt worth correcting


def base_up_rate(close, h: int):
    """Unconditional P(close[t+h] > close[t]) -- the 'fair' UP-lean rate. Leak-free / forward only."""
    c = np.asarray(close, float)
    r = np.full(len(c), np.nan)
    if len(c) > h:
        r[:-h] = c[h:] / c[:-h] - 1.0
    m = np.isfinite(r) & (r != 0.0)
    return (float(np.mean(r[m] > 0)) if m.sum() else float("nan")), int(m.sum())


def fetch_live(url="http://127.0.0.1:8000/api/scorecard", timeout=60):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.loads(resp.read())
    except Exception as e:
        return {"_error": str(e)[:120]}


def analyze(close, live: dict, horizons=(5, 15)):
    """PURE core. Returns per-horizon tilt rows."""
    out = {}
    hz = (live or {}).get("horizons") or {}
    for h in horizons:
        base, n_base = base_up_rate(close, h)
        row = {"base_up_rate": base, "n_base": n_base, "model": None, "verdict": "no live leans"}
        v = hz.get(str(h)) or hz.get(h)
        if v and v.get("up_n") is not None and v.get("down_n") is not None:
            up_n, dn_n = int(v["up_n"]), int(v["down_n"])
            tot = up_n + dn_n
            if tot:
                up_acc, dn_acc = v.get("up_acc"), v.get("down_acc")
                model_up_lean = up_n / tot
                implied_up = None
                if up_acc is not None and dn_acc is not None:
                    # sign-truth: actual-UP = correct UP leans + wrong DOWN leans
                    implied_up = (up_n * float(up_acc) + dn_n * (1.0 - float(dn_acc))) / tot
                ref = implied_up if implied_up is not None else base
                tilt = model_up_lean - ref
                if abs(tilt) >= TILT_FLAG:
                    verdict = ("UP-TILT (correct next retrain)" if tilt > 0
                               else "DOWN-TILT (correct next retrain)")
                else:
                    verdict = "balanced (no action)"
                row["model"] = {"up_n": up_n, "dn_n": dn_n, "model_up_lean": model_up_lean,
                                "implied_actual_up": implied_up, "up_acc": up_acc, "dn_acc": dn_acc,
                                "tilt": tilt}
                row["verdict"] = verdict
        out[h] = row
    return out


def _pct(x):
    return f"{x*100:.1f}%" if (x is not None and np.isfinite(x)) else "  -  "


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="base-rate window (0=all in matrix)")
    args = ap.parse_args()
    if not os.path.exists(MATRIX):
        print(f"ERROR: matrix not found: {MATRIX}")
        return
    df = pd.read_parquet(MATRIX, columns=["ts_ms", "close"]).replace([np.inf, -np.inf], np.nan)
    if args.days and "ts_ms" in df:
        df = df[df["ts_ms"] >= df["ts_ms"].max() - args.days * 86400_000].reset_index(drop=True)
    live = fetch_live()

    print("=" * 84)
    print(f"DIRECTION-TILT PROBE  (base rate: {len(df):,} 1m bars from the matrix)")
    print("=" * 84)
    if live.get("_error"):
        print(f"[live] /api/scorecard unreachable ({live['_error']}) -> showing base rates only.")
    else:
        print("[live] model leans from /api/scorecard (model-era filtered).")
    print()
    res = analyze(df["close"], live)
    print(f"  {'h':>3}  {'base UP rate':>13}  {'model UP-lean':>14}  {'actual UP':>10}"
          f"  {'tilt':>8}   {'UP-acc/DN-acc':>14}   verdict")
    for h, r in res.items():
        m = r["model"]
        if m:
            print(f"  {h:>2}m  {_pct(r['base_up_rate']):>13}  "
                  f"{_pct(m['model_up_lean'])} ({m['up_n']}/{m['up_n']+m['dn_n']})".rjust(14)
                  + f"  {_pct(m['implied_actual_up']):>10}  "
                  f"{('%+.1f' % (m['tilt']*100))+'pt':>8}   "
                  f"{_pct(m['up_acc'])}/{_pct(m['dn_acc'])}".rjust(14) + f"   {r['verdict']}")
        else:
            print(f"  {h:>2}m  {_pct(r['base_up_rate']):>13}  {'-':>14}  {'-':>10}  {'-':>8}   "
                  f"{'-':>14}   {r['verdict']}")
    print()
    print("Reading: tilt = model UP-lean rate - actual UP rate. |tilt| >= 15pt == structural side bias.")
    print("Fix (NEXT deliberate retrain only): equalize the horizon's class weights (the 5m UP weight")
    print("was 1.115 vs DOWN 1.102 -> nudge DOWN >= UP) and/or make the UP/DOWN decision threshold")
    print("symmetric. This makes leans honest; it does NOT change the coin-flip accuracy.")


def selftest():
    rng = np.random.default_rng(1)
    close = 60000 + np.cumsum(rng.normal(0, 5, 5000))     # random walk -> base UP ~50%
    b5, n5 = base_up_rate(close, 5)
    assert 0.44 < b5 < 0.56 and n5 > 4000, f"base not ~50%: {b5} n={n5}"
    # fake live: 5m heavily UP-tilted (81% UP leans, both sides coin-flip), 15m balanced
    live = {"horizons": {
        "5":  {"n": 158, "up_n": 128, "up_acc": 0.46, "down_n": 30, "down_acc": 0.43},
        "15": {"n": 59,  "up_n": 28,  "up_acc": 0.46, "down_n": 31, "down_acc": 0.48},
    }}
    res = analyze(close, live)
    assert res[5]["model"]["tilt"] > 0.25, res[5]["model"]["tilt"]
    assert "UP-TILT" in res[5]["verdict"], res[5]["verdict"]
    assert "balanced" in res[15]["verdict"], res[15]["verdict"]
    # implied actual-UP should land near the base rate (~48%), not the 81% lean
    assert 0.40 < res[5]["model"]["implied_actual_up"] < 0.55
    print("probe_direction_tilt self-test: base~50%, 5m UP-TILT detected, 15m balanced. ALL PASS")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
