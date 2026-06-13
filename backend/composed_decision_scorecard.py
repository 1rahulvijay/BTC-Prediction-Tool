"""
composed_decision_scorecard.py — the END-TO-END decision metric (read-only, NO retrain).
=========================================================================================
Per-component metrics already exist (direction sign-truth, P(hold) calibration, grade
stratification, feature SHAP). What was MISSING is a metric for the *composed* decision:
when the tool stacks its gates into a single recommendation, does THAT recommendation win?

This script measures exactly that from data ALREADY persisted on `price_to_beat` — no
serving change, no schema bump, no model. It is the integration metric (see
docs/active/INTEGRATION_AND_METRICS.md). Run with the app STOPPED (DuckDB single-writer).

The GATE LADDER (each row adds one gate; a healthy composition is MONOTONE — win-rate and
Wilson lower bound rise as gates are added, and the top tier clears the bettable threshold):
  L0  all committed leans (our_direction UP/DOWN)            — the raw "always bet a side"
  L1  + model lean only (lean_source = 'model')              — drop the coin-flip fallback
  L2  + actionable (the B2 conviction gate)                  — drop the gated-out coin-flips
  L3  + late_entry  (the ⚡ / P(hold) >= 0.93 tier)          — the proven high-precision subset

Plus the grade stratification (A/B/C) with a monotonicity check — flags the §5br inversion.

HONEST framing: a Polymarket binary near 0.50 is break-even at ~50% (≈52% after spread/
slippage). A tier is "bettable" only when its Wilson 95% LOWER bound clears that threshold —
a point estimate of 60% on n=10 is not an edge. The headline is the highest-gate tier's LB.

Usage:  python backend/composed_decision_scorecard.py            (reads the live DB, app stopped)
        python backend/composed_decision_scorecard.py --selftest (synthetic; no DB)
"""
import math
import os
import sys

HORIZONS = [1, 3, 5, 7, 10, 15]
BREAKEVEN = float(os.environ.get("BTC_BET_BREAKEVEN", "0.52"))  # ~50% + spread/slippage
REAL_MARKETS = {5, 15}  # 1/3/7/10 are practice mirrors


def wilson_lb(wins: int, n: int, z: float = 1.96) -> float:
    """Wilson score 95% LOWER bound for a binomial proportion. Small-n honest: a lucky
    3/4 yields a low bound, so it can't masquerade as an edge. Monotone up in n at fixed p."""
    if n <= 0:
        return 0.0
    p = wins / n
    denom = 1.0 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (center - margin) / denom)


def _stat(rows: list) -> dict:
    """rows: list of dicts each with a boolean 'hit'. Returns n / wins / winrate / wilson-LB."""
    n = len(rows)
    wins = sum(1 for r in rows if r.get("hit"))
    return {"n": n, "wins": wins,
            "winrate": (wins / n if n else None),
            "lb": wilson_lb(wins, n)}


def summarize(rows: list, breakeven: float = BREAKEVEN) -> dict:
    """PURE core (unit-testable): given resolved committed-lean price_to_beat rows for ONE
    horizon, compute the gate ladder + grade stratification + the composed-decision verdict.

    Each row dict: our_direction, lean_source, actionable(bool), confluence_grade, late_entry(bool), hit(bool).
    """
    committed = [r for r in rows if r.get("our_direction") in ("UP", "DOWN")]
    model = [r for r in committed if (r.get("lean_source") or "model") == "model"]
    actionable = [r for r in model if r.get("actionable")]
    late = [r for r in model if r.get("late_entry")]

    ladder = [
        ("L0 all committed leans", _stat(committed)),
        ("L1 + model lean only", _stat(model)),
        ("L2 + actionable (B2 gate)", _stat(actionable)),
        ("L3 + late-entry (P(hold) tier)", _stat(late)),
    ]

    # Grade stratification among model leans (the §5br inversion check).
    grades = {}
    for g in ("A", "B", "C"):
        gr = [r for r in model if (r.get("confluence_grade") or "") == g]
        if gr:
            grades[g] = _stat(gr)

    # Monotonicity of the ladder's Wilson-LB across tiers that have enough n (>=30).
    lbs = [s["lb"] for _, s in ladder if s["n"] >= 30]
    ladder_monotone = all(lbs[i] <= lbs[i + 1] + 1e-9 for i in range(len(lbs) - 1)) if len(lbs) >= 2 else None

    # Grade monotonicity A>=B>=C on win-rate (the inversion test); needs each n>=100 to trust.
    grade_ok = None
    if all(g in grades for g in ("A", "B", "C")):
        a, b, c = grades["A"], grades["B"], grades["C"]
        enough = all(x["n"] >= 100 for x in (a, b, c))
        monotone = (a["winrate"] or 0) >= (b["winrate"] or 0) >= (c["winrate"] or 0)
        a_beats_c = a["lb"] > (c["winrate"] or 0)
        grade_ok = bool(enough and monotone and a_beats_c)

    # The composed-decision headline: the highest-gate tier with a usable sample (>=30).
    top = None
    for label, s in reversed(ladder):
        if s["n"] >= 30:
            top = (label, s)
            break
    bettable = bool(top and top[1]["lb"] >= breakeven)

    return {"ladder": ladder, "grades": grades, "ladder_monotone": ladder_monotone,
            "grade_ok": grade_ok, "top": top, "bettable": bettable, "breakeven": breakeven}


# ───────────────────────── live DB reporting ──────────────────────────────────────────
def _fetch(conn, horizon: int, era_ts: int) -> list:
    rows = conn.execute(f"""
        SELECT our_direction, COALESCE(lean_source,'model') lean_source,
               COALESCE(actionable,FALSE) actionable, COALESCE(confluence_grade,'') confluence_grade,
               COALESCE(late_entry,FALSE) late_entry, hit
        FROM price_to_beat
        WHERE horizon={horizon} AND resolved AND our_direction IN ('UP','DOWN')
          AND hit IS NOT NULL AND timestamp >= {era_ts}
    """).fetchall()
    return [{"our_direction": r[0], "lean_source": r[1], "actionable": bool(r[2]),
             "confluence_grade": r[3], "late_entry": bool(r[4]), "hit": bool(r[5])} for r in rows]


def _print_horizon(h: int, s: dict):
    tag = "REAL market" if h in REAL_MARKETS else "practice mirror"
    print(f"\n  {h}m  ({tag})")
    print(f"  {'tier':<34}{'n':>6}{'win%':>8}{'wilsonLB':>10}")
    for label, st in s["ladder"]:
        if not st["n"]:
            print(f"  {label:<34}{'—':>6}")
            continue
        wr = f"{st['winrate']*100:.1f}%"
        lb = f"{st['lb']*100:.1f}%"
        mark = "  <= top tier" if s["top"] and s["top"][0] == label else ""
        print(f"  {label:<34}{st['n']:>6}{wr:>8}{lb:>10}{mark}")
    if s["grades"]:
        gtxt = "  ".join(f"{g}:{st['wins']}/{st['n']}({(st['winrate'] or 0)*100:.0f}%,LB{st['lb']*100:.0f})"
                         for g, st in s["grades"].items())
        verdict = ("OK (A>=B>=C, each n>=100, A-LB>C)" if s["grade_ok"]
                   else "INVERTED/UNPROVEN — do NOT trust grade letters (see §5br)")
        print(f"  grade strat: {gtxt}   -> {verdict}")
    if s["top"]:
        label, st = s["top"]
        verdict = (f"BETTABLE — LB {st['lb']*100:.1f}% clears break-even {s['breakeven']*100:.0f}%"
                   if s["bettable"] else
                   f"NOT yet bettable — LB {st['lb']*100:.1f}% < break-even {s['breakeven']*100:.0f}% (need more n or more edge)")
        print(f"  COMPOSED DECISION ({label.strip()}): {verdict}")


def main():
    import duckdb
    sys.path.insert(0, os.path.dirname(__file__))
    from database import DB_PATH
    try:
        conn = duckdb.connect(DB_PATH, read_only=True)
    except Exception as e:
        print("DB is locked (the live app holds the single-writer lock). Stop the app and "
              f"rerun this script — it is part of the app-stopped morning checklist. ({e})")
        sys.exit(1)

    era_ts = 0
    try:
        vp = os.path.join(os.path.dirname(DB_PATH), "saved_models", "architecture_version.pkl")
        if os.path.exists(vp):
            era_ts = int(os.path.getmtime(vp) * 1000)
            import datetime as _dt
            print(f"[era] scoring rows since model save: {_dt.datetime.fromtimestamp(era_ts/1000)}")
    except Exception:
        pass

    print("=" * 74)
    print("COMPOSED-DECISION SCORECARD — does stacking the gates actually improve the bet?")
    print(f"(break-even {BREAKEVEN*100:.0f}% = ~coin-flip + spread; a tier is bettable only when its")
    print(" Wilson 95% LOWER bound clears it. Practice mirrors shown for context, not betting.)")
    print("=" * 74)
    for h in HORIZONS:
        try:
            rows = _fetch(conn, h, era_ts)
            _print_horizon(h, summarize(rows))
        except Exception as e:
            print(f"  {h}m: {e}")
    conn.close()
    print("\nDONE")


# ───────────────────────── self-test (no DB) ──────────────────────────────────────────
def _selftest():
    # Wilson sanity: small-n can't fake an edge; LB rises with n at fixed p.
    assert wilson_lb(3, 4) < 0.75, "small-n LB should be well below the point estimate"
    assert wilson_lb(60, 100) < wilson_lb(600, 1000), "LB should tighten upward with n"
    assert abs(wilson_lb(0, 0)) < 1e-9

    # Synthetic horizon: baseline coin-flip, gates progressively better, late-entry ~95%.
    rows = []
    def add(n, wins, **kw):
        for i in range(n):
            rows.append({"our_direction": "UP", "lean_source": kw.get("lean_source", "model"),
                         "actionable": kw.get("actionable", False),
                         "confluence_grade": kw.get("grade", ""),
                         "late_entry": kw.get("late_entry", False),
                         "hit": i < wins})
    # fallback leans ~ coin-flip (should be dropped by L1)
    add(100, 50, lean_source="fallback")
    # model, non-actionable, ~52%
    add(200, 104, lean_source="model", actionable=False)
    # model, actionable, ~58%
    add(150, 87, lean_source="model", actionable=True)
    # model, actionable, late-entry ~95%
    add(80, 76, lean_source="model", actionable=True, late_entry=True)
    s = summarize(rows)
    lad = {lbl.split()[0]: st for lbl, st in s["ladder"]}
    assert lad["L0"]["n"] == 530 and lad["L1"]["n"] == 430, "fallback must drop at L1"
    assert lad["L3"]["winrate"] > lad["L2"]["winrate"] > lad["L1"]["winrate"], "ladder should improve"
    assert s["top"][0].startswith("L3") and s["bettable"], "late-entry tier should be the bettable top"

    # Inverted-grade synthetic (the §5br shape): A worst, B best.
    g = []
    for i in range(200):
        g.append({"our_direction": "UP", "lean_source": "model", "actionable": True,
                  "confluence_grade": "A", "late_entry": False, "hit": i < 88})   # 44%
    for i in range(200):
        g.append({"our_direction": "UP", "lean_source": "model", "actionable": True,
                  "confluence_grade": "B", "late_entry": False, "hit": i < 114})  # 57%
    for i in range(200):
        g.append({"our_direction": "UP", "lean_source": "model", "actionable": True,
                  "confluence_grade": "C", "late_entry": False, "hit": i < 100})  # 50%
    sg = summarize(g)
    assert sg["grade_ok"] is False, "inverted grade (A<C<B) must NOT pass the stratification gate"
    print("composed_decision_scorecard self-test: ALL PASS")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
