"""Kelly sizing must account for the price OUR OWN ORDER pays.

THE FLAW
    Standard Kelly assumes exogenous odds - that placing the bet does not change the payout. For
    Polymarket 5m/15m contracts the books are thin, so buying size walks the ask up. The realised
    entry is worse than the quoted top of book, and the gap GROWS with size. Sizing on the quoted
    price therefore overstates the edge exactly where overstating it costs the most.

THE CORRECTION
        g(f) = p*log(1 + f*b(f)) + (1-p)*log(1 - f)
        b(f) = (1 - q(f)) / q(f)      q(f) = VWAP of the depth consumed by f*bankroll

    b(f) must come from the AVERAGE fill price across all consumed depth. Using the marginal
    price at the last level touched understates the cost of the whole order.

    python backend/tests/test_endogenous_kelly.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_OK = True


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _sim():
    from trading_simulator import TradingSimulator

    return TradingSimulator()


def test_vwap_walks_the_ladder() -> None:
    print("the fill price is the VWAP of consumed depth, not the touch")
    sim = _sim()
    book = [(0.50, 100), (0.52, 100), (0.60, 100)]

    vwap, shares, complete = sim.average_fill_price(book, notional=50.0)
    chk(complete and abs(vwap - 0.50) < 1e-9, f"inside the first level, vwap is the touch ({vwap})")

    vwap, shares, complete = sim.average_fill_price(book, notional=102.0)
    chk(complete and vwap > 0.50, f"crossing into level two raises the average ({vwap:.4f})")
    chk(abs(shares * vwap - 102.0) < 1e-6, "spend reconciles with shares x vwap")

    vwap, shares, complete = sim.average_fill_price(book, notional=10_000.0)
    chk(not complete, "an order larger than the book reports INCOMPLETE rather than a fake price")


def test_thin_book_caps_size_below_exogenous() -> None:
    print("a thin book caps size strictly below the exogenous answer")
    sim = _sim()
    # 60% true chance, quoted at 0.50 - a large apparent edge, on a very thin ladder.
    thin = [(0.50, 20), (0.55, 20), (0.62, 20), (0.70, 20), (0.80, 40)]
    endo, exo = sim.endogenous_kelly(p_win=0.60, ask_levels=thin, bankroll=1000.0)
    print(f"       exogenous (top-of-book) f = {exo:.4f}")
    print(f"       endogenous (walks book) f = {endo:.4f}")
    chk(exo > 0.0, "the exogenous calculation sees a large edge")
    chk(endo < exo, f"the endogenous size is SMALLER ({endo:.4f} < {exo:.4f})")
    chk(endo > 0.0, "but still positive - the edge is real, merely smaller than it looked")


def test_deep_book_converges_to_exogenous() -> None:
    print("a deep book converges to the exogenous answer")
    sim = _sim()
    deep = [(0.50, 1_000_000)]
    endo, exo = sim.endogenous_kelly(p_win=0.60, ask_levels=deep, bankroll=1000.0)
    chk(abs(endo - exo) < 1e-6,
        f"with unlimited depth at one price the two agree ({endo:.4f} vs {exo:.4f})")


def test_impact_can_erase_the_edge_entirely() -> None:
    print("impact can erase a marginal edge completely")
    sim = _sim()
    # 52% chance quoted at 0.50: a thin real edge that a steep ladder consumes.
    steep = [(0.50, 2), (0.75, 50), (0.90, 100)]
    endo, exo = sim.endogenous_kelly(p_win=0.52, ask_levels=steep, bankroll=1000.0)
    print(f"       exogenous f = {exo:.4f}   endogenous f = {endo:.4f}")
    chk(exo > 0.0, "top-of-book pricing still reports an edge")
    chk(endo < exo,
        "walking the book destroys most or all of it - the size that looked available is not")


def test_no_edge_is_never_sized() -> None:
    print("no edge is never sized, at any depth")
    sim = _sim()
    fair = [(0.60, 1_000_000)]
    endo, exo = sim.endogenous_kelly(p_win=0.60, ask_levels=fair, bankroll=1000.0)
    chk(endo == 0.0 and exo == 0.0,
        f"paying exactly fair value has zero growth, so size is zero ({endo}, {exo})")

    overpriced = [(0.70, 1_000_000)]
    endo, exo = sim.endogenous_kelly(p_win=0.60, ask_levels=overpriced, bankroll=1000.0)
    chk(endo == 0.0 and exo == 0.0, "paying above fair value is never sized")


def test_larger_bankroll_takes_a_smaller_fraction() -> None:
    """The same book supports a smaller FRACTION of a larger bankroll."""
    print("the same book supports a smaller fraction of a bigger bankroll")
    sim = _sim()
    book = [(0.50, 100), (0.55, 100), (0.65, 100), (0.80, 200)]
    small, _ = sim.endogenous_kelly(p_win=0.60, ask_levels=book, bankroll=100.0)
    large, _ = sim.endogenous_kelly(p_win=0.60, ask_levels=book, bankroll=100_000.0)
    print(f"       bankroll 100 -> f={small:.4f}   bankroll 100000 -> f={large:.4f}")
    chk(large < small,
        "capacity is set by the BOOK, so a bigger bankroll may risk a smaller share of itself")


def test_degenerate_inputs_are_refused() -> None:
    print("degenerate inputs return zero rather than a number")
    sim = _sim()
    chk(sim.endogenous_kelly(0.6, [], 1000.0) == (0.0, 0.0), "empty book -> zero")
    chk(sim.endogenous_kelly(0.6, [(0.5, 100)], 0.0) == (0.0, 0.0), "zero bankroll -> zero")
    chk(sim.endogenous_kelly(0.0, [(0.5, 100)], 1000.0) == (0.0, 0.0), "zero win probability -> zero")


def main() -> int:
    test_vwap_walks_the_ladder()
    test_thin_book_caps_size_below_exogenous()
    test_deep_book_converges_to_exogenous()
    test_impact_can_erase_the_edge_entirely()
    test_no_edge_is_never_sized()
    test_larger_bankroll_takes_a_smaller_fraction()
    test_degenerate_inputs_are_refused()
    print("\nRESEARCH ONLY: live Kelly sizing remains disabled.")
    print("ENDOGENOUS KELLY", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
