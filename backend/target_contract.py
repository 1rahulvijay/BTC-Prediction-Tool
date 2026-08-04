"""The ONE definition of what a directional label means. Training and live grading both call it.

THE DEFECT THIS EXISTS TO REMOVE
    `features.build_sequences` labelled with a triple barrier: UP if the upper barrier is touched
    FIRST at any point in the horizon. `PredictionVerifier.check_and_verify` graded by comparing
    the price AT THE HORIZON END against entry. Those are different random variables.

        BTC touches +threshold at minute 2, reverses, settles below entry at minute 5.
        training label   UP        (upper barrier touched first)
        live grade       DOWN      (endpoint below entry)

    A model can predict its training target perfectly and be recorded as wrong. Worse, those
    wrong grades fed confidence recalibration, regime weights, auto-learning, A/B comparison and
    the accuracy panels - so a first-touch model was being corrected by settlement-direction
    feedback.

    Two implementations of "direction" cannot be kept in agreement by discipline. There is now
    one function per contract, both used by both sides, and a model carries the NAME of the
    contract it was trained under. Grading under a contract the artifact does not declare is
    refused, not guessed.

AMBIGUOUS IS NOT NEUTRAL
    When both barriers are touched inside the same bar, intrabar order is unknown. The old code
    broke out of the loop leaving both flags false, so the row fell through to NEUTRAL - which
    says "price went nowhere" about a bar violent enough to touch both barriers. That is close to
    the opposite of what happened. Unknown chronology is now its own state and is excluded from
    directional training rather than relabelled.

    python backend/target_contract.py --selftest
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

UP, DOWN, NEUTRAL, AMBIGUOUS = "UP", "DOWN", "NEUTRAL", "AMBIGUOUS"

#: A model trained under one of these may only be graded under the same one.
FIRST_TOUCH_TRIPLE_BARRIER_V1 = "first_touch_triple_barrier_v1"
ENDPOINT_SETTLEMENT_V1 = "endpoint_settlement_v1"
KNOWN_CONTRACTS = (FIRST_TOUCH_TRIPLE_BARRIER_V1, ENDPOINT_SETTLEMENT_V1)

#: What `build_sequences` produces today. Stamped onto artifacts and predictions so the grader
#: can pick the matching rule instead of assuming one.
TRAINING_CONTRACT = FIRST_TOUCH_TRIPLE_BARRIER_V1

#: One-hot column order used throughout the model layer: [DOWN, NEUTRAL, UP].
CLASS_ORDER = (DOWN, NEUTRAL, UP)


class UnknownTargetContract(ValueError):
    """Refuse to grade rather than silently pick a rule. A wrong grade is worse than no grade."""


def label_first_touch(entry: float, highs, lows, threshold: float) -> str:
    """Which barrier is touched FIRST over the path. AMBIGUOUS when a single bar touches both.

    `highs`/`lows` are the intrabar extremes of each bar in the horizon, in order."""
    if entry <= 0 or threshold <= 0:
        return NEUTRAL
    upper = entry * (1.0 + threshold)
    lower = entry * (1.0 - threshold)
    for high, low in zip(highs, lows):
        touched_up = high >= upper
        touched_down = low <= lower
        if touched_up and touched_down:
            return AMBIGUOUS       # order inside the bar is unknowable from OHLC
        if touched_up:
            return UP
        if touched_down:
            return DOWN
    return NEUTRAL                 # timeout: neither barrier reached


def label_endpoint(entry: float, final: float, threshold: float) -> str:
    """Where price ENDS relative to entry. This is the Polymarket settlement question."""
    if entry <= 0:
        return NEUTRAL
    change = (final - entry) / entry
    if change > threshold:
        return UP
    if change < -threshold:
        return DOWN
    return NEUTRAL


def label(contract: str, *, entry: float, threshold: float,
          highs=None, lows=None, final: float | None = None) -> str:
    """Dispatch on the DECLARED contract. An unknown contract raises."""
    if contract == FIRST_TOUCH_TRIPLE_BARRIER_V1:
        if highs is None or lows is None:
            raise UnknownTargetContract(
                f"{contract} requires the intrabar path (highs/lows); grading it from an "
                f"endpoint price would silently substitute a different target")
        return label_first_touch(entry, highs, lows, threshold)
    if contract == ENDPOINT_SETTLEMENT_V1:
        if final is None:
            raise UnknownTargetContract(f"{contract} requires the final price")
        return label_endpoint(entry, final, threshold)
    raise UnknownTargetContract(
        f"unknown target contract {contract!r}; known: {KNOWN_CONTRACTS}. Refusing to grade - "
        f"a guessed rule is how a first-touch model came to be corrected by endpoint feedback.")


def contracts_agree(entry: float, highs, lows, final: float, threshold: float) -> bool:
    """Do the two contracts give the same answer on this path? Used to MEASURE the disagreement
    rate rather than assume it is small."""
    first = label_first_touch(entry, highs, lows, threshold)
    end = label_endpoint(entry, final, threshold)
    return first == end


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    entry, threshold = 100.0, 0.01      # barriers at 101 / 99

    # --- THE DEFECT, PINNED -----------------------------------------------------------
    # Touch the upper barrier at bar 1, reverse, settle below entry.
    highs = [101.5, 100.5, 100.0, 99.5, 99.0]
    lows = [100.0, 99.5, 99.3, 99.0, 98.7]
    final = 98.8                        # -1.2%, genuinely past the lower threshold
    check(label_first_touch(entry, highs, lows, threshold) == UP,
          "first-touch calls the reversal path UP - the upper barrier was reached first")
    check(label_endpoint(entry, final, threshold) == DOWN,
          "...while endpoint calls the SAME path DOWN")
    check(not contracts_agree(entry, highs, lows, final, threshold),
          "the two contracts DISAGREE on this path - this is the exact case that graded a "
          "correct first-touch prediction as wrong and fed it to recalibration")

    # --- AMBIGUOUS IS NOT NEUTRAL -----------------------------------------------------
    check(label_first_touch(entry, [101.5], [98.5], threshold) == AMBIGUOUS,
          "one bar touching BOTH barriers is AMBIGUOUS, not NEUTRAL")
    check(label_first_touch(entry, [101.5], [98.5], threshold) != NEUTRAL,
          "...explicitly not NEUTRAL - a bar violent enough to touch both is the opposite of "
          "'price went nowhere'")
    check(label_first_touch(entry, [100.2, 100.1], [99.8, 99.9], threshold) == NEUTRAL,
          "a genuinely quiet path IS NEUTRAL, so AMBIGUOUS has not simply replaced it")

    # Ordering: an earlier single-sided touch wins over a later double touch.
    check(label_first_touch(entry, [101.5, 101.5], [99.5, 98.5], threshold) == UP,
          "a clean touch in an earlier bar decides before a later ambiguous bar is reached")
    check(label_first_touch(entry, [100.5, 101.5], [99.5, 98.5], threshold) == AMBIGUOUS,
          "but if the ambiguous bar comes first, the result is AMBIGUOUS")

    # --- ENDPOINT CONTRACT ------------------------------------------------------------
    check(label_endpoint(entry, 101.5, threshold) == UP, "endpoint UP above the threshold")
    check(label_endpoint(entry, 98.5, threshold) == DOWN, "endpoint DOWN below it")
    check(label_endpoint(entry, 100.5, threshold) == NEUTRAL, "endpoint NEUTRAL inside the band")
    check(label_endpoint(entry, 101.0, threshold) == NEUTRAL,
          "the threshold is EXCLUSIVE at the boundary, matching PredictionVerifier's `>`")

    # --- DISPATCH REFUSES RATHER THAN GUESSES -----------------------------------------
    check(label(FIRST_TOUCH_TRIPLE_BARRIER_V1, entry=entry, threshold=threshold,
                highs=highs, lows=lows) == UP, "dispatch routes first-touch correctly")
    check(label(ENDPOINT_SETTLEMENT_V1, entry=entry, threshold=threshold, final=final) == DOWN,
          "dispatch routes endpoint correctly")

    for bad in ("", "endpoint", "first_touch", None, "settlement_v2"):
        try:
            label(bad, entry=entry, threshold=threshold, final=final)
            raise AssertionError(f"unknown contract {bad!r} was accepted")
        except UnknownTargetContract:
            pass
    checks += 1
    print("  PASS  every unknown contract name is REFUSED, never defaulted to a rule")

    try:
        label(FIRST_TOUCH_TRIPLE_BARRIER_V1, entry=entry, threshold=threshold, final=final)
        raise AssertionError("first-touch was graded from an endpoint price")
    except UnknownTargetContract:
        checks += 1
        print("  PASS  first-touch REFUSES to be graded from an endpoint price - the exact "
              "substitution that caused the defect")

    check(TRAINING_CONTRACT in KNOWN_CONTRACTS,
          "the declared training contract is one the grader knows about")
    check(CLASS_ORDER == (DOWN, NEUTRAL, UP),
          "the one-hot column order matches the model layer's [DOWN, NEUTRAL, UP]")

    # --- HOW OFTEN DO THEY DISAGREE? Measured, not assumed. ---------------------------
    rng = np.random.default_rng(0)
    disagreements = 0
    trials = 4000
    for _ in range(trials):
        path = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.004, 5)))
        hi = path * (1 + abs(rng.normal(0, 0.002, 5)))
        lo = path * (1 - abs(rng.normal(0, 0.002, 5)))
        if not contracts_agree(100.0, hi, lo, path[-1], threshold):
            disagreements += 1
    rate = disagreements / trials
    check(rate > 0.05,
          f"on random walks the two contracts disagree {rate:.1%} of the time - the mismatch is "
          f"common, not a corner case")

    print(f"\nTARGET CONTRACT SELFTEST: PASS ({checks} checks)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    raise SystemExit(selftest() if args.selftest else selftest())
