"""Seven direction heads of different families, combined by voting. Does anything beat chance?

PROTOCOL
    docs/active/PREREG_DIRECTION_ENSEMBLE_V1.md, sha256 bd48d3c5..., frozen before training.

THE PRIOR, RECORDED BEFORE THE RESULT
    Ensembling reduces variance; it does not create information. CONDITIONAL_DIRECTION_V1 read
    AUC 0.498 from one LightGBM, so seven families over the SAME features and target are
    expected near 0.50. It is still worth running: one model class can miss structure another
    captures, and a negative from seven families is far more decisive than from one.

THE NULL FLOOR IS THE POINT
    On 155,000 bars, "AUC 0.51" means nothing without knowing what noise produces. The floor is
    measured by shuffling labels in whole-DAY blocks - shuffling individual bars would destroy
    within-day autocorrelation and give an artificially tight floor, making noise look like
    signal.

    python research/direction_ensemble_v1.py --selftest
    python research/direction_ensemble_v1.py
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tradability_head_v1 import (                                     # noqa: E402
    FEATURES, FORWARD_BARS, MATRIX, PURGE_BARS, TRAIN_FRACTION, auc,
)
from conditional_direction_v1 import (                                # noqa: E402
    COST_BPS, day_block_ci, load_frame, non_overlapping,
)

PROTOCOL = "PREREG_DIRECTION_ENSEMBLE_V1.md"
NULL_REPLICATIONS = 200
HEAD_NAMES = ("LightGBM", "XGBoost", "RandomForest", "ExtraTrees",
              "LogisticRegression", "MLP", "GaussianNB")


def build_heads():
    """Seven families spanning boosting, bagging, linear, neural and generative biases."""
    import lightgbm as lgb
    from sklearn.ensemble import ExtraTreesClassifier, RandomForestClassifier
    from sklearn.linear_model import LogisticRegression
    from sklearn.naive_bayes import GaussianNB
    from sklearn.neural_network import MLPClassifier
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from xgboost import XGBClassifier
    return {
        "LightGBM": lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=31,
                                       min_child_samples=200, verbose=-1, random_state=0),
        "XGBoost": XGBClassifier(n_estimators=300, learning_rate=0.05, max_depth=4,
                                 min_child_weight=50, verbosity=0, eval_metric="logloss",
                                 random_state=0),
        "RandomForest": RandomForestClassifier(n_estimators=200, min_samples_leaf=200,
                                               n_jobs=-1, random_state=0),
        "ExtraTrees": ExtraTreesClassifier(n_estimators=200, min_samples_leaf=200,
                                           n_jobs=-1, random_state=0),
        "LogisticRegression": make_pipeline(StandardScaler(),
                                            LogisticRegression(max_iter=1000, random_state=0)),
        "MLP": make_pipeline(StandardScaler(),
                             MLPClassifier(hidden_layer_sizes=(32, 16), max_iter=300,
                                           early_stopping=True, random_state=0)),
        "GaussianNB": make_pipeline(StandardScaler(), GaussianNB()),
    }


def null_floor(scores: np.ndarray, labels: np.ndarray, days: np.ndarray,
               replications: int = NULL_REPLICATIONS, seed: int = 101) -> tuple:
    """AUC distribution when labels are shuffled in whole-DAY blocks.

    The scores are left untouched and the LABELS are permuted by day, so any real association is
    destroyed while the within-day structure of both series survives."""
    unique = np.unique(days)
    by_day = [np.flatnonzero(days == d) for d in unique]
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(replications):
        order = rng.permutation(len(by_day))
        shuffled = np.empty_like(labels)
        for target, source in zip(by_day, (by_day[i] for i in order)):
            take = source
            if len(take) >= len(target):
                shuffled[target] = labels[take[:len(target)]]
            else:
                reps = int(np.ceil(len(target) / len(take)))
                shuffled[target] = np.tile(labels[take], reps)[:len(target)]
        out.append(auc(scores, shuffled))
    out = np.array([v for v in out if np.isfinite(v)])
    if len(out) < 10:
        return (float("nan"), float("nan"), float("nan"))
    return (float(np.percentile(out, 2.5)), float(np.median(out)),
            float(np.percentile(out, 97.5)))


def verdict_for(soft_auc: float, best_single: float, floor_hi: float,
                net_ci: tuple, any_head_beats_floor: bool) -> tuple[str, str]:
    if not any_head_beats_floor and not (soft_auc > floor_hi):
        return ("DIRECTION_NOT_PREDICTABLE_CONFIRMED",
                f"no head and neither vote exceeds the null floor upper bound of {floor_hi:.4f}")
    if soft_auc <= best_single:
        return ("ENSEMBLE_NO_BETTER_THAN_SINGLE",
                f"soft vote AUC {soft_auc:.4f} does not exceed the best single head "
                f"{best_single:.4f}")
    if np.isfinite(net_ci[0]) and net_ci[0] > 0:
        return ("ENSEMBLE_ADDS_DIRECTION",
                f"soft vote beats the null floor and its post-cost CI lower bound "
                f"{net_ci[0]:+.2f} bps exceeds zero")
    return ("ENSEMBLE_AUC_ONLY",
            f"soft vote AUC {soft_auc:.4f} beats the null floor, but post-cost value "
            f"CI [{net_ci[0]:+.2f}, {net_ci[1]:+.2f}] does not clear zero")


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    check(len(HEAD_NAMES) == 7, "seven heads are declared")
    check(len(set(HEAD_NAMES)) == 7, "...and they are seven distinct families")

    rng = np.random.default_rng(0)
    n = 20_000
    days = np.repeat(np.arange(n // 200), 200)[:n]
    labels = rng.integers(0, 2, n)

    # A score with NO association must sit inside its own null floor.
    noise = rng.normal(size=n)
    lo, mid, hi = null_floor(noise, labels, days, replications=60)
    observed = auc(noise, labels)
    check(np.isfinite(lo) and lo < mid < hi, "the null floor is a proper interval")
    check(abs(mid - 0.5) < 0.02, "the null floor is centred on chance")
    check(lo <= observed <= hi,
          "an UNINFORMATIVE score falls inside its null floor - noise is not called signal")

    # A genuinely informative score must sit above it.
    informative = labels + rng.normal(0, 0.4, n)
    lo2, mid2, hi2 = null_floor(informative, labels, days, replications=60)
    check(auc(informative, labels) > hi2,
          "an INFORMATIVE score sits above its null floor - the floor is not vacuous")

    check(hi > 0.5, "the floor's upper bound exceeds 0.5, so tiny deviations cannot pass")

    kind, _ = verdict_for(0.52, 0.51, 0.505, (1.0, 3.0), True)
    check(kind == "ENSEMBLE_ADDS_DIRECTION", "beating floor, best head and cost passes")
    kind, _ = verdict_for(0.52, 0.51, 0.505, (-1.0, 3.0), True)
    check(kind == "ENSEMBLE_AUC_ONLY",
          "AUC above the floor with no post-cost value is reported as exactly that")
    kind, _ = verdict_for(0.50, 0.53, 0.505, (1.0, 3.0), True)
    check(kind == "ENSEMBLE_NO_BETTER_THAN_SINGLE",
          "an ensemble that loses to its best member is reported as such")
    kind, _ = verdict_for(0.501, 0.502, 0.510, (1.0, 3.0), False)
    check(kind == "DIRECTION_NOT_PREDICTABLE_CONFIRMED",
          "nothing above the floor confirms unpredictability")

    print(f"\nDIRECTION ENSEMBLE SELFTEST: PASS ({checks} checks)")
    return 0


def run() -> int:
    if not MATRIX.is_file():
        print(f"missing {MATRIX}")
        return 1
    frame = load_frame()
    split = int(len(frame) * TRAIN_FRACTION)
    train = frame.iloc[:split]
    test = frame.iloc[split + PURGE_BARS:].reset_index(drop=True)

    Xtr = train[list(FEATURES)].to_numpy(float)
    Xte = test[list(FEATURES)].to_numpy(float)
    ytr = (train["fwd_ret_bps"].to_numpy(float) > 0).astype(int)
    yte = (test["fwd_ret_bps"].to_numpy(float) > 0).astype(int)
    days = test["day"].to_numpy()

    print("=" * 104)
    print(f"DIRECTION ENSEMBLE V1 - protocol {PROTOCOL} (frozen before training)")
    print("=" * 104)
    print(f"  train {len(train):,} / test {len(test):,} bars   horizon {FORWARD_BARS} bars   "
          f"train up-rate {ytr.mean():.1%}")
    print("  single-LightGBM reference from CONDITIONAL_DIRECTION_V1: AUC 0.498")
    print()

    probabilities = {}
    print(f"  {'head':<22}{'test AUC':>10}")
    print("  " + "-" * 34)
    for name, model in build_heads().items():
        try:
            model.fit(Xtr, ytr)
            p = model.predict_proba(Xte)[:, 1]
            probabilities[name] = p
            print(f"  {name:<22}{auc(p, yte):>10.4f}")
        except Exception as exc:
            print(f"  {name:<22}{'FAILED':>10}  {str(exc)[:40]}")

    if not probabilities:
        print("  no head trained")
        return 1

    stacked = np.vstack([probabilities[n] for n in probabilities])
    soft = stacked.mean(axis=0)
    hard = (stacked >= 0.5).sum(axis=0) / len(stacked)
    soft_auc, hard_auc = auc(soft, yte), auc(hard, yte)
    best_single = max(auc(p, yte) for p in probabilities.values())
    correlation = np.corrcoef(stacked)
    mean_corr = float(correlation[np.triu_indices(len(stacked), k=1)].mean())

    print(f"  {'HARD_VOTE':<22}{hard_auc:>10.4f}")
    print(f"  {'SOFT_VOTE (primary)':<22}{soft_auc:>10.4f}")
    print()
    print(f"  mean pairwise correlation between heads: {mean_corr:.3f}")

    lo, mid, hi = null_floor(soft, yte, days)
    print(f"  null floor (labels shuffled by day, {NULL_REPLICATIONS} reps): "
          f"median {mid:.4f}, 95% [{lo:.4f}, {hi:.4f}]")
    any_beats = any(auc(p, yte) > hi for p in probabilities.values()) or hard_auc > hi

    rows = non_overlapping(np.arange(len(test)))
    sides = np.where(soft[rows] >= 0.5, 1, -1)
    net = sides * test["fwd_ret_bps"].to_numpy(float)[rows] - COST_BPS
    net_ci = day_block_ci(net, days[rows])
    print(f"  SOFT_VOTE traded: {len(rows):,} non-overlapping windows, "
          f"{net.mean():+.2f} bps, CI [{net_ci[0]:+.2f}, {net_ci[1]:+.2f}]")

    verdict, reason = verdict_for(soft_auc, best_single, hi, net_ci, any_beats)
    print()
    print(f"  VERDICT: {verdict}")
    print(f"  {reason}")
    print()
    if verdict == "DIRECTION_NOT_PREDICTABLE_CONFIRMED":
        print("  Seven model families spanning boosting, bagging, linear, neural and generative")
        print("  biases all land inside the noise floor. Direction at this horizon on this")
        print("  feature set is closed; no further model-family search may run against it.")
        print("  The remaining hypotheses are different INFORMATION or a different HORIZON.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    raise SystemExit(selftest() if args.selftest else run())
