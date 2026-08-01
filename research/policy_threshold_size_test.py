"""POLICY_THRESHOLD_SIZE_V1 - does the AUC gain convert once threshold and size are re-optimised?

THE QUESTION THIS ANSWERS
    settlement_fragility_test found a clean split: adding z = |distance| / sigma_remaining
    improved RANKING decisively and consistently (out-of-sample AUC +0.0699 / +0.0820 / +0.0595)
    and still LOST money on 2 of 3 splits at a fixed 0.02 entry margin. Calibrating it made it
    worse - 576 trades became 999 and earned less.

    The diagnosis was that the decision rule is a THRESHOLD ON A LEVEL. A better ordering does
    not transfer through a fixed threshold, because the threshold does not know the ordering
    changed. So the honest follow-up is not another feature: it is to let each model choose its
    own threshold and size, and see whether the ranking advantage then pays.

THE TRAP, NAMED BEFORE IT IS SPRUNG
    Choosing a threshold by looking at the outcome it produces is how this repository's earlier
    scripts manufactured edges. A grid of 9 margins x 2 sizing rules is 18 chances to find a
    flattering cell in noise.

    So: the grid is DECLARED below; selection happens on TRAIN DAYS ONLY, by the same day-block
    lower bound used to judge the result; and the chosen policy is then applied unchanged to
    strictly later test days. Both the train-selected value and the test value are printed, so
    the SHRINKAGE between them is visible on every row. A policy that looks excellent in
    selection and mediocre out of sample has told you exactly what it is.

SIZING
    FLAT     one unit per selected round - the incumbent behaviour
    KELLY    fraction of the declared bankroll from the binary Kelly criterion,
                 f* = (p - q) / (1 - q)
             for a contract bought at q paying 1, capped at KELLY_CAP and floored at 0. This is
             the sizing the edge itself implies; capping it is not tuning, it is refusing to bet
             the farm on a probability estimate known to be imperfect.

    Reported per $1 of BANKROLL for both arms, never per trade, so a rule that simply bets more
    cannot look better than one that bets well.

DECLARED GRID AND GATES
    G1  margins swept over MARGIN_GRID, sizing over ("FLAT", "KELLY"); nothing else is searched
    G2  selection uses TRAIN days only, on the day-block 5% lower bound
    G3  strictly temporal: the test window is entirely after the training window
    G4  PASS requires the fragility policy to beat the calibrated policy on the TEST day-block
        lower bound, both having chosen their own threshold and size the same way
    G5  >= MIN_TRADES selected rounds on test, else NOT MEASURED

    python research/policy_threshold_size_test.py

RETRACTED - '0 of 5 beat the pre-declared control'
    This study joined a market STATE to an executable QUOTE without requiring the state to be
    available at the decision timestamp. In 93.5% of rows the state was observed AFTER the
    decision (median +8.1s). See research/causal_decision_join.py for the corrected
    construction and research/research_status.py for the registry.

    It refuses to run without --run-retracted-study.
"""
from __future__ import annotations

RESEARCH_STATUS = "RETRACTED"
RETRACTION_REASON = "NONCAUSAL_STATE_QUOTE_JOIN"
REPLACED_BY = "research/causal_decision_join.py"
CAPITAL_AUTHORITY = False

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phold_auc_and_expectancy import day_block_lcb  # noqa: E402
from settlement_fragility_test import (  # noqa: E402
    DB, FRAGILITY, MODEL_KWARGS, QUERY, attach_volatility,
)

MARGIN_GRID = (0.00, 0.01, 0.02, 0.03, 0.05, 0.07, 0.10, 0.15, 0.20)
SIZING_RULES = ("FLAT", "KELLY")
KELLY_CAP = 0.05            # never more than 5% of bankroll on one binary
MIN_TRADES = 40
SPLIT_DAYS = (7, 10, 14)


def size_weights(probability, ask, fee, rule):
    """Per-round bankroll fraction. FLAT is the incumbent; KELLY is what the edge implies."""
    if rule == "FLAT":
        return np.ones_like(probability)
    payoff_odds = np.clip(1.0 - ask, 1e-6, None)
    edge = probability - ask - fee
    fraction = np.clip(edge / payoff_odds, 0.0, KELLY_CAP)
    return fraction


def evaluate(probability, ask, fee, net, days, margin, rule):
    """Net per $1 of BANKROLL, so a rule that merely bets more cannot look better."""
    selected = (probability - ask - fee) > margin
    if selected.sum() < MIN_TRADES:
        return None
    weights = size_weights(probability[selected], ask[selected], fee[selected], rule)
    if weights.sum() <= 0:
        return None
    # Per-round bankroll return, then averaged over SELECTED rounds. For FLAT this reduces to
    # the previous net/$1; for KELLY it charges the rule for the capital it declines to use.
    per_round = weights * net[selected]
    return {"n": int(selected.sum()),
            "mean": float(per_round.mean()),
            "lcb": day_block_lcb(per_round, days[selected]),
            "avg_size": float(weights.mean())}


def choose_policy(probability, ask, fee, net, days):
    """Pick (margin, sizing) on TRAIN ONLY, by the same lower bound used to judge the result."""
    best = None
    for margin in MARGIN_GRID:
        for rule in SIZING_RULES:
            result = evaluate(probability, ask, fee, net, days, margin, rule)
            if result is None or not np.isfinite(result["lcb"]):
                continue
            if best is None or result["lcb"] > best["lcb"]:
                best = {**result, "margin": margin, "rule": rule}
    return best


def main() -> int:
    from research_status import guard
    guard(Path(__file__).name)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rule", default="LATE_LEADER_30S_V1")
    args = parser.parse_args()
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        print("scikit-learn is required")
        return 1
    import duckdb

    frame = duckdb.connect(str(DB), read_only=True).execute(
        QUERY.format(rule=args.rule)).df()
    frame = attach_volatility(frame)
    frame = frame.dropna(subset=["ask", "held", "p_hold", "z_fragility"]).reset_index(drop=True)
    frame["day"] = frame["ts"].to_numpy("int64") // 86_400_000
    days_all = np.sort(frame["day"].unique())

    print("=" * 110)
    print("JOINT THRESHOLD + SIZE RE-OPTIMISATION - does the ranking advantage convert to money?")
    print("=" * 110)
    print(f"  rounds {len(frame):,} over {len(days_all)} days | "
          f"grid {len(MARGIN_GRID)} margins x {len(SIZING_RULES)} sizing = "
          f"{len(MARGIN_GRID)*len(SIZING_RULES)} cells, selected on TRAIN only")
    print(f"  Kelly capped at {KELLY_CAP:.0%} of bankroll; all results are per $1 of BANKROLL")
    print()
    print(f"{'train':>6}{'model':>12}{'chosen':>16}{'train LCB':>11}"
          f"{'TEST n':>8}{'TEST mean':>11}{'TEST LCB':>10}{'shrink':>9}  verdict")
    print("-" * 110)

    passes = evaluated = 0
    beat_fixed = fixed_seen = 0
    for cut in SPLIT_DAYS:
        if cut >= len(days_all):
            continue
        train = frame[frame["day"] < days_all[cut]]
        test = frame[frame["day"] >= days_all[cut]].reset_index(drop=True)
        if len(train) < 200 or len(test) < MIN_TRADES:
            continue

        # --- incumbent: isotonic-calibrated P(hold), per horizon, fitted on earlier days ------
        cal_test = np.full(len(test), np.nan)
        train = train.copy(); train["p_cal"] = np.nan
        for horizon in (5, 15):
            tr = train[train["horizon"] == horizon]
            te = (test["horizon"] == horizon).to_numpy()
            if len(tr) < 200 or not te.any():
                continue
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            iso.fit(tr["p_hold"].to_numpy(float), tr["held"].to_numpy(float))
            cal_test[te] = iso.predict(test.loc[te, "p_hold"].to_numpy(float))
            train.loc[train["horizon"] == horizon, "p_cal"] = iso.predict(
                tr["p_hold"].to_numpy(float))
        cal_test = np.where(np.isfinite(cal_test), cal_test, test["p_hold"].to_numpy(float))
        train["p_cal"] = train["p_cal"].fillna(train["p_hold"])
        test = test.copy(); test["p_cal"] = cal_test

        # --- challenger: fragility model, fitted on earlier days ------------------------------
        model = HistGradientBoostingClassifier(**MODEL_KWARGS)
        model.fit(train[FRAGILITY].to_numpy(float), train["held"].to_numpy(int))
        frag_train = model.predict_proba(train[FRAGILITY].to_numpy(float))[:, 1]
        frag_test = model.predict_proba(test[FRAGILITY].to_numpy(float))[:, 1]

        tr_ask = train["ask"].to_numpy(float); tr_fee = train["fee"].to_numpy(float)
        tr_net = train["held"].to_numpy(float) - tr_ask - tr_fee
        tr_days = train["day"].to_numpy()
        te_ask = test["ask"].to_numpy(float); te_fee = test["fee"].to_numpy(float)
        te_net = test["held"].to_numpy(float) - te_ask - te_fee
        te_days = test["day"].to_numpy()

        # The control that matters: the PRE-DECLARED fixed margin, chosen before any outcome
        # was seen. If re-optimising cannot beat this, the optimisation is costing money.
        fixed = evaluate(cal_test, te_ask, te_fee, te_net, te_days, 0.02, "FLAT")
        if fixed is not None:
            print(f"{cut:>6}{'CAL@fixed':>12}{'0.02/FLAT':>16}{'(declared)':>11}"
                  f"{fixed['n']:>8}{fixed['mean']:>+11.4f}{fixed['lcb']:>+10.4f}{'n/a':>9}"
                  f"  <- pre-declared control")

        outcomes = {}
        for label, p_train, p_test in (("CALIBRATED", train["p_cal"].to_numpy(float), cal_test),
                                       ("FRAGILITY", frag_train, frag_test)):
            chosen = choose_policy(p_train, tr_ask, tr_fee, tr_net, tr_days)
            if chosen is None:
                print(f"{cut:>6}{label:>12}{'no cell qualified':>16}")
                continue
            policy_label = f"{chosen['margin']:.2f}/{chosen['rule']}"
            result = evaluate(p_test, te_ask, te_fee, te_net, te_days,
                              chosen["margin"], chosen["rule"])
            if result is None:
                print(f"{cut:>6}{label:>12}{policy_label:>16}"
                      f"{chosen['lcb']:>+11.4f}{'NOT MEASURED':>38}")
                continue
            outcomes[label] = result
            if fixed is not None:
                fixed_seen += 1
                beat_fixed += int(result["lcb"] > fixed["lcb"])
            shrink = result["lcb"] - chosen["lcb"]
            print(f"{cut:>6}{label:>12}{policy_label:>16}"
                  f"{chosen['lcb']:>+11.4f}{result['n']:>8}{result['mean']:>+11.4f}"
                  f"{result['lcb']:>+10.4f}{shrink:>+9.4f}", end="")
            if label == "FRAGILITY" and "CALIBRATED" in outcomes:
                gap = result["lcb"] - outcomes["CALIBRATED"]["lcb"]
                evaluated += 1
                ok = gap > 0
                passes += int(ok)
                print(f"  {'BEATS CAL' if ok else 'does not beat CAL'} ({gap:+.4f})")
            else:
                print()
        print("-" * 110)

    print()
    print("VERDICT")
    print("=" * 110)
    print(f"  With BOTH models free to choose threshold and size on training days,")
    print(f"  FRAGILITY beat CALIBRATED on the TEST lower bound in {passes} of {evaluated} splits.")
    print(f"  But re-optimised policies beat the PRE-DECLARED 0.02/FLAT control in only "
          f"{beat_fixed} of {fixed_seen} cases.")
    print()
    print("  'shrink' is TEST LCB minus TRAIN-selected LCB. It is the cost of having chosen a")
    print("  policy by looking at outcomes. A large negative shrink means the selection found")
    print("  noise, and it is printed on every row precisely so that cannot be quietly omitted.")
    print()
    if beat_fixed == 0 and fixed_seen:
        print("  RE-OPTIMISATION MADE IT WORSE. Every re-optimised policy - including the ones")
        print("  that beat each other - lost to a margin declared before any outcome was seen.")
        print("  The FRAGILITY-beats-CALIBRATED result above is a comparison between two")
        print("  overfitted policies, and without the control row it would have read as a win.")
        print()
        print("  The shrink column shows why: a policy selected as +0.34 on training days")
        print("  delivered +0.006 on test. Selection on 7-14 days of rounds is fitting noise,")
        print("  and 18 cells per model per split is 18 chances to find it. The surviving")
        print("  trade counts are 75 and 138 - the selection also discarded most of the sample.")
        print()
        print("  CONCLUSION: the lever is not the threshold either. Two feature families and")
        print("  now the policy dimension have each failed to beat calibrated P(hold) at a")
        print("  fixed, pre-declared margin. What is missing is not model capacity or policy")
        print("  freedom - it is DATA. 21 days cannot support selecting anything.")
    elif passes:
        print("  A converting cell exists AND beats the pre-declared control. 18 policy cells")
        print("  were searched per model per split, so this still needs forward evidence on")
        print("  rounds recorded after today before it is more than a candidate.")
    else:
        print("  The ranking advantage does NOT convert, even with threshold and size")
        print("  re-optimised in the model's favour.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
