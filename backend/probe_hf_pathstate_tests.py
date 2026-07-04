"""
probe_hf_pathstate_tests.py — three BTC-path Category-A tests on existing HF data
=================================================================================
Runs the three clean, existing-data tests from PREDICTION_HEADS_DATA_SPLIT_AND_TEST_LEDGER:
  (1) Round archetype (#14)      — classify each round QUIET/TREND/CHOP/ACTIVE; does leader-hold differ?
  (2) Entry-timing quality (#2)  — leader-hold rate by seconds-left checkpoint (and distance).
  (3) Cross-market consistency (#23) — when 5m and 15m leaders AGREE vs DISAGREE, does the 5m leader hold more?

Uses BTC `distance` (btc_now-anchor) + `vol_60s_pct` + settlement `up_won` ONLY. It deliberately does NOT use
leader trade price, so it is free of the trade-price / Binance-leads-oracle latency confound that taints the
calibration tests. Leader = sign(distance); held = the leading side finished ahead (up_won).

Scope: HF March rounds where the leader had trades (~5,893 rounds). Descriptive first read, not a causal
predictor and not proof. Round-level where noted; Wilson lower bounds throughout.

Usage: python backend/probe_hf_pathstate_tests.py
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
OUT_MD = os.path.join(ROOT, "docs", "active", f"HF_PATHSTATE_TESTS_{date.today().isoformat()}.md")


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n; d = 1 + z * z / n
    return (p + z * z / (2 * n)) / d - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d


def _anchor_ts(mid):
    try:
        return int(str(mid).split("-")[-1])
    except Exception:
        return 0


def load():
    df = pd.read_parquet(SNAPS)
    df = df[df["distance"].abs() >= 1e-9].copy()
    df["held"] = np.where(df["distance"] > 0, df["up_won"], 1 - df["up_won"]).astype(int)
    df["anchor_ts"] = df["market_id"].map(_anchor_ts)
    df["abs_time"] = df["anchor_ts"] + (df["horizon"] * 60 - df["seconds_left"])
    return df


def hline(g, label):
    n = len(g)
    if n < 30:
        return f"| {label} | n={n} (too few) | — |"
    h = g["held"].mean(); lb = wilson(int(g["held"].sum()), n)
    return f"| {label} | n={n:,} | {100*h:.1f}% (LB {100*lb:.1f}%) |"


# ── Test 1: Round archetype ─────────────────────────────────────────────────────
def test_archetype(df, L):
    rows = []
    for mid, g in df.sort_values("seconds_left", ascending=False).groupby("market_id"):
        dist = g["distance"].to_numpy(float)
        horizon = int(g["horizon"].iloc[0])
        max_abs = float(np.abs(dist).max())
        final = float(dist[-1])                                   # last checkpoint (min seconds_left), causal
        nz = np.sign(dist[np.sign(dist) != 0])
        sign_changes = int((np.diff(nz) != 0).sum()) if nz.size > 1 else 0
        thr = 30.0 if horizon == 5 else 60.0                     # "barely moved" $ threshold
        if max_abs < thr:
            arch = "QUIET"
        elif sign_changes >= 1:
            arch = "CHOP"
        elif abs(final) >= 0.7 * max_abs:
            arch = "TREND"
        else:
            arch = "ACTIVE"
        held = int(g["up_won"].iloc[0] == 1) if final > 0 else int(g["up_won"].iloc[0] == 0)
        rows.append((horizon, arch, held))
    r = pd.DataFrame(rows, columns=["horizon", "arch", "held"])
    L += ["## Test 1 — Round archetype (#14)",
          "Round-level. Archetype from the BTC distance trajectory (causal, up to the last checkpoint). "
          "'Hold' = the late leader finished ahead. Useful IF archetypes separate hold rates.", ""]
    for h in (5, 15):
        sub = r[r["horizon"] == h]
        L += [f"### {h}m  (n={len(sub):,} rounds)", "| archetype | share | late-leader hold |", "|---|---|---|"]
        for a in ("QUIET", "TREND", "ACTIVE", "CHOP"):
            ga = sub[sub["arch"] == a]
            share = f"{100*len(ga)/max(1,len(sub)):.0f}%"
            L.append(hline(ga, a).replace("| n=", f"| {share} | n=").replace(" | ", " | ", 1))
        # explicit: TREND vs CHOP separation
        tr, ch = sub[sub["arch"] == "TREND"], sub[sub["arch"] == "CHOP"]
        if len(tr) >= 30 and len(ch) >= 30:
            sep = 100 * (tr["held"].mean() - ch["held"].mean())
            L.append(f"\n**TREND − CHOP hold gap: {sep:+.1f}pp** "
                     f"({'archetype separates outcomes → useful' if abs(sep) >= 5 else 'weak separation'}).\n")
    return r


# ── Test 2: Entry-timing quality ────────────────────────────────────────────────
def test_timing(df, L):
    L += ["## Test 2 — Entry-timing quality (#2)",
          "Snapshot-level (one entry per round per checkpoint). Leader-hold rate by seconds-left. "
          "Does holding improve late? Does a bigger lead (≥$20) help at each time?", ""]
    for h in (5, 15):
        sub = df[df["horizon"] == h]
        L += [f"### {h}m", "| seconds left | all leaders | leaders ≥ $20 |", "|---|---|---|"]
        for s in sorted(sub["seconds_left"].unique(), reverse=True):
            g = sub[sub["seconds_left"] == s]
            gd = g[g["distance"].abs() >= 20]
            def cell(x):
                n = len(x)
                if n < 30:
                    return f"n={n} (few)"
                return f"{100*x['held'].mean():.1f}% (LB {100*wilson(int(x['held'].sum()),n):.0f}%, n={n:,})"
            L.append(f"| {int(s)}s | {cell(g)} | {cell(gd)} |")
        L.append("")


# ── Test 3: Cross-market consistency ────────────────────────────────────────────
def test_crossmarket(df, L):
    m5 = df[df["horizon"] == 5][["abs_time", "distance", "held"]].sort_values("abs_time")
    m15 = df[df["horizon"] == 15][["abs_time", "distance"]].sort_values("abs_time").rename(columns={"distance": "d15"})
    L += ["## Test 3 — Cross-market 5m/15m consistency (#23)",
          "Each 5m snapshot matched to the nearest-in-time 15m snapshot (±90s). AGREE = both lean the same "
          "side. Hypothesis: agreement = more stable = the 5m leader holds more often than when dislocated.", ""]
    if len(m5) < 100 or len(m15) < 100:
        L += ["Too few paired snapshots.", ""]
        return
    j = pd.merge_asof(m5, m15, on="abs_time", direction="nearest", tolerance=90).dropna(subset=["d15"])
    j["agree"] = np.sign(j["distance"]) == np.sign(j["d15"])
    L += [f"Matched 5m snapshots: {len(j):,} (of {len(m5):,}).",
          "| 5m vs 15m lean | n | 5m leader hold |", "|---|---|---|"]
    for lbl, g in (("AGREE (consistent)", j[j["agree"]]), ("DISAGREE (dislocated)", j[~j["agree"]])):
        L.append(hline(g, lbl))   # direct 3-col row: | label | n | rate (LB) | -- no string surgery
    ag, dis = j[j["agree"]], j[~j["agree"]]
    if len(ag) >= 30 and len(dis) >= 30:
        gap = 100 * (ag["held"].mean() - dis["held"].mean())
        L.append(f"\n**AGREE − DISAGREE hold gap: {gap:+.1f}pp** "
                 f"({'consistency is a real stability signal' if gap >= 5 else 'weak / no separation'}).\n")


def main():
    if not os.path.exists(SNAPS):
        print(f"missing {SNAPS}"); return
    df = load()
    L = [f"# HF BTC-Path State Tests — {date.today().isoformat()}", "",
         f"Three existing-data (Category-A) tests on {df['market_id'].nunique():,} HF March rounds "
         f"({len(df):,} snapshots). BTC distance/vol + settlement only — **no leader price**, so free of the "
         "trade-price/latency confound. Descriptive first read; not causal-predictive, not proof.", ""]
    test_archetype(df, L)
    test_timing(df, L)
    test_crossmarket(df, L)
    L += ["## Caveats", "- HF March-only; leader-had-trades subset; ~5.9k rounds. Not forward-validated.",
          "- Archetype/leader use the last checkpoint (causal), but this is characterization, not a live head.",
          "- Real betting still requires live ask/fill/edge-duration (Category B) — these tests inform UX/risk framing only."]
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(str(x) for x in L))
    print("\n".join(str(x) for x in L[2:]))
    print(f"\nWrote {OUT_MD}")


if __name__ == "__main__":
    main()
