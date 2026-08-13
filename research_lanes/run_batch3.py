"""WAIT_VS_BUY_V1 and POLY_SETTLEMENT_CONVEXITY_V1 — both from PM data already on disk.

WAIT_VS_BUY_V1
    Model says buy. Should you cross the ask NOW, or wait? Measures the best executable ask
    reachable within the next N seconds of the SAME round, against crossing immediately.
    Waiting is not free: the ask can rise, and the round can end.

POLY_SETTLEMENT_CONVEXITY_V1
    dP/dBTC is the contract's delta; how that delta grows as settlement nears and the anchor
    is close is its gamma. High gamma plus a stale quote is where a small BTC move should
    reprice the contract most.

    python research_lanes/run_batch3.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

LANES = Path(__file__).resolve().parent
sys.path.insert(0, str(LANES))
from common.pm_data import load_official, round_bootstrap  # noqa: E402


def wait_vs_buy(d) -> dict:
    """Best ask reachable in the next N seconds vs crossing now."""
    x = d.sort_values(["round_id", "ts"]).copy()
    out = []
    for horizon_s in (10, 30, 60):
        best, rose, ended = [], [], []
        for _, g in x.groupby("round_id", sort=False):
            ts = g["ts"].to_numpy(float)
            ask = g["up_ask"].to_numpy(float)
            for i in range(len(g)):
                w = (ts > ts[i]) & (ts <= ts[i] + horizon_s)
                if not w.any():
                    ended.append(1.0); continue
                ended.append(0.0)
                fut = ask[w].min()
                best.append(ask[i] - fut)          # >0 means waiting got a better price
                rose.append(1.0 if ask[w].min() > ask[i] else 0.0)
        b = round_bootstrap(np.array(best), np.zeros(len(best)), np.mean, n_boot=1) \
            if False else None
        arr = np.array(best, float)
        out.append({"horizon_s": horizon_s, "n": int(len(arr)),
                    "mean_improvement": float(arr.mean()),
                    "median_improvement": float(np.median(arr)),
                    "pct_improved": float((arr > 0).mean()),
                    "pct_ask_rose": float(np.mean(rose)),
                    "pct_no_future_quote": float(np.mean(ended))})
    return {"by_horizon": out}


def settlement_convexity(d) -> dict:
    """Delta = dP_poly / dBTC(bps), by time remaining and distance from anchor."""
    x = d.sort_values(["round_id", "ts"]).copy()
    x["mid"] = (x["up_bid"] + x["up_ask"]) / 2.0
    x["d_mid"] = x.groupby("round_id")["mid"].diff()
    x["d_btc"] = x.groupby("round_id")["btc_price"].diff()
    x["d_btc_bps"] = x["d_btc"] / x["btc_price"] * 1e4
    m = x["d_btc_bps"].abs().between(0.5, 200) & x["d_mid"].notna()
    x = x[m]
    x["b_time"] = pd.cut(x["seconds_left"], [0, 60, 120, 300, 600, 1e9],
                         labels=["<60s", "60-120s", "2-5m", "5-10m", ">10m"])
    x["b_dist"] = pd.cut(x["distance_bps"].abs(), [0, 3, 8, 20, 1e9],
                         labels=["0-3bps", "3-8bps", "8-20bps", ">20bps"])
    rows = []
    for key, sub in x.groupby(["b_time", "b_dist"], observed=True):
        if len(sub) < 300:
            continue
        # cents of probability per bp of BTC move
        slope = float(np.polyfit(sub["d_btc_bps"], sub["d_mid"], 1)[0])
        rows.append({"cell": " | ".join(map(str, key)), "n": len(sub),
                     "n_rounds": int(sub["round_id"].nunique()),
                     "delta_cents_per_bp": slope * 100.0})
    rows.sort(key=lambda r: -abs(r["delta_cents_per_bp"]))
    return {"cells": rows}


def main() -> int:
    d = load_official()
    print(f"rows={len(d):,} rounds={d.round_id.nunique():,} days={d.day.nunique()}\n")

    print("=== WAIT_VS_BUY_V1 ===")
    w = wait_vs_buy(d)
    print(f"{'wait':>6}{'n':>10}{'mean gain':>12}{'median':>9}{'% better':>10}"
          f"{'% worse':>9}{'% no quote':>12}")
    for r in w["by_horizon"]:
        print(f"{r['horizon_s']:>5}s{r['n']:>10,}{r['mean_improvement']:>+12.4f}"
              f"{r['median_improvement']:>+9.4f}{r['pct_improved']:>10.1%}"
              f"{r['pct_ask_rose']:>9.1%}{r['pct_no_future_quote']:>12.1%}")

    print("\n=== POLY_SETTLEMENT_CONVEXITY_V1 ===")
    c = settlement_convexity(d)
    print(f"{'cell':<26}{'n':>8}{'rounds':>8}{'delta (cents per bp of BTC)':>30}")
    for r in c["cells"][:12]:
        print(f"{r['cell']:<26}{r['n']:>8,}{r['n_rounds']:>8}"
              f"{r['delta_cents_per_bp']:>30.4f}")

    (LANES / "batch3_results.json").write_text(
        json.dumps({"wait_vs_buy": w, "convexity": c}, indent=2, default=float),
        encoding="utf-8")
    print(f"\nwrote {LANES / 'batch3_results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
