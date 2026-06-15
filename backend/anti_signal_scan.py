"""
anti_signal_scan.py — the anti-signal / fade-candidate detector (read-only, no-train).
=======================================================================================
Codex enhancement: a model becomes useful IN REVERSE when a cell has a STABLE negative edge.
This scans (regime × horizon) committed sign-truth and flags cells where the model is
RELIABLY WRONG — Wilson 95% UPPER bound below coin-flip — i.e. inverting the call would be
reliably right. It LOGS candidates only (Codex rule: do NOT auto-invert; a stable negative
edge can be regime-specific noise — promote a fade only after it holds over enough samples).

Reads `predictions_{h}m` (raw_direction vs realized sign), model-era filtered. App stopped.
Usage:  python backend/anti_signal_scan.py
        python backend/anti_signal_scan.py --selftest
"""
import math
import os
import sys

HORIZONS = (1, 3, 5, 7, 10, 15, 30)
MIN_N = 50          # need a real sample before calling a cell anti-predictive
FADE_UB = 0.50      # Wilson UPPER bound must be below this (confidently below coin-flip)
FADE_PT = 0.45      # and the point estimate clearly below


def _wilson(wins, n, z=1.96, upper=False):
    if n <= 0:
        return 0.0
    p = wins / n
    center = (p + z * z / (2 * n)) / (1 + z * z / n)
    margin = (z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)) / (1 + z * z / n)
    return min(1.0, center + margin) if upper else max(0.0, center - margin)


def find_anti_signals(cells):
    """PURE core. cells: list of {horizon, regime, n, wins}. Returns per-cell stats + the
    fade-candidate flag (reliably wrong = invert candidate)."""
    out = []
    for c in cells:
        n, w = c["n"], c["wins"]
        acc = w / n if n else 0.0
        ub = _wilson(w, n, upper=True)
        fade = n >= MIN_N and ub < FADE_UB and acc < FADE_PT
        out.append({"horizon": c["horizon"], "regime": c["regime"], "n": n,
                    "acc": round(acc, 3), "wilson_ub": round(ub, 3), "fade_candidate": fade})
    out.sort(key=lambda r: (r["horizon"], r["acc"]))
    return out


def main():
    import duckdb
    sys.path.insert(0, os.path.dirname(__file__))
    from database import DB_PATH
    try:
        conn = duckdb.connect(DB_PATH, read_only=True)
    except Exception as e:
        sys.exit(f"DB locked — stop the app and rerun ({str(e)[:70]})")
    era = 0
    try:
        vp = os.path.join(os.path.dirname(DB_PATH), "saved_models", "architecture_version.pkl")
        if os.path.exists(vp):
            era = int(os.path.getmtime(vp) * 1000)
    except Exception:
        pass
    cells = []
    for h in HORIZONS:
        try:
            rows = conn.execute(f"""
                SELECT regime, COUNT(*) n,
                       SUM(CASE WHEN (raw_direction='UP' AND actual_move>0)
                                  OR (raw_direction='DOWN' AND actual_move<0) THEN 1 ELSE 0 END) wins
                FROM predictions_{h}m
                WHERE resolved AND raw_direction IN ('UP','DOWN') AND actual_move IS NOT NULL
                  AND timestamp >= {era}
                GROUP BY regime
            """).fetchall()
            for rg, n, w in rows:
                cells.append({"horizon": h, "regime": rg, "n": int(n), "wins": int(w or 0)})
        except Exception as e:
            print(f"  {h}m: {e}")
    conn.close()
    res = find_anti_signals(cells)
    print("=" * 72)
    print("ANTI-SIGNAL SCAN — (regime × horizon) cells, FADE = reliably wrong (invert?)")
    print("=" * 72)
    fades = [r for r in res if r["fade_candidate"]]
    for r in res:
        flag = "  <<< FADE CANDIDATE (model reliably WRONG — log, don't auto-invert)" if r["fade_candidate"] else ""
        print(f"  {r['horizon']:>2}m {r['regime']:<16} n={r['n']:<5} acc={r['acc']*100:4.1f}%  "
              f"UB={r['wilson_ub']*100:4.1f}%{flag}")
    print(f"\n{len(fades)} fade candidate(s). A fade is promotable only if it stays anti-predictive "
          f"over more samples AND in >=2 windows (Codex). Until then: SILENCE the cell, don't invert.")


def selftest():
    cells = [
        {"horizon": 5, "regime": "TRENDING_DOWN", "n": 200, "wins": 70},   # 35% — reliably wrong
        {"horizon": 5, "regime": "LOW_VOLATILITY", "n": 200, "wins": 100},  # 50% — coin-flip
        {"horizon": 5, "regime": "RANGE", "n": 10, "wins": 2},              # 20% but n too small
        {"horizon": 10, "regime": "TRENDING_UP", "n": 150, "wins": 90},     # 60% — good, not fade
    ]
    res = find_anti_signals(cells)
    by = {(r["regime"]): r for r in res}
    assert by["TRENDING_DOWN"]["fade_candidate"] is True, "35%/n200 should be a fade candidate"
    assert by["LOW_VOLATILITY"]["fade_candidate"] is False, "coin-flip must not be a fade"
    assert by["RANGE"]["fade_candidate"] is False, "n=10 too small to call"
    assert by["TRENDING_UP"]["fade_candidate"] is False, "60% is good, not a fade"
    # Wilson UB sanity: small-n can't confidently claim 'reliably wrong'
    assert _wilson(2, 10, upper=True) > 0.50
    print("anti_signal_scan self-test: ALL PASS")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
