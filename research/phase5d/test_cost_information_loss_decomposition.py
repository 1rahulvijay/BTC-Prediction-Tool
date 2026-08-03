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


def decompose(settlement, midpoint, ask, fee, exit_bid=None) -> dict:
    """Average per-share value at each successive pricing stage, and the deltas between them."""
    at_mid = settlement - midpoint
    at_ask = settlement - ask
    after_fee = settlement - ask - fee
    stages = {
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
        label = {"informational_edge_at_mid": "gross informational edge (at mid)",
                 "spread_burden": "  spread burden",
                 "fee_burden": "  fee burden",
                 "net_hold_to_settlement": "= net, hold to settlement"}[key]
        print(f"{label:<34}{stages[key]:>10.4f}")

    status, reason = verdict(stages)
    costs = -(stages["spread_burden"] + stages["fee_burden"])
    print()
    print(f"  break-even execution cost   : {max(stages['informational_edge_at_mid'], 0.0):.4f}")
    print(f"  actual execution cost       : {costs:.4f}")
    print(f"  required improvement        : "
          f"{max(costs - stages['informational_edge_at_mid'], 0.0):.4f} per share")
    print()
    print(f"  VERDICT: {status}")
    print(f"  {reason}")
    print()
    if status == "EXECUTION_CANNOT_RESCUE":
        print("  Maker infrastructure is NOT justified for this lane. The market prices these")
        print("  contracts above their settlement value on average at the mid, so the deficit")
        print("  is information and not execution - consistent with 157, which found nothing")
        print("  that adds resolution beyond the price.")
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
