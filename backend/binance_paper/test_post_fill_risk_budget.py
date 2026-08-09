"""A worse fill must not leave the position risking more than was approved.

THE DEFECT
    Size is chosen BEFORE the fill: qty = risk_budget / stop_fraction, measured from the
    decided entry. A worse fill moves the entry without moving the stop, so the real distance
    to the stop grows and the position risks more than any gate ever approved:

        approved entry 60,000   stop 59,700  ->  50 bps, sized for that
        actual fill    60,120   stop 59,700  ->  70 bps, same qty  = 40% over budget

    `post_fill_geometry.geometry()` already computed `filled_stop_bps`. It used it only to
    detect a stop on the wrong side of the fill - the magnitude, which is the entire subject
    of a risk limit, was measured and thrown away.

    python -m backend.binance_paper.test_post_fill_risk_budget
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


def main() -> int:
    from binance_paper.post_fill_geometry import geometry

    # Sized at the decided entry for a 50 bps stop and a $30 budget.
    decided, stop, target = 60_000.0, 59_700.0, 60_600.0
    qty = 30.0 / (decided - stop)          # $30 / $300 = 0.1 BTC
    common = dict(side="LONG", decided_entry=decided, stop_price=stop,
                  target_price=target, round_trip_bps=12.0,
                  quantity=qty, approved_risk_usd=30.0)

    # A fill AT the decided price stays inside budget once exit cost is charged.
    at_mark = geometry(fill_price=decided, **common)
    check(at_mark["admissible"] is True,
          f"a fill AT the decided entry is admissible even though it risks "
          f"${at_mark['filled_stop_loss_usd']:.2f} against a $30.00 budget - that gap is a "
          f"SIZING defect (qty = budget/stop_fraction omits the exit cost it sizes for), not "
          f"something the fill caused, and rejecting on it would block flawless fills")
    check(at_mark["decided_stop_loss_usd"] > at_mark["approved_risk_usd"],
          f"the sizing shortfall is REPORTED instead (${at_mark['decided_stop_loss_usd']:.2f} "
          f"decided vs ${at_mark['approved_risk_usd']:.2f} approved) so the sizing policy can "
          f"see it rather than it being hidden by a rejection")

    # The defect case: 20 bps of adverse slippage, same quantity, same stop.
    slipped = geometry(fill_price=60_120.0, **common)
    check(slipped["admissible"] is False,
          "a fill 120 above the decided entry is REJECTED - the stop is now 70 bps away on a "
          "position sized for 50, which is 40% more risk than was ever approved")
    check("stop_risk_exceeds_approved_budget" in (slipped["reason"] or ""),
          f"and says so specifically: {str(slipped['reason'])[:70]}")
    check(slipped["risk_budget_overrun_usd"] > 0,
          f"reporting the overrun (${slipped['risk_budget_overrun_usd']:.2f}) so the size "
          f"reduction needed is computable rather than guessed")

    # Exit cost must be charged: price distance alone understates the realised stop loss.
    no_cost = abs(60_120.0 - stop) * qty
    check(slipped["filled_stop_loss_usd"] > no_cost,
          f"the loss at the stop (${slipped['filled_stop_loss_usd']:.2f}) exceeds the raw "
          f"price distance (${no_cost:.2f}) - a stop-out pays fees and slippage on the way "
          f"out, and ignoring that understates risk in the unsafe direction")

    # A FAVOURABLE fill reduces risk and must stay admissible.
    better = geometry(fill_price=59_940.0, **common)
    check(better["admissible"] is True,
          "a favourable fill stays admissible - the check is one-sided, as a risk limit is")

    # Backward compatibility: callers that pass no budget are unaffected.
    legacy = geometry(side="LONG", fill_price=60_120.0, decided_entry=decided,
                      stop_price=stop, target_price=target, round_trip_bps=12.0)
    check(legacy["admissible"] is True and "filled_stop_loss_usd" not in legacy,
          "a caller supplying no risk budget is UNCHANGED - the check activates only when a "
          "budget is declared, so it cannot silently reject existing callers")

    print("")
    print(f"POST-FILL RISK BUDGET: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
