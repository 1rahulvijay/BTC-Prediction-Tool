"""
probe_champion_ask_lift.py - executable-ask lift test with MANDATORY NULLS (the frozen rule family).
=====================================================================================================
Rule family (frozen): late-window LEADER ask edge / actual executable ask / crypto taker fee included /
ONE entry per round / gates tested only as SELECTORS / nulls mandatory.

Data: Kaggle archive (7) btc_ticks (per-second bid/ask both sides) + btc_markets outcomes (settled 5m
rounds, 2026-03-24..2026-05-18) + research_matrix_1m.parquet BTC closes for gate reconstruction.

HONEST LIMIT: the live champion ledger starts 2026-06-18 (NO overlap with these asks), so the actual
recorded SETUP / P(hold) values CANNOT be joined here. Arms C-J from the protocol are approximated by
the gates reconstructible at 1m parity (lead $, trend, 15m agreement); P(hold)'s vol_60s_pct is NOT
reconstructible at parity (rule 3) and is NOT faked. The true ledger-joined test runs on the live
recorders (quotes + champion state recorded together from 2026-07-02).

Arms:  A buy every leader @30s | B @60s | G lead>=20 | H lead>=20+TREND | I +15m agree | J all
Nulls: N1 shuffled gate flag within checkpoint (500 perms) | N2 trailing-side control |
       N3 random same-ask-bucket control | N4 week-split stability | N5 ask-bucket table.
Metrics: n / win% / Wilson-LB / avg ask / EV / EV(LB) / profit factor.  ASCII output.

Usage:  python backend/probe_champion_ask_lift.py          # full run (needs the zip + matrix)
        python backend/probe_champion_ask_lift.py --selftest
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


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    return (p + z * z / (2 * n)) / d - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d


def fee(a):
    return FEE_RATE * a * (1.0 - a)


def stats(ask, win):
    """n, win%, LB, avg ask, EV, EV_LB, profit factor (win pays 1-a-f, loss costs a+f)."""
    n = len(win)
    if n == 0:
        return None
    w = win.mean()
    lb = wilson(int(win.sum()), n)
    a = ask.mean()
    ev = w - a - fee(a)
    evlb = lb - a - fee(a)
    pay = (1.0 - ask - fee(ask))
    cost = (ask + fee(ask))
    gross_w = float(pay[win == 1].sum())
    gross_l = float(cost[win == 0].sum())
    pf = gross_w / gross_l if gross_l > 0 else float("inf")
    return {"n": n, "win": w, "lb": lb, "ask": a, "ev": ev, "evlb": evlb, "pf": pf}


def line(label, s):
    if s is None:
        return f"  {label:<42} (no rounds)"
    return (f"  {label:<42} n={s['n']:>6,} win={s['win']*100:5.1f}% LB={s['lb']*100:5.1f}% "
            f"ask={s['ask']*100:4.1f}c EV={s['ev']*100:+5.1f}c EV(LB)={s['evlb']*100:+5.1f}c PF={s['pf']:.2f}")


def build(zpath=ZIP, matrix=MATRIX):
    import pyarrow.parquet as pq
    with zipfile.ZipFile(zpath) as zf:
        mk = pq.read_table(io.BytesIO(zf.read("btc_markets.parquet")),
                           columns=["condition_id", "market_start", "market_end", "outcome"]).to_pandas()
        tk = pq.read_table(io.BytesIO(zf.read("btc_ticks.parquet")),
                           columns=["condition_id", "t", "bu", "au", "bd", "ad"]).to_pandas()
    mk["outcome"] = mk["outcome"].str.lower()
    mk = mk[mk["outcome"].isin(["up", "down"])].copy()
    mk["start_ms"] = mk["market_start"].astype("int64") // 10**6
    mk["end_ms"] = mk["market_end"].astype("int64") // 10**6
    mk["end_t"] = mk["end_ms"] // 1000
    mx = pq.read_table(matrix, columns=["ts_ms", "close"]).to_pandas().drop_duplicates("ts_ms")
    px = dict(zip(mx.ts_ms.astype("int64"), mx.close.astype(float)))
    mk["anchor"] = mk["start_ms"].map(lambda s: px.get(int(s - 60_000)))
    mk["p180"] = mk["end_ms"].map(lambda e: px.get(int(e - 240_000)))
    mk["p60"] = mk["end_ms"].map(lambda e: px.get(int(e - 120_000)))
    mk["anchor15"] = mk["start_ms"].map(lambda s: px.get(int((s // 900_000) * 900_000 - 60_000)))
    mk = mk.dropna(subset=["anchor", "p180", "p60", "anchor15"])
    mk["d60"] = mk["p60"] - mk["anchor"]
    mk["d180"] = mk["p180"] - mk["anchor"]
    mk["d15"] = mk["p60"] - mk["anchor15"]
    mk["week"] = pd.to_datetime(mk["start_ms"], unit="ms").dt.isocalendar().week.astype(int)
    tk = tk.merge(mk[["condition_id", "end_t"]], on="condition_id", how="inner")
    tk["secs_left"] = tk["end_t"] - tk["t"]
    out = {}
    for chk in (60, 30):
        s = tk[tk["secs_left"] == chk].drop_duplicates("condition_id").set_index("condition_id")
        j = mk.set_index("condition_id").join(s[["bu", "au", "bd", "ad"]], how="inner")
        lead_up = j["bu"] > j["bd"]
        j["ask"] = np.where(lead_up, j["au"], j["ad"])
        j["ask_trail"] = np.where(lead_up, j["ad"], j["au"])           # the TRAILING side's ask (control)
        j["win"] = np.where(lead_up, j["outcome"].eq("up"), j["outcome"].eq("down")).astype(int)
        j["win_trail"] = 1 - j["win"]
        j = j[(j["ask"] > 0.02) & (j["ask"] < 0.97)]
        out[chk] = j
    return out


def run(data, n_perm=500, seed=0):
    rng = np.random.default_rng(seed)
    print("=" * 100)
    print("CHAMPION-GATED EXECUTABLE-ASK LIFT -- frozen rule family, one entry/round, fees included")
    print("NOTE: live ledger (SETUP / real P(hold)) starts 2026-06-18 = NO overlap with these asks;")
    print("      gates below are the 1m-parity reconstructions. True ledger test = live recorders.")
    for chk in (30, 60):
        j = data[chk]
        print(f"\n--- entry @{chk}s left  ({len(j):,} rounds with quotes+context) ---")
        base = stats(j["ask"].values, j["win"].values)
        print(line(f"{'A' if chk == 30 else 'B'}. buy EVERY leader @{chk}s (baseline)", base))
        gates = {
            "G. lead >= $20": (j["d60"].abs() >= 20),
            "H. G + TREND (lead grew, same sign)": (j["d60"].abs() >= 20) & (j["d60"].abs() > j["d180"].abs())
                                                   & (np.sign(j["d60"]) == np.sign(j["d180"])),
            "I. H + 15m agrees": (j["d60"].abs() >= 20) & (j["d60"].abs() > j["d180"].abs())
                                 & (np.sign(j["d60"]) == np.sign(j["d180"]))
                                 & (np.sign(j["d15"]) == np.sign(j["d60"])),
        }
        gates["J. ALL strict"] = gates["I. H + 15m agrees"]
        for lbl, m in gates.items():
            g = stats(j.loc[m, "ask"].values, j.loc[m, "win"].values)
            print(line(lbl, g))
            if g is None or lbl.startswith("J"):
                continue
            # N1: shuffled-gate null -- permute the flag across rounds in this checkpoint; the gate adds
            # information ONLY if its EV lift beats the permuted distribution.
            k = int(m.sum())
            evs = np.empty(n_perm)
            ask_v, win_v = j["ask"].values, j["win"].values
            for i in range(n_perm):
                idx = rng.choice(len(j), size=k, replace=False)
                w = win_v[idx].mean()
                a = ask_v[idx].mean()
                evs[i] = w - a - fee(a)
            plift = float((evs >= g["ev"]).mean())
            print(f"      N1 shuffled-gate null: perm EV mean={evs.mean()*100:+.1f}c  "
                  f"p(perm >= gate)={plift:.2f}  -> {'GATE ADDS NOTHING' if plift > 0.05 else 'gate beats null'}")
            # N3: random same-ask-bucket control -- match the gated arm's ask-bucket histogram.
            bins = np.array([0.02, 0.5, 0.6, 0.7, 0.8, 0.9, 0.97])
            gb = np.digitize(j.loc[m, "ask"].values, bins)
            sel = []
            for b in np.unique(gb):
                pool = np.where(np.digitize(ask_v, bins) == b)[0]
                take = int((gb == b).sum())
                if len(pool) >= take > 0:
                    sel.append(rng.choice(pool, size=take, replace=False))
            if sel:
                sel = np.concatenate(sel)
                c = stats(ask_v[sel], win_v[sel])
                print(f"      N3 same-ask-bucket control: EV={c['ev']*100:+.1f}c vs gate {g['ev']*100:+.1f}c "
                      f"-> {'ask composition explains it' if abs(c['ev'] - g['ev']) < 0.01 else 'residual gate effect'}")
        # N2: trailing-side control (must be clearly negative or the join/labels are broken)
        tr = stats(j["ask_trail"].values[(j["ask_trail"] > 0.02) & (j["ask_trail"] < 0.97)],
                   j["win_trail"].values[(j["ask_trail"] > 0.02) & (j["ask_trail"] < 0.97)])
        print(line("N2. TRAILING-side control (sanity)", tr))
        # N4: week-split stability of the BASELINE rule
        wk = []
        for w_, g_ in j.groupby("week"):
            s_ = stats(g_["ask"].values, g_["win"].values)
            if s_ and s_["n"] >= 200:
                wk.append((w_, s_["n"], s_["ev"]))
        pos = sum(1 for _, _, e in wk if e > 0)
        print(f"  N4 baseline week stability: {pos}/{len(wk)} weeks EV>0  "
              + " ".join(f"w{w_}:{e*100:+.1f}c(n{n_})" for w_, n_, e in wk))
        # N5: ask-bucket stability of the baseline
        print("  N5 baseline by ask bucket: ", end="")
        for lo, hi in [(0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9)]:
            m = (j["ask"] >= lo) & (j["ask"] < hi)
            s_ = stats(j.loc[m, "ask"].values, j.loc[m, "win"].values)
            if s_:
                print(f"{lo:.1f}-{hi:.1f}: EV(LB)={s_['evlb']*100:+.1f}c(n{s_['n']})  ", end="")
        print()
    print("\nPROMOTION THRESHOLDS (paper agent): n>=500, EV>+2c, LB>0, PF>1.2, >=3 positive time blocks,")
    print("nulls materially weaker. Verdict printed above; the doc records the ruling.")


def selftest():
    rng = np.random.default_rng(1)
    n = 3000
    ask = rng.uniform(0.5, 0.95, n)
    win = (rng.random(n) < np.clip(ask + 0.03, 0, 1)).astype(int)   # synthetic +3c calibration gap
    s = stats(ask, win)
    ok = s["n"] == n and 0 < s["win"] < 1 and s["pf"] > 0 and abs(wilson(50, 100) - 0.401) < 0.01
    print(f"selftest: n={s['n']} win={s['win']:.3f} ev={s['ev']*100:+.1f}c pf={s['pf']:.2f}")
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--perms", type=int, default=500)
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if not (os.path.exists(ZIP) and os.path.exists(MATRIX)):
        print(f"missing {ZIP} or {MATRIX}")
        sys.exit(2)
    run(build(), n_perm=a.perms)


if __name__ == "__main__":
    main()
