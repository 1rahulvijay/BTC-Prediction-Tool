"""
diagnose_model.py — systematic FEATURE / MODEL noise diagnostic.
================================================================
Answers, with evidence from the DB: which FEATURES are dead or noise, which base MODEL is
weak, and what is dragging accuracy — so you cut the right thing before a retrain instead of
guessing. Read-only; touches nothing.

Sources (all written by the normal app/train):
  • feature_importance  (SHAP, written every train) → per-horizon feature ranking; bottom = the
    model barely uses them (noise/dead candidates), cross-checked against the known constant slots.
  • feature_outcome_log (B1) → per-feature VARIANCE over live rows; ~0 variance = CONSTANT in
    serving (the L2/options dead-weight problem) — those columns can't help, only add overfit risk.
  • model_predictions   (sign-truth graded) → per-base-model COMMITTED accuracy; the weakest model
    is dead weight in the stacker/agreement.
  • predictions_{h}m    → committed sign-truth per horizon (overall health check).

NOTE: DuckDB is single-writer — run this when the APP IS STOPPED (the natural pre-retrain moment),
or it will report the lock and exit cleanly.

Usage:  python backend/diagnose_model.py  [--db PATH] [--bottom 15]
"""
import argparse
import os
import sys

try:                       # Windows consoles default to cp1252 — force UTF-8 for the report
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass


def _connect(db_path):
    import duckdb
    return duckdb.connect(db_path, read_only=True)


def feature_signal(conn, bottom=15):
    """Latest-train SHAP ranking per horizon → top drivers + bottom (low-signal) features."""
    out = {}
    try:
        horizons = [r[0] for r in conn.execute(
            "SELECT DISTINCT horizon FROM feature_importance ORDER BY horizon").fetchall()]
    except Exception as e:
        return {"error": f"no feature_importance ({str(e)[:60]})"}
    for h in horizons:
        latest = conn.execute(
            "SELECT MAX(timestamp) FROM feature_importance WHERE horizon=?", [h]).fetchone()[0]
        rows = conn.execute("""
            SELECT feature, importance FROM feature_importance
            WHERE horizon=? AND timestamp=? ORDER BY importance DESC""", [h, latest]).fetchall()
        if not rows:
            continue
        out[h] = {"top": rows[:8], "bottom": rows[-bottom:]}
    return out


def feature_deadness(conn):
    """Per-feature variance over feature_outcome_log (B1) → constant/near-dead columns."""
    try:
        n = conn.execute("SELECT COUNT(*) FROM feature_outcome_log").fetchone()[0]
    except Exception as e:
        return {"error": f"no feature_outcome_log ({str(e)[:60]})"}
    import numpy as np
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    from features import FEATURE_NAMES, calculate_schema_hash
    W = len(FEATURE_NAMES)
    sh = calculate_schema_hash(FEATURE_NAMES)
    # A schema bump (e.g. 130->136) leaves MIXED-WIDTH vectors in the log — filter to the
    # CURRENT schema so variance is computed on a homogeneous, current-features matrix.
    try:
        feats = conn.execute(
            "SELECT features FROM feature_outcome_log WHERE schema_hash=?", [sh]).fetchall()
    except Exception:
        feats = conn.execute("SELECT features FROM feature_outcome_log").fetchall()
    rows = [f[0] for f in feats if f[0] is not None and len(f[0]) == W]
    if len(rows) < 200:
        return {"pending": len(rows),
                "msg": f"only {len(rows)} B1 rows on the current schema (hash {sh}) — "
                       f"variance needs ~200+ (collect more live; {n} total rows, mixed schema)"}
    X = np.array(rows, dtype=float)
    var = X.var(axis=0)
    dead = [(FEATURE_NAMES[i], float(var[i])) for i in np.argsort(var) if var[i] < 1e-9][:40]
    return {"rows": len(rows), "dead": dead, "n_dead": int((var < 1e-9).sum()), "n_total": int(len(var))}


def model_noise(conn):
    """Per-base-model COMMITTED (UP/DOWN) sign-truth accuracy from model_predictions."""
    try:
        rows = conn.execute("""
            SELECT model,
                   COUNT(*) AS n,
                   AVG(CASE WHEN direction = actual_direction THEN 1.0 ELSE 0.0 END) AS acc
            FROM model_predictions
            WHERE resolved AND direction IN ('UP','DOWN')
            GROUP BY model HAVING COUNT(*) >= 20 ORDER BY acc""").fetchall()
    except Exception as e:
        return {"error": f"no model_predictions ({str(e)[:60]})"}
    return {"by_model": rows}


def horizon_health(conn):
    out = {}
    for h in (1, 3, 5, 7, 10, 15, 30):
        try:
            r = conn.execute(f"""
                SELECT COUNT(*) n,
                  AVG(CASE WHEN (raw_direction='UP' AND actual_move>0)
                            OR (raw_direction='DOWN' AND actual_move<0) THEN 1.0 ELSE 0.0 END) acc
                FROM predictions_{h}m
                WHERE resolved AND raw_direction IN ('UP','DOWN') AND actual_move IS NOT NULL""").fetchone()
            if r and r[0]:
                out[h] = (int(r[0]), float(r[1]))
        except Exception:
            pass
    return out


def _wilson_lb(p, n, z=1.96):
    """Wilson 95% lower bound — the anti-small-sample-lie number for a win rate."""
    if n <= 0:
        return 0.0
    d = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5
    return max(0.0, (centre - margin) / d)


def grade_validation(conn):
    """Per A/B/C grade: committed sign-truth win rate across ALL horizons, with Wilson LB.
    Answers 'do grades actually stratify (A>B>C)?' — read from the persisted confluence_grade."""
    parts = []
    for h in (1, 3, 5, 7, 10, 15, 30):
        parts.append(f"""SELECT substr(confluence_grade,1,1) g,
            CASE WHEN (raw_direction='UP' AND actual_move>0)
                  OR (raw_direction='DOWN' AND actual_move<0) THEN 1 ELSE 0 END hit
            FROM predictions_{h}m
            WHERE resolved AND raw_direction IN ('UP','DOWN') AND actual_move IS NOT NULL
              AND confluence_grade IS NOT NULL AND confluence_grade <> ''""")
    sql = ("SELECT g, COUNT(*) n, AVG(hit) acc FROM (" + " UNION ALL ".join(parts) +
           ") WHERE g IN ('A','B','C') GROUP BY g ORDER BY g")
    try:
        rows = conn.execute(sql).fetchall()
    except Exception as e:
        return {"error": str(e)[:60]}
    return [(g, int(n), float(acc), _wilson_lb(float(acc), int(n))) for g, n, acc in rows]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("BTC_DB_PATH") or os.path.join(
        os.environ.get("BTC_DATA_DIR") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "data"),
        "analytics.duckdb"))
    ap.add_argument("--bottom", type=int, default=15)
    args = ap.parse_args()

    if not os.path.exists(args.db):
        print(f"DB not found: {args.db}"); return
    try:
        conn = _connect(args.db)
    except Exception as e:
        print(f"Could not open DB read-only (is the app running? stop it first): {str(e)[:120]}")
        return

    print("=" * 70)
    print("MODEL NOISE DIAGNOSTIC")
    print("=" * 70)

    print("\n1) HORIZON HEALTH (committed sign-truth — the number that matters)")
    for h, (n, acc) in sorted(horizon_health(conn).items()):
        flag = "  <-- BELOW COIN-FLIP" if acc < 0.5 else ("  <-- edge" if acc >= 0.54 else "")
        print(f"   {h:>2}m: n={n:<5} acc={acc*100:.1f}%{flag}")

    print("\n2) WEAKEST BASE MODELS (committed accuracy; lowest = dead weight)")
    print("   [!] reliable ONLY on rows written after the model_verifier sign-truth fix (§5ba) went")
    print("       live — i.e. after one restart. Pre-fix rows read artificially low; trust §1 first.")
    mn = model_noise(conn)
    if mn.get("by_model"):
        for model, n, acc in mn["by_model"]:
            print(f"   {model:<10} acc={acc*100:.1f}%  (n={n})")
    else:
        print(f"   {mn.get('error','no data')}")

    print("\n3) DEAD FEATURES (≈0 variance in live B1 rows = constant-in-training, pure dead weight)")
    fd = feature_deadness(conn)
    if fd.get("dead") is not None:
        print(f"   {fd['n_dead']}/{fd['n_total']} features are ~constant. Worst:")
        for name, v in fd["dead"][:20]:
            print(f"     {name:<28} var={v:.2e}")
    else:
        print(f"   {fd.get('msg', fd.get('error','no data'))}")

    print("\n4) LOW-SIGNAL FEATURES (bottom SHAP per horizon — model barely uses them)")
    fs = feature_signal(conn, args.bottom)
    if isinstance(fs, dict) and not fs.get("error"):
        from collections import Counter
        cnt = Counter()
        for h, d in fs.items():
            for feat, imp in d["bottom"]:
                cnt[feat] += 1
        print("   features in the bottom-%d across the MOST horizons (cut candidates):" % args.bottom)
        for feat, c in cnt.most_common(20):
            print(f"     {feat:<28} bottom in {c}/{len(fs)} horizons")
    else:
        print(f"   {fs.get('error','no data')}")

    print("\n5) GRADE VALIDATION (do A/B/C actually stratify? committed sign-truth + Wilson 95% LB)")
    gv = grade_validation(conn)
    if isinstance(gv, list) and gv:
        for g, n, acc, lb in gv:
            print(f"   Grade {g}: {acc*100:>5.1f}%  (n={n:<5} Wilson-LB {lb*100:.1f}%)")
        order = {g: acc for g, n, acc, lb in gv}
        if {"A", "B", "C"} <= set(order):
            stratifies = order["A"] >= order["B"] >= order["C"]
            small = any(n < 100 for g, n, acc, lb in gv)
            print(f"   -> {'A>=B>=C holds' if stratifies else 'NOT monotonic (grades unreliable)'}"
                  f"{' — but n<100 somewhere, still small' if small else ''}. "
                  "Trust grades only once each n>=100 AND A's Wilson-LB > C's win rate.")
    else:
        print(f"   {gv.get('error','no graded rows yet') if isinstance(gv, dict) else gv}")

    print("\nVERDICT: cut = features that are BOTH ~0-variance (§3) AND bottom-SHAP (§4); drop the")
    print("weakest base model (§2) if it trails the pack by >3pts on a stable sample. Re-measure §1")
    print("after each cut — only keep a change if committed sign-truth improves.")
    conn.close()


if __name__ == "__main__":
    main()
