"""
grade_scorecard.py - does the A/B/C confluence grade actually STRATIFY sign-truth? (rule #2)
=============================================================================================
A grade is only allowed to be a trust signal / gate if it stratifies: realized A > B > C on
sign-truth, each n>=100, and the TOP grade's Wilson lower bound clears the BOTTOM grade's rate.
The current `_confluence` grade is built from order-flow agreement (proven coin-flip) + regime
(shadow LB<50%), displayed against DIRECTION win-rate (unstratifiable) -> expected to FAIL this.
This tool measures it honestly and is the gate any REBUILT grade must pass before `grade_validated`.

Read-only, era-filtered (architecture_version.pkl mtime). Reads predictions_{h}m from analytics.duckdb;
exits cleanly if the app holds the write-lock (stop the app briefly and rerun -- no /api fallback yet).

Usage:  python backend/research/standalone/grade_scorecard.py        |        python backend/research/standalone/grade_scorecard.py --selftest
"""
from __future__ import annotations

try:
    from . import _bootstrap as _research_bootstrap  # noqa: F401
except ImportError:
    import _bootstrap as _research_bootstrap  # noqa: F401

del _research_bootstrap


import math
import os
import sys

HORIZONS = (5, 15)
GRADES = ("A", "B", "C")
MIN_N = 100   # rule #2: each grade needs n>=100 before the grade can be trusted


def wilson_lb(wins, n, z=1.96):
    if n <= 0:
        return 0.0
    p = wins / n
    return max(0.0, (p + z*z/(2*n) - z*math.sqrt((p*(1-p) + z*z/(4*n))/n)) / (1 + z*z/n))


def stratification(rows):
    """PURE core. rows: list of {grade, won(0/1)}. Returns per-grade stats + a gate verdict."""
    by = {g: [r["won"] for r in rows if r["grade"] == g] for g in GRADES}
    stats = {}
    for g in GRADES:
        w = sum(by[g]); n = len(by[g])
        stats[g] = {"n": n, "rate": (w / n if n else None), "wilson_lb": wilson_lb(w, n)}
    rates = [stats[g]["rate"] for g in GRADES]
    ns = [stats[g]["n"] for g in GRADES]
    monotone = all(r is not None for r in rates) and rates[0] >= rates[1] >= rates[2]
    enough = all(n >= MIN_N for n in ns)
    top_clears_bottom = (rates[0] is not None and rates[2] is not None
                         and stats["A"]["wilson_lb"] > rates[2])
    if not enough:
        verdict = "INSUFFICIENT (need n>=100 per grade)"
    elif monotone and top_clears_bottom:
        verdict = "STRATIFIES -> grade is gate-eligible (set grade_validated)"
    elif monotone:
        verdict = "monotone but A's Wilson-LB does not clear C's rate -> NOT yet trustworthy"
    else:
        verdict = "DOES NOT STRATIFY (inverted/flat) -> keep grade_validated=False"
    return {"stats": stats, "monotone": monotone, "gate_eligible": bool(enough and monotone and top_clears_bottom),
            "verdict": verdict}


def _load(conn, era_ts):
    out = {}
    for h in HORIZONS:
        try:
            r = conn.execute(f"""
                SELECT confluence_grade AS g,
                       CASE WHEN (raw_direction='UP' AND actual_move>0)
                              OR (raw_direction='DOWN' AND actual_move<0) THEN 1 ELSE 0 END AS won
                FROM predictions_{h}m
                WHERE resolved AND raw_direction IN ('UP','DOWN') AND actual_move IS NOT NULL
                  AND confluence_grade IN ('A','B','C') AND timestamp >= {era_ts}
            """).fetchall()
            out[h] = [{"grade": g, "won": int(won)} for g, won in r]
        except Exception as e:
            out[h] = e
    return out


def main():
    import duckdb
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    from database import DB_PATH
    era_ts = 0
    vp = os.path.join(os.path.dirname(DB_PATH), "saved_models", "architecture_version.pkl")
    if os.path.exists(vp):
        import datetime as dt
        era_ts = int(os.path.getmtime(vp) * 1000)
        print(f"[era] grading rows since model save: {dt.datetime.fromtimestamp(era_ts/1000)}")
    try:
        conn = duckdb.connect(DB_PATH, read_only=True)
    except Exception as e:
        print(f"analytics.duckdb is locked by the running app ({str(e)[:70]}).\n"
              f"  Stop the app briefly and rerun (no /api fallback for the grade yet).")
        return
    data = _load(conn, era_ts); conn.close()
    print("=" * 78)
    print("A/B/C GRADE STRATIFICATION (sign-truth, model era) — rule #2 gate")
    print("=" * 78)
    for h in HORIZONS:
        rows = data[h]
        if isinstance(rows, Exception):
            print(f"\n{h}m: query failed: {rows}"); continue
        if not rows:
            print(f"\n{h}m: no resolved graded rows yet."); continue
        res = stratification(rows)
        print(f"\n{h}m  (n={len(rows):,})  verdict: {res['verdict']}")
        for g in GRADES:
            s = res["stats"][g]
            rate = f"{s['rate']*100:.1f}%" if s["rate"] is not None else "  -  "
            print(f"   {g}: n={s['n']:<5} sign-acc={rate:<7} Wilson-LB={s['wilson_lb']*100:.1f}%")
    print("\nRule #2: trust/gate a grade ONLY when A>B>C, each n>=100, and A's Wilson-LB > C's rate.")


def selftest():
    good = ([{"grade": "A", "won": 1 if i % 5 else 0} for i in range(300)]      # ~80%
            + [{"grade": "B", "won": 1 if i % 2 else 0} for i in range(300)]     # ~50%
            + [{"grade": "C", "won": 1 if i % 3 == 0 else 0} for i in range(300)])  # ~33%
    rg = stratification(good)
    assert rg["gate_eligible"] and "STRATIFIES" in rg["verdict"], rg["verdict"]
    bad = ([{"grade": "A", "won": 1 if i % 100 < 44 else 0} for i in range(300)]   # 44%
           + [{"grade": "B", "won": 1 if i % 100 < 59 else 0} for i in range(300)]  # 59% (inverted)
           + [{"grade": "C", "won": 1 if i % 100 < 41 else 0} for i in range(300)])
    rb = stratification(bad)
    assert not rb["gate_eligible"] and "DOES NOT" in rb["verdict"], rb["verdict"]
    thin = [{"grade": g, "won": 1} for g in GRADES]
    assert "INSUFFICIENT" in stratification(thin)["verdict"]
    assert wilson_lb(80, 100) < 0.80
    print("grade_scorecard self-test: stratifying PASS, inverted FAIL, thin INSUFFICIENT. ALL PASS")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
