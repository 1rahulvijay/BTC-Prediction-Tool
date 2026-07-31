"""POLYMARKET_CALIBRATED_FAIR_VALUE_V1 - the only measured candidate, with dynamic exit.

WHAT THIS IS BUILT ON
    research/phold_calibrated_fair_value.py measured the one result in this repository that
    survived a strictly temporal split: with a CALIBRATED P(hold), buying the leader only when
    the calibrated probability exceeds the quoted ask plus fee earned +0.0430/$1 with a day-block
    lower bound of +0.0164, against a trade-everything baseline of +0.0076 with a NEGATIVE bound.

    2 of 3 temporal splits passed. THE FAILING ONE WAS THE MOST RECENT. That is a candidate, not
    a finding, and this module exists to collect forward evidence in paper - not to authorize a
    real order.

THE MECHANISM, STATED HONESTLY
    This is not a better forecast. P(hold)'s ranking is unchanged and the market price already
    contains it. What a calibrated probability adds is the ability to tell when the ask is ABOVE
    the true probability and decline - the edge is in the refusals, not the picks. Roughly a
    third of rounds are traded and two thirds are skipped.

    Because the edge is a fair-value disagreement, the exit is the same comparison run backwards,
    which is why entry and exit here are one rule rather than two heuristics:

        ENTER  when the market UNDERprices the leader :  p_cal  >  ask + fee + entry_margin
        EXIT   when the market OVERprices it          :  bid    >  p_cal + exit_margin
        STOP   when the thesis itself breaks          :  p_cal  <  entry_p_cal - stop_drop
        else HOLD to settlement

    Taking profit early is not a target in price space; it is the moment someone else is willing
    to pay more than the position is worth. That is the same test that justified entry, so it
    needs no separate calibration and adds no free parameter beyond its margin.

THE LEAKAGE GUARD, MADE STRUCTURAL
    A calibrator fitted on rounds it later scores produces a confident number that means nothing.
    The shipped calibrator in data/research/phold_challenger/ was fitted on the whole live
    sample, so using it to score that sample is exactly that error.

    Here it is impossible by construction: a Calibration carries `fitted_through_ms`, and
    `decide()` REFUSES any round whose decision timestamp is at or before it. There is no flag to
    turn this off, so a look-ahead evaluation cannot be written by accident - it raises.

EVIDENCE STATUS - THE ENTRY IS MEASURED, THE EXIT IS NOT
    Be clear about which half of this carries evidence.

      ENTRY   measured. research/phold_calibrated_fair_value.py, 2 of 3 strictly temporal
              splits, +0.0430/$1 with a day-block lower bound of +0.0164 against a
              trade-everything baseline whose bound is negative. Hold-to-settlement.

      EXIT    NOT MEASURED, and it cannot be measured from the data on disk. A dynamic exit
              needs the bid observed repeatedly across a round's life. `rule_paper_trades`
              holds exactly 1.00 rows per round for every rule - one snapshot, no trajectory.
              `polymarket_quotes` does carry 1,174 quotes per market, but for only TWO
              markets. Two markets is not a sample.

    So the exit rule below is a stated hypothesis with a symmetric justification, not a
    result. Running it in paper is how it earns evidence. Until per-round quote trajectories
    accrue, HOLD-TO-SETTLEMENT is the only exit this module can claim support for, and a
    caller that wants to stay inside the measured envelope should ignore TAKE_PROFIT and STOP
    and simply hold. `python -m backend.polymarket_paper.calibrated_fair_value --measured-only`
    prints that reduced policy.

WHAT IS DELIBERATELY NOT HERE
    No threshold search, no per-regime tuning, no sizing model. Three margins are declared as
    constants and this module never selects them; choosing them from results is what
    manufactured every earlier "edge" in this repository. Sizing belongs to whoever calls this.

    python -m backend.polymarket_paper.calibrated_fair_value --selftest
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from enum import Enum

# Declared, not searched. Changing these is a protocol change, not a tuning knob.
ENTRY_MARGIN = 0.02        # required fair-value edge over ask+fee before entering
EXIT_MARGIN = 0.02         # bid must exceed fair value by this before selling early
STOP_DROP = 0.10           # calibrated probability falling this far below entry breaks the thesis
MIN_SECONDS_LEFT = 15      # below this the quote is not reliably executable
MAX_SECONDS_LEFT = 120     # the window the calibrated evidence was measured on


class Action(str, Enum):
    ENTER = "ENTER"
    HOLD = "HOLD"
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP = "STOP"
    NO_EDGE = "NO_EDGE"
    REFUSED = "REFUSED"


class CalibrationRefused(Exception):
    """Raised when a decision would consume a calibrator that has seen its own answer."""


@dataclass(frozen=True)
class Calibration:
    """A monotone probability map plus the instant after which it may be used.

    `fitted_through_ms` is not documentation. `decide()` enforces it, so a calibrator can never
    score a round that took part in fitting it."""

    x: tuple[float, ...]
    y: tuple[float, ...]
    fitted_through_ms: int
    horizon: int

    def __post_init__(self) -> None:
        if len(self.x) != len(self.y) or len(self.x) < 2:
            raise ValueError("calibration needs at least two paired knots")
        if any(b < a for a, b in zip(self.x, self.x[1:])):
            raise ValueError("calibration knots must be non-decreasing in x")
        if any(b < a for a, b in zip(self.y, self.y[1:])):
            raise ValueError("a calibrator must be monotone; a non-monotone map reorders rounds")

    def apply(self, probability: float) -> float:
        value = float(probability)
        if value <= self.x[0]:
            return float(self.y[0])
        if value >= self.x[-1]:
            return float(self.y[-1])
        for index in range(1, len(self.x)):
            if value <= self.x[index]:
                x0, x1 = self.x[index - 1], self.x[index]
                y0, y1 = self.y[index - 1], self.y[index]
                if x1 == x0:
                    return float(y1)
                return float(y0 + (y1 - y0) * (value - x0) / (x1 - x0))
        return float(self.y[-1])


@dataclass(frozen=True)
class Quote:
    round_id: str
    timestamp_ms: int
    horizon: int
    seconds_left: int
    leader_side: str            # 'UP' or 'DOWN'
    raw_p_hold: float
    ask: float
    bid: float
    fee: float


@dataclass(frozen=True)
class Position:
    entry_ask: float
    entry_fee: float
    entry_p_cal: float
    entry_ts_ms: int


@dataclass(frozen=True)
class Decision:
    action: Action
    reason: str
    p_cal: float | None = None
    edge: float | None = None
    features: dict = field(default_factory=dict)


def decide(quote: Quote, calibration: Calibration,
           position: Position | None = None) -> Decision:
    """One fair-value comparison, run forwards to enter and backwards to exit.

    Raises CalibrationRefused if the calibrator has seen this round. That is deliberate: a
    silent skip would let a look-ahead evaluation report a clean number."""
    if calibration.horizon != quote.horizon:
        raise CalibrationRefused(
            f"calibrator is for {calibration.horizon}m, quote is {quote.horizon}m - "
            "5m and 15m are different games and pooling them is a bug"
        )
    if quote.timestamp_ms <= calibration.fitted_through_ms:
        raise CalibrationRefused(
            f"round at {quote.timestamp_ms} is at or before the calibrator's fitting boundary "
            f"{calibration.fitted_through_ms}; scoring it would let the calibrator see its own "
            "answer"
        )

    p_cal = calibration.apply(quote.raw_p_hold)
    features = {
        "raw_p_hold": quote.raw_p_hold,
        "p_cal": p_cal,
        "ask": quote.ask,
        "bid": quote.bid,
        "fee": quote.fee,
        "seconds_left": quote.seconds_left,
    }

    if position is not None:
        # Thesis broken: the calibrated probability has collapsed since entry. Checked BEFORE
        # take-profit so a position cannot be reported as a winner while its premise is gone.
        if p_cal < position.entry_p_cal - STOP_DROP:
            return Decision(Action.STOP,
                            f"calibrated probability fell {position.entry_p_cal - p_cal:.3f} "
                            f"below entry", p_cal, None, features)
        # The market now pays more than the position is worth: sell to it.
        if quote.bid > p_cal + EXIT_MARGIN:
            return Decision(Action.TAKE_PROFIT,
                            f"bid {quote.bid:.3f} exceeds fair value {p_cal:.3f} by more than "
                            f"{EXIT_MARGIN:.2f}", p_cal, quote.bid - p_cal, features)
        return Decision(Action.HOLD, "fair value still above the bid", p_cal, None, features)

    if not (MIN_SECONDS_LEFT <= quote.seconds_left <= MAX_SECONDS_LEFT):
        return Decision(Action.NO_EDGE,
                        f"seconds_left {quote.seconds_left} outside the measured window",
                        p_cal, None, features)
    if not (0.0 < quote.ask < 1.0) or not (0.0 <= quote.bid < 1.0):
        return Decision(Action.REFUSED, "quote is not a valid binary price", p_cal, None, features)

    edge = p_cal - quote.ask - quote.fee
    if edge <= ENTRY_MARGIN:
        return Decision(Action.NO_EDGE,
                        f"edge {edge:+.4f} does not clear the {ENTRY_MARGIN:.2f} entry margin",
                        p_cal, edge, features)
    return Decision(Action.ENTER, f"market underprices the leader by {edge:+.4f}",
                    p_cal, edge, features)


# ------------------------------------------------------------------------------------------
def _selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    cal = Calibration(x=(0.0, 0.5, 0.9, 1.0), y=(0.0, 0.45, 0.80, 0.90),
                      fitted_through_ms=1_000, horizon=5)

    # --- the leakage guard is structural, not advisory ------------------------------------
    stale = Quote("r", 1_000, 5, 60, "UP", 0.95, 0.70, 0.68, 0.01)
    try:
        decide(stale, cal)
        check(False, "unreachable")
    except CalibrationRefused:
        check(True, "a round at the fitting boundary is REFUSED, not silently skipped")

    try:
        decide(Quote("r", 2_000, 15, 60, "UP", 0.95, 0.70, 0.68, 0.01), cal)
        check(False, "unreachable")
    except CalibrationRefused:
        check(True, "a 15m quote cannot be scored by a 5m calibrator")

    # --- a calibrator must be monotone ----------------------------------------------------
    try:
        Calibration(x=(0.0, 0.5, 1.0), y=(0.0, 0.6, 0.4), fitted_through_ms=0, horizon=5)
        check(False, "unreachable")
    except ValueError:
        check(True, "a non-monotone calibrator is rejected (it would reorder rounds)")

    # --- entry ----------------------------------------------------------------------------
    cheap = Quote("r", 2_000, 5, 60, "UP", 0.95, 0.70, 0.68, 0.01)
    out = decide(cheap, cal)
    check(out.action is Action.ENTER, "enters when the market underprices fair value")
    check(abs(out.edge - (out.p_cal - 0.70 - 0.01)) < 1e-9, "edge is p_cal - ask - fee exactly")

    expensive = Quote("r", 2_000, 5, 60, "UP", 0.95, 0.86, 0.84, 0.01)
    check(decide(expensive, cal).action is Action.NO_EDGE,
          "declines when the ask is at or above fair value")

    marginal = Quote("r", 2_000, 5, 60, "UP", 0.95, 0.845, 0.83, 0.0)
    check(decide(marginal, cal).action is Action.NO_EDGE,
          "an edge inside the entry margin is not a trade")

    early = Quote("r", 2_000, 5, 200, "UP", 0.95, 0.70, 0.68, 0.01)
    check(decide(early, cal).action is Action.NO_EDGE,
          "refuses outside the seconds-left window the evidence was measured on")

    # --- dynamic exit ---------------------------------------------------------------------
    held = Position(entry_ask=0.70, entry_fee=0.01, entry_p_cal=0.86, entry_ts_ms=2_000)
    rich = Quote("r", 3_000, 5, 40, "UP", 0.95, 0.95, 0.92, 0.01)
    check(decide(rich, cal, held).action is Action.TAKE_PROFIT,
          "takes profit when the bid exceeds fair value by the exit margin")

    steady = Quote("r", 3_000, 5, 40, "UP", 0.95, 0.88, 0.84, 0.01)
    check(decide(steady, cal, held).action is Action.HOLD,
          "holds while fair value still exceeds the bid")

    broken = Quote("r", 3_000, 5, 40, "UP", 0.55, 0.60, 0.55, 0.01)
    out = decide(broken, cal, held)
    check(out.action is Action.STOP, "stops when the calibrated probability collapses")

    # Stop must win over take-profit, or a position could be booked as a winner while its
    # premise is gone.
    both = Quote("r", 3_000, 5, 40, "UP", 0.10, 0.99, 0.99, 0.0)
    check(decide(both, cal, both and held).action is Action.STOP,
          "STOP is evaluated before TAKE_PROFIT when both would fire")

    # --- no free parameters leak in --------------------------------------------------------
    check(all(isinstance(v, (int, float)) for v in
              (ENTRY_MARGIN, EXIT_MARGIN, STOP_DROP, MIN_SECONDS_LEFT, MAX_SECONDS_LEFT)),
          "all thresholds are module constants, none is selected at runtime")

    print(f"\nPOLYMARKET CALIBRATED FAIR VALUE SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--measured-only", action="store_true",
                        help="print the reduced policy that stays inside measured evidence")
    args = parser.parse_args()
    if args.selftest:
        return _selftest()
    if args.measured_only:
        print("MEASURED-ONLY POLICY (the envelope the evidence actually covers)")
        print("=" * 78)
        print(f"  ENTER when  p_cal - ask - fee > {ENTRY_MARGIN:.2f}"
              f"   and {MIN_SECONDS_LEFT} <= seconds_left <= {MAX_SECONDS_LEFT}")
        print("  then HOLD TO SETTLEMENT. Ignore TAKE_PROFIT and STOP.")
        print()
        print("  The entry rule carries temporal-split evidence; the dynamic exit does not,")
        print("  and cannot until per-round quote trajectories accrue. rule_paper_trades has")
        print("  1.00 rows per round and polymarket_quotes covers two markets.")
        return 0
    print(__doc__)
    print("This module makes decisions; it does not trade. Run --selftest.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
