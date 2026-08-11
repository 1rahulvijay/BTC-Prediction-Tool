"""
probe_tp_or_settle.py - TP_OR_SETTLE_V1: early entry, take-profit at +20/35/50%, ELSE hold to settle.
======================================================================================================
The operator's idea, distinct from the KILLED scalp: buy the leader EARLY, sell only if the share
reprices +20..50% (a $1 bet -> exit at 20-50c profit), otherwise hold to settlement. No stop-loss leg:
the spread+fee is paid twice ONLY on TP winners; losers ride to resolution like the settlement rule.

Frozen spec (no grid tuning; thresholds are the operator's own numbers):
  Market : BTC 5m rounds (Kaggle archive 7 quotes; NO 15m quote history exists in any archive --
           the 15m confirmation runs on the live recorder, which records both horizons)
  Entry  : FIRST second with 240..180s left where leader ask in [0.50, 0.70] and spread <= 2c;
           one entry per round; entry = executable ASK + taker fee
  Exits  : A HOLD-TO-SETTLE baseline (winner 1 / loser 0, no exit fee)
           B TP +20%  (first BID >= 1.20 x entry ask -> sell at that bid, taker fee)  else settle
           C TP +35%  else settle
           D TP +50%  else settle
  Latency: 0s and 1s variants.
Theory check: under an efficient (martingale) share path, optional stopping makes B/C/D <= A by the
extra exit fees; TP also CAPS winners that would have converged to $1. If B/C/D beat A, the share
path mean-reverts after big runs -- measure, don't assume. ASCII output.

Usage:  python backend/research/standalone/probe_tp_or_settle.py [--latency 0|1]   |   --selftest
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
ASK_LO, ASK_HI, SPREAD_MAX = 0.50, 0.70, 0.02
WIN_HI, WIN_LO = 240, 180
TPS = (0.20, 0.35, 0.50)


def fee(p):
    return FEE_RATE * p * (1.0 - p)


def replay_round(secs, bu, au, bd, ad, outcome_up, latency=0):
    """secs DESCENDING. Returns dict with entry + per-variant pnl, or None (no qualifying entry)."""
    n = len(secs)
    for i in range(n):
        s = secs[i]
        if s > WIN_HI:
            continue
        if s < WIN_LO:
            return None
        lead_up = bu[i] > bd[i]
        ask = au[i] if lead_up else ad[i]
        bid = bu[i] if lead_up else bd[i]
        if not (ASK_LO <= ask <= ASK_HI) or (ask - bid) > SPREAD_MAX:
            continue
        j = min(i + latency, n - 1)
        entry = au[j] if lead_up else ad[j]
        settle = 1.0 if (lead_up == bool(outcome_up)) else 0.0
        base = settle - entry - fee(entry)                      # variant A: hold to settlement
        out = {"entry": entry, "settle_win": int(settle == 1.0), "A": base}
        for tp in TPS:
            tp_px = entry * (1.0 + tp)
            pnl, hit = base, 0
            for k in range(j + 1, n):
                b = bu[k] if lead_up else bd[k]
                if b >= tp_px:
                    kk = min(k + latency, n - 1)
                    xb = bu[kk] if lead_up else bd[kk]
                    pnl = xb - entry - fee(entry) - fee(xb)     # sold at bid, fee both legs
                    hit = 1
                    break
            out[f"tp{int(tp*100)}"] = pnl
            out[f"hit{int(tp*100)}"] = hit
        return out
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
    rows = []
    for cid, g in tk.groupby("condition_id", sort=False):
        m = meta.loc[cid]
        g = g.sort_values("t")
        secs = (m["end_t"] - g["t"].values).astype(int)          # t ascending -> secs DESCENDING
        r = replay_round(secs, g["bu"].values, g["au"].values, g["bd"].values, g["ad"].values,
                         m["outcome"] == "up", latency=latency)
        if r:
            r["week"] = m["week"]
            rows.append(r)
    d = pd.DataFrame(rows)
    print(f"\n=== TP_OR_SETTLE_5M_V1 (latency={latency}s)  n={len(d):,} entries "
          f"(ask {ASK_LO}-{ASK_HI}, {WIN_HI}-{WIN_LO}s left) ===")
    if not len(d):
        return
    print(f"  avg entry ask {d['entry'].mean()*100:.1f}c   settle-win rate {d['settle_win'].mean()*100:.1f}%")
    print(f"  {'variant':<22}{'EV/share':>10}{'95% LB':>9}{'PF':>7}{'TP hit':>8}{'weeks+':>8}")
    for lbl, col, hitc in [("A HOLD-TO-SETTLE", "A", None)] + [
            (f"{'BCD'[i]} TP +{int(t*100)}% else settle", f"tp{int(t*100)}", f"hit{int(t*100)}")
            for i, t in enumerate(TPS)]:
        pnl = d[col].values
        mean = pnl.mean()
        lb = mean - 1.96 * pnl.std(ddof=1) / math.sqrt(len(pnl))
        gw, gl = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
        pf = gw / gl if gl > 0 else float("inf")
        wk = d.groupby("week")[col].mean()
        hits = f"{d[hitc].mean()*100:5.0f}%" if hitc else "    -"
        print(f"  {lbl:<22}{mean*100:>+9.2f}c{lb*100:>+8.2f}c{pf:>7.2f}{hits:>8}{(wk>0).sum():>5}/{len(wk)}")
    print("  READ: if B/C/D <= A, the TP overlay only donates exit fees + caps winners -> ride to settle.")


def selftest():
    secs = np.arange(260, 0, -1)
    bu = np.clip(np.linspace(0.60, 0.95, len(secs)), 0, 1); au = bu + 0.01
    bd = 1 - au; ad = 1 - bu
    r = replay_round(secs, bu, au, bd, ad, True, latency=0)
    ok = r is not None and r["hit20"] == 1 and r["tp20"] > 0 and r["A"] > 0
    print(f"selftest: {None if not r else {k: round(float(v), 3) for k, v in r.items()}}")
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
