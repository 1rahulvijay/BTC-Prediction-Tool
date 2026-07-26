"""PHASE 2 -- data-quality validation of an immutable Polymarket L2 research snapshot.

Canonical blueprint (2026-07-25), Phase 2: before ANY executable-surface research is built,
prove the snapshot can actually support it. Per the contract: "Abort research when required
execution fields are incomplete."

Read-only. Runs against a SNAPSHOT copy, never the live writer DB. Emits an ASCII report plus
a machine-readable JSON verdict so the decision to build Phase 3 is evidence-based, not
optimistic. Nothing here computes a strategy result -- it only measures whether the data
could support one.

Usage:
    python backend/research/validate_l2_snapshot.py [--db PATH] [--out DIR]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import duckdb

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DB = os.path.join(ROOT, "data", "research_snapshots",
                          "polymarket_l2_2026-07-25.duckdb")
DEFAULT_OUT = os.path.join(ROOT, "data", "research", "l2_snapshot_validation")

# Pre-declared minimum requirements (from the Promotion Contract). These are NOT lowered to
# fit the data: if the snapshot misses them, the verdict is "insufficient", not "adjusted".
GATE_ROUNDS = 500
GATE_WEEKS = 8


def _ts_scale(sample: float) -> float:
    """Return the divisor turning a raw timestamp into seconds (handles s / ms / ns)."""
    if sample is None:
        return 1.0
    v = abs(float(sample))
    if v > 1e17:
        return 1e9        # nanoseconds
    if v > 1e11:
        return 1e3        # milliseconds
    return 1.0            # seconds


def _fmt(ts: float, scale: float) -> str:
    try:
        return datetime.fromtimestamp(float(ts) / scale, timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "?"


def _q1(con, sql, params=None):
    row = con.execute(sql, params or []).fetchone()
    return row[0] if row else None


def validate(db_path: str) -> dict:
    con = duckdb.connect(db_path, read_only=True)
    rep: dict = {"db": db_path, "generated_utc": datetime.now(timezone.utc).isoformat(),
                 "gates": {"min_rounds": GATE_ROUNDS, "min_weeks": GATE_WEEKS}}

    # ---- market inventory + true calendar span -------------------------------------------
    scale = _ts_scale(_q1(con, "SELECT MAX(start_ts) FROM pm_l2_markets"))
    rep["timestamp_scale_divisor"] = scale
    markets = con.execute(f"""
        SELECT horizon,
               COUNT(DISTINCT condition_id) AS rounds,
               COUNT(*)                     AS assets,
               MIN(start_ts) AS lo, MAX(start_ts) AS hi,
               COUNT(DISTINCT date_trunc('week',
                     to_timestamp(start_ts / {scale}))) AS weeks,
               COUNT(DISTINCT date_trunc('day',
                     to_timestamp(start_ts / {scale}))) AS days
        FROM pm_l2_markets GROUP BY horizon ORDER BY horizon
    """).fetchall()
    rep["markets"] = [
        {"horizon": h, "rounds": r, "assets": a, "first": _fmt(lo, scale),
         "last": _fmt(hi, scale), "weeks": w, "days": d}
        for h, r, a, lo, hi, w, d in markets
    ]

    # ---- executable coverage: rounds carrying usable two-sided quotes ---------------------
    usable = con.execute("""
        WITH per_round AS (
          SELECT m.horizon, m.condition_id,
                 COUNT(*)                                              AS quotes,
                 SUM(CASE WHEN b.best_ask > b.best_bid THEN 1 ELSE 0 END) AS ok_quotes,
                 SUM(CASE WHEN b.best_ask <= b.best_bid THEN 1 ELSE 0 END) AS crossed,
                 SUM(CASE WHEN b.best_ask_size IS NULL OR b.best_ask_size <= 0
                          THEN 1 ELSE 0 END)                            AS no_ask_size,
                 MIN(b.spread) AS min_spread, MAX(b.spread) AS max_spread
          FROM pm_l2_book_summaries b JOIN pm_l2_markets m USING (asset_id)
          WHERE b.valid AND b.synchronized
            AND b.best_bid IS NOT NULL AND b.best_ask IS NOT NULL
          GROUP BY 1, 2)
        SELECT horizon,
               COUNT(*)                                        AS rounds_with_quotes,
               COUNT(*) FILTER (WHERE ok_quotes >= 30)          AS rounds_ge30_quotes,
               SUM(quotes)                                      AS quotes,
               SUM(crossed)                                     AS crossed_quotes,
               SUM(no_ask_size)                                 AS quotes_without_ask_size,
               ROUND(AVG(quotes), 1)                            AS avg_quotes_per_round
        FROM per_round GROUP BY horizon ORDER BY horizon
    """).fetchall()
    rep["executable_coverage"] = [
        {"horizon": h, "rounds_with_quotes": rq, "rounds_ge30_quotes": r30,
         "quotes": q, "crossed_quotes": cr, "quotes_without_ask_size": nas,
         "avg_quotes_per_round": avg}
        for h, rq, r30, q, cr, nas, avg in usable
    ]

    # ---- ladder depth: can a size-aware VWAP be reconstructed at all? ---------------------
    lvl = con.execute("""
        SELECT m.horizon, l.side,
               COUNT(*) AS level_rows,
               COUNT(DISTINCT m.condition_id) AS rounds,
               ROUND(AVG(l.size), 3) AS avg_size,
               ROUND(MEDIAN(l.size), 3) AS median_size
        FROM pm_l2_book_levels l JOIN pm_l2_markets m USING (asset_id)
        GROUP BY 1, 2 ORDER BY 1, 2
    """).fetchall()
    rep["ladder_depth"] = [
        {"horizon": h, "side": s, "level_rows": n, "rounds": r,
         "avg_size": a, "median_size": md} for h, s, n, r, a, md in lvl
    ]
    rep["max_levels_seen"] = _q1(con, "SELECT MAX(level_index) + 1 FROM pm_l2_book_levels")

    # ---- fee availability (needed for exact post-cost accounting) -------------------------
    fee_rows = _q1(con, "SELECT COUNT(*) FROM pm_l2_trades WHERE fee_rate_bps IS NOT NULL")
    fee_total = _q1(con, "SELECT COUNT(*) FROM pm_l2_trades")
    rep["fees"] = {
        "trades": fee_total, "trades_with_fee_rate": fee_rows,
        "pct": round(100.0 * fee_rows / fee_total, 2) if fee_total else 0.0,
        "distinct_fee_rates": [r[0] for r in con.execute(
            "SELECT DISTINCT fee_rate_bps FROM pm_l2_trades "
            "WHERE fee_rate_bps IS NOT NULL ORDER BY 1 LIMIT 10").fetchall()],
    }

    # ---- integrity: monotonicity, duplicates, invalid books ------------------------------
    rep["integrity"] = {
        "summaries": _q1(con, "SELECT COUNT(*) FROM pm_l2_book_summaries"),
        "invalid_books": _q1(con, "SELECT COUNT(*) FROM pm_l2_book_summaries WHERE NOT valid"),
        "unsynchronized": _q1(con,
            "SELECT COUNT(*) FROM pm_l2_book_summaries WHERE NOT synchronized"),
        "duplicate_seq": _q1(con, """
            SELECT COUNT(*) FROM (SELECT seq FROM pm_l2_book_summaries
                                  GROUP BY seq HAVING COUNT(*) > 1)"""),
        "recv_ts_non_monotonic": _q1(con, """
            SELECT COUNT(*) FROM (
              SELECT recv_ts_ns, LAG(recv_ts_ns) OVER (ORDER BY seq) AS prev
              FROM pm_l2_book_summaries) WHERE prev IS NOT NULL AND recv_ts_ns < prev"""),
        "unapplied_level_updates": _q1(con,
            "SELECT COUNT(*) FROM pm_l2_level_updates WHERE NOT applied"),
        "orphan_assets": _q1(con, """
            SELECT COUNT(DISTINCT b.asset_id) FROM pm_l2_book_summaries b
            LEFT JOIN pm_l2_markets m USING (asset_id) WHERE m.asset_id IS NULL"""),
    }

    # ---- 5m / 15m simultaneity (gates the cross-horizon test) ----------------------------
    rep["cross_horizon_overlap_rounds"] = _q1(con, f"""
        WITH r AS (SELECT DISTINCT horizon, condition_id,
                          start_ts / {scale} AS s, end_ts / {scale} AS e
                   FROM pm_l2_markets)
        SELECT COUNT(*) FROM r a JOIN r b
          ON a.horizon = 15 AND b.horizon = 5 AND b.s < a.e AND b.e > a.s
    """)

    # ---- settlement join (outcome truth for any hold-to-settle policy) --------------------
    sett = os.path.join(ROOT, "data", "pm_export_settlements.parquet")
    if os.path.exists(sett):
        rep["settlement_join"] = {
            "settlements": _q1(con, f"SELECT COUNT(*) FROM read_parquet('{sett}')"),
            "joined_rounds": _q1(con, f"""
                SELECT COUNT(DISTINCT m.condition_id)
                FROM pm_l2_markets m
                JOIN read_parquet('{sett}') s
                  ON m.horizon = s.horizon
                 AND abs(m.start_ts / {scale} - s.anchor_ts) < 90"""),
        }
    else:
        rep["settlement_join"] = {"settlements": 0, "joined_rounds": 0}

    con.close()

    # ---- verdict against the UNLOWERED pre-declared gates ---------------------------------
    verdict = {}
    cov = {c["horizon"]: c for c in rep["executable_coverage"]}
    for m in rep["markets"]:
        h = m["horizon"]
        rounds = cov.get(h, {}).get("rounds_ge30_quotes", 0)
        verdict[f"{h}m"] = {
            "usable_rounds": rounds, "weeks": m["weeks"],
            "rounds_gate": rounds >= GATE_ROUNDS, "weeks_gate": m["weeks"] >= GATE_WEEKS,
            "rounds_shortfall_x": round(GATE_ROUNDS / rounds, 1) if rounds else None,
            "weeks_shortfall_x": round(GATE_WEEKS / m["weeks"], 1) if m["weeks"] else None,
            "promotable": rounds >= GATE_ROUNDS and m["weeks"] >= GATE_WEEKS,
        }
    rep["verdict"] = verdict
    rep["research_status"] = (
        "PROMOTION-CAPABLE" if any(v["promotable"] for v in verdict.values())
        else "PILOT-ONLY (mechanics/feasibility; NOT promotable)")
    return rep


def render(rep: dict) -> str:
    L = []
    L.append("=" * 78)
    L.append("PHASE 2 -- L2 SNAPSHOT DATA-QUALITY REPORT")
    L.append(f"snapshot : {os.path.basename(rep['db'])}")
    L.append(f"generated: {rep['generated_utc']}")
    L.append("=" * 78)

    L.append("\n[1] MARKET INVENTORY")
    for m in rep["markets"]:
        L.append(f"  {m['horizon']:>4}m  rounds={m['rounds']:<6} assets={m['assets']:<6}"
                 f" {m['first']} -> {m['last']}  days={m['days']} weeks={m['weeks']}")

    L.append("\n[2] EXECUTABLE QUOTE COVERAGE (valid + synchronized + two-sided)")
    for c in rep["executable_coverage"]:
        L.append(f"  {c['horizon']:>4}m  rounds_with_quotes={c['rounds_with_quotes']:<6}"
                 f" rounds>=30q={c['rounds_ge30_quotes']:<6}"
                 f" quotes={c['quotes']:,}  avg/round={c['avg_quotes_per_round']}")
        L.append(f"         crossed={c['crossed_quotes']:,}"
                 f"  missing_ask_size={c['quotes_without_ask_size']:,}")

    L.append(f"\n[3] LADDER DEPTH (max levels seen: {rep['max_levels_seen']})")
    for d in rep["ladder_depth"]:
        L.append(f"  {d['horizon']:>4}m {str(d['side']):<5} rows={d['level_rows']:,}"
                 f" rounds={d['rounds']:<6} avg_size={d['avg_size']} med={d['median_size']}")

    f = rep["fees"]
    L.append(f"\n[4] FEE AVAILABILITY  trades={f['trades']:,}"
             f" with_fee_rate={f['trades_with_fee_rate']:,} ({f['pct']}%)"
             f"  rates={f['distinct_fee_rates']}")

    L.append("\n[5] INTEGRITY")
    for k, v in rep["integrity"].items():
        L.append(f"  {k:<28} {v:,}" if isinstance(v, int) else f"  {k:<28} {v}")

    L.append(f"\n[6] CROSS-HORIZON  overlapping 15m/5m round pairs:"
             f" {rep['cross_horizon_overlap_rounds']:,}")
    s = rep["settlement_join"]
    L.append(f"[7] SETTLEMENT JOIN  settlements={s['settlements']:,}"
             f"  joined_rounds={s['joined_rounds']:,}")

    L.append("\n" + "=" * 78)
    L.append("VERDICT vs PRE-DECLARED GATES "
             f"(>= {rep['gates']['min_rounds']} rounds AND >= {rep['gates']['min_weeks']} weeks)")
    L.append("=" * 78)
    for hz, v in rep["verdict"].items():
        r_tag = "PASS" if v["rounds_gate"] else f"FAIL {v['rounds_shortfall_x']}x short"
        w_tag = "PASS" if v["weeks_gate"] else f"FAIL {v['weeks_shortfall_x']}x short"
        L.append(f"  {hz:>5}: usable_rounds={v['usable_rounds']:<6}({r_tag})"
                 f"   weeks={v['weeks']:<3}({w_tag})")
    L.append(f"\n  RESEARCH STATUS: {rep['research_status']}")
    L.append("  Gates are NOT lowered to fit the data. A shortfall means underpowered,")
    L.append("  not 'adjusted'. Pilot results may KILL a hypothesis; they may never promote one.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args()
    if not os.path.exists(args.db):
        print(f"ERROR: snapshot not found: {args.db}")
        return 1
    rep = validate(args.db)
    text = render(rep)
    print(text)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "data_quality_report.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2)
    with open(os.path.join(args.out, "data_quality_report.txt"), "w", encoding="utf-8") as fh:
        fh.write(text + "\n")
    print(f"\nwrote -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
