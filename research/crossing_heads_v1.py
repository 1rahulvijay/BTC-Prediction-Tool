"""Final-crossing and reversion heads on 15,428 real labelled crossings.

PROTOCOL
    docs/active/PREREG_CROSSING_HEADS_V1.md, sha256 762532c9..., frozen before training.

THE INCUMBENT IS THE CLOCK
    A crossing with 10 seconds left is more likely final than one with 4 minutes left, for a
    purely mechanical reason. Any head that rediscovers that has added nothing, so the baseline
    is a model on `seconds_left` ALONE - not a constant.

    REGIME_VOLATILITY_CONTROL_V1 retired a taxonomy after 84% of it turned out to be current
    volatility. This is the same discipline in a different costume.

CAUSAL MARKET JOIN
    Market state comes from the last 1-minute bar that had CLOSED before the crossing. Using the
    bar CONTAINING the crossing would leak up to 59 seconds of future - the exact defect fixed
    in train_round_state_heads.

    python research/crossing_heads_v1.py --selftest
    python research/crossing_heads_v1.py
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

sys.path.insert(0, str(Path(__file__).resolve().parent))
from tradability_head_v1 import auc                                    # noqa: E402
from direction_ensemble_v1 import null_floor                           # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CROSSING_DB = ROOT / "data" / "polymarket_crossings.duckdb"
MATRIX = ROOT / "data" / "research_matrix_1m.parquet"
PROTOCOL = "PREREG_CROSSING_HEADS_V1.md"

BAR_MS = 60_000
TRAIN_FRACTION = 0.70
MATERIAL_AUC = 0.02
# v1 wrote `reverted_Ns`; v2 writes `state_original_side_at_Ns`. They are the SAME
# measurement - state at the horizon - and v1 was simply misnamed, so they can be
# unioned. The trainer reports the honest name.
#
# `ever_reverted_by_Ns` is a genuinely DIFFERENT target with no v1 equivalent. It is
# deliberately NOT listed here: backfilling it from v1 rows would invent data, and
# training on a target that only exists for recent rows would silently change what the
# head predicts partway through the sample.
TARGETS = ("is_final_crossing", "state_original_side_at_30s", "state_original_side_at_60s")
BASELINE_FEATURE = "seconds_left"
ROUND_FEATURES = ("seconds_left", "horizon_min", "crossing_index", "move_at_crossing",
                  "from_up", "elapsed_fraction")
MARKET_FEATURES = ("rv_15m", "rv_60m", "compression_ratio", "vpin_15m", "cvd_5m",
                   "delta", "large_trade_imbalance", "shock_magnitude")
FEATURES = ROUND_FEATURES + MARKET_FEATURES


def causal_bar_ts(crossing_ts):
    """Open time of the last 1-minute bar that had CLOSED before the crossing.

    The bar containing the crossing closes AFTER it, so it is never admissible."""
    return (crossing_ts // BAR_MS) * BAR_MS - BAR_MS


def load() -> pd.DataFrame:
    import duckdb
    con = duckdb.connect(str(CROSSING_DB), read_only=True)
    try:
        frame = con.execute("""
            SELECT e.crossing_id, e.round_id, e.horizon_min, e.crossing_ts, e.from_side,
                   e.seconds_left, e.move_at_crossing, e.crossing_index,
                   l.is_final_crossing,
                   -- v1 rows carry reverted_Ns, v2 rows carry state_original_side_at_Ns.
                   -- Same quantity, one honest name. COALESCE is safe ONLY because v2
                   -- leaves the v1 column NULL, so exactly one side is ever populated.
                   COALESCE(l.state_original_side_at_30s, l.reverted_30s)
                       AS state_original_side_at_30s,
                   COALESCE(l.state_original_side_at_60s, l.reverted_60s)
                       AS state_original_side_at_60s,
                   l.label_version
            FROM crossing_events e
            JOIN (
                -- CANONICAL: exactly one label row per crossing. The backfill re-resolves
                -- every crossing on each run, so once the recorder writes v2 a crossing
                -- can carry BOTH a (crossing_id, v1) and a (crossing_id, v2) row. Joining
                -- both would count the same crossing twice - COALESCE picks between
                -- columns inside a row, it does not remove duplicate rows. Prefer v2
                -- (richer, honestly named); fall back to the v1 row otherwise.
                SELECT * FROM (
                    SELECT *, row_number() OVER (
                        PARTITION BY crossing_id
                        ORDER BY CASE WHEN label_version = 'crossing_labels_v2'
                                      THEN 0 ELSE 1 END
                    ) AS _rank
                    FROM crossing_labels
                ) WHERE _rank = 1
            ) l ON l.crossing_id = e.crossing_id
            ORDER BY e.crossing_ts""").df()
    finally:
        con.close()

    # The invariant the canonical subquery exists to guarantee. If it ever fails, the
    # dataset is double-counting crossings and every AUC downstream is invalid.
    if frame["crossing_id"].duplicated().any():
        dupes = int(frame["crossing_id"].duplicated().sum())
        raise SystemExit(f"crossing_labels join produced {dupes} duplicate crossing_id "
                         f"rows - canonical selection is broken; refusing to train")
    frame["from_up"] = (frame["from_side"] == "UP").astype(float)
    frame["elapsed_fraction"] = 1.0 - frame["seconds_left"] / (frame["horizon_min"] * 60.0)
    frame["day"] = frame["crossing_ts"] // 86_400_000
    frame["bar_ts"] = causal_bar_ts(frame["crossing_ts"].to_numpy("int64"))

    import pyarrow.parquet as pq
    market = pq.read_table(MATRIX, columns=["ts_ms"] + list(MARKET_FEATURES)).to_pandas()
    market = market.rename(columns={"ts_ms": "bar_ts"}).drop_duplicates("bar_ts")
    return frame.merge(market, on="bar_ts", how="left")


def day_block_diff_ci(scores_a, scores_b, labels, days,
                      iterations: int = 1500, seed: int = 167) -> tuple:
    """CI on the AUC DIFFERENCE, resampling whole days.

    Bootstrapped on the difference because both models rank the same crossings and their errors
    are strongly correlated; differencing two independent intervals would be far too wide."""
    unique = np.unique(days)
    if len(unique) < 2:
        return (float("nan"), float("nan"))
    index = {d: np.flatnonzero(days == d) for d in unique}
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(iterations):
        pick = rng.integers(0, len(unique), len(unique))
        rows = np.concatenate([index[unique[j]] for j in pick])
        if len(np.unique(labels[rows])) < 2:
            continue
        draws.append(auc(scores_a[rows], labels[rows]) - auc(scores_b[rows], labels[rows]))
    if len(draws) < 50:
        return (float("nan"), float("nan"))
    return (float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5)))


#: A round with 12 crossings contributes 12 rows to a pooled AUC, so choppy rounds dominate it.
#: This is the same defect that flipped the sign of the taker-surplus estimate in test 164,
#: where equal-round and equal-day weighting disagreed and the pooled figure was the misleading
#: one. The correction is the same: report every weighting, and declare which governs.
ROUND_EQUAL_DRAWS = 400


def round_equal_auc(scores, labels, rounds, draws: int = ROUND_EQUAL_DRAWS,
                    seed: int = 181) -> tuple[float, float]:
    """AUC when each ROUND contributes exactly one crossing, averaged over `draws` samples.

    Returns (mean, standard deviation across draws). One crossing per round is the honest unit:
    crossings inside a round are the same market state observed repeatedly, not independent
    opportunities. The spread across draws says how much the pooled number depended on WHICH
    crossing of a round was counted."""
    rounds = np.asarray(rounds)
    order = np.argsort(rounds, kind="mergesort")
    grouped: dict = {}
    for idx in order:
        grouped.setdefault(rounds[idx], []).append(idx)
    buckets = [np.array(v) for v in grouped.values()]
    rng = np.random.default_rng(seed)
    values = []
    for _ in range(draws):
        picked = np.array([b[rng.integers(0, len(b))] for b in buckets])
        y = labels[picked]
        if len(np.unique(y)) < 2:
            continue
        values.append(auc(scores[picked], y))
    if not values:
        return (float("nan"), float("nan"))
    return (float(np.mean(values)), float(np.std(values)))


def round_equal_diff_ci(scores_a, scores_b, labels, rounds, days,
                        iterations: int = 800, seed: int = 191) -> tuple:
    """CI on the round-equal AUC difference, resampling DAYS and one crossing per round.

    Both arms are evaluated on the SAME sampled crossings, so the comparison stays paired -
    weighting one arm differently from the other would not be a correction, it would be a
    different question."""
    rounds = np.asarray(rounds)
    unique_days = np.unique(days)
    if len(unique_days) < 2:
        return (float("nan"), float("nan"))
    by_day: dict = {}
    for idx in range(len(rounds)):
        by_day.setdefault(days[idx], {}).setdefault(rounds[idx], []).append(idx)
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(iterations):
        pick = rng.integers(0, len(unique_days), len(unique_days))
        chosen = []
        for j in pick:
            for _round, idxs in by_day[unique_days[j]].items():
                chosen.append(idxs[rng.integers(0, len(idxs))])
        chosen = np.array(chosen)
        y = labels[chosen]
        if len(np.unique(y)) < 2:
            continue
        draws.append(auc(scores_a[chosen], y) - auc(scores_b[chosen], y))
    if len(draws) < 50:
        return (float("nan"), float("nan"))
    return (float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5)))


def verdict_for(candidate: float, incumbent: float, floor_hi: float, ci: tuple) -> tuple:
    gain = candidate - incumbent
    if not np.isfinite(candidate) or candidate <= floor_hi:
        return ("CROSSING_NOT_PREDICTABLE",
                f"AUC {candidate:.4f} does not exceed the null floor {floor_hi:.4f}")
    if gain >= MATERIAL_AUC and np.isfinite(ci[0]) and ci[0] > 0:
        return ("CROSSING_HEAD_ADDS",
                f"AUC {candidate:.4f} beats the clock baseline {incumbent:.4f} by "
                f"{gain:+.4f}, CI excludes zero")
    return ("CROSSING_IS_TIME_REMAINING",
            f"gain over the clock is {gain:+.4f} AUC "
            f"(CI [{ci[0]:+.4f}, {ci[1]:+.4f}]) - below the declared {MATERIAL_AUC:.2f} bar "
            f"or not distinguishable from zero")


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    check(len(TARGETS) == 3, "three targets - the 5s/15s horizons are excluded as unresolvable")
    check(not any(t.startswith("ever_reverted") for t in TARGETS),
          "ever_reverted_* is NOT trained: no v1 equivalent exists, so it would be "
          "present for recent rows only and would change the target mid-sample")
    check(BASELINE_FEATURE in ROUND_FEATURES,
          "the incumbent feature is inside the candidate set, so the comparison is nested")
    check(MATERIAL_AUC == 0.02, "the declared materiality bar is in force")
    check(len(FEATURES) == 14, "the frozen feature set is 14 columns")

    # THE CAUSAL JOIN. A crossing at 12:30:45 must use the 12:29 bar, which closed at 12:30:00.
    minute = 1_785_000_000_000 // BAR_MS * BAR_MS
    got = causal_bar_ts(np.array([minute + 45_000], dtype="int64"))[0]
    check(got == minute - BAR_MS,
          "a mid-minute crossing uses the PREVIOUS bar, not the one containing it")
    check(got + BAR_MS <= minute + 45_000,
          "the joined bar had CLOSED before the crossing - the defining property")
    check(causal_bar_ts(np.array([minute], dtype="int64"))[0] == minute - BAR_MS,
          "on an exact boundary the just-closed bar is used")
    leaky = (minute + 45_000) // BAR_MS * BAR_MS
    check(leaky + BAR_MS > minute + 45_000,
          "the OLD containing-bar rule is demonstrably non-causal - the guard can fail")

    rng = np.random.default_rng(0)
    n = 3000
    days = np.repeat(np.arange(30), 100)
    labels = rng.integers(0, 2, n)
    strong = labels + rng.normal(0, 0.5, n)
    weak = labels + rng.normal(0, 3.0, n)
    lo, hi = day_block_diff_ci(strong, weak, labels, days)
    check(np.isfinite(lo) and lo > 0, "a real AUC advantage yields a difference CI above zero")
    lo2, hi2 = day_block_diff_ci(weak, weak, labels, days)
    check(abs(lo2) < 1e-9 and abs(hi2) < 1e-9,
          "a model against ITSELF has a zero-width difference interval")

    kind, _ = verdict_for(0.70, 0.60, 0.52, (0.05, 0.15))
    check(kind == "CROSSING_HEAD_ADDS", "a large significant gain over the clock passes")
    kind, _ = verdict_for(0.61, 0.60, 0.52, (0.002, 0.02))
    check(kind == "CROSSING_IS_TIME_REMAINING",
          "a significant but immaterial gain restates the clock")
    kind, _ = verdict_for(0.70, 0.60, 0.52, (-0.02, 0.20))
    check(kind == "CROSSING_IS_TIME_REMAINING", "a gain whose CI spans zero does not pass")
    kind, _ = verdict_for(0.51, 0.50, 0.52, (0.0, 0.02))
    check(kind == "CROSSING_NOT_PREDICTABLE", "inside the null floor is NOT predictable")

    print(f"\nCROSSING HEADS SELFTEST: PASS ({checks} checks)")
    return 0


def run() -> int:
    if not CROSSING_DB.is_file():
        print(f"missing {CROSSING_DB} - run polymarket_crossing_recorder.py --backfill")
        return 1
    frame = load()
    import datetime as dt
    import lightgbm as lgb
    fmt = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d")

    days = np.sort(frame["day"].unique())
    split_day = days[int(len(days) * TRAIN_FRACTION)]
    print("=" * 100)
    print(f"CROSSING HEADS V1 - protocol {PROTOCOL} (frozen before training)")
    print("=" * 100)
    print(f"  {len(frame):,} labelled crossings over {frame.round_id.nunique():,} rounds   "
          f"{fmt(frame.crossing_ts.min())} -> {fmt(frame.crossing_ts.max())}")
    print(f"  split by DAY at {fmt(split_day * 86_400_000)}   "
          f"market features joined from the last CLOSED 1m bar")
    print(f"  incumbent baseline: '{BASELINE_FEATURE}' alone   "
          f"materiality {MATERIAL_AUC:.2f} AUC")
    coverage = frame[list(MARKET_FEATURES)].notna().all(axis=1).mean()
    print(f"  market-feature coverage {coverage:.1%}")

    params = dict(n_estimators=200, learning_rate=0.05, num_leaves=15,
                  min_child_samples=100, verbose=-1, random_state=0)
    for target in TARGETS:
        usable = frame.dropna(subset=[target] + list(FEATURES)).copy()
        if usable.empty:
            print(f"\n  --- {target}: no usable rows")
            continue
        train = usable[usable["day"] < split_day]
        test = usable[usable["day"] >= split_day]
        ytr = train[target].astype(int).to_numpy()
        yte = test[target].astype(int).to_numpy()
        print(f"\n  --- {target}   train {len(train):,} / test {len(test):,}   "
              f"base rate {yte.mean():.1%}")
        if len(train) < 200 or len(test) < 100 or len(np.unique(ytr)) < 2:
            print("      insufficient data for this target")
            continue

        incumbent = lgb.LGBMClassifier(**params).fit(
            train[[BASELINE_FEATURE]].to_numpy(float), ytr)
        clock = incumbent.predict_proba(test[[BASELINE_FEATURE]].to_numpy(float))[:, 1]
        candidate = lgb.LGBMClassifier(**params).fit(
            train[list(FEATURES)].to_numpy(float), ytr)
        full = candidate.predict_proba(test[list(FEATURES)].to_numpy(float))[:, 1]

        auc_clock, auc_full = auc(clock, yte), auc(full, yte)
        test_days = test["day"].to_numpy()
        ci = day_block_diff_ci(full, clock, yte, test_days)
        lo, mid, hi = null_floor(full, yte, test_days, replications=200)

        print(f"      {'BASELINE_CONSTANT':<24}{yte.mean():>10.4f}  (base rate)")
        print(f"      {'BASELINE_TIME (clock)':<24}{auc_clock:>10.4f}")
        print(f"      {'CANDIDATE (14 features)':<24}{auc_full:>10.4f}")
        print(f"      null floor 95% [{lo:.4f}, {hi:.4f}]   "
              f"gain over clock {auc_full - auc_clock:+.4f}   "
              f"CI [{ci[0]:+.4f}, {ci[1]:+.4f}]")
        # ROUND-EQUAL WEIGHTING. Both arms on the same sampled crossings, so the comparison
        # stays paired. This governs; the pooled figure above is reported for continuity.
        test_rounds = test["round_id"].to_numpy()
        re_full, re_full_sd = round_equal_auc(full, yte, test_rounds)
        re_clock, re_clock_sd = round_equal_auc(clock, yte, test_rounds)
        re_ci = round_equal_diff_ci(full, clock, yte, test_rounds, test_days)
        per_round = len(test) / max(1, len(np.unique(test_rounds)))
        print(f"      -- round-equal ({per_round:.2f} crossings/round pooled) --")
        print(f"      {'BASELINE_TIME (clock)':<24}{re_clock:>10.4f}  (sd {re_clock_sd:.4f})")
        print(f"      {'CANDIDATE':<24}{re_full:>10.4f}  (sd {re_full_sd:.4f})")
        print(f"      gain over clock {re_full - re_clock:+.4f}   "
              f"CI [{re_ci[0]:+.4f}, {re_ci[1]:+.4f}]")

        verdict, reason = verdict_for(re_full, re_clock, hi, re_ci)
        pooled_verdict, _ = verdict_for(auc_full, auc_clock, hi, ci)
        print(f"      VERDICT (round-equal, governing): {verdict}")
        print(f"      {reason}")
        if pooled_verdict != verdict:
            print(f"      NOTE: the pooled weighting would have said {pooled_verdict} - "
                  f"the weighting changes the verdict")

    print()
    print("  A crossing probability is an INPUT to a decision, not a decision. Every action")
    print("  lane measured in this repository is currently closed on cost, so a well")
    print("  calibrated head here does not by itself create a tradable opportunity.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    raise SystemExit(selftest() if args.selftest else run())
