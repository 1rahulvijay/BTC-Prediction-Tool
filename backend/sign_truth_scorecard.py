# Sign-truth scorecard from DuckDB. Works with the app RUNNING (snapshots the DB
# when the live process holds the single-writer lock) or stopped (direct read).
import duckdb, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from database import DB_PATH

HORIZONS = [1, 3, 5, 7, 10, 15, 30]


def _http_fallback():
    """Windows DuckDB locks are EXCLUSIVE — an outside process can't even copy the
    file. But the lock holder is the app itself, so ask IT for the scorecard."""
    import json, urllib.request
    try:
        with urllib.request.urlopen("http://127.0.0.1:8000/api/scorecard", timeout=120) as r:
            d = json.loads(r.read())
    except Exception as e:
        print("DB is locked AND the live-app endpoint is unreachable.")
        print("Either: (a) the app is an older build without /api/scorecard — stop the")
        print("app briefly and rerun this script; or (b) a zombie process holds the DB")
        print(f"— check Get-Process python. ({e})")
        sys.exit(1)
    import datetime
    print("[live-mode] scorecard served by the RUNNING app via /api/scorecard")
    if d.get("era_ts"):
        print(f"[era] rows since model save: {datetime.datetime.fromtimestamp(d['era_ts']/1000)}")
    print("=" * 70)
    print("1) SIGN-TRUTH LEAN ACCURACY per horizon (model era)")
    print("=" * 70)
    for h, v in sorted(d.get("horizons", {}).items(), key=lambda kv: int(kv[0])):
        if v.get("error") or not v.get("n"):
            print(f"  {h:>2}m: {v.get('error', 'no rows')}")
            continue
        ua = f"{v['up_acc']*100:.0f}%" if v.get("up_acc") is not None else "—"
        da = f"{v['down_acc']*100:.0f}%" if v.get("down_acc") is not None else "—"
        print(f"  {h:>2}m: n={v['n']:<5} sign-acc={v['acc']*100:.1f}%  "
              f"UP {v['up_n']} ({ua})  DOWN {v['down_n']} ({da})")
    print()
    print("=" * 70)
    print("2) POLYMARKET MIRROR — committed bets, model vs fallback (model era)")
    print("=" * 70)
    for h, srcs in sorted(d.get("mirror", {}).items(), key=lambda kv: int(kv[0])):
        if isinstance(srcs, dict) and not srcs.get("error"):
            parts = ", ".join(f"{s}: {v['wins']}/{v['n']} ({v['acc']*100:.0f}%)"
                              for s, v in srcs.items() if v.get("n"))
            print(f"  {h:>2}m: {parts or 'no resolved rounds'}")
        else:
            print(f"  {h:>2}m: {srcs}")
    print()
    print("=" * 70)
    print("3) PER-BASE-MODEL directional accuracy (which model earns its seat)")
    print("=" * 70)
    models = d.get("models") or {}
    if isinstance(models, dict) and models and not models.get("error"):
        hs = [1, 3, 5, 7, 10, 15, 30]
        print(f"  {'model':<10}" + "".join(f"{str(h)+'m':>12}" for h in hs))
        for m, by_h in sorted(models.items()):
            cells = []
            for h in hs:
                v = by_h.get(str(h)) or by_h.get(h)
                cells.append(f"{v['acc']*100:.0f}% ({v['n']})" if v and v.get("n") else "—")
            print(f"  {m:<10}" + "".join(f"{c:>12}" for c in cells))
    else:
        print(f"  {models.get('error', 'no resolved per-model votes yet') if isinstance(models, dict) else models}")
    print()
    print("=" * 70)
    print("4) PARTIAL-CANDLE SKEW (5m sign-acc by second-of-minute)")
    print("=" * 70)
    _lbl = {0: "0-14s (freshest bar)", 1: "15-29s", 2: "30-44s", 3: "45-59s (fullest bar)"}
    bks = d.get("partial_candle_buckets", [])
    for b in bks:
        print(f"  {_lbl.get(b['bucket'], b['bucket']):<22} n={b['n']:<5} sign-acc={b['acc']*100:.1f}%")
    accs = {b["bucket"]: b["acc"] for b in bks if b["n"] >= 30}
    if 0 in accs and 3 in accs:
        delta = (accs[3] - accs[0]) * 100
        print(f"  -> late-minus-early delta: {delta:+.1f} pts "
              f"({'SKEW LIKELY REAL — see V5.md' if delta >= 4 else 'no strong evidence of skew'})")
    print("\nDONE (live mode)")
    sys.exit(0)


try:
    conn = duckdb.connect(DB_PATH, read_only=True)
except Exception:
    _http_fallback()

# MODEL-ERA filter (same rule as calibration): only rows predicted by the CURRENT
# bundle. Without this, yesterday's old-model rows blend into "last 24h" and the
# scorecard grades two different models as one.
ERA_TS = 0
try:
    _vp = os.path.join(os.path.dirname(DB_PATH), "saved_models", "architecture_version.pkl")
    if os.path.exists(_vp):
        ERA_TS = int(os.path.getmtime(_vp) * 1000)
        import datetime as _dt
        print(f"[era] scoring rows since model save: "
              f"{_dt.datetime.fromtimestamp(ERA_TS/1000)} (older rows excluded)")
except Exception:
    pass

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
              AND timestamp >= {ERA_TS}
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
cut = max(int((time.time() - 86400) * 1000), ERA_TS)
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
print("   (1m/3m/7m/10m are PRACTICE mirrors — only 5m/15m are real markets)")
print("=" * 70)
for h in (1, 3, 5, 7, 10, 15, 30):
    try:
        rows = conn.execute(f"""
            SELECT COALESCE(lean_source,'model') src, COUNT(*) n,
                   SUM(CASE WHEN hit THEN 1 ELSE 0 END) wins
            FROM price_to_beat
            WHERE horizon={h} AND resolved AND our_direction IN ('UP','DOWN')
              AND timestamp >= {ERA_TS}
            GROUP BY 1
        """).fetchall()
        tot = conn.execute(f"""
            SELECT COUNT(*), SUM(CASE WHEN hit THEN 1 ELSE 0 END)
            FROM price_to_beat
            WHERE horizon={h} AND resolved AND our_direction IN ('UP','DOWN')
              AND timestamp >= {ERA_TS}
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
    rows = conn.execute(f"""
        SELECT regime, COUNT(*) n,
               ROUND(AVG(CASE WHEN (raw_direction='UP' AND actual_move>0)
                                OR (raw_direction='DOWN' AND actual_move<0)
                              THEN 1.0 ELSE 0.0 END)*100,1) acc
        FROM predictions_5m
        WHERE resolved AND raw_direction IN ('UP','DOWN') AND actual_move IS NOT NULL
          AND timestamp >= {ERA_TS}
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
    rows = conn.execute(f"""
        SELECT regime, COUNT(*) n,
               ROUND(AVG(CASE WHEN hit THEN 1.0 ELSE 0.0 END)*100,1) old_hit_acc,
               ROUND(AVG(CASE WHEN (raw_direction='UP' AND actual_move>0)
                                OR (raw_direction='DOWN' AND actual_move<0)
                              THEN 1.0 ELSE 0.0 END)*100,1) true_acc
        FROM predictions_5m
        WHERE resolved AND raw_direction IN ('UP','DOWN') AND actual_move IS NOT NULL
          AND timestamp >= {ERA_TS}
        GROUP BY regime ORDER BY n DESC
    """).fetchall()
    for rg, n, oh, ta in rows:
        print(f"  {rg:<18} n={n:<5} old(hit)={oh}%   TRUE={ta}%   delta={oh-ta:+.1f}")
except Exception as e:
    print(f"  {e}")

print()
print("=" * 70)
print("6) PARTIAL-CANDLE SKEW CHECK — sign-acc by second-of-minute at prediction (5m)")
print("   Serve-time predictions use the FORMING 1m candle; training only saw")
print("   complete candles. If early-minute buckets are clearly worse, that")
print("   train/serve skew is real -> V5 fix (bar-progress normalization).")
print("=" * 70)
try:
    rows = conn.execute(f"""
        SELECT CAST(FLOOR((timestamp % 60000) / 15000.0) AS INT) bucket, COUNT(*) n,
               ROUND(AVG(CASE WHEN (raw_direction='UP' AND actual_move>0)
                                OR (raw_direction='DOWN' AND actual_move<0)
                              THEN 1.0 ELSE 0.0 END)*100,1) acc
        FROM predictions_5m
        WHERE resolved AND raw_direction IN ('UP','DOWN') AND actual_move IS NOT NULL
          AND timestamp >= {ERA_TS}
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    _lbl = {0: "0-14s (freshest bar)", 1: "15-29s", 2: "30-44s", 3: "45-59s (fullest bar)"}
    for b, n, acc in rows:
        print(f"  {_lbl.get(b, b):<22} n={n:<5} sign-acc={acc}%")
    if len(rows) >= 2:
        accs = {b: a for b, n, a in rows if n >= 30}
        if 0 in accs and 3 in accs:
            d = accs[3] - accs[0]
            print(f"  -> late-minus-early delta: {d:+.1f} pts "
                  f"({'SKEW LIKELY REAL — see V5.md' if d >= 4 else 'no strong evidence of skew'})")
except Exception as e:
    print(f"  {e}")

print()
print("=" * 70)
print("7) ROW COUNTS / data freshness")
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
