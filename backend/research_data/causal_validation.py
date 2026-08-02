"""The causal contract every research dataset in this repository must satisfy.

WHY THIS IS A MODULE AND NOT A COMMENT
    Five studies were retracted because a market STATE was paired with an executable QUOTE that
    the state postdated - in 93.5% of rows, median +8.1s. Every one of those studies had a
    preregistered protocol, matched controls and day-block bounds. None of them had a function
    that could say "this row is not admissible".

    So the contract lives in code, it is imported by the builders, and it RAISES.

THE STRUCTURAL FIX, WHICH MATTERS MORE THAN THE CHECKS
    The checkpoint dataset does not join a state table to a quote table at all. Every feature in
    a row comes from ONE row of pm_round_snapshots, which the recorder wrote atomically: the
    BTC price, both order books, the depth ladder and p_hold were all observed in the same
    instant by the same process. There is no pairing to get wrong.

    That is why this contract is short. It guards the two places where a join still happens:

      1. THE GRID.       A checkpoint at T-300s must use the last snapshot at or before that
                         instant - never the next one after it. Encoded as checkpoint_age_s >= 0.
      2. THE OUTCOME.    Settlement is joined in, and it is deliberately from the future. That
                         is legitimate for a LABEL and fatal for a FEATURE, so outcome columns
                         are named and segregated rather than trusted to discipline.

STALENESS IS NOT CAUSALITY
    A snapshot 40 seconds before the grid point is causal and probably useless. Age is STORED
    and eligibility is computed from a DECLARED threshold, so filtering reshapes the population
    visibly instead of silently.

    python backend/research_data/causal_validation.py --selftest
"""
from __future__ import annotations

import argparse

# Declared once, here, so a builder cannot quietly widen it.
MAX_CHECKPOINT_AGE_S = 10.0

#: Columns that describe what happened AFTER the checkpoint. Legitimate as labels, never as
#: model inputs. Named explicitly so a feature list can be checked against them mechanically.
OUTCOME_COLUMNS = frozenset({
    "settled_side", "up_win", "down_win", "resolution_source", "expiry_btc", "resolved_at",
})

#: Columns identifying the ROW rather than describing the state of the world.
#:
#: The clock fields are deliberately NOT here. `seconds_left`, `checkpoint_s` and `horizon` are
#: observations available at the decision instant and are among the most informative inputs
#: any of these problems has - a first version filed them under identity, which would have
#: silently starved a hold-versus-exit head of time-to-expiry for a bookkeeping reason.
#: `checkpoint_age_s` stays: it describes how stale the RECORD is, not the market.
IDENTITY_COLUMNS = frozenset({
    "opportunity_id", "slug", "condition_id", "anchor_ts",
    "snapshot_ts", "checkpoint_age_s", "evidence_class", "eligible",
})


class NonCausalRow(Exception):
    """Raised when a dataset row would use information from after its own checkpoint."""


def row_violations(row: dict) -> list[str]:
    """Every breach in one row. Empty list means admissible."""
    problems: list[str] = []
    age = row.get("checkpoint_age_s")
    if age is None:
        problems.append("checkpoint_age_s is missing - admissibility cannot be established")
    elif age < 0:
        problems.append(
            f"checkpoint_age_s {age:.3f} is negative: the snapshot is from AFTER the checkpoint "
            "instant. This is the defect that invalidated five studies")
    seconds_left = row.get("seconds_left")
    horizon = row.get("horizon")
    if seconds_left is not None and horizon is not None and seconds_left > float(horizon) * 60.0:
        problems.append(
            f"seconds_left {seconds_left} exceeds the {horizon}m round length - the snapshot "
            "does not belong to this round")
    checkpoint = row.get("checkpoint_s")
    if (checkpoint is not None and seconds_left is not None and seconds_left < checkpoint):
        problems.append(
            f"seconds_left {seconds_left} is below the checkpoint target {checkpoint}: the row "
            "was taken after the grid point, not at or before it")
    return problems


def assert_frame(frame) -> dict:
    """Validate a built dataset. Raises rather than returning a dataset nobody should use."""
    if "checkpoint_age_s" not in frame.columns:
        raise NonCausalRow("dataset has no checkpoint_age_s column; admissibility is unprovable")

    negative = int((frame["checkpoint_age_s"] < 0).sum())
    if negative:
        raise NonCausalRow(
            f"{negative} rows have a snapshot from AFTER their checkpoint instant. The build is "
            "refused - this is the exact defect that retracted five studies.")

    overlong = int((frame["seconds_left"] > frame["horizon"] * 60.0).sum())
    if overlong:
        raise NonCausalRow(f"{overlong} rows carry seconds_left beyond their round length")

    late = int((frame["seconds_left"] < frame["checkpoint_s"]).sum())
    if late:
        raise NonCausalRow(f"{late} rows sit past their own grid point")

    return {
        "rows": int(len(frame)),
        "max_checkpoint_age_s": float(frame["checkpoint_age_s"].max()),
        "median_checkpoint_age_s": float(frame["checkpoint_age_s"].median()),
        "eligible_rows": int((frame["checkpoint_age_s"] <= MAX_CHECKPOINT_AGE_S).sum()),
    }


#: Every path label carries this prefix. A hand-maintained set of outcome names works until
#: someone adds the thirty-seventh label and forgets; a prefix cannot be forgotten, because the
#: column does not exist without it. OUTCOME_COLUMNS stays for the settlement columns, which
#: the recorder named and which are not ours to rename.
LABEL_PREFIX = "label_"


def is_label(column: str) -> bool:
    return column.startswith(LABEL_PREFIX) or column in OUTCOME_COLUMNS


def feature_columns(columns) -> list[str]:
    """Everything that may be fed to a model: not identity, not outcome, not a label."""
    return sorted(name for name in set(columns)
                  if name not in IDENTITY_COLUMNS and not is_label(name))


def selftest() -> int:
    import pandas as pd

    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    good = {"checkpoint_age_s": 1.2, "seconds_left": 301.2, "horizon": 15, "checkpoint_s": 300}
    check(row_violations(good) == [], "a snapshot 1.2s before the grid point is admissible")

    # A grid point AT the round start has no snapshot at or before it - the first observation
    # lands microseconds later. The checkpoint is then absent by construction; it is never
    # satisfied by reaching forward for the next row.
    check(row_violations({"checkpoint_age_s": -0.0003, "seconds_left": 299.9997,
                          "horizon": 5, "checkpoint_s": 300}) != [],
          "the round-start grid point cannot be met by the FIRST snapshot after it")

    bad = {**good, "checkpoint_age_s": -3.0, "seconds_left": 297.0}
    problems = row_violations(bad)
    check(any("AFTER the checkpoint" in p for p in problems),
          "a snapshot from AFTER the checkpoint is REFUSED")

    check(row_violations({**good, "seconds_left": 1000.0})[0].startswith("seconds_left"),
          "a snapshot longer than the round length is REFUSED")
    check(any("admissibility" in p for p in row_violations({"seconds_left": 1, "horizon": 5})),
          "a row with no checkpoint_age_s is REFUSED, not assumed fine")

    frame = pd.DataFrame([good, {**good, "checkpoint_age_s": 0.4, "seconds_left": 300.4}])
    summary = assert_frame(frame)
    check(summary["rows"] == 2 and summary["eligible_rows"] == 2,
          "a clean frame validates and reports its age distribution")

    try:
        assert_frame(pd.DataFrame([good, bad]))
        check(False, "unreachable")
    except NonCausalRow as exc:
        check("retracted five studies" in str(exc),
              "a frame containing one non-causal row is REFUSED WHOLE, not filtered")

    features = feature_columns(["up_ask", "p_hold_cur", "settled_side", "slug", "up_win"])
    check(features == ["p_hold_cur", "up_ask"],
          "outcome and identity columns are excluded from the feature list")

    # The prefix rule is what makes this safe as labels multiply. A label invented today, never
    # added to any list, must still be refused as a model input.
    check(feature_columns(["up_ask", "label_invented_tomorrow"]) == ["up_ask"],
          "an unheard-of label_* column is excluded WITHOUT being registered anywhere")
    check(is_label("label_remaining_max_up_usd") and is_label("settled_side")
          and not is_label("up_ask"),
          "is_label covers both the prefix rule and the recorder's own outcome names")
    check(set(feature_columns(["seconds_left", "checkpoint_s", "horizon", "slug"]))
          == {"seconds_left", "checkpoint_s", "horizon"},
          "the CLOCK is a feature, not identity - time-to-expiry drives every one of these "
          "problems and filing it under identity would starve a head of its best input")

    print(f"\nCAUSAL VALIDATION SELFTEST: PASS ({checks} checks)")
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
