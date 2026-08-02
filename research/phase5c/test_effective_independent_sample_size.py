"""PHASE5C_136 - how many INDEPENDENT observations does the 21-day window actually contain?

WHY THIS RUNS BEFORE THE OTHER FIFTY-ONE
    Phase 5C proposes 52 tests against the existing data. Every one of them will be judged by
    some confidence statement, and the value of that statement depends entirely on what is
    treated as an independent unit. 50,272 checkpoints look like 50,272 trials. They are 6.5
    checkpoints drawn from each of 7,782 rounds, spread over 21 days and 4 weeks.

    So this computes, for each clustering level, the MINIMUM DETECTABLE EFFECT - the smallest
    shift in win rate that could be distinguished from noise at 80% power. If the effect a test
    is hunting is smaller than its own MDE, the test cannot answer its question no matter what
    it finds, and building it is wasted work.

    This is not a result about any strategy. It is a bound on what the WINDOW can support, and
    it applies to every candidate equally.

THE INFERENCE LEVEL THIS REPOSITORY ALREADY USES
    `day_block_lcb` resamples DAYS. That is day-clustered inference, and it is the right choice
    - volatility, regime and recorder health all cluster within a day, so two checkpoints from
    the same day are not two independent draws.

    It also means the operative MDE is the day-clustered one. Reporting a row-level interval
    beside it would be the same error in a different costume.

    python research/phase5c/test_effective_independent_sample_size.py
    python research/phase5c/test_effective_independent_sample_size.py --selftest
"""
from __future__ import annotations

RESEARCH_STATUS = "VALID_DIAGNOSTIC"
CAPITAL_AUTHORITY = False

import argparse
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
CHECKPOINTS = ROOT / "data" / "research" / "causal_checkpoints_v1.parquet"
LABELS = ROOT / "data" / "research" / "causal_checkpoint_labels_v1.parquet"

#: Two-sided 80% power at alpha=0.05 -> z(0.975)+z(0.80) = 1.96 + 0.84.
POWER_Z = 2.80


def minimum_detectable_shift(rate: float, n: int) -> float:
    """Smallest win-rate shift distinguishable from noise, in percentage points."""
    if n < 2:
        return float("nan")
    return POWER_Z * float(np.sqrt(rate * (1.0 - rate) / n)) * 100.0


def intra_cluster_correlation(values: np.ndarray, groups: np.ndarray) -> float:
    """One-way ANOVA ICC: how much of the variance lives BETWEEN clusters.

    An ICC near zero means rows inside a cluster are effectively independent; near one means a
    cluster contributes about one observation however many rows it holds."""
    unique = np.unique(groups)
    if len(unique) < 2 or len(values) <= len(unique):
        return float("nan")
    grand = values.mean()
    sizes = np.array([int((groups == g).sum()) for g in unique], dtype=float)
    means = np.array([values[groups == g].mean() for g in unique])
    between = float((sizes * (means - grand) ** 2).sum() / (len(unique) - 1))
    within = float(sum(((values[groups == g] - m) ** 2).sum()
                       for g, m in zip(unique, means)) / (len(values) - len(unique)))
    average = float(sizes.mean())
    variance = (between - within) / average
    return float(variance / (variance + within)) if (variance + within) > 0 else float("nan")


def design_effect(icc: float, average_cluster_size: float) -> float:
    """Kish design effect: 1 + (m - 1) * ICC. Divide raw N by this for the effective N."""
    if not np.isfinite(icc):
        return float("nan")
    return 1.0 + (average_cluster_size - 1.0) * max(icc, 0.0)


def analyse(outcomes, clusters: dict) -> dict:
    rate = float(np.mean(outcomes))
    rows = []
    for name, groups in clusters.items():
        n = int(len(np.unique(groups)))
        icc = intra_cluster_correlation(outcomes, groups)
        average = len(outcomes) / n if n else float("nan")
        effect = design_effect(icc, average)
        rows.append({
            "level": name, "clusters": n,
            "avg_rows_per_cluster": round(average, 1),
            "icc": round(icc, 5) if np.isfinite(icc) else None,
            "design_effect": round(effect, 2) if np.isfinite(effect) else None,
            "effective_n": int(len(outcomes) / effect) if np.isfinite(effect) and effect > 0
            else None,
            "mde_points": round(minimum_detectable_shift(rate, n), 2),
        })
    return {"rows": int(len(outcomes)), "win_rate": round(rate, 4), "levels": rows}


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    check(minimum_detectable_shift(0.5, 100) > minimum_detectable_shift(0.5, 10_000),
          "more observations detect smaller effects - the MDE falls as N rises")
    check(abs(minimum_detectable_shift(0.5, 10_000) - 1.4) < 0.05,
          "MDE at p=0.5, N=10,000 is 2.80*sqrt(.25/1e4)*100 = 1.4 points")

    rng = np.random.default_rng(11)
    # Independent rows: ICC ~ 0, so the design effect is ~1 and effective N ~ raw N.
    groups = np.repeat(np.arange(200), 10)
    independent = rng.integers(0, 2, 2000).astype(float)
    icc_independent = intra_cluster_correlation(independent, groups)
    check(abs(icc_independent) < 0.05,
          "independent rows inside a cluster produce an ICC near zero")
    check(abs(design_effect(icc_independent, 10.0) - 1.0) < 0.5,
          "an ICC near zero leaves the design effect near 1 - no inflation to correct")

    # Perfectly clustered: every row in a cluster is identical, so 200 clusters carry 200 draws.
    clustered = np.repeat(rng.integers(0, 2, 200).astype(float), 10)
    icc_clustered = intra_cluster_correlation(clustered, groups)
    check(icc_clustered > 0.9,
          "rows identical within a cluster produce an ICC near one")
    check(design_effect(icc_clustered, 10.0) > 9.0,
          "a high ICC inflates the design effect toward the cluster size - 2,000 rows are 200")
    check(minimum_detectable_shift(0.77, 21) > 20.0,
          "21 day-clusters cannot detect a shift below about 20 points, whatever the row count")

    print(f"\nEFFECTIVE SAMPLE SIZE SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    print("=" * 96)
    print("PHASE5C-136  EFFECTIVE INDEPENDENT SAMPLE SIZE - what can this window support?")
    print("=" * 96)
    for path in (CHECKPOINTS, LABELS):
        if not path.is_file():
            print(f"  BLOCKED: {path.name} is missing.")
            return 0

    import duckdb
    con = duckdb.connect(":memory:")
    try:
        frame = con.execute(f"""
            SELECT k.slug, k.snapshot_ts, l.label_checkpoint_side_wins AS won
            FROM read_parquet('{CHECKPOINTS.as_posix()}') k
            JOIN read_parquet('{LABELS.as_posix()}') l
              ON l.slug = k.slug AND l.checkpoint_s = k.checkpoint_s
            WHERE k.eligible AND l.label_checkpoint_side_wins IS NOT NULL""").df()
    finally:
        con.close()

    outcomes = frame["won"].to_numpy(float)
    seconds = frame["snapshot_ts"].to_numpy(float)
    report = analyse(outcomes, {
        "row (no clustering)": np.arange(len(frame)),
        "round": frame["slug"].to_numpy(),
        "day": (seconds // 86_400).astype(np.int64),
        "week": (seconds // 604_800).astype(np.int64),
    })

    print(f"  rows {report['rows']:,} | base win rate {report['win_rate']:.4f}")
    print()
    print(f"{'clustering':<22}{'clusters':>10}{'rows/cluster':>14}{'ICC':>9}"
          f"{'design eff':>12}{'effective N':>13}{'MDE pts':>10}")
    for row in report["levels"]:
        print(f"{row['level']:<22}{row['clusters']:>10,}{row['avg_rows_per_cluster']:>14}"
              f"{str(row['icc']):>9}{str(row['design_effect']):>12}"
              f"{str(row['effective_n'] or '-'):>13}{row['mde_points']:>10}")

    day = next(r for r in report["levels"] if r["level"] == "day")
    print()
    print("  TWO NUMBERS, TWO QUESTIONS - they are not in conflict:")
    print("    effective N  precision of the POINT ESTIMATE after correcting for correlation")
    print("    MDE          set by the CLUSTER COUNT, because you cannot estimate between-day")
    print("                 variance from 21 days however many rows each day holds")
    print()
    print("  This repository judges results with day_block_lcb, which resamples DAYS. So the")
    print(f"  operative bound is the day-clustered row: **{day['mde_points']} percentage points**.")
    print("  Any Phase 5C test hunting an effect smaller than that cannot answer its question")
    print("  on this window, however many rows it reports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
