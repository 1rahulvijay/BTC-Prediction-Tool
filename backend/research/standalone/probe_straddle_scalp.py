"""
probe_straddle_scalp.py - STRADDLE_SCALP_V1: buy BOTH sides near the anchor, TP each leg on the swings.
=========================================================================================================
The operator's "bet both ways" idea in SHARE space: when the round is near 50/50 (price at the anchor,
reversal possible), buy UP and DOWN together; as price swings one way sell that leg at +20..50%; if it
swings back, sell the other leg too ("ride both directions"). Legs that never TP ride to settlement
(one of them always pays $1).

Frozen spec (operator's numbers; no grid tuning):
  Market : BTC 5m rounds (archive 7 quotes; NO 15m history exists -- 15m queued for the live recorder)
  Entry  : FIRST second with 270..180s left where the book is near 50/50: max(bid_up, bid_down) <= 0.55
           and BOTH spreads <= 2c. Buy BOTH at their asks (+ taker fee each). One straddle per round.
  Exits  : each leg independently -- sell at the first BID >= its entry ask x (1+TP), taker fee on sale;
           legs never TP'd settle (winner $1 / loser $0, fee-free).
  TPs    : +20% / +35% / +50%.  Latency 0s and 1s.
Economics: combined entry ~101c + ~3.5c fees vs a guaranteed 100c settle floor -> the swings must
reliably add >4-5c. Under a martingale share path, optional stopping says they cannot. Measure it.
Also reports the outcome mix: both-TP (the dream), TP'd-loser-kept-winner (jackpot), TP'd-winner-
kept-loser (the trap), none-TP. ASCII output.

Usage:  python backend/research/standalone/probe_straddle_scalp.py [--latency 0|1]   |   --selftest
"""
from __future__ import annotations

try:
    from . import _bootstrap as _research_bootstrap  # noqa: F401
except ImportError:
    import _bootstrap as _research_bootstrap  # noqa: F401

del _research_bootstrap


import argparse
import io
import math
import os
import sys
import zipfile

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
ZIP = os.path.join(ROOT, "Kaggle Data", "archive (7).zip")
FEE_RATE = 0.07
WIN_HI, WIN_LO = 270, 180
NEAR_5050, SPREAD_MAX = 0.55, 0.02
TPS = (0.20, 0.35, 0.50)


def fee(p):
    return FEE_RATE * p * (1.0 - p)


def replay_round(secs, bu, au, bd, ad, outcome_up, tp, latency=0):
    """secs DESCENDING. Buy both legs at the first qualifying second; TP each leg independently at
    bid >= entry_ask*(1+tp); un-TP'd legs settle. Returns dict or None."""
    n = len(secs)
    for i in range(n):
        s = secs[i]
        if s > WIN_HI:
            continue
        if s < WIN_LO:
            return None
        if max(bu[i], bd[i]) > NEAR_5050 or (au[i] - bu[i]) > SPREAD_MAX or (ad[i] - bd[i]) > SPREAD_MAX:
            continue
        j = min(i + latency, n - 1)
        eu, ed = au[j], ad[j]                                   # entry asks, both legs
        cost = eu + ed + fee(eu) + fee(ed)
        tp_u, tp_d = eu * (1 + tp), ed * (1 + tp)
        xu = xd = None                                          # leg exit values
        for k in range(j + 1, n):
            if xu is None and bu[k] >= tp_u:
                kk = min(k + latency, n - 1)
                xu = bu[kk] - fee(bu[kk])
            if xd is None and bd[k] >= tp_d:
                kk = min(k + latency, n - 1)
                xd = bd[kk] - fee(bd[kk])
            if xu is not None and xd is not None:
                break
        tp_u_hit, tp_d_hit = xu is not None, xd is not None
        if xu is None:
            xu = 1.0 if outcome_up else 0.0                     # settle, fee-free
        if xd is None:
            xd = 0.0 if outcome_up else 1.0
        if tp_u_hit and tp_d_hit:
            mix = "both_tp"
        elif not tp_u_hit and not tp_d_hit:
            mix = "none_tp"
        else:                                                    # exactly one leg TP'd
            kept_won = (xu == 1.0) or (xd == 1.0)
            mix = "tp_loser_kept_winner" if kept_won else "tp_winner_kept_loser"
        return {"pnl": xu + xd - cost, "cost": cost, "mix": mix}
    return None


def run(latency=0):
    import pyarrow.parquet as pq
    with zipfile.ZipFile(ZIP) as zf:
        mk = pq.read_table(io.BytesIO(zf.read("btc_markets.parquet")),
                           columns=["condition_id", "market_start", "market_end", "outcome"]).to_pandas()
        tk = pq.read_table(io.BytesIO(zf.read("btc_ticks.parquet")),
                           columns=["condition_id", "t", "bu", "au", "bd", "ad"]).to_pandas()
    mk["outcome"] = mk["outcome"].str.lower()
    mk = mk[mk["outcome"].isin(["up", "down"])].copy()
    mk["end_t"] = (mk["market_end"].astype("int64") // 10**6) // 1000
    mk["week"] = mk["market_start"].dt.isocalendar().week.astype(int)
    meta = mk.set_index("condition_id")[["end_t", "outcome", "week"]]
    tk = tk[tk["condition_id"].isin(meta.index)]
    groups = [(cid, g.sort_values("t")) for cid, g in tk.groupby("condition_id", sort=False)]
    for tp in TPS:
        rows = []
        for cid, g in groups:
            m = meta.loc[cid]
            secs = (m["end_t"] - g["t"].values).astype(int)
            r = replay_round(secs, g["bu"].values, g["au"].values, g["bd"].values, g["ad"].values,
                             m["outcome"] == "up", tp, latency=latency)
            if r:
                r["week"] = m["week"]
                rows.append(r)
        d = pd.DataFrame(rows)
        if not len(d):
            print(f"TP+{int(tp*100)}%: no qualifying straddles")
            continue
        pnl = d["pnl"].values
        mean = pnl.mean()
        lb = mean - 1.96 * pnl.std(ddof=1) / math.sqrt(len(pnl))
        gw, gl = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
        pf = gw / gl if gl > 0 else float("inf")
        wk = d.groupby("week")["pnl"].mean()
        mixes = d["mix"].value_counts(normalize=True)
        print(f"\n=== STRADDLE TP+{int(tp*100)}% (latency={latency}s)  n={len(d):,} straddles  "
              f"avg cost {d['cost'].mean()*100:.1f}c ===")
        print(f"  EV/straddle {mean*100:+.2f}c   95% LB {lb*100:+.2f}c   PF {pf:.2f}   "
              f"win-rate {(pnl>0).mean()*100:.0f}%   weeks+ {(wk>0).sum()}/{len(wk)}")
        print("  outcome mix: " + "  ".join(f"{k}={v*100:.0f}%" for k, v in mixes.items()))


def selftest():
    # book swings up then down -> BOTH legs should TP -> the dream case, positive pnl
    up_leg = np.concatenate([np.linspace(0.50, 0.68, 30), np.linspace(0.68, 0.35, 30)])
    secs = np.arange(260, 200, -1)
    bu = up_leg; au = bu + 0.01
    bd = 1 - au; ad = 1 - bu
    r = replay_round(secs, bu, au, bd, ad, False, 0.20, latency=0)
    ok = r is not None and r["mix"] == "both_tp" and r["pnl"] > 0
    print(f"selftest: {None if not r else {k: (round(v,3) if isinstance(v,float) else v) for k,v in r.items()}}")
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--latency", type=int, default=-1)
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if not os.path.exists(ZIP):
        print(f"missing {ZIP}")
        sys.exit(2)
    for lat in ([0, 1] if a.latency < 0 else [a.latency]):
        run(latency=lat)


if __name__ == "__main__":
    main()
