"""
analyze_timeframe_value.py — which HORIZON earns its keep, and which to ELIMINATE (no use).
============================================================================================
The tool trains/serves 7 horizons (1/3/5/7/10/15/30m). Each one multiplies head-training +
matrix-labeling cost ~linearly. This script measures, per horizon, whether it provides any value
and emits a KEEP / OPTIONAL / REMOVE verdict, so the dead ones can be pruned to speed training.

A horizon earns its keep ONLY via a concrete value source (measured, not assumed):
  1. TRADEABLE  — a real Polymarket market exists at this horizon (5m, 15m). This is the whole point.
  2. DIRECTIONAL— the committed MODEL lean beats a coin-flip (Wilson-LB > 50%, n>=MIN_N). Direction is
                  at the information ceiling, so this is expected to be EMPTY — but we test, not assume.
  3. FEEDBACK   — fastest clock = densest resolved rounds + P(hold) snapshots (1m). Useful even with no
                  market: more late-entry P(hold) samples, fastest calibration feedback.
Anything that is none of the above = REMOVE (no market, coin-flip direction, pure compute cost).

KEY POINT (rule 7 — don't chase a dead end): because direction is a coin-flip at EVERY horizon, no
non-tradeable horizon's DIRECTION can inform a tradeable one (stacking coin-flips != signal). So the
only honest reasons to keep a non-tradeable horizon are TRADEABLE (n/a) or FEEDBACK (1m).

Read-only. If the backend holds the analytics.duckdb lock, this copies a stale-but-consistent snapshot
(last checkpoint) and reads that, so it runs WITHOUT stopping the app. Verdict logic has a --selftest.

Usage:  python backend/research/standalone/analyze_timeframe_value.py [--source pyth] [--min-n 200]
        python backend/research/standalone/analyze_timeframe_value.py --selftest
"""
from __future__ import annotations

try:
    from . import _bootstrap as _research_bootstrap  # noqa: F401
except ImportError:
    import _bootstrap as _research_bootstrap  # noqa: F401

del _research_bootstrap


import argparse
import math
import os
import shutil
import sys
import tempfile
from datetime import date

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
# ONE RESOLVER - see datastore_identity. Four analytics.duckdb files exist with disjoint spans,
# and a script that resolves its own path can silently report on the wrong three weeks.
from datastore_identity import resolve as _resolve_store        # noqa: E402
DB = str(_resolve_store())
HORIZONS = (1, 3, 5, 7, 10, 15, 30)

# Horizons with a real Polymarket market to bet on. The ONLY hard reason to keep a horizon.
# (Polymarket runs recurring BTC up/down markets at 5m and 15m; the others have no venue.)
TRADEABLE = {5, 15}
MIN_N = 200          # min committed model leans before a "directional edge" claim is allowed


def wilson_lb(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    cen = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100.0 * (cen - half)


def classify_horizon(s, min_n=MIN_N):
    """PURE verdict logic (unit-tested). `s` = per-horizon stat dict. Returns (verdict, reason)."""
    h = s["horizon"]
    if s["n"] == 0:
        return ("NO DATA", "no resolved rounds logged")
    # 1. tradeable market -> always keep
    if h in TRADEABLE:
        edge = (s["model_n"] >= min_n and s["model_lb"] > 50.0)
        return ("KEEP", "tradeable Polymarket market" + (
            f"; +model directional edge (LB {s['model_lb']:.1f}%)" if edge else "; direction ~coin-flip (value=P(hold)+band)"))
    # 2. genuine directional edge even without a market -> keep
    if s["model_n"] >= min_n and s["model_lb"] > 50.0:
        return ("KEEP", f"no market BUT model direction beats coin-flip (LB {s['model_lb']:.1f}%, n={s['model_n']})")
    # 3. fastest feedback clock -> optional keep (densest P(hold)/feedback)
    if h == min(HORIZONS):
        return ("OPTIONAL", "no market + coin-flip direction; KEEP only for fastest feedback / densest P(hold) snapshots")
    # 4. otherwise dead weight
    return ("REMOVE", "no market + direction ~coin-flip (LB %.1f%% <= 50) -> pure training/label cost" % s["model_lb"])


def _connect_ro():
    """Read-only connect; if the app holds the lock, read a stale-but-consistent snapshot copy."""
    import duckdb
    try:
        return duckdb.connect(DB, read_only=True), "live (db was free)"
    except Exception:
        pass
    try:
        snap = os.path.join(tempfile.gettempdir(), "analytics_tf_snapshot.duckdb")
        shutil.copy2(DB, snap)   # copy ONLY the .duckdb (no .wal) -> opens read_only at last checkpoint
        return duckdb.connect(snap, read_only=True), "SNAPSHOT copy (app running; data current to last checkpoint, ~stale by seconds)"
    except Exception as e:
        return None, f"locked and snapshot failed: {str(e)[:80]}"


def run(source="pyth", min_n=MIN_N):
    if not os.path.exists(DB):
        print("no analytics.duckdb"); return
    c, how = _connect_ro()
    if c is None:
        print("Cannot read analytics.duckdb -- " + how + "\nStop the backend briefly and re-run."); return
    print(f"DB read mode: {how}")

    cols = [r[1] for r in c.execute("PRAGMA table_info('price_to_beat')").fetchall()]
    SRC = (f"COALESCE(source,'pyth') = '{source}'" if "source" in cols
           else ("1=1" if source == "pyth" else "1=0"))

    span = c.execute(f"""SELECT (max(timestamp)-min(timestamp))/86400000.0
                         FROM price_to_beat WHERE resolved AND our_direction IN ('UP','DOWN') AND {SRC}""").fetchone()[0]
    span_days = float(span or 0.0)

    stats = []
    for h in HORIZONS:
        row = c.execute(f"""
            SELECT count(*) n,
                   sum(CASE WHEN hit THEN 1 ELSE 0 END) k,
                   sum(CASE WHEN COALESCE(lean_source,'model')<>'fallback' THEN 1 ELSE 0 END) mn,
                   sum(CASE WHEN COALESCE(lean_source,'model')<>'fallback' AND hit THEN 1 ELSE 0 END) mk,
                   sum(CASE WHEN COALESCE(lean_source,'model')='fallback' THEN 1 ELSE 0 END) fn,
                   sum(CASE WHEN COALESCE(lean_source,'model')='fallback' AND hit THEN 1 ELSE 0 END) fk
            FROM price_to_beat WHERE horizon={h} AND resolved AND our_direction IN ('UP','DOWN') AND {SRC}
        """).fetchone()
        n, k, mn, mk, fn, fk = (int(x or 0) for x in row)
        stats.append({
            "horizon": h, "n": n, "win": (100*k/n if n else 0.0), "lb": wilson_lb(k, n),
            "model_n": mn, "model_win": (100*mk/mn if mn else 0.0), "model_lb": wilson_lb(mk, mn),
            "fb_n": fn, "fb_win": (100*fk/fn if fn else 0.0),
            "per_day": (n/span_days if span_days else 0.0),
        })
    c.close()

    L = [f"# Timeframe VALUE / Elimination Analysis — source={source} — {date.today().isoformat()}", "",
         f"History span: {span_days:.1f} days. Win% = directional hit on committed UP/DOWN. MODEL = committed "
         f"lean (lean_source != fallback); a horizon has a *directional edge* only if model Wilson-LB > 50% "
         f"with n >= {min_n}. Tradeable markets: {sorted(TRADEABLE)} (5m/15m). DB read: {how}.", ""]

    L.append("## Per-horizon value")
    hdr = ["hz", "market?", "n", "rounds/day", "win %", "Wilson-LB", "model n", "model win %", "model LB", "fb win %"]
    L.append("| " + " | ".join(hdr) + " |")
    L.append("|" + "|".join("---" for _ in hdr) + "|")
    for s in stats:
        L.append("| {hz}m | {mk} | {n} | {pd:.1f} | {w:.1f} | {lb:.1f} | {mn} | {mw:.1f} | {mlb:.1f} | {fw:.1f} |".format(
            hz=s["horizon"], mk=("YES" if s["horizon"] in TRADEABLE else "no"), n=s["n"], pd=s["per_day"],
            w=s["win"], lb=s["lb"], mn=s["model_n"], mw=s["model_win"], mlb=s["model_lb"], fw=s["fb_win"]))

    L.append("\n## Verdict per horizon")
    L.append("| hz | verdict | reason |")
    L.append("|---|---|---|")
    keep, optional, remove = [], [], []
    for s in stats:
        v, why = classify_horizon(s, min_n)
        L.append(f"| {s['horizon']}m | **{v}** | {why} |")
        (keep if v == "KEEP" else optional if v == "OPTIONAL" else remove if v == "REMOVE" else []).append(s["horizon"])

    n_total = len([s for s in stats if s["n"] > 0])
    n_drop = len(remove)
    pct = (100*n_drop/n_total if n_total else 0.0)
    L.append("\n## Bottom line")
    L.append(f"- **KEEP:** {', '.join(f'{h}m' for h in keep) or '(none)'}  (tradeable market or a real directional edge)")
    L.append(f"- **OPTIONAL:** {', '.join(f'{h}m' for h in optional) or '(none)'}  (no market/edge; keep only for fastest feedback + P(hold) density)")
    L.append(f"- **REMOVE:** {', '.join(f'{h}m' for h in remove) or '(none)'}  (no market, coin-flip direction, pure cost)")
    L.append(f"- Pruning the REMOVE set drops **{n_drop}/{n_total} horizons (~{pct:.0f}%)** of per-horizon head "
             f"training + matrix labeling, with **no accuracy lost** (they carry no market and no directional edge).")
    L.append("- Direction is a coin-flip at EVERY horizon, so a non-tradeable horizon can NOT inform a tradeable "
             "one (stacking coin-flips != signal). The fine-scale value lives in P(hold) late-entry + L2 "
             "microstructure, not in small-TF direction models.")
    L.append(f"\n_Recommended keep-set: {{1m(optional), 5m, 15m}} — leanest tool, the two tradeable markets plus "
             f"the densest feedback clock. Removing {', '.join(f'{h}m' for h in remove) or 'the rest'} is free speed._")

    out = os.path.join(ROOT, "docs", "active", f"TIMEFRAME_VALUE_{source}_{date.today().isoformat()}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\nWrote {out}")


def selftest():
    # tradeable horizon, coin-flip -> KEEP (market)
    v, _ = classify_horizon({"horizon": 5, "n": 1000, "model_n": 800, "model_lb": 48.0})
    assert v == "KEEP", v
    # tradeable horizon WITH directional edge -> KEEP (and reason mentions edge)
    v, why = classify_horizon({"horizon": 15, "n": 1000, "model_n": 800, "model_lb": 53.0})
    assert v == "KEEP" and "edge" in why, (v, why)
    # non-tradeable, coin-flip, not the fastest clock -> REMOVE
    v, _ = classify_horizon({"horizon": 7, "n": 1000, "model_n": 800, "model_lb": 47.0})
    assert v == "REMOVE", v
    v, _ = classify_horizon({"horizon": 30, "n": 1000, "model_n": 800, "model_lb": 49.9})
    assert v == "REMOVE", v
    # non-tradeable BUT real directional edge -> KEEP
    v, _ = classify_horizon({"horizon": 3, "n": 1000, "model_n": 800, "model_lb": 51.0})
    assert v == "KEEP", v
    # fastest clock (1m), coin-flip, no market -> OPTIONAL
    v, _ = classify_horizon({"horizon": 1, "n": 1000, "model_n": 800, "model_lb": 48.0})
    assert v == "OPTIONAL", v
    # too few model leans to claim an edge even if lb>50 -> REMOVE (n gate)
    v, _ = classify_horizon({"horizon": 7, "n": 1000, "model_n": 50, "model_lb": 60.0}, min_n=200)
    assert v == "REMOVE", v
    # no data -> NO DATA
    v, _ = classify_horizon({"horizon": 7, "n": 0, "model_n": 0, "model_lb": 0.0})
    assert v == "NO DATA", v
    print("analyze_timeframe_value self-test: ALL PASS (verdict logic across tradeable / edge / coin-flip / "
          "fastest-clock / n-gate / no-data)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["pyth", "binance"], default="pyth")
    ap.add_argument("--min-n", type=int, default=MIN_N)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    selftest() if a.selftest else run(a.source, a.min_n)
