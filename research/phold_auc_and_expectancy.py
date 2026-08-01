"""P_HOLD_AUC_AND_EXPECTANCY_V1 - does the ranking skill survive contact with the quoted price?

THE QUESTION
    The June retrain recorded held-out test AUC 0.746 for P(hold) and called its calibration
    excellent. The same document recorded that raw DIRECTION cleared 0 of 7 horizons at
    AUC 0.50-0.52 and was not saved. Live head-health then demoted P(hold) to CALIBRATION_ONLY
    on ECE 0.0678 against a 0.05 gate: "ranks, does not price".

    So: the head ranks. This measures whether that ranking is worth money at the price you
    would actually have paid, using the LIVE deployed head's own logged predictions and the
    quoted asks recorded beside them.

WHAT THIS DOES AND DOES NOT ANSWER
    IT DOES answer: is the head that is deployed today, as actually served, profitable after
    the real quoted ask and the real recorded fee?

    IT DOES NOT answer: would a head retrained under the current v14 contract do better? The
    served artifact was trained under v11 with VWAP semantics v1, and cannot be scored on v14
    features because the feature sets differ (69 pruned vs 63, hash 7977e0559560 vs
    864622d65e85). Answering that needs the retrain, not a measurement.

    This is the more decisive of the two questions anyway. A head that cannot pay for its own
    spread after being served in production for weeks does not become profitable by being
    retrained on the same target at the same horizon.

POPULATION - identical to backend/monitoring/head_health.py, deliberately
    One observation per round, the FIRST snapshot with seconds_left in [15, 120], official
    settlement only, leader in {UP, DOWN}. Pooling ticks would inflate n roughly 13x and
    manufacture confidence, which is why the head-health protocol takes one per round and why
    this reuses that exact query rather than writing a friendlier one.

GATES, DECLARED BEFORE ANY RESULT IS SEEN
    G1  one observation per round; the executable price is LATE_LEADER_30S_V1's recorded ask
        plus its recorded fee, one quote per round. Several rules quote the same round, and
        taking the best of them across rules would be cherry-picking across different clock
        times - 8,009 joined rows against 6,725 rounds is double counting, not extra evidence.
    G2  expectancy is per $1 of notional: payoff (1 if the leader held, else 0) minus ask
        minus fee. No sizing, no compounding, no reinvestment.
    G3  a bucket counts as evidence only with n >= 100.
    G4  PASS requires mean net expectancy > 0 at the 5% DAY-BLOCK lower bound, not at the
        point estimate. Days are the block because rounds inside a day share regime.
    G5  the always-trade-the-leader baseline is reported beside every filtered result. A
        filter that does not beat trading everything has established nothing.

    python research/phold_auc_and_expectancy.py

RETRACTED - p_hold 0.97-0.99 bucket, +0.0371/$1 with day LCB +0.0069
    This study joined a market STATE to an executable QUOTE without requiring the state to be
    available at the decision timestamp. In 93.5% of rows the state was observed AFTER the
    decision (median +8.1s). See research/causal_decision_join.py for the corrected
    construction and research/research_status.py for the registry.

    It refuses to run without --run-retracted-study.
"""
from __future__ import annotations

RESEARCH_STATUS = "RETRACTED"
RETRACTION_REASON = "NONCAUSAL_STATE_QUOTE_JOIN"
REPLACED_BY = "research/causal_decision_join.py"
CAPITAL_AUTHORITY = False

import argparse
from pathlib import Path

import duckdb
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "btc_duckdbs" / "analytics.duckdb"

RNG = np.random.default_rng(20260731)
QUOTE_RULE = "LATE_LEADER_30S_V1"
MIN_BUCKET_N = 100
DRAWS = 2000

POPULATION = """
WITH s AS (
    SELECT round_id, ts, p_leader_holds, current_position, seconds_left,
           ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY seconds_left) rn
    FROM round_state_snapshots
    WHERE seconds_left BETWEEN 15 AND 120 AND p_leader_holds IS NOT NULL
),
q AS (
    SELECT round_id, side, ask, fee,
           ROW_NUMBER() OVER (PARTITION BY round_id ORDER BY ts) rn
    FROM rule_paper_trades
    WHERE rule = '{rule}' AND ask IS NOT NULL AND ask > 0
)
SELECT s.ts,
       s.p_leader_holds                                                  AS p_hold,
       CASE WHEN s.current_position = p.actual_direction THEN 1 ELSE 0 END AS held,
       q.ask,
       COALESCE(q.fee, 0.0)                                              AS fee
FROM s
JOIN price_to_beat p ON p.id = s.round_id
LEFT JOIN q ON q.round_id = s.round_id AND q.side = s.current_position AND q.rn = 1
WHERE s.rn = 1
  AND p.resolved
  AND p.actual_direction IN ('UP', 'DOWN')
  AND p.settlement_source LIKE 'official:%'
  AND s.current_position IN ('UP', 'DOWN')
ORDER BY s.ts
"""


def roc_auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """Rank-based AUC, ties averaged. No sklearn dependency, no surprises."""
    positives = labels == 1
    n_pos, n_neg = int(positives.sum()), int((~positives).sum())
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), dtype=float)
    ranks[order] = np.arange(1, len(scores) + 1)
    # average ranks within tie groups
    sorted_scores = scores[order]
    start = 0
    for index in range(1, len(sorted_scores) + 1):
        if index == len(sorted_scores) or sorted_scores[index] != sorted_scores[start]:
            ranks[order[start:index]] = ranks[order[start:index]].mean()
            start = index
    return float((ranks[positives].sum() - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg))


def day_block_lcb(values: np.ndarray, days: np.ndarray, draws: int = DRAWS) -> float:
    unique = np.unique(days)
    if len(unique) < 5 or len(values) == 0:
        return float("nan")
    by_day = {day: values[days == day] for day in unique}
    means = np.empty(draws)
    for index in range(draws):
        picked = RNG.integers(0, len(unique), len(unique))
        means[index] = np.concatenate([by_day[unique[j]] for j in picked]).mean()
    means.sort()
    return float(means[int(0.05 * draws)])


def expected_calibration_error(probabilities, labels, bins=10) -> float:
    edges = np.linspace(0.0, 1.0, bins + 1)
    total, error = len(probabilities), 0.0
    for low, high in zip(edges[:-1], edges[1:]):
        mask = (probabilities >= low) & (probabilities < high if high < 1.0 else probabilities <= 1.0)
        if not mask.any():
            continue
        error += mask.sum() / total * abs(labels[mask].mean() - probabilities[mask].mean())
    return float(error)


def main() -> int:
    from research_status import guard
    guard(Path(__file__).name)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rule", default=QUOTE_RULE)
    args = parser.parse_args()

    if not DB.exists():
        print(f"missing {DB}")
        return 1
    con = duckdb.connect(str(DB), read_only=True)
    frame = con.execute(POPULATION.format(rule=args.rule)).df()
    con.close()

    print("=" * 100)
    print("P(HOLD) - AUC AND NET-OF-COST EXPECTANCY ON THE LIVE DEPLOYED HEAD")
    print("=" * 100)
    print(f"  population (head-health protocol, one row per round) : {len(frame):,}")
    if frame.empty:
        print("  no rows - nothing to measure")
        return 1

    probabilities = frame["p_hold"].to_numpy(dtype=float)
    labels = frame["held"].to_numpy(dtype=int)
    # ts is BIGINT MILLISECONDS. An earlier pass divided by 86_400_000_000_000 (nanoseconds
    # per day), collapsing every row into one block, so every day-block LCB came back nan and
    # every bucket printed "fails G4" - a vacuous negative dressed as a result.
    days = (frame["ts"].to_numpy(dtype="int64") // 86_400_000)

    base_rate = labels.mean()
    auc = roc_auc(probabilities, labels)
    ece = expected_calibration_error(probabilities, labels)

    # AUC standard error (Hanley-McNeil), enough to say whether 0.746 is even in range.
    n_pos, n_neg = int(labels.sum()), int((labels == 0).sum())
    q1 = auc / (2 - auc)
    q2 = 2 * auc * auc / (1 + auc)
    se = float(np.sqrt(
        (auc * (1 - auc) + (n_pos - 1) * (q1 - auc**2) + (n_neg - 1) * (q2 - auc**2))
        / (n_pos * n_neg)
    ))

    print(f"  leader actually held                                 : {base_rate*100:.2f}%")
    print(f"  mean predicted p_hold                                : {probabilities.mean()*100:.2f}%")
    print()
    print("-" * 100)
    print("1. RANKING")
    print("-" * 100)
    print(f"  AUC (live, current serving)  : {auc:.4f}  +/- {1.96*se:.4f} (95%)")
    print(f"  June held-out test AUC       : 0.746   <- measured under contract v11 / VWAP v1")
    print(f"  ECE                          : {ece:.4f}  (gate 0.05)")
    print()
    if auc + 1.96 * se < 0.746:
        print("  The June figure is OUTSIDE the live confidence interval. Whatever the head")
        print("  scored on held-out June data, it does not rank that well in production now.")
    elif auc > 0.5 + 1.96 * se:
        print("  Ranking skill is present and better than chance.")
    else:
        print("  Ranking is not distinguishable from chance at this sample size.")

    print()
    print("-" * 100)
    print("2. NET-OF-COST EXPECTANCY - buy the leader at the recorded ask, hold to settlement")
    print("-" * 100)
    quoted = frame.dropna(subset=["ask"]).copy()
    print(f"  rounds with a {args.rule} quote : {len(quoted):,}")
    if len(quoted) < MIN_BUCKET_N:
        print("  too few quoted rounds to measure expectancy")
        return 0

    q_prob = quoted["p_hold"].to_numpy(dtype=float)
    q_held = quoted["held"].to_numpy(dtype=float)
    q_ask = quoted["ask"].to_numpy(dtype=float)
    q_fee = quoted["fee"].to_numpy(dtype=float)
    q_days = quoted["ts"].to_numpy(dtype="int64") // 86_400_000
    net = q_held - q_ask - q_fee                      # per $1 of notional

    baseline_mean = net.mean()
    baseline_lcb = day_block_lcb(net, q_days)
    print(f"  mean ask {q_ask.mean():.4f} | mean fee {q_fee.mean():.4f} | "
          f"leader held {q_held.mean()*100:.1f}% | distinct days "
          f"{len(np.unique(quoted['ts'].to_numpy(dtype='int64') // 86_400_000))}")
    print()
    print(f"{'p_hold bucket':>16}{'n':>8}{'held %':>9}{'mean ask':>10}"
          f"{'net/$1':>10}{'day LCB':>10}  verdict")
    print("-" * 100)
    baseline_measured = bool(np.isfinite(baseline_lcb))
    baseline_lcb_text = f"{baseline_lcb:>+10.4f}" if baseline_measured else f"{'-':>10}"
    baseline_verdict = (
        ("positive" if baseline_lcb > 0 else "NEGATIVE") if baseline_measured
        else "NOT MEASURED (too few days)"
    )
    print(f"{'ALL (baseline)':>16}{len(net):>8}{q_held.mean()*100:>8.1f}%{q_ask.mean():>10.4f}"
          f"{baseline_mean:>+10.4f}{baseline_lcb_text}  {baseline_verdict}")

    survivors = []
    for low, high in ((0.0, 0.90), (0.90, 0.95), (0.95, 0.97), (0.97, 0.99), (0.99, 1.01)):
        mask = (q_prob >= low) & (q_prob < high)
        if mask.sum() < MIN_BUCKET_N:
            if mask.sum():
                print(f"{f'{low:.2f}-{high:.2f}':>16}{int(mask.sum()):>8}"
                      f"{'':>9}{'':>10}{'(below n gate)':>10}")
            continue
        bucket_net = net[mask]
        lcb = day_block_lcb(bucket_net, q_days[mask])
        # A nan lower bound means the bootstrap could not run, NOT that the bucket failed.
        # Printing "fails G4" for an unmeasured bucket manufactures a negative result.
        measured = np.isfinite(lcb)
        passes = bool(measured and lcb > 0)
        if passes:
            survivors.append((low, high, bucket_net.mean(), lcb))
        verdict = "PASS" if passes else ("fails G4" if measured else "NOT MEASURED (too few days)")
        lcb_text = f"{lcb:>+10.4f}" if measured else f"{'-':>10}"
        print(f"{f'{low:.2f}-{high:.2f}':>16}{int(mask.sum()):>8}{q_held[mask].mean()*100:>8.1f}%"
              f"{q_ask[mask].mean():>10.4f}{bucket_net.mean():>+10.4f}{lcb_text}  {verdict}")

    print()
    print("-" * 100)
    print("VERDICT")
    print("-" * 100)
    if survivors:
        print(f"  {len(survivors)} bucket(s) clear the DECLARED gate G4 (5% day-block LCB > 0):")
        for low, high, mean, lcb in survivors:
            lift = mean - baseline_mean
            print(f"    p_hold {low:.2f}-{high:.2f}: {mean:+.4f}/$1, LCB {lcb:+.4f}, "
                  f"lift over always-trading {lift:+.4f}")

        print()
        print("  SCRUTINY. G4 was declared before results and the bucket passed it, so that")
        print("  pass is recorded as stated. What follows is not a moved goalpost - it is the")
        print("  multiplicity the protocol flagged in prose but did not quantify, plus two")
        print("  robustness diagnostics. A pass that does not survive them is not an edge.")
        print()
        for low, high, mean, _ in survivors:
            mask = (q_prob >= low) & (q_prob < high)
            bucket_net, bucket_days = net[mask], q_days[mask]
            unique_days = np.unique(bucket_days)

            # Bonferroni over the five buckets examined: 0.05 / 5 = 0.01.
            by_day = {day: bucket_net[bucket_days == day] for day in unique_days}
            means = np.sort(np.array([
                np.concatenate(
                    [by_day[unique_days[j]]
                     for j in RNG.integers(0, len(unique_days), len(unique_days))]
                ).mean()
                for _ in range(DRAWS * 2)
            ]))
            bonferroni_lcb = float(means[int(0.01 * len(means))])

            totals = sorted(
                ((day, by_day[day].sum()) for day in unique_days),
                key=lambda item: -item[1],
            )
            grand = sum(value for _, value in totals)
            top3 = sum(value for _, value in totals[:3]) / grand * 100 if grand else float("nan")

            print(f"    p_hold {low:.2f}-{high:.2f}")
            print(f"      Bonferroni 1% LCB (5 buckets)   : {bonferroni_lcb:+.4f}"
                  f"   {'survives' if bonferroni_lcb > 0 else '<- CROSSES ZERO'}")
            print(f"      top 3 of {len(unique_days)} days as % of profit : {top3:.0f}%"
                  f"   {'concentrated' if top3 > 50 else 'spread'}")

        # Monotonicity: if the ranking translated into money, expectancy should RISE with
        # p_hold. A single spiking bucket between flat neighbours is the shape of noise.
        ordered = []
        for low, high in ((0.0, 0.90), (0.90, 0.95), (0.95, 0.97), (0.97, 0.99), (0.99, 1.01)):
            mask = (q_prob >= low) & (q_prob < high)
            if mask.sum() >= MIN_BUCKET_N:
                ordered.append((low, high, float(net[mask].mean())))
        rising = all(b[2] >= a[2] for a, b in zip(ordered, ordered[1:]))
        print(f"      expectancy monotone in p_hold   : {'yes' if rising else 'NO'}")
        if not rising:
            print("        " + "  ".join(f"{lo:.2f}-{hi:.2f}:{val:+.4f}" for lo, hi, val in ordered))
            print("        Expectancy does not rise with the score. The largest bucket by far")
            print("        (0.99+) pays almost nothing, so the money is not where the model is")
            print("        most confident - which is what a real edge would require.")
        print()
        print("  READ: passing a pre-declared gate is necessary, not sufficient. Forward")
        print("  evidence on rounds that took no part in this measurement is the only thing")
        print("  that would settle it.")
    else:
        print("  NO p_hold bucket clears a positive day-block lower bound.")
        print()
        print("  The head ranks - that is what AUC measures and it is not in dispute. But the")
        print("  price already contains the ranking. Buying the leader costs roughly what the")
        print("  leader is worth, so ordering rounds correctly does not produce money at the")
        print("  quoted ask. This is the same result the 39-script research suite reached from")
        print("  a completely different direction, and the reason head_permissions demoted this")
        print("  head to 'ranks, does not price'.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
