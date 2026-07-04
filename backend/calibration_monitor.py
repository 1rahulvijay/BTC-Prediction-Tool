"""
calibration_monitor.py — live calibration drift monitor for P(hold) (Tier-1 #1).
================================================================================
Calibration, not accuracy, is what the champion and the Polymarket edge gate consume.
The live gate now caps entry fair value at the independent-entry lower bound, while this
tool measures whether the SERVED P(hold) still means what it
says — does "93%" still hold 93% of the time on REAL resolved rounds — and flags drift.

Source (read-only, safe while the backend holds the DB):
    champion_snapshots cs  ⋈  price_to_beat p  ON p.id = cs.round_id
    P(hold) prediction = cs.p_hold ;  realized "hold" = (cs.current_position == p.actual_direction)
    (the SAME join the meta-champion trains on)

Outputs a reliability table + ECE + Brier, overall and by horizon, plus a drift verdict vs the
trained calibration (P≥0.93 → ~96% claim). It does NOT change serving.

  python backend/calibration_monitor.py                 # report only (read-only)
  python backend/calibration_monitor.py --recalibrate   # also fit a fresh isotonic overlay and
                                                         #   save data/saved_models/phold_live_recal.pkl
                                                         #   (NOT auto-applied; opt-in by the serving path)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")   # Windows cp1252 can't encode ≥ / ·
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(ROOT, "data")
DB_PATH = os.path.join(DATA_DIR, "analytics.duckdb")
OUT_MD = os.path.join(ROOT, "docs", "active", f"CALIBRATION_MONITOR_{date.today().isoformat()}.md")
OVERLAY = os.path.join(DATA_DIR, "saved_models", "phold_live_recal.pkl")

# Trained claim to drift-check against (persistence head, from the 150d retrain).
TRAINED_CLAIM = {0.85: 0.921, 0.90: 0.946, 0.93: 0.963, 0.95: 0.972}
MIN_ROWS = 200                 # below this we report "insufficient" rather than mislead
DRIFT_TOL = 0.04               # realized more than 4 pts under the claim at a tier == drift
ENTRY_HORIZONS = (5, 15)
ENTRY_MIN_SECONDS = 15
ENTRY_MAX_SECONDS = 120
ENTRY_MIN_MOVE_USD = 10.0


def _load_pairs():
    """Return served snapshot rows joined to resolved rounds, or None."""
    import duckdb
    if not os.path.exists(DB_PATH):
        return None
    conn = duckdb.connect(DB_PATH, read_only=True)
    try:
        return conn.execute("""
            SELECT cs.round_id, cs.ts, cs.p_hold AS p, cs.horizon AS h,
                   cs.seconds_left, cs.current_move,
                   CASE WHEN cs.current_position = p.actual_direction THEN 1 ELSE 0 END AS y
            FROM champion_snapshots cs
            JOIN price_to_beat p ON p.id = cs.round_id
            WHERE p.resolved = TRUE
              AND cs.current_position IN ('UP', 'DOWN')
              AND p.actual_direction IN ('UP', 'DOWN')
              AND cs.p_hold IS NOT NULL
        """).df()
    except Exception as exc:
        print(f"[calib] query failed: {exc}")
        return None
    finally:
        conn.close()


def _ece(p, y, bins=10):
    p = np.asarray(p, float); y = np.asarray(y, float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for i in range(bins):
        m = (p >= edges[i]) & (p < edges[i + 1] if i < bins - 1 else p <= edges[i + 1])
        if m.sum() == 0:
            continue
        ece += (m.sum() / len(p)) * abs(float(p[m].mean()) - float(y[m].mean()))
    return round(ece, 4)


def _brier(p, y):
    p = np.asarray(p, float); y = np.asarray(y, float)
    return round(float(np.mean((p - y) ** 2)), 4)


def _reliability(p, y, edges=(0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.01)):
    p = np.asarray(p, float); y = np.asarray(y, float)
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p < hi)
        if m.sum() == 0:
            continue
        rows.append({"bin": f"[{lo:.2f},{hi:.2f})", "n": int(m.sum()),
                     "mean_pred": round(float(p[m].mean()), 3), "realized": round(float(y[m].mean()), 3)})
    return rows


def _tier_table(p, y):
    p = np.asarray(p, float); y = np.asarray(y, float)
    out = []
    for thr, claim in sorted(TRAINED_CLAIM.items()):
        m = p >= thr
        if m.sum() == 0:
            out.append({"thr": thr, "n": 0, "realized": None, "claim": claim, "drift": None}); continue
        realized = float(y[m].mean())
        out.append({"thr": thr, "n": int(m.sum()), "realized": round(realized, 3), "claim": claim,
                    "coverage": round(float(m.mean()), 3),
                    "drift": round(realized - claim, 3)})
    return out


def _verdict(tier_rows):
    bad = [t for t in tier_rows if t.get("realized") is not None and t["n"] >= 50
           and (t["claim"] - t["realized"]) > DRIFT_TOL]
    if not tier_rows or all(t["n"] < 50 for t in tier_rows):
        return "INSUFFICIENT (need more resolved high-P(hold) rounds)"
    return "DRIFT — recalibrate" if bad else "STABLE — calibration holds"


def _section(df, title):
    p, y = df["p"].to_numpy(), df["y"].to_numpy()
    tiers = _tier_table(p, y)
    L = [f"### {title}", f"n={len(df):,} · base hold-rate {y.mean()*100:.1f}% · "
         f"ECE **{_ece(p, y)}** · Brier {_brier(p, y)} · verdict: **{_verdict(tiers)}**", "",
         "| P(hold) ≥ | n | coverage | realized | trained claim | drift |",
         "|---|---:|---:|---:|---:|---:|"]
    for t in tiers:
        rl = "—" if t["realized"] is None else f"{t['realized']*100:.1f}%"
        dr = "—" if t.get("drift") is None else f"{t['drift']*100:+.1f}pt"
        cov = "—" if t.get("coverage") is None else f"{t['coverage']*100:.0f}%"
        L.append(f"| {t['thr']:.2f} | {t['n']:,} | {cov} | {rl} | {t['claim']*100:.1f}% | {dr} |")
    L.append("")
    L.append("Reliability (predicted → realized): "
             + " · ".join(f"{r['mean_pred']:.2f}→{r['realized']:.2f}(n{r['n']})"
                          for r in _reliability(p, y)))
    L.append("")
    return L


def _wilson_lb(k, n, z=1.96):
    if n <= 0:
        return None
    phat = k / n
    den = 1.0 + z * z / n
    centre = phat + z * z / (2.0 * n)
    margin = z * np.sqrt(phat * (1.0 - phat) / n + z * z / (4.0 * n * n))
    return (centre - margin) / den


def _first_qualifying_entries(df, threshold):
    """Choose the first qualifying timestamp and at most one simulated entry per round."""
    eligible = df[
        df["h"].isin(ENTRY_HORIZONS)
        & (df["p"] >= float(threshold))
        & (df["seconds_left"] > ENTRY_MIN_SECONDS)
        & (df["seconds_left"] <= ENTRY_MAX_SECONDS)
        & (df["current_move"].abs() >= ENTRY_MIN_MOVE_USD)
    ].copy()
    if eligible.empty:
        return eligible
    return (eligible.sort_values(["ts", "round_id"])
            .drop_duplicates("round_id", keep="first")
            .reset_index(drop=True))


def _entry_section(df):
    lines = ["## Decision-Level Late-Entry Scorecard (Independent Rounds)", "",
             "Each row counts at most one decision per round: the first 5m/15m snapshot that "
             f"clears P(hold), {ENTRY_MIN_SECONDS}<seconds_left<={ENTRY_MAX_SECONDS}, and "
             f"|move|>=${ENTRY_MIN_MOVE_USD:.0f}. This is side-hold precision, not profitability; "
             "market ask, taker fee, spread, depth, and fill are not included.", "",
             "| P(hold) >= | rounds | held | mean predicted | 95% Wilson lower bound |",
             "|---:|---:|---:|---:|---:|"]
    for threshold in sorted(TRAINED_CLAIM):
        entries = _first_qualifying_entries(df, threshold)
        n = len(entries)
        if n == 0:
            lines.append(f"| {threshold:.2f} | 0 | - | - | - |")
            continue
        held = float(entries["y"].mean())
        lower = _wilson_lb(int(entries["y"].sum()), n)
        lines.append(f"| {threshold:.2f} | {n:,} | {held*100:.1f}% | "
                     f"{entries['p'].mean()*100:.1f}% | {lower*100:.1f}% |")
    lines += ["", "Snapshot counts below are useful for drift diagnosis only. "
              "Do not quote them as independent bets.", ""]
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recalibrate", action="store_true",
                    help="Fit a fresh isotonic on live (p_hold, realized) and save an overlay (NOT auto-applied).")
    args = ap.parse_args()

    df = _load_pairs()
    if df is None or len(df) < MIN_ROWS:
        n = 0 if df is None else len(df)
        print(f"[calib] insufficient resolved data: {n} < {MIN_ROWS}. "
              f"Keep the app running so champion_snapshots ⋈ price_to_beat accrues resolved rounds.")
        return

    L = [f"# P(hold) Calibration Monitor — {datetime.now():%Y-%m-%d %H:%M}", "",
         "Does the SERVED P(hold) still mean what it says, on REAL resolved rounds? "
         "The champion consumes this probability but caps entry fair value at 91c. "
         "Read-only; does not change serving.", "",
         "> Snapshot rows are repeated observations inside the same round. They diagnose "
         "calibration drift but are not independent betting opportunities.", ""]
    L += _entry_section(df)
    L += _section(df, "Snapshot Diagnostics — Overall (all horizons)")
    for h in sorted(df["h"].unique()):
        sub = df[df["h"] == h]
        if len(sub) >= 100:
            L += _section(sub, f"Horizon {int(h)}m")

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))

    p, y = df["p"].to_numpy(), df["y"].to_numpy()
    print(f"[calib] n={len(df):,}  ECE={_ece(p, y)}  Brier={_brier(p, y)}  verdict={_verdict(_tier_table(p, y))}")
    for t in _tier_table(p, y):
        if t.get("realized") is not None:
            print(f"  P(hold)>={t['thr']:.2f}: realized={t['realized']*100:.1f}% vs claim {t['claim']*100:.1f}% "
                  f"(drift {t['drift']*100:+.1f}pt, n={t['n']:,})")
    print(f"[calib] wrote {OUT_MD}")

    if args.recalibrate:
        from sklearn.isotonic import IsotonicRegression
        import joblib
        iso = IsotonicRegression(out_of_bounds="clip").fit(p, y)
        joblib.dump({"iso": iso, "n": int(len(df)), "fit_at": datetime.now().isoformat(),
                     "ece_before": _ece(p, y), "ece_after": _ece(iso.predict(p), y),
                     "note": "Live P(hold) recalibration overlay. NOT auto-applied — the serving path "
                             "loads it only behind an explicit opt-in flag."}, OVERLAY)
        print(f"[calib] saved recalibration overlay -> {OVERLAY} "
              f"(ECE {_ece(p, y)} -> {_ece(iso.predict(p), y)}). Opt-in to apply; serving unchanged by default.")


if __name__ == "__main__":
    main()
