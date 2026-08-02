"""HOLD_VS_EXIT_HEAD_V1 - can anything capture the exit-timing headroom the ceiling exposed?

WHY THIS HEAD AND NOT ANOTHER DIRECTION MODEL
    Two action-value engines measured the same shape on both venues: no fixed rule beats
    standing aside, and the only headroom sits in EXIT TIMING, not direction.

        Polymarket   best fixed rule -0.0105/share   perfect exit +0.1313/share
        Binance 15m  best fixed rule -11.86 bps      perfect exit  +2.75 bps (median -2.63)
        Binance 120m best fixed rule -10.88 bps      perfect exit +31.37 bps

    A ceiling is not an edge. It is the most a perfect head could win, and the question this
    file answers is whether ANY head captures enough of it to cross zero. If the answer is no,
    that closes the lane cheaply instead of after four more models.

THE DECISION, STATED AS ONE BINARY
    At a checkpoint holding the leading side, exit at EXIT_HORIZON_S or hold to settlement?

        y = 1  when exiting at the horizon would have beaten holding
        p      the head's probability of that
        policy exit when p > threshold, else hold

    One declared horizon, not "the best of three" - choosing the best horizon in hindsight is
    the foresight this study exists to avoid claiming.

PROTOCOL, DECLARED BEFORE RESULTS
    G1  TRAIN on the earlier 70% of LIVE_RESEARCH days, CALIBRATE on the later 30%,
        EVALUATE on RETROSPECTIVE_VALIDATION - which is strictly later than both
    G2  features come from causal_validation.feature_columns(), so no label and no outcome
        column can enter, and the clock does
    G3  the threshold is chosen on CALIBRATION days only, never on the evaluation set
    G4  PASS requires beating BOTH always-hold AND always-exit on realised net value
    G5  a matched-count RANDOM policy, so "it acts less often" cannot read as skill
    G6  there are ZERO FORWARD_UNTOUCHED rows, so this study can eliminate and never promote

    python research/hold_vs_exit_head_v1.py
    python research/hold_vs_exit_head_v1.py --selftest
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

from causal_validation import feature_columns  # noqa: E402
from execution_cost import exit_fill, settlement_value  # noqa: E402
from phold_auc_and_expectancy import (  # noqa: E402
    day_block_lcb, expected_calibration_error, roc_auc,
)

CHECKPOINTS = ROOT / "data" / "research" / "causal_checkpoints_v1.parquet"
LABELS = ROOT / "data" / "research" / "causal_checkpoint_labels_v1.parquet"

#: Declared. The single exit horizon this head decides about.
EXIT_HORIZON_S = 30
#: Thresholds swept on CALIBRATION days only.
THRESHOLD_GRID = (0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70)
TRAIN_FRACTION = 0.70
MIN_ROWS = 500
RNG = np.random.default_rng(20260802)

MODEL_KWARGS = dict(max_iter=250, max_depth=4, learning_rate=0.06,
                    min_samples_leaf=60, l2_regularization=1.0, random_state=42)


def load():
    """Checkpoints with the realised value of both actions attached."""
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        frame = con.execute(f"""
            SELECT k.*,
                   l.label_checkpoint_side_wins AS won,
                   l.label_path_samples
            FROM read_parquet('{CHECKPOINTS.as_posix()}') k
            JOIN read_parquet('{LABELS.as_posix()}') l
              ON l.slug = k.slug AND l.checkpoint_s = k.checkpoint_s
            WHERE k.eligible AND k.current_side IS NOT NULL
              AND l.label_checkpoint_side_wins IS NOT NULL
        """).df()
    finally:
        con.close()
    return frame


def attach_action_values(frame, bid_at_horizon):
    """hold and exit value per share, using the same cost primitives the engine uses."""
    ask = np.where(frame["current_side"].to_numpy() == 1,
                   frame["up_ask"].to_numpy(float), frame["down_ask"].to_numpy(float))
    won = frame["won"].to_numpy(float)
    from polymarket_fee import polymarket_taker_fee_per_share

    hold = np.array([settlement_value(bool(w), float(a)) for w, a in zip(won, ask)])
    # Entry outlay is ask + fee, from the canonical helper rather than the formula restated.
    entry_cost = ask + np.array([polymarket_taker_fee_per_share(float(a)) for a in ask])
    exit_value = np.array([
        exit_fill(float(b), 1).proceeds_per_share if np.isfinite(b) else np.nan
        for b in bid_at_horizon]) - entry_cost
    return hold, exit_value


def fetch_horizon_bids(frame):
    """The bid on the held side EXIT_HORIZON_S after each checkpoint."""
    import duckdb

    from checkpoint_builder import SOURCE_DB
    con = duckdb.connect(str(SOURCE_DB), read_only=True)
    try:
        con.execute("CREATE TEMP TABLE cp AS SELECT * FROM read_parquet(?)",
                    [CHECKPOINTS.as_posix()])
        return con.execute(f"""
            SELECT c.slug, c.checkpoint_s,
                   arg_max(CASE WHEN c.current_side = 1 THEN s.up_bid ELSE s.down_bid END,
                           c.checkpoint_s - s.seconds_left) AS bid_at_horizon
            FROM cp c
            JOIN pm_round_snapshots s
              ON s.slug = c.slug
             AND s.seconds_left < c.checkpoint_s
             AND (c.checkpoint_s - s.seconds_left) <= {EXIT_HORIZON_S}
            GROUP BY c.slug, c.checkpoint_s
        """).df()
    finally:
        con.close()


def policy_value(exit_flags, hold, exit_value):
    """Realised net per share of a policy that exits where the flag is set."""
    return np.where(exit_flags, exit_value, hold)


def evaluate(name, exit_flags, hold, exit_value, days):
    value = policy_value(exit_flags, hold, exit_value)
    return {"policy": name, "exits": int(exit_flags.sum()),
            "mean": float(value.mean()), "lcb": day_block_lcb(value, days)}


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    hold = np.array([0.30, -0.60, 0.30, -0.60])
    exits = np.array([-0.05, -0.05, -0.05, -0.05])
    days = np.array([1, 1, 2, 2])

    always_hold = evaluate("HOLD", np.zeros(4, bool), hold, exits, days)
    always_exit = evaluate("EXIT", np.ones(4, bool), hold, exits, days)
    check(abs(always_hold["mean"] - (-0.15)) < 1e-12,
          "always-hold realises the hold column and nothing else")
    check(abs(always_exit["mean"] - (-0.05)) < 1e-12,
          "always-exit realises the exit column and nothing else")

    perfect = exits > hold
    check(evaluate("PERFECT", perfect, hold, exits, days)["mean"] > always_hold["mean"],
          "a policy that exits exactly when exiting was better beats always-hold")
    check(abs(evaluate("PERFECT", perfect, hold, exits, days)["mean"] - 0.125) < 1e-12,
          "the perfect policy takes 0.30 on winners and -0.05 on losers")

    # A head must not be able to look at its own target.
    columns = ["up_ask", "seconds_left", "label_checkpoint_side_wins", "settled_side", "slug"]
    offered = feature_columns(columns)
    check("label_checkpoint_side_wins" not in offered and "settled_side" not in offered,
          "the outcome the target is built from can never enter the feature matrix")
    check("seconds_left" in offered,
          "time-to-expiry IS offered - it is an observation, not row identity")

    print(f"\nHOLD VS EXIT SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    print("=" * 104)
    print(f"HOLD VS EXIT HEAD - can anything capture the exit ceiling? (horizon "
          f"{EXIT_HORIZON_S}s)")
    print("=" * 104)
    if args.selftest:
        return selftest()
    try:
        from sklearn.ensemble import HistGradientBoostingClassifier
        from sklearn.isotonic import IsotonicRegression
    except ImportError:
        print("  scikit-learn is required")
        return 1
    for path in (CHECKPOINTS, LABELS):
        if not path.is_file():
            print(f"  BLOCKED: {path.name} is missing.")
            return 0

    frame = load()
    bids = fetch_horizon_bids(frame)
    frame = frame.merge(bids, on=["slug", "checkpoint_s"], how="left")
    hold, exit_value = attach_action_values(frame, frame["bid_at_horizon"].to_numpy(float))

    usable = np.isfinite(exit_value) & np.isfinite(hold)
    frame, hold, exit_value = frame[usable].reset_index(drop=True), hold[usable], exit_value[usable]
    target = (exit_value > hold).astype(int)
    days = (frame["snapshot_ts"].to_numpy(float) // 86400).astype(np.int64)

    names = [c for c in feature_columns(frame.columns)
             if frame[c].dtype.kind in "fiu" and frame[c].notna().mean() > 0.95]
    matrix = frame[names].to_numpy(float)

    live = frame["evidence_class"].to_numpy() == "LIVE_RESEARCH"
    test = frame["evidence_class"].to_numpy() == "RETROSPECTIVE_VALIDATION"
    live_days = np.sort(np.unique(days[live]))
    if len(live_days) < 5 or test.sum() < MIN_ROWS:
        print("  BLOCKED: not enough LIVE_RESEARCH days or RETROSPECTIVE_VALIDATION rows.")
        return 0
    cut = live_days[int(len(live_days) * TRAIN_FRACTION)]
    train = live & (days < cut)
    calibrate = live & (days >= cut)

    print(f"  rows {len(frame):,} | features {len(names)} | "
          f"exit-beats-hold base rate {target.mean():.3f}")
    print(f"  train {train.sum():,}  calibrate {calibrate.sum():,}  "
          f"evaluate {test.sum():,} (RETROSPECTIVE_VALIDATION, strictly later)")

    model = HistGradientBoostingClassifier(**MODEL_KWARGS)
    model.fit(matrix[train], target[train])
    raw_cal = model.predict_proba(matrix[calibrate])[:, 1]
    iso = IsotonicRegression(out_of_bounds="clip").fit(raw_cal, target[calibrate])
    probability = np.clip(iso.predict(model.predict_proba(matrix)[:, 1]), 1e-6, 1 - 1e-6)

    print()
    print(f"  discrimination on the EVALUATION set: AUC {roc_auc(probability[test], target[test]):.4f}"
          f" | ECE {expected_calibration_error(probability[test], target[test]):.4f}")

    # --- threshold chosen on CALIBRATION days only -------------------------------------------
    best = None
    for threshold in THRESHOLD_GRID:
        result = evaluate(f"HEAD@{threshold}", probability[calibrate] > threshold,
                          hold[calibrate], exit_value[calibrate], days[calibrate])
        if best is None or result["mean"] > best["mean"]:
            best = {**result, "threshold": threshold}
    print(f"  threshold chosen on calibration days: {best['threshold']:.2f} "
          f"(calibration mean {best['mean']:+.4f})")

    print()
    print(f"{'policy':<24}{'exits':>9}{'mean/$1':>11}{'day LCB':>11}")
    rows = [
        evaluate("ALWAYS_HOLD", np.zeros(test.sum(), bool), hold[test], exit_value[test],
                 days[test]),
        evaluate("ALWAYS_EXIT", np.ones(test.sum(), bool), hold[test], exit_value[test],
                 days[test]),
        evaluate(f"HEAD@{best['threshold']:.2f}", probability[test] > best["threshold"],
                 hold[test], exit_value[test], days[test]),
    ]
    matched = np.zeros(test.sum(), bool)
    take = int((probability[test] > best["threshold"]).sum())
    if take:
        matched[RNG.choice(test.sum(), size=take, replace=False)] = True
    rows.append(evaluate("RANDOM_MATCHED", matched, hold[test], exit_value[test], days[test]))
    rows.append(evaluate("PERFECT (hindsight)", exit_value[test] > hold[test], hold[test],
                         exit_value[test], days[test]))
    for row in rows:
        print(f"{row['policy']:<24}{row['exits']:>9,}{row['mean']:>11.4f}{row['lcb']:>11.4f}")

    head = next(r for r in rows if r["policy"].startswith("HEAD"))
    bar = max(rows[0]["mean"], rows[1]["mean"])
    print()
    if head["mean"] > bar and head["lcb"] > 0:
        print("  VERDICT: the head beats both fixed policies AND its lower bound clears zero.")
    elif head["mean"] > bar:
        print(f"  VERDICT: beats both fixed policies ({head['mean']:+.4f} vs {bar:+.4f}) but "
              f"its day-block lower bound is {head['lcb']:+.4f} - not distinguishable from")
        print("  noise, and not a candidate.")
    else:
        print(f"  VERDICT: FAILS - {head['mean']:+.4f} does not beat the better fixed policy "
              f"({bar:+.4f}).")
        print("  The ceiling is real and this head does not reach it.")
    print("  Elimination-grade regardless: zero FORWARD_UNTOUCHED rows exist.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
