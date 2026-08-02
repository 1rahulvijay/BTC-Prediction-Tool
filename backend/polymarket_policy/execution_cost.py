"""What an action actually costs, in executable terms rather than midpoint terms.

THE ONE RULE
    You buy at the ASK and sell at the BID. Every retracted study in this repository that
    quoted a midpoint was quoting a price nobody could trade. Fees come from the canonical
    polymarket_fee module so a rate can never be retyped slightly differently in two places.

CAPACITY IS ASYMMETRIC IN THE RECORDED DATA, AND THAT IS NOT A DETAIL
    pm_round_snapshots stores `up_top_ask_size` and cumulative ask-side depth `d1/d2/d5`
    (size within 1c/2c/5c of the top ask). It stores NO bid-side size. So:

        ENTRY capacity  measurable - the ladder is there
        EXIT  capacity  UNKNOWN    - only the bid PRICE was recorded, never its size

    A position whose exit capacity cannot be established is not a position you can size. This
    module therefore returns capacity as an explicit `EXIT_CAPACITY_UNRECORDED` marker rather
    than defaulting to "one share is always fillable", which is the assumption that quietly
    turns an unexitable position into a backtest profit.

    python backend/polymarket_policy/execution_cost.py --selftest
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_fee import polymarket_taker_fee_per_share  # noqa: E402

#: Returned wherever exit size cannot be established from the recorded data.
EXIT_CAPACITY_UNRECORDED = None


@dataclass(frozen=True)
class Fill:
    """The executable result of one intent, per share."""

    price: float                 # what each share actually costs or returns
    fee: float                   # taker fee per share
    shares: float                # how many shares the visible ladder supports
    capacity_known: bool         # False when the recorded data cannot establish size

    @property
    def cost_per_share(self) -> float:
        """Total outlay per share when buying. Negative of proceeds when selling."""
        return self.price + self.fee

    @property
    def proceeds_per_share(self) -> float:
        """What a seller nets per share after the taker fee."""
        return self.price - self.fee


def entry_fill(ask: float, requested_shares: float, *, top_ask_size: float | None = None,
               depth_within_1c: float | None = None) -> Fill:
    """Buy at the ask, capped by the visible ladder.

    Depth within 1c is used when present because a taker crossing one cent is still a
    realistic fill; beyond that the price is no longer the quoted ask and pretending otherwise
    understates cost."""
    ask = float(ask)
    available = depth_within_1c if depth_within_1c is not None else top_ask_size
    known = available is not None
    shares = min(float(requested_shares), float(available)) if known \
        else float(requested_shares)
    return Fill(price=ask, fee=polymarket_taker_fee_per_share(ask),
                shares=max(0.0, shares), capacity_known=known)


def exit_fill(bid: float, requested_shares: float) -> Fill:
    """Sell at the bid. Capacity is UNKNOWN because bid-side size was never recorded.

    `shares` echoes the request so a caller can still compute a per-share value, but
    `capacity_known` is False and any study sizing on this must say so."""
    bid = float(bid)
    return Fill(price=bid, fee=polymarket_taker_fee_per_share(bid),
                shares=float(requested_shares), capacity_known=False)


def round_trip_cost(ask: float, bid: float) -> float:
    """Cost of entering and leaving immediately, per share, charged ONCE each way.

    The spread is crossed once on the way in and once on the way out; double-counting it is a
    mistake this repository has already made and corrected."""
    return (float(ask) - float(bid)) + polymarket_taker_fee_per_share(ask) \
        + polymarket_taker_fee_per_share(bid)


def settlement_value(won: bool, ask: float) -> float:
    """Net per share of buying at `ask` and holding to settlement."""
    return (1.0 if won else 0.0) - float(ask) - polymarket_taker_fee_per_share(ask)


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    fill = entry_fill(0.60, 100, top_ask_size=40.0)
    check(fill.shares == 40.0 and fill.capacity_known,
          "entry is capped by the visible ask ladder, not by what was requested")
    check(abs(fill.fee - polymarket_taker_fee_per_share(0.60)) < 1e-12,
          "the fee comes from the canonical module, never a constant retyped here")
    check(entry_fill(0.60, 100, top_ask_size=5.0, depth_within_1c=25.0).shares == 25.0,
          "depth within 1c is used when present - a taker crossing a cent still fills")

    out = exit_fill(0.58, 100)
    check(out.capacity_known is False,
          "exit capacity is UNKNOWN: bid-side size was never recorded, so it is not asserted")
    check(abs(out.proceeds_per_share - (0.58 - polymarket_taker_fee_per_share(0.58))) < 1e-12,
          "a seller receives the bid MINUS the fee, not the bid")

    trip = round_trip_cost(0.60, 0.58)
    check(abs(trip - (0.02 + polymarket_taker_fee_per_share(0.60)
                      + polymarket_taker_fee_per_share(0.58))) < 1e-12,
          "the round trip crosses the spread ONCE, plus one fee each way")
    check(trip < 2 * 0.02 + 0.1,
          "the spread is not double-counted - the error this repository already made once")

    check(abs(settlement_value(True, 0.60) - (0.40 - polymarket_taker_fee_per_share(0.60)))
          < 1e-12, "a winning hold returns 1 - ask - fee")
    check(settlement_value(False, 0.60) < 0,
          "a losing hold costs the full ask plus the fee")
    check(entry_fill(0.60, 100).capacity_known is False,
          "with no ladder recorded, capacity is reported UNKNOWN rather than assumed")

    print(f"\nEXECUTION COST SELFTEST: PASS ({checks} checks)")
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
