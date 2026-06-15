"""
phold_tier_scorecard.py — the T2/T3 precision-tier proof + signal lifecycle (read-only, no-train).
====================================================================================================
Turns the §5bw P(hold) validation into a repeatable PROOF PANEL and the lifecycle/half-life
measurement (Codex enhancements #2/#3), from `persistence_snapshot` ⋈ resolved `price_to_beat`.
No app changes, no retrain; run with the app STOPPED (DuckDB single-writer) or via a future API.

Three sections:
  1. T2/T3 TIER PROOF — per horizon: the late-entry structural zone (hold%, n, Wilson-LB) and, where
     `p_hold` is recorded (post-2026-06-14 rows), the calibrated P(hold)≥0.93 tier. This is the
     evidence a T3 card must show before it is trusted (n≥100, observed≥90%, Wilson-LB≥80%).
  2. HALF-LIFE — hold rate by seconds_left: the edge STRENGTHENS toward the close (the persistence
     half-life). Quantifies "wait for late-entry" vs "act early".
  3. SIGNAL STABILITY (lifecycle) — per round, how often the side (position) FLIPS intra-window; a
     non-flipping (stable) position is the higher-precision setup.

Writes `data/phold_tier.json` (the proof panel) for later UI consumption.
Usage:  python backend/phold_tier_scorecard.py          (reads the live DB, app stopped)
        python backend/phold_tier_scorecard.py --selftest
"""
import json
import math
import os
import sys

HORIZONS = (1, 3, 5, 7, 10, 15, 30)
REAL_MARKETS = {5, 15}
DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(__file__)), "data")
OUT_PATH = os.path.join(DATA_DIR, "phold_tier.json")


def wilson_lb(wins, n, z=1.96):
    if n <= 0:
        return 0.0
    p = wins / n
    return max(0.0, (p + z * z / (2 * n) - z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)) / (1 + z * z / n))


def _late_win(h):
    return min(120, int(h * 60 * 0.4))


def _min_ahead(h):
    return 10.0 if h >= 5 else 5.0


def tier_stats(rows):
    """PURE core. rows: list of dicts {horizon, seconds_left, abs_dist, held(0/1), p_hold(or None),
    round_id}. Returns the T2/T3 tier proof, the half-life curve, and the flip-stability summary."""
    out = {"tiers": {}, "half_life": {}, "stability": {}}
    by_h = {}
    for r in rows:
        by_h.setdefault(r["horizon"], []).append(r)

    for h, rs in sorted(by_h.items()):
        lw, ma = _late_win(h), _min_ahead(h)
        # T2 = structural late-entry zone; T3 = + P(hold) >= 0.93 (where recorded)
        t2 = [r for r in rs if 15 < r["seconds_left"] <= lw and r["abs_dist"] >= ma]
        t3 = [r for r in t2 if r.get("p_hold") is not None and r["p_hold"] >= 0.93]
        def stat(g):
            n = len(g); w = sum(r["held"] for r in g)
            return {"n": n, "hold_pct": round(100 * w / n, 1) if n else None,
                    "wilson_lb": round(100 * wilson_lb(w, n), 1)}
        out["tiers"][h] = {"T2_structural": stat(t2), "T3_phold>=0.93": stat(t3),
                           "real_market": h in REAL_MARKETS}

    # half-life: hold rate by seconds_left bucket (all snapshots that are AHEAD, |dist|>=min)
    for h, rs in sorted(by_h.items()):
        ma = _min_ahead(h)
        ahead = [r for r in rs if r["abs_dist"] >= ma]
        curve = {}
        for lo, hi in ((0, 15), (15, 30), (30, 60), (60, 120), (120, 10 ** 9)):
            g = [r for r in ahead if lo <= r["seconds_left"] < hi]
            if len(g) >= 30:
                w = sum(r["held"] for r in g)
                curve[f"{lo}-{hi if hi < 10 ** 9 else '+'}s"] = {"n": len(g), "hold_pct": round(100 * w / len(g), 1)}
        out["half_life"][h] = curve

    # stability: per round_id, did the position flip across its snapshots?
    by_round = {}
    for r in rows:
        by_round.setdefault(r["round_id"], []).append(r)
    flipped = stable = 0
    stable_held = stable_n = flip_held = flip_n = 0
    for rid, rs in by_round.items():
        positions = {r.get("position") for r in rs if r.get("position")}
        held = rs[-1]["held"]  # round outcome (same for all snaps of a round)
        if len(positions) > 1:
            flipped += 1; flip_n += 1; flip_held += held
        else:
            stable += 1; stable_n += 1; stable_held += held
    out["stability"] = {
        "rounds": flipped + stable, "flipped": flipped, "stable": stable,
        "flip_rate_pct": round(100 * flipped / (flipped + stable), 1) if (flipped + stable) else None,
        "stable_hold_pct": round(100 * stable_held / stable_n, 1) if stable_n else None,
        "flipped_hold_pct": round(100 * flip_held / flip_n, 1) if flip_n else None,
    }
    return out


# ───────────────────────── live DB ──────────────────────────────────────────────────
def _load_rows(conn):
    rows = conn.execute("""
        SELECT s.round_id, s.horizon, s.seconds_left, s.distance, s.position,
               COALESCE(s.p_hold, NULL) p_hold, p.price_to_beat anchor, p.actual_direction
        FROM persistence_snapshot s JOIN price_to_beat p ON s.round_id = p.id
        WHERE p.resolved AND p.actual_direction IN ('UP','DOWN')
          AND s.position IN ('UP','DOWN') AND p.price_to_beat > 0
    """).fetchall()
    out = []
    for rid, h, sl, dist, pos, ph, anchor, act in rows:
        out.append({"round_id": rid, "horizon": int(h), "seconds_left": int(sl),
                    "abs_dist": abs(float(dist)), "position": pos,
                    "p_hold": (float(ph) if ph is not None else None),
                    "held": 1 if pos == act else 0})
    return out


def main():
    import duckdb
    sys.path.insert(0, os.path.dirname(__file__))
    from database import DB_PATH
    try:
        conn = duckdb.connect(DB_PATH, read_only=True)
    except Exception as e:
        sys.exit(f"DB locked — stop the app and rerun ({str(e)[:70]})")
    rows = _load_rows(conn)
    conn.close()
    if not rows:
        sys.exit("no resolved persistence snapshots yet.")
    res = tier_stats(rows)
    print("=" * 74)
    print(f"P(HOLD) T2/T3 PRECISION-TIER PROOF  ({len(rows):,} resolved snapshots)")
    print("=" * 74)
    for h in HORIZONS:
        t = res["tiers"].get(h)
        if not t:
            continue
        tag = "REAL" if t["real_market"] else "practice"
        s2, s3 = t["T2_structural"], t["T3_phold>=0.93"]
        print(f"  {h}m ({tag}): T2 late-entry hold={s2['hold_pct']}% n={s2['n']} LB={s2['wilson_lb']}%"
              + (f"  |  T3 P(hold)>=.93 hold={s3['hold_pct']}% n={s3['n']} LB={s3['wilson_lb']}%"
                 if s3["n"] else "  |  T3: no p_hold rows yet (accrues post-restart)"))
    print("\nHALF-LIFE (hold% by seconds_left — edge strengthens toward close):")
    for h in (5, 15):
        c = res["half_life"].get(h) or {}
        if c:
            print(f"  {h}m: " + "  ".join(f"{k}={v['hold_pct']}%(n{v['n']})" for k, v in c.items()))
    st = res["stability"]
    print(f"\nSIGNAL STABILITY: {st['rounds']} rounds | flip-rate {st['flip_rate_pct']}% | "
          f"stable-side hold {st['stable_hold_pct']}% vs flipped {st['flipped_hold_pct']}% "
          f"(stable positions are the higher-precision setup)")
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=2)
    print(f"\nProof panel -> {OUT_PATH}")
    print("T3 gate to surface a card: n>=100 AND hold>=90% AND Wilson-LB>=80%.")


def selftest():
    rows = []
    # 5m: build late-entry snapshots that HOLD ~95%, plus early/far ones that hold less.
    for i in range(400):
        rows.append({"round_id": f"r{i}", "horizon": 5, "seconds_left": 40, "abs_dist": 30.0,
                     "position": "UP", "p_hold": 0.95, "held": 1 if i % 20 else 0})   # ~95%
    for i in range(200):
        rows.append({"round_id": f"e{i}", "horizon": 5, "seconds_left": 200, "abs_dist": 4.0,
                     "position": "UP", "p_hold": 0.5, "held": i % 2})                  # ~50%, not in T2
    res = tier_stats(rows)
    t = res["tiers"][5]
    assert t["T2_structural"]["n"] == 400, f"T2 n {t['T2_structural']['n']}"
    assert t["T2_structural"]["hold_pct"] >= 90, t["T2_structural"]["hold_pct"]
    assert t["T3_phold>=0.93"]["n"] == 400 and t["T3_phold>=0.93"]["wilson_lb"] >= 80
    assert wilson_lb(95, 100) < 95 / 100  # LB below point estimate
    # stability: add a flipping round
    rows2 = [{"round_id": "f1", "horizon": 5, "seconds_left": 100, "abs_dist": 20, "position": "UP", "p_hold": None, "held": 0},
             {"round_id": "f1", "horizon": 5, "seconds_left": 20, "abs_dist": 20, "position": "DOWN", "p_hold": None, "held": 0},
             {"round_id": "s1", "horizon": 5, "seconds_left": 100, "abs_dist": 20, "position": "UP", "p_hold": None, "held": 1},
             {"round_id": "s1", "horizon": 5, "seconds_left": 20, "abs_dist": 20, "position": "UP", "p_hold": None, "held": 1}]
    st = tier_stats(rows2)["stability"]
    assert st["flipped"] == 1 and st["stable"] == 1 and st["flip_rate_pct"] == 50.0
    print("phold_tier_scorecard self-test: ALL PASS")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        main()
