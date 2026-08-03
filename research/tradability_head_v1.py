"""Movement gate: is there enough predicted move in the next 15 minutes to be worth trading?

PROTOCOL
    docs/active/PREREG_TRADABILITY_HEAD_V1.md, sha256 bb48c577..., frozen before training.

THE BASELINE IS THE INCUMBENT, NOT ZERO
    REGIME_VOLATILITY_CONTROL_V1 retired a taxonomy that looked separable until it was compared
    against current realised volatility, which explained 84% of it. So this head is scored
    against rv_60m alone. A model that matches the volatility baseline has added nothing,
    however good its AUC looks against a constant.

    python research/tradability_head_v1.py --selftest
    python research/tradability_head_v1.py
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "data" / "research_matrix_1m.parquet"
PROTOCOL = "PREREG_TRADABILITY_HEAD_V1.md"

FORWARD_BARS = 15
TRAIN_FRACTION = 0.70
PURGE_BARS = 60
TOP_DECILE = 0.10
MATERIAL_POINTS = 2.0
BINANCE_HURDLE = 14.0
POLYMARKET_HURDLE = 149.0
COVERAGES = (0.05, 0.10, 0.25, 0.50)

FEATURES = (
    "rv_15m", "rv_30m", "rv_60m", "rv_term",
    "compression_ratio", "range_15m", "micro_range_15m", "shock_magnitude",
    "cvd_1m", "cvd_5m", "cvd_change", "delta", "large_trade_imbalance",
    "vpin_15m", "vpin_30m", "vpin_50m",
    "log_vol", "vol_accel", "count_accel_5m", "log_count",
    "perp_spot_basis_bps", "funding_velocity", "cvd_divergence",
)
BASELINE_FEATURE = "rv_60m"

#: Anything that could see the target. Asserted, not merely intended.
FORBIDDEN = ("future_close_5m", "future_high_5m", "future_low_5m", "future_abs_move_5m",
             "future_direction_5m", "ret_5m", "tradable_move_label", "fail_fast_label",
             "fwd_abs_bps")


def load_frame() -> pd.DataFrame:
    import pyarrow.parquet as pq
    columns = ["ts_ms", "close"] + list(FEATURES)
    frame = pq.read_table(MATRIX, columns=columns).to_pandas()
    frame = frame.sort_values("ts_ms").drop_duplicates("ts_ms").reset_index(drop=True)
    frame["fwd_abs_bps"] = (frame["close"].shift(-FORWARD_BARS) / frame["close"] - 1).abs() * 1e4
    frame["day"] = frame["ts_ms"] // 86_400_000
    return frame.dropna(subset=list(FEATURES) + ["fwd_abs_bps"]).reset_index(drop=True)


def top_decile_hit(scores: np.ndarray, realised: np.ndarray, hurdle: float,
                   coverage: float = TOP_DECILE) -> float:
    """Share of the top-`coverage` predicted bars whose realised move clears the hurdle."""
    if len(scores) == 0:
        return float("nan")
    k = max(1, int(len(scores) * coverage))
    top = np.argsort(-scores)[:k]
    return float((realised[top] > hurdle).mean())


def day_block_diff_ci(scores_a: np.ndarray, scores_b: np.ndarray, realised: np.ndarray,
                      days: np.ndarray, hurdle: float,
                      iterations: int = 800, seed: int = 29) -> tuple:
    """CI on the DIFFERENCE in top-decile hit rate, resampling whole days.

    The difference is bootstrapped directly rather than differencing two separate intervals -
    the two models rank the same bars, so their errors are strongly correlated and independent
    intervals would be far too wide."""
    unique = np.unique(days)
    if len(unique) < 2:
        return (float("nan"), float("nan"))
    index = {d: np.flatnonzero(days == d) for d in unique}
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(iterations):
        pick = rng.integers(0, len(unique), len(unique))
        rows = np.concatenate([index[unique[j]] for j in pick])
        a = top_decile_hit(scores_a[rows], realised[rows], hurdle)
        b = top_decile_hit(scores_b[rows], realised[rows], hurdle)
        if np.isfinite(a) and np.isfinite(b):
            draws.append(a - b)
    if len(draws) < 50:
        return (float("nan"), float("nan"))
    return (float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5)))


def auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """Rank AUC. Argument order is (scores, labels) - swapping them silently returns nonsense,
    which is how an earlier study in this repository reported nan for every arm."""
    positives, negatives = labels == 1, labels == 0
    if not positives.any() or not negatives.any():
        return float("nan")
    # AVERAGE ranks for ties. Assigning distinct ranks by argsort order makes the result depend
    # on an arbitrary ordering: a constant score would return anything but 0.5. This is not
    # hypothetical - a tree model with a large min_child_samples emits the same probability for
    # many rows, so ties are the normal case here, not an edge case.
    order = np.argsort(scores, kind="mergesort")
    ordered = scores[order]
    ranks_sorted = np.arange(1, len(scores) + 1, dtype=float)
    start = 0
    for i in range(1, len(ordered) + 1):
        if i == len(ordered) or ordered[i] != ordered[start]:
            if i - start > 1:
                ranks_sorted[start:i] = ranks_sorted[start:i].mean()
            start = i
    ranks = np.empty(len(scores), dtype=float)
    ranks[order] = ranks_sorted
    n_pos, n_neg = positives.sum(), negatives.sum()
    return float((ranks[positives].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def verdict_for(candidate: float, volatility: float, constant: float,
                ci: tuple) -> tuple[str, str]:
    gain_points = (candidate - volatility) * 100
    if not (np.isfinite(candidate) and np.isfinite(volatility)):
        return "TRADABILITY_NOT_PREDICTABLE", "no usable scores"
    if candidate <= constant and volatility <= constant:
        return ("TRADABILITY_NOT_PREDICTABLE",
                "neither the candidate nor the volatility baseline beats the base rate")
    if gain_points >= MATERIAL_POINTS and np.isfinite(ci[0]) and ci[0] > 0:
        return ("TRADABILITY_HEAD_ADDS",
                f"top-decile hit rate beats the volatility baseline by "
                f"{gain_points:+.1f} points, CI excludes zero")
    if np.isfinite(ci[0]) and ci[0] > 0:
        return ("TRADABILITY_IS_VOLATILITY",
                f"the gain of {gain_points:+.1f} points is statistically present but below "
                f"the declared {MATERIAL_POINTS:.1f}-point bar")
    return ("TRADABILITY_IS_VOLATILITY",
            f"the CI on the gain ({gain_points:+.1f} points) spans zero")


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    check(len(FEATURES) == 23, "the frozen feature set is 23 backward-looking columns")
    check(BASELINE_FEATURE in FEATURES, "the incumbent baseline feature is inside the set")
    leaked = [f for f in FEATURES if f in FORBIDDEN]
    check(not leaked, f"NO forbidden column is in the feature set (checked {len(FORBIDDEN)})")
    check("fwd_abs_bps" in FORBIDDEN, "the target itself is on the forbidden list")
    check(MATERIAL_POINTS == 2.0 and TOP_DECILE == 0.10,
          "the protocol's declared constants are in force")

    rng = np.random.default_rng(0)
    n = 20_000
    signal = rng.normal(size=n)
    realised = np.abs(signal) * 20 + rng.normal(0, 1, n).clip(0)
    days = np.repeat(np.arange(n // 100), 100)[:n]

    perfect = np.abs(signal)
    useless = rng.normal(size=n)
    check(top_decile_hit(perfect, realised, 20.0) >
          top_decile_hit(useless, realised, 20.0),
          "a informative score beats a random one on top-decile hit rate")
    base = float((realised > 20.0).mean())
    check(top_decile_hit(perfect, realised, 20.0) > base,
          "...and beats the unconditional base rate")

    lo, hi = day_block_diff_ci(perfect, useless, realised, days, 20.0)
    check(np.isfinite(lo) and lo > 0,
          "a genuine advantage produces a difference CI that EXCLUDES zero")
    lo2, hi2 = day_block_diff_ci(useless, rng.normal(size=n), realised, days, 20.0)
    check(np.isfinite(lo2) and lo2 < 0 < hi2,
          "two equally useless scores produce a CI that SPANS zero - not always significant")

    labels = (realised > 20.0).astype(int)
    check(auc(perfect, labels) > 0.7, "AUC recognises an informative score")
    check(abs(auc(useless, labels) - 0.5) < 0.05, "AUC of noise is ~0.5")
    check(auc(np.zeros(n), labels) == 0.5,
          "a CONSTANT score has AUC exactly 0.5 - ties get average ranks, not argsort order")
    half_tied = np.where(rng.random(n) < 0.5, 0.0, np.abs(signal))
    check(0.5 <= auc(half_tied, labels) <= 1.0,
          "a heavily tied score still yields a valid AUC in [0.5, 1]")

    kind, _ = verdict_for(0.80, 0.60, 0.30, (0.10, 0.30))
    check(kind == "TRADABILITY_HEAD_ADDS", "a large, significant gain passes")
    kind, _ = verdict_for(0.61, 0.60, 0.30, (0.002, 0.02))
    check(kind == "TRADABILITY_IS_VOLATILITY",
          "a significant but IMMATERIAL gain is a restatement, not a pass")
    kind, _ = verdict_for(0.80, 0.60, 0.30, (-0.05, 0.30))
    check(kind == "TRADABILITY_IS_VOLATILITY", "a large gain whose CI spans zero does not pass")
    kind, _ = verdict_for(0.30, 0.29, 0.30, (0.0, 0.1))
    check(kind == "TRADABILITY_NOT_PREDICTABLE",
          "if neither model beats the base rate, movement is not predictable")

    print(f"\nTRADABILITY HEAD SELFTEST: PASS ({checks} checks)")
    return 0


def run() -> int:
    if not MATRIX.is_file():
        print(f"missing {MATRIX}")
        return 1
    frame = load_frame()
    split = int(len(frame) * TRAIN_FRACTION)
    train = frame.iloc[:split]
    test = frame.iloc[split + PURGE_BARS:].reset_index(drop=True)

    import datetime as dt
    fmt = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d")
    print("=" * 104)
    print(f"TRADABILITY HEAD V1 - protocol {PROTOCOL} (frozen before training)")
    print("=" * 104)
    print(f"  bars {len(frame):,}   train {fmt(frame.ts_ms.iloc[0])} -> "
          f"{fmt(frame.ts_ms.iloc[split])}   purge {PURGE_BARS}   test "
          f"-> {fmt(frame.ts_ms.iloc[-1])} ({len(test):,} bars)")
    print(f"  features {len(FEATURES)}   baseline '{BASELINE_FEATURE}' alone   "
          f"materiality {MATERIAL_POINTS:.1f} points")

    Xtr, Xte = train[list(FEATURES)].to_numpy(float), test[list(FEATURES)].to_numpy(float)
    realised = test["fwd_abs_bps"].to_numpy(float)
    days = test["day"].to_numpy()

    import lightgbm as lgb
    for hurdle, name in ((BINANCE_HURDLE, "BINANCE"), (POLYMARKET_HURDLE, "POLYMARKET")):
        ytr = (train["fwd_abs_bps"].to_numpy(float) > hurdle).astype(int)
        yte = (realised > hurdle).astype(int)
        print()
        print(f"  --- hurdle {name} ({hurdle:.0f} bps).  base rate: train "
              f"{ytr.mean():.1%}, test {yte.mean():.1%}")
        if ytr.min() == ytr.max():
            print("      degenerate: every training bar is on one side of the hurdle")
            continue

        params = dict(n_estimators=300, learning_rate=0.05, num_leaves=31, min_child_samples=200,
                      verbose=-1, random_state=0)
        candidate = lgb.LGBMClassifier(**params).fit(Xtr, ytr)
        cand_scores = candidate.predict_proba(Xte)[:, 1]
        incumbent = lgb.LGBMClassifier(**params).fit(
            train[[BASELINE_FEATURE]].to_numpy(float), ytr)
        vol_scores = incumbent.predict_proba(test[[BASELINE_FEATURE]].to_numpy(float))[:, 1]

        cand_hit = top_decile_hit(cand_scores, realised, hurdle)
        vol_hit = top_decile_hit(vol_scores, realised, hurdle)
        const_hit = float(yte.mean())
        ci = day_block_diff_ci(cand_scores, vol_scores, realised, days, hurdle)

        print(f"      {'model':<24}{'top-decile hit':>16}{'AUC':>8}")
        print(f"      {'BASELINE_CONSTANT':<24}{const_hit:>15.1%}{'-':>8}")
        print(f"      {'BASELINE_VOLATILITY':<24}{vol_hit:>15.1%}{auc(vol_scores, yte):>8.3f}")
        print(f"      {'CANDIDATE (23 features)':<24}{cand_hit:>15.1%}"
              f"{auc(cand_scores, yte):>8.3f}")
        gain = (cand_hit - vol_hit) * 100
        ci_text = (f"[{ci[0]*100:+5.2f}, {ci[1]*100:+5.2f}] points"
                   if np.isfinite(ci[0]) else "(insufficient days)")
        print(f"      gain over incumbent  : {gain:+.2f} points   day-block 95% CI {ci_text}")

        print("      coverage curve (candidate):", end="")
        for cov in COVERAGES:
            print(f"  {cov:.0%}->{top_decile_hit(cand_scores, realised, hurdle, cov):.1%}",
                  end="")
        print()
        print("      coverage curve (incumbent):", end="")
        for cov in COVERAGES:
            print(f"  {cov:.0%}->{top_decile_hit(vol_scores, realised, hurdle, cov):.1%}",
                  end="")
        print()

        verdict, reason = verdict_for(cand_hit, vol_hit, const_hit, ci)
        print(f"      VERDICT: {verdict}")
        print(f"      {reason}")

    print()
    print("  Predicting movement is NECESSARY for a tradable round, never sufficient: a large")
    print("  expected move with unpredictable sign is not an opportunity. This head produces no")
    print("  direction forecast and no position.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    raise SystemExit(selftest() if args.selftest else run())
