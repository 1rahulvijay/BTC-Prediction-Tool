"""
test_head_calibration.py — are the app's deployed probabilities actually calibrated? (Oracle, 21d)
===================================================================================================
Priority-1 test. Every strategy the app could ever run consumes these heads. If P(Hold) says 95%
and reality is 88%, then every downstream edge calculation is biased before a single trade - and
no amount of strategy work fixes it.

Joins each deployed head's LIVE predictions to the officially resolved outcome of the same round:

  P(Hold)        persistence_snapshot.p_hold        -> did the side it was quoted on finish ahead?
  flip risk      round_state_snapshots.flip_risk    -> did the leader actually flip by settlement?
  late shock     late_shock_20/50/100               -> did BTC move >= that much before expiry?
  champion tier  round_state_snapshots.action       -> does WAIT/AVOID/PAPER stratify monotonically?

Reported per head: decile reliability (predicted vs realized), Brier score, ECE, and the
Brier skill score against the base rate (a head that cannot beat "always predict the base rate"
carries no usable information, however good its AUC looks).

Independence: ONE observation per round per head (the last snapshot in the head's valid window),
never per tick - pooling ticks would inflate n by ~13x and manufacture false confidence.

Read-only against the Oracle copies. ASCII output. CPU-polite.
Usage: python backend/research/test_head_calibration.py
"""
from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "2")

import sys
from datetime import date

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ORACLE = os.path.join(ROOT, "btc_full_project", "btc-tool", "data")
OUT_MD = os.path.join(ROOT, "docs", "active", f"HEAD_CALIBRATION_{date.today().isoformat()}.md")


def metrics(p, y):
    """Brier, ECE (10 equal-width bins), and Brier skill vs the base rate."""
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    brier = float(np.mean((p - y) ** 2))
    base = float(np.mean(y))
    brier_base = float(np.mean((base - y) ** 2))
    bss = 1.0 - brier / brier_base if brier_base > 0 else float("nan")
    edges = np.linspace(0, 1, 11)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, 9)
    ece = 0.0
    for b in range(10):
        m = idx == b
        if m.sum():
            ece += (m.sum() / len(p)) * abs(p[m].mean() - y[m].mean())
    return brier, ece, bss, base


def reliability(p, y, L, lo=0.0, hi=1.0, step=0.1):
    L += ["| predicted band | n | mean predicted | realized | gap |", "|---|---|---|---|---|"]
    p = np.asarray(p, float)
    y = np.asarray(y, float)
    edges = np.arange(lo, hi + 1e-9, step)
    for a, b in zip(edges[:-1], edges[1:]):
        m = (p >= a) & (p < b) if b < hi else (p >= a) & (p <= b)
        if m.sum() < 25:
            L.append(f"| {a:.0%}-{b:.0%} | {int(m.sum())} (too few) | - | - | - |")
            continue
        pm, ym = p[m].mean(), y[m].mean()
        flag = " **<-**" if abs(pm - ym) > 0.05 else ""
        L.append(f"| {a:.0%}-{b:.0%} | {int(m.sum()):,} | {pm:.1%} | {ym:.1%} | "
                 f"{ym - pm:+.1%}{flag} |")


def main():
    import duckdb
    ana = os.path.join(ORACLE, "analytics.duckdb")
    if not os.path.exists(ana):
        print(f"missing {ana}")
        return
    con = duckdb.connect(ana, read_only=True)

    L = [f"# Deployed-head live calibration ({date.today().isoformat()})", "",
         "Every strategy consumes these probabilities. If they are biased, every downstream edge "
         "calculation is wrong before a trade is placed. Joined to officially resolved outcomes over "
         "the 21-day Oracle deployment. **One observation per round per head** (never per tick - "
         "pooling ticks would inflate n ~13x and manufacture false confidence).", ""]

    # ---------------- P(Hold) --------------------------------------------------------------
    # Last snapshot in the validated late window; did the side it was quoted on finish ahead?
    ph = con.execute("""
        WITH s AS (
          SELECT p.round_id, p.horizon, p.p_hold, p.position, p.seconds_left,
                 ROW_NUMBER() OVER (PARTITION BY p.round_id ORDER BY p.seconds_left ASC) rn
          FROM persistence_snapshot p
          WHERE p.p_hold IS NOT NULL AND p.seconds_left BETWEEN 15 AND 120
            AND p.position IN ('UP','DOWN')
        )
        SELECT s.horizon, s.p_hold, s.position, b.actual_direction
        FROM s JOIN price_to_beat b ON b.id = s.round_id
        WHERE s.rn = 1 AND b.resolved AND b.actual_direction IN ('UP','DOWN')
    """).fetchdf()
    if len(ph):
        y = (ph["position"] == ph["actual_direction"]).astype(int).to_numpy()
        p = ph["p_hold"].to_numpy(float)
        br, ece, bss, base = metrics(p, y)
        L += ["## P(Hold) - the app's most-used probability", "",
              f"n = **{len(ph):,}** rounds (last snapshot in the 15-120s window). "
              f"Brier **{br:.4f}** | ECE **{ece:.4f}** | skill vs base rate **{bss:+.3f}** | "
              f"base rate {base:.1%}", ""]
        reliability(p, y, L, 0.5, 1.0, 0.05)
        mean_gap = float(np.mean(y) - np.mean(p))
        L += ["", f"**Overall: predicted {np.mean(p):.1%} vs realized {np.mean(y):.1%} "
                  f"({mean_gap:+.1%}).**"]
        if bss <= 0:
            L.append("**No skill over the base rate** - it ranks no better than a constant.")
        elif abs(mean_gap) > 0.03:
            L.append(f"**Systematically {'OVER' if mean_gap < 0 else 'UNDER'}-confident** by "
                     f"{abs(mean_gap):.1%}. Any gate using a raw P(Hold) threshold is mis-set by "
                     "that much and needs recalibration before use.")
        else:
            L.append("**Well calibrated in aggregate** - the aggregate gap is within 3 points. "
                     "Check the per-band rows above for local bias.")
        L.append("")

    # ---------------- round-state heads ----------------------------------------------------
    rs = con.execute("""
        WITH s AS (
          SELECT r.*, ROW_NUMBER() OVER (PARTITION BY r.round_id ORDER BY r.seconds_left ASC) rn
          FROM round_state_snapshots r
          WHERE r.seconds_left BETWEEN 30 AND 120
        )
        SELECT s.horizon, s.current_position, s.flip_risk, s.late_shock_20, s.late_shock_50,
               s.late_shock_100, s.action, s.current_move,
               b.actual_direction, b.move
        FROM s JOIN price_to_beat b ON b.id = s.round_id
        WHERE s.rn = 1 AND b.resolved AND b.actual_direction IN ('UP','DOWN')
    """).fetchdf()

    if len(rs):
        # flip risk: did the leader at the snapshot fail to finish ahead?
        f = rs[rs["flip_risk"].notna() & rs["current_position"].isin(["UP", "DOWN"])]
        if len(f) >= 50:
            y = (f["current_position"] != f["actual_direction"]).astype(int).to_numpy()
            p = f["flip_risk"].to_numpy(float)
            br, ece, bss, base = metrics(p, y)
            L += ["## Flip risk - does a higher number mean more actual flips?", "",
                  f"n = **{len(f):,}** rounds. Brier **{br:.4f}** | ECE **{ece:.4f}** | "
                  f"skill **{bss:+.3f}** | actual flip rate {base:.1%}", ""]
            reliability(p, y, L, 0.0, 0.5, 0.05)
            L += ["", ("**Ranks flips better than a constant.**" if bss > 0 else
                       "**No skill over the base rate** - it does not rank flips."), ""]

        # Late shocks: the head predicts movement REMAINING FROM THE CHECKPOINT, not the total
        # round move. Both `current_move` (snapshot) and `move` (settlement) are measured from
        # the same anchor, so the remaining move is their difference:
        #     settle_price - snapshot_price = (settle - anchor) - (snapshot - anchor)
        # Labelling with the total round move instead (the first version of this test) compares
        # the head against a strictly larger quantity over a longer interval and manufactures a
        # ~20x "underconfidence" that is pure measurement error.
        for bar, col in ((20, "late_shock_20"), (50, "late_shock_50"), (100, "late_shock_100")):
            g = rs[rs[col].notna() & rs["move"].notna() & rs["current_move"].notna()]
            if len(g) < 50:
                continue
            remaining = (g["move"].astype(float) - g["current_move"].astype(float)).abs()
            y = (remaining >= bar).astype(int).to_numpy()
            p = g[col].to_numpy(float)
            br, ece, bss, base = metrics(p, y)
            L += [f"## Late shock >= ${bar}", "",
                  f"n = **{len(g):,}** | Brier **{br:.4f}** | ECE **{ece:.4f}** | "
                  f"skill **{bss:+.3f}** | actual rate {base:.1%} | mean predicted {p.mean():.1%}", ""]
            reliability(p, y, L)
            L.append("")

        # champion action tiers: must be MONOTONE to be shown to a human
        L += ["## Champion action tiers - do they stratify monotonically?", "",
              "| action | rounds | leader held |", "|---|---|---|"]
        seen = []
        for act in ("PAPER", "WAIT", "AVOID"):
            g = rs[(rs["action"] == act) & rs["current_position"].isin(["UP", "DOWN"])]
            if len(g) < 25:
                L.append(f"| {act} | {len(g)} (too few) | - |")
                continue
            held = float((g["current_position"] == g["actual_direction"]).mean())
            seen.append((act, len(g), held))
            L.append(f"| {act} | {len(g):,} | {held:.1%} |")
        if len(seen) >= 2:
            mono = all(seen[i][2] >= seen[i+1][2] for i in range(len(seen)-1))
            L += ["", f"**{'Monotone' if mono else 'NOT monotone'}** "
                      f"({' > '.join(f'{a} {h:.0%}' for a, _, h in seen)}). "
                      + ("" if mono else "A non-monotone tier must not be presented as a "
                                         "confidence ranking - per the stratifier rule it is noise.")]
        L.append("")

    con.close()
    L += ["## How to read this", "",
          "- **Skill vs base rate (BSS)** is the load-bearing number. <= 0 means the head carries no "
          "usable information no matter how good its AUC looked in training.",
          "- **ECE / per-band gap** says whether a *threshold* on the head is set where you think. "
          "A head can rank well (good BSS) and still be mis-scaled (bad ECE) - then it needs "
          "recalibration, not replacement.",
          "- Bands marked **<-** are off by more than 5 points.",
          "- One row per round; the 21-day window can kill a head but cannot promote one "
          "(the promotion contract needs 8 calendar weeks).", "",
          "**Nothing here changes a threshold or promotes anything. Measurement only.**"]

    os.makedirs(os.path.dirname(OUT_MD), exist_ok=True)
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    print("\n".join(L))
    print(f"\nWrote {OUT_MD}")


if __name__ == "__main__":
    main()
