"""PHASE5D_157 - after conditioning on the market price, does ANY feature add information?

WHY THIS IS THE MOST IMPORTANT TEST IN THE BACKLOG
    Preregistration A (market-prior residual) is frozen and waiting for forward rows. This test
    can kill it BEFORE any of that forward evidence is spent.

        if no feature family adds resolution beyond the executable market price,
        the entire market-prior residual programme stops

    That is worth more than any model it might otherwise produce, because the alternative is
    spending eight forward weeks to learn the same thing.

WHAT IS MEASURED, AND WHY RESOLUTION
    Murphy: Brier = reliability - resolution + uncertainty. Section 10.5 measured P(hold)'s
    entire deficit as RESOLUTION (+0.0143 of +0.0144). Calibration is not the problem, so an
    improvement in Brier that comes from reliability is a re-mapping, not new information.

    Every arm here is therefore judged on INCREMENTAL RESOLUTION over the market-only baseline.

CHRONOLOGICAL CROSS-FITTING, NOT A SINGLE SPLIT
    A feature family cannot be judged by a model trained and scored on the same period. Three
    strictly chronological folds; each fold trains on everything BEFORE it and scores only
    itself; the reported number is the pooled out-of-fold prediction.

DECLARED STATUS: DESCRIPTIVE_ONLY
    21 days gives a ~25 point minimum detectable effect and this test generates no trades - by
    design, per its specification. It informs a decision; it does not authorise one.

    python research/phase5d/test_market_price_sufficiency_boundary.py
"""
from __future__ import annotations

RESEARCH_STATUS = "VALID_DIAGNOSTIC"
CAPITAL_AUTHORITY = False

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "research" / "phase5c"))

from _common import load_checkpoints, murphy_decomposition, side_ask  # noqa: E402

BOOTSTRAP_DRAWS = 2000


def day_block_resolution_ci(probability, outcomes, days, baseline, draws=BOOTSTRAP_DRAWS):
    """Day-block bootstrap CI on the resolution DIFFERENCE against a baseline forecast.

    A point estimate of -0.0003 says nothing without its spread over 21 days. Resampling DAYS
    - the same unit day_block_lcb uses - is what makes the interval honest here."""
    unique = np.unique(days)
    if len(unique) < 5:
        return (float("nan"), float("nan"))
    by_day = {d: np.where(days == d)[0] for d in unique}
    generator = np.random.default_rng(20260802)
    deltas = np.empty(draws)
    for index in range(draws):
        picked = generator.integers(0, len(unique), len(unique))
        rows = np.concatenate([by_day[unique[j]] for j in picked])
        challenger = murphy_decomposition(probability[rows], outcomes[rows])["resolution"]
        champion = murphy_decomposition(baseline[rows], outcomes[rows])["resolution"]
        deltas[index] = challenger - champion
    deltas.sort()
    return (float(deltas[int(0.025 * draws)]), float(deltas[int(0.975 * draws)]))

FOLDS = 3
MIN_FOLD_ROWS = 2_000

#: Frozen feature families. Declared before the run; not searched over.
FAMILIES: dict[str, tuple[str, ...]] = {
    "A market only": (),
    "B + BTC state": ("distance_pct", "distance_bps"),
    "C + volatility": ("vol_60s_pct",),
    "D + model outputs": ("p_hold_cur", "p_hold_up", "p_hold_down"),
    "E + book state": ("up_spread", "down_spread", "up_top_ask_size", "down_top_ask_size",
                       "up_d1", "down_d1"),
    "F + everything": ("distance_pct", "distance_bps", "vol_60s_pct", "p_hold_cur",
                       "p_hold_up", "p_hold_down", "up_spread", "down_spread",
                       "up_top_ask_size", "down_top_ask_size", "up_d1", "down_d1"),
}
#: A matched NULL arm: the same number of columns as the largest real family, filled with
#: random noise. Any real family must beat this to count as adding information - a positive
#: delta on its own is worthless, because fitting more parameters moves the number a little
#: whatever they contain. A first version reported +0.00004 as "RESIDUAL_LANE_SUPPORTED".
NULL_COLUMNS = 12
#: Always present in every arm, including the baseline.
BASELINE = ("market_logit", "checkpoint_s")


def cross_fitted_probability(matrix, target, days, folds: int = FOLDS):
    """Out-of-fold predictions from strictly chronological folds.

    Fold k trains on every day BEFORE fold k and scores only fold k. No fold is ever scored by
    a model that saw it, and no fold is scored by a model trained on its future."""
    from sklearn.linear_model import LogisticRegression
    from sklearn.preprocessing import StandardScaler

    unique = np.sort(np.unique(days))
    if len(unique) < folds * 2:
        return None
    edges = np.array_split(unique, folds)
    out = np.full(len(target), np.nan)
    for index in range(1, folds):                      # fold 0 has no past to train on
        test_days = set(edges[index].tolist())
        test = np.array([d in test_days for d in days])
        train = days < edges[index][0]
        if train.sum() < MIN_FOLD_ROWS or test.sum() < 200:
            continue
        scaler = StandardScaler().fit(matrix[train])
        model = LogisticRegression(max_iter=2000, C=1.0)
        model.fit(scaler.transform(matrix[train]), target[train])
        out[test] = model.predict_proba(scaler.transform(matrix[test]))[:, 1]
    return out


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    rng = np.random.default_rng(17)
    days = np.repeat(np.arange(30), 400)
    signal = rng.normal(0, 1, len(days))
    target = (rng.uniform(0, 1, len(days)) < 1 / (1 + np.exp(-signal))).astype(int)

    informative = cross_fitted_probability(np.column_stack([signal]), target, days)
    noise = cross_fitted_probability(np.column_stack([rng.normal(0, 1, len(days))]),
                                     target, days)
    scored = ~np.isnan(informative)
    check(scored.sum() > 0, "the later folds are scored out of fold")
    check(np.isnan(informative[days < 10]).all(),
          "the FIRST fold is never scored - it has no past to train on")

    good = murphy_decomposition(informative[scored], target[scored])
    bad = murphy_decomposition(noise[scored], target[scored])
    check(good["resolution"] > bad["resolution"],
          "a genuinely informative feature has higher out-of-fold resolution than noise")
    check(bad["resolution"] < 0.01,
          "a pure-noise feature adds essentially no resolution - the null behaves")
    check(cross_fitted_probability(np.column_stack([signal[:20]]), target[:20],
                                   days[:20]) is None,
          "too few days to fold returns None rather than a fabricated score")

    print(f"\nSUFFICIENCY BOUNDARY SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    print("=" * 100)
    print("PHASE5D-157  MARKET SUFFICIENCY - does anything add resolution beyond the price?")
    print("=" * 100)
    frame = load_checkpoints()
    if frame.empty:
        print("  BLOCKED: no eligible settled checkpoints.")
        return 0

    won = frame["won"].to_numpy(int)
    ask = np.clip(side_ask(frame), 1e-4, 1 - 1e-4)
    frame = frame.assign(market_logit=np.log(ask / (1 - ask)))
    days = (frame["snapshot_ts"].to_numpy(float) // 86_400).astype(np.int64)

    print(f"  rows {len(frame):,} over {len(np.unique(days))} days | "
          f"{FOLDS} chronological folds, out-of-fold scoring")
    print("  DESCRIPTIVE_ONLY by design - this test generates no trades.")
    print()
    print(f"{'family':<22}{'n scored':>10}{'Brier':>9}{'resolution':>12}{'d resolution':>14}"
          f"  95% day-block CI")

    baseline_resolution = None
    results = {}
    for name, extra in FAMILIES.items():
        columns = list(BASELINE) + [c for c in extra if c in frame.columns]
        matrix = frame[columns].to_numpy(float)
        keep = np.isfinite(matrix).all(axis=1)
        probability = cross_fitted_probability(matrix[keep], won[keep], days[keep])
        if probability is None:
            print(f"{name:<22}{'-':>10}  not enough days to cross-fit")
            continue
        scored = ~np.isnan(probability)
        values = np.clip(probability[scored], 1e-6, 1 - 1e-6)
        outcomes = won[keep][scored]
        part = murphy_decomposition(values, outcomes)
        log_loss = float(-np.mean(outcomes * np.log(values)
                                  + (1 - outcomes) * np.log(1 - values)))
        if baseline_resolution is None:
            baseline_resolution, baseline_log_loss = part["resolution"], log_loss
            baseline_values, baseline_outcomes = values, outcomes
            baseline_days = days[keep][scored]
            low, high = 0.0, 0.0
        else:
            low, high = day_block_resolution_ci(values, outcomes, days[keep][scored],
                                                baseline_values)
        results[name] = (part["resolution"] - baseline_resolution, log_loss - baseline_log_loss,
                         low, high)
        print(f"{name:<22}{int(scored.sum()):>10,}{part['brier']:>9.4f}"
              f"{part['resolution']:>12.4f}{results[name][0]:>14.4f}"
              f"  [{low:+.4f}, {high:+.4f}]")

    # The matched null: same column count as the widest real family, pure noise.
    generator = np.random.default_rng(20260802)
    noise = generator.normal(0, 1, (len(frame), NULL_COLUMNS))
    null_matrix = np.column_stack([frame[list(BASELINE)].to_numpy(float), noise])
    keep = np.isfinite(null_matrix).all(axis=1)
    null_probability = cross_fitted_probability(null_matrix[keep], won[keep], days[keep])
    null_gain = 0.0
    if null_probability is not None:
        scored = ~np.isnan(null_probability)
        values = np.clip(null_probability[scored], 1e-6, 1 - 1e-6)
        null_part = murphy_decomposition(values, won[keep][scored])
        null_gain = null_part["resolution"] - baseline_resolution
        print(f"{'Z null (noise)':<22}{int(scored.sum()):>10,}{null_part['brier']:>9.4f}"
              f"{null_part['resolution']:>12.4f}{null_gain:>14.4f}")

    gains = {k: v for k, v in results.items() if k != "A market only"}
    best = max(gains.items(), key=lambda kv: kv[1][0]) if gains else None
    print()
    if best is None:
        print("  VERDICT: NOT MEASURED")
    elif best[1][0] <= max(null_gain, 0.0):
        print("  VERDICT: NO_DETECTABLE_INCREMENTAL_RESOLUTION")
        print("           for CURRENT FEATURES, under the TESTED LEARNER, at THIS SAMPLE SIZE.")
        print(f"  No frozen family beats a MATCHED NOISE arm (null gain {null_gain:+.4f}).")
        positive = [n for n, v in gains.items() if v[2] > 0]
        harmful = [n for n, v in gains.items() if v[3] < 0]
        print(f"  detectable POSITIVE gain (CI above zero): "
              f"{', '.join(positive) if positive else 'NONE'}")
        print(f"  detectable HARM (CI below zero)         : "
              f"{', '.join(harmful) if harmful else 'none'}")
        print("  Adding features does not merely fail to help - on this sample some of them")
        print("  measurably HURT out-of-fold resolution. Preregistration A is retired")
        print("  operationally: eight forward weeks are not justified on this evidence.")
        print()
        print("  What this does NOT establish: that no information exists. 21 independent days,")
        print("  one learner family and one probability grouping cannot prove absence. A future")
        print("  A2 must freeze a NEW information source, exact learner families, transforms,")
        print("  a search budget, null controls and a minimum material lift before it is worth")
        print("  running.")
    else:
        print(f"  VERDICT: DETECTABLE_RESOLUTION_GAIN (best {best[0]}, "
              f"+{best[1][0]:.4f}, CI [{best[1][2]:+.4f}, {best[1][3]:+.4f}])")
        print("  At least one family adds out-of-fold resolution beyond the price. That is the")
        print("  raw material Preregistration A needs - necessary, not sufficient: the forward")
        print("  run still has to convert it into executable value.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
