"""
analyze_duckdb_metrics.py — full accuracy/precision/signal breakdown from analytics.duckdb.
=========================================================================================
Reports, focused on the 5m & 15m horizons:
  A. ENSEMBLE action log  (predictions_5m / predictions_15m): by day, by model_version,
     directional accuracy, per-class precision, actionable-only accuracy, signal/conviction mix.
  B. PRICE-TO-BEAT tracker (price_to_beat): by day, by direction/regime/confluence — the clean
     UP/DOWN hit rate.
  C. INDIVIDUAL models    (model_predictions, 8 models): per-model accuracy + precision at 5m/15m,
     model ranking, abstain rate.

Metric definitions (printed in the report):
  • Accuracy  = correct directional calls / all directional (UP/DOWN) calls.
  • Precision(UP)   = P(actual UP   | predicted UP);   Precision(DOWN) = P(actual DOWN | predicted DOWN).
  • Actionable accuracy = accuracy on rows the app flagged actionable / high-conviction (what matters).
  NEUTRAL/ABSTAIN calls are excluded from directional accuracy and counted separately as abstention.

Ensemble `hit` mixes "correctly neutral" with directional, so ensemble directional correctness is
computed from the signed `actual_move`. price_to_beat & model_predictions use their own pure-directional
`hit`. Read-only; safe to run while the app is live.

Usage:  python backend/analyze_duckdb_metrics.py
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime

import duckdb

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
DB = os.path.join(DATA, "analytics.duckdb")
OUT = os.path.join(ROOT, "docs", "active", f"DUCKDB_METRICS_ANALYSIS_{date.today().isoformat()}.md")
HZ = (5, 15)
DAY5 = "CAST(to_timestamp(timestamp/1000) AS DATE)"


def md_table(rows, headers):
    """Build a GitHub markdown table from a list of row-tuples."""
    def fmt(v):
        if v is None:
            return "–"
        if isinstance(v, float):
            return f"{v:.1f}"
        return str(v)
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join(fmt(v) for v in r) + " |")
    return "\n".join(out)


def main():
    if not os.path.exists(DB):
        print("no analytics.duckdb"); return
    c = duckdb.connect(DB, read_only=True)
    L = [f"# DuckDB Metrics Analysis — {date.today().isoformat()}", ""]

    rng = c.execute(f"SELECT min({DAY5}), max({DAY5}) FROM price_to_beat").fetchone()
    L.append(f"Window **{rng[0]} → {rng[1]}**. Horizons: **5m & 15m**. "
             "Accuracy = correct/all directional calls; Precision(UP/DOWN) = per-class hit; "
             "Actionable accuracy = accuracy on app-flagged actionable rows; NEUTRAL/ABSTAIN counted "
             "separately. Ensemble directional correctness is from signed `actual_move`; the tracker and "
             "individual models use their pure-directional `hit`.")
    L.append("")

    # ─────────────── A. ENSEMBLE (predictions_5m / predictions_15m) ───────────────
    L.append("## A. Ensemble action log — `predictions_5m` / `predictions_15m`")
    # directional-correct expr from signed actual_move
    DIROK = ("CASE WHEN raw_direction='UP' AND actual_move>0 THEN 1 "
             "WHEN raw_direction='DOWN' AND actual_move<0 THEN 1 "
             "WHEN raw_direction IN ('UP','DOWN') THEN 0 END")
    rows = []
    for h in HZ:
        t = f"predictions_{h}m"
        q = c.execute(f"""
            SELECT count(*) total,
                   sum(CASE WHEN resolved THEN 1 ELSE 0 END) resolved,
                   sum(CASE WHEN raw_direction IN ('UP','DOWN') AND resolved THEN 1 ELSE 0 END) dir_calls,
                   avg(CASE WHEN raw_direction IN ('UP','DOWN') AND resolved THEN {DIROK} END)*100 dir_acc,
                   sum(CASE WHEN raw_direction='NEUTRAL' THEN 1 ELSE 0 END) neutral,
                   sum(CASE WHEN actionable=1 THEN 1 ELSE 0 END) act,
                   avg(CASE WHEN actionable=1 AND raw_direction IN ('UP','DOWN') AND resolved THEN {DIROK} END)*100 act_acc,
                   avg(CASE WHEN actionable=1 THEN conviction END) avg_conv
            FROM {t}""").fetchone()
        rows.append((f"{h}m", q[0], q[1], q[2], q[3], q[4], q[5], q[6], q[7]))
    L.append("\n### A1 — Overall by horizon")
    L.append(md_table(rows, ["hz", "total", "resolved", "dir calls", "dir acc %", "neutral",
                             "actionable", "act acc %", "avg conv"]))

    L.append("\n### A2 — Directional precision per class (UP vs DOWN)")
    rows = []
    for h in HZ:
        t = f"predictions_{h}m"
        for d in ("UP", "DOWN"):
            ok = "actual_move>0" if d == "UP" else "actual_move<0"
            q = c.execute(f"""SELECT count(*), avg(CASE WHEN {ok} THEN 1.0 ELSE 0 END)*100
                              FROM {t} WHERE raw_direction='{d}' AND resolved""").fetchone()
            rows.append((f"{h}m", d, q[0], q[1]))
    L.append(md_table(rows, ["hz", "predicted", "support", "precision %"]))

    L.append("\n### A3 — By day × horizon (directional accuracy)")
    rows = []
    for h in HZ:
        t = f"predictions_{h}m"
        for r in c.execute(f"""
            SELECT {DAY5} d, count(*) n,
                   sum(CASE WHEN raw_direction IN ('UP','DOWN') AND resolved THEN 1 ELSE 0 END) dc,
                   avg(CASE WHEN raw_direction IN ('UP','DOWN') AND resolved THEN {DIROK} END)*100 acc,
                   sum(CASE WHEN actionable=1 THEN 1 ELSE 0 END) act
            FROM {t} GROUP BY 1 ORDER BY 1""").fetchall():
            rows.append((f"{h}m", str(r[0]), r[1], r[2], r[3], r[4]))
    L.append(md_table(rows, ["hz", "day", "n", "dir calls", "dir acc %", "actionable"]))

    L.append("\n### A4 — By model_version × horizon (≥20 resolved directional calls)")
    rows = []
    for h in HZ:
        t = f"predictions_{h}m"
        for r in c.execute(f"""
            SELECT model_version,
                   to_timestamp(CAST(replace(model_version,'bundle_','') AS BIGINT)) bundled,
                   count(*) n,
                   sum(CASE WHEN raw_direction IN ('UP','DOWN') AND resolved THEN 1 ELSE 0 END) dc,
                   avg(CASE WHEN raw_direction IN ('UP','DOWN') AND resolved THEN {DIROK} END)*100 acc,
                   avg(CASE WHEN actionable=1 AND raw_direction IN ('UP','DOWN') AND resolved THEN {DIROK} END)*100 act_acc
            FROM {t} GROUP BY 1,2
            HAVING sum(CASE WHEN raw_direction IN ('UP','DOWN') AND resolved THEN 1 ELSE 0 END) >= 20
            ORDER BY acc DESC NULLS LAST""").fetchall():
            bundled = r[1].strftime("%m-%d %H:%M") if isinstance(r[1], datetime) else str(r[1])
            rows.append((f"{h}m", r[0].replace("bundle_", ""), bundled, r[2], r[3], r[4], r[5]))
    L.append(md_table(rows, ["hz", "version", "bundled", "n", "dir calls", "dir acc %", "act acc %"]))
    L.append("\n_Per-version samples are small (frequent retrains) — read as spread, not ranking._")

    # ─────────────── B. PRICE-TO-BEAT tracker ───────────────
    L.append("\n## B. Price-to-Beat tracker — `price_to_beat` (pure UP/DOWN hit)")
    rows = []
    for h in HZ:
        q = c.execute(f"""
            SELECT count(*) total, sum(CASE WHEN resolved THEN 1 ELSE 0 END) resolved,
                   sum(CASE WHEN our_direction IN ('UP','DOWN') AND resolved THEN 1 ELSE 0 END) dir,
                   avg(CASE WHEN our_direction IN ('UP','DOWN') AND resolved THEN CASE WHEN hit THEN 1.0 ELSE 0 END END)*100 acc,
                   avg(CASE WHEN our_direction='UP' AND resolved THEN CASE WHEN hit THEN 1.0 ELSE 0 END END)*100 up_p,
                   avg(CASE WHEN our_direction='DOWN' AND resolved THEN CASE WHEN hit THEN 1.0 ELSE 0 END END)*100 dn_p,
                   sum(CASE WHEN our_direction='NEUTRAL' THEN 1 ELSE 0 END) neu
            FROM price_to_beat WHERE horizon={h}""").fetchone()
        rows.append((f"{h}m", q[0], q[1], q[2], q[3], q[4], q[5], q[6]))
    L.append("\n### B1 — Overall by horizon")
    L.append(md_table(rows, ["hz", "total", "resolved", "dir calls", "acc %", "UP prec %", "DOWN prec %", "neutral"]))

    L.append("\n### B2 — By day × horizon")
    rows = []
    for h in HZ:
        for r in c.execute(f"""
            SELECT {DAY5} d, count(*) n,
                   sum(CASE WHEN our_direction IN ('UP','DOWN') AND resolved THEN 1 ELSE 0 END) dc,
                   avg(CASE WHEN our_direction IN ('UP','DOWN') AND resolved THEN CASE WHEN hit THEN 1.0 ELSE 0 END END)*100 acc
            FROM price_to_beat WHERE horizon={h} GROUP BY 1 ORDER BY 1""").fetchall():
            rows.append((f"{h}m", str(r[0]), r[1], r[2], r[3]))
    L.append(md_table(rows, ["hz", "day", "n", "dir calls", "acc %"]))

    L.append("\n### B3 — By regime & confluence grade (5m+15m pooled, directional)")
    for col in ("regime", "confluence_grade", "lean_source"):
        rows = []
        for r in c.execute(f"""
            SELECT coalesce(CAST({col} AS VARCHAR),'(none)') g,
                   sum(CASE WHEN our_direction IN ('UP','DOWN') AND resolved THEN 1 ELSE 0 END) dc,
                   avg(CASE WHEN our_direction IN ('UP','DOWN') AND resolved THEN CASE WHEN hit THEN 1.0 ELSE 0 END END)*100 acc
            FROM price_to_beat WHERE horizon IN (5,15) GROUP BY 1
            HAVING sum(CASE WHEN our_direction IN ('UP','DOWN') AND resolved THEN 1 ELSE 0 END) >= 25
            ORDER BY acc DESC NULLS LAST""").fetchall():
            rows.append((r[0], r[1], r[2]))
        if rows:
            L.append(f"\n**by {col}:**")
            L.append(md_table(rows, [col, "dir calls", "acc %"]))

    # ─────────────── C. INDIVIDUAL models ───────────────
    L.append("\n## C. Individual models — `model_predictions` (8 base models)")
    L.append("\n### C1 — Per-model accuracy & precision at 5m / 15m")
    rows = []
    for h in HZ:
        for r in c.execute(f"""
            SELECT model,
                   sum(CASE WHEN direction IN ('UP','DOWN') AND resolved AND hit IS NOT NULL THEN 1 ELSE 0 END) dc,
                   avg(CASE WHEN direction IN ('UP','DOWN') AND resolved AND hit IS NOT NULL THEN CASE WHEN hit THEN 1.0 ELSE 0 END END)*100 acc,
                   avg(CASE WHEN direction='UP' AND resolved AND hit IS NOT NULL THEN CASE WHEN hit THEN 1.0 ELSE 0 END END)*100 up_p,
                   avg(CASE WHEN direction='DOWN' AND resolved AND hit IS NOT NULL THEN CASE WHEN hit THEN 1.0 ELSE 0 END END)*100 dn_p,
                   avg(CASE WHEN resolved THEN CASE WHEN direction='NEUTRAL' THEN 1.0 ELSE 0 END END)*100 abst
            FROM model_predictions WHERE horizon={h} GROUP BY 1
            ORDER BY acc DESC NULLS LAST""").fetchall():
            rows.append((f"{h}m", r[0], r[1], r[2], r[3], r[4], r[5]))
    L.append(md_table(rows, ["hz", "model", "dir calls", "acc %", "UP prec %", "DOWN prec %", "abstain %"]))

    L.append("\n### C2 — Model ranking (directional accuracy, 5m & 15m side by side)")
    rank = {}
    for h in HZ:
        for r in c.execute(f"""
            SELECT model,
                   avg(CASE WHEN direction IN ('UP','DOWN') AND resolved AND hit IS NOT NULL THEN CASE WHEN hit THEN 1.0 ELSE 0 END END)*100 acc,
                   sum(CASE WHEN direction IN ('UP','DOWN') AND resolved AND hit IS NOT NULL THEN 1 ELSE 0 END) n
            FROM model_predictions WHERE horizon={h} GROUP BY 1""").fetchall():
            rank.setdefault(r[0], {})[h] = (r[1], r[2])
    rows = []
    for m, d in sorted(rank.items(), key=lambda kv: -(kv[1].get(5, (0,))[0] or 0)):
        a5, n5 = d.get(5, (None, 0)); a15, n15 = d.get(15, (None, 0))
        rows.append((m, a5, n5, a15, n15))
    L.append(md_table(rows, ["model", "5m acc %", "5m n", "15m acc %", "15m n"]))

    L.append("\n### C3 — By day (all models pooled, 5m & 15m)")
    rows = []
    for h in HZ:
        for r in c.execute(f"""
            SELECT {DAY5} d,
                   sum(CASE WHEN direction IN ('UP','DOWN') AND resolved AND hit IS NOT NULL THEN 1 ELSE 0 END) dc,
                   avg(CASE WHEN direction IN ('UP','DOWN') AND resolved AND hit IS NOT NULL THEN CASE WHEN hit THEN 1.0 ELSE 0 END END)*100 acc
            FROM model_predictions WHERE horizon={h} GROUP BY 1 ORDER BY 1""").fetchall():
            rows.append((f"{h}m", str(r[0]), r[1], r[2]))
    L.append(md_table(rows, ["hz", "day", "dir calls", "acc %"]))

    # ─────────────── D. takeaways ───────────────
    L.append("\n## D. Cross-cutting read")
    p2b = {h: c.execute(f"""SELECT avg(CASE WHEN hit THEN 1.0 ELSE 0 END)*100
                            FROM price_to_beat WHERE horizon={h} AND our_direction IN ('UP','DOWN') AND resolved""").fetchone()[0] for h in HZ}
    ens = {h: c.execute(f"""SELECT avg({DIROK})*100 FROM predictions_{h}m WHERE raw_direction IN ('UP','DOWN') AND resolved""").fetchone()[0] for h in HZ}
    best = {}
    for h in HZ:
        r = c.execute(f"""SELECT model, avg(CASE WHEN hit THEN 1.0 ELSE 0 END)*100 a,
                          sum(CASE WHEN direction IN ('UP','DOWN') AND resolved AND hit IS NOT NULL THEN 1 ELSE 0 END) n
                          FROM model_predictions WHERE horizon={h} AND direction IN ('UP','DOWN') AND resolved AND hit IS NOT NULL
                          GROUP BY 1 ORDER BY a DESC LIMIT 1""").fetchone()
        best[h] = r
    L.append(f"- **Price-to-Beat tracker (clean coin-flip baseline):** 5m **{p2b[5]:.1f}%**, 15m **{p2b[15]:.1f}%** directional.")
    L.append(f"- **Ensemble raw_direction:** 5m **{ens[5]:.1f}%**, 15m **{ens[15]:.1f}%** (from signed move).")
    L.append(f"- **Best individual model:** 5m {best[5][0]} **{best[5][1]:.1f}%** (n={best[5][2]}), "
             f"15m {best[15][0]} **{best[15][1]:.1f}%** (n={best[15][2]}).")
    L.append("- Individual models call UP/DOWN **selectively** (high abstain %); their directional accuracy "
             "on the calls they DO make is the number above — compare against the ~50% tracker baseline and "
             "treat any lift as selection, not a generalizable direction edge, until it survives more samples.")

    c.close()
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
