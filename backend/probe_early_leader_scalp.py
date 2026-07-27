"""
probe_early_leader_scalp.py - EARLY_LEADER_SCALP_V1 replay (enter at ASK, exit at BID, fees both ways).
========================================================================================================
A SEPARATE strategy from the adopted LATE_LEADER_30S_V1 settlement rule: instead of holding to expiry,
scalp the SHARE PRICE path -- "will this share reprice in my favor before it goes against me?"

Spec (frozen before running -- no grid tuning):
  Market      : BTC 5m up/down only (Kaggle archive 7 per-second quotes, settled rounds)
  Entry window: 180s..60s left -- FIRST qualifying second, one entry per round
  Entry side  : current leader (higher bid side)
  Entry gates : ask in [0.50, 0.70] / spread <= 2c / BTC |distance from anchor| >= $10 (last 1m bar)
  Entry price : the executable ASK (+ taker fee on ask)
  Exits       : TP  first BID >= entry_ask + 5c   (exit at that bid, - taker fee on bid)
                SL  first BID <= entry_ask - 3c   (exit at that bid)
                TIME STOP 30s after entry -> exit at prevailing bid
                SETTLEMENT fallback: if quotes vanish, exit at outcome value (1 or 0), fee-free
  Latency     : base = same-second fills; sensitivity pass = act on signals one second LATE
Report: n, win rate, mean pnl/share, mean-LB (t-approx), PF, TP/SL/timestop rates, median hold,
weekly stability. HONEST: buy@ask sell@bid crosses the spread twice -- the bar is high on purpose.

Usage:  python backend/probe_early_leader_scalp.py [--latency 0|1]
        python backend/probe_early_leader_scalp.py --selftest
"""
from __future__ import annotations

import argparse
import io
import math
import os
import sys
import zipfile

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP = os.path.join(ROOT, "Kaggle Data", "archive (7).zip")
MATRIX = os.path.join(ROOT, "data", "research_matrix_1m.parquet")
FEE_RATE = 0.07
TP, SL, TSTOP = 0.05, 0.03, 30
ASK_LO, ASK_HI, SPREAD_MAX, DIST_MIN = 0.50, 0.70, 0.02, 10.0
WIN_HI, WIN_LO = 180, 60          # entry window, seconds left


def fee(p):
    return FEE_RATE * p * (1.0 - p)


def replay_round(secs, bu, au, bd, ad, dist_ok, outcome_up, latency=0):
    """One round. Arrays are per-second, sorted secs DESCENDING (e.g. 299..0). Returns trade dict or None."""
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
        if not (ASK_LO <= ask <= ASK_HI) or (ask - bid) > SPREAD_MAX or not dist_ok(s):
            continue
        j = i + latency                     # latency: fill at the NEXT observed second's ask
        if j >= n:
            return None
        entry = au[j] if lead_up else ad[j]
        entry_sec = secs[j]
        tp_px, sl_px = entry + TP, entry - SL
        # walk forward from the fill
        for k in range(j + 1, n):
            b = bu[k] if lead_up else bd[k]
            hit = None
            if b >= tp_px:
                hit = "TP"
            elif b <= sl_px:
                hit = "SL"
            elif entry_sec - secs[k] >= TSTOP:
                hit = "TIME"
            if hit:
                kk = min(k + latency, n - 1)  # latency on the exit too
                xb = bu[kk] if lead_up else bd[kk]
                pnl = xb - entry - fee(entry) - fee(xb)
                return {"pnl": pnl, "hit": hit, "hold": entry_sec - secs[kk], "entry": entry, "exit": xb}
        # quotes ran out: settle at outcome value (winner=1, loser=0), no exit fee
        val = 1.0 if (lead_up == bool(outcome_up)) else 0.0
        return {"pnl": val - entry - fee(entry), "hit": "SETTLE", "hold": entry_sec, "entry": entry, "exit": val}
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
    mk["start_ms"] = mk["market_start"].astype("int64") // 10**6
    mk["end_t"] = (mk["market_end"].astype("int64") // 10**6) // 1000
    mx = pq.read_table(MATRIX, columns=["ts_ms", "close"]).to_pandas().drop_duplicates("ts_ms")
    px = dict(zip(mx.ts_ms.astype("int64"), mx.close.astype(float)))
    mk["anchor"] = mk["start_ms"].map(lambda s: px.get(int(s - 60_000)))
    mk = mk.dropna(subset=["anchor"])
    meta = mk.set_index("condition_id")[["anchor", "start_ms", "end_t", "outcome"]]
    tk = tk[tk["condition_id"].isin(meta.index)]
    trades = []
    for cid, g in tk.groupby("condition_id", sort=False):
        m = meta.loc[cid]
        g = g.sort_values("t")
        secs = (m["end_t"] - g["t"].values).astype(int)              # t ascending -> secs_left DESCENDING
        arr = {c: g[c].values for c in ("bu", "au", "bd", "ad")}
        end_ms = int(m["end_t"]) * 1000

        def dist_ok(s, end_ms=end_ms, anchor=float(m["anchor"])):
            # BTC distance from the last COMPLETED 1m bar before the moment (parity: no lookahead)
            bar_open = ((end_ms - s * 1000) // 60_000 - 1) * 60_000
            c = px.get(int(bar_open))
            return c is not None and abs(c - anchor) >= DIST_MIN

        tr = replay_round(secs, arr["bu"], arr["au"], arr["bd"], arr["ad"],
                          dist_ok, m["outcome"] == "up", latency=latency)
        if tr:
            tr["week"] = pd.Timestamp(m["start_ms"], unit="ms").week
            trades.append(tr)
    d = pd.DataFrame(trades)
    print(f"\n=== EARLY_LEADER_SCALP_V1  (latency={latency}s)  n={len(d):,} trades ===")
    if not len(d):
        return
    pnl = d["pnl"].values
    mean, se = pnl.mean(), pnl.std(ddof=1) / math.sqrt(len(pnl))
    lb = mean - 1.96 * se
    gw, gl = pnl[pnl > 0].sum(), -pnl[pnl <= 0].sum()
    pf = gw / gl if gl > 0 else float("inf")
    print(f"  win rate     : {(pnl > 0).mean()*100:.1f}%")
    print(f"  mean pnl     : {mean*100:+.2f}c/share   95% LB: {lb*100:+.2f}c")
    print(f"  profit factor: {pf:.2f}")
    print("  exits        : " + "  ".join(f"{h}={n_}({n_/len(d)*100:.0f}%)"
                                           for h, n_ in d["hit"].value_counts().items()))
    print(f"  median hold  : {d['hold'].median():.0f}s   avg entry ask: {d['entry'].mean()*100:.1f}c")
    wk = d.groupby("week")["pnl"].agg(["count", "mean"])
    pos = int((wk["mean"] > 0).sum())
    print(f"  week stability: {pos}/{len(wk)} weeks positive  "
          + " ".join(f"w{w}:{r['mean']*100:+.1f}c(n{int(r['count'])})" for w, r in wk.iterrows()))
    print("  GATE: mean EV >= +1c after fees, LB > 0, PF >= 1.20, n >= 500, most weeks positive, "
          "works at 1s latency.")


def selftest():
    # synthetic: leader bid ramps up -> TP should trigger and pnl be positive net of fees
    secs = np.arange(200, 140, -1)
    bu = np.linspace(0.55, 0.70, len(secs)); au = bu + 0.01
    bd = 1 - au; ad = 1 - bu
    tr = replay_round(secs, bu, au, bd, ad, lambda s: True, True, latency=0)
    ok = tr is not None and tr["hit"] == "TP" and tr["pnl"] > 0
    print(f"selftest: {tr}")
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--latency", type=int, default=-1, help="-1 = run both 0s and 1s")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if not (os.path.exists(ZIP) and os.path.exists(MATRIX)):
        print(f"missing {ZIP} or {MATRIX}")
        sys.exit(2)
    for lat in ([0, 1] if a.latency < 0 else [a.latency]):
        run(latency=lat)


if __name__ == "__main__":
    main()
