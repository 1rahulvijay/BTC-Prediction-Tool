"""
shadow_report.py — standalone analytics over the SEPARATE execution_layer.duckdb.
=================================================================================
Reads ONLY `data/execution_layer.duckdb` (never the live app DB) and prints:
  • rejection breakdown — which gate blocked the most decisions
  • tier scorecard — per final_tier: N, win%, avg realized PnL (bps), EV at 0/7/14 bps cost
  • selectivity calibration — does a higher selectivity tier actually win more?

This is the "rejection analytics + tier scorecard" item, built as an independent
report. It works whether the shadow data came from the offline replay
(`composed_decision_backtest.py --persist`) or, later, the live-shadow runner.

No retraining, no app, no model — pure read of the separate DB.

Usage:  python backend/decision/shadow_report.py
"""
from __future__ import annotations

import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from decision import shadow_store


def main():
    if not os.path.exists(shadow_store.DB_PATH):
        print(f"No execution-layer DB yet at {shadow_store.DB_PATH}.")
        print("Populate it first: python backend/decision/composed_decision_backtest.py --persist")
        return
    conn = shadow_store.get_conn()
    shadow_store.init_db(conn)

    n_sig = conn.execute("SELECT COUNT(*) FROM shadow_signals").fetchone()[0]
    n_rej = conn.execute("SELECT COUNT(*) FROM rejection_events").fetchone()[0]
    print("=" * 60)
    print(f"EXECUTION-LAYER SHADOW REPORT  ({shadow_store.DB_PATH})")
    print(f"  shadow_signals: {n_sig:,}   rejection_events: {n_rej:,}")
    print("=" * 60)

    print("\nREJECTION / DECISION BREAKDOWN (every bar)")
    tot = sum(c for _, c in shadow_store.rejection_summary(conn=conn)) or 1
    for reason, c in shadow_store.rejection_summary(conn=conn):
        print(f"  {reason:<30} {c:>8}  ({c/tot*100:5.1f}%)")

    print("\nTIER SCORECARD (resolved sided signals)")
    sc = shadow_store.tier_scorecard(conn=conn)
    if not sc:
        print("  (no resolved sided signals yet)")
    else:
        print(f"  {'tier':<6}{'N':>8}{'win%':>8}{'avgPnL':>9}{'EV@0':>8}{'EV@7':>8}{'EV@14':>8}")
        for tier, n, win, avg in sc:
            print(f"  {str(tier):<6}{int(n):>8}{win:>7.1f}%{avg:>+9.2f}{avg:>+8.2f}"
                  f"{avg-7:>+8.2f}{avg-14:>+8.2f}")

    print("\nSELECTIVITY CALIBRATION (does a higher tier win more?)")
    rows = conn.execute("""
        SELECT final_tier,
               COUNT(*) n,
               AVG(CASE WHEN realized_pnl_bps > 0 THEN 1.0 ELSE 0.0 END)*100 win_pct,
               AVG(p_big_move) avg_pbig
        FROM shadow_signals WHERE resolved = TRUE
        GROUP BY final_tier ORDER BY avg_pbig
    """).fetchall()
    for tier, n, win, pbig in rows:
        print(f"  {str(tier):<6} p_big_move~{pbig:.3f}  win {win:5.1f}%  (n={int(n)})")

    conn.close()
    print("\n(Read-only on the separate DB. The live app's analytics.duckdb is untouched.)")


if __name__ == "__main__":
    main()
