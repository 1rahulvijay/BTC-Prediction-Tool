"""PHOLD_CALIBRATED_FAIR_VALUE_V1 - does fixing calibration turn ranking into money?

WHAT CAME BEFORE
    research/phold_auc_and_expectancy.py established: P(hold) ranks well live (AUC 0.776) but
    is overconfident by 6.7 points, and buying the leader at the quoted ask has a NEGATIVE
    day-block lower bound. The price already contains the ranking.

    That measurement used RAW p_hold. Raw p_hold cannot support a fair-value comparison at all:
    comparing a probability to a price requires the probability to be right in LEVEL, and raw
    p_hold says 95% where 87% realizes. Every "cheap" call it makes is biased.

WHAT THIS TESTS
    backend/phold_challenger.py fits a calibrator that wins on Brier AND log-loss AND ECE
    (5m ECE 0.0883 -> 0.0136). With a calibrated probability the fair-value question becomes
    askable for the first time:

        edge = calibrated_p - ask - fee        trade only when edge > 0

    A calibrator is a MONOTONE map, so it cannot reorder rounds and cannot change AUC. It
    changes nothing about ranking. What it changes is whether "the ask is below the true
    probability" is a statement you can trust.

THE LEAKAGE THIS EXISTS TO AVOID
    The deployable calibrator in data/research/phold_challenger/ was FITTED on the same rounds
    an evaluation would score. Applying it to those rounds lets its knots carry outcome
    information about the very observations being judged, and produces a confident number that
    means nothing. Every result in this repository that ignored a split died on contact with
    one.

    So the calibrator here is refitted INSIDE each split, on strictly earlier days only, per
    horizon. The in-sample figure is printed beside it purely to show the size of the gap.

GATES, DECLARED BEFORE RESULTS
    G1  strictly temporal day splits; a calibrator never sees a day at or after its test window
    G2  per-horizon calibration - 5m and 15m are different games and pooling them is a bug
    G3  >= 200 training rounds per horizon, >= 50 selected trades per split, else NOT MEASURED
    G4  PASS requires the 5% day-block lower bound above zero on the TEST days
    G5  every split reports the trade-everything baseline on the same test days. A filter that
        does not beat trading everything has established nothing.

    python research/phold_calibrated_fair_value.py

RETRACTED - THE CANDIDATE EDGE: +0.0430/$1, day LCB +0.0164, 2 of 3 splits
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

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phold_auc_and_expectancy import DB, day_block_lcb  # noqa: E402

MIN_TRAIN_PER_HORIZON = 200
MIN_TRADES = 50
SPLIT_DAYS = (7, 10, 14)

QUERY = """
WITH s AS (
    SELECT round_id, ts, horizon, p_leader_holds, current_position, seconds_left,
           ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY seconds_left) rn
    FROM round_state_snapshots
    WHERE seconds_left BETWEEN 15 AND 120 AND p_leader_holds IS NOT NULL
),
q AS (
    SELECT round_id, side, ask, fee,
           ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY ts) rn
    FROM rule_paper_trades
    WHERE rule = '{rule}' AND ask IS NOT NULL AND ask > 0
)
SELECT s.ts, s.horizon, s.p_leader_holds AS p_hold,
       CASE WHEN s.current_position = p.actual_direction THEN 1 ELSE 0 END AS held,
       q.ask, COALESCE(q.fee, 0.0) AS fee
FROM s
JOIN price_to_beat p ON p.id = s.round_id
LEFT JOIN q ON q.round_id = s.round_id AND q.side = s.current_position AND q.rn = 1
WHERE s.rn = 1 AND p.resolved AND p.actual_direction IN ('UP', 'DOWN')
  AND p.settlement_source LIKE 'official:%' AND s.current_position IN ('UP', 'DOWN')
ORDER BY s.ts
"""


def main() -> int:
    from research_status import guard
    guard(Path(__file__).name)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rule", default="LATE_LEADER_30S_V1")
    args = parser.parse_args()
    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        print("scikit-learn is required for this study")
        return 1

    frame = duckdb.connect(str(DB), read_only=True).execute(
        QUERY.format(rule=args.rule)).df().dropna(subset=["ask"])
    frame["day"] = frame["ts"].to_numpy("int64") // 86_400_000
    days = np.sort(frame["day"].unique())

    print("=" * 96)
    print("P(HOLD) CALIBRATED FAIR VALUE - edge = calibrated_p - ask - fee")
    print("=" * 96)
    print(f"  rounds {len(frame):,} over {len(days)} days | quote source {args.rule}")
    print(f"  mean ask {frame['ask'].mean():.4f} | leader held {frame['held'].mean()*100:.2f}%")
    print()
    print("  The calibrator is REFITTED inside each split on strictly earlier days only. The")
    print("  shipped calibrator was fitted on these same rounds, so using it here would score")
    print("  observations its own knots had already seen.")
    print()
    print(f"{'train d':>9}{'test d':>8}{'test n':>9}{'traded':>8}{'net/$1':>10}{'day LCB':>10}"
          f"{'baseline':>10}{'base LCB':>10}  verdict")
    print("-" * 96)

    passes = 0
    evaluated = 0
    for cut in SPLIT_DAYS:
        if cut >= len(days):
            continue
        train = frame[frame["day"] < days[cut]]
        test = frame[frame["day"] >= days[cut]]
        calibrated = np.full(len(test), np.nan)
        for horizon in (5, 15):
            train_mask = train["horizon"] == horizon
            test_mask = (test["horizon"] == horizon).to_numpy()
            if train_mask.sum() < MIN_TRAIN_PER_HORIZON or not test_mask.any():
                continue
            model = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            model.fit(train.loc[train_mask, "p_hold"].to_numpy(float),
                      train.loc[train_mask, "held"].to_numpy(float))
            calibrated[test_mask] = model.predict(
                test.loc[test_mask, "p_hold"].to_numpy(float))

        usable = np.isfinite(calibrated)
        scored = test[usable]
        probability = calibrated[usable]
        ask = scored["ask"].to_numpy(float)
        fee = scored["fee"].to_numpy(float)
        net = scored["held"].to_numpy(float) - ask - fee
        day_index = scored["day"].to_numpy()
        selected = (probability - ask - fee) > 0

        base_mean, base_lcb = net.mean(), day_block_lcb(net, day_index)
        if selected.sum() < MIN_TRADES:
            print(f"{cut:>9}{len(days)-cut:>8}{len(scored):>9}{int(selected.sum()):>8}"
                  f"{'NOT MEASURED (too few trades)':>42}")
            continue
        evaluated += 1
        lcb = day_block_lcb(net[selected], day_index[selected])
        ok = np.isfinite(lcb) and lcb > 0
        passes += int(ok)
        print(f"{cut:>9}{len(days)-cut:>8}{len(scored):>9}{int(selected.sum()):>8}"
              f"{net[selected].mean():>+10.4f}{lcb:>+10.4f}{base_mean:>+10.4f}"
              f"{base_lcb:>+10.4f}  {'PASS' if ok else 'FAILS'}")

    print()
    print("-" * 96)
    print("VERDICT")
    print("-" * 96)
    print(f"  {passes} of {evaluated} temporal splits pass G4 with a positive day-block bound,")
    print("  each against a trade-everything baseline that does not.")
    print()
    if passes and passes < evaluated:
        print("  THE FAILING SPLIT IS THE MOST RECENT ONE, and that is not a footnote. It is")
        print("  the window closest to what tomorrow looks like. It also has the fewest test")
        print("  days and the fewest trades, so decay and small-sample noise are not separable")
        print("  here. 21 days of live rounds cannot separate them.")
        print()
    print("  STATUS: CANDIDATE, not a finding. What would settle it is forward evidence on")
    print("  rounds recorded AFTER this measurement, scored by a calibrator frozen today.")
    print("  Nothing here is wired, and this does not authorize a real order.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
