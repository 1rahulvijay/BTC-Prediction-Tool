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
import dataclasses
import hashlib
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
#: How the Polymarket BTC up/down market ACTUALLY resolves: a direct comparison of the final
#: price against the strike under the market's own rule, two outcomes, no neutral band.
#:
#: ENDPOINT_SETTLEMENT_V1 answers "where does price end, allowing for a flat zone" using an
#: ADAPTIVE volatility threshold. That flat zone does not exist on the venue. Every row inside
#: the band is a real UP or DOWN resolution that the three-class contract calls NEUTRAL, so a
#: probability trained under it is systematically not the probability the market pays out on -
#: and the gap is widest in exactly the quiet regimes where the band is widest relative to the
#: move.
#: WHAT THIS ACTUALLY PREDICTS
#:
#:     P(exchange close after h minutes  >=  exchange close at the DECISION time)
#:
#: That is the sign of a rolling h-minute return. It is a real, useful quantity - it is simply
#: not the question Polymarket settles. The name says the quantity so no future reader has to
#: infer it from the contract family, which is how "binary endpoint" came to be read as
#: "Polymarket settlement" in the first place.
#:
#: WRONG REFERENCE POINT for Polymarket - the defect that matters most about this contract.
#:
#:     build_sequences labels   final > closes[i]        (the DECISION-time price)
#:     the venue resolves       final >= round anchor    (fixed at the round's wall-clock open)
#:
#: Those are different questions, and the label is not merely noisier - it is INVERTED on a
#: large share of rounds. Worked example: anchor 100, minute-6 price 104, settlement 102.
#: The venue pays UP (102 >= 100); this label says DOWN (102 < 104).
#:
#: Measured on 40k simulated 15m rounds at realistic per-bar volatility, disagreement with the
#: venue's outcome by checkpoint:
#:
#:     0m  (15m left)   0.0%     <- only correct because decision price IS the anchor
#:     3m  (12m left)  14.8%
#:     6m  ( 9m left)  21.5%
#:     9m  ( 6m left)  28.2%
#:     12m ( 3m left)  35.3%     <- worst exactly where seconds-left information is worth most
#:
#: A head fitted on this cannot be corrected by recalibration; it is answering "will price rise
#: from here" while the market asks "will price close above the anchor". Round-aligned labels
#: (fixed anchor, fixed wall-clock end, one outcome shared by every checkpoint in the round)
#: are required before any artifact may claim to price this market.
#:
#: What we can build TODAY: the market's comparison rule applied to EXCHANGE CLOSES.
#: `build_sequences` reads closes[i] and closes[i+h]; the venue settles on a Chainlink
#: stream. The rule is right, the price series is not, so the name says so. Calling this
#: `polymarket_binary_settlement` asserted a data source the pipeline never touched, and
#: nothing downstream could have caught the claim.
ROLLING_EXCHANGE_RETURN_SIGN_V1 = "rolling_exchange_return_sign_v1"

#: RESERVED. The real thing: the venue's rule applied to the venue's own oracle values, with
#: the window start/end observations recorded. No labels and no artifact exist under this yet,
#: so every consumer requiring it still REFUSES - which is the honest state, not a regression.
POLYMARKET_BINARY_SETTLEMENT_V1 = "polymarket_binary_settlement_v1"

KNOWN_CONTRACTS = (FIRST_TOUCH_TRIPLE_BARRIER_V1, ENDPOINT_SETTLEMENT_V1,
                   ROLLING_EXCHANGE_RETURN_SIGN_V1, POLYMARKET_BINARY_SETTLEMENT_V1)

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
#: Kept SEPARATE from SETTLEMENT_CONTRACTS. Both answer "where does price end", but only this
#: one answers it the way the venue pays. Folding it into the three-class set would let a
#: banded probability price a binary market again, which is the defect this contract exists
#: to close.
BINARY_SETTLEMENT_CONTRACTS = frozenset({POLYMARKET_BINARY_SETTLEMENT_V1})
#: Same question, wrong price series. Kept in its OWN family so it can be measured without
#: being admissible anywhere a real settlement probability is required.
PROXY_SETTLEMENT_CONTRACTS = frozenset({ROLLING_EXCHANGE_RETURN_SIGN_V1})

@dataclasses.dataclass(frozen=True)
class SettlementRule:
    """How ONE market decides its outcome. Not a global convention.

    A previous version of this file hardcoded `TIE_RESOLVES_TO = DOWN` with a strict `>`,
    reasoned from the usual "Up requires strictly greater" convention. That was WRONG for the
    market this system trades: Polymarket's BTC Up/Down 15m resolves Up when the ending
    Chainlink price is greater than OR EQUAL TO the starting price. Every label, every grade
    and every trained head inherited the error, and nothing in the system could have detected
    it - the code was self-consistent, just self-consistently wrong.

    Replacing one hardcoded convention with another would repeat the mistake at a different
    value. Resolution rules are per-market: the source, the end condition and the edge cases
    are defined by each market's own text. So the rule is an OBJECT, carried on the artifact
    and stamped on every label, and a market whose rule text differs cannot silently reuse a
    contract fitted under this one.
    """

    rule_version: str
    #: What the MARKET settles against. A claim about the venue, not about our data.
    source: str
    comparator: str          # ">=", ">", "<=", "<"
    tie_outcome: str         # UP, DOWN, or UNKNOWN when the text does not say
    rule_text: str
    market_id: str
    #: What OUR labels were actually observed from. When this differs from `source` the
    #: labels are a PROXY: the comparison rule is the market's, the price series is not.
    #: Recorded separately because stamping the venue's source onto exchange candles is a
    #: false provenance claim that nothing downstream could detect.
    observed_source: str = "exchange_close_proxy"

    @property
    def rule_text_hash(self) -> str:
        """Digest of the recorded rule text. A venue edit changes this, and the mismatch is
        detectable instead of being absorbed silently by a model trained under the old text."""
        return hashlib.sha256(self.rule_text.encode("utf-8")).hexdigest()

    def resolve(self, start: float, end: float) -> str:
        """Apply THIS market's rule. The comparator and tie outcome both come from the rule."""
        if self.comparator == ">=":
            return UP if end >= start else DOWN
        if self.comparator == ">":
            if end > start:
                return UP
            if end < start:
                return DOWN
            return self.tie_outcome
        raise UnknownTargetContract(
            f"{self.rule_version}: unsupported comparator {self.comparator!r}; refusing to "
            f"guess how this market resolves")

    @property
    def is_proxy(self) -> bool:
        """True when the labels did not come from the venue's own settlement source."""
        return self.observed_source != self.source

    def identity(self) -> dict:
        return {"rule_version": self.rule_version, "source": self.source,
                "observed_source": self.observed_source, "is_proxy": self.is_proxy,
                "comparator": self.comparator, "tie_outcome": self.tie_outcome,
                "market_id": self.market_id, "rule_text_hash": self.rule_text_hash}


#: The rule for the market this system actually trades, recorded from its published terms
#: (verified by the operator 2026-08-06). Equality resolves UP - this is the correction that
#: made the previous strict-`>` labelling wrong on every tied round.
POLYMARKET_BTC_UPDOWN_15M_V1 = SettlementRule(
    rule_version="polymarket_btc_updown_15m_v1",
    source="chainlink_btc_usd",
    comparator=">=",
    tie_outcome=UP,
    rule_text=("Resolves Up when the ending Chainlink BTC/USD price is greater than or equal "
               "to the starting price; otherwise Down."),
    market_id="btc-updown-15m",
    # OUR labels are built from exchange closes, so this rule is applied to a proxy series.
    # Stating it here is what makes `is_proxy` true and keeps the artifact honest about what
    # it was actually fitted on.
    observed_source="exchange_close_proxy",
)

#: Which rule resolves which contract. The grader looks up by CONTRACT rather than assuming
#: the default, so a second market with a different comparator cannot be graded under this one.
SETTLEMENT_RULE_BY_CONTRACT: dict = {}

#: The rule used when a caller does not name one. Kept as a single binding so the default is
#: greppable, rather than a literal repeated at each call site.
DEFAULT_SETTLEMENT_RULE = POLYMARKET_BTC_UPDOWN_15M_V1

#: Retained because callers and artifacts reference it, but it is now DERIVED from the rule
#: rather than being the source of truth. Editing it in isolation no longer changes labelling.
TIE_RESOLVES_TO = DEFAULT_SETTLEMENT_RULE.tie_outcome
SETTLEMENT_RULE_BY_CONTRACT[POLYMARKET_BINARY_SETTLEMENT_V1] = POLYMARKET_BTC_UPDOWN_15M_V1
# The rolling-return proxy uses the same comparator, applied to exchange closes from a
# decision-time reference. Registered explicitly so it is a stated choice, not a fallback.
SETTLEMENT_RULE_BY_CONTRACT[ROLLING_EXCHANGE_RETURN_SIGN_V1] = POLYMARKET_BTC_UPDOWN_15M_V1

#: Binary settlement has two outcomes, not three. NEUTRAL is not reachable.
BINARY_CLASS_ORDER = (DOWN, UP)

#: What each CONSUMER needs. The point of naming purposes is that a consumer declares the
#: question it is asking, and a probability answering a different question is refused rather
#: than silently accepted because both happen to be floats in [0, 1].
POLYMARKET_SETTLEMENT_EV = "polymarket_settlement_ev"
BINANCE_DIRECTIONAL_EV = "binance_directional_ev"
STOP_TARGET_PLANNING = "stop_target_planning"
PATH_EXCURSION_FORECAST = "path_excursion_forecast"

#: HOLD_EXIT_DECISION was ONE purpose covering two different questions, and so required one
#: contract for both:
#:
#:   "will my stop be hit before my target"    - a PATH question, first-touch is correct
#:   "is holding to settlement worth more than
#:    selling this position at the current bid" - a SETTLEMENT question, and on Polymarket a
#:                                                BINARY one
#:
#: Answering the second with a first-touch probability is the same substitution the contract
#: layer exists to refuse - it was simply happening inside a purpose name broad enough to
#: cover it. Splitting the name is what makes the guard able to see it.
PATH_STOP_MANAGEMENT = "path_stop_management"
POLYMARKET_HOLD_EXIT_EV = "polymarket_hold_exit_ev"
#: Consumers of the ROLLING RETURN SIGN. These ask "will the exchange price be higher than it
#: is right now, h minutes from now" - which is exactly what the head predicts. None of them
#: is a Polymarket settlement question, and none carries pricing authority today.
PROXY_SETTLEMENT_RESEARCH = "proxy_settlement_research"
BINANCE_DIRECTION_CONFIRMATION = "binance_direction_confirmation"
QUOTE_REVISION_RESEARCH = "quote_revision_research"
CROSS_VENUE_PROPAGATION_RESEARCH = "cross_venue_propagation_research"
PATH_CONTINUATION_RESEARCH = "path_continuation_research"

PURPOSE_REQUIREMENTS: dict[str, frozenset] = {
    # Polymarket resolves on a STRICT comparison with no neutral band. The three-class
    # endpoint contract is refused here too, not just the path one: its band is an artefact
    # of our labelling, not of the market.
    # These require the ORACLE-sourced contract. No artifact exists under it, so both still
    # refuse - the proxy head is measurable but may not price a real settlement.
    POLYMARKET_SETTLEMENT_EV: BINARY_SETTLEMENT_CONTRACTS,
    POLYMARKET_HOLD_EXIT_EV: BINARY_SETTLEMENT_CONTRACTS,
    # Research only: scoring the proxy head against geometry and the market price. Carries no
    # authority, which is why it is a separate purpose rather than a widening of the two above.
    # The rolling-return-sign head answers all of these directly. It is refused for every
    # Polymarket purpose above, and admitted here, because the QUESTION matches.
    PROXY_SETTLEMENT_RESEARCH: PROXY_SETTLEMENT_CONTRACTS,
    BINANCE_DIRECTION_CONFIRMATION: PROXY_SETTLEMENT_CONTRACTS,
    QUOTE_REVISION_RESEARCH: PROXY_SETTLEMENT_CONTRACTS,
    CROSS_VENUE_PROPAGATION_RESEARCH: PROXY_SETTLEMENT_CONTRACTS,
    PATH_CONTINUATION_RESEARCH: PROXY_SETTLEMENT_CONTRACTS,
    # The Binance EV is (2p-1) * expected_move - costs, which treats p as the probability
    # that the ENDPOINT lands on the predicted side. A neutral band is meaningful there: it
    # is the region where the perp trade is not worth its costs.
    BINANCE_DIRECTIONAL_EV: SETTLEMENT_CONTRACTS,
    # These are genuinely path questions, and the first-touch head is the right input.
    STOP_TARGET_PLANNING: PATH_CONTRACTS,
    PATH_STOP_MANAGEMENT: PATH_CONTRACTS,
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


def is_banded_endpoint(contract: str) -> bool:
    """Three-class endpoint with an adaptive neutral band."""
    return contract in SETTLEMENT_CONTRACTS


def is_binary_endpoint(contract: str) -> bool:
    """Two-class endpoint, no band - either the venue's rule or the exchange proxy."""
    return contract in (BINARY_SETTLEMENT_CONTRACTS | PROXY_SETTLEMENT_CONTRACTS)


def is_endpoint(contract: str) -> bool:
    """Any contract answering 'where does price END', banded or binary."""
    return is_banded_endpoint(contract) or is_binary_endpoint(contract)


def is_settlement(contract: str) -> bool:
    """DEPRECATED spelling of `is_banded_endpoint`.

    It returned False for the binary contracts, which is the opposite of what the name
    suggests: a caller routing on `is_settlement` would have sent the one contract that
    actually answers a settlement question down the non-settlement branch. Kept only so no
    existing caller changes behaviour silently; new code should name which endpoint it means.
    """
    return is_banded_endpoint(contract)


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


def first_touch_at(entry: float, highs, lows, threshold: float):
    """(outcome, resolving_index). The index is WHERE the outcome was decided.

    P1-1. The label alone is not enough to grade a row honestly: every magnitude metric needs
    the observation that PRODUCED the label, not some other bar in the same window. Returning
    the index is what lets one resolution observation flow to `actual_price`, `actual_move_usd`
    and `target_error_usd` instead of three different moments.

    On a NEUTRAL timeout the resolving bar is the LAST one - the horizon expiring is the event.
    """
    if entry <= 0 or threshold <= 0:
        return NEUTRAL, None
    upper = entry * (1.0 + threshold)
    lower = entry * (1.0 - threshold)
    index = -1
    for index, (high, low) in enumerate(zip(highs, lows)):
        touched_up = high >= upper
        touched_down = low <= lower
        if touched_up and touched_down:
            return AMBIGUOUS, index    # order inside the bar is unknowable from OHLC
        if touched_up:
            return UP, index
        if touched_down:
            return DOWN, index
    # timeout: neither barrier reached. `index` is the last bar, or -1 for an empty path.
    return NEUTRAL, (index if index >= 0 else None)


def label_first_touch(entry: float, highs, lows, threshold: float) -> str:
    """Which barrier is touched FIRST over the path. AMBIGUOUS when a single bar touches both.

    `highs`/`lows` are the intrabar extremes of each bar in the horizon, in order."""
    return first_touch_at(entry, highs, lows, threshold)[0]


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


def label_polymarket_binary(entry: float, final: float,
                            rule: SettlementRule = DEFAULT_SETTLEMENT_RULE) -> str:
    """Resolve one round under a specific market's rule.

    Takes NO threshold, by signature. The three-class endpoint label needs one and uses an
    adaptive volatility band; passing one here would silently reintroduce a flat zone that the
    market does not have, so the parameter simply does not exist to be passed.

    The comparator and the tie outcome come from `rule`, not from a module constant. The
    default was `>` with a DOWN tie, which mislabelled every tied round on a market that
    actually resolves Up on equality.
    """
    return rule.resolve(entry, final)


def label(contract: str, *, entry: float, threshold: float | None = None,
          highs=None, lows=None, final: float | None = None) -> str:
    """Dispatch on the DECLARED contract. An unknown contract raises."""
    if contract == POLYMARKET_BINARY_SETTLEMENT_V1:
        if final is None:
            raise UnknownTargetContract(f"{contract} requires the final price")
        if threshold is not None:
            # `if threshold:` let 0.0 through - exactly the value a caller passes while
            # believing they have disabled the band. The parameter is refused by PRESENCE,
            # not by truthiness, so "I turned the band off" cannot silently mean "I passed
            # a banded contract's argument to a binary one".
            raise UnknownTargetContract(
                f"{contract} resolves on a direct comparison and takes no threshold; got "
                f"{threshold!r}. A threshold here would carve a neutral band into a market "
                f"that pays out on two outcomes.")
        return label_polymarket_binary(entry, final)
    if threshold is None:
        raise UnknownTargetContract(
            f"{contract} requires a threshold; grading it without one would collapse its "
            f"neutral band and silently turn it into a binary contract")
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

    __slots__ = ("direction", "status", "resolution_price", "resolution_event_ts", "contract",
                 "resolution_basis", "interval_start_ms", "interval_end_ms",
                 "endpoint_price", "endpoint_ts",
                 "observed_start_ms", "observed_end_ms", "window_shift_ms")

    def __init__(self, direction, status, resolution_price=None, resolution_event_ts=None,
                 contract=None, resolution_basis=None, interval_start_ms=None,
                 interval_end_ms=None, endpoint_price=None, endpoint_ts=None,
                 observed_start_ms=None, observed_end_ms=None, window_shift_ms=None):
        self.direction = direction
        self.status = status
        self.resolution_price = resolution_price
        self.resolution_event_ts = resolution_event_ts
        self.contract = contract
        #: WHICH observation this is, recorded on the row. A number without its basis is how
        #: a loop-time price came to sit beside a horizon-end direction for so long.
        self.resolution_basis = resolution_basis
        #: The bar the touch happened INSIDE. OHLC cannot say when within it, so the honest
        #: object is an interval, not an instant. `resolution_event_ts` is its start, kept for
        #: ordering, and must never be described as the exact moment of the crossing.
        self.interval_start_ms = interval_start_ms
        self.interval_end_ms = interval_end_ms
        #: The horizon-end observation, ALWAYS carried. First-touch economics use the barrier;
        #: anything that genuinely wants endpoint economics must take it from here rather than
        #: re-deriving it from a price that answers a different question.
        self.endpoint_price = endpoint_price
        self.endpoint_ts = endpoint_ts
        #: 5.2. THE INTERVAL ACTUALLY WATCHED, which is not the interval declared.
        #:
        #: A path is selected as `entry_ts < open_ms <= verify_ts` over 1-minute bars, and a
        #: prediction is issued at an arbitrary second. MEASURED on a 5m horizon: the count
        #: is always 5 bars, so the window is the right LENGTH - the scan's "shorter than
        #: the declared horizon" is wrong - but it is SHIFTED forward by up to 60s:
        #:
        #:     entry +0s   observed [ 60s.. 360s]  declared [ 0s.. 300s]
        #:     entry +20s  observed [ 60s.. 360s]  declared [20s.. 320s]
        #:     entry +59s  observed [ 60s.. 360s]  declared [59s.. 359s]
        #:
        #: Both ends matter and they do not cancel. At the head, a barrier touched between
        #: entry and the first bar open is INVISIBLE. At the tail, a touch after the horizon
        #: ended is attributed to the round - an outcome no position could have taken.
        #:
        #: Tightening the selection to bars fully inside the horizon would drop to 4 bars
        #: and grade a 5-minute contract over 4 minutes, which is a worse error than the
        #: one being fixed. The structural remedy is bar-aligned entry timestamps, which is
        #: a change to when predictions are issued, not to this function. Until then every
        #: row carries what was watched, so no consumer has to assume it matched.
        self.observed_start_ms = observed_start_ms
        self.observed_end_ms = observed_end_ms
        self.window_shift_ms = window_shift_ms

    @property
    def graded(self) -> bool:
        return self.direction is not None

    def __repr__(self) -> str:
        return (f"GradeResult(direction={self.direction!r}, status={self.status!r}, "
                f"price={self.resolution_price!r}, ts={self.resolution_event_ts!r})")


def as_of_close(klines, at_ms: int):
    """(close, resolution_event_ms) of the last closed bar at or before `at_ms`.

    P0-4, FIXED HERE - and the reason it could not be fixed before.

    SELECTION IS UNCHANGED, deliberately. `at_ms` NAMES the horizon-end bar; callers set
    `verify_at` to that bar's OPEN, and the P0-11 fixture asserts exactly that. Requiring
    `open + interval <= at_ms` would silently redefine every horizon by one bar - which is why
    that attempt was reverted twice.

    What WAS wrong is the second element. It returned the bar's OPEN timestamp as the
    resolution event, so every consumer recording `resolution_event_ts` stamped the observation
    one interval early. Correcting it needed the bar's duration, and the only signal available
    was the spacing of neighbouring rows - unsafe, because on a filtered or sparse list that is
    not the real cadence (the P0-11 fixture's bars run +60s/+300s/+540s, so `min(diffs)` yields
    240s).

    `kline_schema` removes the guess: producers now RECORD `close_ts_ms` from the exchange, on
    both transports. Where it is present the true close is returned; where it is absent - a
    legacy row - the open time is returned as before, so nothing regresses while the schema
    propagates.
    """
    from kline_schema import close_ts_ms as _recorded_close

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
    recorded = _recorded_close(best) if isinstance(best, dict) else None
    return float(best["close"]), int(recorded if recorded is not None else best_ms)


def grade(*, contract: str, entry: float, threshold: float, klines,
          entry_ts: int, verify_ts: int) -> GradeResult:
    """THE canonical grader. Every verifier resolves through this and nothing else.

    Returns a GradeResult whose `direction` is None whenever the row must not be graded -
    refusing is always preferred to labelling by a rule the model was not trained under."""
    if contract not in KNOWN_CONTRACTS:
        return GradeResult(None, f"UNKNOWN_CONTRACT:{contract}", contract=contract)

    endpoint_price, endpoint_ts = as_of_close(klines, verify_ts)

    if contract == ENDPOINT_SETTLEMENT_V1:
        if endpoint_price is None:
            return GradeResult(None, "GRADE_UNAVAILABLE:no_as_of_price", contract=contract)
        # Here the close IS the resolving event, so price/interval/endpoint all coincide.
        return GradeResult(label_endpoint(entry, endpoint_price, threshold), "GRADED_ENDPOINT",
                           endpoint_price, endpoint_ts, contract, "as_of_kline_close",
                           interval_start_ms=endpoint_ts, interval_end_ms=endpoint_ts,
                           endpoint_price=endpoint_price, endpoint_ts=endpoint_ts)

    # BINARY ENDPOINT, BEFORE the first-touch fall-through.
    #
    # This branch was missing while both binary contracts were added to KNOWN_CONTRACTS. The
    # effect was worse than an oversight: previously an unrecognised contract was REFUSED
    # here, so extending the known set converted a safe refusal into a confident wrong grade.
    # A prediction stamped binary was resolved using intrabar highs/lows, a barrier threshold,
    # first-touch direction and a barrier price - none of which its contract mentions.
    #
    #     entry 100, price touches 98 first, settles 101
    #     first touch -> DOWN        binary -> UP
    #
    # No threshold is consulted. These contracts resolve on a direct comparison, and the
    # neutral band belongs to a different contract.
    if is_binary_endpoint(contract):
        if endpoint_price is None:
            return GradeResult(None, "GRADE_UNAVAILABLE:no_as_of_price", contract=contract)
        rule = SETTLEMENT_RULE_BY_CONTRACT.get(contract, DEFAULT_SETTLEMENT_RULE)
        return GradeResult(rule.resolve(entry, endpoint_price), "GRADED_BINARY_ENDPOINT",
                           endpoint_price, endpoint_ts, contract, "as_of_kline_close",
                           interval_start_ms=endpoint_ts, interval_end_ms=endpoint_ts,
                           endpoint_price=endpoint_price, endpoint_ts=endpoint_ts)

    path = [k for k in (klines or [])
            if int(entry_ts) < kline_open_ms(k) <= int(verify_ts)
            and (k.get("is_closed") is not False if isinstance(k, dict) else True)]
    if not path:
        return GradeResult(None, "GRADE_UNAVAILABLE:no_intrabar_path", contract=contract)
    # 5.2. What was WATCHED, measured from the bars actually selected, so every graded row
    # below can carry it. `close_ts_ms` is used where the producer recorded one and the bar
    # cadence is inferred only as a last resort - a duration guessed from an irregular list
    # is how the earlier P0-4 attempt went wrong.
    _observed = _observed_window(path, entry_ts, verify_ts)
    outcome, index = first_touch_at(entry, [float(k["high"]) for k in path],
                                    [float(k["low"]) for k in path], threshold)
    if outcome == AMBIGUOUS:
        # A single bar touched both barriers, so the target is undefined on this row. Grading
        # it either way manufactures a hit or a miss out of an unknowable ordering.
        return GradeResult(None, "GRADE_UNAVAILABLE:ambiguous_bar", contract=contract)
    if index is None:
        return GradeResult(None, "GRADE_UNAVAILABLE:no_intrabar_path", contract=contract)

    bar = path[index]
    start_ms = kline_open_ms(bar)
    # The bar is an INTERVAL. OHLC cannot say when inside it the barrier was crossed, so the
    # end is the next bar's open where one exists, else the horizon end.
    end_ms = kline_open_ms(path[index + 1]) if index + 1 < len(path) else int(verify_ts)

    if outcome == NEUTRAL:
        # Timeout: no barrier was reached and the horizon expiring IS the event, so the
        # last bar's close is the correct resolving observation.
        last = path[-1]
        return GradeResult(outcome, "GRADED_FIRST_TOUCH", float(last["close"]),
                           kline_open_ms(last), contract, "first_touch_timeout_close",
                           interval_start_ms=kline_open_ms(last), interval_end_ms=int(verify_ts),
                           endpoint_price=endpoint_price, endpoint_ts=endpoint_ts, **_observed)

    # P0-2. THE BARRIER, not the resolving bar's CLOSE.
    #
    # The close was wrong for the same reason loop-time price was wrong. A bar can pierce the
    # lower barrier and still close above entry:
    #
    #     entry 100, lower 99 | bar low 98.0, bar close 100.5
    #     -> direction DOWN, close-derived move +0.50
    #
    # which is precisely the contradictory row the resolution work claimed to have removed. The
    # earlier fixture never caught it because its touching bars closed on the same side they
    # touched, so direction and close agreed by construction.
    #
    # The barrier price is the observation that DEFINED the outcome, it is exactly known, and
    # its sign can never disagree with the direction. The consequence is worth stating plainly:
    # under first touch, |move| is always the barrier distance, so magnitude error on these
    # rows measures the barrier, not a magnitude forecast. That is a true statement about the
    # contract rather than a limitation of this function - endpoint economics must come from
    # `endpoint_price`, which is carried for exactly that purpose.
    barrier = entry * (1.0 + threshold) if outcome == UP else entry * (1.0 - threshold)
    return GradeResult(outcome, "GRADED_FIRST_TOUCH", float(barrier), start_ms, contract,
                       "first_touch_barrier", interval_start_ms=start_ms,
                       interval_end_ms=end_ms, endpoint_price=endpoint_price,
                       endpoint_ts=endpoint_ts, **_observed)


#: The band used when a prediction did not declare one. It is a FALLBACK for an absent
#: value, never a substitute for a declared one - see `resolve_neutral_band`.
DEFAULT_NEUTRAL_BAND = 0.0008


def resolve_neutral_band(declared, default: float = DEFAULT_NEUTRAL_BAND) -> float:
    """The band to grade at, distinguishing ABSENT from ZERO.

    Every consumer wrote `float(pred.get("neutralBand", 0.0008) or 0.0008)`. A declared band
    of 0.0 is falsy, so `or` replaced it with 8bps - and 0.0 is REACHABLE and legitimate:

        BTC_LABEL_COST_FLOOR=0  ->  causal_neutral_band(...) == 0.0
        training labels built at  0.0
        the model declares        0.0
        the verifier recorded     0.0008

    which is precisely the train/serve barrier mismatch `causal_neutral_band` was written to
    eliminate, reintroduced by an `or`. A zero-cost study would have been graded against an
    8bps barrier the model was never trained on, and nothing would have said so.

    Absent, non-numeric, negative and NaN all fall back. Zero does not.
    """
    if declared is None:
        return float(default)
    try:
        value = float(declared)
    except (TypeError, ValueError):
        return float(default)
    if value != value or value < 0.0:      # NaN or negative: not a width
        return float(default)
    return value


def _observed_window(path, entry_ts, verify_ts) -> dict:
    """The interval the selected bars actually cover, and how far it sits from the declared one.

    The end is a RECORDED close where the producer wrote one. Where it did not, the cadence is
    inferred from the bar openings and used only if it is regular - an interval guessed from an
    irregular list is exactly what made the earlier P0-4 attempt unsafe, and a wrong duration
    here would misreport the shift rather than leave it unknown.
    """
    if not path:
        return {"observed_start_ms": None, "observed_end_ms": None, "window_shift_ms": None}
    start = kline_open_ms(path[0])
    last_open = kline_open_ms(path[-1])
    end = None
    try:
        from kline_schema import close_ts_ms as _close_ts
        end = _close_ts(path[-1])
    except Exception:
        end = None
    if end is None and len(path) >= 3:
        diffs = {kline_open_ms(path[i + 1]) - kline_open_ms(path[i])
                 for i in range(len(path) - 1)}
        if len(diffs) == 1:
            end = last_open + diffs.pop()
    if end is None:
        # Unknown cadence: report the last OPEN and say the shift is unknown rather than
        # inventing a duration.
        return {"observed_start_ms": start, "observed_end_ms": None, "window_shift_ms": None}
    return {"observed_start_ms": start, "observed_end_ms": int(end),
            "window_shift_ms": int(end) - int(verify_ts)}


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

    check(assert_admissible(POLYMARKET_SETTLEMENT_EV, POLYMARKET_BINARY_SETTLEMENT_V1)
          == POLYMARKET_BINARY_SETTLEMENT_V1,
          "settlement EV accepts the BINARY settlement probability - the one the venue pays on")
    check(assert_admissible(BINANCE_DIRECTIONAL_EV, ENDPOINT_SETTLEMENT_V1)
          == ENDPOINT_SETTLEMENT_V1,
          "and the perp lane still accepts the banded endpoint probability, where the band is "
          "the region the trade does not clear its costs")
    check(assert_admissible(STOP_TARGET_PLANNING, FIRST_TOUCH_TRIPLE_BARRIER_V1)
          == FIRST_TOUCH_TRIPLE_BARRIER_V1, "stop/target planning accepts a PATH probability")

    for purpose in (POLYMARKET_SETTLEMENT_EV, POLYMARKET_HOLD_EXIT_EV, BINANCE_DIRECTIONAL_EV):
        try:
            assert_admissible(purpose, FIRST_TOUCH_TRIPLE_BARRIER_V1)
            raise AssertionError(f"{purpose} accepted a path probability")
        except ContractMisuse:
            pass
    checks += 1
    print("  PASS  no EV purpose accepts a PATH probability - the substitution that made a "
          "first-touch model price an endpoint question")

    # The band is OUR artefact, not the venue's. A Polymarket purpose must refuse it.
    for purpose in (POLYMARKET_SETTLEMENT_EV, POLYMARKET_HOLD_EXIT_EV):
        try:
            assert_admissible(purpose, ENDPOINT_SETTLEMENT_V1)
            raise AssertionError(f"{purpose} accepted a BANDED settlement probability")
        except ContractMisuse:
            pass
    checks += 1
    print("  PASS  and every Polymarket purpose refuses the three-class endpoint probability - "
          "its NEUTRAL band is a labelling artefact, and the market has no flat outcome to pay")

    for purpose in (STOP_TARGET_PLANNING, PATH_STOP_MANAGEMENT, PATH_EXCURSION_FORECAST):
        for offered in (ENDPOINT_SETTLEMENT_V1, POLYMARKET_BINARY_SETTLEMENT_V1):
            try:
                assert_admissible(purpose, offered)
                raise AssertionError(f"{purpose} accepted {offered}")
            except ContractMisuse:
                pass
    checks += 1
    print("  PASS  and no PATH purpose accepts a settlement probability - the guard runs "
          "both ways, so it is not simply refusing everything")

    # The split is the point: one name that covered both questions could not refuse either.
    check(PURPOSE_REQUIREMENTS[PATH_STOP_MANAGEMENT] is PATH_CONTRACTS
          and PURPOSE_REQUIREMENTS[POLYMARKET_HOLD_EXIT_EV] is BINARY_SETTLEMENT_CONTRACTS,
          "the two halves of the old hold/exit purpose now require DIFFERENT contract "
          "families, which a single purpose name could not express")
    check("hold_exit_decision" not in PURPOSE_REQUIREMENTS,
          "and the merged purpose is gone, so no caller can keep asking the ambiguous question")

    # ---- the binary contract ---------------------------------------------------------
    # The three cases from the market's published terms, asserted literally.
    check(label_polymarket_binary(100.0, 100.0) == UP,
          "an exact tie resolves UP - the market resolves Up when the ending price is greater "
          "than OR EQUAL TO the start, and the previous strict-'>' convention mislabelled "
          "every tied round")
    check(label_polymarket_binary(100.0, 100.01) == UP,
          "a higher ending price resolves UP")
    check(label_polymarket_binary(100.0, 99.99) == DOWN,
          "and a lower one resolves DOWN, with no neutral band between them")

    _rule = DEFAULT_SETTLEMENT_RULE
    check(_rule.comparator == ">=" and _rule.tie_outcome == UP,
          f"the rule itself declares comparator {_rule.comparator!r} and tie {_rule.tie_outcome} "
          f"- the label reads them from the rule rather than from a module constant")
    check(_rule.source == "chainlink_btc_usd",
          "and names its settlement SOURCE, so a model cannot be scored against a price feed "
          "the market does not settle on")
    check(len(_rule.rule_text_hash) == 64,
          "the rule text is hashed, so a venue wording change is detectable rather than being "
          "absorbed by a model still trained under the old text")

    # A DIFFERENT market may not silently reuse this contract's labelling.
    _other = SettlementRule(
        rule_version="some_other_market_v1", source="chainlink_btc_usd", comparator=">",
        tie_outcome=DOWN, rule_text="Resolves Up only when the ending price exceeds the start.",
        market_id="other-market")
    check(_other.resolve(100.0, 100.0) == DOWN
          and label_polymarket_binary(100.0, 100.0, _other) == DOWN,
          "a market whose rule says a tie is DOWN resolves it DOWN - the outcome follows the "
          "rule object, so one market's convention cannot leak into another's labels")
    check(_other.rule_text_hash != _rule.rule_text_hash,
          "and its rule hash differs, so an artifact trained under one cannot pass as the other")
    try:
        SettlementRule(rule_version="bad", source="x", comparator="~=", tie_outcome=UP,
                       rule_text="unparseable", market_id="m").resolve(1.0, 1.0)
        raise AssertionError("an unsupported comparator resolved anyway")
    except UnknownTargetContract:
        checks += 1
        print("  PASS  an unsupported comparator RAISES rather than falling back to a default "
              "- guessing how a market resolves is how the tie rule was wrong to begin with")
    check(set(BINARY_CLASS_ORDER) == {UP, DOWN} and NEUTRAL not in BINARY_CLASS_ORDER,
          "NEUTRAL is not reachable under the binary contract")
    check(not (BINARY_SETTLEMENT_CONTRACTS & SETTLEMENT_CONTRACTS)
          and not (BINARY_SETTLEMENT_CONTRACTS & PATH_CONTRACTS),
          "and the binary family is disjoint from both others - it is a third question, not "
          "a relabelling of the second")
    try:
        label(POLYMARKET_BINARY_SETTLEMENT_V1, entry=100.0, final=101.0, threshold=0.001)
        raise AssertionError("the binary contract accepted a threshold")
    except UnknownTargetContract:
        checks += 1
        print("  PASS  passing a threshold to the binary contract RAISES - that is how the "
              "band would come back, so the parameter is refused rather than ignored")
    try:
        label(ENDPOINT_SETTLEMENT_V1, entry=100.0, final=101.0)
        raise AssertionError("the banded contract graded without a threshold")
    except UnknownTargetContract:
        checks += 1
        print("  PASS  and omitting the threshold on a BANDED contract raises rather than "
              "defaulting to zero, which would silently make it binary")

    # THE REFERENCE-POINT DEFECT, MEASURED. A rolling label anchored to the decision price is
    # not a weaker version of the venue's question; it is inverted on a large share of rounds,
    # and worst late in the round where the edge would be.
    _r = np.random.default_rng(5)
    _rounds, _dur, _vol = 20000, 15, 0.0009
    _worst = 0.0
    for _cp in (3, 6, 9, 12):
        _steps = _r.normal(0, _vol, (_rounds, _dur))
        _path = 100.0 * np.cumprod(1.0 + _steps, axis=1)
        _decision, _final = _path[:, _cp - 1], _path[:, -1]
        _venue = _final >= 100.0                 # vs the fixed round anchor
        _ours = _final > _decision               # vs the decision-time price
        _worst = max(_worst, float(np.mean(_venue != _ours)))
    check(_worst > 0.25,
          f"a decision-anchored label disagrees with the venue on up to {_worst:.1%} of rounds "
          f"late in the round - this is why the proxy contract may not price the market, and "
          f"why recalibration cannot repair it")

    # ---- P0-1: the grader must not resolve a binary contract by first touch -------------
    # Both binary contracts were added to KNOWN_CONTRACTS with no branch of their own, so they
    # fell through to the first-touch path. That is worse than an omission: an unrecognised
    # contract used to be REFUSED, so extending the known set turned a safe refusal into a
    # confident wrong grade.
    _BASE = 1_785_000_000_000
    _entry = 100.0
    # Touches the LOWER barrier first, then settles ABOVE entry. The contracts must disagree.
    _bars = [
        {"time": _BASE + 60_000, "high": 100.2, "low": 98.0, "close": 99.0},
        {"time": _BASE + 120_000, "high": 101.5, "low": 99.5, "close": 101.0},
    ]
    _ft = grade(contract=FIRST_TOUCH_TRIPLE_BARRIER_V1, entry=_entry, threshold=0.01,
                klines=_bars, entry_ts=_BASE, verify_ts=_BASE + 120_000)
    _bin = grade(contract=ROLLING_EXCHANGE_RETURN_SIGN_V1, entry=_entry, threshold=0.01,
                 klines=_bars, entry_ts=_BASE, verify_ts=_BASE + 120_000)
    check(_ft.direction == DOWN and _ft.status == "GRADED_FIRST_TOUCH",
          f"first touch grades this path DOWN ({_ft.status}) - the lower barrier is hit first")
    check(_bin.direction == UP,
          f"while the BINARY contract grades the SAME path UP (settles 101 >= 100) - before "
          f"this branch existed it returned {_ft.direction}, the first-touch answer, under a "
          f"binary contract name")
    check(_bin.status == "GRADED_BINARY_ENDPOINT",
          f"and says so in its status ({_bin.status}), so a reader can tell which rule ran")
    check(_bin.resolution_basis == "as_of_kline_close"
          and _ft.resolution_basis == "first_touch_barrier",
          f"resolving on the as-of close ({_bin.resolution_basis}) rather than the barrier "
          f"price the first-touch path uses ({_ft.resolution_basis}) - a contract the binary "
          f"rule never mentions")

    # A tie resolves UP under the verified venue rule, through the GRADER too - not only
    # through label_polymarket_binary.
    _tie = [{"time": _BASE + 60_000, "high": 100.5, "low": 99.5, "close": 100.0}]
    _tg = grade(contract=ROLLING_EXCHANGE_RETURN_SIGN_V1, entry=_entry, threshold=0.01,
                klines=_tie, entry_ts=_BASE, verify_ts=_BASE + 60_000)
    check(_tg.direction == UP,
          "an exact tie grades UP through the dispatcher, matching the market rule rather "
          "than a second convention living inside the grader")

    check(set(SETTLEMENT_RULE_BY_CONTRACT) == set(BINARY_SETTLEMENT_CONTRACTS
                                                  | PROXY_SETTLEMENT_CONTRACTS),
          "every binary contract has a REGISTERED rule, so the grader looks one up instead of "
          "assuming the default for a market it has never seen")

    # The two settlement contracts must actually disagree, or the split is a naming exercise.
    _rng = np.random.default_rng(11)
    _entry, _band = 100.0, 0.0008
    _finals = _entry * (1.0 + _rng.normal(0, _band, 4000))
    _disagree = float(np.mean([
        label_endpoint(_entry, f, _band) != label_polymarket_binary(_entry, f)
        for f in _finals]))
    check(_disagree > 0.2,
          f"the banded and binary settlement labels disagree on {_disagree:.1%} of endpoints "
          f"at a realistic band - every one of those is a real payout the banded contract "
          f"calls NEUTRAL")

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
