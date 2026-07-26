"""PR2 - daily HEAD HEALTH: which heads are allowed to affect a decision.

A head can stay wired into the decision path long after its live skill has gone. The 21-day
Oracle deployment showed exactly that: flip risk scored BSS +0.002 (no skill) and the $20 shock
head BSS -0.013 (NEGATIVE skill) while both were still being displayed and consumed.

This module assigns each head a state from live outcomes and states what that permits:

    USABLE              skill > 0 and calibrated       -> may supply fair value AND rank
    CALIBRATION_ONLY    skill > 0 but ECE too high     -> may RANK, may NOT supply fair value
    DISABLED_NO_SKILL   BSS <= 0                       -> may not affect any decision
    SHADOW              n below the required sample    -> record only
    INSUFFICIENT_DATA   no joined outcomes
    DRIFTED             recent window materially worse than the full window

It NEVER retrains and NEVER edits a threshold: it fails closed and reports. A head that loses
its state is demoted, and the fix is an offline challenger, not an automatic refit.

    python backend/monitoring/head_health.py --db data/btc_duckdbs/analytics.duckdb
    python backend/monitoring/head_health.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout.reconfigure(encoding="utf-8")

from phold_challenger import brier, log_loss, ece, brier_skill  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT_DIR = os.path.join(ROOT, "data", "research", "head_health")

# ---- frozen thresholds ------------------------------------------------------------------------
MIN_N = 500              # below this a head is SHADOW regardless of how good it looks
BSS_FLOOR = 0.0          # <= 0 means no usable information vs the base rate
ECE_MAX = 0.05           # above this the head may rank but may not price
DRIFT_BSS_DROP = 0.02    # recent window losing this much skill = DRIFTED
RECENT_FRACTION = 0.30
PROTOCOL_VERSION = "2026-07-26-head-health-v1"

STATE_PERMISSIONS = {
    "USABLE": {"may_price": True, "may_rank": True, "may_display_confidence": True},
    "CALIBRATION_ONLY": {"may_price": False, "may_rank": True, "may_display_confidence": False},
    "DISABLED_NO_SKILL": {"may_price": False, "may_rank": False, "may_display_confidence": False},
    "SHADOW": {"may_price": False, "may_rank": False, "may_display_confidence": False},
    "INSUFFICIENT_DATA": {"may_price": False, "may_rank": False, "may_display_confidence": False},
    "DRIFTED": {"may_price": False, "may_rank": True, "may_display_confidence": False},
}


def classify(p, y, name="head"):
    """Assign a head-health state from predictions and realized binary outcomes."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    n = len(p)
    out = {"head": name, "n": int(n), "protocol": PROTOCOL_VERSION}
    if n == 0 or len(np.unique(y)) < 2:
        out.update({"state": "INSUFFICIENT_DATA", "reason": "no joined outcomes or a single class"})
        out["permissions"] = STATE_PERMISSIONS["INSUFFICIENT_DATA"]
        return out

    m = {"brier": brier(p, y), "log_loss": log_loss(p, y), "ece": ece(p, y),
         "bss": brier_skill(p, y), "mean_pred": float(p.mean()), "realized": float(y.mean())}
    out["metrics"] = {k: round(v, 6) for k, v in m.items()}

    if n < MIN_N:
        state, reason = "SHADOW", f"n={n} < {MIN_N}; record only"
    elif m["bss"] <= BSS_FLOOR:
        # The load-bearing rule. AUC in training is irrelevant here: BSS <= 0 means predicting the
        # base rate every time would have scored at least as well on live outcomes.
        state, reason = "DISABLED_NO_SKILL", f"BSS {m['bss']:+.4f} <= {BSS_FLOOR}"
    else:
        cut = int(n * (1 - RECENT_FRACTION))
        drift = None
        if cut > MIN_N // 2 and len(np.unique(y[cut:])) > 1:
            drift = brier_skill(p[:cut], y[:cut]) - brier_skill(p[cut:], y[cut:])
            out["metrics"]["bss_recent"] = round(brier_skill(p[cut:], y[cut:]), 6)
        if drift is not None and drift > DRIFT_BSS_DROP:
            state, reason = "DRIFTED", f"skill fell {drift:.4f} in the most recent {int(RECENT_FRACTION*100)}%"
        elif m["ece"] > ECE_MAX:
            state, reason = "CALIBRATION_ONLY", f"ECE {m['ece']:.4f} > {ECE_MAX} (ranks, does not price)"
        else:
            state, reason = "USABLE", f"BSS {m['bss']:+.4f}, ECE {m['ece']:.4f}"

    out["state"] = state
    out["reason"] = reason
    out["permissions"] = STATE_PERMISSIONS[state]
    return out


def check_tier_monotonicity(tiers):
    """`tiers`: {label: (n, realized_rate)} ordered strongest->weakest as PRESENTED.

    A tier scale that is not monotone is not a confidence scale. It must not be displayed as one
    (the live ledger had PAPER at 69.4% and WAIT at 89.6% - the 'act' tier was the weakest)."""
    ordered = [(k, v) for k, v in tiers.items() if v[0] >= 30]
    rates = [v[1] for _, v in ordered]
    mono = all(rates[i] >= rates[i + 1] for i in range(len(rates) - 1))
    return {"tiers": {k: {"n": v[0], "realized": round(v[1], 4)} for k, v in ordered},
            "monotone": bool(mono),
            "may_display_as_confidence": bool(mono),
            "reason": "" if mono else "presented order does not match realized order"}


# ---------------------------------------------------------------- live data
def run(db_path):
    import duckdb
    con = duckdb.connect(db_path, read_only=True)
    rep = {"db": db_path, "generated_utc": datetime.now(timezone.utc).isoformat(),
           "protocol": PROTOCOL_VERSION, "heads": {}}

    def one_per_round(sql, params=()):
        try:
            return np.array(con.execute(sql, params).fetchall(), float)
        except Exception as exc:
            print(f"   query failed: {str(exc)[:90]}")
            return np.empty((0, 2))

    # P(hold): does the leading side hold?
    a = one_per_round("""
        WITH s AS (SELECT round_id, ts, p_leader_holds, current_position, seconds_left,
                          ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY seconds_left) rn
                   FROM round_state_snapshots
                   WHERE seconds_left BETWEEN 15 AND 120 AND p_leader_holds IS NOT NULL)
        SELECT s.p_leader_holds,
               CASE WHEN s.current_position = p.actual_direction THEN 1 ELSE 0 END
        FROM s JOIN price_to_beat p ON p.id = s.round_id
        WHERE s.rn = 1 AND p.resolved AND p.actual_direction IN ('UP','DOWN')
          AND p.settlement_source LIKE 'official:%'
          AND s.current_position IN ('UP','DOWN')
        ORDER BY s.ts""")
    if len(a):
        rep["heads"]["p_hold"] = classify(a[:, 0], a[:, 1], "p_hold")

    # flip risk: is it the complement of holding?
    b = one_per_round("""
        WITH s AS (SELECT round_id, ts, flip_risk, current_position, seconds_left,
                          ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY seconds_left) rn
                   FROM round_state_snapshots
                   WHERE seconds_left BETWEEN 15 AND 120 AND flip_risk IS NOT NULL)
        SELECT s.flip_risk,
               CASE WHEN s.current_position <> p.actual_direction THEN 1 ELSE 0 END
        FROM s JOIN price_to_beat p ON p.id = s.round_id
        WHERE s.rn = 1 AND p.resolved AND p.actual_direction IN ('UP','DOWN')
          AND p.settlement_source LIKE 'official:%'
          AND s.current_position IN ('UP','DOWN')
        ORDER BY s.ts""")
    if len(b):
        rep["heads"]["flip_risk"] = classify(b[:, 0], b[:, 1], "flip_risk")

    # champion action tiers - are they a confidence scale at all?
    try:
        rows = con.execute("""
            SELECT c.champion_action, COUNT(*) n,
                   AVG(CASE WHEN c.current_position = p.actual_direction THEN 1.0 ELSE 0.0 END)
            FROM (SELECT round_id, champion_action, current_position, seconds_left,
                         ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY seconds_left) rn
                  FROM champion_snapshots WHERE seconds_left BETWEEN 15 AND 120) c
            JOIN price_to_beat p ON p.id = c.round_id
            WHERE c.rn = 1 AND p.resolved AND p.actual_direction IN ('UP','DOWN')
              AND p.settlement_source LIKE 'official:%'
              AND c.current_position IN ('UP','DOWN')
            GROUP BY 1 ORDER BY 3 DESC""").fetchall()
        presented = ["PAPER_BET", "PAPER", "SETUP", "LEAN", "WATCH", "WAIT", "NO_EDGE", "AVOID"]
        d = {r[0]: (int(r[1]), float(r[2])) for r in rows}
        ordered = {k: d[k] for k in presented if k in d}
        ordered.update({k: v for k, v in d.items() if k not in ordered})
        rep["champion_tiers"] = check_tier_monotonicity(ordered)
    except Exception as exc:
        rep["champion_tiers"] = {"error": str(exc)[:120]}
    con.close()
    return rep


def render(rep):
    L = ["=" * 96, f"HEAD HEALTH  |  {rep['protocol']}", f"generated {rep['generated_utc']}",
         "=" * 96,
         f"{'head':<14}{'n':>7}{'state':>20}{'BSS':>10}{'ECE':>9}{'pred':>8}{'real':>8}  permissions"]
    L.append("-" * 96)
    for name, h in rep.get("heads", {}).items():
        m = h.get("metrics", {})
        perm = h["permissions"]
        tags = ",".join(k.replace("may_", "") for k, v in perm.items() if v) or "none"
        L.append(f"{name:<14}{h['n']:>7}{h['state']:>20}"
                 f"{m.get('bss', float('nan')):>10.4f}{m.get('ece', float('nan')):>9.4f}"
                 f"{m.get('mean_pred', float('nan'))*100:>7.1f}%{m.get('realized', float('nan'))*100:>7.1f}%"
                 f"  {tags}")
        L.append(f"{'':<14}-> {h['reason']}")
    ct = rep.get("champion_tiers") or {}
    if "tiers" in ct:
        L += ["", "CHAMPION ACTION TIERS (as presented, strongest first)"]
        for k, v in ct["tiers"].items():
            L.append(f"   {k:<12} n={v['n']:<6} realized={v['realized']*100:.1f}%")
        L.append(f"   monotone={ct['monotone']}  may_display_as_confidence="
                 f"{ct['may_display_as_confidence']}"
                 + (f"  ({ct['reason']})" if ct.get("reason") else ""))
    L += ["", "No head is retrained here. A demoted head is fixed by an offline challenger,",
          "never by an automatic refit and never by moving a threshold."]
    return "\n".join(L)


def selftest():
    ok = True

    def chk(c, m):
        nonlocal ok
        print(f"  {'PASS' if c else 'FAIL'}  {m}")
        ok = ok and c

    print("head_health selftest")
    rng = np.random.default_rng(20260726)
    n = 3000

    # a genuinely skilled + calibrated head
    tp = rng.uniform(0.05, 0.95, n)
    y = (rng.uniform(size=n) < tp).astype(float)
    r = classify(tp, y, "good")
    chk(r["state"] == "USABLE" and r["permissions"]["may_price"], f"skilled+calibrated -> USABLE ({r['state']})")

    # a head with NO skill: pure noise
    noise = rng.uniform(0.2, 0.8, n)
    r2 = classify(noise, y, "noise")
    chk(r2["state"] == "DISABLED_NO_SKILL" and not r2["permissions"]["may_rank"],
        f"no-skill head -> DISABLED_NO_SKILL, may not rank ({r2['state']})")

    # skilled but badly scaled: must RANK but not PRICE.
    # Calibrated to resemble the REAL failure mode measured live on P(hold): BSS stays positive
    # (the ranking survives) while ECE blows past the pricing threshold. A harsher distortion
    # correctly lands in DISABLED_NO_SKILL instead - miscalibration severe enough to destroy
    # Brier skill is not "needs recalibration", it is "carries no usable information".
    over = np.clip(tp + (1 - tp) * 0.22, 0, 0.999)
    r3 = classify(over, y, "overconfident")
    chk(r3["state"] in ("CALIBRATION_ONLY", "DRIFTED") and not r3["permissions"]["may_price"]
        and r3["permissions"]["may_rank"],
        f"skilled+miscalibrated -> ranks but cannot price ({r3['state']})")

    # too small a sample
    r4 = classify(tp[:100], y[:100], "small")
    chk(r4["state"] == "SHADOW" and not r4["permissions"]["may_price"], "n<500 -> SHADOW")

    # tier monotonicity: the live inversion must be caught
    inv = check_tier_monotonicity({"PAPER": (755, 0.694), "WAIT": (5834, 0.896)})
    chk(not inv["monotone"] and not inv["may_display_as_confidence"],
        "inverted tiers (PAPER 69.4% < WAIT 89.6%) refused as a confidence scale")
    good = check_tier_monotonicity({"T3": (500, 0.95), "T2": (500, 0.88), "T1": (500, 0.80)})
    chk(good["monotone"] and good["may_display_as_confidence"], "monotone tiers accepted")

    # every state maps to explicit permissions
    chk(all(s in STATE_PERMISSIONS for s in
            ("USABLE", "CALIBRATION_ONLY", "DISABLED_NO_SKILL", "SHADOW",
             "INSUFFICIENT_DATA", "DRIFTED")), "every state has explicit permissions")
    print("head-health:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.join(ROOT, "data", "btc_duckdbs", "analytics.duckdb"))
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--out", default=OUT_DIR)
    a = ap.parse_args()
    if a.selftest:
        return selftest()
    if not os.path.exists(a.db):
        print(f"ERROR: db not found: {a.db}")
        return 1
    rep = run(a.db)
    print(render(rep))
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "head_health.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2)
    print(f"\nwrote -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
