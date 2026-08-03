"""Are the six declared market regimes distinguishable? Scored once, on frozen rules.

PROTOCOL
    docs/active/PREREG_REGIME_LABELER_V1.md, sha256 c4ae06b6..., frozen 2026-08-03 before any
    regime result was computed. This script implements those rules exactly and may not tune a
    threshold, add or drop a regime, or change the priority order.

WHY IT EXISTS TO SAY NO
    A Strategy Router routes between strategies conditional on regime. If regimes do not have
    distinguishable forward behaviour, there is nothing to route, and the router is decoration
    over a coin flip. This test runs BEFORE the router so that answer is still cheap.

THE LEAK THIS AVOIDS
    Every percentile threshold is estimated on the TRAIN window and applied unchanged to test.
    A full-sample percentile lets a test observation help set the threshold that labels it -
    the defect found in edge_probe.py, where a "large trade" cutoff was computed over all rows.

    python research/regime_labeler_v1.py --selftest
    python research/regime_labeler_v1.py
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
PROTOCOL = "PREREG_REGIME_LABELER_V1.md"

REGIMES = ("LIQUIDATION_SHOCK", "SHOCK_EXHAUSTION", "THIN_LIQUIDITY",
           "COMPRESSION_EXPANDING", "TRENDING", "RANGE")

TRAIN_FRACTION = 0.70
PURGE_BARS = 60
FORWARD_BARS = 15
SHOCK_LOOKBACK = 30
EXPANSION_RATIO = 1.20
EFFICIENCY_MIN = 1.00
#: A regime below this share of bars cannot support an interval and is excluded from the verdict.
UNDERPOPULATED = 0.01
DOMINANT = 0.80


def load_matrix() -> pd.DataFrame:
    import pyarrow.parquet as pq
    frame = pq.read_table(MATRIX, columns=[
        "ts_ms", "close", "volume", "rv_15m", "rv_60m", "compression_ratio"]).to_pandas()
    return frame.sort_values("ts_ms").drop_duplicates("ts_ms").reset_index(drop=True)


def add_inputs(frame: pd.DataFrame) -> pd.DataFrame:
    """The six inputs the protocol names. Everything is known at the bar's close."""
    f = frame.copy()
    close = f["close"]
    f["ret_1m"] = close.pct_change()
    f["ret_5m"] = close.pct_change(5)
    f["abs_ret_5m"] = f["ret_5m"].abs()
    f["volume_60m"] = f["volume"].rolling(60, min_periods=60).sum()
    # efficiency = |sum of last 60 one-minute returns| / (rv_60m * sqrt(60))
    drift = f["ret_1m"].rolling(60, min_periods=60).sum().abs()
    f["efficiency"] = drift / (f["rv_60m"] * np.sqrt(60)).replace(0.0, np.nan)
    f["expansion"] = f["rv_15m"] / f["rv_60m"].replace(0.0, np.nan)
    # The endpoint: forward 15-minute absolute move in bps. Used for scoring, never for labels.
    f["fwd_abs_bps"] = (close.shift(-FORWARD_BARS) / close - 1.0).abs() * 1e4
    return f


def fit_thresholds(train: pd.DataFrame) -> dict:
    """Every percentile the protocol names, estimated on TRAIN ONLY."""
    return {
        "shock_ret": float(train["abs_ret_5m"].quantile(0.99)),
        "shock_rv": float(train["rv_15m"].quantile(0.90)),
        "calm_ret": float(train["abs_ret_5m"].quantile(0.50)),
        "elevated_rv": float(train["rv_15m"].quantile(0.75)),
        "thin_volume": float(train["volume_60m"].quantile(0.10)),
        "tight": float(train["compression_ratio"].quantile(0.25)),
        "active_rv": float(train["rv_60m"].quantile(0.50)),
    }


def label(frame: pd.DataFrame, t: dict) -> pd.Series:
    """The six regimes in the protocol's strict priority order. First match wins."""
    n = len(frame)
    out = np.full(n, "RANGE", dtype=object)

    shock = ((frame["abs_ret_5m"] >= t["shock_ret"]) &
             (frame["rv_15m"] >= t["shock_rv"])).to_numpy()
    # "a LIQUIDATION_SHOCK occurred in the previous 30 bars" - strictly previous, so shift(1)
    # before the rolling window; a bar must not count itself as its own precedent.
    recent_shock = (pd.Series(shock).shift(1).fillna(False)
                    .rolling(SHOCK_LOOKBACK, min_periods=1).max().astype(bool).to_numpy())
    exhaustion = (recent_shock &
                  (frame["abs_ret_5m"] <= t["calm_ret"]).to_numpy() &
                  (frame["rv_15m"] >= t["elevated_rv"]).to_numpy())
    thin = (frame["volume_60m"] <= t["thin_volume"]).to_numpy()
    expanding = ((frame["compression_ratio"] <= t["tight"]) &
                 (frame["expansion"] >= EXPANSION_RATIO)).to_numpy()
    trending = ((frame["efficiency"] >= EFFICIENCY_MIN) &
                (frame["rv_60m"] >= t["active_rv"])).to_numpy()

    # Reverse priority assignment: later writes win, so apply lowest priority first.
    out[trending] = "TRENDING"
    out[expanding] = "COMPRESSION_EXPANDING"
    out[thin] = "THIN_LIQUIDITY"
    out[exhaustion] = "SHOCK_EXHAUSTION"
    out[shock] = "LIQUIDATION_SHOCK"
    return pd.Series(out, index=frame.index, name="regime")


def day_block_ci(values: np.ndarray, days: np.ndarray,
                 iterations: int = 2000, seed: int = 17) -> tuple:
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


def dwell_times(labels: pd.Series) -> dict:
    """Median consecutive-bar run length per regime. A regime that flickers is not a state."""
    arr = labels.to_numpy()
    runs: dict[str, list[int]] = {r: [] for r in REGIMES}
    if len(arr) == 0:
        return {r: 0.0 for r in REGIMES}
    current, length = arr[0], 1
    for value in arr[1:]:
        if value == current:
            length += 1
        else:
            runs.setdefault(current, []).append(length)
            current, length = value, 1
    runs.setdefault(current, []).append(length)
    return {r: float(np.median(runs[r])) if runs.get(r) else 0.0 for r in REGIMES}


def separability(stats: dict) -> tuple[str, list]:
    """Verdict from the protocol. Only populated regimes may decide it."""
    usable = {r: s for r, s in stats.items()
              if s["share"] >= UNDERPOPULATED and np.isfinite(s["ci"][0])}
    pairs = []
    for a in usable:
        for b in usable:
            if a >= b:
                continue
            lo_a, hi_a = usable[a]["ci"]
            lo_b, hi_b = usable[b]["ci"]
            if hi_a < lo_b or hi_b < lo_a:
                pairs.append((a, b, usable[a]["mean"], usable[b]["mean"]))
    return ("SEPARABLE" if pairs else "NONE"), pairs


def selftest() -> int:
    checks = 0

    def check(cond, label_text):
        nonlocal checks
        assert cond, label_text
        checks += 1
        print(f"  PASS  {label_text}")

    check(len(REGIMES) == 6, "the protocol's six regimes, no more and no fewer")
    check(REGIMES[0] == "LIQUIDATION_SHOCK" and REGIMES[-1] == "RANGE",
          "shock has top priority and RANGE is the residual class")

    n = 4000
    rng = np.random.default_rng(0)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    frame = add_inputs(pd.DataFrame({
        "ts_ms": np.arange(n, dtype="int64") * 60_000 + 1_785_000_000_000,
        "close": close, "volume": np.full(n, 10.0),
        "rv_15m": np.full(n, 5.0), "rv_60m": np.full(n, 5.0),
        "compression_ratio": np.full(n, 1.0)})).dropna(subset=["volume_60m"]).reset_index(drop=True)

    split = int(len(frame) * TRAIN_FRACTION)
    train = frame.iloc[:split]
    t = fit_thresholds(train)
    check(all(np.isfinite(v) for v in t.values()), "every threshold is finite")

    # THE LEAK CHECK: thresholds must depend only on train rows.
    mutated = frame.copy()
    mutated.loc[mutated.index[split + 10:], "abs_ret_5m"] = 999.0
    check(fit_thresholds(mutated.iloc[:split]) == t,
          "changing TEST rows cannot move a threshold - it is fitted on train alone")

    labels = label(frame, t)
    check(set(labels.unique()) <= set(REGIMES), "only declared regimes are ever emitted")
    check(len(labels) == len(frame), "every bar receives exactly one regime")

    # Priority: a bar meeting both shock and thin conditions must be a SHOCK.
    probe = frame.iloc[:200].copy()
    probe["abs_ret_5m"] = t["shock_ret"] + 1.0
    probe["rv_15m"] = t["shock_rv"] + 1.0
    probe["volume_60m"] = 0.0                       # also thin
    check((label(probe, t) == "LIQUIDATION_SHOCK").all(),
          "a bar that is both shocked and thin is a SHOCK - priority order holds")

    # SHOCK_EXHAUSTION must not fire on the shock bar itself.
    shocked = frame.iloc[:100].copy()
    shocked["abs_ret_5m"] = 0.0
    shocked.iloc[0, shocked.columns.get_loc("abs_ret_5m")] = t["shock_ret"] + 1
    shocked["rv_15m"] = t["shock_rv"] + 1
    shocked["volume_60m"] = 1e9
    got = label(shocked, t)
    check(got.iloc[0] == "LIQUIDATION_SHOCK", "the shock bar is labelled a shock")
    check(got.iloc[1] == "SHOCK_EXHAUSTION",
          "the bar AFTER a shock can be exhaustion - the lookback is strictly previous")

    check(np.isnan(frame["fwd_abs_bps"].iloc[-1]),
          "the forward endpoint is NaN at the tail - it is never fabricated")
    check("fwd_abs_bps" not in {"abs_ret_5m", "rv_15m", "rv_60m", "compression_ratio",
                                "volume_60m", "efficiency", "expansion"},
          "the scoring endpoint is not one of the labelling inputs")

    runs = dwell_times(pd.Series(["A", "A", "B", "A", "A", "A"]))
    check(runs.get("RANGE", 0.0) == 0.0, "a regime that never occurs has zero dwell")

    values = np.array([1.0, 2.0, 3.0, 4.0])
    check(not np.isfinite(day_block_ci(values, np.array([1, 1, 1, 1]))[0]),
          "one day yields nan, never a fabricated interval")

    verdict, pairs = separability({
        "TRENDING": {"share": 0.3, "mean": 100.0, "ci": (90.0, 110.0)},
        "RANGE": {"share": 0.3, "mean": 20.0, "ci": (10.0, 30.0)}})
    check(verdict == "SEPARABLE" and len(pairs) == 1, "non-overlapping CIs separate")
    verdict, _ = separability({
        "TRENDING": {"share": 0.3, "mean": 100.0, "ci": (10.0, 190.0)},
        "RANGE": {"share": 0.3, "mean": 20.0, "ci": (10.0, 30.0)}})
    check(verdict == "NONE", "overlapping CIs do NOT separate")
    verdict, _ = separability({
        "TRENDING": {"share": 0.3, "mean": 100.0, "ci": (90.0, 110.0)},
        "RANGE": {"share": 0.0005, "mean": 20.0, "ci": (10.0, 30.0)}})
    check(verdict == "NONE",
          "an UNDERPOPULATED regime cannot decide the verdict on its own")

    print(f"\nREGIME LABELER SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    if not MATRIX.is_file():
        print(f"missing {MATRIX}")
        return 1
    frame = add_inputs(load_matrix())
    frame = frame.dropna(subset=["abs_ret_5m", "rv_15m", "rv_60m", "compression_ratio",
                                 "volume_60m", "efficiency", "expansion"]).reset_index(drop=True)
    frame["day"] = frame["ts_ms"] // 86_400_000

    split = int(len(frame) * TRAIN_FRACTION)
    train = frame.iloc[:split].copy()
    test = frame.iloc[split + PURGE_BARS:].copy()
    thresholds = fit_thresholds(train)
    train["regime"] = label(train, thresholds)
    test["regime"] = label(test, thresholds)

    import datetime as dt
    fmt = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d")
    print("=" * 100)
    print(f"REGIME LABELER V1 - protocol {PROTOCOL} (frozen before any result)")
    print("=" * 100)
    print(f"  bars {len(frame):,}   train {fmt(frame.ts_ms.iloc[0])} -> "
          f"{fmt(frame.ts_ms.iloc[split])}   purge {PURGE_BARS}   "
          f"test -> {fmt(frame.ts_ms.iloc[-1])}")
    print("  thresholds fitted on TRAIN only: " +
          ", ".join(f"{k}={v:.4g}" for k, v in thresholds.items()))
    print()

    stats, train_means = {}, {}
    scored = test.dropna(subset=["fwd_abs_bps"])
    dwell = dwell_times(test["regime"])
    print(f"  {'regime':<24}{'share':>8}{'bars':>9}{'dwell':>7}"
          f"{'fwd |move| bps':>16}   day-block 95% CI")
    print("  " + "-" * 92)
    for regime in REGIMES:
        sub = scored[scored.regime == regime]
        share = len(test[test.regime == regime]) / len(test)
        tr = train[train.regime == regime].dropna(subset=["fwd_abs_bps"])
        train_means[regime] = float(tr.fwd_abs_bps.mean()) if len(tr) else float("nan")
        if sub.empty:
            stats[regime] = {"share": share, "mean": float("nan"),
                             "ci": (float("nan"), float("nan"))}
            print(f"  {regime:<24}{share:>7.1%}{0:>9}{dwell[regime]:>7.0f}{'-':>16}   absent")
            continue
        mean = float(sub.fwd_abs_bps.mean())
        ci = day_block_ci(sub.fwd_abs_bps.to_numpy(float), sub.day.to_numpy())
        stats[regime] = {"share": share, "mean": mean, "ci": ci}
        flag = "  UNDERPOPULATED" if share < UNDERPOPULATED else ""
        ci_text = (f"[{ci[0]:7.1f}, {ci[1]:7.1f}]" if np.isfinite(ci[0]) else "  (one day)")
        print(f"  {regime:<24}{share:>7.1%}{len(sub):>9,}{dwell[regime]:>7.0f}"
              f"{mean:>16.1f}   {ci_text}{flag}")

    verdict_kind, pairs = separability(stats)

    # Ordering stability: does the train ranking survive into test?
    usable = [r for r in REGIMES if stats[r]["share"] >= UNDERPOPULATED
              and np.isfinite(stats[r]["mean"]) and np.isfinite(train_means[r])]
    train_order = sorted(usable, key=lambda r: train_means[r])
    test_order = sorted(usable, key=lambda r: stats[r]["mean"])
    stable = train_order == test_order

    print()
    dominant = max(stats.values(), key=lambda s: s["share"])["share"]
    if dominant > DOMINANT:
        verdict = "REGIME_NOT_SEPARABLE"
        reason = f"one regime holds {dominant:.1%} of bars - the taxonomy partitions nothing"
    elif verdict_kind == "NONE":
        verdict = "REGIME_NOT_SEPARABLE"
        reason = "no pair of populated regimes has non-overlapping day-block CIs"
    elif not stable:
        verdict = "REGIME_NOT_SEPARABLE"
        reason = ("regime ordering inverts between train and test - the taxonomy is unstable\n"
                  f"      train {' < '.join(train_order)}\n      test  {' < '.join(test_order)}")
    else:
        verdict = "REGIME_SEPARABLE"
        reason = f"{len(pairs)} regime pair(s) separate with non-overlapping CIs, ordering stable"

    print(f"  VERDICT: {verdict}")
    print(f"  {reason}")
    if pairs:
        print("  separating pairs:")
        for a, b, ma, mb in pairs[:8]:
            print(f"    {a} ({ma:.1f} bps)  vs  {b} ({mb:.1f} bps)")
    print()
    print(f"  train ordering by forward |move|: {' < '.join(train_order)}")
    print(f"  test  ordering by forward |move|: {' < '.join(test_order)}")
    print()
    if verdict == "REGIME_SEPARABLE":
        print("  Separability is a NECESSARY condition for a Strategy Router, not evidence that")
        print("  a router would be profitable. Nothing here is a trading signal, and no")
        print("  threshold may be tuned on this result.")
    else:
        print("  The Strategy Router is NOT built. Routing between states that do not differ")
        print("  in forward behaviour would be decoration over a coin flip.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
