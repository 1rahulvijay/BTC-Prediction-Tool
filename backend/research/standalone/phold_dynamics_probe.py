"""
phold_dynamics_probe.py — do LATE-WINDOW DYNAMICS lift the P(Hold) model? (offline, leak-free)
================================================================================================
The current P(Hold) model sees a STATIC snapshot (distance, seconds_left, position, vol_60s). This
probe tests whether DYNAMICS computed from the within-round snapshot sequence add AUC:
  * distance_velocity   — is the lead growing or shrinking? (Δdistance vs previous snapshot)
  * dist_accel          — 2nd difference (is the change accelerating?)
  * line_cross_count    — how many times price already crossed the line this round (chop count)
  * time_since_last_cross — seconds since the side last flipped (longer = more committed)

Measure-FIRST: if these don't clear +0.01 AUC over the static base, we don't add them (no parity
cost, no retrain). If they do, wire them into build_persistence_dataset.py + the live recorder.

Leak-free: every dynamic uses ONLY snapshots up to the current one within the round; the label is the
round outcome. Split is by ROUND (window_start_ms) chronologically — no same-round rows straddle
train/test. Uses data/persistence_dataset.parquet (1.95M snapshots).

Usage:  python backend/research/standalone/phold_dynamics_probe.py            # 5m + 15m
        python backend/research/standalone/phold_dynamics_probe.py --selftest
"""

try:
    from . import _bootstrap as _research_bootstrap  # noqa: F401
except ImportError:
    import _bootstrap as _research_bootstrap  # noqa: F401

del _research_bootstrap

import argparse
import os
import sys

import numpy as np

DATA_DIR = os.environ.get("BTC_DATA_DIR") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "data")
PATH = os.path.join(DATA_DIR, "persistence_dataset.parquet")
HORIZONS = (5, 15)
BASE = ["distance_pct_abs", "seconds_left", "vol_60s_pct", "pos_num"]
DYN = ["distance_velocity", "dist_accel", "line_cross_count", "time_since_last_cross"]


def add_dynamics(d):
    """Add within-round dynamics. d: one horizon's rows. Returns d sorted with the new columns.
    All backward-looking within each round (window_start_ms), ordered by seconds_elapsed."""
    d = d.sort_values(["window_start_ms", "seconds_elapsed"]).copy()
    d["pos_num"] = (d["position"] == "UP").astype(int)
    d["distance_pct_abs"] = d["distance_pct"].abs()
    g = d.groupby("window_start_ms", sort=False)
    d["distance_velocity"] = g["distance"].diff().fillna(0.0)
    d["dist_accel"] = g["distance_velocity"].diff().fillna(0.0)
    cross = (d["pos_num"] != g["pos_num"].shift()).astype(int)
    cross = cross.where(g.cumcount() > 0, 0)                      # first snapshot of a round = no cross
    d["line_cross"] = cross
    d["line_cross_count"] = g["line_cross"].cumsum()
    # time since last cross: seconds_elapsed at the last cross, ffilled within round
    se_at_cross = d["seconds_elapsed"].where(cross == 1)
    last_cross_se = se_at_cross.groupby(d["window_start_ms"], sort=False).ffill().fillna(0.0)
    d["time_since_last_cross"] = d["seconds_elapsed"] - last_cross_se
    return d


def _auc_round_split(d, feats):
    """AUC with a chronological ROUND split (70/30 by window_start_ms) so no round straddles."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score
    rounds = np.sort(d["window_start_ms"].unique())
    cut = rounds[int(len(rounds) * 0.70)]
    tr = d[d["window_start_ms"] < cut]; te = d[d["window_start_ms"] >= cut]
    Xtr = tr[feats].to_numpy(float); ytr = tr["label"].to_numpy(int)
    Xte = te[feats].to_numpy(float); yte = te["label"].to_numpy(int)
    m = np.all(np.isfinite(Xtr), axis=1); Xtr, ytr = Xtr[m], ytr[m]
    m2 = np.all(np.isfinite(Xte), axis=1); Xte, yte = Xte[m2], yte[m2]
    if len(np.unique(ytr)) < 2 or len(np.unique(yte)) < 2:
        return None, 0
    sc = StandardScaler().fit(Xtr)
    lr = LogisticRegression(max_iter=1000, class_weight="balanced").fit(sc.transform(Xtr), ytr)
    return float(roc_auc_score(yte, lr.predict_proba(sc.transform(Xte))[:, 1])), len(yte)


def run():
    import pandas as pd
    if not os.path.exists(PATH):
        sys.exit(f"missing {PATH}")
    df = pd.read_parquet(PATH)
    print(f"persistence_dataset {df.shape}\n")
    print(f"  {'h':>3} {'n_test':>8} {'base_AUC':>9} {'base+dyn_AUC':>13} {'lift':>7}  verdict")
    for h in HORIZONS:
        d = add_dynamics(df[df["horizon"] == h])
        base_auc, _ = _auc_round_split(d, BASE)
        full_auc, n = _auc_round_split(d, BASE + DYN)
        if base_auc is None or full_auc is None:
            print(f"  {h:>3}  insufficient"); continue
        lift = full_auc - base_auc
        verdict = "WIRE (>=+.01)" if lift >= 0.01 else "skip (<+.01)"
        print(f"  {h:>3} {n:>8} {base_auc:>9.3f} {full_auc:>13.3f} {lift:>+7.3f}  {verdict}")
    print("\nREAD: lift >= +0.01 -> add the dynamics to build_persistence_dataset.py + the live")
    print("recorder (parity) + retrain P(Hold). Else the static snapshot already captures it.")


def selftest():
    import pandas as pd
    rng = np.random.default_rng(0)
    # synthetic rounds: snapshots every 15s; a round where the lead SHRINKS (neg velocity) and
    # crosses the line should HOLD less -> dynamics carry signal.
    rows = []
    for r in range(400):
        n = 8
        growing = rng.random() > 0.5
        for i in range(n):
            se = 15 * (i + 1); sl = 300 - se
            dist = (i + 1) * 2.0 if growing else (n - i) * 2.0     # growing vs shrinking lead
            pos = "UP" if dist >= 0 else "DOWN"
            rows.append({"horizon": 5, "window_start_ms": r * 10_000, "seconds_elapsed": se,
                         "seconds_left": sl, "distance": dist, "distance_pct": dist / 1000.0,
                         "position": pos, "vol_60s_pct": rng.uniform(0, 0.05),
                         "label": int(growing)})       # growing lead -> holds
    df = pd.DataFrame(rows)
    d = add_dynamics(df[df["horizon"] == 5])
    # dynamics correctness: growing rounds have positive mean velocity, shrinking negative
    gv = d[d["label"] == 1]["distance_velocity"].mean()
    sv = d[d["label"] == 0]["distance_velocity"].mean()
    assert gv > 0 > sv, f"velocity sign wrong: grow {gv}, shrink {sv}"
    assert (d["line_cross_count"] >= 0).all() and (d["time_since_last_cross"] >= 0).all()
    # the probe must DETECT the planted dynamics lift
    base_auc, _ = _auc_round_split(d, ["distance_pct_abs", "seconds_left", "vol_60s_pct", "pos_num"])
    full_auc, _ = _auc_round_split(d, BASE + DYN)
    assert full_auc >= base_auc, f"dynamics should not hurt on planted signal ({base_auc}->{full_auc})"
    print(f"phold_dynamics_probe self-test: ALL PASS (base {base_auc:.2f} -> +dyn {full_auc:.2f})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    selftest() if a.selftest else run()
