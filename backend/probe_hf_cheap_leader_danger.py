"""
probe_hf_cheap_leader_danger.py - is a CHEAP leader mispriced, or cheap because it's genuinely fragile?
======================================================================================================
Splits the leader-price anomaly by FRAGILITY. If cheap leaders win ~65% REGARDLESS of fragility, the
anomaly is robust (CHEAP-VALID). If FRAGILE cheap leaders (small lead vs volatility, easy to flip) win far
LESS than SAFE cheap leaders, then the market was pricing danger correctly for those (CHEAP-DANGEROUS), and
the real mispricing lives only in the SAFE-cheap set. Directly answers operator prediction #2.

Fragility proxy = dist_vol_ratio = abs_distance_pct / vol_60s_pct (the persistence model's own feature).
Low ratio = small lead relative to volatility = fragile. Round-level. Cheap = leader trade-price in 0.42-0.58.

⚠️ TRADE prices, not asks; possibly a Binance-leader-vs-Polymarket-oracle latency effect. Live /book required.

Usage: python backend/probe_hf_cheap_leader_danger.py
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
OUT_MD = os.path.join(ROOT, "docs", "active", f"HF_CHEAP_LEADER_DANGER_{date.today().isoformat()}.md")
PC = "vwap30"


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n; d = 1 + z * z / n
    return (p + z * z / (2 * n)) / d - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d


def prep(raw):
    lead_up = raw["distance"].to_numpy(float) > 0
    up_p = raw["up_" + PC].to_numpy(float); dn_p = raw["down_" + PC].to_numpy(float)
    d = raw.assign(price=np.where(lead_up, up_p, dn_p),
                   won=np.where(lead_up, raw["up_won"].to_numpy(int), 1 - raw["up_won"].to_numpy(int)),
                   dvr=raw["abs_dist_pct"].to_numpy(float) / (raw["vol_60s_pct"].to_numpy(float) + 1e-6))
    d = d[np.isfinite(d["price"]) & (d["seconds_left"] > 0) & (d["distance"].abs() >= 1e-9)]
    return d.sort_values("seconds_left", ascending=False).groupby("market_id", as_index=False).first()  # round-level


def line(g):
    n = len(g)
    if n < 30:
        return f"n={n} (too few)"
    wr = g["won"].mean(); pr = g["price"].mean(); lb = wilson(int(g["won"].sum()), n)
    return f"n={n:,} win={100*wr:.1f}% LB={100*lb:.1f}% price={100*pr:.1f}% gap={100*(wr-pr):+.1f}pp"


def main():
    if not os.path.exists(SNAPS):
        print(f"missing {SNAPS}"); return
    d = prep(pd.read_parquet(SNAPS))
    cheap = d[(d["price"] >= 0.42) & (d["price"] <= 0.58)].copy()      # where the +12pp anomaly lives
    if len(cheap) < 200:
        print("too few cheap-leader rounds."); return
    # fragility terciles on dist_vol_ratio (low = fragile)
    cheap["frag"] = pd.qcut(cheap["dvr"], 3, labels=["FRAGILE (low dist/vol)", "MID", "SAFE (high dist/vol)"], duplicates="drop")
    L = [f"# Cheap-Leader: Valid vs Dangerous — {date.today().isoformat()}", "",
         f"Does the cheap-leader anomaly survive when conditioned on fragility? Round-level, cheap = leader trade-price "
         f"0.42–0.58 (n={len(cheap):,}). Fragility = dist_vol_ratio (small lead vs vol = easy to flip). "
         f"⚠️ Trade prices, not asks; live /book required.", "",
         "## Cheap leaders split by fragility",
         "| bucket | result |", "|---|---|"]
    for f in ("FRAGILE (low dist/vol)", "MID", "SAFE (high dist/vol)"):
        L.append(f"| {f} | {line(cheap[cheap['frag']==f])} |")
    # also by seconds_left (late = more shock exposure) and vol (high = danger)
    L += ["\n## Cheap leaders by other danger axes", "| axis | result |", "|---|---|"]
    L.append(f"| early (secs≥180) | {line(cheap[cheap['seconds_left']>=180])} |")
    L.append(f"| late (secs<120) | {line(cheap[cheap['seconds_left']<120])} |")
    vmed = cheap["vol_60s_pct"].median()
    L.append(f"| low vol (<median) | {line(cheap[cheap['vol_60s_pct']<vmed])} |")
    L.append(f"| high vol (≥median) | {line(cheap[cheap['vol_60s_pct']>=vmed])} |")
    # verdict
    frag = cheap[cheap["frag"] == "FRAGILE (low dist/vol)"]; safe = cheap[cheap["frag"] == "SAFE (high dist/vol)"]
    fg = frag["won"].mean() - frag["price"].mean() if len(frag) >= 30 else float("nan")
    sg = safe["won"].mean() - safe["price"].mean() if len(safe) >= 30 else float("nan")
    L += ["\n## Verdict"]
    if np.isfinite(fg) and np.isfinite(sg):
        if sg - fg > 0.05:
            L.append(f"**PARTLY 'cheap for a reason' — the gap concentrates in SAFE leaders (+{100*sg:.1f}pp) and "
                     f"shrinks for FRAGILE ones (+{100*fg:.1f}pp).** So the market prices *some* of the cheapness as "
                     f"real flip-risk (CHEAP-DANGEROUS). The exploitable mispricing (if any) is the **SAFE-cheap** set — "
                     f"the cheap-VALID signal. A live head should require low fragility before flagging a cheap leader.")
        elif fg > 0.03 and sg > 0.03:
            L.append(f"**ROBUST — cheap leaders are underpriced regardless of fragility** (FRAGILE +{100*fg:.1f}pp, "
                     f"SAFE +{100*sg:.1f}pp). The anomaly is NOT explained by mispriced danger. Stronger case that it "
                     f"is a real (latency or momentum) underpricing — but still trade-price only; live /book decides.")
        else:
            L.append(f"**NO robust cheap-VALID edge** (FRAGILE +{100*fg:.1f}pp, SAFE +{100*sg:.1f}pp).")
    L.append("\n- Decision-support use (once live): classify a cheap leader **CHEAP-VALID** (low fragility, high "
             "dist/vol, book fresh) vs **CHEAP-DANGEROUS** (fragile / high vol / late shock) vs **CHEAP-ILLIQUID** "
             "(bad book). Only CHEAP-VALID + fillable live ask is a candidate. All still PAPER until the recorder proves it.")
    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(str(x) for x in L))
    print("\n".join(str(x) for x in L[2:]))
    print(f"\nWrote {OUT_MD}")


if __name__ == "__main__":
    main()
