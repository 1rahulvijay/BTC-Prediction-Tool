"""Inside the gated windows, does direction produce post-cost VALUE - and better than untargeted?

PROTOCOL
    docs/active/PREREG_CONDITIONAL_DIRECTION_V1.md, sha256 dd5c7a75..., frozen before any result.

WHY VALUE AND NOT ACCURACY
    Phase 5 measured direction AUC 0.87 and magnitude AUC 0.58, and no fixed rule beat WAIT
    after costs. Sign predictability that does not convert into profit is this repository's most
    repeated finding, so AUC here is a diagnostic and the verdict is realised basis points.

    GATED_RANDOM is the control that matters. If the gated windows simply drift, a random side
    profits too, and the direction model would be taking credit for a property of the window.

    python research/conditional_direction_v1.py --selftest
    python research/conditional_direction_v1.py
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
from tradability_head_v1 import (                                     # noqa: E402
    BINANCE_HURDLE, FEATURES, FORWARD_BARS, MATRIX, PURGE_BARS, TOP_DECILE,
    TRAIN_FRACTION, auc,
)

PROTOCOL = "PREREG_CONDITIONAL_DIRECTION_V1.md"
COST_BPS = 14.0
RANDOM_SEED = 41


def load_frame() -> pd.DataFrame:
    import pyarrow.parquet as pq
    frame = pq.read_table(MATRIX, columns=["ts_ms", "open", "close"] + list(FEATURES)).to_pandas()
    frame = frame.sort_values("ts_ms").drop_duplicates("ts_ms").reset_index(drop=True)
    # Execution: enter at the NEXT bar's open, exit 15 bars later at that bar's open.
    entry = frame["open"].shift(-1)
    exit_ = frame["open"].shift(-1 - FORWARD_BARS)
    frame["fwd_ret_bps"] = (exit_ / entry - 1.0) * 1e4
    frame["fwd_abs_bps"] = frame["fwd_ret_bps"].abs()
    frame["day"] = frame["ts_ms"] // 86_400_000
    return frame.dropna(subset=list(FEATURES) + ["fwd_ret_bps"]).reset_index(drop=True)


def non_overlapping(indices: np.ndarray, horizon: int = FORWARD_BARS) -> np.ndarray:
    """Keep only trades that do not overlap: after an entry, skip `horizon` bars.

    Consecutive selected bars describe the SAME move over a 15-bar horizon. Counting them as
    separate trades would inflate the sample several-fold and make one lucky move look like
    fifteen."""
    kept, last = [], -10**9
    for i in np.sort(indices):
        if i - last > horizon:
            kept.append(i)
            last = i
    return np.array(kept, dtype=int)


def day_block_ci(values: np.ndarray, days: np.ndarray,
                 iterations: int = 2000, seed: int = 53) -> tuple:
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


def paired_diff_ci(a_vals: np.ndarray, a_days: np.ndarray,
                   b_vals: np.ndarray, b_days: np.ndarray,
                   iterations: int = 2000, seed: int = 59) -> tuple:
    """CI on the difference of two arm means, resampling the same DAYS for both arms.

    Resampling days jointly preserves the correlation between arms that trade the same market;
    bootstrapping them independently would widen the interval and hide a real difference."""
    unique = np.unique(np.concatenate([a_days, b_days]))
    if len(unique) < 2:
        return (float("nan"), float("nan"))
    a_index = {d: a_vals[a_days == d] for d in unique}
    b_index = {d: b_vals[b_days == d] for d in unique}
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(iterations):
        pick = rng.integers(0, len(unique), len(unique))
        a = np.concatenate([a_index[unique[j]] for j in pick])
        b = np.concatenate([b_index[unique[j]] for j in pick])
        if len(a) and len(b):
            draws.append(a.mean() - b.mean())
    if len(draws) < 50:
        return (float("nan"), float("nan"))
    return (float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5)))


def arm(name: str, rows: np.ndarray, sides: np.ndarray, frame: pd.DataFrame) -> dict:
    if len(rows) == 0:
        return {"name": name, "n": 0, "mean": float("nan"), "ci": (np.nan, np.nan),
                "hit": float("nan"), "values": np.array([]), "days": np.array([])}
    ret = frame["fwd_ret_bps"].to_numpy(float)[rows]
    net = sides * ret - COST_BPS
    days = frame["day"].to_numpy()[rows]
    return {"name": name, "n": len(rows), "mean": float(net.mean()),
            "ci": day_block_ci(net, days),
            "hit": float((sides * ret > 0).mean() * 100),
            "values": net, "days": days}


def verdict_for(gated: dict, uncond: dict, random_arm: dict,
                vs_uncond: tuple, vs_random: tuple,
                gated_auc: float, uncond_auc: float) -> tuple[str, str]:
    if (abs(gated_auc - 0.5) < 0.02) and (abs(uncond_auc - 0.5) < 0.02):
        return ("DIRECTION_NOT_PREDICTABLE",
                f"direction AUC is {gated_auc:.3f} gated and {uncond_auc:.3f} unconditional - "
                f"both within 0.02 of a coin flip")
    beats_random = np.isfinite(vs_random[0]) and vs_random[0] > 0
    profitable = np.isfinite(gated["ci"][0]) and gated["ci"][0] > 0
    if profitable and beats_random:
        return ("GATED_DIRECTION_PROFITABLE",
                f"gated net {gated['mean']:+.2f} bps with CI lower bound "
                f"{gated['ci'][0]:+.2f} > 0, and it beats a random side in the same windows")
    if profitable and not beats_random:
        return ("GATING_ADDS_NOTHING",
                "the gated arm is positive but does NOT beat a random side in the same "
                "windows - the window is doing the work, not the direction model")
    beats_uncond = np.isfinite(vs_uncond[0]) and vs_uncond[0] > 0
    if beats_uncond:
        return ("GATING_HELPS_BUT_UNPROFITABLE",
                f"gating beats unconditional trading, but the gated arm's own CI "
                f"[{gated['ci'][0]:+.2f}, {gated['ci'][1]:+.2f}] does not exclude zero")
    return ("GATING_ADDS_NOTHING", "no CI on any difference excludes zero")


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    check(COST_BPS == 14.0, "the declared cost is in force and is not negotiable downward")

    idx = np.arange(0, 100)
    kept = non_overlapping(idx)
    check(len(kept) == 7 and kept[0] == 0 and kept[1] == 16,
          "overlapping windows collapse to non-overlapping trades 16 bars apart")
    check(len(non_overlapping(np.array([0, 5, 10]))) == 1,
          "three bars inside one horizon become ONE trade, not three")
    check(len(non_overlapping(np.array([0, 20, 40]))) == 3,
          "genuinely separated entries are all kept")

    rng = np.random.default_rng(0)
    n = 6000
    ret = rng.normal(0, 40, n)
    frame = pd.DataFrame({"fwd_ret_bps": ret, "day": np.repeat(np.arange(n // 60), 60)[:n]})
    rows = np.arange(0, n, 20)

    perfect = arm("perfect", rows, np.sign(ret[rows]), frame)
    check(perfect["mean"] > 0, "a perfect-foresight side is profitable after cost")
    check(perfect["hit"] == 100.0, "...and its hit rate is 100%")
    wrong = arm("wrong", rows, -np.sign(ret[rows]), frame)
    check(wrong["mean"] < 0, "an always-wrong side loses")
    coin = arm("coin", rows, rng.choice([-1, 1], len(rows)), frame)
    check(np.isfinite(coin["ci"][0]) and coin["ci"][0] < 0 < coin["ci"][1] or
          coin["mean"] < 0,
          "a random side is not profitable after cost")

    # COST IS ALWAYS CHARGED: a zero-return market must lose exactly the cost.
    flat = pd.DataFrame({"fwd_ret_bps": np.zeros(n),
                         "day": np.repeat(np.arange(n // 60), 60)[:n]})
    zero = arm("flat", rows, np.ones(len(rows)), flat)
    check(abs(zero["mean"] + COST_BPS) < 1e-9,
          "in a market that never moves, every trade loses exactly the cost")

    lo, hi = paired_diff_ci(perfect["values"], perfect["days"], wrong["values"], wrong["days"])
    check(np.isfinite(lo) and lo > 0, "a real difference between arms is detected")
    lo2, hi2 = paired_diff_ci(coin["values"], coin["days"], coin["values"], coin["days"])
    check(abs(lo2) < 1e-9 and abs(hi2) < 1e-9,
          "an arm against ITSELF has a zero-width difference interval")

    good = {"mean": 5.0, "ci": (1.0, 9.0)}
    kind, _ = verdict_for(good, {"mean": 0.0}, {"mean": 0.0}, (1.0, 3.0), (1.0, 3.0),
                          0.60, 0.55)
    check(kind == "GATED_DIRECTION_PROFITABLE", "profitable AND beats random passes")
    kind, _ = verdict_for(good, {"mean": 0.0}, {"mean": 0.0}, (1.0, 3.0), (-1.0, 3.0),
                          0.60, 0.55)
    check(kind == "GATING_ADDS_NOTHING",
          "positive but NOT beating a random side in the same windows does not pass")
    kind, _ = verdict_for({"mean": -2.0, "ci": (-6.0, 2.0)}, {"mean": -8.0}, {"mean": -3.0},
                          (1.0, 5.0), (0.5, 2.0), 0.60, 0.55)
    check(kind == "GATING_HELPS_BUT_UNPROFITABLE",
          "a gate that helps but does not pay is reported as exactly that")
    kind, _ = verdict_for({"mean": 0.0, "ci": (-1.0, 1.0)}, {"mean": 0.0}, {"mean": 0.0},
                          (-1.0, 1.0), (-1.0, 1.0), 0.505, 0.501)
    check(kind == "DIRECTION_NOT_PREDICTABLE", "coin-flip AUC in both arms is reported as such")

    print(f"\nCONDITIONAL DIRECTION SELFTEST: PASS ({checks} checks)")
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
    import lightgbm as lgb
    fmt = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d")
    print("=" * 104)
    print(f"CONDITIONAL DIRECTION V1 - protocol {PROTOCOL} (frozen before any result)")
    print("=" * 104)
    print(f"  train -> {fmt(frame.ts_ms.iloc[split])}   purge {PURGE_BARS}   "
          f"test {len(test):,} bars -> {fmt(frame.ts_ms.iloc[-1])}")
    print(f"  cost {COST_BPS:.0f} bps round trip   horizon {FORWARD_BARS} bars   "
          f"gate = top {TOP_DECILE:.0%} of predicted movement   trades do NOT overlap")

    Xtr = train[list(FEATURES)].to_numpy(float)
    Xte = test[list(FEATURES)].to_numpy(float)
    params = dict(n_estimators=300, learning_rate=0.05, num_leaves=31,
                  min_child_samples=200, verbose=-1, random_state=0)

    # Gate, trained on train only.
    gate = lgb.LGBMClassifier(**params).fit(
        Xtr, (train["fwd_abs_bps"].to_numpy(float) > BINANCE_HURDLE).astype(int))
    gate_scores = gate.predict_proba(Xte)[:, 1]
    k = int(len(test) * TOP_DECILE)
    gated_rows = np.sort(np.argsort(-gate_scores)[:k])

    # Direction, trained on train only.
    direction = lgb.LGBMClassifier(**params).fit(
        Xtr, (train["fwd_ret_bps"].to_numpy(float) > 0).astype(int))
    up_prob = direction.predict_proba(Xte)[:, 1]
    sides_all = np.where(up_prob >= 0.5, 1, -1)

    labels = (test["fwd_ret_bps"].to_numpy(float) > 0).astype(int)
    gated_auc = auc(up_prob[gated_rows], labels[gated_rows])
    uncond_auc = auc(up_prob, labels)

    gated_trades = non_overlapping(gated_rows)
    uncond_trades = non_overlapping(np.arange(len(test)))
    rng = np.random.default_rng(RANDOM_SEED)

    arms = [
        arm("GATED_DIRECTION", gated_trades, sides_all[gated_trades], test),
        arm("UNCONDITIONAL", uncond_trades, sides_all[uncond_trades], test),
        arm("GATED_RANDOM", gated_trades, rng.choice([-1, 1], len(gated_trades)), test),
    ]
    gated, uncond, random_arm = arms

    print()
    print(f"  {'arm':<20}{'trades':>8}{'hit%':>8}{'net bps':>10}   day-block 95% CI")
    print("  " + "-" * 74)
    for a in arms:
        ci = (f"[{a['ci'][0]:+7.2f}, {a['ci'][1]:+7.2f}]"
              if np.isfinite(a["ci"][0]) else "  (insufficient)")
        print(f"  {a['name']:<20}{a['n']:>8,}{a['hit']:>8.1f}{a['mean']:>10.2f}   {ci}")
    print(f"  {'ALWAYS_FLAT':<20}{0:>8}{'-':>8}{0.0:>10.2f}   (zero by construction)")

    vs_uncond = paired_diff_ci(gated["values"], gated["days"],
                               uncond["values"], uncond["days"])
    vs_random = paired_diff_ci(gated["values"], gated["days"],
                               random_arm["values"], random_arm["days"])
    print()
    print(f"  gated - unconditional : {gated['mean'] - uncond['mean']:+7.2f} bps   "
          f"CI [{vs_uncond[0]:+7.2f}, {vs_uncond[1]:+7.2f}]")
    print(f"  gated - random side   : {gated['mean'] - random_arm['mean']:+7.2f} bps   "
          f"CI [{vs_random[0]:+7.2f}, {vs_random[1]:+7.2f}]")
    print(f"  direction AUC         : {gated_auc:.3f} gated / {uncond_auc:.3f} unconditional "
          f"(DIAGNOSTIC ONLY)")

    verdict, reason = verdict_for(gated, uncond, random_arm, vs_uncond, vs_random,
                                  gated_auc, uncond_auc)
    print()
    print(f"  VERDICT: {verdict}")
    print(f"  {reason}")
    print()
    if verdict != "GATED_DIRECTION_PROFITABLE":
        print("  The lane does not pay after costs on this window. Accuracy figures above are")
        print("  diagnostic and do not change that: being right more often than not is not the")
        print("  same as being right on the moves that are large enough to matter.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    raise SystemExit(selftest() if args.selftest else run())
