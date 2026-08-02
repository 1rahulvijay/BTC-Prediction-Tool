"""LOCK_COMPLETE_SET - the only action in the catalogue whose value is arithmetic.

WHY IT MATTERS OUT OF PROPORTION TO ITS RARITY
    Holding one YES and one NO of the same market pays exactly $1 at resolution, whichever way
    it settles. So a lock converts an uncertain position into a known payout, and its value
    needs no probability, no model and no forecast - only the two asks and the fees.

    Everything else in this repository is an estimate that could be wrong. This is not. That
    makes it the natural first action to implement and the natural sanity check on the cost
    plumbing: if the lock arithmetic disagrees with the recorded ladder, the plumbing is wrong.

THE CONDITION
    A pair bought outright is profitable when

        up_ask + down_ask + fee(up_ask) + fee(down_ask)  <  1

    Measured across 1,713,160 live snapshots: the mean pair cost is 1.0104, so the typical pair
    LOSES about a cent. Only 3,178 snapshots (0.19%) have the raw pair below $1, and only
    515 (0.03%) survive fees. A lock is real, rare, and small.

WHAT THIS MODULE REFUSES TO DO
    It does not claim the 515 are tradeable. Executing a lock needs BOTH legs filled at those
    asks, and the recorded data has ask-side depth but no way to prove the two fills happen
    together. `available` is therefore a statement about PRICE only, and `shares` is capped by
    the thinner of the two visible ask ladders - a bound, not a promise.

    python backend/polymarket_policy/complete_set.py --selftest
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution_cost import entry_fill  # noqa: E402

#: A complete set of one YES and one NO always redeems for exactly this.
COMPLETE_SET_PAYOUT = 1.0


@dataclass(frozen=True)
class Lock:
    up_ask: float
    down_ask: float
    pair_cost: float             # both asks plus both fees, per pair
    margin: float                # payout minus pair cost; positive means locked profit
    available: bool              # margin > 0 on PRICE alone
    shares: float                # capped by the thinner visible ask ladder
    capacity_known: bool

    @property
    def total_margin(self) -> float:
        return self.margin * self.shares


def evaluate(up_ask: float, down_ask: float, *, requested_shares: float = 1.0,
             up_depth: float | None = None, down_depth: float | None = None) -> Lock:
    """Value an outright complete-set purchase at the two quoted asks."""
    up = entry_fill(up_ask, requested_shares, top_ask_size=up_depth)
    down = entry_fill(down_ask, requested_shares, top_ask_size=down_depth)
    pair_cost = up.cost_per_share + down.cost_per_share
    margin = COMPLETE_SET_PAYOUT - pair_cost
    return Lock(
        up_ask=float(up_ask), down_ask=float(down_ask),
        pair_cost=pair_cost, margin=margin, available=margin > 0.0,
        # A pair needs BOTH legs, so the thinner ladder governs.
        shares=min(up.shares, down.shares),
        capacity_known=up.capacity_known and down.capacity_known,
    )


def lock_from_position(entry_ask: float, opposite_ask: float, *,
                       requested_shares: float = 1.0,
                       opposite_depth: float | None = None) -> Lock:
    """Value locking a position ALREADY held at `entry_ask` by buying the opposite side.

    The entry cost is sunk, and this deliberately includes it: the question an operator asks is
    "does completing this pair leave me ahead overall?", not "is the second leg cheap?". A
    version that ignored the sunk leg would report a profit on a pair that lost money."""
    return evaluate(entry_ask, opposite_ask, requested_shares=requested_shares,
                    down_depth=opposite_depth)


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    # Deep in the money on both sides: the pair costs more than it pays.
    typical = evaluate(0.60, 0.44)
    check(typical.pair_cost > COMPLETE_SET_PAYOUT and not typical.available,
          "a typical pair costs MORE than $1 and is correctly reported unavailable")

    cheap = evaluate(0.39, 0.54)
    check(cheap.available and cheap.margin > 0,
          "a genuinely cheap pair (0.39 + 0.54) locks a positive margin")
    check(abs(cheap.margin - (1.0 - cheap.pair_cost)) < 1e-12,
          "margin is exactly payout minus the all-in pair cost")
    check(cheap.pair_cost > 0.39 + 0.54,
          "fees are INCLUDED in the pair cost - a fee-free lock is not a lock")

    # The boundary case that decides whether a scan reports opportunities that are not there.
    edge = evaluate(0.50, 0.50)
    check(not edge.available,
          "a 0.50/0.50 pair costs exactly $1 BEFORE fees, so after fees it is not available")

    sized = evaluate(0.39, 0.54, requested_shares=100, up_depth=12.0, down_depth=80.0)
    check(sized.shares == 12.0,
          "a pair needs both legs, so the THINNER ladder caps it (12, not 80)")
    check(abs(sized.total_margin - sized.margin * 12.0) < 1e-12,
          "total margin scales with the fillable pair count")
    check(evaluate(0.39, 0.54, requested_shares=5).capacity_known is False,
          "with no ladder recorded, capacity is UNKNOWN rather than assumed fillable")

    held = lock_from_position(0.40, 0.52, requested_shares=1)
    check(held.pair_cost > 0.92,
          "locking a held position counts the SUNK entry leg, not just the new one")

    print(f"\nCOMPLETE SET SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    if parser.parse_args().selftest:
        return selftest()
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
