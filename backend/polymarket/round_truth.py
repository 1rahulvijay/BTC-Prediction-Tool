"""Round-aligned settlement truth: one anchor, one outcome, many causal checkpoints.

WHY THIS EXISTS
    `build_sequences` labels a settlement row as

        final close after h minutes  >=  close at the DECISION time

    while the venue resolves

        oracle value at the fixed round END  >=  oracle value at the round ANCHOR

    Two defects, and the second is the worse one. Measured over 40k simulated 15m rounds, a
    decision-anchored label disagrees with the venue's own outcome on:

        0m  (15m left)   0.0%    (only correct because decision price IS the anchor)
        3m  (12m left)  14.8%
        6m  ( 9m left)  21.5%
        9m  ( 6m left)  28.2%
        12m ( 3m left)  35.3%    <- worst exactly where late-round information is worth most

    A 35% inversion is not miscalibration. No amount of retraining or recalibration repairs a
    label that is backwards, so every settlement metric downstream of it measures the wrong
    thing. This module defines the labels that replace it.

THE SHAPE
    ONE truth row per round: anchor, final, official outcome, rule hash, admissibility.
    MANY checkpoint rows per round: same anchor, same outcome, different information cutoff.

        C0 t+0s    900s left        all five share  anchor_value
        C1 t+180s  720s left                        outcome
        C2 t+360s  540s left                        rule_text_hash
        C3 t+540s  360s left        and differ only in what was knowable at decision_ts
        C4 t+720s  180s left

    The decision-time price is a FEATURE (`distance_from_anchor`), never part of the label.

WHAT THIS MODULE DOES NOT DO
    It does not capture anything. Anchors cannot be backfilled: if no recorder observed the
    oracle at a round's wall-clock open, that round is permanently INADMISSIBLE. The live
    capture side needs a running app and a live feed, and is deliberately separate so this
    logic can be tested without either.

    python backend/polymarket/round_truth.py --selftest
"""
from __future__ import annotations

import argparse
import dataclasses
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import target_contract as tc                                       # noqa: E402

#: Checkpoint offsets for a 15-minute market, in seconds from the round open.
CHECKPOINTS_15M = (0, 180, 360, 540, 720)

#: Admissibility verdicts. A row is either usable evidence or it is quarantined WITH a reason;
#: there is no third state where a disagreement is absorbed into model noise.
ADMISSIBLE = "ADMISSIBLE"
QUARANTINED = "QUARANTINED"

#: How stale an oracle observation may be relative to the boundary it is meant to describe.
#: An anchor read 90 seconds after the open is not this round's anchor.
MAX_OBSERVATION_LAG_MS = 5_000


class RoundTruthError(ValueError):
    """Refuse to emit a label rather than emit one whose provenance is unclear."""


def round_start_from_slug(slug: str) -> int:
    """Round start in ms, from the SLUG - not from Gamma's `startDate`.

    `btc-updown-15m-1778437800` carries the interval start as a unix timestamp. Gamma's
    `startDate` is when the market was LISTED, which for these recurring markets can be a day
    earlier. Using it as the anchor boundary would place the anchor outside the round entirely
    and silently mis-anchor every reconstructed row.
    """
    tail = slug.rsplit("-", 1)[-1]
    if not tail.isdigit():
        raise RoundTruthError(
            f"slug {slug!r} carries no interval timestamp; refusing to guess the round "
            f"boundary from listing metadata")
    seconds = int(tail)
    if not 1_400_000_000 < seconds < 4_000_000_000:
        raise RoundTruthError(f"slug {slug!r} timestamp {seconds} is not a plausible epoch")
    return seconds * 1000


#: WHICH oracle report is "the" boundary value is an EMPIRICAL question, not a preference.
#: Chainlink Data Streams reports carry both `validFromTimestamp` and `observationsTimestamp`,
#: and a report five seconds after a boundary may contain observations unavailable at it.
#: Candidate policies must be tested against the venue's own displayed Price to Beat and
#: resolved outcomes before one is frozen. Recorded per row so a wrong choice is diagnosable
#: rather than baked in.
BOUNDARY_POLICIES = (
    "LATEST_OBSERVATION_AT_OR_BEFORE",
    "FIRST_VALID_AT_OR_AFTER",
    "VALID_INTERVAL_CONTAINS",
)

@dataclasses.dataclass(frozen=True)
class RoundSettlementTruth:
    """One immutable row per resolved market. The canonical label lives here."""

    market_id: str
    condition_id: str
    round_start_ms: int
    round_end_ms: int
    rule_version: str
    rule_text_hash: str
    resolution_source: str
    comparator: str
    tie_outcome: str
    anchor_value: float
    anchor_source_ts_ms: int
    final_value: float
    final_source_ts_ms: int
    official_outcome: str | None = None
    round_duration_s: int = 900

    @property
    def derived_outcome(self) -> str:
        """What the recorded oracle values say, under this market's own rule."""
        rule = tc.SettlementRule(
            rule_version=self.rule_version, source=self.resolution_source,
            comparator=self.comparator, tie_outcome=self.tie_outcome,
            rule_text="(recorded)", market_id=self.market_id,
            observed_source=self.resolution_source)
        return rule.resolve(self.anchor_value, self.final_value)

    @property
    def outcomes_match(self) -> bool:
        return self.official_outcome is not None and self.official_outcome == self.derived_outcome

    def admissibility(self) -> tuple[str, str]:
        """(verdict, reason). Only fully reconciled rounds may train anything.

        The canonical label is the OFFICIAL outcome; the recorded oracle values must
        independently reconstruct it. A mismatch means one of the two is wrong, and which one
        is unknown - so the round is quarantined rather than silently relabelled to whichever
        source is more convenient."""
        if self.official_outcome is None:
            return QUARANTINED, "no official outcome recorded"
        if not self.rule_text_hash or len(self.rule_text_hash) != 64:
            return QUARANTINED, "rule text hash missing or malformed"
        if self.round_end_ms <= self.round_start_ms:
            return QUARANTINED, "round end is not after round start"
        if not (self.anchor_value > 0 and self.final_value > 0):
            return QUARANTINED, "non-positive oracle value"
        anchor_lag = self.anchor_source_ts_ms - self.round_start_ms
        if abs(anchor_lag) > MAX_OBSERVATION_LAG_MS:
            return QUARANTINED, (f"anchor observed {anchor_lag}ms from the round open "
                                 f"(limit {MAX_OBSERVATION_LAG_MS}ms)")
        final_lag = self.final_source_ts_ms - self.round_end_ms
        if abs(final_lag) > MAX_OBSERVATION_LAG_MS:
            return QUARANTINED, (f"final observed {final_lag}ms from the round close "
                                 f"(limit {MAX_OBSERVATION_LAG_MS}ms)")
        if not self.outcomes_match:
            return QUARANTINED, (f"derived {self.derived_outcome} != official "
                                 f"{self.official_outcome} - one source is wrong and which is "
                                 f"unknown")
        return ADMISSIBLE, "reconciled against the official outcome"


@dataclasses.dataclass(frozen=True)
class SettlementCheckpoint:
    """One causal decision moment inside a round. The label is the ROUND's, not this row's."""

    market_id: str
    checkpoint_index: int
    decision_ts_ms: int
    seconds_left: int
    anchor_value: float
    current_reference_price: float
    outcome: str
    rule_text_hash: str

    @property
    def distance_from_anchor(self) -> float:
        """A FEATURE. It must never enter the label comparator - that substitution is the
        entire defect this module replaces."""
        return self.current_reference_price - self.anchor_value


def build_checkpoints(truth: RoundSettlementTruth, reference_prices: dict,
                      offsets=CHECKPOINTS_15M) -> list[SettlementCheckpoint]:
    """Emit one row per checkpoint, all sharing the round's anchor, outcome and rule.

    `reference_prices` maps offset-seconds -> the price observable at that moment. A missing
    observation drops that checkpoint rather than interpolating one: an invented decision-time
    price is a feature the model could never have seen live.
    """
    verdict, reason = truth.admissibility()
    if verdict != ADMISSIBLE:
        raise RoundTruthError(
            f"{truth.market_id}: refusing to emit checkpoints for a {verdict} round ({reason})")
    rows = []
    for index, offset in enumerate(offsets):
        if offset not in reference_prices:
            continue
        decision_ts = truth.round_start_ms + offset * 1000
        if decision_ts >= truth.round_end_ms:
            raise RoundTruthError(
                f"{truth.market_id}: checkpoint {index} at +{offset}s is at or after the "
                f"round close - a decision cannot be made after the outcome is determined")
        rows.append(SettlementCheckpoint(
            market_id=truth.market_id,
            checkpoint_index=index,
            decision_ts_ms=decision_ts,
            seconds_left=int((truth.round_end_ms - decision_ts) // 1000),
            anchor_value=truth.anchor_value,
            current_reference_price=float(reference_prices[offset]),
            outcome=truth.official_outcome,
            rule_text_hash=truth.rule_text_hash,
        ))
    return rows


SCHEMA = """
CREATE TABLE IF NOT EXISTS round_settlement_truth (
    market_id VARCHAR, condition_id VARCHAR,
    round_start_ms BIGINT, round_end_ms BIGINT, round_duration_s INTEGER,
    rule_version VARCHAR, rule_text_hash VARCHAR,
    resolution_source VARCHAR, comparator VARCHAR, tie_outcome VARCHAR,
    anchor_value DOUBLE, anchor_source_ts_ms BIGINT,
    final_value DOUBLE, final_source_ts_ms BIGINT,
    official_outcome VARCHAR, derived_outcome VARCHAR, outcomes_match BOOLEAN,
    admissibility VARCHAR, admissibility_reason VARCHAR,
    PRIMARY KEY (market_id)
);
CREATE TABLE IF NOT EXISTS settlement_checkpoint (
    market_id VARCHAR, checkpoint_index INTEGER,
    decision_ts_ms BIGINT, seconds_left INTEGER,
    anchor_value DOUBLE, current_reference_price DOUBLE, distance_from_anchor DOUBLE,
    outcome VARCHAR, rule_text_hash VARCHAR,
    PRIMARY KEY (market_id, checkpoint_index)
);
"""


def selftest() -> int:
    checks = 0

    def check(cond, text):
        nonlocal checks
        assert cond, text
        checks += 1
        print(f"  PASS  {text}")

    rule = tc.DEFAULT_SETTLEMENT_RULE
    START, END = 1_785_000_000_000, 1_785_000_900_000

    def make(anchor=100.0, final=102.0, official="UP", **kw):
        base = dict(
            market_id="btc-updown-15m-1", condition_id="0xabc",
            round_start_ms=START, round_end_ms=END,
            rule_version=rule.rule_version, rule_text_hash=rule.rule_text_hash,
            resolution_source=rule.source, comparator=rule.comparator,
            tie_outcome=rule.tie_outcome,
            anchor_value=anchor, anchor_source_ts_ms=START,
            final_value=final, final_source_ts_ms=END, official_outcome=official)
        base.update(kw)
        return RoundSettlementTruth(**base)

    # ---- THE DEFECT THIS REPLACES, stated as a test ----------------------------------
    truth = make(anchor=100.0, final=102.0, official="UP")
    check(truth.derived_outcome == "UP",
          "anchor 100, settles 102 -> UP, because the label compares the FINAL to the ANCHOR")
    cps = build_checkpoints(truth, {0: 100.0, 180: 103.0, 360: 104.0, 540: 101.5, 720: 99.0})
    check(len(cps) == 5, "five checkpoints from one round")
    check(len({c.outcome for c in cps}) == 1 and cps[0].outcome == "UP",
          "ALL FIVE share one outcome - they differ only in what was knowable, never in the "
          "answer")
    mid = next(c for c in cps if c.checkpoint_index == 2)
    check(mid.current_reference_price == 104.0 and mid.outcome == "UP",
          "the minute-6 row saw price at 104 and is still labelled UP - the old label compared "
          "102 < 104 and said DOWN, inverting this row")
    check(abs(mid.distance_from_anchor - 4.0) < 1e-9,
          "the decision price survives only as distance_from_anchor, a FEATURE")

    # A DOWN round, because every fixture above settles UP and `>=` makes a label that
    # compares the final to ITSELF also return UP. A mutation replacing the anchor with the
    # final value - the exact defect this module exists to replace - survived the first
    # version of this selftest for precisely that reason. Both outcomes must appear.
    down = make(anchor=100.0, final=98.0, official="DOWN")
    check(down.derived_outcome == "DOWN",
          "anchor 100, settles 98 -> DOWN. This is the assertion that pins the label to the "
          "ANCHOR: comparing the final to itself, or to the decision price, cannot produce it")
    check(down.admissibility()[0] == ADMISSIBLE,
          "and it reconciles, so the module is exercised on BOTH outcomes rather than only "
          "the one the comparator defaults to")
    dcps = build_checkpoints(down, {0: 100.0, 360: 96.0, 720: 101.0})
    check({c.outcome for c in dcps} == {"DOWN"},
          "its checkpoints are all DOWN - including the +720s row that saw price at 101, "
          "ABOVE the anchor, which a decision-anchored label would have called UP")
    check([c.seconds_left for c in cps] == [900, 720, 540, 360, 180],
          "seconds_left decreases across the lattice, so a conditional head can learn from it")

    # ---- admissibility: reconcile or quarantine, never relabel ------------------------
    check(make().admissibility()[0] == ADMISSIBLE, "a fully reconciled round is ADMISSIBLE")
    bad = make(official="DOWN")             # derived UP, official DOWN
    v, why = bad.admissibility()
    check(v == QUARANTINED and "derived" in why,
          f"a derived/official MISMATCH is quarantined ({why[:50]}) - one source is wrong and "
          f"which is unknown, so it is not silently relabelled")
    check(make(official=None).admissibility()[0] == QUARANTINED,
          "a round with no official outcome is quarantined - the canonical label IS the "
          "official one; the oracle values only reconstruct it")
    check(make(anchor_source_ts_ms=START + 60_000).admissibility()[0] == QUARANTINED,
          "an anchor observed a minute after the open is not this round's anchor")
    check(make(final_source_ts_ms=END + 60_000).admissibility()[0] == QUARANTINED,
          "and a final observed a minute after the close is not this round's settlement")
    check(make(rule_text_hash="").admissibility()[0] == QUARANTINED,
          "a round whose RULE is unknown cannot be graded under it")

    # A quarantined round must never produce training rows.
    try:
        build_checkpoints(bad, {0: 100.0})
        raise AssertionError("a quarantined round produced checkpoints")
    except RoundTruthError:
        checks += 1
        print("  PASS  and a quarantined round RAISES rather than emitting checkpoints - "
              "the quarantine is a gate, not an annotation")

    # ---- the tie, through this path too ----------------------------------------------
    check(make(anchor=100.0, final=100.0, official="UP").derived_outcome == "UP",
          "an exact tie derives UP, matching the venue rule rather than a second convention")

    # ---- causality --------------------------------------------------------------------
    for c in cps:
        assert c.decision_ts_ms >= truth.round_start_ms
        assert c.decision_ts_ms < truth.round_end_ms
    checks += 1
    print("  PASS  every checkpoint decision_ts lies inside the round and strictly BEFORE the "
          "close, so no row is decided after its own outcome is determined")
    try:
        build_checkpoints(truth, {900: 100.0}, offsets=(900,))
        raise AssertionError("a checkpoint at the close was accepted")
    except RoundTruthError:
        checks += 1
        print("  PASS  a checkpoint at or after the close RAISES")

    missing = build_checkpoints(truth, {0: 100.0, 360: 104.0})
    check(len(missing) == 2,
          "a checkpoint with no observed price is DROPPED, not interpolated - an invented "
          "decision price is a feature the model could never have seen live")

    check("PRIMARY KEY (market_id)" in SCHEMA
          and "PRIMARY KEY (market_id, checkpoint_index)" in SCHEMA,
          "the schema enforces one truth row per market and one row per checkpoint, so a "
          "conflicting anchor or a duplicate checkpoint cannot be inserted")

    print(f"\nROUND TRUTH SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--schema", action="store_true", help="print the DDL")
    args = ap.parse_args()
    if args.schema:
        print(SCHEMA)
        return 0
    return selftest()


if __name__ == "__main__":
    raise SystemExit(main())
