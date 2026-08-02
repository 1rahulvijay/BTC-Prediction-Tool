"""POLY_ACTION_VALUE_V1 - what each action WOULD have returned, priced from the recorded ladder.

WHAT THIS IS, AND WHAT IT DELIBERATELY IS NOT
    It is a counterfactual valuer: given a checkpoint and the round's future quote path, it
    computes the realised net value of every action in the catalogue. Every number is an
    ARITHMETIC consequence of prices that were actually recorded - no probability, no model, no
    forecast.

    It is NOT a predictor. It does not say which action to take next time. That head would need
    a model, and the vintage comparison measured the market's own ask beating both model
    vintages on Brier, log loss, ECE and AUC - so a value engine resting on those models would
    rest on something already known to be worse than the price it is trying to beat.

    Counterfactual values first, predictor later, is also the only order that produces a
    training target the predictor could honestly be fitted to.

THE CEILING ARM, AND WHY IT IS LABELLED SO LOUDLY
    ORACLE_BEST_EXIT sells at the best bid the round ever printed after the checkpoint. That
    requires hindsight and NOBODY CAN TRADE IT. It is here because it bounds the question that
    decides whether exit management is worth building at all:

        if the perfectly-timed exit is still negative after costs,
        no exit model can rescue this lane, and none should be built.

    Any arm carrying `requires_hindsight = True` is a bound, never a strategy. The selection
    rule below refuses to return one.

CATALOGUE
    WAIT                 stand aside; value exactly 0, the bar everything else must clear
    HOLD_TO_SETTLEMENT   buy at ask, hold to resolution
    EXIT_AT_HORIZON      buy at ask, sell at the bid a declared number of seconds later
    ORACLE_BEST_EXIT     buy at ask, sell at the best future bid            (hindsight bound)
    LOCK_COMPLETE_SET    buy both sides; pays $1 whatever happens          (arithmetic)

    SWITCH, ADD, REDUCE and the opposite-side tail are deliberately absent: each needs a
    position-state machine, and shipping half of one would produce values that look comparable
    to these and are not.

    python backend/polymarket_policy/action_value.py --selftest
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import complete_set  # noqa: E402
from execution_cost import entry_fill, exit_fill, settlement_value  # noqa: E402

#: Declared before results. Seconds after the checkpoint at which a fixed-horizon exit sells.
EXIT_HORIZONS_S = (15, 30, 60)
#: An action must beat WAIT by this much to be selected. WAIT is worth exactly 0.
SELECTION_BUFFER = 0.02


class Action(str, Enum):
    WAIT = "WAIT"
    HOLD_TO_SETTLEMENT = "HOLD_TO_SETTLEMENT"
    EXIT_AT_HORIZON = "EXIT_AT_HORIZON"
    ORACLE_BEST_EXIT = "ORACLE_BEST_EXIT"
    LOCK_COMPLETE_SET = "LOCK_COMPLETE_SET"


@dataclass(frozen=True)
class ActionValue:
    action: Action
    net_per_share: float | None      # None means NOT COMPUTABLE, never 0
    detail: str
    requires_hindsight: bool = False
    capacity_known: bool = False
    horizon_s: int | None = None

    @property
    def selectable(self) -> bool:
        """Hindsight arms and uncomputable arms can never be chosen."""
        return (not self.requires_hindsight) and self.net_per_share is not None


def value_actions(*, ask: float, bid: float, opposite_ask: float, won: bool | None,
                  future_bids: list[tuple[float, float]] | None,
                  top_ask_size: float | None = None,
                  opposite_ask_size: float | None = None,
                  shares: float = 1.0) -> list[ActionValue]:
    """Value every action at one checkpoint.

    `future_bids` is [(seconds_after_checkpoint, bid), ...] for the side being bought, strictly
    after the checkpoint. Empty or None means the exit arms are NOT COMPUTABLE - which is
    reported as None, never as a zero that would read as "exiting was worthless"."""
    values: list[ActionValue] = [
        ActionValue(Action.WAIT, 0.0, "stand aside; the bar every other action must clear")
    ]

    entry = entry_fill(ask, shares, top_ask_size=top_ask_size)
    if won is None:
        values.append(ActionValue(Action.HOLD_TO_SETTLEMENT, None,
                                  "round has no official settlement recorded"))
    else:
        values.append(ActionValue(
            Action.HOLD_TO_SETTLEMENT, settlement_value(bool(won), ask),
            "buy at ask, hold to official resolution",
            capacity_known=entry.capacity_known))

    ordered = sorted(future_bids or [], key=lambda pair: pair[0])
    for horizon in EXIT_HORIZONS_S:
        within = [price for offset, price in ordered if offset <= horizon]
        if not within:
            values.append(ActionValue(
                Action.EXIT_AT_HORIZON, None,
                f"no quote recorded within {horizon}s of the checkpoint",
                horizon_s=horizon))
            continue
        # The LAST bid at or before the horizon - the one an exit at that clock would meet.
        proceeds = exit_fill(within[-1], shares).proceeds_per_share
        values.append(ActionValue(
            Action.EXIT_AT_HORIZON, proceeds - entry.cost_per_share,
            f"buy at ask, sell at the bid {horizon}s later", horizon_s=horizon))

    if ordered:
        best = max(price for _, price in ordered)
        values.append(ActionValue(
            Action.ORACLE_BEST_EXIT,
            exit_fill(best, shares).proceeds_per_share - entry.cost_per_share,
            "sells at the best bid the round ever printed - HINDSIGHT, bounds the lane",
            requires_hindsight=True))
    else:
        values.append(ActionValue(Action.ORACLE_BEST_EXIT, None,
                                  "no future quotes recorded", requires_hindsight=True))

    lock = complete_set.evaluate(ask, opposite_ask, requested_shares=shares,
                                 up_depth=top_ask_size, down_depth=opposite_ask_size)
    values.append(ActionValue(
        Action.LOCK_COMPLETE_SET, lock.margin,
        f"pair cost {lock.pair_cost:.4f}; pays $1 whatever settles",
        capacity_known=lock.capacity_known))
    return values


def select(values: list[ActionValue], *, buffer: float = SELECTION_BUFFER) -> ActionValue:
    """Pick the best SELECTABLE action, or WAIT.

    Hindsight arms are excluded before the comparison, not filtered out of the printout
    afterwards - an ordering that cannot accidentally return a value nobody could realise."""
    wait = next(value for value in values if value.action is Action.WAIT)
    candidates = [value for value in values
                  if value.selectable and value.action is not Action.WAIT]
    if not candidates:
        return wait
    best = max(candidates, key=lambda value: value.net_per_share)
    return best if best.net_per_share > wait.net_per_share + buffer else wait


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    # Bought at 0.60. The bid rises to 0.75 at 20s, then falls back to 0.50 by 60s.
    path = [(10.0, 0.62), (20.0, 0.75), (40.0, 0.58), (60.0, 0.50)]
    values = value_actions(ask=0.60, bid=0.58, opposite_ask=0.44, won=True,
                           future_bids=path, top_ask_size=50.0, opposite_ask_size=50.0)
    by_action = {}
    for value in values:
        by_action.setdefault(value.action, []).append(value)

    hold = by_action[Action.HOLD_TO_SETTLEMENT][0]
    check(hold.net_per_share is not None and hold.net_per_share > 0,
          "a winning hold is worth 1 - ask - fee and is positive at a 0.60 ask")

    horizons = {value.horizon_s: value for value in by_action[Action.EXIT_AT_HORIZON]}
    check(abs(horizons[15].net_per_share
              - (exit_fill(0.62, 1).proceeds_per_share
                 - entry_fill(0.60, 1).cost_per_share)) < 1e-12,
          "the 15s exit uses the LAST bid at or before 15s (0.62), not the next one after")
    check(horizons[30].net_per_share > horizons[60].net_per_share,
          "exiting at 30s into the 0.75 spike beats exiting at 60s after it faded")

    ceiling = by_action[Action.ORACLE_BEST_EXIT][0]
    check(ceiling.requires_hindsight and not ceiling.selectable,
          "the best-exit arm is marked hindsight and is NOT selectable")
    check(ceiling.net_per_share >= max(h.net_per_share for h in horizons.values()),
          "the hindsight ceiling is an upper bound on every realisable exit")

    chosen = select(values)
    check(chosen.action is not Action.ORACLE_BEST_EXIT,
          "selection can never return the hindsight arm even when it scores highest")

    # A losing round where every action is bad: WAIT must win.
    losing = value_actions(ask=0.60, bid=0.58, opposite_ask=0.44, won=False,
                           future_bids=[(10.0, 0.30)], top_ask_size=50.0)
    check(select(losing).action is Action.WAIT,
          "when nothing clears WAIT plus the buffer, WAIT is chosen")

    # No recorded future path: exits are NOT COMPUTABLE, not zero.
    blind = value_actions(ask=0.60, bid=0.58, opposite_ask=0.44, won=True, future_bids=[])
    exits = [v for v in blind if v.action is Action.EXIT_AT_HORIZON]
    check(all(value.net_per_share is None for value in exits),
          "with no future quotes the exit arms are None, never 0 - unknown is not worthless")
    check(all(not value.selectable for value in exits),
          "an uncomputable arm cannot be selected")

    unsettled = value_actions(ask=0.60, bid=0.58, opposite_ask=0.44, won=None,
                              future_bids=path)
    check(next(v for v in unsettled
               if v.action is Action.HOLD_TO_SETTLEMENT).net_per_share is None,
          "an unsettled round yields None for the hold arm rather than assuming a loss")

    lock = value_actions(ask=0.39, bid=0.37, opposite_ask=0.54, won=False,
                         future_bids=path)[-1]
    check(lock.action is Action.LOCK_COMPLETE_SET and lock.net_per_share > 0,
          "a cheap pair locks a positive margin even though the round LOST")

    print(f"\nACTION VALUE SELFTEST: PASS ({checks} checks)")
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
