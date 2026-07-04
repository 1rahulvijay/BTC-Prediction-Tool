"""
probe_hf_market_calibration.py - market calibration curve: when the leader TRADES at price X, does it win X%?
============================================================================================================
Characterizes the leader-price anomaly (HF_EDGE_ROBUSTNESS) as a proper CALIBRATION CURVE. For each leader
trade-price bucket, measure the actual settlement win rate vs the price. gap = win% - price:
  gap > 0  -> the market TRADED the leader BELOW its realized win rate (underpriced -- the anomaly)
  gap ~ 0  -> the market was calibrated (efficient)
Split by seconds_left and horizon to see WHERE the mispricing concentrates. Decision-support head #10.

⚠️ On TRADE prices, not resting asks. gap>0 means "leaders traded cheap", NOT "you could buy them cheap" (the
barbell book means the ask may be far higher; a low trade could be a seller hitting a bid). Live /book required
to convert this into an executable calibration. Snapshot-level curve (correlated) + round-level honest n.

Usage: python backend/probe_hf_market_calibration.py
"""
from __future__ import annotations

import math
import os
import sys
from datetime import date

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
SNAPS = os.path.join(DATA, "hf_trades_cache", "pm_hf_trade_snapshots.parquet")
OUT_MD = os.path.join(ROOT, "docs", "active", f"HF_MARKET_CALIBRATION_{date.today().isoformat()}.md")
PC = "vwap30"
BINS = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.80, 1.01]


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n; d = 1 + z * z / n
    return (p + z * z / (2 * n)) / d - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d


def prep(raw):
    lead_up = raw["distance"].to_numpy(float) > 0
    up_p = raw["up_" + PC].to_numpy(float); dn_p = raw["down_" + PC].to_numpy(float)
    d = raw.assign(price=np.where(lead_up, up_p, dn_p),
                   won=np.where(lead_up, raw["up_won"].to_numpy(int), 1 - raw["up_won"].to_numpy(int)))
    return d[np.isfinite(d["price"]) & (d["seconds_left"] > 0) & (d["distance"].abs() >= 1e-9)].copy()


def curve(d, title):
    L = [f"\n### {title}  (n={len(d):,})", "| leader price | n | actual win% | Wilson-LB | gap (win−price) |",
         "|---|---|---|---|---|"]
    d = d.assign(b=pd.cut(d["price"], BINS))
    for b, g in d.groupby("b", observed=True):
        n = len(g)
        if n < 30:
            continue
        wr = g["won"].mean(); pr = g["price"].mean(); lb = wilson(int(g["won"].sum()), n)
        flag = " ⬆underpriced" if (lb - pr) > 0.03 else " ⬇overpriced" if (wr - pr) < -0.03 else ""
        L.append(f"| {b} | {n:,} | {100*wr:.1f} | {100*lb:.1f} | **{100*(wr-pr):+.1f}pp**{flag} |")
    return L


def main():
    if not os.path.exists(SNAPS):
        print(f"missing {SNAPS}"); return
    d = prep(pd.read_parquet(SNAPS))
    # round-level: one snapshot per round (the earliest) for the honest independent curve
    rl = d.sort_values("seconds_left", ascending=False).groupby("market_id", as_index=False).first()
    L = [f"# HF Market Calibration Curve (leader trade-price) — {date.today().isoformat()}", "",
         f"When the leader TRADES at price X, does it win X%? {len(d):,} leader snapshots / {d['market_id'].nunique():,} "
         f"rounds. gap = actual win% − price. ⚠️ **TRADE prices, not resting asks** — gap>0 means leaders traded "
         f"cheap, NOT that you could buy them cheap (barbell book). Live /book required for executable calibration."]
    L += curve(d, "All leader snapshots (calibration curve — correlated)")
    L += curve(rl, "ROUND-level (one earliest snapshot per round — honest independent n)")
    L += curve(d[d["horizon"] == 5], "5m only")
    L += curve(d[d["horizon"] == 15], "15m only")
    L += curve(d[d["seconds_left"] >= 180], "Early (seconds_left ≥ 180)")
    L += curve(d[d["seconds_left"] < 120], "Late (seconds_left < 120)")
    # overall gap
    overall_gap = (d["won"].mean() - d["price"].mean())
    rl_gap = (rl["won"].mean() - rl["price"].mean())
    L += ["\n## Verdict",
          f"- Overall leader gap: snapshot **{100*overall_gap:+.1f}pp** (win {100*d['won'].mean():.1f}% vs price "
          f"{100*d['price'].mean():.1f}%), round-level **{100*rl_gap:+.1f}pp**.",
          f"- {'**Leaders are systematically underpriced in the TRADE data across price levels** — a real (research) '
             'market-calibration finding.' if rl_gap > 0.03 else 'No systematic underpricing — the market trade prices are roughly calibrated.'}",
          "- ⚠️ This is a **trade-price** calibration; the *executable* version (leader ASK vs win rate) can only be "
          "measured on the live /book recorder. gap>0 here is the hypothesis to validate live, not a tradeable edge.",
          "- Decision-support use (once validated live): show `leader historical win rate in this state` next to the "
          "live ask — flag when the ask is below the state's win rate (the cheap-leader signal)."]
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(str(x) for x in L))
    print("\n".join(str(x) for x in L[2:]))
    print(f"\nWrote {OUT_MD}")


if __name__ == "__main__":
    main()
