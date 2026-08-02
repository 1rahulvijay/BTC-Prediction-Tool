"""BINANCE_ACTION_VALUE_V1 - what each action WOULD have paid on the perpetual, after costs.

WHY THIS IS NOT THE POLYMARKET ENGINE WITH DIFFERENT NUMBERS
    Polymarket has a settlement cliff: a position resolves to $1 or $0 at a known instant, so
    HOLD_TO_SETTLEMENT is a well-defined action. The perpetual has no cliff. Every exit is a
    choice, which means the horizon IS the action, and the interesting quantity is not "which
    side wins" but "over what window does anything clear the round trip".

    So the catalogue is horizons, not sides-and-hold, and the ceiling arms are the maximum
    favourable excursion within each window - which is the same quantity as MFE.

COSTS COME FROM THE ENGINE THAT WOULD ACTUALLY TRADE
    fee and slippage are read from binance_paper.config, and the round trip is 2 x (fee +
    slippage) = 12.0 bps at the declared defaults. This module does not carry its own number.
    backend/binance_paper/strategy_base.py already refuses a take-profit target inside the
    round trip, because two paper strategies were once arithmetically incapable of profit; the
    same constant governs here so the two can never disagree.

FUNDING IS OMITTED, AND THAT BIASES THE RESULT
    Horizons run to 2h and funding settles every 8h, so roughly a quarter of 2h windows cross a
    funding stamp. Funding is not modelled here. In a positive-funding regime that makes LONG
    arms look slightly BETTER than they were and SHORT arms slightly worse. Stated because it
    flatters longs, which is the direction that would otherwise go unnoticed.

WINDOWS DO NOT OVERLAP
    Overlapping windows once let this repository report 11 "independent" expiries that carried
    about one independent observation. The builder strides by the horizon so each window is
    disjoint, and any dispersion figure is computed on those.

    python backend/binance_alpha/action_value.py --selftest
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from binance_paper.config import EngineConfig  # noqa: E402

#: Declared before any result. Holding windows in MINUTES.
HORIZONS_M = (15, 30, 60, 120)


def round_trip_bps(config: EngineConfig | None = None) -> float:
    """2 x (fee + slippage), from the paper engine's own configuration."""
    config = config or EngineConfig.from_env()
    return 2.0 * (float(config.fee_rate_bps) + float(config.slippage_bps))


class Action(str, Enum):
    WAIT = "WAIT"
    LONG_HOLD = "LONG_HOLD"
    SHORT_HOLD = "SHORT_HOLD"
    ORACLE_BEST_EXIT_LONG = "ORACLE_BEST_EXIT_LONG"
    ORACLE_BEST_EXIT_SHORT = "ORACLE_BEST_EXIT_SHORT"


@dataclass(frozen=True)
class ActionValue:
    action: Action
    net_bps: float | None
    horizon_m: int | None
    detail: str
    requires_hindsight: bool = False

    @property
    def selectable(self) -> bool:
        return (not self.requires_hindsight) and self.net_bps is not None


def value_actions(*, entry: float, close_at_horizon: float | None,
                  window_high: float | None, window_low: float | None,
                  horizon_m: int, cost_bps: float) -> list[ActionValue]:
    """Value every action for one entry at one horizon, in basis points of the entry price.

    `window_high` / `window_low` are the extremes STRICTLY INSIDE the window and drive the
    hindsight arms. A None anywhere yields None, never 0 - an unobserved window is not a flat
    one, and zero would read as "this action was worthless" instead of "we cannot say"."""
    values = [ActionValue(Action.WAIT, 0.0, horizon_m,
                          "stand aside; the bar every other action must clear")]
    if entry is None or entry <= 0:
        return values

    def bps(target: float) -> float:
        return (target - entry) / entry * 10_000.0

    if close_at_horizon is None:
        values.append(ActionValue(Action.LONG_HOLD, None, horizon_m, "no bar at the horizon"))
        values.append(ActionValue(Action.SHORT_HOLD, None, horizon_m, "no bar at the horizon"))
    else:
        move = bps(close_at_horizon)
        values.append(ActionValue(Action.LONG_HOLD, move - cost_bps, horizon_m,
                                  f"buy, hold {horizon_m}m, sell at the close"))
        values.append(ActionValue(Action.SHORT_HOLD, -move - cost_bps, horizon_m,
                                  f"sell, hold {horizon_m}m, buy back at the close"))

    values.append(ActionValue(
        Action.ORACLE_BEST_EXIT_LONG,
        None if window_high is None else bps(window_high) - cost_bps, horizon_m,
        "sells at the window HIGH - hindsight; this is MFE and bounds any long exit rule",
        requires_hindsight=True))
    values.append(ActionValue(
        Action.ORACLE_BEST_EXIT_SHORT,
        None if window_low is None else -bps(window_low) - cost_bps, horizon_m,
        "covers at the window LOW - hindsight; bounds any short exit rule",
        requires_hindsight=True))
    return values


def select(values: list[ActionValue], *, buffer_bps: float = 0.0) -> ActionValue:
    """Best SELECTABLE action, or WAIT. Hindsight arms are removed before comparing."""
    wait = next(value for value in values if value.action is Action.WAIT)
    candidates = [value for value in values
                  if value.selectable and value.action is not Action.WAIT]
    if not candidates:
        return wait
    best = max(candidates, key=lambda value: value.net_bps)
    return best if best.net_bps > wait.net_bps + buffer_bps else wait


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    cost = round_trip_bps()
    check(abs(cost - 12.0) < 1e-9,
          "the round trip is 12.0 bps, read from binance_paper.config, not retyped here")

    # Entry 100. Rises to 101 inside the window (100 bps) but closes at 100.2 (20 bps).
    values = value_actions(entry=100.0, close_at_horizon=100.2, window_high=101.0,
                           window_low=99.5, horizon_m=15, cost_bps=cost)
    by_action = {value.action: value for value in values}

    check(abs(by_action[Action.LONG_HOLD].net_bps - (20.0 - cost)) < 1e-9,
          "a long that closes +20 bps nets 20 minus the round trip, i.e. +8 bps")
    check(abs(by_action[Action.SHORT_HOLD].net_bps - (-20.0 - cost)) < 1e-9,
          "the short is the mirror of the move and pays the SAME round trip, not none")
    check(abs(by_action[Action.LONG_HOLD].net_bps
              + by_action[Action.SHORT_HOLD].net_bps + 2 * cost) < 1e-9,
          "long and short together lose exactly two round trips - costs are never netted away")

    ceiling = by_action[Action.ORACLE_BEST_EXIT_LONG]
    check(abs(ceiling.net_bps - (100.0 - cost)) < 1e-9,
          "the long ceiling sells at the window high (+100 bps), not at the close")
    check(ceiling.net_bps > by_action[Action.LONG_HOLD].net_bps,
          "perfect exit timing beats holding to the horizon when price spiked and faded")
    check(not ceiling.selectable and select(values).action is not
          Action.ORACLE_BEST_EXIT_LONG,
          "hindsight arms are excluded BEFORE selection, so they cannot be recommended")

    check(select(values).action is Action.LONG_HOLD,
          "with +8 bps net the long clears WAIT and is selected")
    flat = value_actions(entry=100.0, close_at_horizon=100.05, window_high=100.1,
                         window_low=99.9, horizon_m=15, cost_bps=cost)
    check(select(flat).action is Action.WAIT,
          "a 5 bps move cannot pay a 12 bps round trip, so WAIT wins")

    blind = value_actions(entry=100.0, close_at_horizon=None, window_high=None,
                          window_low=None, horizon_m=15, cost_bps=cost)
    check(all(value.net_bps is None for value in blind
              if value.action is not Action.WAIT),
          "a window with no data yields None everywhere, never 0")
    check(select(blind).action is Action.WAIT,
          "nothing computable means WAIT, not an arbitrary pick")

    print(f"\nBINANCE ACTION VALUE SELFTEST: PASS ({checks} checks)")
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
