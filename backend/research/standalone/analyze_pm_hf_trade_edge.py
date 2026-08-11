"""
analyze_pm_hf_trade_edge.py - historical P(Hold) vs actual traded-price edge, the HONEST (leader-only) way.
==========================================================================================================
The persistence model predicts ONLY "the current LEADER holds to close" -- NOT arbitrary P(up)/P(down) from
any state. So we trade ONLY the currently-leading side and never fabricate a symmetric opposite probability:

    lead_side  = UP if btc>anchor else DOWN
    edge       = p_hold_current_leader - executed_trade_price(lead_side) - buffer
    take       = edge >= 0   (leading side only, timestamp inside round, seconds_left>0)

Correlated snapshots from one round inflate confidence, so we report BOTH:
  - snapshot-level (every qualifying checkpoint), and
  - ROUND-level (the FIRST qualifying entry per round, counted once) -- the decision-grade number.

⚠️ EXECUTED-TRADE RESEARCH, NOT FILLABILITY PROOF. A trade price is not an executable resting ask; this cannot
prove we could enter at that price. The live /book recorder remains REQUIRED for executable edge. Positive =
thesis alive (confirm on live /book); negative = the market's traded prices were already efficient vs P(Hold).

Usage: python backend/research/standalone/analyze_pm_hf_trade_edge.py [--price vwap30|last]
"""
from __future__ import annotations

try:
    from . import _bootstrap as _research_bootstrap  # noqa: F401
except ImportError:
    import _bootstrap as _research_bootstrap  # noqa: F401

del _research_bootstrap


import argparse
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

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
SNAPS = os.path.join(DATA, "hf_trades_cache", "pm_hf_trade_snapshots.parquet")
OUT_MD = os.path.join(ROOT, "docs", "active", f"HF_TRADE_EDGE_ANALYSIS_{date.today().isoformat()}.md")
BUFFERS = (0.01, 0.02, 0.03, 0.05)


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n; d = 1 + z * z / n
    return (p + z * z / (2 * n)) / d - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d


def prep(df, pc):
    """Leader-only fields: only the currently-ahead side, priced from its executed trades."""
    lead_up = df["distance"].to_numpy(float) > 0
    up_p = df["up_" + pc].to_numpy(float); dn_p = df["down_" + pc].to_numpy(float)
    df = df.assign(
        lead_up=lead_up,
        lead_price=np.where(lead_up, up_p, dn_p),
        lead_won=np.where(lead_up, df["up_won"].to_numpy(int), 1 - df["up_won"].to_numpy(int)),
        p_hold=df["p_hold"].to_numpy(float))
    return df[np.isfinite(df["lead_price"]) & (df["seconds_left"] > 0) & (df["distance"].abs() >= 1e-9)].copy()


def take_signals(df, buf, round_level):
    df = df.assign(edge=df["p_hold"] - df["lead_price"] - buf)
    q = df[df["edge"] >= 0]
    if round_level and len(q):
        q = q.sort_values("seconds_left", ascending=False).groupby("market_id", as_index=False).first()  # first qualifying entry / round
    pnl = np.where(q["lead_won"].to_numpy(int) == 1, 1.0 - q["lead_price"], -q["lead_price"])
    return q.assign(pnl=pnl)


def metrics(q):
    n = len(q)
    if n < 15:
        return f"| {n} | — | — | — | — | — | — | — |"
    won = q["lead_won"].to_numpy(int); price = q["lead_price"].to_numpy(float); pnl = q["pnl"].to_numpy(float)
    wr = won.mean(); lb = wilson(int(won.sum()), n)
    pf = pnl[pnl > 0].sum() / (-pnl[pnl < 0].sum() + 1e-9)
    dd = float((np.cumsum(pnl) - np.maximum.accumulate(np.cumsum(pnl))).min())
    return (f"| {n} | {price.mean():.3f} | {q['p_hold'].mean():.3f} | {wr:.3f} | {lb:.3f} | "
            f"{np.mean(pnl):+.3f} | {pf:.2f} | ${dd:.1f} |")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--price", default="vwap30", choices=["vwap30", "last"])
    a = ap.parse_args()
    if not os.path.exists(SNAPS):
        print(f"missing {SNAPS} — run build_pm_hf_trade_snapshots.py first."); return
    raw = pd.read_parquet(SNAPS)
    df = prep(raw, a.price)
    L = [f"# HF Trade-Price Edge Analysis (leader-only) — {date.today().isoformat()}", "",
         f"First historical test of **our P(Hold) vs the actual traded price**, trading ONLY the currently-leading "
         f"side (no fabricated symmetric probability). {len(df):,} leader snapshots over {df['market_id'].nunique():,} "
         f"rounds (Jan–Mar 2026), price={a.price}.",
         "⚠️ **Executed-trade research, NOT fillability proof** — a trade price is not an executable resting ask; the "
         "live /book recorder is still required to prove executable edge.", ""]
    hdr = "| n | avg price | avg P(Hold) | win% | Wilson-LB | mean PnL/sh | PF | max-DD |"
    sep = "|" + "|".join("---" for _ in range(8)) + "|"
    for buf in BUFFERS:
        L.append(f"\n## Buffer {int(buf*100)}c  (edge = P(Hold_leader) − trade_price − {buf:.2f})")
        snap = take_signals(df, buf, round_level=False)
        rnd = take_signals(df, buf, round_level=True)
        L += [hdr, sep,
              "**snapshot-level (correlated — context only)** " + metrics(snap),
              "**ROUND-level (first qualifying/round — the honest number)** " + metrics(rnd)]
        if len(rnd) >= 30:
            L.append("\n_round-level by cut:_")
            L += [hdr.replace("| n |", "| cut | n |"), "|" + "|".join("---" for _ in range(9)) + "|"]
            for hz in (5, 15):
                L.append(f"| {hz}m " + metrics(rnd[rnd["horizon"] == hz]))
            for sl in sorted(rnd["seconds_left"].unique()):
                L.append(f"| secs_left={int(sl)} " + metrics(rnd[rnd["seconds_left"] == sl]))
    # verdict from round-level @2c
    rnd2 = take_signals(df, 0.02, round_level=True)
    L.append("\n## Verdict")
    if len(rnd2) >= 40:
        won = rnd2["lead_won"].to_numpy(int); price = rnd2["lead_price"].to_numpy(float); pnl = rnd2["pnl"].to_numpy(float)
        lb = wilson(int(won.sum()), len(rnd2)); roi = pnl.sum() / (price.sum() + 1e-9)
        alive = lb > price.mean() and roi > 0
        L.append(f"**{'THESIS ALIVE (research)' if alive else 'THESIS WEAK'} — round-level @2c: {len(rnd2)} rounds, "
                 f"win {won.mean():.3f} (LB {lb:.3f}) vs avg price {price.mean():.3f}, ROI {roi:+.3f}, "
                 f"mean PnL {np.mean(pnl):+.3f}/share.**")
        L.append("- **Alive** → our P(Hold) disagrees with the market's traded price profitably; NEXT: confirm the SAME "
                 "buckets on live /book ask + depth + edge-duration (this is NOT fillability proof)."
                 if alive else
                 "- **Weak** → the market's traded prices were already efficient vs our P(Hold); no historical "
                 "trade-price edge. Do not add strategy layers; continue only with the live /book recorder.")
    else:
        L.append(f"- too few round-level signals @2c ({len(rnd2)}) for a verdict.")
    L.append("\n_A trade price is not an executable resting ask; positive = research signal only, not tradeable proof. "
             "Live /book ask/depth/edge-duration + settlement is the only executable-edge proof._")
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(str(x) for x in L))
    print("\n".join(str(x) for x in L[3:]))
    print(f"\nWrote {OUT_MD}")


if __name__ == "__main__":
    main()
