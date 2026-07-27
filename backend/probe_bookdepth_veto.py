"""
probe_bookdepth_veto.py - bookDepth as a SHADOW risk/regime VETO, judged on retained-call quality (not AUC).
==========================================================================================================
bookDepth failed the predictive test (no AUC lift on big-move/big-drop). But a dead-AUC feature can still be a
useful VETO if, at the SAME P(Hold), the model holds LESS often in a thin/vacuum book. This probe tests exactly
that, on REAL data: champion_snapshots (actual served p_hold + the side ahead) x price_to_beat (did it hold) x
Binance bookDepth liquidity regime (joined by ts). The rule (your framing): bookDepth may only reduce confidence
/ widen buffer / veto -- never create a trade. Success = removes more bad calls than good, NOT higher AUC.

Test A: held% + ECE by liquidity regime at P(Hold)>=0.93 / 0.95.
Test B: veto -- P(Hold)>=0.93 baseline vs +not-vacuum -> coverage, held%, Wilson-LB, avoided-bad vs lost-good.
Test C: interaction -- does the vacuum penalty concentrate in high-vol / near-anchor / low-seconds-left subsets?

Usage: python backend/probe_bookdepth_veto.py
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

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
DB = os.path.join(DATA, "analytics.duckdb")
OUT_MD = os.path.join(ROOT, "docs", "active", f"BOOKDEPTH_VETO_PROBE_{date.today().isoformat()}.md")
sys.path.insert(0, HERE)


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n; d = 1 + z * z / n
    return 100 * ((p + z * z / (2 * n)) / d - z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d)


def main():
    import duckdb
    import probe_bookdepth_liquidity as BD
    # 1. real P(Hold) snapshots + realized hold outcome
    try:
        c = duckdb.connect(DB, read_only=True)
    except Exception as e:
        print(f"analytics.duckdb locked — stop the app.\n  {str(e)[:80]}"); return
    snaps = c.execute("""
        SELECT cs.ts, cs.horizon, cs.seconds_left, cs.current_position, cs.p_hold, COALESCE(cs.regime,'?') regime,
               cs.current_move, p.actual_direction
        FROM champion_snapshots cs JOIN price_to_beat p ON p.id = cs.round_id
        WHERE p.resolved AND cs.current_position IN ('UP','DOWN') AND p.actual_direction IN ('UP','DOWN')
          AND cs.p_hold IS NOT NULL AND p.horizon IN (5,15) ORDER BY cs.ts""").df()
    c.close()
    if len(snaps) < 3000:
        print(f"only {len(snaps)} snapshots — too few."); return
    snaps["held"] = (snaps["current_position"].astype(str) == snaps["actual_direction"].astype(str)).astype(int)
    snaps["dist"] = snaps["current_move"].abs()
    smin, smax = int(snaps["ts"].min()), int(snaps["ts"].max())
    end = pd.to_datetime(smax, unit="ms").date()
    span_days = (smax - smin) / 86400000 + 3
    print(f"champion snapshots: {len(snaps):,} over {span_days:.0f}d ending {end}")
    # 2. bookDepth liquidity for the same window (cached from the earlier probe; fetches any missing days)
    wide = BD.load_bookdepth(int(span_days) + 2, end)
    liq = BD.build_liquidity(wide).sort_values("ts_ms").reset_index(drop=True)
    liq = liq[["ts_ms", "vacuum_z", "near_depth", "depth_slope", "imb_0p2", "depth_chg_2m"]]
    m = pd.merge_asof(snaps.sort_values("ts").rename(columns={"ts": "ts_ms"}), liq, on="ts_ms",
                      direction="backward", tolerance=90_000)
    m = m[m["vacuum_z"].notna()].reset_index(drop=True)
    cov = 100 * len(m) / len(snaps)
    if len(m) < 2000:
        print(f"only {len(m)} snapshots joined to bookDepth ({cov:.0f}% cov) — too few (need bookDepth overlap)."); return
    # liquidity regime from the near-book vacuum z-score (thin near book = vacuum)
    m["liq"] = pd.cut(m["vacuum_z"], [-1e9, -1.0, -0.3, 0.5, 1e9], labels=["VACUUM", "THIN", "NORMAL", "DEEP"])
    L = [f"# bookDepth Veto / Regime Probe — {date.today().isoformat()}", "",
         f"Real P(Hold) snapshots ({len(m):,}, {cov:.0f}% joined to bookDepth) x Binance liquidity regime. "
         f"bookDepth may only VETO/haircut — never create a trade. Judged on retained-call quality, not AUC.", ""]

    # ---- Test A: held% + ECE by liquidity regime, at the betting gates ----
    L.append("## Test A — P(Hold) realized by liquidity regime")
    for thr in (0.93, 0.95):
        g = m[m["p_hold"] >= thr]
        L.append(f"\n**P(Hold) ≥ {thr:.2f}** (n={len(g):,}, overall held {100*g['held'].mean():.1f}%):")
        L.append("| liquidity regime | n | held% | Wilson-LB | mean P(Hold) | vs overall |")
        L.append("|---|---|---|---|---|---|")
        base = g["held"].mean()
        for r in ("DEEP", "NORMAL", "THIN", "VACUUM"):
            gr = g[g["liq"] == r]
            if len(gr) < 30:
                continue
            h = gr["held"].mean()
            L.append(f"| {r} | {len(gr):,} | {100*h:.1f} | {wilson(int(gr['held'].sum()),len(gr)):.1f} | "
                     f"{gr['p_hold'].mean():.3f} | {100*(h-base):+.1f}pp |")

    # ---- Test B: the veto ----
    L.append("\n## Test B — veto: P(Hold)≥0.93  vs  +not-VACUUM")
    A = m[m["p_hold"] >= 0.93]
    B = A[A["liq"] != "VACUUM"]
    dropped = A[A["liq"] == "VACUUM"]
    ab, lg = int((dropped["held"] == 0).sum()), int((dropped["held"] == 1).sum())
    L.append(f"- baseline A: {len(A):,} calls, held {100*A['held'].mean():.1f}%, Wilson-LB {wilson(int(A['held'].sum()),len(A)):.1f}")
    L.append(f"- vetoed  B: {len(B):,} calls, held {100*B['held'].mean():.1f}%, Wilson-LB {wilson(int(B['held'].sum()),len(B)):.1f}")
    L.append(f"- veto dropped {len(dropped)} VACUUM calls → **avoided-bad {ab}** vs **lost-good {lg}** → "
             f"net {ab-lg:+d}; coverage {100*len(B)/len(A):.1f}% of A")
    veto_good = ab > lg and len(dropped) >= 20

    # ---- Test C: interaction (where does the VACUUM penalty concentrate?) ----
    L.append("\n## Test C — where the VACUUM penalty concentrates (interaction)")
    A2 = A.copy()
    A2["near_anchor"] = A2["dist"] < A2["dist"].median()
    A2["low_time"] = A2["seconds_left"] < 60
    for name, mask in (("near-anchor (dist<median)", A2["near_anchor"]),
                       ("late (secs_left<60)", A2["low_time"]),
                       ("TREND/VOLATILE regime", A2["regime"].astype(str).str.contains("TREND|VOL", case=False))):
        sub = A2[mask]
        vac = sub[sub["liq"] == "VACUUM"]; oth = sub[sub["liq"] != "VACUUM"]
        if len(vac) >= 20 and len(oth) >= 20:
            pen = 100 * (oth["held"].mean() - vac["held"].mean())
            L.append(f"- {name}: VACUUM held {100*vac['held'].mean():.1f}% vs {100*oth['held'].mean():.1f}% "
                     f"→ penalty {pen:+.1f}pp (n_vac={len(vac)})")

    # ---- Verdict ----
    vac_all = A[A["liq"] == "VACUUM"]["held"].mean() if (A["liq"] == "VACUUM").sum() >= 20 else float("nan")
    oth_all = A[A["liq"] != "VACUUM"]["held"].mean()
    pen_all = 100 * (oth_all - vac_all) if np.isfinite(vac_all) else float("nan")
    L.append("\n## Verdict")
    if veto_good and np.isfinite(pen_all) and pen_all >= 2.0:
        v = (f"USABLE VETO — at P(Hold)≥0.93, VACUUM-book calls hold {pen_all:.1f}pp less; vetoing them removes "
             f"more bad ({ab}) than good ({lg}). Promote to champion_shadow as a confidence haircut/veto (NOT a trade).")
    elif np.isfinite(pen_all) and pen_all >= 1.0:
        v = (f"WEAK — VACUUM penalty {pen_all:+.1f}pp is directionally right but small (avoided {ab} / lost {lg}). "
             f"Keep shadow-logging; re-test with more data before wiring.")
    else:
        v = (f"NO VETO VALUE — VACUUM penalty {pen_all:+.1f}pp / avoided {ab} vs lost {lg}. bookDepth does not "
             f"improve retained-call quality either. Drop it (dead for prediction AND veto at this resolution).")
    L.append(f"**{v}**")
    L.append("\n_Judged on retained-call quality (held% / Wilson-LB / bad-vs-good), never AUC. bookDepth can only "
             "veto/haircut, never create a trade. Research only — shadow first if it passes._")

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(str(x) for x in L))
    print("\n".join(str(x) for x in L[3:]))
    print(f"\nWrote {OUT_MD}")


if __name__ == "__main__":
    main()
