"""Re-check trade economics against the ACTUAL fill, not the mid the decision was made on.

THE DEFECT
    `model_consensus` builds its stop and target as offsets from `snapshot.mark_price`, and
    checks "does the target clear the round trip?" using that same mark. But a long enters at the
    best ASK plus slippage and latency drift, and a short at the best BID minus the same. The
    fill simulator applies exactly that, AFTER the decision.

    So by the time the position exists:

        target distance is SMALLER than declared
        stop distance is LARGER than declared
        reward-to-risk is worse than the gate approved
        "target clears round trip" can have become false

    Worked example from the review, and it is not exaggerated:

        mark 60,000   target 60,108 (+18 bps)   assumed costs 12 bps
        actual long entry after spread and slippage: 60,030
        target distance from the real entry: 13 bps

    A "winner" that barely clears its own declared cost, before any adverse latency beyond the
    fixed assumption.

WHAT THIS DOES
    Recomputes both distances from the fill price and REJECTS the entry when the target no longer
    clears the round trip. The target PRICE is kept as decided - it represents the level the model
    forecast as reachable - so re-anchoring it to the fill would quietly extend the trade past
    what the forecast supports.

    python -m backend.binance_paper.post_fill_geometry --selftest
"""
from __future__ import annotations

import argparse
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

LONG, SHORT = "LONG", "SHORT"


def geometry(*, side: str, fill_price: float, decided_entry: float,
             stop_price: float | None, target_price: float | None,
             round_trip_bps: float,
             quantity: float | None = None,
             approved_risk_usd: float | None = None) -> dict:
    """Distances as DECIDED versus as FILLED, plus whether the target still pays."""
    if not fill_price or fill_price <= 0 or not decided_entry or decided_entry <= 0:
        return {"admissible": False, "reason": "non_positive_price"}
    sign = 1.0 if side == LONG else -1.0

    def bps(level, anchor):
        if level is None:
            return None
        return sign * (level - anchor) / anchor * 10_000.0

    decided_target_bps = bps(target_price, decided_entry)
    filled_target_bps = bps(target_price, fill_price)
    decided_stop_bps = bps(stop_price, decided_entry)
    filled_stop_bps = bps(stop_price, fill_price)

    # Entry slippage in bps, signed so positive always means "worse for this side".
    entry_slippage_bps = sign * (fill_price - decided_entry) / decided_entry * 10_000.0

    out = {
        "decided_entry": decided_entry,
        "fill_price": fill_price,
        "entry_slippage_bps": entry_slippage_bps,
        "decided_target_bps": decided_target_bps,
        "filled_target_bps": filled_target_bps,
        "decided_stop_bps": decided_stop_bps,
        "filled_stop_bps": filled_stop_bps,
        "round_trip_bps": round_trip_bps,
        # How much the target beats its own cost by, AFTER the fill. A trade clearing by 1 bps
        # is not the same proposition as one clearing by 6, and the decision-time number cannot
        # show the difference. Reported rather than thresholded: what margin is "enough" is a
        # policy the operator declares, and inventing a constant here would repeat the fixed-
        # haircut mistake this work is removing.
        "target_margin_bps": (filled_target_bps - round_trip_bps)
        if filled_target_bps is not None else None,
        "decided_target_margin_bps": (decided_target_bps - round_trip_bps)
        if decided_target_bps is not None else None,
        "admissible": True,
        "reason": None,
    }
    if filled_target_bps is None:
        out["admissible"] = False
        out["reason"] = "no_target"
        return out
    if filled_target_bps <= round_trip_bps:
        out["admissible"] = False
        out["reason"] = (
            "target_no_longer_clears_costs: "
            f"{filled_target_bps:.1f}bps from the fill vs {round_trip_bps:.1f}bps round trip "
            f"(declared {decided_target_bps:.1f}bps from mark)")
        return out
    if filled_stop_bps is not None and filled_stop_bps >= 0:
        # The stop sits on the wrong side of the actual entry: it would trigger immediately.
        out["admissible"] = False
        out["reason"] = f"stop_through_fill: stop is {filled_stop_bps:.1f}bps favourable"
        return out

    # THE RISK BUDGET, REVALIDATED AGAINST THE ACTUAL FILL.
    #
    # Size is chosen before the fill: qty = risk_budget / stop_fraction, using the decided
    # entry. A worse fill moves the entry without moving the stop, so the REAL distance to
    # the stop grows and the position now risks more than was ever approved:
    #
    #     approved entry 60,000  stop 59,700   ->  50 bps, sized for that
    #     actual fill    60,120  stop 59,700   ->  70 bps, same qty  = 40% over budget
    #
    # This function already computed filled_stop_bps and used it only to detect a stop on the
    # wrong side. The magnitude - the thing the risk limit is actually about - was measured
    # and discarded.
    #
    # Exit cost is charged too: a stop-out pays fees and slippage on the way out, so the loss
    # realised at the stop exceeds the price distance alone. Half the round trip is the exit
    # leg, which is the conservative direction for a risk check.
    if (quantity is not None and approved_risk_usd is not None
            and stop_price is not None and quantity > 0 and approved_risk_usd > 0):
        price_loss = abs(fill_price - stop_price) * quantity
        exit_cost = abs(stop_price) * quantity * (round_trip_bps / 2.0) / 10_000.0
        stop_loss_usd = price_loss + exit_cost
        # The SAME formula at the decided entry, so the two are comparable.
        decided_loss_usd = abs(decided_entry - stop_price) * quantity + exit_cost
        out["filled_stop_loss_usd"] = stop_loss_usd
        out["decided_stop_loss_usd"] = decided_loss_usd
        out["approved_risk_usd"] = approved_risk_usd
        out["risk_budget_overrun_usd"] = stop_loss_usd - approved_risk_usd
        # The sizing engine already includes the exit leg in its approved quantity. This
        # function independently checks whether the actual fill made that bounded geometry
        # worse. A fill at or better than the decided entry carries no extra fill risk;
        # slippage-induced growth beyond the approved budget is rejected.
        if stop_loss_usd > approved_risk_usd + 1e-9:
            out["admissible"] = False
            out["reason"] = (
                f"stop_risk_exceeds_approved_budget: ${stop_loss_usd:.2f} at the stop "
                f"(incl. ${exit_cost:.2f} exit cost) vs ${approved_risk_usd:.2f} approved - "
                f"the fill moved {entry_slippage_bps:.1f}bps from the decision and the "
                f"decided geometry would have risked ${decided_loss_usd:.2f}")
            return out
    return out


def reward_to_risk(result: dict):
    target = result.get("filled_target_bps")
    stop = result.get("filled_stop_bps")
    if target is None or stop is None or stop == 0:
        return None
    return target / abs(stop)


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    # THE REVIEW'S EXAMPLE, exactly.
    result = geometry(side=LONG, fill_price=60_030.0, decided_entry=60_000.0,
                      stop_price=59_640.0, target_price=60_108.0, round_trip_bps=12.0)
    check(abs(result["decided_target_bps"] - 18.0) < 0.1,
          "the target was +18 bps from the mark the decision used")
    check(abs(result["filled_target_bps"] - 13.0) < 0.2,
          "...but only +13 bps from the price actually filled")
    check(abs(result["entry_slippage_bps"] - 5.0) < 0.1,
          "and the 5 bps of entry slippage is reported, not absorbed silently")
    check(abs(result["decided_target_margin_bps"] - 6.0) < 0.1
          and abs(result["target_margin_bps"] - 1.0) < 0.2,
          "the margin over cost collapses from 6 bps at decision time to 1 bps at the fill - "
          "still positive, which is why this case must be REPORTED rather than rejected")
    check(result["admissible"],
          "...so it is admitted, honestly: 13 > 12. Rejecting it would need a margin policy, "
          "and inventing that constant here is the fixed-haircut mistake again")

    # A target that genuinely stops paying after the fill.
    eaten = geometry(side=LONG, fill_price=60_030.0, decided_entry=60_000.0,
                     stop_price=59_640.0, target_price=60_090.0, round_trip_bps=12.0)
    check(abs(eaten["decided_target_bps"] - 15.0) < 0.1,
          "a +15 bps target cleared the 12 bps round trip at decision time")
    check(not eaten["admissible"] and "no_longer_clears" in eaten["reason"],
          "...and is REJECTED after a 5 bps fill leaves it at 10 bps - the gate approved a "
          "trade that no longer exists")

    # A trade with real room survives.
    good = geometry(side=LONG, fill_price=60_030.0, decided_entry=60_000.0,
                    stop_price=59_640.0, target_price=60_600.0, round_trip_bps=12.0)
    check(good["admissible"], "a target with genuine room still passes")
    check(good["filled_target_bps"] < good["decided_target_bps"],
          "...while still reporting the degradation rather than hiding it")
    check(reward_to_risk(good) < good["decided_target_bps"] / abs(good["decided_stop_bps"]),
          "reward-to-risk measured from the fill is WORSE than the gate approved")

    # SHORT is the mirror: filled BELOW the mark is the adverse direction.
    short = geometry(side=SHORT, fill_price=59_970.0, decided_entry=60_000.0,
                     stop_price=60_360.0, target_price=59_892.0, round_trip_bps=12.0)
    check(abs(short["entry_slippage_bps"] - 5.0) < 0.1,
          "a short filled BELOW the mark is 5 bps adverse - the sign convention holds")
    check(abs(short["target_margin_bps"] - 1.0) < 0.2,
          "and its margin collapses 6 bps -> 1 bps identically, so the measure is symmetric")
    eaten_short = geometry(side=SHORT, fill_price=59_970.0, decided_entry=60_000.0,
                           stop_price=60_360.0, target_price=59_910.0, round_trip_bps=12.0)
    check(not eaten_short["admissible"] and "no_longer_clears" in eaten_short["reason"],
          "a short whose target stops paying after the fill is REJECTED too - not long-only")

    lucky = geometry(side=LONG, fill_price=59_970.0, decided_entry=60_000.0,
                     stop_price=59_640.0, target_price=60_108.0, round_trip_bps=12.0)
    check(lucky["entry_slippage_bps"] < 0,
          "a FAVOURABLE fill reports negative slippage")
    check(lucky["filled_target_bps"] > lucky["decided_target_bps"] and lucky["admissible"],
          "...and improves the target distance - the check is not a one-way ratchet")

    through = geometry(side=LONG, fill_price=59_600.0, decided_entry=60_000.0,
                       stop_price=59_640.0, target_price=60_600.0, round_trip_bps=12.0)
    check(not through["admissible"] and through["reason"].startswith("stop_through_fill"),
          "a fill past its own stop is refused instead of opening a position already stopped out")

    check(not geometry(side=LONG, fill_price=0.0, decided_entry=60_000.0, stop_price=1.0,
                       target_price=2.0, round_trip_bps=12.0)["admissible"],
          "a non-positive fill price is refused, never divided by")
    check(not geometry(side=LONG, fill_price=60_000.0, decided_entry=60_000.0,
                       stop_price=59_000.0, target_price=None,
                       round_trip_bps=12.0)["admissible"],
          "a missing target is refused rather than treated as infinitely profitable")

    print(f"\nPOST-FILL GEOMETRY SELFTEST: PASS ({checks} checks)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.parse_args()
    raise SystemExit(selftest())
