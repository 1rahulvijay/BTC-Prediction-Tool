"""BINANCE_OPPORTUNITY_HEAD_V1 - does direction become predictable inside high-opportunity states?

THE ONE HYPOTHESIS, TAKEN FROM THE BLUEPRINT AND TESTED DIRECTLY
    "A 51% unconditional model may have better precision inside a small, high-opportunity
    subset." That is a real, falsifiable claim and it is the only untested lane left: the
    action-value engine measured a ceiling of +18.46 bps at 60m and +31.37 at 120m against a
    12.0 bps round trip, while every fixed rule lost.

    The two-stage design follows from it:

        head 1  OPPORTUNITY   P(|return over H| > round trip) - no direction needed
        head 2  DIRECTION     P(up | this state), measured OVERALL and inside the top decile
        policy               trade the predicted side only where opportunity is high

    Magnitude alone cannot pay. Knowing a big move is coming without knowing its sign is worth
    nothing unless you straddle, which doubles the cost. So the study stands or falls on
    whether conditioning lifts DIRECTION - and it reports that number whatever it says.

WHAT THE PREVIOUS TWO STUDIES TAUGHT, APPLIED HERE
    A head can rank well and convert nothing: the hold-vs-exit classifier reached AUC 0.8731
    and lost to doing nothing. So AUC is reported as a DIAGNOSTIC and the verdict is decided on
    realised net value against fixed policies and a matched-count control.

PROTOCOL, DECLARED BEFORE RESULTS
    G1  strictly chronological: train / calibrate / evaluate, evaluation last and untouched
    G2  training windows may overlap (more data, correlated); EVALUATION windows are DISJOINT,
        and a purge gap of one horizon separates the splits so no window straddles a boundary
    G3  features verified backward-looking before use - each correlates more with the PAST
        absolute return than the future one
    G4  threshold chosen on CALIBRATE only, never on the evaluation window
    G5  PASS requires beating WAIT, always-long, always-short and matched random on realised
        net value, with a day-block lower bound above zero
    G6  costs from binance_paper.config, never retyped

    python research/binance_opportunity_head_v1.py
    python research/binance_opportunity_head_v1.py --selftest
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
sys.path.insert(0, str(ROOT / "backend" / "binance_alpha"))

from action_value import round_trip_bps  # noqa: E402
from ev_magnitude_rule_v1 import day_block_lcb_at  # noqa: E402
from phold_auc_and_expectancy import expected_calibration_error, roc_auc  # noqa: E402

BARS = ROOT / "data" / "btc_1m_data.csv"
HORIZONS_M = (60, 120)
#: Verified backward-looking. vol_accel and count_accel_5m are dropped as uninformative
#: (|corr| < 0.05 with both past and future absolute return) rather than kept as noise.
FEATURES = ("rv_15m", "rv_30m", "rv_60m", "rv_term", "vpin_15m", "vpin_30m", "vpin_50m",
            "log_count", "log_vol", "trade_count", "volume", "taker_buy", "taker_sell")
TRAIN_END, CALIBRATE_END = 0.60, 0.80
OPPORTUNITY_GRID = (0.30, 0.40, 0.50, 0.60, 0.70)
TOP_DECILE = 0.90
MIN_EVAL = 200
RNG = np.random.default_rng(20260802)
MODEL_KWARGS = dict(max_iter=250, max_depth=4, learning_rate=0.06,
                    min_samples_leaf=80, l2_regularization=1.0, random_state=42)


def load():
    import duckdb

    con = duckdb.connect(":memory:")
    try:
        return con.execute(
            f"SELECT ts_ms, close, {','.join(FEATURES)} "
            f"FROM read_csv_auto('{BARS.as_posix()}') ORDER BY ts_ms").df()
    finally:
        con.close()


def build_windows(frame, horizon_m: int, *, stride: int):
    """Rows of (index, features, forward return in bps). Stride 1 overlaps; stride H does not."""
    close = frame["close"].to_numpy(float)
    matrix = frame[list(FEATURES)].to_numpy(float)
    starts = np.arange(0, len(close) - horizon_m, stride)
    forward = (close[starts + horizon_m] - close[starts]) / close[starts] * 10_000.0
    usable = np.isfinite(forward) & np.isfinite(matrix[starts]).all(axis=1)
    return starts[usable], matrix[starts][usable], forward[usable]


def auc_standard_error(auc: float, labels) -> float:
    """Hanley-McNeil standard error of an AUC.

    Without it a lift measured on 178 rows reads the same as one measured on 17,800, and a
    first version of this file printed "LIFTS" for exactly that reason - a 0.5632 on n=178 is
    about one standard error from chance."""
    positives = int(np.sum(labels == 1))
    negatives = int(len(labels) - positives)
    if positives < 2 or negatives < 2 or not np.isfinite(auc):
        return float("nan")
    q1 = auc / (2.0 - auc)
    q2 = 2.0 * auc * auc / (1.0 + auc)
    variance = (auc * (1 - auc)
                + (positives - 1) * (q1 - auc * auc)
                + (negatives - 1) * (q2 - auc * auc)) / (positives * negatives)
    return float(np.sqrt(max(variance, 0.0)))


def policy_value(trade, direction_up, forward_bps, cost_bps):
    """Net bps: long earns the move, short earns its negative, both pay the round trip."""
    gross = np.where(direction_up, forward_bps, -forward_bps)
    return np.where(trade, gross - cost_bps, 0.0)


def selftest() -> int:
    import pandas as pd

    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    check(abs(round_trip_bps() - 12.0) < 1e-9,
          "the round trip comes from binance_paper.config, not a constant retyped here")

    close = [100.0 * (1.0 + 0.001 * index) for index in range(200)]
    frame = pd.DataFrame({"ts_ms": range(200), "close": close,
                          **{name: np.linspace(1, 2, 200) for name in FEATURES}})
    starts_overlap, _, fwd_overlap = build_windows(frame, 60, stride=1)
    starts_disjoint, _, _ = build_windows(frame, 60, stride=60)
    check(len(starts_overlap) > len(starts_disjoint) * 10,
          "overlapping windows give far more TRAINING rows than disjoint ones")
    check(len(np.unique(np.diff(starts_disjoint))) == 1
          and np.diff(starts_disjoint)[0] == 60,
          "evaluation windows are DISJOINT - stride equals the horizon exactly")
    check(fwd_overlap[0] > 0, "a rising series yields a positive forward return")

    forward = np.array([50.0, -50.0, 5.0, -5.0])
    always_long = policy_value(np.ones(4, bool), np.ones(4, bool), forward, 12.0)
    check(abs(always_long.mean() - (forward.mean() - 12.0)) < 1e-9,
          "always-long earns the mean move minus the full round trip")
    perfect = policy_value(np.abs(forward) > 12.0, forward > 0, forward, 12.0)
    check(abs(perfect.mean() - ((50.0 - 12.0) + (50.0 - 12.0)) / 4) < 1e-9,
          "a perfect gate+direction trades only the two big moves and wins both")
    check(policy_value(np.zeros(4, bool), np.ones(4, bool), forward, 12.0).sum() == 0.0,
          "not trading is worth exactly zero, never a fee")

    print(f"\nBINANCE OPPORTUNITY SELFTEST: PASS ({checks} checks)")
    return 0


def run_horizon(frame, horizon_m: int, cost: float) -> None:
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.isotonic import IsotonicRegression

    total = len(frame)
    train_end = int(total * TRAIN_END)
    calibrate_end = int(total * CALIBRATE_END)

    starts, matrix, forward = build_windows(frame, horizon_m, stride=1)
    # PURGE one horizon at each boundary so no training window overlaps a later split.
    train = starts < (train_end - horizon_m)
    calibrate = (starts >= train_end) & (starts < calibrate_end - horizon_m)

    eval_starts, eval_matrix, eval_forward = build_windows(frame, horizon_m, stride=horizon_m)
    keep = eval_starts >= calibrate_end
    eval_matrix, eval_forward = eval_matrix[keep], eval_forward[keep]
    days = (frame["ts_ms"].to_numpy()[eval_starts[keep]] // 86_400_000).astype(np.int64)

    print()
    print(f"  === {horizon_m}m " + "=" * 84)
    if eval_matrix.shape[0] < MIN_EVAL or train.sum() < 1000:
        print(f"  BLOCKED: {eval_matrix.shape[0]} evaluation windows, {train.sum()} training.")
        return
    print(f"  train {train.sum():,} overlapping | calibrate {calibrate.sum():,} | "
          f"evaluate {eval_matrix.shape[0]:,} DISJOINT")

    opportunity_target = (np.abs(forward) > cost).astype(int)
    direction_target = (forward > 0).astype(int)

    opportunity = HistGradientBoostingClassifier(**MODEL_KWARGS)
    opportunity.fit(matrix[train], opportunity_target[train])
    iso = IsotonicRegression(out_of_bounds="clip").fit(
        opportunity.predict_proba(matrix[calibrate])[:, 1], opportunity_target[calibrate])
    p_opportunity = np.clip(iso.predict(opportunity.predict_proba(eval_matrix)[:, 1]),
                            1e-6, 1 - 1e-6)

    direction = HistGradientBoostingClassifier(**MODEL_KWARGS)
    direction.fit(matrix[train], direction_target[train])
    p_up = direction.predict_proba(eval_matrix)[:, 1]

    eval_opportunity = (np.abs(eval_forward) > cost).astype(int)
    eval_direction = (eval_forward > 0).astype(int)
    print(f"  opportunity head : AUC {roc_auc(p_opportunity, eval_opportunity):.4f} | "
          f"ECE {expected_calibration_error(p_opportunity, eval_opportunity):.4f} | "
          f"base rate {eval_opportunity.mean():.3f}")

    # --- THE HYPOTHESIS -----------------------------------------------------------------
    overall = roc_auc(p_up, eval_direction)
    cutoff = np.quantile(p_opportunity, TOP_DECILE)
    top = p_opportunity >= cutoff
    inside = roc_auc(p_up[top], eval_direction[top]) if top.sum() > 30 else float("nan")
    inside_se = (auc_standard_error(inside, eval_direction[top])
                 if top.sum() > 30 else float("nan"))
    print(f"  DIRECTION AUC overall {overall:.4f} | inside the top opportunity decile "
          f"{inside:.4f} +/- {inside_se:.4f}  (n={int(top.sum())})")
    # A lift must clear its own standard error. Comparing point estimates alone once made this
    # file print "LIFTS" for a 0.5632 on 178 rows - about one standard error from chance.
    lifts = (np.isfinite(inside) and np.isfinite(inside_se)
             and inside - overall > 2.0 * inside_se)
    print(f"  -> conditioning on high opportunity "
          f"{'LIFTS' if lifts else 'does NOT lift'} direction "
          f"(lift {inside - overall:+.4f} vs 2 s.e. {2 * inside_se:.4f})")

    # --- policy, threshold chosen on CALIBRATE only ---------------------------------------
    cal_p_opp = np.clip(iso.predict(
        opportunity.predict_proba(matrix[calibrate])[:, 1]), 1e-6, 1 - 1e-6)
    cal_p_up = direction.predict_proba(matrix[calibrate])[:, 1]
    best = None
    for threshold in OPPORTUNITY_GRID:
        value = policy_value(cal_p_opp > threshold, cal_p_up > 0.5,
                             forward[calibrate], cost)
        if best is None or value.mean() > best[1]:
            best = (threshold, float(value.mean()))
    print(f"  threshold chosen on calibrate: {best[0]:.2f} (calibrate mean {best[1]:+.2f} bps)")

    trade = p_opportunity > best[0]
    rows = [
        ("WAIT", np.zeros(len(eval_forward))),
        ("ALWAYS_LONG", policy_value(np.ones(len(eval_forward), bool),
                                     np.ones(len(eval_forward), bool), eval_forward, cost)),
        ("ALWAYS_SHORT", policy_value(np.ones(len(eval_forward), bool),
                                      np.zeros(len(eval_forward), bool), eval_forward, cost)),
        (f"HEAD@{best[0]:.2f}", policy_value(trade, p_up > 0.5, eval_forward, cost)),
    ]
    matched = np.zeros(len(eval_forward), bool)
    if trade.sum():
        matched[RNG.choice(len(eval_forward), size=int(trade.sum()), replace=False)] = True
    rows.append(("RANDOM_MATCHED", policy_value(matched, RNG.random(len(eval_forward)) > 0.5,
                                                eval_forward, cost)))
    rows.append(("PERFECT (hindsight)", policy_value(np.abs(eval_forward) > cost,
                                                     eval_forward > 0, eval_forward, cost)))
    print()
    print(f"{'policy':<24}{'trades':>8}{'mean bps':>11}{'day LCB':>11}")
    for name, value in rows:
        trades = int((value != 0).sum())
        print(f"{name:<24}{trades:>8,}{value.mean():>11.2f}"
              f"{day_block_lcb_at(value, days, 0.05):>11.2f}")

    head = next(v for n, v in rows if n.startswith("HEAD"))
    bar = max(rows[0][1].mean(), rows[1][1].mean(), rows[2][1].mean(), rows[4][1].mean())
    lcb = day_block_lcb_at(head, days, 0.05)
    if head.mean() > bar and lcb > 0:
        print(f"  VERDICT {horizon_m}m: PASSES - beats every fixed policy and the lower bound "
              f"clears zero.")
    else:
        print(f"  VERDICT {horizon_m}m: FAILS - {head.mean():+.2f} bps against a bar of "
              f"{bar:+.2f}, lower bound {lcb:+.2f}.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    print("=" * 104)
    print("BINANCE OPPORTUNITY HEAD - does conditioning on a big move make DIRECTION tradeable?")
    print("=" * 104)
    if args.selftest:
        return selftest()
    if not BARS.is_file():
        print(f"  BLOCKED: {BARS.name} is missing.")
        return 0

    cost = round_trip_bps()
    frame = load()
    print(f"  bars {len(frame):,} | features {len(FEATURES)} (all verified backward-looking) "
          f"| round trip {cost:.1f} bps")
    for horizon in HORIZONS_M:
        run_horizon(frame, horizon, cost)
    print()
    print("  Magnitude alone cannot pay: a big move of unknown sign is worth nothing unless")
    print("  you straddle, which doubles the cost. The direction line above is the study.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
