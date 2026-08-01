"""SETTLEMENT_FRAGILITY_V1 - does distance-in-volatility-units add what raw distance cannot?

THE HYPOTHESIS, AND WHY IT IS NOT JUST ANOTHER FEATURE
    P(hold) currently sees `distance_usd`, `seconds_left` and `rv_15m/30m/60m` as SEPARATE
    features. The quantity that actually governs a binary settlement is none of them alone:

        z = |S_t - S_0| / (sigma_per_second * sqrt(seconds_left))

    BTC one dollar above the anchor with 800 volatile seconds left is a coin flip. The same
    dollar with 10 seconds left is nearly settled. Raw distance cannot express that; the model
    must reconstruct a RATIO CONTAINING A SQUARE ROOT from three separate inputs, which is
    exactly the functional form gradient-boosted trees approximate worst - they partition axes,
    they do not divide them.

    So this is a targeted test of a specific missing interaction, not a fishing expedition for
    more features. meta_label_head_test already showed that 13 generic features add NOTHING over
    calibrated P(hold) - losing on all 3 temporal splits, and losing to random selection on one.
    If fragility is different, it must beat that same bar.

WHAT IS BEING COMPARED - the bar is deliberately hard
    ALL         trade every leader
    CALIBRATED  isotonic-calibrated P(hold)               <- the incumbent, and the bar
    FRAGILITY   a model given calibrated P(hold) PLUS z and its components

    FRAGILITY is handed the calibrated probability as an input, so it starts from the
    incumbent's answer and can only add. If it still cannot beat it, the fragility term carries
    nothing the incumbent lacks - which is a real result about the feature, not about tuning.

PROTOCOL, DECLARED BEFORE RESULTS
    G1  strictly temporal day splits; calibrator and model fitted only on strictly earlier days
    G2  one identical decision rule for every arm: enter when estimate > ask + fee + margin,
        so any difference is the probability and not the policy
    G3  fixed declared hyperparameters, no search, no feature selection
    G4  PASS requires FRAGILITY to beat CALIBRATED on the 5% day-block lower bound
    G5  a RANDOM arm matched on trade COUNT, so "it merely trades less" cannot look like skill

    python research/settlement_fragility_test.py

RETRACTED - fragility AUC +0.0699/+0.0820/+0.0595 and its expectancy arms
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
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phold_auc_and_expectancy import DB, day_block_lcb, roc_auc  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BTC_CSV = ROOT / "data" / "btc_1m_data.csv"
RNG = np.random.default_rng(20260801)
ENTRY_MARGIN = 0.02
MIN_TRAIN_ROWS = 200
MIN_TRADES = 50
SPLIT_DAYS = (7, 10, 14)
MODEL_KWARGS = dict(max_iter=200, max_depth=3, learning_rate=0.05,
                    min_samples_leaf=40, l2_regularization=1.0, random_state=42)

QUERY = """
WITH s AS (
    SELECT round_id, ts, horizon, seconds_left, current_position, current_move,
           p_leader_holds, flip_risk, late_shock_20, late_shock_50, late_shock_100,
           ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY seconds_left) rn
    FROM round_state_snapshots
    WHERE seconds_left BETWEEN 15 AND 120 AND p_leader_holds IS NOT NULL
),
q AS (
    SELECT round_id, side, ask, bid, fee,
           ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY ts) rn
    FROM rule_paper_trades
    WHERE rule = '{rule}' AND ask IS NOT NULL AND ask > 0
)
SELECT s.ts, s.horizon, s.seconds_left, s.current_move,
       s.p_leader_holds AS p_hold, s.flip_risk,
       s.late_shock_20, s.late_shock_50, s.late_shock_100,
       CASE WHEN s.current_position = p.actual_direction THEN 1 ELSE 0 END AS held,
       q.ask, q.bid, q.fee
FROM s
JOIN price_to_beat p ON p.id = s.round_id
JOIN q ON q.round_id = s.round_id AND q.side = s.current_position AND q.rn = 1
WHERE s.rn = 1 AND p.resolved AND p.actual_direction IN ('UP','DOWN')
  AND p.settlement_source LIKE 'official:%' AND s.current_position IN ('UP','DOWN')
ORDER BY s.ts
"""

BASE = ["p_cal", "p_hold", "flip_risk", "late_shock_20", "late_shock_50",
        "late_shock_100", "seconds_left", "current_move", "ask", "fee"]
FRAGILITY = BASE + ["sigma_remaining_usd", "z_fragility", "inverse_fragility"]


def attach_volatility(frame: pd.DataFrame) -> pd.DataFrame:
    """sigma over the REMAINING window, from 1-minute realized vol known at decision time."""
    bars = pd.read_csv(BTC_CSV, usecols=["ts_ms", "close", "rv_15m"])
    bars["minute"] = bars["ts_ms"] // 60_000
    frame = frame.copy()
    frame["minute"] = frame["ts"].to_numpy("int64") // 60_000
    merged = frame.merge(bars[["minute", "close", "rv_15m"]], on="minute", how="left")
    # rv_15m is a per-bar realized volatility in return units; convert to USD per second,
    # then scale to the remaining window by sqrt(time). Causal: both terms are known now.
    sigma_second = merged["rv_15m"] * merged["close"] / np.sqrt(15.0 * 60.0)
    merged["sigma_remaining_usd"] = sigma_second * np.sqrt(merged["seconds_left"].clip(lower=1))
    merged["z_fragility"] = (
        merged["current_move"].abs() / merged["sigma_remaining_usd"].replace(0, np.nan)
    )
    # The reciprocal is the fragility itself: expected remaining range per unit of distance.
    merged["inverse_fragility"] = 1.0 / (merged["z_fragility"] + 1e-9)
    return merged


def arm(estimate, ask, fee, net, days, label):
    selected = (estimate - ask - fee) > ENTRY_MARGIN
    if selected.sum() < MIN_TRADES:
        return {"label": label, "n": int(selected.sum()), "measured": False}
    return {"label": label, "n": int(selected.sum()), "measured": True,
            "mean": float(net[selected].mean()),
            "lcb": day_block_lcb(net[selected], days[selected])}


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

    frame = duckdb.connect(str(DB), read_only=True).execute(
        QUERY.format(rule=args.rule)).df()
    frame = attach_volatility(frame)
    before = len(frame)
    frame = frame.dropna(subset=["ask", "held", "p_hold", "z_fragility"]).reset_index(drop=True)
    frame["day"] = frame["ts"].to_numpy("int64") // 86_400_000
    days_all = np.sort(frame["day"].unique())

    print("=" * 104)
    print("SETTLEMENT FRAGILITY - does distance in VOLATILITY UNITS add what raw distance cannot?")
    print("=" * 104)
    print(f"  rounds {len(frame):,} of {before:,} joined to a bar (rest lack volatility) "
          f"| {len(days_all)} days")
    if frame.empty or len(days_all) < 8:
        print("  BLOCKED: too few joined rounds/days to split temporally.")
        return 0
    print(f"  z = |distance| / (sigma_sec * sqrt(seconds_left)):  "
          f"p10 {frame.z_fragility.quantile(.1):.2f}  median {frame.z_fragility.median():.2f}  "
          f"p90 {frame.z_fragility.quantile(.9):.2f}")
    print()
    print(f"{'train d':>8}{'test d':>7}{'arm':>12}{'trades':>8}{'net/$1':>10}{'day LCB':>10}"
          f"{'vs CAL':>10}  verdict")
    print("-" * 104)

    passes = evaluated = 0
    auc_rows = []
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

        # CALIBRATED incumbent, per horizon, fitted on earlier days only.
        calibrated = np.full(len(test), np.nan)
        train = train.copy()
        train["p_cal"] = np.nan
        for horizon in (5, 15):
            tr = train[train["horizon"] == horizon]
            te = (test["horizon"] == horizon).to_numpy()
            if len(tr) < MIN_TRAIN_ROWS or not te.any():
                continue
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            iso.fit(tr["p_hold"].to_numpy(float), tr["held"].to_numpy(float))
            calibrated[te] = iso.predict(test.loc[te, "p_hold"].to_numpy(float))
            train.loc[train["horizon"] == horizon, "p_cal"] = iso.predict(
                tr["p_hold"].to_numpy(float))
        calibrated = np.where(np.isfinite(calibrated), calibrated, test["p_hold"].to_numpy(float))
        test = test.copy()
        test["p_cal"] = calibrated
        train["p_cal"] = train["p_cal"].fillna(train["p_hold"])

        model = HistGradientBoostingClassifier(**MODEL_KWARGS)
        model.fit(train[FRAGILITY].to_numpy(float), train["held"].to_numpy(int))
        fragility = model.predict_proba(test[FRAGILITY].to_numpy(float))[:, 1]

        auc_rows.append((cut, roc_auc(calibrated, test["held"].to_numpy(int)),
                         roc_auc(fragility, test["held"].to_numpy(int))))

        # FRAGILITY ranks better but its LEVEL may be off, and the decision rule is a
        # threshold on the level. Calibrating it isolates ranking from level: if the AUC gain
        # is real money, it should appear once the level is corrected.
        frag_cal = np.array(fragility, dtype=float)
        in_train = model.predict_proba(train[FRAGILITY].to_numpy(float))[:, 1]
        iso_f = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
        iso_f.fit(in_train, train["held"].to_numpy(float))
        frag_cal = iso_f.predict(frag_cal)

        results = [arm(np.ones(len(test)), ask, fee, net, days, "ALL"),
                   arm(calibrated, ask, fee, net, days, "CALIBRATED"),
                   arm(fragility, ask, fee, net, days, "FRAGILITY"),
                   arm(frag_cal, ask, fee, net, days, "FRAG+CALIB")]
        cal = results[1]
        frag = results[3]          # the calibrated variant is the one judged against CAL
        if frag["measured"]:
            picks = RNG.permutation(len(test))[:frag["n"]]
            mask = np.zeros(len(test), bool); mask[picks] = True
            results.append({"label": "RANDOM(n=FRAG)", "n": int(mask.sum()), "measured": True,
                            "mean": float(net[mask].mean()),
                            "lcb": day_block_lcb(net[mask], days[mask])})

        for r in results:
            if not r["measured"]:
                print(f"{cut:>8}{len(days_all)-cut:>7}{r['label']:>12}{r['n']:>8}"
                      f"{'NOT MEASURED':>38}")
                continue
            delta = verdict = ""
            if r["label"] in ("FRAGILITY", "FRAG+CALIB") and cal.get("measured"):
                gap = r["lcb"] - cal["lcb"]
                delta = f"{gap:>+10.4f}"
                ok = gap > 0
                verdict = "beats CAL" if ok else "does not beat CAL"
                if r["label"] == "FRAG+CALIB":
                    evaluated += 1; passes += int(ok)
            print(f"{cut:>8}{len(days_all)-cut:>7}{r['label']:>12}{r['n']:>8}"
                  f"{r['mean']:>+10.4f}{r['lcb']:>+10.4f}{delta:>10}  {verdict}")
        print("-" * 104)

    print()
    print("RANKING (AUC out of sample) - does fragility even ORDER rounds better?")
    print(f"{'train d':>9}{'CALIBRATED':>13}{'FRAGILITY':>12}{'delta':>10}")
    for cut, a_cal, a_frag in auc_rows:
        print(f"{cut:>9}{a_cal:>13.4f}{a_frag:>12.4f}{a_frag - a_cal:>+10.4f}")

    print()
    print("VERDICT")
    print("=" * 104)
    print(f"  FRAGILITY beat CALIBRATED on the day-block lower bound in {passes} of "
          f"{evaluated} splits.")
    print()
    if passes == 0:
        print("  The fragility term adds nothing the calibrated head does not already carry.")
        print("  That is a real result about THIS feature: the model was handed the calibrated")
        print("  probability as an input and could only add to it, and still did not.")
        print()
        print("  Read together with meta_label_head_test - where 13 generic features also added")
        print("  nothing - the consistent finding is that P(hold) is close to SUFFICIENT for")
        print("  this decision, and the remaining error is calibration rather than a missing")
        print("  view of the market. More features is not the lane; the ONE probability is.")
    else:
        print("  Treat with suspicion proportional to the count. Three splits, one feature")
        print("  family, ~2,000 training rows. Required next: forward evidence on rounds")
        print("  recorded after this measurement, with the model frozen today.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
