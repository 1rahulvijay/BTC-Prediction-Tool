"""BUILD_CANONICAL_CAUSAL_CHECKPOINT_DATASET - one admissible row per round per grid point.

WHAT MAKES THIS DIFFERENT FROM EVERY RETRACTED STUDY
    The retracted studies built their rows by joining a market STATE table to a QUOTE table and
    hoping the pairing was sane. It was not: the state postdated the decision in 93.5% of rows.

    This dataset performs NO such join. Every feature comes from a single row of
    `pm_round_snapshots`, which the recorder wrote atomically - BTC price, both order books,
    the depth ladder and p_hold observed in one instant by one process. There is nothing to
    mispair. That is a structural property, not a discipline, and it is the entire reason this
    dataset can be trusted where the earlier ones could not.

    Two joins remain and both are guarded:
      the GRID       picks the LAST snapshot at or before each checkpoint instant
      the SETTLEMENT is from the future by design, so it is segregated as a LABEL

THE GRID
    Checkpoints are declared in CHECKPOINTS_S. For each (round, checkpoint) the builder takes
    the snapshot with the SMALLEST seconds_left that is still >= the target - the most recent
    observation at or before that instant. `checkpoint_age_s` records how stale it was.

    A grid point AT the round start (300s for 5m, 900s for 15m) has no snapshot at or before
    it: recording begins microseconds later. Those checkpoints are ABSENT rather than filled
    from the next row. Absent is the honest answer; reaching forward is the defect.

EVIDENCE CLASSES
    July data has already shaped the research, so none of it is untouched. Each row carries the
    class its checkpoint falls in, and the promotion rule reads off it:

        PRE_ORACLE               before the Oracle served      diagnostic only
        LIVE_RESEARCH            2026-07-06 .. 2026-07-20      model and feature construction
        RETROSPECTIVE_VALIDATION 2026-07-21 .. 2026-08-01       candidate elimination
        FORWARD_UNTOUCHED        2026-08-02 onward             the only promotion evidence

    PRE_ORACLE is not in the original four. It exists because the live recorder holds rows from
    before the deployment, and forcing them into HISTORICAL_TRAIN (which means the 400-day
    archive) or LIVE_RESEARCH would mislabel them. A fifth honest class beats four tidy ones.

    python backend/research_data/checkpoint_builder.py --selftest
    python backend/research_data/checkpoint_builder.py --limit-rounds 200   # quick build
    python backend/research_data/checkpoint_builder.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "backend"))

from causal_validation import (  # noqa: E402
    MAX_CHECKPOINT_AGE_S, assert_frame, feature_columns,
)

DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or REPO / "data")
# The LIVE archive. data/analytics.duckdb is a stale pre-Oracle copy - see the coverage
# manifest headline. Naming the path here rather than defaulting is deliberate.
SOURCE_DB = DATA_DIR / "btc_duckdbs" / "execution_layer.duckdb"
OUTPUT = DATA_DIR / "research" / "causal_checkpoints_v1.parquet"

# Declared before any result is seen. Seconds remaining at each checkpoint.
CHECKPOINTS_S = (900, 720, 600, 540, 360, 300, 240, 180, 120, 60, 30, 15)

# `ts` is SECONDS in pm_round_snapshots and MILLISECONDS in analytics.rule_paper_trades. Both
# are called `ts`. Hardcoding /1000 sent every row to 1970 and silently classified the entire
# dataset PRE_ORACLE, which looked plausible enough to ship. Infer instead of assuming.
EPOCH_SECONDS = "(CASE WHEN {col} > 1e11 THEN {col} / 1000.0 ELSE {col} END)"

EVIDENCE_BOUNDS = (
    ("PRE_ORACLE", None, "2026-07-06"),
    ("LIVE_RESEARCH", "2026-07-06", "2026-07-21"),
    ("RETROSPECTIVE_VALIDATION", "2026-07-21", "2026-08-02"),
    ("FORWARD_UNTOUCHED", "2026-08-02", None),
)

SNAPSHOT_COLUMNS = (
    "btc_price", "anchor_price", "distance_pct", "distance_bps", "current_side",
    "vol_60s_pct", "p_hold_cur", "p_hold_up", "p_hold_down", "model_version",
    "decision_tier", "no_trade_reason", "price_source",
    "up_bid", "up_ask", "up_mid", "up_spread", "up_top_ask_size", "up_d1", "up_d2", "up_d5",
    "down_bid", "down_ask", "down_mid", "down_spread", "down_top_ask_size",
    "down_d1", "down_d2", "down_d5",
)


def _evidence_case() -> str:
    """SQL CASE assigning each checkpoint to its evidence class from its own timestamp."""
    parts = []
    for name, start, end in EVIDENCE_BOUNDS:
        conditions = []
        if start:
            conditions.append(f"day >= '{start}'")
        if end:
            conditions.append(f"day < '{end}'")
        clause = " AND ".join(conditions) if conditions else "TRUE"
        parts.append(f"WHEN {clause} THEN '{name}'")
    return "CASE " + " ".join(parts) + " ELSE 'UNCLASSIFIED' END"


def build_query(limit_rounds: int | None) -> str:
    grid = ", ".join(f"({value})" for value in CHECKPOINTS_S)
    columns = ",\n           ".join(f"c.{name}" for name in SNAPSHOT_COLUMNS)
    round_filter = ""
    if limit_rounds:
        round_filter = (f"WHERE s.slug IN (SELECT DISTINCT slug FROM pm_round_snapshots "
                        f"ORDER BY slug LIMIT {int(limit_rounds)})")
    epoch = EPOCH_SECONDS.format(col="ts")
    return f"""
WITH grid(checkpoint_s) AS (VALUES {grid}),
candidates AS (
    SELECT s.*, g.checkpoint_s,
           row_number() OVER (PARTITION BY s.slug, g.checkpoint_s
                              ORDER BY s.seconds_left ASC) AS rn
    FROM pm_round_snapshots s
    JOIN grid g
      -- CAUSAL: only snapshots at or before the grid instant are eligible, and the round-start
      -- point is excluded because no snapshot precedes it.
      ON s.seconds_left >= g.checkpoint_s
     AND g.checkpoint_s < s.horizon * 60
    {round_filter}
),
chosen AS (SELECT * FROM candidates WHERE rn = 1)
SELECT c.slug,
       c.condition_id,
       CAST(c.horizon AS INTEGER) AS horizon,
       c.anchor_ts,
       CAST(c.checkpoint_s AS INTEGER) AS checkpoint_s,
       c.ts AS snapshot_ts,
       c.seconds_left,
       c.seconds_left - c.checkpoint_s AS checkpoint_age_s,
       (c.seconds_left - c.checkpoint_s) <= {MAX_CHECKPOINT_AGE_S} AS eligible,
       {_evidence_case()} AS evidence_class,
       {columns},
       -- LABEL. From after the checkpoint by construction; segregated in OUTCOME_COLUMNS.
       t.settled_side, t.up_win, t.down_win, t.resolution_source, t.expiry_btc
FROM (SELECT *, strftime(to_timestamp({epoch}), '%Y-%m-%d') AS day FROM chosen) c
LEFT JOIN pm_round_settlements t ON t.slug = c.slug AND t.horizon = c.horizon
ORDER BY c.ts, c.checkpoint_s
"""


def build(limit_rounds: int | None = None):
    import duckdb
    if not SOURCE_DB.is_file():
        raise FileNotFoundError(f"live archive not found at {SOURCE_DB}")
    con = duckdb.connect(str(SOURCE_DB), read_only=True)
    try:
        return con.execute(build_query(limit_rounds)).df()
    finally:
        con.close()


def summarise(frame) -> dict:
    validation = assert_frame(frame)
    by_class = frame["evidence_class"].value_counts().to_dict()
    per_checkpoint = (frame.groupby("checkpoint_s")
                      .agg(rows=("slug", "size"),
                           median_age_s=("checkpoint_age_s", "median"),
                           eligible=("eligible", "sum"))
                      .reset_index().to_dict("records"))
    settled = int(frame["settled_side"].notna().sum())
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(SOURCE_DB),
        "checkpoints_s": list(CHECKPOINTS_S),
        "max_checkpoint_age_s": MAX_CHECKPOINT_AGE_S,
        "validation": validation,
        "rounds": int(frame["slug"].nunique()),
        "rows_by_evidence_class": {str(k): int(v) for k, v in by_class.items()},
        "rows_with_settlement": settled,
        "settlement_coverage": round(settled / len(frame), 4) if len(frame) else 0.0,
        "per_checkpoint": [{k: (float(v) if k == "median_age_s" else int(v))
                            for k, v in row.items()} for row in per_checkpoint],
        "feature_columns": feature_columns(frame.columns),
    }


def selftest() -> int:
    """Prove the as-of selection is causal on a table built with a known answer."""
    import duckdb

    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    def make_probe(ts_values):
        """Build the same two-snapshot round with timestamps in a caller-chosen unit."""
        probe = duckdb.connect(":memory:")
        probe.execute("CREATE TABLE pm_round_snapshots (ts DOUBLE, slug VARCHAR,"
                      " condition_id VARCHAR, horizon DOUBLE, anchor_ts BIGINT,"
                      " seconds_left DOUBLE, " +
                      ", ".join(f"{name} DOUBLE" if name not in
                                ("current_side", "model_version", "decision_tier",
                                 "no_trade_reason", "price_source") else f"{name} VARCHAR"
                                for name in SNAPSHOT_COLUMNS) + ")")
        probe.execute("CREATE TABLE pm_round_settlements (slug VARCHAR, horizon DOUBLE,"
                      " settled_side VARCHAR, up_win BOOLEAN, down_win BOOLEAN,"
                      " resolution_source VARCHAR, expiry_btc DOUBLE)")
        for stamp, left in zip(ts_values, (63.0, 58.0)):
            probe.execute(
                "INSERT INTO pm_round_snapshots VALUES (?,?,?,?,?,?," +
                ",".join(["?"] * len(SNAPSHOT_COLUMNS)) + ")",
                [stamp, "r1", "c1", 5.0, 0, left] +
                [("UP" if name == "current_side" else "v" if name in
                  ("model_version", "decision_tier", "no_trade_reason", "price_source")
                  else 1.0) for name in SNAPSHOT_COLUMNS])
        probe.execute("INSERT INTO pm_round_settlements VALUES ('r1', 5.0, 'UP', TRUE, FALSE,"
                      " 'official:clob', 64000.0)")
        return probe

    # SECONDS, as pm_round_snapshots actually stores it. 1_783_400_000 is 2026-07-07, inside
    # LIVE_RESEARCH - so a unit mistake shows up as a WRONG CLASS rather than as nothing at
    # all. The first version of this selftest used milliseconds and asserted only the grid
    # behaviour, which is why a hardcoded /1000 shipped and sent 56,467 real rows to 1970.
    base = 1_783_400_000
    con = make_probe((base, base + 5))
    frame = con.execute(build_query(None)).df()
    con.close()

    row60 = frame[frame["checkpoint_s"] == 60]
    check(len(row60) == 1, "exactly one row per round per reachable checkpoint")
    check(float(row60["seconds_left"].iloc[0]) == 63.0,
          "the 60s checkpoint takes the 63.0s snapshot - the last one BEFORE it, not the 58.0s "
          "one after")
    check(abs(float(row60["checkpoint_age_s"].iloc[0]) - 3.0) < 1e-9,
          "checkpoint_age_s records the 3.0s staleness rather than hiding it")
    check(300 not in set(frame["checkpoint_s"]),
          "the round-start grid point (300s for a 5m round) is ABSENT, not back-filled")
    check(bool(row60["eligible"].iloc[0]), "a 3.0s-old snapshot is eligible at a 10s bound")
    check(str(row60["settled_side"].iloc[0]) == "UP",
          "the settlement label is attached")
    check(str(row60["evidence_class"].iloc[0]) == "LIVE_RESEARCH",
          "a 2026-07-07 checkpoint lands in LIVE_RESEARCH - the epoch unit is inferred, so a "
          "seconds/milliseconds mixup cannot silently classify everything PRE_ORACLE")

    check("settled_side" not in feature_columns(frame.columns),
          "the settlement label is NOT offered as a feature")
    summary = assert_frame(frame)
    check(summary["max_checkpoint_age_s"] >= 0, "the built frame passes causal validation")

    # And the guard must be able to REFUSE: flip one row to look ahead.
    from causal_validation import NonCausalRow
    broken = frame.copy()
    broken.loc[broken.index[0], "checkpoint_age_s"] = -4.0
    try:
        assert_frame(broken)
        check(False, "unreachable")
    except NonCausalRow:
        check(True, "a planted look-ahead row makes the whole build fail")

    print(f"\nCHECKPOINT BUILDER SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--limit-rounds", type=int, default=None)
    args = parser.parse_args()

    print("=" * 96)
    print("CANONICAL CAUSAL CHECKPOINT DATASET")
    print("=" * 96)
    if args.selftest:
        return selftest()

    frame = build(args.limit_rounds)
    if frame.empty:
        print("  no rows built - is the live archive present?")
        return 1
    summary = summarise(frame)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(OUTPUT, index=False)
    manifest_path = OUTPUT.with_suffix(".manifest.json")
    try:
        from artifact_identity import hash_file
        summary["dataset_sha256"] = hash_file(OUTPUT)
    except Exception as exc:
        summary["dataset_sha256"] = f"unavailable: {type(exc).__name__}"
    manifest_path.write_text(json.dumps(summary, indent=1, sort_keys=True) + "\n",
                             encoding="utf-8")

    print(f"  rounds                : {summary['rounds']:,}")
    print(f"  checkpoint rows       : {summary['validation']['rows']:,}")
    print(f"  eligible (<= {MAX_CHECKPOINT_AGE_S:.0f}s old): "
          f"{summary['validation']['eligible_rows']:,}")
    print(f"  median checkpoint age : {summary['validation']['median_checkpoint_age_s']:.2f} s")
    print(f"  max checkpoint age    : {summary['validation']['max_checkpoint_age_s']:.2f} s")
    print(f"  settlement coverage   : {summary['settlement_coverage']:.1%}")
    print(f"  feature columns       : {len(summary['feature_columns'])}")
    print()
    print("  rows by evidence class (only FORWARD_UNTOUCHED may promote):")
    for name, _, _ in EVIDENCE_BOUNDS:
        print(f"    {name:<26} {summary['rows_by_evidence_class'].get(name, 0):>9,}")
    print()
    print(f"{'checkpoint':>11}{'rows':>10}{'eligible':>10}{'median age s':>14}")
    for row in summary["per_checkpoint"]:
        print(f"{row['checkpoint_s']:>10}s{row['rows']:>10,}{row['eligible']:>10,}"
              f"{row['median_age_s']:>14.2f}")
    print()
    print(f"  wrote {OUTPUT.relative_to(REPO).as_posix()}")
    print(f"  wrote {manifest_path.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
