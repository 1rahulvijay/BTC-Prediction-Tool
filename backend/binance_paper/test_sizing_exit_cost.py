"""Position size must account for the exit cost it is sizing for.

THE DEFECT (found while writing the post-fill risk check, not from an audit)
    `risk_notional = risk_budget / stop_fraction` solves from the PRICE distance alone. The
    loss actually realised when the stop triggers also pays the exit fee and slippage, so the
    realised loss exceeded the budget on EVERY trade, before any adverse fill:

        equity 30,000, risk 0.1%  ->  budget $30
        stop 50 bps, round trip 12 bps (fee 5 + slippage 1 per leg)

        price-only sizing   qty 0.10000   loss at stop $33.58   ~12% OVER budget
        with the exit leg   qty 0.08929   loss at stop $29.98   within

    A risk limit that is quietly 12% larger than declared is not the limit.

WHY ONLY THE EXIT LEG
    Entry cost is paid out of cash at fill; it is not part of the loss the stop triggering
    causes. Charging the full round trip would under-size by the same reasoning that
    over-sized before.

    python -m backend.binance_paper.test_sizing_exit_cost
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def _loss_at_stop(budget, ref, stop, exit_fraction, denom):
    qty = (budget / denom) / ref
    return qty, qty * (ref - stop) + qty * stop * exit_fraction


def main() -> int:
    budget, ref, stop = 30.0, 60_000.0, 59_700.0
    sf = (ref - stop) / ref
    exitf = (5.0 + 1.0) / 10_000.0

    _, old_loss = _loss_at_stop(budget, ref, stop, exitf, sf)
    qty_new, new_loss = _loss_at_stop(budget, ref, stop, exitf, sf + exitf)

    check(old_loss > budget,
          f"price-only sizing loses ${old_loss:.2f} at the stop against a ${budget:.2f} "
          f"budget - {100 * (old_loss / budget - 1):.0f}% over, on every trade, before any "
          f"adverse fill")
    check(new_loss <= budget + 0.05,
          f"including the exit leg brings it to ${new_loss:.2f}, inside the budget")
    check(qty_new < budget / sf / ref,
          "which necessarily means a SMALLER position - the fix reduces risk, it does not "
          "merely relabel it")

    # The source must divide by the widened denominator, not the raw stop fraction.
    src = (BACKEND / "binance_paper" / "risk_engine.py").read_text(encoding="utf-8")
    idx = src.index("risk_notional = risk_budget /")
    expr = src[idx:idx + 140]
    check("exit_cost_fraction" in expr,
          f"risk_notional divides by a denominator including the exit cost ({expr.splitlines()[0][:70]})")
    check("stop_fraction + exit_cost_fraction" in expr,
          "specifically stop_fraction + exit_cost_fraction - adding it to the numerator or "
          "applying it after would not bound the realised loss")

    # Only the exit leg: a full round trip would under-size.
    i2 = src.index("exit_cost_fraction = (")
    defn = src[i2:i2 + 180]
    check("fee_rate_bps" in defn and "slippage_bps" in defn and "* 2" not in defn,
          "and it is ONE leg of fee+slippage, not the round trip - entry cost is paid at fill "
          "and is not part of the loss the stop causes")

    # It must come from the engine's configured costs, not a constant.
    check("self.engine_config" in defn,
          "taken from engine_config, so changing BTC_BINANCE_PAPER_FEE_BPS resizes positions "
          "rather than leaving sizing on a stale assumption")

    print("")
    print(f"SIZING EXIT COST: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
