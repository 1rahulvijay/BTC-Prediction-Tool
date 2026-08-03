"""Once capital is committed, is there value in when it is released?

PROTOCOL
    docs/active/PREREG_EXIT_TIMING_V1.md, sha256 09ee0bf3..., frozen before any result.

ENTRIES ARE RANDOM, DELIBERATELY
    CONDITIONAL_DIRECTION_V1 measured entry direction at AUC 0.498. Choosing entries with a
    model here would confound exit skill with entry skill, and entry skill is already known to
    be absent. Random entry isolates the only question asked: does exit TIMING carry value?

    A consequence declared in advance: a random entry has negative expected value by the cost,
    so the candidate is not expected to be profitable in absolute terms. Profitability is not
    the verdict. Beating HOLD_TO_HORIZON and RANDOM_EXIT is.

RANDOM_EXIT IS THE CONTROL THAT DECIDES
    Shortening average holding time changes the risk profile, and that alone moves the mean
    without any timing skill. Matched on count, RANDOM_EXIT detects exactly that.

    python research/exit_timing_v1.py --selftest
    python research/exit_timing_v1.py
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
from tradability_head_v1 import FEATURES, MATRIX, PURGE_BARS, TRAIN_FRACTION  # noqa: E402

PROTOCOL = "PREREG_EXIT_TIMING_V1.md"
MAX_HOLD = 240
COST_BPS = 14.0
N_ENTRIES = 2000
ENTRY_SEED = 71
TRAIL_BPS = 50.0
POSITION_FEATURES = ("unrealised_bps", "bars_held", "side", "mfe_bps", "mae_bps")


def load_frame() -> pd.DataFrame:
    import pyarrow.parquet as pq
    frame = pq.read_table(MATRIX, columns=["ts_ms", "open", "close"] + list(FEATURES)).to_pandas()
    frame = frame.sort_values("ts_ms").drop_duplicates("ts_ms").reset_index(drop=True)
    frame["day"] = frame["ts_ms"] // 86_400_000
    return frame.dropna(subset=list(FEATURES)).reset_index(drop=True)


def sample_entries(n_bars: int, count: int, seed: int) -> list[tuple[int, int]]:
    """Non-overlapping random (bar, side) pairs. A position closes before the next opens."""
    rng = np.random.default_rng(seed)
    usable = n_bars - MAX_HOLD - 2
    if usable <= 0:
        return []
    picked, last = [], -10**9
    for bar in np.sort(rng.choice(usable, size=min(count * 4, usable), replace=False)):
        if bar - last > MAX_HOLD:
            picked.append((int(bar), int(rng.choice([-1, 1]))))
            last = bar
            if len(picked) >= count:
                break
    return picked


def position_paths(frame: pd.DataFrame, entries) -> list[dict]:
    """Per-bar unrealised path for each position. Everything here is arithmetic, not a policy."""
    opens = frame["open"].to_numpy(float)
    days = frame["day"].to_numpy()
    out = []
    for bar, side in entries:
        entry_price = opens[bar + 1]
        window = opens[bar + 2: bar + 2 + MAX_HOLD]
        if len(window) < MAX_HOLD:
            continue
        unreal = side * (window / entry_price - 1.0) * 1e4
        running_max = np.maximum.accumulate(unreal)
        running_min = np.minimum.accumulate(unreal)
        out.append({"bar": bar, "side": side, "day": int(days[bar]), "unreal": unreal,
                    "mfe": running_max, "mae": running_min})
    return out


def build_training_rows(frame: pd.DataFrame, paths: list[dict]) -> tuple:
    """One row per open-position bar. Label may see the future; features may not."""
    market = frame[list(FEATURES)].to_numpy(float)
    X, y = [], []
    for p in paths:
        final = p["unreal"][-1]
        for t in range(MAX_HOLD):
            row_index = p["bar"] + 2 + t
            if row_index >= len(market):
                break
            X.append(np.concatenate([market[row_index],
                                     [p["unreal"][t], float(t), float(p["side"]),
                                      p["mfe"][t], p["mae"][t]]]))
            y.append(1 if p["unreal"][t] > final else 0)
    return np.array(X, dtype=float), np.array(y, dtype=int)


def apply_policy(frame: pd.DataFrame, paths: list[dict], model) -> np.ndarray:
    """Exit at the first bar where the model says exiting now beats holding."""
    market = frame[list(FEATURES)].to_numpy(float)
    results = []
    for p in paths:
        rows = []
        for t in range(MAX_HOLD):
            row_index = p["bar"] + 2 + t
            if row_index >= len(market):
                break
            rows.append(np.concatenate([market[row_index],
                                        [p["unreal"][t], float(t), float(p["side"]),
                                         p["mfe"][t], p["mae"][t]]]))
        if not rows:
            results.append(p["unreal"][-1])
            continue
        probs = model.predict_proba(np.array(rows, dtype=float))[:, 1]
        hits = np.flatnonzero(probs >= 0.5)
        results.append(float(p["unreal"][hits[0]] if len(hits) else p["unreal"][-1]))
    return np.array(results)


def trailing_stop(paths: list[dict], trail_bps: float = TRAIL_BPS) -> np.ndarray:
    out = []
    for p in paths:
        drop = p["mfe"] - p["unreal"]
        hits = np.flatnonzero(drop >= trail_bps)
        out.append(float(p["unreal"][hits[0]] if len(hits) else p["unreal"][-1]))
    return np.array(out)


def day_block_ci(values: np.ndarray, days: np.ndarray,
                 iterations: int = 2000, seed: int = 83) -> tuple:
    unique = np.unique(days)
    if len(unique) < 2 or len(values) == 0:
        return (float("nan"), float("nan"))
    groups = [values[days == d] for d in unique]
    rng = np.random.default_rng(seed)
    means = np.empty(iterations)
    for k in range(iterations):
        pick = rng.integers(0, len(groups), len(groups))
        means[k] = np.concatenate([groups[j] for j in pick]).mean()
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def paired_diff_ci(a: np.ndarray, b: np.ndarray, days: np.ndarray,
                   iterations: int = 2000, seed: int = 89) -> tuple:
    """Both arms hold the SAME positions, so the difference is paired per position."""
    diff = a - b
    return day_block_ci(diff, days, iterations, seed)


def verdict_for(cand, hold, rand, vs_hold, vs_rand) -> tuple[str, str]:
    beats_hold = np.isfinite(vs_hold[0]) and vs_hold[0] > 0
    beats_rand = np.isfinite(vs_rand[0]) and vs_rand[0] > 0
    if beats_hold and beats_rand:
        return ("EXIT_TIMING_ADDS",
                f"the learned policy beats holding by {cand - hold:+.2f} bps and a random exit "
                f"by {cand - rand:+.2f} bps, both intervals clear of zero")
    if beats_hold and not beats_rand:
        return ("EXIT_TIMING_IS_RANDOM",
                "exiting early beats holding, but the learned policy does NOT beat a random "
                "exit - the value is in the shorter horizon, not in choosing when")
    return ("EXIT_TIMING_ADDS_NOTHING",
            f"the learned policy does not beat holding "
            f"(difference CI [{vs_hold[0]:+.2f}, {vs_hold[1]:+.2f}])")


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    check(COST_BPS == 14.0 and MAX_HOLD == 240 and N_ENTRIES == 2000,
          "the protocol's declared constants are in force")
    check(len(POSITION_FEATURES) == 5, "five position-state features accompany the market set")

    entries = sample_entries(20_000, 50, ENTRY_SEED)
    bars = [b for b, _ in entries]
    check(all(np.diff(bars) > MAX_HOLD),
          "entries never overlap - one position closes before the next opens")
    check(set(s for _, s in entries) <= {-1, 1}, "sides are only long or short")
    check(sample_entries(20_000, 50, ENTRY_SEED) == entries,
          "the entry sample is reproducible from its declared seed")

    n = 5000
    rng = np.random.default_rng(0)
    walk = 100 * np.exp(np.cumsum(rng.normal(0, 0.0008, n)))
    frame = pd.DataFrame({"ts_ms": np.arange(n, dtype="int64") * 60_000,
                          "open": walk, "close": walk, "day": np.arange(n) // 1440})
    for f in FEATURES:
        frame[f] = rng.normal(size=n)
    paths = position_paths(frame, sample_entries(n, 12, ENTRY_SEED))
    check(len(paths) > 0, "positions are constructed")
    check(all(len(p["unreal"]) == MAX_HOLD for p in paths),
          "every position path runs the full maximum hold")
    check(all(p["mfe"][0] == p["unreal"][0] for p in paths),
          "MFE starts at the first unrealised value, not at zero")
    check(all((np.diff(p["mfe"]) >= -1e-9).all() for p in paths),
          "MFE is non-decreasing by construction")
    check(all((np.diff(p["mae"]) <= 1e-9).all() for p in paths),
          "MAE is non-increasing by construction")

    # The ORACLE is a ceiling: no arm may exceed it.
    oracle = np.array([p["unreal"].max() for p in paths])
    hold = np.array([p["unreal"][-1] for p in paths])
    trail = trailing_stop(paths)
    check((oracle >= hold - 1e-9).all(), "the oracle is never worse than holding")
    check((oracle >= trail - 1e-9).all(), "the oracle is never worse than a trailing stop")

    X, y = build_training_rows(frame, paths)
    check(len(X) == len(paths) * MAX_HOLD, "one training row per open-position bar")
    check(X.shape[1] == len(FEATURES) + len(POSITION_FEATURES),
          "features are the market set plus position state, and nothing else")
    check(set(np.unique(y)) <= {0, 1}, "the label is binary")

    # A position that only ever rises must never be labelled 'exit now beats holding'.
    rising = [{"bar": 0, "side": 1, "day": 0,
               "unreal": np.arange(MAX_HOLD, dtype=float),
               "mfe": np.arange(MAX_HOLD, dtype=float),
               "mae": np.zeros(MAX_HOLD)}]
    _, y_rise = build_training_rows(frame, rising)
    check(y_rise.sum() == 0,
          "in a monotonically rising position, exiting early NEVER beats holding")

    diff_ci = paired_diff_ci(np.array([2.0, 2.0, 2.0, 2.0]), np.array([1.0, 1.0, 1.0, 1.0]),
                             np.array([1, 1, 2, 2]))
    check(abs(diff_ci[0] - 1.0) < 1e-9, "a constant paired advantage yields a tight interval")

    kind, _ = verdict_for(5.0, 0.0, 0.0, (1.0, 3.0), (1.0, 3.0))
    check(kind == "EXIT_TIMING_ADDS", "beating both baselines passes")
    kind, _ = verdict_for(5.0, 0.0, 4.0, (1.0, 3.0), (-1.0, 2.0))
    check(kind == "EXIT_TIMING_IS_RANDOM",
          "beating holding but NOT a random exit is reported as the horizon, not the policy")
    kind, _ = verdict_for(0.0, 0.0, 0.0, (-1.0, 1.0), (-1.0, 1.0))
    check(kind == "EXIT_TIMING_ADDS_NOTHING", "beating nothing is reported as nothing")

    print(f"\nEXIT TIMING SELFTEST: PASS ({checks} checks)")
    return 0


def run() -> int:
    if not MATRIX.is_file():
        print(f"missing {MATRIX}")
        return 1
    frame = load_frame()
    split = int(len(frame) * TRAIN_FRACTION)
    train = frame.iloc[:split].reset_index(drop=True)
    test = frame.iloc[split + PURGE_BARS:].reset_index(drop=True)

    import datetime as dt
    import lightgbm as lgb
    fmt = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d")
    print("=" * 104)
    print(f"EXIT TIMING V1 - protocol {PROTOCOL} (frozen before any result)")
    print("=" * 104)
    print(f"  train -> {fmt(frame.ts_ms.iloc[split])}   purge {PURGE_BARS}   "
          f"test -> {fmt(frame.ts_ms.iloc[-1])}")
    print(f"  entries RANDOM (seed {ENTRY_SEED})   max hold {MAX_HOLD} bars   "
          f"cost {COST_BPS:.0f} bps, identical in every arm")

    train_paths = position_paths(train, sample_entries(len(train), N_ENTRIES * 2, ENTRY_SEED))
    test_paths = position_paths(test, sample_entries(len(test), N_ENTRIES, ENTRY_SEED + 1))
    print(f"  positions: {len(train_paths):,} train / {len(test_paths):,} test")

    Xtr, ytr = build_training_rows(train, train_paths)
    print(f"  training rows {len(Xtr):,}   'exit now beats holding' base rate {ytr.mean():.1%}")
    model = lgb.LGBMClassifier(n_estimators=300, learning_rate=0.05, num_leaves=31,
                               min_child_samples=200, verbose=-1,
                               random_state=0).fit(Xtr, ytr)

    days = np.array([p["day"] for p in test_paths])
    hold = np.array([p["unreal"][-1] for p in test_paths]) - COST_BPS
    candidate = apply_policy(test, test_paths, model) - COST_BPS
    oracle = np.array([p["unreal"].max() for p in test_paths]) - COST_BPS
    trail = trailing_stop(test_paths) - COST_BPS
    rng = np.random.default_rng(97)
    random_exit = np.array([p["unreal"][rng.integers(0, MAX_HOLD)]
                            for p in test_paths]) - COST_BPS

    arms = [("CANDIDATE", candidate), ("HOLD_TO_HORIZON", hold),
            ("RANDOM_EXIT", random_exit), ("TRAILING_STOP", trail),
            ("ORACLE_BEST_EXIT", oracle)]
    print()
    print(f"  {'arm':<22}{'net bps':>10}   day-block 95% CI")
    print("  " + "-" * 60)
    for name, values in arms:
        lo, hi = day_block_ci(values, days)
        ci = f"[{lo:+8.2f}, {hi:+8.2f}]" if np.isfinite(lo) else "  (insufficient)"
        flag = "   CEILING - requires hindsight" if name == "ORACLE_BEST_EXIT" else ""
        print(f"  {name:<22}{values.mean():>10.2f}   {ci}{flag}")

    vs_hold = paired_diff_ci(candidate, hold, days)
    vs_rand = paired_diff_ci(candidate, random_exit, days)
    print()
    print(f"  candidate - hold        {candidate.mean() - hold.mean():+8.2f} bps   "
          f"CI [{vs_hold[0]:+8.2f}, {vs_hold[1]:+8.2f}]")
    print(f"  candidate - random exit {candidate.mean() - random_exit.mean():+8.2f} bps   "
          f"CI [{vs_rand[0]:+8.2f}, {vs_rand[1]:+8.2f}]")
    captured = ((candidate.mean() - hold.mean()) / (oracle.mean() - hold.mean()) * 100
                if oracle.mean() != hold.mean() else float("nan"))
    print(f"  ceiling                 {oracle.mean() - hold.mean():+8.2f} bps above holding; "
          f"candidate captures {captured:.1f}% of it")

    verdict, reason = verdict_for(candidate.mean(), hold.mean(), random_exit.mean(),
                                  vs_hold, vs_rand)
    print()
    print(f"  VERDICT: {verdict}")
    print(f"  {reason}")
    print()
    print("  Entries were RANDOM by protocol, so absolute levels are negative by the cost and")
    print("  are not the result. ORACLE_BEST_EXIT is a hindsight ceiling and is never")
    print("  achievable; it sizes the opportunity, it does not describe a strategy.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    raise SystemExit(selftest() if args.selftest else run())
