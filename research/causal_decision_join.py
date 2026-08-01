"""CAUSAL_DECISION_JOIN_V1 - the corrected quote/state/bar join, and what it does to the candidate.

THE DEFECT THIS FIXES, IN MY OWN WORK
    Every p_hold economic script in research/ built its decision rows like this:

        state:  ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY seconds_left) = 1
        quote:  ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY ts)           = 1
        join:   ON round_id AND side

    Nothing required state.ts <= quote.ts. `ORDER BY seconds_left` ascending picks the state
    CLOSEST TO EXPIRY, i.e. the LATEST in the window; `ORDER BY ts` picks the EARLIEST quote.
    The two selections pull in opposite directions, so the state is systematically later.

    Measured on the live sample:

        joined rows                      3,709
        state AFTER quote (LOOK-AHEAD)   3,467   (93.5%)
        median skew                      +8.1 s
        maximum skew                     +17.8 s

    Eight seconds of hindsight in a window with 20-32 seconds left is a quarter of the
    remaining time. P(hold) read 8 seconds later knows materially more about the outcome than
    the quote could have. This affects:

        phold_auc_and_expectancy.py        (the 0.97-0.99 bucket)
        phold_calibrated_fair_value.py     (THE CANDIDATE EDGE, +0.0430, 2 of 3 splits)
        meta_label_head_test.py
        settlement_fragility_test.py
        policy_threshold_size_test.py

    backend/monitoring/head_health.py uses the same state selection, and there it is CORRECT -
    it joins state to OUTCOME with no quote, so there is no earlier decision instant to violate.
    The defect is mine: I reused a state-selection built for calibration inside an economic test
    that has a decision timestamp.

THE CORRECTED CONSTRUCTION
    decision_ts   = the executable quote's own timestamp
    state         = the LATEST round_state_snapshot with state.ts <= decision_ts
    max state age = MAX_STATE_AGE_S, else the row is REJECTED rather than back-filled
    bar           = the latest COMPLETED 1-minute bar at or before decision_ts, which is the
                    previous minute's bar - a decision at 12:30:15 cannot know the 12:30 close

    Rejection is deliberate. Silently substituting a stale or future state is what produced the
    original number.

    python research/causal_decision_join.py
"""
from __future__ import annotations

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
MAX_STATE_AGE_S = 20          # a state older than this is not a description of "now"
ENTRY_MARGIN = 0.02
MIN_TRADES = 40
SPLIT_DAYS = (7, 10, 14)

CAUSAL_QUERY = """
WITH q AS (
    SELECT round_id, side, ask, bid, fee, ts AS decision_ts, horizon,
           ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY ts) rn
    FROM rule_paper_trades
    WHERE rule = '{rule}' AND ask IS NOT NULL AND ask > 0
),
paired AS (
    SELECT q.round_id, q.side, q.ask, q.bid, q.fee, q.decision_ts, q.horizon,
           s.ts AS state_ts, s.p_leader_holds, s.seconds_left, s.current_move,
           s.current_position, s.flip_risk,
           ROW_NUMBER() OVER (PARTITION BY q.round_id ORDER BY s.ts DESC) rn
    FROM q
    JOIN round_state_snapshots s
      ON s.round_id = q.round_id
     AND s.p_leader_holds IS NOT NULL
     AND s.ts <= q.decision_ts                         -- CAUSAL: never after the decision
     AND s.ts >= q.decision_ts - {max_age_ms}          -- and never stale
     -- p_leader_holds is a probability ABOUT the state's leader. If that leader is not the
     -- side the quote prices, the probability describes a different bet and must not be
     -- applied to it. A first draft of this query dropped this constraint and computed the
     -- label from the STATE's leader while pricing the QUOTE's side - a mismatch on 14.6% of
     -- rows, which alone dragged the measured baseline from +0.005 to -0.09.
     AND s.current_position = q.side
    WHERE q.rn = 1
)
SELECT paired.decision_ts, paired.state_ts, paired.horizon, paired.seconds_left,
       paired.current_move, paired.p_leader_holds AS p_hold, paired.flip_risk,
       paired.ask, paired.bid, paired.fee,
       -- The label is the side ACTUALLY BOUGHT settling true. The state supplies features,
       -- never the outcome.
       CASE WHEN paired.side = p.actual_direction THEN 1 ELSE 0 END AS held
FROM paired
JOIN price_to_beat p ON p.id = paired.round_id
WHERE paired.rn = 1
  AND p.resolved AND p.actual_direction IN ('UP','DOWN')
  AND p.settlement_source LIKE 'official:%'
  AND paired.side IN ('UP','DOWN')
ORDER BY paired.decision_ts
"""


def attach_completed_bar(frame: pd.DataFrame) -> pd.DataFrame:
    """Latest bar whose minute has CLOSED at or before the decision - the previous minute."""
    bars = pd.read_csv(BTC_CSV, usecols=["ts_ms", "close", "rv_15m"]).sort_values("ts_ms")
    # A bar stamped at minute M closes at M+60s, so it is knowable only from M+60s onward.
    bars["available_from_ms"] = bars["ts_ms"] + 60_000
    frame = frame.sort_values("decision_ts").copy()
    merged = pd.merge_asof(frame, bars[["available_from_ms", "close", "rv_15m"]],
                           left_on="decision_ts", right_on="available_from_ms",
                           direction="backward")
    merged["bar_age_s"] = (merged["decision_ts"] - merged["available_from_ms"]) / 1000.0
    sigma_second = merged["rv_15m"] * merged["close"] / np.sqrt(15.0 * 60.0)
    merged["sigma_remaining_usd"] = sigma_second * np.sqrt(
        merged["seconds_left"].clip(lower=1))
    merged["z_fragility"] = (merged["current_move"].abs()
                             / merged["sigma_remaining_usd"].replace(0, np.nan))
    return merged


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rule", default="LATE_LEADER_30S_V1")
    args = parser.parse_args()
    try:
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        print("scikit-learn is required")
        return 1

    con = duckdb.connect(str(DB), read_only=True)
    causal = con.execute(CAUSAL_QUERY.format(
        rule=args.rule, max_age_ms=MAX_STATE_AGE_S * 1000)).df()

    print("=" * 100)
    print("CAUSAL DECISION JOIN - what the candidate edge looks like without look-ahead")
    print("=" * 100)
    print(f"  rule {args.rule} | max state age {MAX_STATE_AGE_S}s")
    print(f"  causally joined rows : {len(causal):,}")
    if causal.empty:
        print("\n  NO ROWS SURVIVE. Every decision either had no state at or before its quote,")
        print("  or only a stale one. The previous results were built entirely on states that")
        print("  postdated their own decision, and there is no causal sample to replace them.")
        return 0

    skew = (causal["decision_ts"] - causal["state_ts"]) / 1000.0
    print(f"  state age at decision: median {skew.median():.1f}s  p95 {skew.quantile(.95):.1f}s "
          f"(all >= 0 by construction)")

    frame = attach_completed_bar(causal).dropna(subset=["ask", "held", "p_hold"])
    frame["day"] = frame["decision_ts"].to_numpy("int64") // 86_400_000
    days_all = np.sort(frame["day"].unique())
    print(f"  after completed-bar join : {len(frame):,} rows over {len(days_all)} days")
    print(f"  bar age since AVAILABILITY: median {frame['bar_age_s'].median():.0f}s "
          f"(bar itself is >= 60s old - a minute must close before it is knowable)")
    print()

    if len(days_all) < 8:
        print("  BLOCKED: too few causal days to split temporally. No number is reported.")
        return 0

    print("  THE CANDIDATE, RE-RUN CAUSALLY (calibrated P(hold) fair value, fixed 0.02 margin)")
    print(f"{'train d':>9}{'test d':>8}{'test n':>9}{'traded':>8}"
          f"{'net/$1':>10}{'day LCB':>10}{'baseline':>10}  verdict")
    print("-" * 100)

    passes = evaluated = 0
    for cut in SPLIT_DAYS:
        if cut >= len(days_all):
            continue
        train = frame[frame["day"] < days_all[cut]]
        test = frame[frame["day"] >= days_all[cut]]
        if len(train) < 150 or len(test) < MIN_TRADES:
            continue
        calibrated = np.full(len(test), np.nan)
        for horizon in (5, 15):
            tr = train[train["horizon"] == horizon]
            te = (test["horizon"] == horizon).to_numpy()
            if len(tr) < 100 or not te.any():
                continue
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            iso.fit(tr["p_hold"].to_numpy(float), tr["held"].to_numpy(float))
            calibrated[te] = iso.predict(test.loc[te, "p_hold"].to_numpy(float))
        calibrated = np.where(np.isfinite(calibrated), calibrated,
                              test["p_hold"].to_numpy(float))
        ask = test["ask"].to_numpy(float); fee = test["fee"].to_numpy(float)
        net = test["held"].to_numpy(float) - ask - fee
        days = test["day"].to_numpy()
        selected = (calibrated - ask - fee) > ENTRY_MARGIN
        base_lcb = day_block_lcb(net, days)
        if selected.sum() < MIN_TRADES:
            print(f"{cut:>9}{len(days_all)-cut:>8}{len(test):>9}{int(selected.sum()):>8}"
                  f"{'NOT MEASURED (too few trades)':>40}")
            continue
        lcb = day_block_lcb(net[selected], days[selected])
        evaluated += 1
        ok = np.isfinite(lcb) and lcb > 0
        passes += int(ok)
        print(f"{cut:>9}{len(days_all)-cut:>8}{len(test):>9}{int(selected.sum()):>8}"
              f"{net[selected].mean():>+10.4f}{lcb:>+10.4f}{base_lcb:>+10.4f}"
              f"  {'PASS' if ok else 'FAILS'}")

    print()
    print("VERDICT")
    print("=" * 100)
    print(f"  Causally, the candidate passes {passes} of {evaluated} temporal splits.")
    print()
    print("  The previous figure - +0.0430/$1 with a day-block lower bound of +0.0164, passing")
    print("  2 of 3 splits - was produced with a state read a median of 8.1 SECONDS AFTER the")
    print("  quote it was traded against, on 93.5% of rows. Whatever that number measured, it")
    print("  was not a decision anyone could have made.")
    print()
    print("  Compare the two honestly rather than quoting whichever is larger.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
