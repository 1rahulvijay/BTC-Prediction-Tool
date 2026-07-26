"""PHASE 5+6 (POWERED) -- executable surface + null battery on the Oracle deployment data.

20.6 continuous days from a live Oracle box (2026-07-05 -> 2026-07-25):
    5m  : 5,838 rounds   |   15m : 1,949 rounds   |   1.71M two-sided quote snapshots
That clears the pre-declared ROUND gate (>=500). The CALENDAR gate (>=8 weeks) is still short
at 2.9 weeks, so results remain NOT PROMOTABLE -- but they are now powered enough to KILL.

Uses the SAME frozen grid and the SAME deterministic fill engine as the L2 pilot, so the two
runs are directly comparable.

HONEST LIMITATION (stated in every output): the compact recorder stores `top_ask_size` but NOT
top_bid_size. Entry capacity is therefore REAL (verified against displayed ask size, extended
with the cumulative d1/d2/d5 ladder); exit capacity is ASSUMED at 1 share on the top bid. All
runs are qty=1 for that reason. A larger size cannot be honestly claimed from this recorder.

    python backend/research/run_oracle_executable_surface.py --horizon 15
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import defaultdict

import duckdb
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from executable_fill_engine import BookState, net_path, first_barrier, first_profitable  # noqa
import executable_surface_config as CFG  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXEC_DB = os.path.join(ROOT, "data", "btc_duckdbs", "execution_layer.duckdb")
OUT = os.path.join(ROOT, "data", "research", "oracle_executable_surface")
NS = 1_000_000_000
QTY = 1                      # see HONEST LIMITATION above


def build_books(rows, side: str):
    """BookState timeline for one side from compact snapshots.

    Ask ladder is reconstructed from top-of-book plus the CUMULATIVE depth columns
    (d1/d2/d5 = shares available within 1c/2c/5c of best). Bid side carries no recorded size,
    so it is represented as a single level -- which is why every run here is qty=1.
    """
    p = "up" if side == "UP" else "down"
    out = []
    for r in rows:
        ask, bid = r[f"{p}_ask"], r[f"{p}_bid"]
        if ask is None or bid is None or not (0.0 < bid <= ask < 1.0):
            continue
        top = float(r[f"{p}_top_ask_size"] or 0.0)
        d1, d2, d5 = (float(r[f"{p}_d1"] or 0.0), float(r[f"{p}_d2"] or 0.0),
                      float(r[f"{p}_d5"] or 0.0))
        asks = [(ask, top)]
        for px_off, cum, prev in ((0.01, d1, top), (0.02, d2, d1), (0.05, d5, d2)):
            inc = cum - prev
            if inc > 0 and ask + px_off < 1.0:
                asks.append((round(ask + px_off, 4), inc))
        out.append(BookState(seq=len(out), recv_ts_ns=int(float(r["ts"]) * NS),
                             best_bid=bid, best_ask=ask,
                             best_bid_size=1.0, best_ask_size=top,
                             spread=float(r[f"{p}_spread"] or (ask - bid)),
                             asks=asks, bids=[(bid, 1e9)]))
    return out


def load(con, horizon: int, max_rounds: int):
    setts = {int(a): int(s) for a, s in con.execute(
        "SELECT anchor_ts, settled_side FROM pm_round_settlements "
        "WHERE horizon = ? AND settled_side IN (0,1)", (horizon,)).fetchall()}
    cur = con.execute("""
        SELECT anchor_ts, ts, seconds_left, current_side,
               up_bid, up_ask, up_spread, up_top_ask_size, up_d1, up_d2, up_d5,
               down_bid, down_ask, down_spread, down_top_ask_size, down_d1, down_d2, down_d5
        FROM pm_round_snapshots WHERE horizon = ? AND up_ask IS NOT NULL AND down_ask IS NOT NULL
        ORDER BY anchor_ts, ts""", (horizon,))
    cols = [c[0] for c in cur.description]
    rounds, cur_anchor, buf = [], None, []

    def flush():
        if cur_anchor is None or cur_anchor not in setts or len(buf) < 20:
            return
        rounds.append({"anchor_ts": cur_anchor,
                       "winner": "UP" if setts[cur_anchor] == 1 else "DOWN",
                       "rows": buf})
    for rec in cur.fetchall():
        r = dict(zip(cols, rec))
        if r["anchor_ts"] != cur_anchor:
            flush()
            if max_rounds and len(rounds) >= max_rounds:
                return rounds
            cur_anchor, buf = r["anchor_ts"], []
        buf.append(r)
    flush()
    return rounds


def run(horizon: int, max_rounds: int):
    t0 = time.time()
    con = duckdb.connect(EXEC_DB, read_only=True)
    rounds = load(con, horizon, max_rounds)
    con.close()
    print(f"loaded {len(rounds):,} rounds ({time.time()-t0:.0f}s)", flush=True)

    checkpoints = CFG.ENTRY_CHECKPOINTS_S[horizon]
    elig = {"min_ask": CFG.MIN_ASK, "max_ask": CFG.MAX_ASK, "max_spread": CFG.MAX_SPREAD,
            "min_top_ask_size": CFG.MIN_TOP_ASK_SIZE,
            "max_book_staleness_s": CFG.MAX_BOOK_STALENESS_S}

    cells = defaultdict(list)          # key -> [net per share]
    cell_meta = defaultdict(lambda: {"tp": 0, "sl": 0, "settle": 0, "hold": 0.0})
    hazard = defaultdict(lambda: {"n": 0, "t": []})
    weeks_of = defaultdict(list)
    skips = defaultdict(int)

    for k, rnd in enumerate(rounds):
        rows = rnd["rows"]
        end_ts = float(rows[0]["ts"]) + float(rows[0]["seconds_left"])
        wk = time.strftime("%Y-W%W", time.gmtime(float(rows[0]["ts"])))
        books = {"UP": build_books(rows, "UP"), "DOWN": build_books(rows, "DOWN")}
        side_by_t = [(float(r["ts"]), "UP" if float(r["current_side"] or 0) == 1.0 else "DOWN")
                     for r in rows]
        for cp in checkpoints:
            dec = end_ts - cp
            btc_side = None
            for ts, s in side_by_t:
                if ts <= dec:
                    btc_side = s
                else:
                    break
            if btc_side is None:
                skips["no_btc_state"] += 1
                continue
            for side_name in CFG.SIDES:
                asset = btc_side if side_name == "LEADER" else ("DOWN" if btc_side == "UP" else "UP")
                bk = books[asset]
                if len(bk) < 5:
                    skips["no_books"] += 1
                    continue
                settle = 1.0 if asset == rnd["winner"] else 0.0
                for lat in CFG.LATENCIES_MS:
                    p = net_path(bk, int(dec * NS), lat, QTY, settle, eligibility=elig)
                    if not p.eligible:
                        skips[p.reason] += 1
                        continue
                    ft, _ = first_profitable(p)
                    hz = hazard[(side_name, cp, lat)]
                    hz["n"] += 1
                    if ft is not None:
                        hz["t"].append(ft)
                    for tp in CFG.TP_CENTS:
                        for sl in CFG.SL_CENTS:
                            o = first_barrier(p, tp, sl)
                            key = (side_name, cp, tp, sl, lat)
                            cells[key].append(o["net_per_share"])
                            m = cell_meta[key]
                            m[o["exit_kind"].lower()] += 1
                            m["hold"] += o["holding_s"]
                            if tp == 3 and sl == 3 and lat == 500:
                                weeks_of[(side_name, cp)].append((wk, o["net_per_share"]))
        if (k + 1) % 250 == 0:
            print(f"  .. {k+1}/{len(rounds)} rounds  {time.time()-t0:.0f}s", flush=True)
    all_weeks = {time.strftime("%Y-W%W", time.gmtime(float(r["rows"][0]["ts"])))
                 for r in rounds}
    return {"rounds": len(rounds), "cells": cells, "meta": cell_meta, "hazard": hazard,
            "weeks": weeks_of, "calendar_weeks": len(all_weeks), "skips": dict(skips),
            "elapsed": round(time.time() - t0, 1)}


# ---------------------------------------------------------------------------------------------
# PHASE 6 -- null battery
# ---------------------------------------------------------------------------------------------
def bootstrap_ci(x: np.ndarray, iters: int = 4000, seed: int = 20260725):
    rng = np.random.default_rng(seed)
    if len(x) < 5:
        return (float("nan"), float("nan"), float("nan"))
    idx = rng.integers(0, len(x), (iters, len(x)))
    m = x[idx].mean(axis=1)
    return (float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5)), float((m > 0).mean()))


def bh(pvals, alpha=0.05):
    """Benjamini-Hochberg over the WHOLE declared family, not the interesting cells."""
    n = len(pvals)
    order = sorted(range(n), key=lambda i: pvals[i])
    keep = [False] * n
    thresh = 0
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= alpha * rank / n:
            thresh = rank
    for rank, i in enumerate(order, start=1):
        keep[i] = rank <= thresh
    return keep


def analyse(res: dict, horizon: int) -> str:
    L = ["=" * 104,
         f"PHASE 5+6 POWERED -- ORACLE 20.6d  |  {horizon}m  |  qty={QTY}",
         f"config {CFG.CONFIG_VERSION} hash={CFG.config_hash()}",
         f"rounds={res['rounds']:,}   elapsed={res['elapsed']}s",
         "=" * 104]
    n_wk = res.get("calendar_weeks", 0)
    r_ok = res["rounds"] >= CFG.GATE["min_independent_rounds"]
    w_ok = n_wk >= CFG.GATE["min_calendar_weeks"]
    L += [f"GATE: rounds {res['rounds']:,} vs {CFG.GATE['min_independent_rounds']} "
          f"{'PASS' if r_ok else 'FAIL'}  |  calendar weeks {n_wk} vs "
          f"{CFG.GATE['min_calendar_weeks']} {'PASS' if w_ok else 'FAIL'}",
          ("=> PROMOTION-CAPABLE" if (r_ok and w_ok) else
           "=> NOT PROMOTABLE. Powered enough to KILL a hypothesis; not to promote one."),
         "LIMITATION: exit capacity assumed 1 share (recorder stores no top_bid_size).", ""]

    rows, pvals = [], []
    for key, nets in res["cells"].items():
        if len(nets) < 100:
            continue
        x = np.array(nets, float)
        lo, hi, pgt = bootstrap_ci(x)
        m = res["meta"][key]
        n = len(x)
        gw = x[x > 0].sum()
        gl = -x[x <= 0].sum()
        rows.append({"side": key[0], "cp": key[1], "tp": key[2], "sl": key[3], "lat": key[4],
                     "n": n, "ev": x.mean() * 100, "lo": lo * 100, "hi": hi * 100,
                     "win": 100.0 * (x > 0).mean(),
                     "pf": (gw / gl) if gl > 1e-12 else float("inf"),
                     "tp_r": 100.0 * m["tp"] / n, "sl_r": 100.0 * m["sl"] / n,
                     "set_r": 100.0 * m["settle"] / n, "hold": m["hold"] / n,
                     "p": 2 * min(pgt, 1 - pgt)})
        pvals.append(rows[-1]["p"])

    keep = bh(pvals, CFG.BH_ALPHA) if pvals else []
    for r, k in zip(rows, keep):
        r["bh"] = k
    pos = [r for r in rows if r["ev"] > 0]
    sig = [r for r in rows if r.get("bh") and r["ev"] > 0]
    L += [f"cells tested (n>=100): {len(rows):,}   positive EV: {len(pos):,}"
          f" ({100.0*len(pos)/max(1,len(rows)):.1f}%)",
          f"survive Benjamini-Hochberg @alpha={CFG.BH_ALPHA} across the family: "
          f"{len(sig):,}", ""]

    rows.sort(key=lambda r: -r["ev"])
    hdr = (f"{'side':<8}{'cp':>5}{'tp':>4}{'sl':>4}{'lat':>6}{'n':>7}{'EV c':>8}"
           f"{'95% CI':>18}{'win%':>7}{'PF':>7}{'TP%':>6}{'SL%':>6}{'SET%':>6}{'BH':>4}")
    L += ["TOP CELLS BY EV", hdr, "-" * len(hdr)]
    for r in rows[:14]:
        L.append(f"{r['side']:<8}{r['cp']:>5}{r['tp']:>4}{r['sl']:>4}{r['lat']:>6}{r['n']:>7}"
                 f"{r['ev']:>8.2f}  [{r['lo']:>6.2f},{r['hi']:>6.2f}]{r['win']:>7.1f}"
                 f"{r['pf']:>7.2f}{r['tp_r']:>6.1f}{r['sl_r']:>6.1f}{r['set_r']:>6.1f}"
                 f"{'YES' if r.get('bh') else 'no':>4}")

    L += ["", "TRAILING-SIDE CONTROL (same policy, opposite side -- must not also be positive)"]
    lead = {(r["cp"], r["tp"], r["sl"], r["lat"]): r for r in rows if r["side"] == "LEADER"}
    trail = {(r["cp"], r["tp"], r["sl"], r["lat"]): r for r in rows if r["side"] == "TRAILER"}
    both = [(k, lead[k]["ev"], trail[k]["ev"]) for k in lead if k in trail]
    both_pos = [b for b in both if b[1] > 0 and b[2] > 0]
    L.append(f"  matched pairs={len(both):,}   both-sides-positive={len(both_pos):,}"
             f" ({100.0*len(both_pos)/max(1,len(both)):.1f}%)")
    if both:
        s = np.array([b[1] + b[2] for b in both])
        L.append(f"  mean(LEADER EV + TRAILER EV) = {s.mean():+.2f}c   "
                 "(a pure two-sided cost drag should be clearly negative)")

    L += ["", "WEEK STABILITY (tp=3 sl=3 lat=500)"]
    for (side, cp), vals in sorted(res["weeks"].items()):
        agg = defaultdict(list)
        for w, v in vals:
            agg[w].append(v)
        if len(agg) < 2:
            continue
        parts = [f"{w}:{np.mean(v)*100:+.1f}c(n={len(v)})" for w, v in sorted(agg.items())]
        signs = [1 if np.mean(v) > 0 else -1 for _, v in sorted(agg.items())]
        flips = sum(1 for i in range(1, len(signs)) if signs[i] != signs[i-1])
        L.append(f"  {side:<8}cp={cp:<5}flips={flips}  " + "  ".join(parts))

    L += ["", "FIRST-PROFITABLE-EXIT HAZARD (lat=500)"]
    L.append(f"  {'side':<8}{'cp':>5}{'n':>8}{'ever%':>8}{'p25 s':>8}{'p50 s':>8}{'p90 s':>8}")
    for (side, cp, lat), h in sorted(res["hazard"].items(), key=lambda kv: (kv[0][0], -kv[0][1])):
        if lat != 500 or h["n"] < 50:
            continue
        ts = np.sort(np.array(h["t"], float))
        ever = 100.0 * len(ts) / h["n"]
        q = (np.percentile(ts, [25, 50, 90]) if len(ts) else [float("nan")] * 3)
        L.append(f"  {side:<8}{cp:>5}{h['n']:>8}{ever:>8.1f}"
                 f"{q[0]:>8.1f}{q[1]:>8.1f}{q[2]:>8.1f}")

    L += ["", f"SKIPS: {json.dumps(res['skips'], sort_keys=True)}"]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--horizon", type=int, default=15)
    ap.add_argument("--max-rounds", type=int, default=0)
    a = ap.parse_args()
    res = run(a.horizon, a.max_rounds)
    txt = analyse(res, a.horizon)
    print("\n" + txt)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, f"report_{a.horizon}m.txt"), "w", encoding="utf-8") as fh:
        fh.write(txt + "\n")
    print(f"\nwrote -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
