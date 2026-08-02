"""EV_MAGNITUDE_RULE_V1 - decide on predicted VALUE, not on a threshold over predicted sign.

WHAT THE PREVIOUS STUDY ESTABLISHED
    hold_vs_exit_head_v1 trained a classifier on "will exiting beat holding?". It reached
    AUC 0.8731 with ECE 0.0126 - it ranks the decision very well - and still lost to doing
    nothing, because the threshold selected on calibration days fired on 8.2% of rows against
    a 23.4% base rate and did not transfer.

    The payoff structure is not the problem. It favours exiting when right:

        exit beats hold : 23.4% of rows, mean gain +0.4758
        hold beats exit : 76.6% of rows, mean loss -0.1713   (gain is 2.8x the loss)

    A threshold on P(sign) throws that away: it treats a row worth +0.50 and a row worth +0.01
    as the same decision. This study replaces the threshold with the quantity that actually
    matters.

THE RULE, AND WHY IT HAS NO FREE PARAMETER
    Regress the incremental value directly

        d = exit_value - hold_value

    and EXIT when the predicted d is above zero. That is the whole rule. There is no threshold
    to sweep, no grid, and nothing to select on the evaluation set - which matters here more
    than usual, because the previous study already spent one look at this window.

THIS IS THE SECOND TEST ON THE SAME EVALUATION SET
    RETROSPECTIVE_VALIDATION has now been looked at twice: once for the classifier, once here.
    Two looks at one window is two chances to find noise, so the lower bound is taken at the
    BONFERRONI-CORRECTED 2.5% quantile rather than the usual 5%. That is stated before the
    result, and the uncorrected bound is printed beside it so the correction cannot be quietly
    dropped later.

    The only clean resolution is forward data. There are still ZERO FORWARD_UNTOUCHED rows.

PROTOCOL, DECLARED BEFORE RESULTS
    G1  regressor fitted on the earlier 70% of LIVE_RESEARCH days; the later 30% is used ONLY
        to verify the predictions are unbiased, never to tune the rule
    G2  features from causal_validation.feature_columns(); no label, no outcome, clock included
    G3  the decision rule is `predicted d > 0`, fixed in advance, no parameter
    G4  PASS requires beating ALWAYS_HOLD, ALWAYS_EXIT and the classifier head, AND a
        Bonferroni-corrected day-block lower bound above zero
    G5  matched-count RANDOM control, so acting less often cannot read as skill

    python research/ev_magnitude_rule_v1.py
    python research/ev_magnitude_rule_v1.py --selftest
"""
from __future__ import annotations

RESEARCH_STATUS = "VALID_DIAGNOSTIC"
CAPITAL_AUTHORITY = False

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "research_data"))
sys.path.insert(0, str(ROOT / "backend" / "polymarket_policy"))

import hold_vs_exit_head_v1 as previous  # noqa: E402
from causal_validation import feature_columns  # noqa: E402

#: Two hypotheses have now been tested on this evaluation window.
TESTS_ON_THIS_WINDOW = 2
BASE_ALPHA = 0.05
TRAIN_FRACTION = 0.70
MIN_ROWS = 500
RNG = np.random.default_rng(20260802)
BOOTSTRAP_DRAWS = 2000

MODEL_KWARGS = dict(max_iter=300, max_depth=4, learning_rate=0.06,
                    min_samples_leaf=60, l2_regularization=1.0, random_state=42)


def day_block_lcb_at(values, days, quantile: float, draws: int = BOOTSTRAP_DRAWS) -> float:
    """Day-block bootstrap lower bound at an ARBITRARY quantile.

    The shared helper is hardcoded to 5%, and a Bonferroni correction needs 2.5%. Rather than
    passing a magic number into a function that does not accept one, this reimplements the same
    estimator with the quantile exposed - and the selftest pins the two to agree at 5%, so the
    copies cannot drift apart while both look authoritative."""
    unique = np.unique(days)
    if len(unique) < 5 or len(values) == 0:
        return float("nan")
    by_day = {day: values[days == day] for day in unique}
    generator = np.random.default_rng(20260801)
    means = np.empty(draws)
    for index in range(draws):
        picked = generator.integers(0, len(unique), len(unique))
        means[index] = np.concatenate([by_day[unique[j]] for j in picked]).mean()
    means.sort()
    return float(means[int(quantile * draws)])


def evaluate(name, exit_flags, hold, exit_value, days) -> dict:
    value = np.where(exit_flags, exit_value, hold)
    corrected = BASE_ALPHA / TESTS_ON_THIS_WINDOW
    return {"policy": name, "exits": int(exit_flags.sum()), "mean": float(value.mean()),
            "lcb_5pct": day_block_lcb_at(value, days, BASE_ALPHA),
            "lcb_corrected": day_block_lcb_at(value, days, corrected)}


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    from phold_auc_and_expectancy import day_block_lcb

    rng = np.random.default_rng(3)
    values = rng.normal(0.02, 0.1, 2000)
    days = np.repeat(np.arange(20), 100)
    mine = day_block_lcb_at(values, days, 0.05)
    theirs = day_block_lcb(values, days)
    check(abs(mine - theirs) < 0.01,
          "the local lower bound agrees with the shared helper at 5% - the copies cannot drift")
    check(day_block_lcb_at(values, days, 0.025) < mine,
          "the Bonferroni-corrected 2.5% bound is STRICTER than the uncorrected 5% one")

    hold = np.array([0.30, -0.60, 0.30, -0.60])
    exits = np.array([-0.05, -0.05, -0.05, -0.05])
    d = exits - hold
    check(list(np.sign(d)) == [-1.0, 1.0, -1.0, 1.0],
          "the incremental value is positive exactly where exiting was the better action")

    # The rule itself: exit where predicted d > 0. With perfect predictions it is the perfect
    # policy; with predictions of the wrong SIGN it must be worse than always-hold.
    days4 = np.array([1, 1, 2, 2])
    perfect = evaluate("PERFECT", d > 0, hold, exits, days4)
    inverted = evaluate("INVERTED", (-d) > 0, hold, exits, days4)
    check(perfect["mean"] > inverted["mean"],
          "a rule fed correct-sign predictions beats one fed inverted predictions")
    check(abs(perfect["mean"] - 0.125) < 1e-12,
          "the EV rule with perfect predictions realises the perfect policy exactly")

    columns = ["up_ask", "seconds_left", "label_remaining_range_usd", "settled_side"]
    offered = feature_columns(columns)
    check("label_remaining_range_usd" not in offered and "settled_side" not in offered,
          "no label or outcome column can enter the regressor's feature matrix")

    print(f"\nEV MAGNITUDE RULE SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    print("=" * 104)
    print("EV MAGNITUDE RULE - exit when the PREDICTED incremental value is positive")
    print("=" * 104)
    if args.selftest:
        return selftest()
    try:
        from sklearn.ensemble import (HistGradientBoostingClassifier,
                                      HistGradientBoostingRegressor)
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        print("  scikit-learn is required")
        return 1
    for path in (previous.CHECKPOINTS, previous.LABELS):
        if not path.is_file():
            print(f"  BLOCKED: {path.name} is missing.")
            return 0

    frame = previous.load()
    bids = previous.fetch_horizon_bids(frame)
    frame = frame.merge(bids, on=["slug", "checkpoint_s"], how="left")
    hold, exit_value = previous.attach_action_values(
        frame, frame["bid_at_horizon"].to_numpy(float))
    usable = np.isfinite(exit_value) & np.isfinite(hold)
    frame = frame[usable].reset_index(drop=True)
    hold, exit_value = hold[usable], exit_value[usable]

    incremental = exit_value - hold                       # the quantity the rule predicts
    days = (frame["snapshot_ts"].to_numpy(float) // 86400).astype(np.int64)
    names = [c for c in feature_columns(frame.columns)
             if frame[c].dtype.kind in "fiu" and frame[c].notna().mean() > 0.95]
    matrix = frame[names].to_numpy(float)

    live = frame["evidence_class"].to_numpy() == "LIVE_RESEARCH"
    test = frame["evidence_class"].to_numpy() == "RETROSPECTIVE_VALIDATION"
    live_days = np.sort(np.unique(days[live]))
    if len(live_days) < 5 or test.sum() < MIN_ROWS:
        print("  BLOCKED: not enough LIVE_RESEARCH days or evaluation rows.")
        return 0
    cut = live_days[int(len(live_days) * TRAIN_FRACTION)]
    train, holdout = live & (days < cut), live & (days >= cut)

    print(f"  rows {len(frame):,} | features {len(names)}")
    print(f"  incremental value d: mean {incremental.mean():+.4f}, "
          f"positive on {(incremental > 0).mean():.1%} of rows")
    print(f"  train {train.sum():,}  unbiasedness-check {holdout.sum():,}  "
          f"evaluate {test.sum():,}")
    print(f"  DECISION RULE: exit when predicted d > 0. No threshold, no grid, no parameter.")
    print(f"  This is test {TESTS_ON_THIS_WINDOW} on this window, so the gate uses the "
          f"Bonferroni-corrected {BASE_ALPHA / TESTS_ON_THIS_WINDOW:.3f} quantile.")

    regressor = HistGradientBoostingRegressor(**MODEL_KWARGS)
    regressor.fit(matrix[train], incremental[train])
    predicted = regressor.predict(matrix)

    bias = predicted[holdout].mean() - incremental[holdout].mean()
    print()
    print(f"  unbiasedness on held-out LIVE days: predicted {predicted[holdout].mean():+.4f} "
          f"vs realised {incremental[holdout].mean():+.4f}  (bias {bias:+.4f})")

    # The classifier from the previous study, rebuilt identically so the two are comparable.
    target = (incremental > 0).astype(int)
    classifier = HistGradientBoostingClassifier(**previous.MODEL_KWARGS)
    classifier.fit(matrix[train], target[train])
    iso = IsotonicRegression(out_of_bounds="clip").fit(
        classifier.predict_proba(matrix[holdout])[:, 1], target[holdout])
    probability = iso.predict(classifier.predict_proba(matrix)[:, 1])

    rows = [
        evaluate("ALWAYS_HOLD", np.zeros(test.sum(), bool), hold[test], exit_value[test],
                 days[test]),
        evaluate("ALWAYS_EXIT", np.ones(test.sum(), bool), hold[test], exit_value[test],
                 days[test]),
        evaluate("CLASSIFIER@0.65", probability[test] > 0.65, hold[test], exit_value[test],
                 days[test]),
        evaluate("EV_RULE (d > 0)", predicted[test] > 0, hold[test], exit_value[test],
                 days[test]),
    ]
    matched = np.zeros(test.sum(), bool)
    take = int((predicted[test] > 0).sum())
    if take:
        matched[RNG.choice(test.sum(), size=take, replace=False)] = True
    rows.append(evaluate("RANDOM_MATCHED", matched, hold[test], exit_value[test], days[test]))
    rows.append(evaluate("PERFECT (hindsight)", incremental[test] > 0, hold[test],
                         exit_value[test], days[test]))

    print()
    print(f"{'policy':<24}{'exits':>9}{'mean/$1':>11}{'LCB 5%':>11}{'LCB 2.5%':>11}")
    for row in rows:
        print(f"{row['policy']:<24}{row['exits']:>9,}{row['mean']:>11.4f}"
              f"{row['lcb_5pct']:>11.4f}{row['lcb_corrected']:>11.4f}")

    rule = next(r for r in rows if r["policy"].startswith("EV_RULE"))
    bar = max(r["mean"] for r in rows[:3])
    print()
    beats = rule["mean"] > bar
    clears = rule["lcb_corrected"] > 0
    if beats and clears:
        print("  VERDICT: PASSES every declared gate - beats all three fixed policies and the")
        print("  corrected lower bound clears zero. A forward run is the next step, not a")
        print("  promotion: zero FORWARD_UNTOUCHED rows exist.")
    elif beats:
        print(f"  VERDICT: beats every fixed policy ({rule['mean']:+.4f} vs {bar:+.4f}) but the")
        print(f"  corrected lower bound is {rule['lcb_corrected']:+.4f}. Not distinguishable")
        print("  from noise under two looks at one window. NOT a candidate.")
    else:
        print(f"  VERDICT: FAILS - {rule['mean']:+.4f} does not beat the best fixed policy "
              f"({bar:+.4f}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
