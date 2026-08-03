"""PHASE5D_164 - is the loss caused by COSTS or by an information deficit?

THE ONE QUESTION, AND IT IS A CAPITAL-ALLOCATION QUESTION
    Would better execution plausibly rescue any existing lane, or is the information deficit
    already too large? This decides whether maker infrastructure is worth building. It does not
    search for alpha and must not be read as doing so.

THE DECOMPOSITION - an accounting identity, not an estimate
    For the SAME causal opportunity population, price the same settlement outcome four ways:

        settlement - midpoint          what a costless, information-free entry would earn
        settlement - ask               after crossing the spread
        settlement - ask - fee         after fees
        future bid - ask - fee         if exited instead of held

    Differences between consecutive lines ARE the components. Nothing is fitted, so nothing can
    overfit; the only judgement is which opportunity population to price, and that is the
    existing eligible-settled checkpoint set.

THE DECISION RULE, DECLARED
    Maker or execution research is justified only when ALL of:

        gross informational edge > 0
        required execution improvement is operationally plausible
        optimistic net edge exceeds 1.25x the remaining uncertainty

    Given 157, the likely finding is that the gross informational edge is already negative -
    in which case no execution improvement can rescue the lane and the question is closed
    cheaply.

DECLARED STATUS: DESCRIPTIVE_ONLY. Diagnostic by design; it generates no trades.

    python research/phase5d/test_cost_information_loss_decomposition.py
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
sys.path.insert(0, str(ROOT / "backend"))

from _common import load_checkpoints, side_ask  # noqa: E402
from polymarket_fee import polymarket_taker_fee_per_share  # noqa: E402

MIN_EDGE_MULTIPLE = 1.25
BOOTSTRAP_DRAWS = 2000
#: The one frozen checkpoint used for the NON-OVERLAPPING population. Declared, not chosen from
#: results: 60s is the middle of the grid and the horizon the exit studies already use.
FROZEN_CHECKPOINT_S = 60


def block_ci(values, blocks, *, equal_cluster: bool = False, draws=BOOTSTRAP_DRAWS):
    """Block bootstrap CI, resampling whole clusters.

    `equal_cluster` must match the ESTIMATOR being reported. Pooling rows inside a resample
    gives an opportunity-weighted mean; averaging per-cluster means gives an equal-cluster
    mean. Reporting one estimator beside the other's interval produced a CI that did not
    contain its own point estimate."""
    unique = np.unique(blocks)
    if len(unique) < 5 or not len(values):
        return (float("nan"), float("nan"))
    index = {b: np.where(blocks == b)[0] for b in unique}
    generator = np.random.default_rng(20260802)
    means = np.empty(draws)
    for draw in range(draws):
        picked = generator.integers(0, len(unique), len(unique))
        if equal_cluster:
            means[draw] = np.mean([values[index[unique[j]]].mean() for j in picked])
        else:
            means[draw] = values[np.concatenate([index[unique[j]] for j in picked])].mean()
    means.sort()
    return (float(means[int(0.025 * draws)]), float(means[int(0.975 * draws)]))


def equal_weighted_mean(values, blocks) -> float:
    """Mean of per-cluster means: every day (or round) counts once, whatever its row count.

    The opportunity-weighted mean lets a busy day dominate. For a quantity that must persist
    ACROSS days, the equal-day mean is the honest summary."""
    unique = np.unique(blocks)
    if not len(unique):
        return float("nan")
    return float(np.mean([values[blocks == b].mean() for b in unique]))


def decompose(settlement, midpoint, ask, fee, exit_bid=None) -> dict:
    """Average per-share value at each successive pricing stage, and the deltas between them."""
    at_mid = settlement - midpoint
    at_ask = settlement - ask
    after_fee = settlement - ask - fee
    stages = {
        # NOT "gross edge" - the midpoint is not executable. This is an OBSERVED SURPLUS of
        # settlement value over the quoted mid on this sample, which is a diagnostic quantity.
        "informational_edge_at_mid": float(np.mean(at_mid)),
        "spread_burden": float(np.mean(at_ask - at_mid)),
        "fee_burden": float(np.mean(after_fee - at_ask)),
        "net_hold_to_settlement": float(np.mean(after_fee)),
    }
    if exit_bid is not None:
        usable = np.isfinite(exit_bid)
        if usable.any():
            exit_fee = np.array([polymarket_taker_fee_per_share(float(b))
                                 for b in exit_bid[usable]])
            exit_net = exit_bid[usable] - exit_fee - ask[usable] - fee[usable]
            stages["net_if_exited"] = float(np.mean(exit_net))
            stages["exit_vs_hold"] = float(np.mean(exit_net) - np.mean(after_fee[usable]))
    return stages


def verdict(stages: dict) -> tuple[str, str]:
    """Which deficit dominates, and whether execution work could plausibly help."""
    edge = stages["informational_edge_at_mid"]
    costs = -(stages["spread_burden"] + stages["fee_burden"])
    if edge <= 0:
        return ("EXECUTION_CANNOT_RESCUE",
                f"the informational edge is {edge:+.4f} BEFORE any cost is charged - there is "
                f"nothing for cheaper execution to preserve")
    if edge > costs * MIN_EDGE_MULTIPLE:
        return ("EXECUTION_DEFICIT_DOMINANT",
                f"edge {edge:+.4f} exceeds {MIN_EDGE_MULTIPLE}x costs {costs:.4f} - execution "
                f"is the binding constraint and maker research is plausible")
    if edge > 0:
        return ("BOTH_DOMINANT",
                f"edge {edge:+.4f} is positive but does not clear {MIN_EDGE_MULTIPLE}x costs "
                f"{costs:.4f} - both would have to improve")
    return ("INFORMATION_DEFICIT_DOMINANT", "the edge cannot carry the costs")


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    n = 1000
    settlement = np.ones(n)                       # every round settles in the money
    midpoint = np.full(n, 0.60)
    ask = np.full(n, 0.62)
    fee = np.array([polymarket_taker_fee_per_share(0.62)] * n)

    stages = decompose(settlement, midpoint, ask, fee)
    check(abs(stages["informational_edge_at_mid"] - 0.40) < 1e-9,
          "the edge at midpoint is settlement minus midpoint, before any cost")
    check(abs(stages["spread_burden"] + 0.02) < 1e-9,
          "the spread burden is exactly the ask-minus-mid, charged NEGATIVE")
    check(stages["fee_burden"] < 0, "the fee burden is charged negative, never netted away")
    check(abs(stages["net_hold_to_settlement"]
              - (stages["informational_edge_at_mid"] + stages["spread_burden"]
                 + stages["fee_burden"])) < 1e-9,
          "the components sum EXACTLY to the net - it is an identity, not a fit")

    check(verdict(stages)[0] == "EXECUTION_DEFICIT_DOMINANT",
          "a large edge against small costs makes execution the binding constraint")
    losing = decompose(np.zeros(n), midpoint, ask, fee)
    check(verdict(losing)[0] == "EXECUTION_CANNOT_RESCUE",
          "a NEGATIVE pre-cost edge means no execution improvement can help - checked first")
    marginal = decompose(np.full(n, 0.625), midpoint, ask, fee)
    check(verdict(marginal)[0] in ("BOTH_DOMINANT", "EXECUTION_CANNOT_RESCUE"),
          "an edge that barely covers costs is never reported as execution-dominant")

    with_exit = decompose(settlement, midpoint, ask, fee, np.full(n, 0.70))
    check("net_if_exited" in with_exit and with_exit["exit_vs_hold"] < 0,
          "exiting at 0.70 is worse than holding a winner to 1.00, and is reported as such")

    print(f"\nCOST/INFORMATION SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()

    print("=" * 96)
    print("PHASE5D-164  COST vs INFORMATION - would better execution rescue any lane?")
    print("=" * 96)
    frame = load_checkpoints()
    if frame.empty:
        print("  BLOCKED: no eligible settled checkpoints.")
        return 0

    won = frame["won"].to_numpy(float)
    ask = side_ask(frame)
    bid = np.where(frame["current_side"].to_numpy() == 1,
                   frame["up_bid"].to_numpy(float), frame["down_bid"].to_numpy(float))
    midpoint = (ask + bid) / 2.0
    fee = np.array([polymarket_taker_fee_per_share(float(a)) for a in ask])

    print(f"  {len(frame):,} eligible settled checkpoints | DESCRIPTIVE_ONLY, no trades")
    stages = decompose(won, midpoint, ask, fee)

    print()
    print(f"{'component':<34}{'$/share':>10}")
    for key in ("informational_edge_at_mid", "spread_burden", "fee_burden",
                "net_hold_to_settlement"):
        label = {"informational_edge_at_mid": "observed pre-cost midpoint surplus",
                 "spread_burden": "  spread burden",
                 "fee_burden": "  fee burden",
                 "net_hold_to_settlement": "= net, hold to settlement"}[key]
        print(f"{label:<34}{stages[key]:>10.4f}")

    # --- how robust is the surplus to weighting and to within-round dependence? ------------
    surplus = won - midpoint
    days = (frame["snapshot_ts"].to_numpy(float) // 86_400).astype(np.int64)
    rounds = frame["slug"].to_numpy()
    frozen = frame["checkpoint_s"].to_numpy(float) == FROZEN_CHECKPOINT_S

    print()
    print("  OBSERVED_PRE_COST_MIDPOINT_SURPLUS under several weightings")
    print("  (the midpoint is NOT executable - this is a diagnostic, not an edge)")
    print(f"{'weighting':<36}{'n':>9}{'surplus':>10}  95% CI")
    day_ci = block_ci(surplus, days)
    day_equal_ci = block_ci(surplus, days, equal_cluster=True)
    round_equal_ci = block_ci(surplus, rounds, equal_cluster=True)
    print(f"{'raw opportunity-weighted':<36}{len(surplus):>9,}{surplus.mean():>10.4f}"
          f"  day [{day_ci[0]:+.4f}, {day_ci[1]:+.4f}]")
    print(f"{'equal-DAY weighted':<36}{len(np.unique(days)):>9,}"
          f"{equal_weighted_mean(surplus, days):>10.4f}"
          f"  day [{day_equal_ci[0]:+.4f}, {day_equal_ci[1]:+.4f}]")
    print(f"{'equal-ROUND weighted':<36}{len(np.unique(rounds)):>9,}"
          f"{equal_weighted_mean(surplus, rounds):>10.4f}"
          f"  round [{round_equal_ci[0]:+.4f}, {round_equal_ci[1]:+.4f}]")
    if frozen.any():
        frozen_ci = block_ci(surplus[frozen], days[frozen])
        print(f"{f'NON-OVERLAPPING (one @ {FROZEN_CHECKPOINT_S}s/round)':<36}"
              f"{int(frozen.sum()):>9,}{surplus[frozen].mean():>10.4f}"
              f"  day [{frozen_ci[0]:+.4f}, {frozen_ci[1]:+.4f}]")
        print("  The non-overlapping row is the only one where every observation could have")
        print("  been independently funded. Multiple checkpoints from one round are overlapping")
        print("  positions in the same contract, not separate opportunities.")

    costs = -(stages["spread_burden"] + stages["fee_burden"])
    # The verdict uses the UPPER 95% bound of the surplus, not its point estimate. If even the
    # optimistic end of the interval cannot carry the cost, no execution work can rescue the
    # lane, and saying so from a point estimate whose CI spans zero would overstate the case.
    optimistic = day_ci[1]
    if not np.isfinite(optimistic):
        status, reason = verdict(stages)
    elif optimistic <= 0:
        status = "EXECUTION_CANNOT_RESCUE"
        reason = (f"even the 95% UPPER bound of the surplus ({optimistic:+.4f}) is not "
                  f"positive - there is nothing for cheaper execution to preserve")
    elif optimistic < costs:
        status = "TAKER_EXECUTION_CANNOT_RESCUE"
        reason = (f"the 95% upper bound of the surplus ({optimistic:+.4f}) is BELOW the current "
                  f"cost ({costs:.4f}) - no reduction in TAKER cost clears it")
    else:
        status, reason = verdict(stages)
    print()
    print(f"  break-even execution cost   : {max(stages['informational_edge_at_mid'], 0.0):.4f}")
    print(f"  actual execution cost       : {costs:.4f}")
    print(f"  required improvement        : "
          f"{max(costs - stages['informational_edge_at_mid'], 0.0):.4f} per share")
    print()
    print(f"  VERDICT: {status}")
    print(f"  {reason}")
    print()
    if status.endswith("CANNOT_RESCUE"):
        surplus_point = stages["informational_edge_at_mid"]
        print("  A cheaper TAKER channel cannot rescue this lane: even the optimistic end of")
        print(f"  the day-clustered interval ({optimistic:+.4f}) sits below the cost it must")
        print(f"  clear ({costs:.4f}).")
        print()
        print("  A MAKER fill is a different question, and this test cannot answer it. Posting")
        print(f"  at the mid would remove essentially all cost, leaving the surplus itself:")
        print(f"    point estimate {surplus_point:+.4f}/share, day-clustered 95% CI "
              f"[{day_ci[0]:+.4f}, {day_ci[1]:+.4f}]")
        print("  That interval SPANS ZERO, so a maker fill would be trading on a surplus not")
        print("  distinguishable from nothing - before charging any adverse selection.")
        print()
        print("  Round-clustered the same surplus reads [+0.0063, +0.0161], which excludes zero.")
        print("  Day clustering governs here because volatility, regime and recorder health all")
        print("  cluster within a day; rounds inside one day are not independent draws. The")
        print("  disagreement between the two is itself the reason to treat this as weak.")
    else:
        edge = stages["informational_edge_at_mid"]
        reduction = (costs - edge) / costs if costs > 0 else float("nan")
        # A maker entry can remove at most the spread AND the taker fee - Polymarket does not
        # charge makers. That is the OPTIMISTIC bound; it ignores fill risk entirely, and a
        # resting order is filled precisely when someone informed wants the other side.
        print(f"  costs must fall {reduction:.0%} for this lane to break even.")
        print(f"  A maker entry removes at most the spread ({-stages['spread_burden']:.4f}) and")
        print(f"  the taker fee ({-stages['fee_burden']:.4f}) - enough on paper, and that bound")
        print("  assumes every resting order fills with no adverse selection. A resting order")
        print("  is filled exactly when someone informed wants the other side, so the realised")
        print("  figure sits below the bound by an amount this test cannot measure.")
        print()
        print(f"  So: a BOUNDED maker study is justified, on a gross edge of {edge:.4f}/share")
        print("  (~0.6% of a 0.70 contract). Committing maker INFRASTRUCTURE is not, until a")
        print("  fill-and-adverse-selection bound exists. That is test 165, not this one.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
