"""Does regime separate forward move AFTER conditioning on current realised volatility?

PROTOCOL
    docs/active/PREREG_REGIME_VOLATILITY_CONTROL_V1.md, sha256 4d504551..., frozen before any
    conditional result was computed.

WHY
    REGIME_LABELER_V1 found THIN_LIQUIDITY < RANGE < TRENDING on forward 15m absolute move.
    But TRENDING requires rv_60m >= P50, THIN_LIQUIDITY selects low volume, and the endpoint IS
    forward volatility. The finding may simply restate volatility clustering, which Phase 5C
    already measured. This conditions on current volatility and reports what survives.

    Regime labels are consumed exactly as the labeler produced them. No threshold, priority
    order or class definition is altered here.

    python research/regime_volatility_control_v1.py --selftest
    python research/regime_volatility_control_v1.py
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
from regime_labeler_v1 import (                                       # noqa: E402
    PURGE_BARS, TRAIN_FRACTION, add_inputs, fit_thresholds, label, load_matrix,
)

PROTOCOL = "PREREG_REGIME_VOLATILITY_CONTROL_V1.md"
DECILES = 10
MIN_CELL = 200
MIN_DECILES = 4
MATERIAL_SHRINKAGE = 0.25
PAIRS = (("TRENDING", "RANGE"), ("RANGE", "THIN_LIQUIDITY"),
         ("TRENDING", "THIN_LIQUIDITY"))


def decile_edges(train: pd.DataFrame) -> np.ndarray:
    """Volatility strata fitted on TRAIN only, as the protocol requires."""
    qs = np.linspace(0, 1, DECILES + 1)[1:-1]
    return np.unique(train["rv_60m"].quantile(qs).to_numpy())


def assign_decile(frame: pd.DataFrame, edges: np.ndarray) -> np.ndarray:
    return np.searchsorted(edges, frame["rv_60m"].to_numpy(), side="right")


def stratified_gap(frame: pd.DataFrame, a: str, b: str) -> tuple[float, int, list]:
    """Equal-weighted mean of the within-decile gap, over deciles populated in BOTH regimes."""
    gaps, used = [], []
    for d in np.unique(frame["decile"]):
        cell = frame[frame.decile == d]
        ca = cell[cell.regime == a]
        cb = cell[cell.regime == b]
        if len(ca) < MIN_CELL or len(cb) < MIN_CELL:
            continue
        gap = float(ca.fwd_abs_bps.mean() - cb.fwd_abs_bps.mean())
        gaps.append(gap)
        used.append((int(d), len(ca), len(cb), gap))
    if not gaps:
        return float("nan"), 0, used
    return float(np.mean(gaps)), len(gaps), used


def day_block_gap_ci(frame: pd.DataFrame, a: str, b: str,
                     iterations: int = 1000, seed: int = 23) -> tuple:
    """Resample whole DAYS and recompute the entire stratified statistic each time.

    Bootstrapping the bars would ignore that bars in a day share both regime and volatility -
    the two things this test is trying to separate."""
    days = frame["day"].unique()
    if len(days) < 2:
        return (float("nan"), float("nan"))
    by_day = {d: g for d, g in frame.groupby("day")}
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(iterations):
        pick = rng.integers(0, len(days), len(days))
        sample = pd.concat([by_day[days[j]] for j in pick], ignore_index=True)
        gap, n, _ = stratified_gap(sample, a, b)
        if np.isfinite(gap) and n:
            draws.append(gap)
    if len(draws) < 50:
        return (float("nan"), float("nan"))
    return (float(np.percentile(draws, 2.5)), float(np.percentile(draws, 97.5)))


def verdict_for(results: list) -> tuple[str, str]:
    usable = [r for r in results if r["deciles"] >= MIN_DECILES and np.isfinite(r["ci"][0])]
    if not usable:
        return ("REGIME_CONTROL_UNDERPOWERED",
                f"fewer than {MIN_DECILES} deciles have {MIN_CELL}+ bars in both regimes")
    excluding = [r for r in usable if (r["ci"][0] > 0) or (r["ci"][1] < 0)]
    if not excluding:
        return ("REGIME_IS_VOLATILITY_RESTATED",
                "every pair's pooled within-stratum CI spans zero")
    material = [r for r in excluding if abs(r["shrinkage"]) >= MATERIAL_SHRINKAGE]
    if material:
        best = max(material, key=lambda r: abs(r["shrinkage"]))
        return ("REGIME_ADDS_BEYOND_VOLATILITY",
                f"{best['a']} vs {best['b']} survives conditioning with "
                f"{best['shrinkage']:.0%} of its unconditional gap")
    best = max(excluding, key=lambda r: abs(r["shrinkage"]))
    return ("REGIME_ADDS_WEAKLY",
            f"the strongest surviving pair keeps only {best['shrinkage']:.0%} of its "
            f"unconditional gap - below the declared {MATERIAL_SHRINKAGE:.0%} bar")


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    check(MATERIAL_SHRINKAGE == 0.25 and MIN_CELL == 200 and MIN_DECILES == 4,
          "the protocol's declared constants are in force")
    check(len(PAIRS) == 3, "only the three unconditionally-separating pairs are examined")

    rng = np.random.default_rng(0)
    n = 60_000
    days = np.repeat(np.arange(n // 200), 200)[:n]
    rv = rng.uniform(1.0, 10.0, n)

    # NEGATIVE CONTROL: forward move is a function of rv ALONE. Regime correlates with rv but
    # is not a deterministic function of it - noise on the threshold, so both regimes appear
    # inside every decile. That is the real situation: TRENDING also requires efficiency >= 1,
    # so an rv stratum contains both. A perfect step function of rv is degenerate - it puts one
    # regime per stratum and the control cannot be computed at all.
    noisy = rv + rng.normal(0, 2.0, n)
    frame = pd.DataFrame({
        "rv_60m": rv, "fwd_abs_bps": rv * 3.0 + rng.normal(0, 0.5, n),
        "regime": np.where(noisy > 5.5, "TRENDING", "RANGE"), "day": days})
    edges = decile_edges(frame)
    frame["decile"] = assign_decile(frame, edges)
    gap, ndec, _ = stratified_gap(frame, "TRENDING", "RANGE")
    uncond = float(frame[frame.regime == "TRENDING"].fwd_abs_bps.mean()
                   - frame[frame.regime == "RANGE"].fwd_abs_bps.mean())
    check(uncond > 3.0, "unconditionally the constructed regimes look very different")
    check(ndec >= MIN_DECILES, "both regimes populate enough strata for the control to run")
    check(abs(gap) < MATERIAL_SHRINKAGE * abs(uncond),
          "...but WITHIN volatility strata the gap collapses - a restatement IS detected")

    # POSITIVE CONTROL: an effect genuinely independent of rv must survive conditioning.
    flag = rng.random(n) < 0.5
    extra = pd.DataFrame({
        "rv_60m": rv, "fwd_abs_bps": rv * 3.0 + np.where(flag, 8.0, 0.0) + rng.normal(0, .5, n),
        "regime": np.where(flag, "TRENDING", "RANGE"), "day": days})
    extra["decile"] = assign_decile(extra, decile_edges(extra))
    gap2, ndec2, _ = stratified_gap(extra, "TRENDING", "RANGE")
    check(ndec2 >= MIN_DECILES and gap2 > 6.0,
          "a genuinely independent effect SURVIVES conditioning - the control is not always null")

    check(np.isnan(stratified_gap(frame.iloc[:10], "TRENDING", "RANGE")[0]),
          "too few bars per cell yields nan, never a fabricated gap")
    # A regime that is a PERFECT function of the stratifier leaves no within-stratum contrast.
    degenerate = frame.copy()
    degenerate["regime"] = np.where(degenerate.rv_60m > 5.52, "TRENDING", "RANGE")
    degenerate["decile"] = assign_decile(degenerate, edges)
    check(stratified_gap(degenerate, "TRENDING", "RANGE")[1] == 0,
          "a regime perfectly determined by volatility yields NO comparable strata")
    kind, _ = verdict_for([{"deciles": 1, "ci": (1.0, 2.0), "shrinkage": 1.0,
                            "a": "A", "b": "B"}])
    check(kind == "REGIME_CONTROL_UNDERPOWERED",
          "too few populated deciles is UNDERPOWERED, never a pass")
    kind, _ = verdict_for([{"deciles": 8, "ci": (-1.0, 2.0), "shrinkage": 0.9,
                            "a": "A", "b": "B"}])
    check(kind == "REGIME_IS_VOLATILITY_RESTATED", "a CI spanning zero does not survive")
    kind, _ = verdict_for([{"deciles": 8, "ci": (0.1, 2.0), "shrinkage": 0.10,
                            "a": "A", "b": "B"}])
    check(kind == "REGIME_ADDS_WEAKLY",
          "significant but immaterial shrinkage is WEAK, not a pass")
    kind, _ = verdict_for([{"deciles": 8, "ci": (0.1, 2.0), "shrinkage": 0.60,
                            "a": "A", "b": "B"}])
    check(kind == "REGIME_ADDS_BEYOND_VOLATILITY", "significant AND material passes")

    print(f"\nVOLATILITY CONTROL SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    frame = add_inputs(load_matrix())
    frame = frame.dropna(subset=["abs_ret_5m", "rv_15m", "rv_60m", "compression_ratio",
                                 "volume_60m", "efficiency", "expansion"]).reset_index(drop=True)
    frame["day"] = frame["ts_ms"] // 86_400_000
    split = int(len(frame) * TRAIN_FRACTION)
    train = frame.iloc[:split].copy()
    test = frame.iloc[split + PURGE_BARS:].copy()

    thresholds = fit_thresholds(train)
    test["regime"] = label(test, thresholds)
    edges = decile_edges(train)
    test["decile"] = assign_decile(test, edges)
    scored = test.dropna(subset=["fwd_abs_bps"])

    print("=" * 100)
    print(f"REGIME VOLATILITY CONTROL - protocol {PROTOCOL} (frozen before any result)")
    print("=" * 100)
    print(f"  test bars {len(scored):,}   volatility deciles fitted on TRAIN only   "
          f"min cell {MIN_CELL}   materiality {MATERIAL_SHRINKAGE:.0%}")
    print()

    results = []
    for a, b in PAIRS:
        uncond = float(scored[scored.regime == a].fwd_abs_bps.mean()
                       - scored[scored.regime == b].fwd_abs_bps.mean())
        gap, ndec, used = stratified_gap(scored, a, b)
        ci = day_block_gap_ci(scored, a, b) if ndec else (float("nan"), float("nan"))
        shrink = (gap / uncond) if (np.isfinite(gap) and uncond) else float("nan")
        results.append({"a": a, "b": b, "uncond": uncond, "gap": gap, "ci": ci,
                        "deciles": ndec, "shrinkage": shrink, "used": used})

    print(f"  {'pair':<34}{'uncond':>9}{'within-vol':>12}{'shrink':>9}{'deciles':>9}"
          f"   day-block 95% CI")
    print("  " + "-" * 96)
    for r in results:
        ci = (f"[{r['ci'][0]:+7.2f}, {r['ci'][1]:+7.2f}]"
              if np.isfinite(r["ci"][0]) else "   (insufficient)")
        gap = f"{r['gap']:+11.2f}" if np.isfinite(r["gap"]) else f"{'-':>11}"
        shrink = f"{r['shrinkage']:>8.0%}" if np.isfinite(r["shrinkage"]) else f"{'-':>8}"
        print(f"  {r['a'] + ' vs ' + r['b']:<34}{r['uncond']:>+9.2f}{gap}{shrink}"
              f"{r['deciles']:>9}   {ci}")

    print()
    print("  within-decile detail (the strongest pair):")
    strongest = max(results, key=lambda r: r["deciles"])
    print(f"    {strongest['a']} vs {strongest['b']}")
    print(f"      {'decile':>7}{'n(' + strongest['a'][:6] + ')':>12}"
          f"{'n(' + strongest['b'][:6] + ')':>12}{'gap bps':>10}")
    for d, na, nb, gap in strongest["used"]:
        print(f"      {d:>7}{na:>12,}{nb:>12,}{gap:>10.2f}")

    verdict, reason = verdict_for(results)
    print()
    print(f"  VERDICT: {verdict}")
    print(f"  {reason}")
    print()
    if verdict == "REGIME_IS_VOLATILITY_RESTATED":
        print("  The unconditional regime separation does NOT survive conditioning on current")
        print("  realised volatility. The Strategy Router is not built on this taxonomy: it")
        print("  would route on a quantity the volatility model already predicts.")
        print("  A regime definition that does not use volatility thresholds would need its")
        print("  own preregistration.")
    elif verdict == "REGIME_ADDS_WEAKLY":
        print("  Statistically present, materially mostly volatility. Not a basis for a router.")
    elif verdict == "REGIME_ADDS_BEYOND_VOLATILITY":
        print("  Regime carries information beyond current volatility. This is a necessary")
        print("  condition for the router, still not evidence that a router would be")
        print("  profitable after costs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
