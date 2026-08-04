"""Are the crossing-head probabilities calibrated, and does calibrating them change a decision?

PROTOCOL
    docs/active/PREREG_CROSSING_CALIBRATION_V1.md, sha256 add7cb22..., frozen before any
    calibration was fitted.

WHY THIS IS A GATE, NOT A REFINEMENT
    CROSSING_HEADS_V1 established DISCRIMINATION - round-equal AUC 0.6694/0.6814/0.6547. AUC is
    invariant to any monotone transform of the score, so a head can rank perfectly and still be
    systematically overconfident.

    Every downstream use is an expected-value calculation:

        E[value] = P(state) x payoff - cost

    which consumes the PROBABILITY, not its ranking. An uncalibrated 0.70 that is really 0.55
    turns a losing action into an apparent winner. So calibration gates the crossing-informed
    action tests rather than polishing them.

    python research/crossing_calibration_v1.py --selftest
    python research/crossing_calibration_v1.py
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
from tradability_head_v1 import auc                                     # noqa: E402

PROTOCOL = "PREREG_CROSSING_CALIBRATION_V1.md"

#: THE PRIMARY ENDPOINT AS SCORED, 2026-08-04. The protocol's stopping rule is "scored once",
#: and a promise in prose is not a control. These are the numbers the first and only scoring
#: produced; the run asserts recomputation still matches them. Anything that would move a
#: primary number - a feature change, a different split, a re-trained head - now fails loudly
#: instead of quietly producing a second, better-looking result.
FIRST_SCORING = {
    "is_final_crossing":          {"raw_ece": 0.1563, "verdict": "CALIBRATION_FAILS"},
    "state_original_side_at_30s": {"raw_ece": 0.0499, "verdict": "CALIBRATION_FAILS"},
    "state_original_side_at_60s": {"raw_ece": 0.0839, "verdict": "CALIBRATION_FAILS"},
}
SCORING_TOLERANCE = 0.002

BINS = 10
MATERIAL_ECE = 0.02
MATERIAL_FLIP = 0.01
EV_THRESHOLD = 0.50
ROUND_EQUAL_DRAWS = 400
AUC_TOLERANCE = 0.005


#: Tie-break seed. Fixed so the metric is deterministic, random so it cannot follow row order.
TIE_SEED = 41


def _ordered(probs: np.ndarray) -> np.ndarray:
    """Sort by probability, breaking TIES RANDOMLY rather than by input order.

    `np.argsort` is stable, so tied scores keep their row order - and equal-count bins then cut
    along whatever that order encodes. A constant score over rows grouped by round size reports a
    large ECE for a perfectly calibrated model, purely from the row ordering. LightGBM emits many
    exactly-tied probabilities (the same property that forced average-rank AUC in this work), so
    this is not hypothetical."""
    rng = np.random.default_rng(TIE_SEED)
    return np.lexsort((rng.random(len(probs)), probs))


def ece(probs: np.ndarray, labels: np.ndarray, bins: int = BINS) -> float:
    """Expected calibration error over EQUAL-COUNT bins.

    Equal-count rather than equal-width: crossing probabilities cluster, and equal-width bins
    would leave most bins nearly empty and let a handful of points dominate the average."""
    if len(probs) == 0:
        return float("nan")
    order = _ordered(probs)
    total = 0.0
    for chunk in np.array_split(order, bins):
        if len(chunk) == 0:
            continue
        total += len(chunk) / len(probs) * abs(probs[chunk].mean() - labels[chunk].mean())
    return float(total)


def reliability(probs: np.ndarray, labels: np.ndarray, bins: int = BINS) -> list[tuple]:
    order = _ordered(probs)
    out = []
    for chunk in np.array_split(order, bins):
        if len(chunk):
            out.append((float(probs[chunk].mean()), float(labels[chunk].mean()), len(chunk)))
    return out


def brier_decomposition(probs: np.ndarray, labels: np.ndarray, bins: int = BINS) -> dict:
    """Murphy: Brier = reliability - resolution + uncertainty. Lower reliability is better."""
    base = float(labels.mean())
    order = _ordered(probs)
    reliability_term = resolution_term = 0.0
    for chunk in np.array_split(order, bins):
        if not len(chunk):
            continue
        weight = len(chunk) / len(probs)
        reliability_term += weight * (probs[chunk].mean() - labels[chunk].mean()) ** 2
        resolution_term += weight * (labels[chunk].mean() - base) ** 2
    return {"brier": float(np.mean((probs - labels) ** 2)),
            "reliability": reliability_term, "resolution": resolution_term,
            "uncertainty": base * (1 - base)}


def fit_isotonic(probs: np.ndarray, labels: np.ndarray):
    from sklearn.isotonic import IsotonicRegression
    model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    model.fit(probs, labels)
    return lambda x: np.clip(model.predict(x), 0.0, 1.0)


def fit_platt(probs: np.ndarray, labels: np.ndarray):
    from sklearn.linear_model import LogisticRegression
    eps = 1e-6
    logit = np.log(np.clip(probs, eps, 1 - eps) / (1 - np.clip(probs, eps, 1 - eps)))
    model = LogisticRegression(max_iter=1000).fit(logit.reshape(-1, 1), labels)

    def apply(x):
        lx = np.log(np.clip(x, eps, 1 - eps) / (1 - np.clip(x, eps, 1 - eps)))
        return model.predict_proba(lx.reshape(-1, 1))[:, 1]
    return apply


def round_equal_metric(fn, probs, labels, rounds, draws=ROUND_EQUAL_DRAWS, seed=211):
    """`fn(probs, labels)` averaged over draws that take ONE crossing per round.

    Crossings inside a round are the same market state observed repeatedly. Pooling them lets
    choppy rounds dominate - the correction CROSSING_HEADS_V1 already had to apply to AUC."""
    rounds = np.asarray(rounds)
    grouped: dict = {}
    for index, key in enumerate(rounds):
        grouped.setdefault(key, []).append(index)
    buckets = [np.array(v) for v in grouped.values()]
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(draws):
        picked = np.array([b[rng.integers(0, len(b))] for b in buckets])
        y = labels[picked]
        if len(np.unique(y)) < 2:
            continue
        values.append(fn(probs[picked], y))
    return (float(np.mean(values)), float(np.std(values))) if values else (float("nan"),) * 2


def weighting_decomposition(probs, labels, rounds, draws=ROUND_EQUAL_DRAWS) -> dict:
    """Split the round-equal ECE into a POPULATION SHIFT and a genuine calibration residual.

    THE DEFECT THIS EXISTS TO EXPOSE, WHICH WAS MINE
        The frozen protocol declared round-equal weighting for ECE, carrying over the correction
        CROSSING_HEADS_V1 needed for AUC. For AUC that correction is right: it stops a handful of
        choppy rounds from dominating a RANKING statistic.

        For ECE it is wrong, and provably so. Sampling one crossing per round changes the
        population's BASE RATE. For `is_final_crossing` each round has exactly one final
        crossing, so

            pooled base rate      = n_rounds / n_crossings = 1 / mean(n)   = 0.370
            round-equal base rate = mean(1 / n)                            = 0.550

        and those differ by Jensen's inequality whenever round sizes vary at all. The head's
        probabilities are unchanged by the resampling, so a PERFECTLY calibrated head must show
        an ECE of about |mean(p) - base| on the resampled population. Measured, that term is the
        whole of it: 0.1563 of a 0.1563 ECE.

        So the primary endpoint measured the reweighting, not the head. This function reports
        both parts so the number cannot be read as the head's miscalibration.
    """
    pooled_base = float(np.mean(labels))
    re_base, _ = round_equal_metric(lambda p, y: float(y.mean()), probs, labels, rounds, draws)
    re_mean, _ = round_equal_metric(lambda p, y: float(p.mean()), probs, labels, rounds, draws)
    re_ece, _ = round_equal_metric(ece, probs, labels, rounds, draws)
    shift = abs(re_mean - re_base)
    return {"pooled_base": pooled_base, "pooled_mean": float(np.mean(probs)),
            "pooled_ece": ece(probs, labels), "round_equal_base": re_base,
            "round_equal_mean": re_mean, "round_equal_ece": re_ece,
            "base_rate_shift": re_base - pooled_base, "explained_by_shift": shift,
            "residual": re_ece - shift}


def decision_flip_rate(raw: np.ndarray, calibrated: np.ndarray,
                       threshold: float = EV_THRESHOLD) -> float:
    """Share of cases whose implied action changes. Calibration that flips nothing is cosmetic."""
    return float(np.mean((raw >= threshold) != (calibrated >= threshold)))


def verdict_for(raw_ece, best_ece, flip, auc_shift) -> tuple[str, str]:
    if abs(auc_shift) > AUC_TOLERANCE:
        return ("CALIBRATION_FAILS",
                f"AUC moved {auc_shift:+.4f} - a monotone calibrator cannot do that, so the "
                f"procedure is broken")
    if raw_ece <= MATERIAL_ECE:
        return ("HEAD_IS_CALIBRATED",
                f"raw ECE {raw_ece:.4f} is already within the {MATERIAL_ECE:.2f} bar")
    if best_ece > raw_ece:
        return ("CALIBRATION_FAILS", "no calibrator reduced ECE")
    if (raw_ece - best_ece) >= MATERIAL_ECE and flip >= MATERIAL_FLIP:
        return ("CALIBRATION_MATERIALLY_IMPROVES",
                f"ECE {raw_ece:.4f} -> {best_ece:.4f} and {flip:.1%} of decisions flip")
    return ("CALIBRATION_COSMETIC",
            f"ECE {raw_ece:.4f} -> {best_ece:.4f} but only {flip:.1%} of decisions flip")


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    rng = np.random.default_rng(0)
    n = 20_000
    truth = rng.random(n)
    labels = (rng.random(n) < truth).astype(int)

    check(ece(truth, labels) < 0.02, "a PERFECTLY calibrated score has near-zero ECE")
    overconfident = np.clip(truth * 1.6 - 0.3, 0, 1)
    check(ece(overconfident, labels) > 0.05,
          "a systematically overconfident score has a LARGE ECE - the metric can fail")
    check(abs(auc(truth, labels) - auc(overconfident, labels)) < 0.02,
          "...while its AUC is barely changed - which is exactly why AUC cannot see this")

    fitted = fit_isotonic(overconfident[:10_000], labels[:10_000])
    after = fitted(overconfident[10_000:])
    check(ece(after, labels[10_000:]) < ece(overconfident[10_000:], labels[10_000:]),
          "isotonic fitted on TRAIN reduces ECE on unseen data")
    check(abs(auc(after, labels[10_000:])
              - auc(overconfident[10_000:], labels[10_000:])) < AUC_TOLERANCE,
          "a monotone calibrator leaves AUC unchanged - the correctness check on the procedure")

    platt = fit_platt(overconfident[:10_000], labels[:10_000])
    check(ece(platt(overconfident[10_000:]), labels[10_000:])
          < ece(overconfident[10_000:], labels[10_000:]),
          "Platt scaling also reduces ECE on unseen data")

    decomposition = brier_decomposition(truth, labels)
    check(abs(decomposition["brier"] -
              (decomposition["reliability"] - decomposition["resolution"]
               + decomposition["uncertainty"])) < 0.01,
          "the Murphy decomposition reconstructs the Brier score")
    check(brier_decomposition(overconfident, labels)["reliability"]
          > decomposition["reliability"],
          "an overconfident score has WORSE reliability, which is the term calibration fixes")

    check(decision_flip_rate(np.array([0.4, 0.6]), np.array([0.6, 0.4])) == 1.0,
          "both sides crossing the threshold is a 100% flip rate")
    check(decision_flip_rate(np.array([0.4, 0.6]), np.array([0.45, 0.55])) == 0.0,
          "a shift that crosses no threshold flips nothing - cosmetic is detectable")

    rounds = np.array([f"r{i // 3}" for i in range(n)])
    mean_ece, sd = round_equal_metric(ece, truth, labels, rounds, draws=40)
    check(np.isfinite(mean_ece) and mean_ece < 0.05,
          "round-equal weighting produces a finite ECE on a calibrated score when round size "
          "carries no information")
    check(sd >= 0.0, "the draw-to-draw spread is reported")

    # THE PROTOCOL DEFECT, PINNED. Build a PERFECTLY calibrated score on a population where
    # each round holds exactly one positive - the structure of `is_final_crossing`. Round-equal
    # resampling must then inflate ECE purely through the base rate, with the head untouched.
    # If a future edit "fixes" this by reporting only one weighting, these checks fail.
    sizes, labels_list, round_list = [1, 2, 4, 8, 16], [], []
    rng2 = np.random.default_rng(7)
    for size in sizes:
        for r in range(600):
            winner = rng2.integers(0, size)
            for k in range(size):
                labels_list.append(1 if k == winner else 0)
                round_list.append(f"s{size}_r{r}")
    sl = np.array(labels_list)
    sr = np.array(round_list)
    # The score must be BLIND TO ROUND SIZE, as the real head is: how many more crossings a
    # round will produce is not causally available at the moment of a crossing. A score of
    # 1/size would shift with the base rate and hide the whole effect - which is exactly the
    # mistake the first version of this fixture made.
    sp = np.full(len(sl), float(sl.mean()))
    check(ece(sp, sl) < 0.01,
          "a BY-CONSTRUCTION perfect score has near-zero POOLED ECE")
    parts = weighting_decomposition(sp, sl, sr, draws=60)
    check(parts["round_equal_ece"] > 0.10,
          "...yet round-equal weighting reports a LARGE ECE for that same perfect score - the "
          "primary endpoint of the frozen protocol measures the reweighting, not the head")
    check(parts["round_equal_base"] > parts["pooled_base"] + 0.10,
          "the cause is a base-rate shift: mean(1/n) exceeds 1/mean(n) by Jensen")
    check(abs(parts["residual"]) < 0.02,
          "and the shift explains essentially ALL of it - residual calibration error is ~0")
    check(parts["pooled_ece"] < parts["round_equal_ece"],
          "the decomposition reports BOTH weightings, so neither can be quoted alone")

    kind, _ = verdict_for(0.01, 0.005, 0.5, 0.0)
    check(kind == "HEAD_IS_CALIBRATED", "an already-calibrated head needs no calibration")
    kind, _ = verdict_for(0.10, 0.02, 0.05, 0.0)
    check(kind == "CALIBRATION_MATERIALLY_IMPROVES", "a large ECE gain that flips decisions passes")
    kind, _ = verdict_for(0.10, 0.02, 0.001, 0.0)
    check(kind == "CALIBRATION_COSMETIC", "a large ECE gain that flips nothing is COSMETIC")
    kind, _ = verdict_for(0.10, 0.12, 0.5, 0.0)
    check(kind == "CALIBRATION_FAILS", "a calibrator that worsens ECE fails")
    kind, _ = verdict_for(0.10, 0.02, 0.5, 0.05)
    check(kind == "CALIBRATION_FAILS",
          "an AUC shift means a non-monotone calibrator - the procedure is broken, not the head")

    print(f"\nCROSSING CALIBRATION SELFTEST: PASS ({checks} checks)")
    return 0


def run() -> int:
    import lightgbm as lgb
    from crossing_heads_v1 import (BASELINE_FEATURE, FEATURES, TARGETS, TRAIN_FRACTION, load)

    frame = load()
    days = np.sort(frame["day"].unique())
    split_day = days[int(len(days) * TRAIN_FRACTION)]

    print("=" * 100)
    print(f"CROSSING CALIBRATION V1 - protocol {PROTOCOL} (frozen before fitting)")
    print("=" * 100)
    print(f"  {len(frame):,} crossings   split by DAY   calibrators fitted on TRAIN only")
    print(f"  ECE over {BINS} equal-count bins, ROUND-EQUAL weighted   "
          f"materiality {MATERIAL_ECE:.2f} ECE and {MATERIAL_FLIP:.0%} decision flips")
    _ = BASELINE_FEATURE

    params = dict(n_estimators=200, learning_rate=0.05, num_leaves=15,
                  min_child_samples=100, verbose=-1, random_state=0)
    for target in TARGETS:
        usable = frame.dropna(subset=[target] + list(FEATURES)).copy()
        train = usable[usable["day"] < split_day]
        test = usable[usable["day"] >= split_day]
        ytr = train[target].astype(int).to_numpy()
        yte = test[target].astype(int).to_numpy()
        if len(train) < 200 or len(test) < 100 or len(np.unique(ytr)) < 2:
            print(f"\n  --- {target}: insufficient data")
            continue

        model = lgb.LGBMClassifier(**params).fit(train[list(FEATURES)].to_numpy(float), ytr)
        raw_tr = model.predict_proba(train[list(FEATURES)].to_numpy(float))[:, 1]
        raw_te = model.predict_proba(test[list(FEATURES)].to_numpy(float))[:, 1]
        rounds = test["round_id"].to_numpy()

        iso = fit_isotonic(raw_tr, ytr)(raw_te)
        platt = fit_platt(raw_tr, ytr)(raw_te)

        print(f"\n  --- {target}   train {len(train):,} / test {len(test):,}   "
              f"base rate {yte.mean():.1%}")
        results = {}
        for name, probs in (("RAW", raw_te), ("ISOTONIC", iso), ("PLATT", platt)):
            e, sd = round_equal_metric(ece, probs, yte, rounds)
            decomposition = brier_decomposition(probs, yte)
            results[name] = {"ece": e, "sd": sd, "auc": auc(probs, yte),
                             "brier": decomposition["brier"],
                             "reliability": decomposition["reliability"],
                             "mean": float(probs.mean())}
            print(f"      {name:<10} ECE {e:.4f} (sd {sd:.4f})   Brier {decomposition['brier']:.4f}"
                  f"   reliability {decomposition['reliability']:.5f}"
                  f"   mean p {probs.mean():.3f}   AUC {results[name]['auc']:.4f}")
        print(f"      observed base rate {yte.mean():.3f}")

        best = min(("ISOTONIC", "PLATT"), key=lambda k: results[k]["ece"])
        flip = decision_flip_rate(raw_te, iso if best == "ISOTONIC" else platt)
        shift = results[best]["auc"] - results["RAW"]["auc"]
        verdict, reason = verdict_for(results["RAW"]["ece"], results[best]["ece"], flip, shift)
        print(f"      best calibrator {best}   decision flips {flip:.2%}   "
              f"AUC shift {shift:+.4f}")
        print(f"      VERDICT: {verdict}   (primary endpoint, as frozen)")
        print(f"      {reason}")

        pinned = FIRST_SCORING.get(target)
        if pinned:
            drift = abs(results["RAW"]["ece"] - pinned["raw_ece"])
            state = "matches" if drift <= SCORING_TOLERANCE else "DRIFTED"
            print(f"      scored-once check: RAW ECE {results['RAW']['ece']:.4f} vs recorded "
                  f"{pinned['raw_ece']:.4f} - {state}")
            if drift > SCORING_TOLERANCE or verdict != pinned["verdict"]:
                raise SystemExit(
                    f"{target}: this run does not reproduce the single scoring of record "
                    f"(ECE {results['RAW']['ece']:.4f} vs {pinned['raw_ece']:.4f}, verdict "
                    f"{verdict} vs {pinned['verdict']}). Something upstream changed; the "
                    f"protocol was scored once and may not be re-scored to a new number.")

        # WHY THE PRIMARY ENDPOINT SAYS WHAT IT SAYS. Not a re-score - a decomposition of the
        # number already reported, which shows it is dominated by the reweighting I specified.
        parts = weighting_decomposition(raw_te, yte, rounds)
        print("      weighting decomposition of that ECE:")
        print(f"        pooled      base {parts['pooled_base']:.4f}  mean p "
              f"{parts['pooled_mean']:.4f}  ECE {parts['pooled_ece']:.4f}")
        print(f"        round-equal base {parts['round_equal_base']:.4f}  mean p "
              f"{parts['round_equal_mean']:.4f}  ECE {parts['round_equal_ece']:.4f}")
        print(f"        base-rate shift {parts['base_rate_shift']:+.4f}   explained by shift "
              f"{parts['explained_by_shift']:.4f}   residual {parts['residual']:.4f}")

        print("      reliability curve, pooled (predicted -> observed, count):")
        for predicted, observed, count in reliability(raw_te, yte):
            print(f"        {predicted:.3f} -> {observed:.3f}  ({count})")

    print()
    print("  READING THIS CORRECTLY")
    print("  The primary endpoint is reported as frozen and the verdicts stand. But the")
    print("  round-equal weighting the protocol declared for ECE was a specification error of")
    print("  mine, carried over from the AUC correction where it was right. Resampling one")
    print("  crossing per round changes the population's base rate, and the decomposition above")
    print("  shows that shift accounts for essentially the entire reported ECE.")
    print()
    print("  The pooled figures are DIAGNOSTIC ONLY. They were computed after the primary")
    print("  endpoint was unblinded, so they carry no verdict and no threshold may be declared")
    print("  against them retroactively - a bar set after seeing the number is not a bar.")
    print()
    print("  What survives both weightings: no calibrator improved anything. Isotonic made the")
    print("  Murphy reliability term an order of magnitude WORSE, which is overfitting the")
    print("  train distribution, not calibration. The actionable conclusion does not depend on")
    print("  which weighting is used - fitting a calibrator on this head is not worth doing.")
    print()
    print("  A calibrated probability is still an INPUT to a decision, not a decision. Every")
    print("  action lane in this repository is closed on cost, and calibration does not")
    print("  change that - it makes the EV arithmetic honest when a lane finally opens.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    raise SystemExit(selftest() if args.selftest else run())
