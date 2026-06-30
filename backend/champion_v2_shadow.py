"""
champion_v2_shadow.py - read-only replay: do regime / meta-skip filters beat P(Hold) alone?  (no live change)
============================================================================================================
The external review proposed a champion_v2 that gates on regime + a CatBoost "meta-skip" head. Before
promoting ANY of that, this script tests it honestly against the right baseline. Two questions:

  TRACK A - "acted side HELD to resolution" (champion_snapshots, late-entry; mean seconds_left ~10s).
    Compares: baseline / regime-friendly / P(Hold)-threshold / meta-skip / regime+meta-skip.
    THE KEY TEST: does meta-skip (top-k) beat a plain P(Hold)-threshold at the SAME coverage? If not,
    meta-skip is just P(Hold) re-derived (no new edge) -- which the deduped data strongly suggests
    (held% ~= mean p_hold at every regime). High held% here is the KNOWN late-entry P(Hold) edge, NOT a
    fresh tradeable direction edge (the market prices late states too -> only profitable if Polymarket
    misprices it, which is gated on enough rounds with both recorded asks and official settlements).

  TRACK B - "fresh DIRECTION correct" (price_to_beat round level). This is the hard, tradeable question.
    Baseline ~coin-flip; reports the regime gate (RANGE/LOW_VOL) with overall + recent-250 drift.

Leak-free: dedupes to ONE row per round (last snapshot) and splits 70/30 BY ROUND-TIME, so no round
straddles the train/test boundary (the all-snapshots exp5 meta-skip leaked here). Read-only; needs the
backend stopped (DuckDB exclusive lock on Windows). Verdict logic has a --selftest.

Usage:  python backend/champion_v2_shadow.py [--source pyth] [--split 0.70] [--recent 250]
        python backend/champion_v2_shadow.py --selftest
"""
from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import date

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
DB = os.path.join(DATA, "analytics.duckdb")

FRIENDLY = {"RANGE", "LOW_VOLATILITY"}
HOSTILE = {"TRENDING_UP", "HIGH_VOLATILITY"}
NUM = ["horizon", "seconds_left", "current_move", "p_hold", "p_big_move", "p_big_drop",
       "p_big_up", "p_big_down", "p_activity", "champion_confidence"]
CAT = ["current_position", "regime", "big_move_tier", "big_drop_risk", "activity_tier", "champion_action"]


def recorder_coverage():
    """Return (official settlements, quote rounds joined to an official settlement)."""
    try:
        import duckdb
        path = os.path.join(DATA, "execution_layer.duckdb")
        con = duckdb.connect(path, read_only=True)
        official = con.execute("""SELECT count(*) FROM pm_round_settlements
            WHERE resolution_source IN ('polymarket_clob','polymarket_gamma')""").fetchone()[0]
        quoted = con.execute("""SELECT count(DISTINCT q.slug)
            FROM pm_round_snapshots q JOIN pm_round_settlements s USING(slug)
            WHERE s.resolution_source IN ('polymarket_clob','polymarket_gamma')""").fetchone()[0]
        con.close()
        return int(official), int(quoted)
    except Exception:
        return 0, 0


def wilson_lb(k, n, z=1.96):
    if n == 0:
        return 0.0
    p = k / n
    d = 1 + z * z / n
    cen = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return 100.0 * (cen - half)


def eval_mask(y, mask):
    """coverage / accuracy / Wilson-LB over the acted rows."""
    n = int(mask.sum())
    if n == 0:
        return {"n": 0, "cov": 0.0, "acc": None, "lb": None}
    k = int(y[mask].sum())
    return {"n": n, "cov": 100.0 * n / len(y), "acc": 100.0 * k / n, "lb": wilson_lb(k, n)}


def topk_mask(score, frac):
    """boolean mask for the top `frac` of rows by score (coverage-controlled)."""
    if len(score) == 0:
        return np.zeros(0, bool)
    thr = np.quantile(score, 1.0 - frac)
    return score >= thr


def fit_meta(train_df, test_df):
    """Train a leak-free meta model on TRAIN rounds, return predicted hold-prob for TEST rounds.
    CatBoost if available else HistGB. Returns None if training is not possible."""
    try:
        from sklearn.compose import ColumnTransformer
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.impute import SimpleImputer
        from sklearn.pipeline import Pipeline
        from sklearn.preprocessing import OneHotEncoder
    except Exception:
        return None, "sklearn unavailable"
    ytr = train_df["held"].to_numpy(int)
    if len(np.unique(ytr)) < 2 or len(train_df) < 200:
        return None, f"train too small/degenerate (n={len(train_df)})"
    pre = ColumnTransformer([
        ("num", SimpleImputer(strategy="median"), NUM),
        ("cat", Pipeline([("imp", SimpleImputer(strategy="most_frequent")),
                          ("oh", OneHotEncoder(handle_unknown="ignore"))]), CAT)])
    name = "histgb"
    clf = HistGradientBoostingClassifier(max_iter=250, random_state=0)
    try:
        from catboost import CatBoostClassifier
        name = "catboost"
        clf = CatBoostClassifier(iterations=250, depth=5, learning_rate=0.05, verbose=0,
                                 random_seed=0, allow_writing_files=False)
    except Exception:
        pass
    try:
        pipe = Pipeline([("pre", pre), ("clf", clf)]).fit(train_df[NUM + CAT], ytr)
        return pipe.predict_proba(test_df[NUM + CAT])[:, 1], name
    except Exception as e:
        return None, f"fit failed: {str(e)[:70]}"


def _row(name, r, gate=None):
    if r["acc"] is None:
        return f"| {name} | {r['n']} | {r['cov']:.0f}% | - | - | {gate or ''} |"
    return f"| {name} | {r['n']} | {r['cov']:.0f}% | {r['acc']:.1f}% | {r['lb']:.1f}% | {gate or ''} |"


def run(source="pyth", split=0.70, recent=250):
    import duckdb
    try:
        c = duckdb.connect(DB, read_only=True)
    except Exception as e:
        print("analytics.duckdb is locked by the running app -- stop the backend briefly and re-run.\n"
              f"  ({str(e)[:90]})"); return
    cols = [r[1] for r in c.execute("PRAGMA table_info('price_to_beat')").fetchall()]
    SRC = (f"COALESCE(p.source,'pyth') = '{source}'" if "source" in cols
           else ("1=1" if source == "pyth" else "1=0"))

    # ---- one row per round (last snapshot), leak-free ordering by round time ----
    base = f"""champion_snapshots cs JOIN price_to_beat p ON p.id = cs.round_id
        WHERE p.resolved AND cs.current_position IN ('UP','DOWN') AND p.actual_direction IN ('UP','DOWN')
          AND cs.p_hold IS NOT NULL AND {SRC}"""
    df = c.execute(f"""
        SELECT * FROM (
          SELECT cs.round_id, p.timestamp AS pts, cs.horizon, cs.seconds_left, cs.current_move,
                 cs.p_hold, cs.p_big_move, cs.p_big_drop, cs.p_big_up, cs.p_big_down, cs.p_activity,
                 cs.champion_confidence, cs.current_position, COALESCE(cs.regime,'UNKNOWN') regime,
                 COALESCE(cs.big_move_tier,'?') big_move_tier, COALESCE(cs.big_drop_risk,'?') big_drop_risk,
                 COALESCE(cs.activity_tier,'?') activity_tier, COALESCE(cs.champion_action,'?') champion_action,
                 CASE WHEN cs.current_position = p.actual_direction THEN 1 ELSE 0 END AS held,
                 row_number() OVER (PARTITION BY cs.round_id ORDER BY cs.seconds_left ASC) rn
          FROM {base}
        ) q WHERE rn = 1 ORDER BY pts
    """).df()

    L = [f"# champion_v2 Shadow - regime / meta-skip vs P(Hold) baseline ({source}) - {date.today().isoformat()}", ""]
    if len(df) < 400:
        L.append(f"_Insufficient deduped rounds ({len(df)} < 400). Let the app run; re-run later._")
        c.close(); _write(L); print("\n".join(L)); return

    cut = int(len(df) * max(0.5, min(0.9, split)))
    train_df, test_df = df.iloc[:cut].copy(), df.iloc[cut:].copy()
    y = test_df["held"].to_numpy(int)
    ph = test_df["p_hold"].to_numpy(float)
    reg = test_df["regime"].to_numpy(str)
    meta, meta_name = fit_meta(train_df, test_df)

    L.append(f"Rounds: {len(df):,} (one per round, last snapshot) - mean seconds_left "
             f"{df['seconds_left'].mean():.0f}s = LATE-ENTRY. Train {len(train_df):,} / Test {len(test_df):,} "
             f"(temporal, by round-time -> no leak). Meta model: {meta_name}.")
    L.append(f"Overall test held = {100*y.mean():.1f}% ; mean test p_hold = {100*ph.mean():.1f}% "
             f"(close => P(Hold) is well-calibrated and IS the signal).")

    # ---- TRACK A: acted-side-held policies ----
    L.append("\n## TRACK A - 'acted side held' (late-entry; this is P(Hold), not a fresh direction edge)")
    L.append("| policy | n | coverage | held % | Wilson-LB | note |")
    L.append("|---|---:|---:|---:|---:|---|")
    masks = {
        "baseline (act all)": np.ones(len(y), bool),
        "regime friendly (RANGE/LOW_VOL)": np.isin(reg, list(FRIENDLY)),
        "P(Hold) top25 (baseline for meta)": topk_mask(ph, 0.25),
        "P(Hold) top10": topk_mask(ph, 0.10),
    }
    if meta is not None:
        masks["meta-skip top25"] = topk_mask(meta, 0.25)
        masks["meta-skip top10"] = topk_mask(meta, 0.10)
        masks["regime + meta-skip top25"] = np.isin(reg, list(FRIENDLY)) & topk_mask(meta, 0.25)
    res = {name: eval_mask(y, m) for name, m in masks.items()}
    for name, r in res.items():
        L.append(_row(name, r))

    # ---- THE KEY DIAGNOSTIC: does meta-skip beat plain P(Hold) at matched coverage? ----
    if meta is not None:
        d25 = res["meta-skip top25"]["acc"] - res["P(Hold) top25 (baseline for meta)"]["acc"]
        d10 = res["meta-skip top10"]["acc"] - res["P(Hold) top10"]["acc"]
        L.append(f"\n**KEY: meta-skip vs plain P(Hold) at matched coverage** - top25 delta **{d25:+.1f} pts**, "
                 f"top10 delta **{d10:+.1f} pts**. "
                 + ("Meta-skip adds ~nothing over a P(Hold) threshold => it is P(Hold) re-derived, NOT a new "
                    "edge. Do not promote meta-skip as a separate head."
                    if abs(d25) < 1.5 and abs(d10) < 1.5 else
                    "Meta-skip shows a real incremental lift over P(Hold) - candidate for shadow (verify it "
                    "holds out-of-sample + recent window before any promotion)."))
    official_settlements, quoted_rounds = recorder_coverage()
    L.append("\n_Reminder: TRACK A held% is high because these are late, already-ahead snapshots. That is the "
             "known P(Hold) edge - it is NOT a fresh tradeable signal (Polymarket prices late states too). "
             f"It converts to profit ONLY if the market misprices it (analyze_pm_recorder.py: "
             f"{official_settlements} official outcomes, {quoted_rounds} joined quote rounds)._")

    # ---- TRACK B: fresh DIRECTION (the hard, tradeable question) ----
    # Apples-to-apples with regime_gate_shadow.py: TRADEABLE horizons (5m,15m) + regime-era only
    # (regime populated, excludes the pre-wiring UNKNOWN backlog). Mixing all 7 horizons / all history
    # would unfairly dilute the regime gate.
    TB = "p.resolved AND p.our_direction IN ('UP','DOWN') AND p.horizon IN (5,15) " \
         f"AND p.regime IS NOT NULL AND p.regime <> 'UNKNOWN' AND {SRC}"
    L.append("\n## TRACK B - fresh DIRECTION correct (price_to_beat, 5m+15m, regime-era - the hard question)")
    dir_rows = c.execute(f"""SELECT p.regime rg, count(*) n, sum(CASE WHEN p.hit THEN 1 ELSE 0 END) k
        FROM price_to_beat p WHERE {TB} GROUP BY 1 ORDER BY n DESC""").fetchall()
    L.append("| regime | n | direction acc % | Wilson-LB | friendly? |")
    L.append("|---|---:|---:|---:|---|")
    for rg, n, k in dir_rows:
        L.append(f"| {rg} | {n} | {100*k/n:.1f}% | {wilson_lb(k, n):.1f}% | "
                 f"{'YES' if rg in FRIENDLY else ('avoid' if rg in HOSTILE else '')} |")
    # friendly-gated direction overall + recent
    fr = c.execute(f"""SELECT count(*) n, sum(CASE WHEN p.hit THEN 1 ELSE 0 END) k
        FROM price_to_beat p WHERE {TB} AND p.regime IN ('RANGE','LOW_VOLATILITY')""").fetchone()
    frr = c.execute(f"""SELECT count(*) n, sum(CASE WHEN hit THEN 1 ELSE 0 END) k FROM (
            SELECT p.hit FROM price_to_beat p WHERE {TB} AND p.regime IN ('RANGE','LOW_VOLATILITY')
            ORDER BY p.timestamp DESC LIMIT {recent})""").fetchone()
    if fr[0]:
        L.append(f"\n**Regime gate (RANGE/LOW_VOL) on fresh direction:** overall {100*fr[1]/fr[0]:.1f}% "
                 f"(n={fr[0]}, LB {wilson_lb(fr[1], fr[0]):.1f}%) ; recent-{recent} "
                 f"{100*frr[1]/frr[0]:.1f}% (n={frr[0]}, LB {wilson_lb(frr[1], frr[0]):.1f}%).")
        gate = wilson_lb(fr[1], fr[0]) > 50 and wilson_lb(frr[1], frr[0]) > 50
        L.append(f"- Promotion gate (overall LB>50 AND recent-{recent} LB>50): "
                 + ("**PASS** - candidate to promote from shadow." if gate else
                    "**FAIL** - keep as shadow (recent window does not hold). Drift, as the external review warned."))
        # honest caveats so a marginal PASS is not overstated
        if fr[0] <= recent * 1.3:
            L.append(f"  - CAVEAT: regime-era n is only {fr[0]}, so recent-{recent} OVERLAPS overall - "
                     f"NOT an independent drift check yet. The PASS rests on a single ~{fr[0]}-round sample.")
        solo = [(rg, n, k) for rg, n, k in dir_rows if rg in FRIENDLY]
        if solo and all(wilson_lb(k, n) <= 50 for rg, n, k in solo):
            L.append("  - CAVEAT: neither friendly regime clears LB>50 ALONE ("
                     + ", ".join(f"{rg} {wilson_lb(k, n):.1f}%" for rg, n, k in solo)
                     + ") - only the POOLED set does. Marginal/fragile: keep accruing before any live wiring.")

    # ---- verdict ----
    L.append("\n## Verdict")
    L.append("- **Meta-skip is not a separate edge** if the KEY delta above is ~0 - it re-derives P(Hold). "
             "Keep P(Hold) as the single fair-value backbone; don't add a CatBoost meta head.")
    L.append("- **Regime gate** is the only policy touching the hard (fresh-direction) question. Promote ONLY "
             "if TRACK B passes overall AND recent-window (it was drifting). Until then: shadow.")
    L.append(f"- **The real ceiling-break stays gated on joined Polymarket quotes + outcomes** "
             f"({official_settlements} official outcomes, {quoted_rounds} joined quote rounds today). "
             "No TRACK-A held% is profit until the market is shown to misprice it.")
    c.close()
    _write(L); print("\n".join(L))


def _write(L):
    out = os.path.join(ROOT, "docs", "active", f"CHAMPION_V2_SHADOW_{date.today().isoformat()}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print(f"\nWrote {out}")


def selftest():
    # wilson sanity
    assert wilson_lb(90, 100) > 50 and wilson_lb(5, 100) < 50
    # eval_mask: planted
    y = np.array([1, 1, 1, 0, 0, 0, 1, 0])
    m = np.array([1, 1, 1, 0, 0, 0, 0, 0], bool)
    r = eval_mask(y, m); assert r["n"] == 3 and abs(r["acc"] - 100.0) < 1e-9, r
    r0 = eval_mask(y, np.zeros(len(y), bool)); assert r0["acc"] is None
    # topk_mask: top 25% of 8 = 2 highest
    s = np.array([0.1, 0.2, 0.9, 0.3, 0.95, 0.4, 0.5, 0.05])
    mk = topk_mask(s, 0.25); assert mk.sum() == 2 and mk[2] and mk[4], mk
    # a score correlated with y should give higher acc on its top-k than baseline
    rng = np.random.default_rng(0)
    yy = rng.integers(0, 2, 2000)
    sc = yy * 0.6 + rng.random(2000) * 0.4   # informative score
    top = eval_mask(yy, topk_mask(sc, 0.25))["acc"]
    assert top > 100 * yy.mean() + 5, (top, 100 * yy.mean())
    print("champion_v2_shadow self-test: ALL PASS (wilson / eval_mask / topk / informative-score lift)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["pyth", "binance"], default="pyth")
    ap.add_argument("--split", type=float, default=0.70)
    ap.add_argument("--recent", type=int, default=250)
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    selftest() if a.selftest else run(a.source, a.split, a.recent)
