"""What scratches DO and DO NOT change, so the two are never confused again.

WHY THIS FILE EXISTS
    An external write-up reported "Standard Kelly 0.0000 -> Endogenous Kelly 0.1250" and
    attributed it to "removing scratches from the denominator". Two things in that are wrong,
    and both are worth pinning down permanently rather than re-arguing.

    1. REMOVING SCRATCHES IS NOT THE FIX, AND UNDER LOG-GROWTH IT CHANGES NOTHING ANYWAY.
       With N observations of which N_nz are non-zero,

           G_all(f) = (1/N) * SUM log(1 + f*R_i)
                    = (1/N) * SUM_{R_i != 0} log(1 + f*R_i)      because log(1+f*0) = 0
                    = (N_nz / N) * G_nz(f)

       That is a positive constant times G_nz. Scaling an objective does not move its
       maximiser, so deleting zero-return trades cannot change the optimal fraction. The
       0.0000 figure demonstrates that the OLD BINARY formula was defective - it does not
       demonstrate anything about scratch removal.

    2. IT IS NOT "ENDOGENOUS KELLY". In this repository that name means something else
       entirely: our own order walking the Polymarket ask ladder, so the average entry price
       worsens with size and the odds decay endogenously. It has nothing to do with zero-PnL
       trades. The two mechanisms are kept apart by name here.

    The old binary bug WAS real, and it is already fixed. This file guards the fix rather than
    replacing it.

    python backend/test_kelly_scratch_semantics.py
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, str(Path(__file__).resolve().parent))

_OK = True
DAY_MS = 86_400_000


def chk(cond: object, msg: str) -> None:
    global _OK
    print(f"  {'OK  ' if cond else 'FAIL'} {msg}")
    _OK = bool(_OK) and bool(cond)


def _sim():
    from trading_simulator import TradingSimulator

    return TradingSimulator()


def test_scratches_do_not_change_the_log_growth_argmax() -> None:
    print("duplicating scratches does not move the optimal fraction")
    sim = _sim()
    edged = [0.02] * 40 + [-0.02] * 20          # a genuine positive edge
    reference = sim.kelly_maximiser(edged)
    chk(reference > 0.0, f"the reference series has a positive optimum ({reference:.4f})")

    for count in (10, 40, 200, 1000):
        padded = edged + [0.0] * count
        chk(abs(sim.kelly_maximiser(padded) - reference) < 1e-12,
            f"+{count} scratches leaves the argmax at {reference:.4f}")


def test_scratches_do_lower_the_achieved_growth() -> None:
    """They are not free. They are simply not LOSSES."""
    print("scratches still dilute the growth achieved at that fraction")
    sim = _sim()
    edged = [0.02] * 40 + [-0.02] * 20
    f = sim.kelly_maximiser(edged)
    previous = sim.expected_log_growth(f, edged)
    for count in (10, 40, 200):
        growth = sim.expected_log_growth(f, edged + [0.0] * count)
        chk(growth < previous, f"+{count} scratches lowers growth ({growth:.8f} < {previous:.8f})")
        previous = growth


def test_the_scaling_identity_holds_exactly() -> None:
    print("G_all(f) = (N_nonzero / N) * G_nonzero(f), exactly")
    sim = _sim()
    edged = [0.02] * 40 + [-0.02] * 20
    padded = edged + [0.0] * 40
    for f in (0.05, 0.25, 0.5):
        left = sim.expected_log_growth(f, padded)
        right = (len(edged) / len(padded)) * sim.expected_log_growth(f, edged)
        chk(abs(left - right) < 1e-12,
            f"f={f}: {left:.10f} == {right:.10f} (a positive rescaling, not a re-weighting)")


def test_the_old_binary_formula_was_the_actual_defect() -> None:
    """Reproduce the historical bug so the fix cannot silently regress."""
    print("the OLD binary formula is what produced zero")
    sim = _sim()
    wins, losses, scratches = 40, 20, 40
    trades = ([{"net_pnl_usd": +20.0}] * wins
              + [{"net_pnl_usd": -20.0}] * losses
              + [{"net_pnl_usd": 0.0}] * scratches)

    win_rate = wins / len(trades)                       # denominator INCLUDES scratches
    b = 20.0 / 20.0                                     # avg win / avg loss, real losses only
    legacy = max(0.0, ((win_rate * b - (1 - win_rate)) / b) / 2.0)
    chk(legacy == 0.0,
        f"legacy binary half-Kelly on a profitable series = {legacy:.4f} - the real defect")

    returns = [t["net_pnl_usd"] / 1000.0 for t in trades]
    chk(sim.kelly_maximiser(returns) > 0.0,
        f"log-growth finds the edge the binary formula missed "
        f"({sim.kelly_maximiser(returns):.4f})")


def test_endogenous_ladder_kelly_is_a_different_mechanism() -> None:
    print("endogenous ladder Kelly is unrelated to scratches")
    sim = _sim()
    thin = [(0.50, 20), (0.55, 20), (0.62, 20), (0.70, 20), (0.80, 40)]
    endo, exo = sim.endogenous_kelly(p_win=0.60, ask_levels=thin, bankroll=1000.0)
    chk(endo < exo,
        f"it reduces size because OUR ORDER moves the price ({endo:.4f} < {exo:.4f})")
    deep = [(0.50, 10_000_000)]
    endo_deep, exo_deep = sim.endogenous_kelly(p_win=0.60, ask_levels=deep, bankroll=1000.0)
    chk(abs(endo_deep - exo_deep) < 1e-9,
        "with unlimited depth the effect vanishes entirely - a liquidity mechanism, "
        "not a return-classification one")


def test_insufficient_evidence_authorizes_nothing() -> None:
    """The reviewer's open real-money concern, pinned as a test."""
    print("insufficient evidence authorizes ZERO, whatever the probe reports")
    sim = _sim()
    one_day = [(0.05, 1_700_000_000_000 + i) for i in range(60)]
    verdict = sim.assess_kelly(one_day, live_mode=True)
    chk(verdict.point_estimate > 0.0,
        f"the sample still has a positive maximiser ({verdict.point_estimate:.4f})")
    chk(verdict.authorized_fraction == 0.0, "authorized fraction is exactly zero")
    chk(verdict.research_probe_fraction == 0.0, "and in live mode the probe is zero too")
    chk(verdict.lower_bound_passed is False, "the bound is recorded as not passed")


def main() -> int:
    test_scratches_do_not_change_the_log_growth_argmax()
    test_scratches_do_lower_the_achieved_growth()
    test_the_scaling_identity_holds_exactly()
    test_the_old_binary_formula_was_the_actual_defect()
    test_endogenous_ladder_kelly_is_a_different_mechanism()
    test_insufficient_evidence_authorizes_nothing()
    print("\nKELLY SCRATCH SEMANTICS", "PASS" if _OK else "FAIL")
    return 0 if _OK else 1


if __name__ == "__main__":
    raise SystemExit(main())
