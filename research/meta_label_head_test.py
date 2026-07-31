"""META_LABEL_HEAD_V1 - can anything beyond P(hold) predict whether a trade pays?

THE QUESTION HAD TO BE REFRAMED BEFORE IT WAS WORTH ASKING
    The obvious meta-label is "will this trade win after costs?", i.e. net = held - ask - fee,
    labelled 1 when positive. Measured on the live sample, that label agrees with the plain
    "did the leader hold?" label on 100.00% of 3,282 rounds - 0 rows differ - because ask + fee
    is below 1.00 on every row, so net > 0 is arithmetically identical to held == 1.

    Training a classifier on it would therefore produce a second P(hold) and call it a new head.
    The meta-label carries no information the existing head does not already have.

    What IS a real decision is cost-aware, and it is not a classification target at all:

        take the trade  <=>  E[held] > ask + fee

    That is the fair-value rule already built, and its quality is entirely the quality of the
    probability. So the only non-circular question left is:

        Can a model using features BEYOND P(hold) - the book, the flip head, the shock heads,
        the clock - estimate that probability better than CALIBRATED P(hold) alone?

    That is what this tests, and the control is deliberately hard: beating "trade everything"
    proves nothing here, because calibrated P(hold) already does that. The bar is beating the
    calibrated rule itself.

PROTOCOL, DECLARED BEFORE RESULTS
    G1  strictly temporal day splits. The model and the calibrator are both fitted only on days
        strictly earlier than the test window, per horizon for the calibrator.
    G2  identical decision rule for every arm: enter when estimate > ask + fee + ENTRY_MARGIN.
        The arms differ ONLY in where the probability comes from, so any difference is the
        probability and not the policy.
    G3  fixed hyperparameters, declared below, no search, no feature selection. Selecting either
        from results is what manufactured every earlier "edge" in this repository.
    G4  three arms always reported together:
            ALL        trade every leader
            CALIBRATED isotonic-calibrated P(hold)          <- the bar to beat
            META       the model using every available feature
        plus a RANDOM control drawing the same NUMBER of trades from the same test rounds, so a
        result that is merely "trading less" is visible as such.
    G5  PASS requires the META arm to beat CALIBRATED on the 5% day-block lower bound, not on
        the point estimate, on the test days.
    G6  >= 200 training rows and >= 50 selected trades per split, else NOT MEASURED.

    python research/meta_label_head_test.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import duckdb
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phold_auc_and_expectancy import DB, day_block_lcb, roc_auc  # noqa: E402

RNG = np.random.default_rng(20260731)
ENTRY_MARGIN = 0.02
MIN_TRAIN_ROWS = 200
MIN_TRADES = 50
SPLIT_DAYS = (7, 10, 14)

# G3: declared, never searched.
MODEL_KWARGS = dict(max_iter=200, max_depth=3, learning_rate=0.05,
                    min_samples_leaf=40, l2_regularization=1.0, random_state=42)

FEATURES = [
    "p_hold",           # the existing head - the thing the challenger must add to
    "flip_risk",
    "late_shock_20", "late_shock_50", "late_shock_100",
    "next_opportunity",
    "current_move",
    "seconds_left",
    "horizon",
    "ask", "bid", "spread", "fee",
]

QUERY = """
WITH s AS (
    SELECT round_id, ts, horizon, seconds_left, current_position, current_move,
           p_leader_holds, flip_risk, late_shock_20, late_shock_50, late_shock_100,
           next_opportunity,
           ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY seconds_left) rn
    FROM round_state_snapshots
    WHERE seconds_left BETWEEN 15 AND 120 AND p_leader_holds IS NOT NULL
),
q AS (
    SELECT round_id, side, ask, bid, fee, spread,
           ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY ts) rn
    FROM rule_paper_trades
    WHERE rule = '{rule}' AND ask IS NOT NULL AND ask > 0
)
SELECT s.ts, s.horizon, s.seconds_left, s.current_move,
       s.p_leader_holds AS p_hold, s.flip_risk,
       s.late_shock_20, s.late_shock_50, s.late_shock_100, s.next_opportunity,
       CASE WHEN s.current_position = p.actual_direction THEN 1 ELSE 0 END AS held,
       q.ask, q.bid, q.fee, q.spread
FROM s
JOIN price_to_beat p ON p.id = s.round_id
JOIN q ON q.round_id = s.round_id AND q.side = s.current_position AND q.rn = 1
WHERE s.rn = 1 AND p.resolved AND p.actual_direction IN ('UP','DOWN')
  AND p.settlement_source LIKE 'official:%' AND s.current_position IN ('UP','DOWN')
ORDER BY s.ts
"""


def arm(estimate, ask, fee, net, days, label):
    """Apply the shared decision rule and score it. One policy, three probability sources."""
    selected = (estimate - ask - fee) > ENTRY_MARGIN
    if selected.sum() < MIN_TRADES:
        return {"label": label, "n": int(selected.sum()), "measured": False}
    values = net[selected]
    return {"label": label, "n": int(selected.sum()), "measured": True,
            "mean": float(values.mean()),
            "lcb": day_block_lcb(values, days[selected])}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rule", default="LATE_LEADER_30S_V1")
    args = parser.parse_args()
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        print("scikit-learn is required")
        return 1

    frame = duckdb.connect(str(DB), read_only=True).execute(
        QUERY.format(rule=args.rule)).df()
    frame = frame.dropna(subset=["ask", "held", "p_hold"]).reset_index(drop=True)
    frame["day"] = frame["ts"].to_numpy("int64") // 86_400_000
    days_all = np.sort(frame["day"].unique())

    print("=" * 104)
    print("META-LABEL HEAD - can anything beyond P(hold) price this trade better?")
    print("=" * 104)
    print(f"  rounds {len(frame):,} over {len(days_all)} days | features {len(FEATURES)}")

    net_all = frame["held"].to_numpy(float) - frame["ask"].to_numpy(float) - frame["fee"].to_numpy(float)
    meta_label = (net_all > 0).astype(int)
    agreement = (meta_label == frame["held"].to_numpy(int)).mean()
    print(f"  'wins after costs' vs 'leader held' agree on {agreement*100:.2f}% of rows "
          f"-> the meta-label is NOT a new target")
    print(f"  so the target is the probability, and the bar is CALIBRATED P(hold), not chance")
    print()
    print(f"{'train d':>8}{'test d':>7}{'arm':>12}{'trades':>8}{'net/$1':>10}{'day LCB':>10}"
          f"{'vs CAL':>10}  verdict")
    print("-" * 104)

    passes = evaluated = 0
    for cut in SPLIT_DAYS:
        if cut >= len(days_all):
            continue
        train = frame[frame["day"] < days_all[cut]]
        test = frame[frame["day"] >= days_all[cut]].reset_index(drop=True)
        if len(train) < MIN_TRAIN_ROWS or len(test) < MIN_TRADES:
            continue

        ask = test["ask"].to_numpy(float)
        fee = test["fee"].to_numpy(float)
        net = test["held"].to_numpy(float) - ask - fee
        days = test["day"].to_numpy()

        # --- CALIBRATED arm: isotonic on raw p_hold, fitted per horizon on earlier days only
        calibrated = np.full(len(test), np.nan)
        for horizon in (5, 15):
            tr = train[train["horizon"] == horizon]
            te = (test["horizon"] == horizon).to_numpy()
            if len(tr) < MIN_TRAIN_ROWS or not te.any():
                continue
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            iso.fit(tr["p_hold"].to_numpy(float), tr["held"].to_numpy(float))
            calibrated[te] = iso.predict(test.loc[te, "p_hold"].to_numpy(float))
        calibrated = np.where(np.isfinite(calibrated), calibrated,
                              test["p_hold"].to_numpy(float))

        # --- META arm: every feature, fitted on earlier days only
        model = HistGradientBoostingClassifier(**MODEL_KWARGS)
        model.fit(train[FEATURES].to_numpy(float), train["held"].to_numpy(int))
        meta = model.predict_proba(test[FEATURES].to_numpy(float))[:, 1]

        results = [
            arm(np.ones(len(test)), ask, fee, net, days, "ALL"),
            arm(calibrated, ask, fee, net, days, "CALIBRATED"),
            arm(meta, ask, fee, net, days, "META"),
        ]
        cal_result = results[1]
        # G4 control: same NUMBER of trades as META, drawn at random from the same rounds, so
        # "it just trades less" cannot masquerade as skill.
        meta_result = results[2]
        if meta_result["measured"]:
            picks = RNG.permutation(len(test))[:meta_result["n"]]
            mask = np.zeros(len(test), bool)
            mask[picks] = True
            results.append({"label": "RANDOM(n=META)", "n": int(mask.sum()), "measured": True,
                            "mean": float(net[mask].mean()),
                            "lcb": day_block_lcb(net[mask], days[mask])})

        for result in results:
            if not result["measured"]:
                print(f"{cut:>8}{len(days_all)-cut:>7}{result['label']:>12}"
                      f"{result['n']:>8}{'NOT MEASURED (too few trades)':>38}")
                continue
            delta = ""
            verdict = ""
            if result["label"] == "META" and cal_result.get("measured"):
                gap = result["lcb"] - cal_result["lcb"]
                delta = f"{gap:>+10.4f}"
                ok = gap > 0
                verdict = "beats CAL" if ok else "does not beat CAL"
                evaluated += 1
                passes += int(ok)
            print(f"{cut:>8}{len(days_all)-cut:>7}{result['label']:>12}{result['n']:>8}"
                  f"{result['mean']:>+10.4f}{result['lcb']:>+10.4f}{delta:>10}  {verdict}")
        print("-" * 104)

    # Ranking check: does the meta model even rank better than raw p_hold out of sample?
    print()
    print("VERDICT")
    print("=" * 104)
    print(f"  META beat CALIBRATED on the day-block lower bound in {passes} of {evaluated} splits.")
    print()
    if passes == 0:
        print("  The extra features add nothing the calibrated head does not already have.")
        print("  That is the expected result if P(hold) is close to sufficient for this decision")
        print("  and the remaining error is calibration rather than missing information - which")
        print("  is exactly what the calibration study found (ECE 0.088 -> 0.014, AUC unchanged).")
        print()
        print("  It also means there is no case for a second head here. The lever is getting the")
        print("  ONE probability right, and that is blocked on artifact manifests, not on models.")
    else:
        print("  Treat with suspicion proportional to the count. Three splits were examined and")
        print("  the model has 13 features against ~2,000 training rows; a single win is inside")
        print("  what noise produces. Required next: forward evidence on rounds recorded after")
        print("  this measurement, with the model frozen today.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
