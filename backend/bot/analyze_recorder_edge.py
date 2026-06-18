"""
analyze_recorder_edge.py — THE make-or-break table for the Polymarket bot.
===========================================================================
Reads the live recorder DB (data/execution_layer.duckdb, or its parquet export when the recorder
holds the write lock) and answers the one question the whole project comes down to:

    Does fair_value beat the Polymarket ask after costs, on real recorded rounds?
        fair_value = min(analytic_barrier, keeper_P(Hold))      # conservative, per master spec
        net_edge   = fair_value − current_side_ask − buffer

It simulates ONE taker entry per round (the first snapshot that passes the gate), settles it against
the recorded round outcome, and aggregates AT THE ROUND LEVEL (no snapshot over-counting). Buffers
1c/2c/3c/5c. Round-clustered Wilson lower bound on win rate.

Outputs: prints the make-or-break table + a filter-stack scorecard + a market-calibration check, and
writes data/research/recorder_edge_report.csv. Read-only; never trades; never touches models/the app.

If the recorder hasn't accrued enough resolved rounds yet, it says so and exits cleanly — so this is
safe to run the instant data starts flowing.

Usage:  python backend/bot/analyze_recorder_edge.py [--min-rounds 30]
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys

import numpy as np
import pandas as pd

try:                                  # Windows consoles default to cp1252 and choke on ≥/≤/—
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(ROOT, "data")
DB = os.environ.get("BTC_EXEC_DB") or os.path.join(DATA, "execution_layer.duckdb")
EXP_SNAP = os.path.join(DATA, "pm_export_snapshots.parquet")
EXP_SETT = os.path.join(DATA, "pm_export_settlements.parquet")
OUT_CSV = os.path.join(DATA, "research", "recorder_edge_report.csv")
BUFFERS = (0.01, 0.02, 0.03, 0.05)


def _phi(x):
    return 0.5 * (1.0 + np.vectorize(math.erf)(np.asarray(x, float) / math.sqrt(2.0)))


def _wilson_lb(k, n, z=1.96):
    if n == 0:
        return float("nan")
    p = k / n
    d = 1 + z * z / n
    return (p + z * z / (2 * n) - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / d


def _load():
    """(snapshots, settlements). DB read_only if free; else the recorder's parquet export."""
    import duckdb
    try:
        con = duckdb.connect(DB, read_only=True)
        snap = con.execute("SELECT * FROM pm_round_snapshots").fetchdf()
        sett = con.execute("SELECT * FROM pm_round_settlements").fetchdf()
        con.close()
        return snap, sett, "live DB"
    except Exception:
        if os.path.exists(EXP_SNAP):
            con = duckdb.connect()
            snap = con.execute(f"SELECT * FROM read_parquet('{EXP_SNAP.replace(chr(92),'/')}')").fetchdf()
            sett = (con.execute(f"SELECT * FROM read_parquet('{EXP_SETT.replace(chr(92),'/')}')").fetchdf()
                    if os.path.exists(EXP_SETT) else pd.DataFrame())
            con.close()
            return snap, sett, "recorder export (DB locked)"
        return pd.DataFrame(), pd.DataFrame(), "none"


def _derive(snap):
    """fair_value = min(barrier, keeper); current-side ask/spread/depth; raw edge."""
    s = snap.copy()
    sig_t = s["vol_60s_pct"] * np.sqrt(np.maximum(s["seconds_left"], 1) / 60.0)
    dz = np.abs(s["distance_pct"]) / np.maximum(sig_t, 1e-9)
    s["barrier"] = _phi(np.clip(dz, -8, 8))
    s["fair"] = np.minimum(s["barrier"], s["p_hold_cur"].fillna(s["barrier"]))
    up = s["current_side"] == 1
    s["cur_ask"] = np.where(up, s["up_ask"], s["down_ask"])
    s["cur_spread"] = np.where(up, s["up_spread"], s["down_spread"])
    s["cur_depth2"] = np.where(up, s["up_d2"], s["down_d2"])
    s["raw_edge"] = s["fair"] - s["cur_ask"]
    return s


def _entries(rounds_snap, sett, gate):
    """One entry per round: first snapshot passing `gate`. Returns per-round entry rows w/ win + edge_dur."""
    wins = sett.set_index("slug")["settled_side"].to_dict() if len(sett) else {}
    out = []
    for slug, g in rounds_snap.groupby("slug"):
        if slug not in wins:
            continue
        g = g.sort_values("ts")
        q = g[gate(g)]
        if q.empty:
            continue
        e = q.iloc[0]
        side = int(e["current_side"])
        won = int(wins[slug] == side)
        # edge persistence: consecutive snapshots from entry with raw_edge>0
        after = g[g["ts"] >= e["ts"]]
        dur = 0.0
        for _, r in after.iterrows():
            if r["raw_edge"] > 0:
                dur = r["ts"] - e["ts"]
            else:
                break
        out.append({"slug": slug, "horizon": int(e["horizon"]), "side": side, "ask": float(e["cur_ask"]),
                    "fair": float(e["fair"]), "win": won, "edge_dur": dur})
    return pd.DataFrame(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-rounds", type=int, default=30)
    a = ap.parse_args()
    snap, sett, src = _load()
    nrounds = snap["slug"].nunique() if len(snap) else 0
    nsettled = len(sett) if len(sett) else 0
    print(f"[edge] source={src}  snapshots={len(snap):,}  rounds={nrounds}  settled={nsettled}")
    if nsettled < a.min_rounds:
        print(f"[edge] INSUFFICIENT DATA — need ≥{a.min_rounds} resolved rounds, have {nsettled}.")
        print("[edge] Keep the recorder running through live windows (start_recorder.bat); rerun later.")
        return

    s = _derive(snap)
    os.makedirs(os.path.dirname(OUT_CSV), exist_ok=True)
    rows = []
    print("\n=== MAKE-OR-BREAK TABLE (one taker entry/round; gate = P(Hold)≥0.93, seconds_left≤180, net_edge>0) ===")
    print(f"{'Hz':>4}{'buf':>5}{'signals':>9}{'avg_fair':>10}{'avg_ask':>9}{'win%':>7}{'wilsonLB':>10}{'ROI%':>8}{'net/ct':>9}{'med_dur_s':>11}")
    for hz in sorted(s["horizon"].unique()):
        for buf in BUFFERS:
            gate = (lambda b: (lambda g: (g["p_hold_cur"] >= 0.93) & (g["seconds_left"] <= 180)
                               & (g["raw_edge"] >= b) & g["cur_ask"].notna()))(buf)
            e = _entries(s[s["horizon"] == hz], sett, gate)
            if e.empty:
                print(f"{hz:>4}{int(buf*100):>4}c{0:>9}{'—':>10}{'—':>9}{'—':>7}{'—':>10}{'—':>8}{'—':>9}{'—':>11}")
                rows.append([hz, f"{int(buf*100)}c", 0, "", "", "", "", "", "", ""]); continue
            n = len(e); k = int(e["win"].sum())
            winr = k / n; lb = _wilson_lb(k, n)
            net = float((e["win"] - e["ask"]).mean())              # $ per 1-contract stake
            roi = net / float(e["ask"].mean()) * 100.0
            mdur = float(e["edge_dur"].median())
            print(f"{hz:>4}{int(buf*100):>4}c{n:>9}{e['fair'].mean():>10.3f}{e['ask'].mean():>9.3f}"
                  f"{winr*100:>7.1f}{lb*100:>10.1f}{roi:>8.1f}{net:>9.3f}{mdur:>11.1f}")
            rows.append([hz, f"{int(buf*100)}c", n, round(e['fair'].mean(), 3), round(e['ask'].mean(), 3),
                         round(winr*100, 1), round(lb*100, 1), round(roi, 1), round(net, 3), round(mdur, 1)])

    # filter-stack scorecard at 2c
    print("\n=== FILTER-STACK SCORECARD (buffer 2c — which filter actually helps) ===")
    base = lambda g: (g["seconds_left"] <= 180) & g["cur_ask"].notna()
    stacks = [
        ("P(Hold)≥0.93", lambda g: base(g) & (g["p_hold_cur"] >= 0.93)),
        ("+ net_edge>0", lambda g: base(g) & (g["p_hold_cur"] >= 0.93) & (g["raw_edge"] >= 0.02)),
        ("+ not near line", lambda g: base(g) & (g["p_hold_cur"] >= 0.93) & (g["raw_edge"] >= 0.02) & (g["distance_pct"].abs() >= 0.02)),
        ("+ spread≤3c", lambda g: base(g) & (g["p_hold_cur"] >= 0.93) & (g["raw_edge"] >= 0.02) & (g["distance_pct"].abs() >= 0.02) & (g["cur_spread"] <= 0.03)),
    ]
    print(f"{'filter':22}{'signals':>9}{'win%':>7}{'wilsonLB':>10}{'ROI%':>8}{'med_dur_s':>11}")
    for name, gate in stacks:
        e = _entries(s, sett, gate)
        if e.empty:
            print(f"{name:22}{0:>9}{'—':>7}{'—':>10}{'—':>8}{'—':>11}"); continue
        n = len(e); k = int(e["win"].sum())
        net = float((e["win"] - e["ask"]).mean()); roi = net / float(e["ask"].mean()) * 100.0
        print(f"{name:22}{n:>9}{k/n*100:>7.1f}{_wilson_lb(k,n)*100:>10.1f}{roi:>8.1f}{e['edge_dur'].median():>11.1f}")

    # market calibration — is the ask already efficient?
    print("\n=== MARKET CALIBRATION (when current-side ask = X, does it win X%?) ===")
    wins = sett.set_index("slug")["settled_side"].to_dict()
    s2 = s.dropna(subset=["cur_ask"]).copy()
    s2["won"] = [int(wins.get(sl, -1) == sd) for sl, sd in zip(s2["slug"], s2["current_side"])]
    s2 = s2[s2["slug"].isin(wins)]
    s2["ask_bin"] = (s2["cur_ask"] * 10).round() / 10
    cal = s2.groupby("ask_bin")["won"].agg(["mean", "size"])
    print(cal.round(3).to_string())

    with open(OUT_CSV, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["horizon", "buffer", "signals", "avg_fair", "avg_ask", "win%", "wilson_lb",
                    "roi%", "net_per_contract", "median_edge_dur_s"])
        w.writerows(rows)
    print(f"\n[edge] wrote {OUT_CSV}")
    print("[edge] VERDICT RULE: a buffer row is a real edge only if win% > avg_ask AND wilson_lb keeps ROI>0.")


if __name__ == "__main__":
    main()
