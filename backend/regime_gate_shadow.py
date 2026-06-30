"""
regime_gate_shadow.py — SHADOW monitor for a regime-selection gate (read-only, no live wiring).
================================================================================================
The DuckDB metrics analysis (DUCKDB_METRICS_ANALYSIS_2026-06-21.md §F) found the ONE statistically
significant directional lever is regime selection: on the regime-era window (regime populated, ~06-18+),
acting only in RANGE/LOW_VOLATILITY hits 59.8% (Wilson-LB 53.5%) vs a 52.6% baseline, and even just
avoiding TRENDING_UP keeps 70% coverage at 55.2% (LB 50.5%).

This script does NOT change any live decision. `regime` and `hit` are already logged on every
`price_to_beat` round, so it simply replays candidate gate policies over the logged rounds and reports
coverage + accuracy + Wilson lower bound — overall, on a recent window, per-horizon, and per-day. Re-run it
any time; it automatically includes newly-settled rounds, accumulating the forward evidence we need before
the gate is ever wired for real. It also appends one headline row per run to
`data/regime_gate_shadow_log.csv` so drift is visible over time.

A policy is only worth promoting when its **Wilson-LB stays > 50%** as n grows.

Usage:  python backend/regime_gate_shadow.py [--recent 250]
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from datetime import date, datetime, timezone

import duckdb

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
DB = os.path.join(DATA, "analytics.duckdb")
OUT = os.path.join(ROOT, "docs", "active", f"REGIME_GATE_SHADOW_{date.today().isoformat()}.md")
LOG = os.path.join(DATA, "regime_gate_shadow_log.csv")

# Candidate gate policies: name -> set of regimes to ACT on (act = take the directional call).
# "baseline" acts on everything in the regime-era window.
POLICIES = {
    "baseline (act on all)":            None,
    "prefer RANGE+LOW_VOLATILITY":      {"RANGE", "LOW_VOLATILITY"},
    "avoid TRENDING_UP":                {"RANGE", "LOW_VOLATILITY", "TRENDING_DOWN", "HIGH_VOLATILITY"},
    "avoid TRENDING_UP + HIGH_VOL":     {"RANGE", "LOW_VOLATILITY", "TRENDING_DOWN"},
}


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    d = 1 + z * z / n
    cen = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (100 * (cen - half), 100 * (cen + half))


def score(rows, act_regimes):
    """rows = list of (regime, hit_bool). Returns (n_act, k_act, acc, lb)."""
    sel = [h for (rg, h) in rows if (act_regimes is None or rg in act_regimes)]
    n = len(sel); k = sum(1 for h in sel if h)
    acc = 100 * k / n if n else 0.0
    lb = wilson(k, n)[0]
    return n, k, acc, lb


def md_table(rows, headers):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join("–" if v is None else (f"{v:.1f}" if isinstance(v, float) else str(v)) for v in r) + " |")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent", type=int, default=250, help="recent-window size (most recent N rounds)")
    a = ap.parse_args()
    if not os.path.exists(DB):
        print("no analytics.duckdb"); return
    c = duckdb.connect(DB, read_only=True)

    # regime-era = regime actually populated (excludes the pre-wiring UNKNOWN backlog). Directional+resolved.
    base_where = ("our_direction IN ('UP','DOWN') AND resolved "
                  "AND regime IS NOT NULL AND regime <> 'UNKNOWN'")
    allrows = c.execute(f"""
        SELECT timestamp, horizon, regime, CASE WHEN hit THEN 1 ELSE 0 END
        FROM price_to_beat WHERE horizon IN (5,15) AND {base_where}
        ORDER BY timestamp""").fetchall()
    c.close()

    if len(allrows) < 30:
        print(f"Only {len(allrows)} regime-era directional rounds — too few; re-run later."); return

    rng = (datetime.fromtimestamp(allrows[0][0] / 1000, timezone.utc).date(),
           datetime.fromtimestamp(allrows[-1][0] / 1000, timezone.utc).date())
    rows_all = [(r[2], bool(r[3])) for r in allrows]
    rows_recent = [(r[2], bool(r[3])) for r in allrows[-a.recent:]]
    rows_5 = [(r[2], bool(r[3])) for r in allrows if r[1] == 5]
    rows_15 = [(r[2], bool(r[3])) for r in allrows if r[1] == 15]

    L = [f"# Regime-Gate Shadow Monitor — {date.today().isoformat()}", "",
         f"Read-only replay over **{len(allrows)} regime-era directional rounds** "
         f"({rng[0]} → {rng[1]}, 5m+15m). No live decision is affected. "
         f"Promote a policy only when its **Wilson-LB stays > 50%** as n grows.", ""]

    def section(title, rows):
        out = [f"\n## {title}  (n={len(rows)})"]
        tbl = []
        for name, regs in POLICIES.items():
            n, k, acc, lb = score(rows, regs)
            cov = 100 * n / len(rows) if rows else 0.0
            flag = "✅" if (regs is not None and lb > 50) else ("—" if regs is None else "⚠️")
            tbl.append((name, n, cov, acc, lb, flag))
        out.append(md_table(tbl, ["policy", "acts", "coverage %", "acc %", "Wilson-LB", "LB>50?"]))
        return "\n".join(out)

    L.append(section("All regime-era rounds", rows_all))
    L.append(section(f"Recent {a.recent} rounds (drift watch)", rows_recent))
    L.append(section("5m only", rows_5))
    L.append(section("15m only", rows_15))

    # per-regime reference
    from collections import defaultdict
    agg = defaultdict(lambda: [0, 0])
    for rg, h in rows_all:
        agg[rg][0] += 1; agg[rg][1] += int(h)
    ref = []
    for rg, (n, k) in sorted(agg.items(), key=lambda kv: -(kv[1][1] / kv[1][0] if kv[1][0] else 0)):
        ref.append((rg, n, 100 * k / n, wilson(k, n)[0]))
    L.append("\n## Per-regime reference (regime-era)")
    L.append(md_table(ref, ["regime", "n", "acc %", "Wilson-LB"]))

    # append headline (prefer-RANGE policy) to the drift log
    n, k, acc, lb = score(rows_all, POLICIES["prefer RANGE+LOW_VOLATILITY"])
    nb, kb, accb, lbb = score(rows_all, None)
    newfile = not os.path.exists(LOG)
    with open(LOG, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if newfile:
            w.writerow(["run_utc", "n_total", "baseline_acc", "prefer_n", "prefer_cov_pct",
                        "prefer_acc", "prefer_wilson_lb"])
        w.writerow([datetime.now(timezone.utc).isoformat(timespec="seconds"), len(rows_all),
                    round(accb, 2), n, round(100 * n / len(rows_all), 1), round(acc, 2), round(lb, 2)])
    L.append(f"\n_Appended headline to `{os.path.relpath(LOG, ROOT)}` (prefer-RANGE policy: "
             f"n={n}, acc={acc:.1f}%, Wilson-LB={lb:.1f}%). Re-run to extend the drift series._")

    L.append("\n## Read\n- A ✅ policy has Wilson-LB > 50% — its edge survives sampling error **at this n**.\n"
             "- Watch the **recent-window** section: if a policy's LB there drops below 50%, the edge is "
             "decaying — do not promote.\n- This stays a shadow monitor until you explicitly approve wiring a "
             "regime gate into the live decision path.")

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
