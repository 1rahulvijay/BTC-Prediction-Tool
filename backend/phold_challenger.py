"""PR2 - P(hold) recalibration CHALLENGER.

P(hold) is the app's most-used probability and it is measurably overconfident live: over 6,725
officially resolved rounds it predicted 96.1% where 89.3% realized, and the gap is worst exactly
where the app acts (the 90-95% band realizes 81.2%). Every downstream fair value inherits that
bias, which is why `decision_champion` no longer authorizes a bet from raw P(hold).

This module fits a CHALLENGER calibrator and scores it head-to-head against raw. It is a
challenger in the strict sense:

    * it NEVER writes into the serving path
    * it NEVER selects a threshold
    * it is adopted only by an explicit operator decision after it wins on Brier AND log-loss
      AND ECE, on rounds it was not fitted on

Protocol (frozen):
    separate calibrators per horizon      - 5m and 15m are different games
    >= 500 independent resolved rounds    - below that, INSUFFICIENT_DATA, no fit
    one observation per round             - never per tick; pooling ticks inflates n ~13x
    broad time-left groups only           - fine slicing manufactures fake structure
    logistic (Platt) first                - 2 parameters, hard to overfit
    isotonic only at >= 2000 rounds       - flexible, needs the sample to earn it
    rolling fixed-size window             - recency without an expanding-window drift blind spot
    temporal split, never random          - the future must not calibrate the past

    python backend/phold_challenger.py --db data/btc_duckdbs/analytics.duckdb
    python backend/phold_challenger.py --selftest
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.stdout.reconfigure(encoding="utf-8")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "data", "research", "phold_challenger")

# ---- frozen protocol constants (changing these is a protocol change, not a tuning knob) -------
MIN_ROUNDS = 500
ISOTONIC_MIN_ROUNDS = 2000
ROLLING_WINDOW = 5000
TEST_FRACTION = 0.30
TIME_GROUPS = ((0, 30), (30, 120), (120, 600))     # broad on purpose
ECE_BINS = 10
PROTOCOL_VERSION = "2026-07-26-phold-challenger-v2"


# ---------------------------------------------------------------- metrics
def brier(p, y):
    return float(np.mean((np.asarray(p, float) - np.asarray(y, float)) ** 2))


def log_loss(p, y, eps=1e-12):
    p = np.clip(np.asarray(p, float), eps, 1 - eps)
    y = np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def ece(p, y, bins=ECE_BINS):
    """Expected calibration error: mean |predicted - realized| weighted by bin population."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    total, n = 0.0, len(p)
    if n == 0:
        return float("nan")
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        m = (p >= lo) & (p < hi if i < bins - 1 else p <= hi)
        if m.sum() == 0:
            continue
        total += (m.sum() / n) * abs(p[m].mean() - y[m].mean())
    return float(total)


def brier_skill(p, y):
    """BSS vs the base rate. <= 0 means the head carries no usable information."""
    y = np.asarray(y, float)
    base = float(y.mean())
    ref = brier(np.full_like(y, base), y)
    return float(1.0 - brier(p, y) / ref) if ref > 0 else float("nan")


def _select_candidate(scored, raw):
    """Apply the three-metric eligibility gate before ranking by Brier.

    Ranking every candidate by Brier first could select one that worsened log-loss or ECE and
    report RAW_RETAINED even when another candidate improved all three metrics.
    """
    eligible = [
        item for item in scored
        if item[1]["brier"] < raw["brier"]
        and item[1]["log_loss"] < raw["log_loss"]
        and item[1]["ece"] < raw["ece"]
    ]
    pool = eligible or scored
    return min(pool, key=lambda item: item[1]["brier"]), bool(eligible)


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str)
                          .encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- calibrators
class PlattCalibrator:
    """Logistic calibration on the logit of the raw probability. Two parameters (a, b)."""
    kind = "logistic"

    def __init__(self):
        self.a, self.b = 1.0, 0.0

    @staticmethod
    def _logit(p, eps=1e-6):
        p = np.clip(np.asarray(p, float), eps, 1 - eps)
        return np.log(p / (1 - p))

    def fit(self, p, y, iters=400, lr=0.25):
        x, y = self._logit(p), np.asarray(y, float)
        a, b = 1.0, 0.0
        for _ in range(iters):                       # plain gradient descent on log-loss
            z = np.clip(a * x + b, -30, 30)
            q = 1.0 / (1.0 + np.exp(-z))
            ga = float(np.mean((q - y) * x))
            gb = float(np.mean(q - y))
            a -= lr * ga
            b -= lr * gb
        self.a, self.b = float(a), float(b)
        return self

    def predict(self, p):
        z = np.clip(self.a * self._logit(p) + self.b, -30, 30)
        return 1.0 / (1.0 + np.exp(-z))

    def params(self):
        return {"kind": self.kind, "a": round(self.a, 6), "b": round(self.b, 6)}


class IsotonicCalibrator:
    """Monotone step calibration. Flexible, so it is only allowed a large sample."""
    kind = "isotonic"

    def __init__(self):
        self._x = self._y = None

    def fit(self, p, y):
        from sklearn.isotonic import IsotonicRegression
        ir = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        ir.fit(np.asarray(p, float), np.asarray(y, float))
        self._ir = ir
        return self

    def predict(self, p):
        return np.clip(self._ir.predict(np.asarray(p, float)), 0.0, 1.0)

    def params(self):
        return {"kind": self.kind, "knots": int(len(getattr(self._ir, "X_thresholds_", [])))}


# ---------------------------------------------------------------- the challenge
def challenge(p_raw, y, ts=None, group_label="all"):
    """Fit on the earlier portion, score on the later. Returns a verdict dict."""
    p_raw = np.asarray(p_raw, float)
    y = np.asarray(y, float)
    n = len(p_raw)
    res = {"group": group_label, "n": int(n), "protocol": PROTOCOL_VERSION}

    if n < MIN_ROUNDS:
        res.update({"calibration_status": "INSUFFICIENT_DATA",
                    "note": f"n={n} < {MIN_ROUNDS} required rounds; no calibrator fitted"})
        return res

    order = np.argsort(np.asarray(ts, float)) if ts is not None else np.arange(n)
    p_raw, y = p_raw[order], y[order]
    if n > ROLLING_WINDOW:                            # rolling window: recency without drift blindness
        p_raw, y, n = p_raw[-ROLLING_WINDOW:], y[-ROLLING_WINDOW:], ROLLING_WINDOW
        res["n_windowed"] = int(n)

    cut = int(n * (1 - TEST_FRACTION))                 # TEMPORAL split - the future never fits the past
    ptr, ytr = p_raw[:cut], y[:cut]
    pte, yte = p_raw[cut:], y[cut:]
    if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
        res.update({"calibration_status": "INSUFFICIENT_DATA",
                    "note": "a split contains a single outcome class"})
        return res

    cands = [PlattCalibrator().fit(ptr, ytr)]
    if n >= ISOTONIC_MIN_ROUNDS:
        try:
            cands.append(IsotonicCalibrator().fit(ptr, ytr))
        except Exception as exc:
            res["isotonic_error"] = str(exc)[:80]

    raw = {"brier": brier(pte, yte), "log_loss": log_loss(pte, yte),
           "ece": ece(pte, yte), "bss": brier_skill(pte, yte),
           "mean_pred": float(pte.mean()), "realized": float(yte.mean())}
    res["raw"] = {k: round(v, 6) for k, v in raw.items()}

    scored = []
    for c in cands:
        q = c.predict(pte)
        m = {"brier": brier(q, yte), "log_loss": log_loss(q, yte),
             "ece": ece(q, yte), "bss": brier_skill(q, yte),
             "mean_pred": float(q.mean()), "realized": float(yte.mean())}
        res.setdefault("candidates", {})[c.kind] = {k: round(v, 6) for k, v in m.items()}
        scored.append((c, m))

    # ADOPTION REQUIRES WINNING ON ALL THREE. A calibrator that improves ECE while worsening
    # log-loss has moved the bias into the tails, which is not an improvement.
    (best, best_m), wins = _select_candidate(scored, raw)
    res["challenger"] = {"kind": best.kind, "params": best.params(),
                         **{k: round(v, 6) for k, v in best_m.items()}}
    res["calibrator_hash"] = _sha({"kind": best.kind, "params": best.params(),
                                   "protocol": PROTOCOL_VERSION, "group": group_label})
    res["beats_raw_on_all_three"] = bool(wins)
    res["calibration_status"] = "CHALLENGER_WINS" if wins else "RAW_RETAINED"
    res["improvement"] = {
        "brier": round(raw["brier"] - best_m["brier"], 6),
        "log_loss": round(raw["log_loss"] - best_m["log_loss"], 6),
        "ece": round(raw["ece"] - best_m["ece"], 6),
        "overconfidence_before_pp": round((raw["mean_pred"] - raw["realized"]) * 100, 2),
        "overconfidence_after_pp": round((best_m["mean_pred"] - best_m["realized"]) * 100, 2),
    }
    # A winning challenger is still NOT applied. Adoption is an operator decision.
    res["applied_to_serving"] = False
    res["adoption_note"] = ("Challenger is shadow-only. Adopting it requires an explicit operator "
                            "decision plus forward evidence on rounds it never saw.")
    return res


# ---------------------------------------------------------------- data
def load_from_db(db_path, horizon):
    """One observation per resolved round: the last P(hold) in the 15-120s window + the outcome."""
    import duckdb
    con = duckdb.connect(db_path, read_only=True)
    rows = con.execute("""
        WITH snap AS (
          SELECT round_id, horizon, ts, seconds_left, p_leader_holds, current_position,
                 ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY seconds_left ASC) rn
          FROM round_state_snapshots
          WHERE horizon = ? AND seconds_left BETWEEN 15 AND 120 AND p_leader_holds IS NOT NULL)
        SELECT s.ts, s.p_leader_holds AS p_hold, s.seconds_left,
               CASE WHEN s.current_position = p.actual_direction THEN 1 ELSE 0 END AS held
        FROM snap s JOIN price_to_beat p ON p.id = s.round_id
        WHERE s.rn = 1 AND p.resolved AND p.actual_direction IN ('UP','DOWN')
          AND p.settlement_source LIKE 'official:%'
          AND s.current_position IN ('UP','DOWN')
        ORDER BY s.ts
    """, (int(horizon),)).fetchall()
    con.close()
    if not rows:
        return None
    a = np.array(rows, float)
    return {"ts": a[:, 0], "p": a[:, 1], "secs": a[:, 2], "y": a[:, 3]}


def run(db_path):
    report = {"db": db_path, "generated_utc": datetime.now(timezone.utc).isoformat(),
              "protocol": PROTOCOL_VERSION, "horizons": {}}
    for h in (5, 15):
        d = load_from_db(db_path, h)
        if not d:
            report["horizons"][str(h)] = {"calibration_status": "INSUFFICIENT_DATA",
                                          "note": "no joined rounds"}
            continue
        hz = {"overall": challenge(d["p"], d["y"], d["ts"], f"{h}m/all")}
        for lo, hi in TIME_GROUPS:                    # broad groups only
            m = (d["secs"] >= lo) & (d["secs"] < hi)
            if m.sum() >= MIN_ROUNDS:
                hz[f"{lo}-{hi}s"] = challenge(d["p"][m], d["y"][m], d["ts"][m],
                                              f"{h}m/{lo}-{hi}s")
        report["horizons"][str(h)] = hz
    return report


def render(rep):
    L = ["=" * 92, f"P(HOLD) RECALIBRATION CHALLENGER  |  {rep['protocol']}",
         f"generated {rep['generated_utc']}", "=" * 92,
         "SHADOW ONLY - nothing here is applied to serving.", ""]
    for h, groups in rep["horizons"].items():
        L.append(f"── {h}m ─────────────────────────────────────────────────────────────────")
        if isinstance(groups, dict) and "calibration_status" in groups:
            L.append(f"   {groups['calibration_status']}: {groups.get('note','')}")
            continue
        for gname, r in groups.items():
            if r.get("calibration_status") == "INSUFFICIENT_DATA":
                L.append(f"   {gname:<12} INSUFFICIENT_DATA  ({r.get('note','')})")
                continue
            raw, ch, imp = r["raw"], r["challenger"], r["improvement"]
            L.append(f"   {gname:<12} n={r['n']:<6} status={r['calibration_status']}")
            L.append(f"      raw        brier={raw['brier']:.5f}  logloss={raw['log_loss']:.5f}"
                     f"  ece={raw['ece']:.5f}  bss={raw['bss']:+.4f}"
                     f"  pred={raw['mean_pred']*100:.1f}% vs real={raw['realized']*100:.1f}%")
            L.append(f"      {ch['kind']:<10} brier={ch['brier']:.5f}  logloss={ch['log_loss']:.5f}"
                     f"  ece={ch['ece']:.5f}  bss={ch['bss']:+.4f}"
                     f"  pred={ch['mean_pred']*100:.1f}% vs real={ch['realized']*100:.1f}%")
            L.append(f"      overconfidence {imp['overconfidence_before_pp']:+.2f}pp"
                     f" -> {imp['overconfidence_after_pp']:+.2f}pp"
                     f"   | wins on all three: {r['beats_raw_on_all_three']}"
                     f"   | calibrator_hash={r.get('calibrator_hash','-')}")
        L.append("")
    L.append("Adoption requires beating raw on brier AND log-loss AND ece, then an explicit")
    L.append("operator decision plus forward evidence on rounds the calibrator never saw.")
    return "\n".join(L)


# ---------------------------------------------------------------- selftest
def selftest():
    ok = True

    def chk(c, m):
        nonlocal ok
        print(f"  {'PASS' if c else 'FAIL'}  {m}")
        ok = ok and c

    print("phold_challenger selftest")
    rng = np.random.default_rng(20260726)

    # 1. metrics behave
    y = rng.integers(0, 2, 4000).astype(float)
    chk(abs(brier(np.full(4000, y.mean()), y) - y.var()) < 0.02, "Brier of the base rate ~ variance")
    chk(abs(brier_skill(np.full(4000, y.mean()), y)) < 1e-6, "BSS of the base rate == 0")
    chk(ece(y, y) < 1e-9, "ECE of a perfect predictor == 0")

    # 2. a KNOWN overconfident head must be detected and repaired
    n = 6000
    true_p = rng.uniform(0.55, 0.99, n)
    y2 = (rng.uniform(size=n) < true_p).astype(float)
    over = np.clip(true_p + (1 - true_p) * 0.6, 0, 0.999)      # systematically too confident
    r = challenge(over, y2, ts=np.arange(n))
    chk(r["calibration_status"] == "CHALLENGER_WINS", "overconfident head -> challenger wins")
    chk(r["improvement"]["overconfidence_before_pp"] > 3.0,
        f"overconfidence detected ({r['improvement']['overconfidence_before_pp']:+.2f}pp)")
    chk(abs(r["improvement"]["overconfidence_after_pp"])
        < abs(r["improvement"]["overconfidence_before_pp"]),
        "challenger reduces overconfidence")
    chk(r["applied_to_serving"] is False, "a winning challenger is STILL not applied")

    # Candidate selection must apply the all-three gate before the Brier tiebreaker.
    raw_m = {"brier": 0.20, "log_loss": 0.60, "ece": 0.10}
    (picked, _), any_win = _select_candidate([
        ("bad_tail", {"brier": 0.10, "log_loss": 0.70, "ece": 0.05}),
        ("all_three", {"brier": 0.12, "log_loss": 0.50, "ece": 0.04}),
    ], raw_m)
    chk(any_win and picked == "all_three",
        "all-three eligibility is applied before Brier ranking")

    # 3. an ALREADY calibrated head must NOT be 'improved' (no free lunch)
    r2 = challenge(true_p, y2, ts=np.arange(n))
    chk(r2["improvement"]["brier"] < 0.004,
        f"well-calibrated head barely improves (dBrier={r2['improvement']['brier']:.5f})")

    # 4. protocol floors
    r3 = challenge(over[:100], y2[:100], ts=np.arange(100))
    chk(r3["calibration_status"] == "INSUFFICIENT_DATA", "n<500 refuses to fit")
    r4 = challenge(over[:1200], y2[:1200], ts=np.arange(1200))
    chk("isotonic" not in (r4.get("candidates") or {}), "isotonic withheld below 2000 rounds")
    chk("isotonic" in (r.get("candidates") or {}), "isotonic offered at >=2000 rounds")

    # 5. temporal split: the calibrator must not be scored on rows it was fitted on
    chk(TEST_FRACTION > 0 and r["n"] >= MIN_ROUNDS, "held-out fraction is non-zero")

    print("phold-challenger:", "PASS" if ok else "FAIL")
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
    with open(os.path.join(a.out, "phold_challenger.json"), "w", encoding="utf-8") as fh:
        json.dump(rep, fh, indent=2)
    print(f"\nwrote -> {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
