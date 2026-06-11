# One-off deep-scan: sign-truth scorecard from DuckDB (app must be stopped).
import duckdb, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from database import DB_PATH

HORIZONS = [1, 3, 5, 7, 10, 15]
conn = duckdb.connect(DB_PATH, read_only=True)

print("=" * 70)
print("1) SIGN-TRUTH LEAN ACCURACY per horizon (all resolved directional rows)")
print("=" * 70)
for h in HORIZONS:
    try:
        r = conn.execute(f"""
            SELECT COUNT(*) n,
                   SUM(CASE WHEN (raw_direction='UP' AND actual_move>0)
                              OR (raw_direction='DOWN' AND actual_move<0) THEN 1 ELSE 0 END) wins,
                   SUM(CASE WHEN raw_direction='UP' THEN 1 ELSE 0 END) ups,
                   SUM(CASE WHEN raw_direction='DOWN' THEN 1 ELSE 0 END) downs
            FROM predictions_{h}m
            WHERE resolved AND raw_direction IN ('UP','DOWN') AND actual_move IS NOT NULL
        """).fetchone()
        n, w, u, d = r
        acc = f"{w/n*100:.1f}%" if n else "n/a"
        print(f"  {h:>2}m: n={n:<4} sign-acc={acc:<7} leans: UP={u} DOWN={d}")
    except Exception as e:
        print(f"  {h}m: {e}")

print()
print("=" * 70)
print("2) SAME, LAST 24H ONLY (recent behavior / DOWN-bias check)")
print("=" * 70)
cut = int((time.time() - 86400) * 1000)
for h in HORIZONS:
    try:
        r = conn.execute(f"""
            SELECT COUNT(*) n,
                   SUM(CASE WHEN (raw_direction='UP' AND actual_move>0)
                              OR (raw_direction='DOWN' AND actual_move<0) THEN 1 ELSE 0 END) wins,
                   SUM(CASE WHEN raw_direction='UP' THEN 1 ELSE 0 END) ups,
                   SUM(CASE WHEN raw_direction='DOWN' THEN 1 ELSE 0 END) downs,
                   SUM(CASE WHEN raw_direction='UP' AND actual_move>0 THEN 1 ELSE 0 END) up_wins,
                   SUM(CASE WHEN raw_direction='DOWN' AND actual_move<0 THEN 1 ELSE 0 END) down_wins
            FROM predictions_{h}m
            WHERE resolved AND raw_direction IN ('UP','DOWN') AND actual_move IS NOT NULL
              AND timestamp >= {cut}
        """).fetchone()
        n, w, u, d, uw, dw = r
        if n:
            ua = f"{uw/u*100:.0f}%" if u else "—"
            da = f"{dw/d*100:.0f}%" if d else "—"
            print(f"  {h:>2}m: n={n:<4} acc={w/n*100:.1f}%  UP {u} ({ua})  DOWN {d} ({da})")
        else:
            print(f"  {h:>2}m: no rows")
    except Exception as e:
        print(f"  {h}m: {e}")

print()
print("=" * 70)
print("3) POLYMARKET MIRROR (price_to_beat) — committed bets, model vs fallback")
print("=" * 70)
for h in (5, 15):
    try:
        rows = conn.execute(f"""
            SELECT COALESCE(lean_source,'model') src, COUNT(*) n,
                   SUM(CASE WHEN hit THEN 1 ELSE 0 END) wins
            FROM price_to_beat
            WHERE horizon={h} AND resolved AND our_direction IN ('UP','DOWN')
            GROUP BY 1
        """).fetchall()
        tot = conn.execute(f"""
            SELECT COUNT(*), SUM(CASE WHEN hit THEN 1 ELSE 0 END)
            FROM price_to_beat
            WHERE horizon={h} AND resolved AND our_direction IN ('UP','DOWN')
        """).fetchone()
        parts = ", ".join(f"{s}: {w}/{n} ({w/n*100:.0f}%)" for s, n, w in rows if n)
        ta = f"{tot[1]/tot[0]*100:.1f}%" if tot[0] else "n/a"
        print(f"  {h:>2}m: all {tot[1]}/{tot[0]} ({ta})   [{parts}]")
    except Exception as e:
        print(f"  {h}m: {e}")

print()
print("=" * 70)
print("4) REGIME x SIGN-TRUTH (the new poor_regimes feed) — 5m")
print("=" * 70)
try:
    rows = conn.execute("""
        SELECT regime, COUNT(*) n,
               ROUND(AVG(CASE WHEN (raw_direction='UP' AND actual_move>0)
                                OR (raw_direction='DOWN' AND actual_move<0)
                              THEN 1.0 ELSE 0.0 END)*100,1) acc
        FROM predictions_5m
        WHERE resolved AND raw_direction IN ('UP','DOWN') AND actual_move IS NOT NULL
        GROUP BY regime ORDER BY acc DESC
    """).fetchall()
    for rg, n, acc in rows:
        flag = "  << would now be BLOCKED" if n >= 30 and acc < 50 else ""
        print(f"  {rg:<18} n={n:<5} sign-acc={acc}%{flag}")
except Exception as e:
    print(f"  {e}")

print()
print("=" * 70)
print("5) OLD-vs-NEW poor_regimes feed comparison — what the bug was hiding (5m)")
print("=" * 70)
try:
    rows = conn.execute("""
        SELECT regime, COUNT(*) n,
               ROUND(AVG(CASE WHEN hit THEN 1.0 ELSE 0.0 END)*100,1) old_hit_acc,
               ROUND(AVG(CASE WHEN (raw_direction='UP' AND actual_move>0)
                                OR (raw_direction='DOWN' AND actual_move<0)
                              THEN 1.0 ELSE 0.0 END)*100,1) true_acc
        FROM predictions_5m
        WHERE resolved AND raw_direction IN ('UP','DOWN') AND actual_move IS NOT NULL
        GROUP BY regime ORDER BY n DESC
    """).fetchall()
    for rg, n, oh, ta in rows:
        print(f"  {rg:<18} n={n:<5} old(hit)={oh}%   TRUE={ta}%   delta={oh-ta:+.1f}")
except Exception as e:
    print(f"  {e}")

print()
print("=" * 70)
print("6) ROW COUNTS / data freshness")
print("=" * 70)
for h in HORIZONS:
    try:
        r = conn.execute(f"""
            SELECT COUNT(*),
                   MAX(timestamp)
            FROM predictions_{h}m
        """).fetchone()
        age_min = (time.time()*1000 - (r[1] or 0)) / 60000 if r[1] else -1
        print(f"  predictions_{h}m: {r[0]} rows, newest {age_min:.0f} min ago")
    except Exception as e:
        print(f"  {h}m: {e}")

conn.close()
print("\nDONE")
