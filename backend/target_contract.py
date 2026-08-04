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


#: Contracts grouped by the QUESTION they answer. These are not interchangeable:
#: a path probability answers "which barrier is touched first", a settlement probability
#: answers "where does price END". They disagree on ~25% of random-walk paths.
PATH_CONTRACTS = frozenset({FIRST_TOUCH_TRIPLE_BARRIER_V1})
SETTLEMENT_CONTRACTS = frozenset({ENDPOINT_SETTLEMENT_V1})

#: What each CONSUMER needs. The point of naming purposes is that a consumer declares the
#: question it is asking, and a probability answering a different question is refused rather
#: than silently accepted because both happen to be floats in [0, 1].
POLYMARKET_SETTLEMENT_EV = "polymarket_settlement_ev"
BINANCE_DIRECTIONAL_EV = "binance_directional_ev"
STOP_TARGET_PLANNING = "stop_target_planning"
HOLD_EXIT_DECISION = "hold_exit_decision"
PATH_EXCURSION_FORECAST = "path_excursion_forecast"

PURPOSE_REQUIREMENTS: dict[str, frozenset] = {
    # Polymarket resolves on where price ENDS relative to the strike. A first-touch
    # probability is a different random variable and may not price it.
    POLYMARKET_SETTLEMENT_EV: SETTLEMENT_CONTRACTS,
    # The Binance EV is (2p-1) * expected_move - costs, which treats p as the probability
    # that the ENDPOINT lands on the predicted side.
    BINANCE_DIRECTIONAL_EV: SETTLEMENT_CONTRACTS,
    # These are genuinely path questions, and the first-touch head is the right input.
    STOP_TARGET_PLANNING: PATH_CONTRACTS,
    HOLD_EXIT_DECISION: PATH_CONTRACTS,
    PATH_EXCURSION_FORECAST: PATH_CONTRACTS,
}


class ContractMisuse(ValueError):
    """A probability was offered to a consumer asking a different question.

    Raised rather than returned so it cannot be ignored by a caller that only checks a
    boolean. The whole class of defect this guards against is silent: both quantities are
    floats in [0, 1], both look like probabilities, and nothing about the value reveals which
    question it answers.
    """


def is_path(contract: str) -> bool:
    return contract in PATH_CONTRACTS


def is_settlement(contract: str) -> bool:
    return contract in SETTLEMENT_CONTRACTS


def assert_admissible(purpose: str, contract: str | None) -> str:
    """Refuse a probability whose contract does not answer the purpose's question."""
    if purpose not in PURPOSE_REQUIREMENTS:
        raise ContractMisuse(
            f"unknown consumer purpose {purpose!r}; declared purposes: "
            f"{sorted(PURPOSE_REQUIREMENTS)}. An undeclared purpose cannot be checked, and "
            f"an unchecked consumer is how a path probability came to price a settlement.")
    if contract is None:
        raise ContractMisuse(
            f"{purpose} requires a probability with a DECLARED target contract; got none. "
            f"An unlabelled probability is not usable evidence - it may answer any question.")
    if contract not in KNOWN_CONTRACTS:
        raise ContractMisuse(f"unknown target contract {contract!r} offered to {purpose}")
    allowed = PURPOSE_REQUIREMENTS[purpose]
    if contract not in allowed:
        raise ContractMisuse(
            f"{purpose} needs one of {sorted(allowed)} but was given {contract!r}. "
            f"These answer different questions and disagree on roughly a quarter of paths; "
            f"substituting one for the other is not an approximation.")
    return contract


class UnknownTargetContract(ValueError):
    """Refuse to grade rather than silently pick a rule. A wrong grade is worse than no grade."""


#: Epoch milliseconds for 2020-01-01. Anything below this magnitude is seconds, not millis.
_MS_FLOOR = 1_577_836_800_000


def kline_open_ms(kline) -> int:
    """Open time in MILLISECONDS, whatever unit the producer used.

    THE DEFECT THIS EXISTS TO REMOVE
        `data_ingestion` stores `"time": k["t"] // 1000` - SECONDS. `PredictionVerifier` builds
        `predicted_at` and `verify_at` from `now_ms` - MILLISECONDS. The grader then compared
        them directly:

            entry_ts < kline_time <= verify_ts
            1.78e12   < 1.78e9                  -> always False

        so the intrabar path was ALWAYS empty, every first-touch grade returned
        GRADE_UNAVAILABLE, and the row was then dropped as INVALID_LATE. The first-touch
        contract could not grade a single production prediction.

        The endpoint path failed the other way: `ts <= verify_at_ms` is true for every second-
        valued bar, so `_as_of_price` selected the NEWEST bar rather than the one at the
        horizon boundary - silently grading at the wrong moment instead of refusing.

        Unit detection by magnitude is safe here because seconds-since-epoch will not reach
        1.578e12 for ~48,000 years.
    """
    if isinstance(kline, dict):
        raw = kline.get("time", kline.get("t", 0))
    else:
        raw = kline
    value = int(raw or 0)
    return value if value >= _MS_FLOOR else value * 1000


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


#: How late a resolution may be before the row is refused rather than graded on a price from
#: the wrong moment. Shared, so the per-model panel and the main verifier cannot drift apart.
MAX_RESOLUTION_LATENESS_MS = 30_000


class GradeResult:
    """One resolution: the outcome AND the exact observation that produced it.

    P1-1/P1-3. Every consumer must take its direction, its price and its timestamp from the
    same object. The per-model panel used to derive direction from a loop-time scalar while the
    main verifier graded from the horizon-end bar, so two panels described the same vote with
    two different random variables and both were labelled "accuracy"."""

    __slots__ = ("direction", "status", "resolution_price", "resolution_event_ts", "contract")

    def __init__(self, direction, status, resolution_price=None, resolution_event_ts=None,
                 contract=None):
        self.direction = direction
        self.status = status
        self.resolution_price = resolution_price
        self.resolution_event_ts = resolution_event_ts
        self.contract = contract

    @property
    def graded(self) -> bool:
        return self.direction is not None

    def __repr__(self) -> str:
        return (f"GradeResult(direction={self.direction!r}, status={self.status!r}, "
                f"price={self.resolution_price!r}, ts={self.resolution_event_ts!r})")


def as_of_close(klines, at_ms: int):
    """(close, open_ms) of the last CLOSED bar at or before `at_ms`, else (None, None).

    Grading from a live `current_price` resolves at LOOP time, not at horizon end."""
    best = None
    best_ms = None
    for k in (klines or []):
        ts = kline_open_ms(k)
        if ts <= int(at_ms) and (k.get("is_closed") is not False
                                 if isinstance(k, dict) else True):
            if best_ms is None or ts > best_ms:
                best, best_ms = k, ts
    if best is None:
        return None, None
    return float(best["close"]), int(best_ms)


def grade(*, contract: str, entry: float, threshold: float, klines,
          entry_ts: int, verify_ts: int) -> GradeResult:
    """THE canonical grader. Every verifier resolves through this and nothing else.

    Returns a GradeResult whose `direction` is None whenever the row must not be graded -
    refusing is always preferred to labelling by a rule the model was not trained under."""
    if contract not in KNOWN_CONTRACTS:
        return GradeResult(None, f"UNKNOWN_CONTRACT:{contract}", contract=contract)

    if contract == ENDPOINT_SETTLEMENT_V1:
        final, final_ts = as_of_close(klines, verify_ts)
        if final is None:
            return GradeResult(None, "GRADE_UNAVAILABLE:no_as_of_price", contract=contract)
        return GradeResult(label_endpoint(entry, final, threshold), "GRADED_ENDPOINT",
                           final, final_ts, contract)

    path = [k for k in (klines or [])
            if int(entry_ts) < kline_open_ms(k) <= int(verify_ts)
            and (k.get("is_closed") is not False if isinstance(k, dict) else True)]
    if not path:
        return GradeResult(None, "GRADE_UNAVAILABLE:no_intrabar_path", contract=contract)
    outcome = label_first_touch(entry, [float(k["high"]) for k in path],
                                [float(k["low"]) for k in path], threshold)
    if outcome == AMBIGUOUS:
        # A single bar touched both barriers, so the target is undefined on this row. Grading
        # it either way manufactures a hit or a miss out of an unknowable ordering.
        return GradeResult(None, "GRADE_UNAVAILABLE:ambiguous_bar", contract=contract)
    last = path[-1]
    return GradeResult(outcome, "GRADED_FIRST_TOUCH", float(last["close"]),
                       kline_open_ms(last), contract)


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

    # --- THE HEAD SPLIT: two questions, never interchangeable ------------------------
    check(is_path(FIRST_TOUCH_TRIPLE_BARRIER_V1) and not is_settlement(
        FIRST_TOUCH_TRIPLE_BARRIER_V1), "first-touch is a PATH contract, not a settlement one")
    check(is_settlement(ENDPOINT_SETTLEMENT_V1) and not is_path(ENDPOINT_SETTLEMENT_V1),
          "endpoint is a SETTLEMENT contract, not a path one")
    check(not (PATH_CONTRACTS & SETTLEMENT_CONTRACTS),
          "the two families are disjoint - no contract may satisfy both questions")

    check(assert_admissible(POLYMARKET_SETTLEMENT_EV, ENDPOINT_SETTLEMENT_V1)
          == ENDPOINT_SETTLEMENT_V1, "settlement EV accepts a settlement probability")
    check(assert_admissible(STOP_TARGET_PLANNING, FIRST_TOUCH_TRIPLE_BARRIER_V1)
          == FIRST_TOUCH_TRIPLE_BARRIER_V1, "stop/target planning accepts a PATH probability")

    for purpose in (POLYMARKET_SETTLEMENT_EV, BINANCE_DIRECTIONAL_EV):
        try:
            assert_admissible(purpose, FIRST_TOUCH_TRIPLE_BARRIER_V1)
            raise AssertionError(f"{purpose} accepted a path probability")
        except ContractMisuse:
            pass
    checks += 1
    print("  PASS  no EV purpose accepts a PATH probability - the substitution that made a "
          "first-touch model price an endpoint question")

    for purpose in (STOP_TARGET_PLANNING, HOLD_EXIT_DECISION, PATH_EXCURSION_FORECAST):
        try:
            assert_admissible(purpose, ENDPOINT_SETTLEMENT_V1)
            raise AssertionError(f"{purpose} accepted a settlement probability")
        except ContractMisuse:
            pass
    checks += 1
    print("  PASS  and no PATH purpose accepts a settlement probability - the guard runs "
          "both ways, so it is not simply refusing everything")

    for bad in (None, "", "made_up_contract"):
        try:
            assert_admissible(POLYMARKET_SETTLEMENT_EV, bad)
            raise AssertionError(f"contract {bad!r} was accepted")
        except ContractMisuse:
            pass
    checks += 1
    print("  PASS  an unlabelled or unknown contract is REFUSED - an unlabelled probability "
          "may answer any question")

    # MISSING and UNKNOWN are different failures and must read differently. Both raise
    # anyway - dropping the None branch is an equivalent mutant behaviourally - but a caller
    # logging the reason gets "no contract was declared" rather than "contract None is not a
    # known name", and only the first tells them what to fix.
    try:
        assert_admissible(POLYMARKET_SETTLEMENT_EV, None)
        raise AssertionError("None was accepted")
    except ContractMisuse as exc:
        missing_reason = str(exc)
    try:
        assert_admissible(POLYMARKET_SETTLEMENT_EV, "made_up_contract")
        raise AssertionError("an unknown contract was accepted")
    except ContractMisuse as exc:
        unknown_reason = str(exc)
    check("DECLARED target contract" in missing_reason,
          "a MISSING contract says the probability was never labelled")
    check("unknown target contract" in unknown_reason and missing_reason != unknown_reason,
          "and an UNKNOWN one says the label is unrecognised - two different fixes")

    try:
        assert_admissible("some_undeclared_purpose", ENDPOINT_SETTLEMENT_V1)
        raise AssertionError("an undeclared purpose was accepted")
    except ContractMisuse:
        checks += 1
        print("  PASS  an UNDECLARED consumer purpose is refused - an unchecked consumer is "
              "how the mismatch happened in the first place")

    check(TRAINING_CONTRACT in PATH_CONTRACTS,
          "the CURRENT training contract is a path contract, so no settlement head exists yet "
          "- every settlement-EV consumer must refuse until one is trained")

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
