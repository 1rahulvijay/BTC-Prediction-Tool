"""
probe_feature_parity.py - does the model SEE live the same features it was TRAINED on? (rule #3)
=================================================================================================
The model's feature ORDER is parity-locked (schema_hash). This probes the harder gap: feature
VALUES. If a feature varies in the offline training matrix but is CONSTANT (dead) in the live vector,
the model trained on something it never actually receives -> degraded live predictions that read as
"something's wrong" while every line of the pipeline is individually correct.

Source of truth:
  * LIVE  = `feature_outcome_log` (server.py logs seq[-1] in FEATURE_NAMES order, ONLY when the
            order-flow feed is alive -> this is the BEST-CASE live distribution; if even this diverges,
            it's a real computation mismatch, not just a feed dropout).
  * OFFLINE = research_matrix_1m.parquet (the same feature names, computed the training way).

DECISIVE flag = DEAD-IN-LIVE: live std ~ 0 while offline std > 0 (the feature is not being produced
live). DIVERGENT (mean/std gap) is ADVISORY only -- it can also be a regime difference, not a bug.

Read-only. Reads analytics.duckdb (app-stopped: DuckDB single-writer) + the matrix parquet (always OK).

Usage:  python backend/probe_feature_parity.py   |   python backend/probe_feature_parity.py --selftest
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(os.path.dirname(HERE), "data")
MATRIX = os.path.join(DATA_DIR, "research_matrix_1m.parquet")
MIN_LIVE = 200          # need this many live rows for a meaningful distribution
DEAD_ABS = 1e-9         # |std| below this == constant
DIVERGE_Z = 1.5         # |live_mean - offline_mean| / offline_std above this == advisory-divergent


def compare(live_df: pd.DataFrame, offline_df: pd.DataFrame, names: list[str]) -> list[dict]:
    """PURE core. Per-feature live-vs-offline distribution check over the shared feature names."""
    rows = []
    common = [c for c in names if c in live_df.columns and c in offline_df.columns]
    for c in common:
        lv = pd.to_numeric(live_df[c], errors="coerce").to_numpy()
        ov = pd.to_numeric(offline_df[c], errors="coerce").to_numpy()
        lv = lv[np.isfinite(lv)]; ov = ov[np.isfinite(ov)]
        if len(lv) < 30 or len(ov) < 30:
            continue
        lm, ls = float(lv.mean()), float(lv.std())
        om, os_ = float(ov.mean()), float(ov.std())
        dead_live = (ls <= DEAD_ABS) and (os_ > DEAD_ABS)      # DECISIVE parity break
        z = abs(lm - om) / (os_ + 1e-12)
        std_ratio = ls / (os_ + 1e-12)
        if dead_live:
            verdict = "DEAD-IN-LIVE **parity break**"
        elif os_ <= DEAD_ABS and ls > DEAD_ABS:
            verdict = "dead-offline/live-only (check)"
        elif z > DIVERGE_Z or std_ratio < 0.3 or std_ratio > 3.0:
            verdict = "divergent (advisory)"
        else:
            verdict = "ok"
        rows.append({"feat": c, "live_mean": lm, "live_std": ls, "off_mean": om, "off_std": os_,
                     "z": z, "std_ratio": std_ratio, "verdict": verdict})
    return rows


def _load_live(conn, schema_hash, names):
    rows = conn.execute(
        "SELECT features FROM feature_outcome_log WHERE schema_hash = ? AND features IS NOT NULL",
        [schema_hash]).fetchall()
    vecs = [r[0] for r in rows if r[0] is not None and len(r[0]) == len(names)]
    if not vecs:
        return pd.DataFrame(), len(rows)
    return pd.DataFrame(np.asarray(vecs, dtype=float), columns=names), len(rows)


def main():
    import duckdb
    from database import DB_PATH
    from features import FEATURE_NAMES, get_feature_schema
    sh = get_feature_schema()["schema_hash"]
    if not os.path.exists(MATRIX):
        print(f"ERROR: offline matrix not found: {MATRIX}"); return
    try:
        conn = duckdb.connect(DB_PATH, read_only=True)
    except Exception as e:
        print(f"analytics.duckdb is locked by the running app ({str(e)[:70]}).\n"
              f"  Stop the app briefly and rerun (it reads the live feature_outcome_log).")
        return
    try:
        live_df, n_raw = _load_live(conn, sh, FEATURE_NAMES)
    finally:
        conn.close()
    print("=" * 92)
    print(f"FEATURE VALUE PARITY  (schema {sh}; live rows matching this schema: {len(live_df):,} of {n_raw:,})")
    print("=" * 92)
    if len(live_df) < MIN_LIVE:
        print(f"INSUFFICIENT live rows ({len(live_df)} < {MIN_LIVE}). The app logs one vector/cycle only when the "
              f"order-flow feed is alive — let it run, then rerun (app stopped).")
        return
    offline_df = pd.read_parquet(MATRIX).replace([np.inf, -np.inf], np.nan)
    res = compare(live_df, offline_df, FEATURE_NAMES)
    dead = [r for r in res if "parity break" in r["verdict"]]
    div = [r for r in res if r["verdict"].startswith("divergent")]
    print(f"compared {len(res)} shared features -> {len(dead)} DEAD-IN-LIVE, {len(div)} divergent(advisory)\n")
    print(f"  {'feature':<26}{'live_mean':>12}{'live_std':>11}{'off_mean':>12}{'off_std':>11}{'z':>7}  verdict")
    for r in sorted(res, key=lambda x: (0 if "parity" in x["verdict"] else 1 if "divergent" in x["verdict"] else 2, -x["z"])):
        if r["verdict"] == "ok":
            continue
        print(f"  {r['feat']:<26}{r['live_mean']:>12.4g}{r['live_std']:>11.4g}"
              f"{r['off_mean']:>12.4g}{r['off_std']:>11.4g}{r['z']:>7.2f}  {r['verdict']}")
    print()
    if dead:
        print(f"*** {len(dead)} DEAD-IN-LIVE feature(s) — the model was TRAINED on these but receives a CONSTANT")
        print("    live: a real parity break. Fix the live computation (or retire the feature). ***")
    else:
        print("No DEAD-IN-LIVE features: the live vector reproduces the trained features (best-case, feed alive).")
        print("Divergent(advisory) rows may just be recent-regime vs 360d-average, not a bug — judge by eye.")


def selftest():
    names = ["a", "b", "cvd_1m", "vpin"]
    rng = np.random.default_rng(0)
    off = pd.DataFrame({"a": rng.normal(0, 1, 1000), "b": rng.normal(5, 2, 1000),
                        "cvd_1m": rng.normal(0, 10, 1000), "vpin": rng.uniform(0, 1, 1000)})
    live = pd.DataFrame({"a": rng.normal(0, 1, 500),                 # ok
                         "b": rng.normal(5, 2, 500),                 # ok
                         "cvd_1m": np.zeros(500),                    # DEAD-IN-LIVE (feed not computed)
                         "vpin": rng.uniform(0, 1, 500)})            # ok
    res = compare(live, off, names)
    by = {r["feat"]: r for r in res}
    assert "parity break" in by["cvd_1m"]["verdict"], by["cvd_1m"]
    assert by["a"]["verdict"] == "ok" and by["vpin"]["verdict"] == "ok", by
    print("probe_feature_parity self-test: DEAD-IN-LIVE detected on zeroed cvd_1m, live features OK. ALL PASS")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
