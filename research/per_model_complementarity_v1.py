"""Is there a rescuable window? Measure the hindsight ceiling BEFORE building any selector.

THE IDEA UNDER TEST
    The ensemble is near a coin flip. Its seats disagree. If different seats are right on
    different windows, a selector that picked the right seat per window would beat the
    ensemble. That is worth testing - but the tempting number is a trap.

WHY THE ORACLE CEILING ALONE PROVES NOTHING
    With 7 seats and 3 classes, "at least one seat was correct" is high BY CONSTRUCTION.
    Three seats that always answer UP, DOWN and NEUTRAL give a perfect hindsight oracle while
    knowing nothing whatsoever. So the observed oracle is compared against a PERMUTATION NULL:
    each seat's predictions are shuffled within its own (day, horizon) block, preserving its
    class frequencies and destroying only the row-level alignment. The result that matters is

        observed oracle  -  permuted oracle

    Everything else here is subordinate to that difference.

A GRADING-CONTRACT DEFECT IN THE RECORDED DATA
    `model_predictions.actual_direction` carries only UP and DOWN. The seats emit NEUTRAL on
    roughly half their rows. A NEUTRAL prediction therefore CANNOT be scored correct - not
    because the seat was wrong, but because the recorded grade answers a different question
    than the seat was trained on (endpoint direction vs first-touch triple barrier).

    Counting those as seat errors inflates the rescue opportunity: every NEUTRAL row becomes a
    window where "the ensemble was wrong and some other seat was right". This script refuses
    to report a single headline oracle over that mixture. It reports the ADMISSIBLE subset -
    rows where a seat committed to UP or DOWN - and states the excluded mass explicitly.

    python research/per_model_complementarity_v1.py
"""
from __future__ import annotations

import os
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "backend"))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

#: The LIVE archive, which is not the default path. Opened READ-ONLY; this script never writes.
DB = os.environ.get("BTC_ANALYTICS_DB") or str(REPO / "data" / "btc_duckdbs" / "analytics.duckdb")

#: Preregistered before looking at any result.
N_PERMUTATIONS = 1000
MIN_DECISIONS = 500
RNG_SEED = 20260806


def load(db_path: str):
    import duckdb
    con = duckdb.connect(db_path, read_only=True)
    rows = con.execute("""
        SELECT timestamp, horizon, model, direction, actual_direction
        FROM model_predictions
        WHERE resolved AND actual_direction IS NOT NULL AND direction IS NOT NULL
        ORDER BY timestamp
    """).fetchall()
    con.close()
    return rows


def main() -> int:
    if not Path(DB).exists():
        print(f"VERDICT: NO DATA - {DB} not found")
        return 0

    rows = load(DB)
    seats = sorted({r[2] for r in rows})
    by_decision: dict = defaultdict(dict)
    truth: dict = {}
    for ts, h, model, direction, actual in rows:
        by_decision[(ts, h)][model] = direction
        truth[(ts, h)] = actual

    complete = [k for k, v in by_decision.items() if len(v) == len(seats)]
    complete.sort()
    print("=" * 92)
    print("PER-MODEL COMPLEMENTARITY V1")
    print("=" * 92)
    print(f"  seats                : {len(seats)}  ({', '.join(seats)})")
    print(f"  decisions with ALL   : {len(complete):,}")

    # ---- the grading-contract defect, quantified before anything is concluded ----------
    truth_classes = sorted({truth[k] for k in complete})
    pred_classes = sorted({d for k in complete for d in by_decision[k].values()})
    neutral_mass = float(np.mean([
        sum(1 for d in by_decision[k].values() if d not in truth_classes) / len(seats)
        for k in complete])) if complete else 0.0
    print(f"  outcome vocabulary   : {truth_classes}")
    print(f"  seat vocabulary      : {pred_classes}")
    print(f"  seat predictions OUTSIDE the outcome vocabulary: {neutral_mass:.1%}")
    if neutral_mass > 0.05:
        print()
        print("  GRADING-CONTRACT MISMATCH. Those predictions cannot be scored correct under")
        print("  the recorded grade regardless of what price did - the seats answer a")
        print("  first-touch question and the grade answers an endpoint one. Counting them as")
        print("  seat errors would INFLATE the rescue opportunity, which is the direction that")
        print("  makes this idea look good. The oracle below is restricted to rows where a")
        print("  seat committed to a class the grade can actually score.")

    if len(complete) < MIN_DECISIONS:
        print(f"\nVERDICT: INSUFFICIENT DATA ({len(complete)} < {MIN_DECISIONS} decisions)")
        return 0

    # ---- admissible view ----------------------------------------------------------------
    # A seat is SCORED on a decision only when it committed to a class the grade can score.
    # Abstention is not an error; treating it as one is how the ceiling gets inflated.
    correct = {}                       # (decision, seat) -> True/False/None(abstained)
    for k in complete:
        y = truth[k]
        for s in seats:
            d = by_decision[k][s]
            correct[(k, s)] = None if d not in truth_classes else (d == y)

    def committed(k, s):
        return correct[(k, s)] is not None

    per_seat = {}
    for s in seats:
        scored = [k for k in complete if committed(k, s)]
        acc = float(np.mean([correct[(k, s)] for k in scored])) if scored else float("nan")
        per_seat[s] = {"coverage": len(scored) / len(complete), "accuracy": acc,
                       "scored": len(scored)}

    print()
    print(f"  {'seat':<10}{'coverage':>10}{'accuracy':>10}{'scored':>10}")
    print("  " + "-" * 40)
    for s in seats:
        m = per_seat[s]
        print(f"  {s:<10}{m['coverage']:>9.1%}{m['accuracy']:>10.4f}{m['scored']:>10,}")

    # ---- majority vote as the stand-in ensemble ------------------------------------------
    # The recorded ensemble signal lives in another table under a different grading contract;
    # a majority of THESE seats is graded identically to them, so the comparison is like for
    # like rather than across contracts.
    def majority(k):
        votes = defaultdict(int)
        for s in seats:
            d = by_decision[k][s]
            if d in truth_classes:
                votes[d] += 1
        if not votes:
            return None
        top = max(votes.values())
        winners = sorted([d for d, c in votes.items() if c == top])
        return winners[0] if len(winners) == 1 else winners[0]

    ens_rows = [k for k in complete if majority(k) is not None]
    ens_correct = {k: (majority(k) == truth[k]) for k in ens_rows}
    ens_acc = float(np.mean(list(ens_correct.values())))

    # ---- the oracle, and the null it must beat -------------------------------------------
    def oracle_rate(assign) -> float:
        hit = 0
        for k in ens_rows:
            y = truth[k]
            if any(assign[(k, s)] == y for s in seats if assign[(k, s)] in truth_classes):
                hit += 1
        return hit / len(ens_rows)

    observed_assign = {(k, s): by_decision[k][s] for k in ens_rows for s in seats}
    observed_oracle = oracle_rate(observed_assign)

    # PERMUTATION NULL: shuffle each seat's predictions WITHIN its own (day, horizon) block.
    # Class frequencies survive; only the row-level alignment is destroyed. If the observed
    # oracle does not clear this, "different seats win different windows" is arithmetic.
    day_of = {k: (k[0] // 86_400_000, k[1]) for k in ens_rows}
    blocks = defaultdict(list)
    for k in ens_rows:
        blocks[day_of[k]].append(k)
    rng = np.random.default_rng(RNG_SEED)
    null = np.empty(N_PERMUTATIONS)
    for i in range(N_PERMUTATIONS):
        shuffled = {}
        for s in seats:
            for _, ks in blocks.items():
                vals = [by_decision[k][s] for k in ks]
                rng.shuffle(vals)
                for k, v in zip(ks, vals):
                    shuffled[(k, s)] = v
        null[i] = oracle_rate(shuffled)
    null_mean, null_p95 = float(null.mean()), float(np.percentile(null, 95))
    edge = observed_oracle - null_mean

    # ---- NULL B: JOINT-VECTOR CIRCULAR SHIFT ---------------------------------------------
    # Null A shuffles each seat separately, which DESTROYS the cross-seat correlation and so
    # inflates oracle coverage. It measures redundancy against independent forecasters - a
    # real result, but it cannot test whether the joint disagreement pattern carries outcome
    # information, because the thing being tested is the thing it destroyed.
    #
    # Null B keeps the whole seat vector at a timestamp intact and circularly shifts it
    # against outcomes within (day, horizon). Seat correlations, disagreement structure, class
    # frequencies and temporal persistence all survive; only the association between the joint
    # pattern and the outcome is broken. The shift exceeds the target horizon so a shifted
    # vector cannot land on an overlapping outcome.
    joint = np.empty(N_PERMUTATIONS)
    for i in range(N_PERMUTATIONS):
        shifted = {}
        for (_day, hz), ks in blocks.items():
            if len(ks) < 8:
                for k in ks:
                    for s in seats:
                        shifted[(k, s)] = by_decision[k][s]
                continue
            min_shift = min(max(int(hz) + 1, 2), max(len(ks) - 1, 1))
            off = int(rng.integers(min_shift, max(min_shift + 1, len(ks))))
            for j, k in enumerate(ks):
                src = ks[(j + off) % len(ks)]
                for s in seats:
                    shifted[(k, s)] = by_decision[src][s]
        joint[i] = oracle_rate(shifted)
    joint_mean, joint_p95 = float(joint.mean()), float(np.percentile(joint, 95))
    joint_edge = observed_oracle - joint_mean

    best_seat = max(per_seat, key=lambda s: per_seat[s]["accuracy"])
    print()
    print(f"  majority-vote accuracy         : {ens_acc:.4f}  ({len(ens_rows):,} decisions)")
    print(f"  best fixed seat                : {per_seat[best_seat]['accuracy']:.4f}  ({best_seat})")
    print(f"  ORACLE any-seat (hindsight)    : {observed_oracle:.4f}")
    print()
    print(f"  NULL A independent-seat shuffle: {null_mean:.4f}   p95={null_p95:.4f}   "
          f"edge {edge:+.4f}")
    print("    -> tests REDUNDANCY. Destroys cross-seat correlation, so it cannot test "
          "whether")
    print("       the joint disagreement pattern is informative.")
    print(f"  NULL B joint-vector circ. shift: {joint_mean:.4f}   p95={joint_p95:.4f}   "
          f"edge {joint_edge:+.4f}")
    print("    -> tests INFORMATION. Preserves seat correlations and disagreement structure;")
    print("       breaks only the link between the joint pattern and the outcome.")

    # ---- rescue, harm, and unanimous failure ---------------------------------------------
    wrong = [k for k in ens_rows if not ens_correct[k]]
    rescuable = [k for k in wrong
                 if any(by_decision[k][s] == truth[k] for s in seats)]
    unanimous = [k for k in wrong
                 if not any(by_decision[k][s] == truth[k] for s in seats)]
    right = [k for k in ens_rows if ens_correct[k]]
    harmable = [k for k in right
                if any(committed(k, s) and by_decision[k][s] != truth[k] for s in seats)]

    print()
    print(f"  ensemble WRONG                 : {len(wrong):,}  ({len(wrong)/len(ens_rows):.1%})")
    print(f"    of which rescuable           : {len(rescuable):,}  "
          f"({len(rescuable)/max(len(wrong),1):.1%} of errors)")
    print(f"    unanimous failure            : {len(unanimous):,}  "
          f"({len(unanimous)/max(len(wrong),1):.1%}) - no selector can repair these")
    print(f"  ensemble RIGHT but a seat wrong: {len(harmable):,}  "
          f"({len(harmable)/max(len(right),1):.1%} of correct rows are damageable)")

    # ---- unique wins and error correlation ------------------------------------------------
    print()
    print("  unique rescues (ensemble wrong, THIS seat right, all others wrong):")
    for s in seats:
        uniq = sum(1 for k in wrong
                   if by_decision[k][s] == truth[k]
                   and not any(by_decision[k][o] == truth[k] for o in seats if o != s))
        print(f"    {s:<10}{uniq:>6,}")

    print()
    print("  net disagreement value per seat (a seat can rescue often merely by disagreeing "
          "often):")
    print(f"    {'seat':<9}{'uniq':>7}{'uniq/committed':>16}{'rescues':>9}{'breaks':>8}"
          f"{'net':>7}")
    print("    " + "-" * 56)
    for s in seats:
        scored_n = per_seat[s]["scored"] or 1
        uniq = sum(1 for k in wrong
                   if by_decision[k][s] == truth[k]
                   and not any(by_decision[k][o] == truth[k] for o in seats if o != s))
        resc = sum(1 for k in wrong if by_decision[k][s] == truth[k])
        brk = sum(1 for k in right
                  if committed(k, s) and by_decision[k][s] != truth[k])
        print(f"    {s:<9}{uniq:>7,}{uniq/scored_n:>15.2%}{resc:>9,}{brk:>8,}"
              f"{resc - brk:>+7,}")

    pairs = []
    for i, a in enumerate(seats):
        for b in seats[i + 1:]:
            both = [k for k in ens_rows if committed(k, a) and committed(k, b)]
            if len(both) < 100:
                continue
            ea = np.array([not correct[(k, a)] for k in both], dtype=float)
            eb = np.array([not correct[(k, b)] for k in both], dtype=float)
            if ea.std() > 0 and eb.std() > 0:
                pairs.append((float(np.corrcoef(ea, eb)[0, 1]), a, b, float(np.mean(ea * eb))))
    pairs.sort()
    if pairs:
        print()
        print("  LEAST correlated errors (the only pairs with complementary information):")
        for r, a, b, df in pairs[:5]:
            print(f"    {a:>8} / {b:<8} error corr {r:+.3f}   double-fault {df:.1%}")

    # ---- day-clustered interval on the edge ------------------------------------------------
    days = sorted({day_of[k][0] for k in ens_rows})
    if len(days) >= 10:
        boot = np.empty(2000)
        by_day = defaultdict(list)
        for k in ens_rows:
            by_day[day_of[k][0]].append(k)
        for i in range(2000):
            pick = rng.choice(days, size=len(days), replace=True)
            ks = [k for d in pick for k in by_day[d]]
            hit = sum(1 for k in ks
                      if any(by_decision[k][s] == truth[k] for s in seats))
            boot[i] = hit / max(len(ks), 1)
        lo, hi = np.percentile(boot, [2.5, 97.5])
        print()
        print(f"  oracle 95% CI (day-clustered, {len(days)} days): [{lo:.4f}, {hi:.4f}]")

    # ---- verdict --------------------------------------------------------------------------
    print()
    print("-" * 92)
    if joint_edge > 0.02 and observed_oracle > joint_p95:
        print(f"VERDICT: JOINT PATTERN CARRIES INFORMATION. Oracle {observed_oracle:.3f} beats "
              f"the joint-vector null {joint_mean:.3f} by {joint_edge:+.3f} (p95 "
              f"{joint_p95:.3f}). Seats are still REDUNDANT vs Null A ({edge:+.3f}), but the "
              f"disagreement pattern is not arithmetic. Next: net rescue per seat, then a "
              f"reliability gate - NOT a hard selector.")
    elif edge <= 0.02:
        print(f"VERDICT: SEATS ARE REDUNDANT; JOINT PATTERN NOT INFORMATIVE. Oracle "
              f"{observed_oracle:.3f} sits BELOW the independent-seat null ({null_mean:.3f}, "
              f"{edge:+.3f}) - the seats fail together more than independent forecasters "
              f"would - and does not clear the joint-vector null ({joint_mean:.3f}, "
              f"{joint_edge:+.3f}). Under THIS grading, 'at least one seat was right' is "
              f"arithmetic. NOT established: complementarity under the seats' own first-touch "
              f"contract, on equal row coverage. Prune redundant seats before building any "
              f"selector.")
    elif observed_oracle <= null_p95:
        print(f"VERDICT: NOT SIGNIFICANT. Observed oracle {observed_oracle:.3f} does not clear the "
              f"null's 95th percentile ({null_p95:.3f}).")
    else:
        print(f"VERDICT: REAL COMPLEMENTARITY PRESENT. Oracle {observed_oracle:.3f} exceeds the "
              f"permutation null by {edge:+.3f} (null p95 {null_p95:.3f}). This licenses ONE next "
              f"step - a failure detector predicting P(ensemble wrong) from pre-decision "
              f"features - NOT a selector. A ceiling is not an edge: {len(harmable):,} correct "
              f"rows are damageable, so net rescue must be measured before anything is wired.")
    print("-" * 92)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
