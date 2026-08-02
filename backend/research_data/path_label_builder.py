"""BUILD_CANONICAL_PATH_LABELS - remaining-move and anchor-crossing labels, computed once.

WHY ONCE, CENTRALLY
    Every retracted study built its own labels inline. When two scripts disagree about what
    "the leader held" means, the disagreement never surfaces as an error - it surfaces as two
    incompatible numbers that both look like results. So the labels live here, are computed
    once, and every campaign reads the same parquet.

LABELS ARE ALLOWED TO SEE THE FUTURE. FEATURES ARE NOT.
    That is the whole distinction, and it is the one place this file could do real damage. Each
    label scans the round's path STRICTLY AFTER its checkpoint - that is the point of a label.
    The protection is that a label can never be mistaken for an input:

        every column here is prefixed `label_`
        causal_validation.feature_columns() excludes anything matching that prefix

    A hand-maintained list of outcome names works until someone adds the thirty-seventh label
    and forgets. The prefix cannot be forgotten because the column does not exist without it.

WHAT IS COMPUTED, per checkpoint row
    remaining move   max upward / max downward excursion in USD, remaining range, terminal
                     distance from the anchor, terminal return
    crossings        time to the next anchor crossing, how many crossings remain, the direction
                     and time of the FINAL crossing, whether the current side survives
    normalised       whether the remaining move clears 0.5 / 1.0 / 1.5 / 2.0 sigma

    The sigma is a declared PROXY: `vol_60s_pct` (a 60-second realized-volatility estimate that
    was on the snapshot at the checkpoint, so it is causal) scaled to the remaining window by
    sqrt(time). It is not an option-implied vol and is not claimed to be one.

THE PATH IS THE SAME TABLE AS THE CHECKPOINTS
    Both come from `pm_round_snapshots`, so a label can never describe a different round, a
    different anchor, or a price series the checkpoint never saw. Median spacing is 1.76s.

    A checkpoint whose forward path is EMPTY - the round ended, or recording stopped - gets
    NULL labels and `label_path_samples = 0`, never a zero-filled row. Zero and unknown are
    different, and a model trained on the difference silently learns the recorder's downtime.

    python backend/research_data/path_label_builder.py --selftest
    python backend/research_data/path_label_builder.py
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

from causal_validation import feature_columns, is_label  # noqa: E402
from checkpoint_builder import OUTPUT as CHECKPOINTS  # noqa: E402
from checkpoint_builder import SOURCE_DB  # noqa: E402

DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or REPO / "data")
OUTPUT = DATA_DIR / "research" / "causal_checkpoint_labels_v1.parquet"

# Declared before any result is seen.
SIGMA_MULTIPLES = (0.5, 1.0, 1.5, 2.0)
# vol_60s_pct is a standard deviation over a 60-second window, expressed in percent.
VOL_WINDOW_S = 60.0

# Burst horizons, in seconds after the checkpoint.
BURST_WINDOWS_S = (5, 15, 30, 60)
# Declared burst sizes in USD. Deliberately NOT stored as columns - see burst_indicator().
BURST_THRESHOLDS_USD = (10, 25, 50, 100, 200)
# Flip-reversion horizons, measured from the FIRST crossing, not from the checkpoint.
FLIP_REVERT_WINDOWS_S = (5, 15, 30, 60)


def burst_indicator(frame, direction: str, usd: float, window_s: int):
    """Did price move `usd` in `direction` within `window_s` of the checkpoint?

    WHY A FUNCTION AND NOT FORTY COLUMNS
        The burst grid is 2 directions x 5 sizes x 4 windows = 40 booleans, and every one is a
        pure function of the continuous max excursion already stored for that window. Storing
        them would cache a computation that goes stale the moment anyone edits the threshold
        grid - the parquet keeps serving yesterday's answer under today's column name, which is
        the same species of silent wrongness this directory exists to prevent.

        The continuous excursion is the sufficient statistic. Any threshold, including ones not
        in BURST_THRESHOLDS_USD, is one comparison away and cannot disagree with the data.

    NULL where the window held no samples: "not observed" is not "no burst"."""
    if direction not in ("up", "down"):
        raise ValueError("direction must be 'up' or 'down'")
    if window_s not in BURST_WINDOWS_S:
        raise ValueError(f"window_s must be one of {BURST_WINDOWS_S}")
    excursion = frame[f"label_max_{direction}_usd_{window_s}s"]
    return (excursion >= float(usd)).astype("boolean").where(excursion.notna())


def build_query() -> str:
    # Computed in an OUTER select: SQL cannot reference an alias defined in the same SELECT
    # list, and inlining the sigma expression four times invites the copies to drift apart.
    sigma_columns = ",\n       ".join(
        f"CASE WHEN sigma_remaining_usd IS NULL OR sigma_remaining_usd <= 0 THEN NULL "
        f"ELSE label_remaining_range_usd >= {multiple} * sigma_remaining_usd END "
        f"AS label_move_exceeds_{str(multiple).replace('.', 'p')}_sigma"
        for multiple in SIGMA_MULTIPLES)
    # Per-window high and low, one conditional aggregate each. A window with no samples yields
    # NULL rather than the checkpoint price, so an unobserved window can never read as "flat".
    burst_aggregates = ",\n           ".join(
        f"max(m.btc_price) FILTER (WHERE m.seconds_left >= c.checkpoint_s - {window}) "
        f"AS high_{window}s,\n"
        f"           min(m.btc_price) FILTER (WHERE m.seconds_left >= c.checkpoint_s - {window})"
        f" AS low_{window}s"
        for window in BURST_WINDOWS_S)
    # DuckDB's greatest() IGNORES NULL: greatest(NULL, 0) is 0, not NULL. Left unguarded, an
    # unobserved window records as a calm one - the precise failure this file claims to avoid.
    burst_columns = ",\n       ".join(
        f"CASE WHEN a.high_{window}s IS NULL THEN NULL "
        f"ELSE greatest(a.high_{window}s - c.btc_price, 0) END AS label_max_up_usd_{window}s,\n"
        f"       CASE WHEN a.low_{window}s IS NULL THEN NULL "
        f"ELSE greatest(c.btc_price - a.low_{window}s, 0) END AS label_max_down_usd_{window}s"
        for window in BURST_WINDOWS_S)
    # Reversion is timed from the FIRST crossing, not from the checkpoint. "The flip lasted 5
    # seconds" is a statement about the flip; anchoring it to the checkpoint would make the
    # same flip look more or less durable purely by how early in the round it happened.
    flip_columns = ",\n       ".join(
        f"CASE WHEN f.first_cross_left IS NULL THEN NULL ELSE f.second_cross_left IS NOT NULL "
        f"AND (f.first_cross_left - f.second_cross_left) <= {window} END "
        f"AS label_flip_reverts_{window}s"
        for window in FLIP_REVERT_WINDOWS_S)
    return f"""
WITH path AS (
    -- One pass over every round's price path. `side` is measured against the round's own
    -- anchor; `prev_side` is the previous sample in TIME order, which is DESCENDING
    -- seconds_left. Getting that ordering backwards would invert every crossing.
    SELECT slug,
           seconds_left,
           btc_price,
           anchor_price,
           CASE WHEN btc_price >= anchor_price THEN 1 ELSE -1 END AS side,
           lag(CASE WHEN btc_price >= anchor_price THEN 1 ELSE -1 END)
               OVER (PARTITION BY slug ORDER BY seconds_left DESC) AS prev_side
    FROM pm_round_snapshots
),
marked AS (
    SELECT *, (prev_side IS NOT NULL AND side <> prev_side) AS crossed FROM marked_src
),
aggregated AS (
    SELECT c.slug,
           c.checkpoint_s,
           count(*)                                        AS label_path_samples,
           max(m.btc_price)                                AS future_high,
           min(m.btc_price)                                AS future_low,
           arg_min(m.btc_price, m.seconds_left)            AS terminal_price,
           arg_min(m.side, m.seconds_left)                 AS terminal_side,
           count(*) FILTER (WHERE m.crossed)               AS label_future_crossings,
           min(c.checkpoint_s - m.seconds_left)
               FILTER (WHERE m.crossed)                    AS label_time_to_next_cross_s,
           min(m.seconds_left) FILTER (WHERE m.crossed)    AS final_cross_seconds_left,
           arg_min(m.side, m.seconds_left)
               FILTER (WHERE m.crossed)                    AS final_cross_side,
           {burst_aggregates}
    FROM checkpoints c
    -- STRICTLY AFTER the checkpoint. `<` not `<=`: the checkpoint's own sample is an input,
    -- and letting it into its own label would leak the present into the future.
    JOIN marked m ON m.slug = c.slug AND m.seconds_left < c.checkpoint_s
    GROUP BY c.slug, c.checkpoint_s
),
crossings AS (
    -- Every crossing after the checkpoint, ranked in TIME order so the first and the second
    -- can be addressed individually. seconds_left DESCENDS with time, hence ORDER BY ... DESC.
    SELECT c.slug, c.checkpoint_s, m.seconds_left,
           row_number() OVER (PARTITION BY c.slug, c.checkpoint_s
                              ORDER BY m.seconds_left DESC) AS cross_rank
    FROM checkpoints c
    JOIN marked m ON m.slug = c.slug AND m.seconds_left < c.checkpoint_s AND m.crossed
),
flip AS (
    SELECT slug, checkpoint_s,
           max(seconds_left) FILTER (WHERE cross_rank = 1) AS first_cross_left,
           max(seconds_left) FILTER (WHERE cross_rank = 2) AS second_cross_left,
           count(*)                                        AS crossings_after
    FROM crossings GROUP BY slug, checkpoint_s
),
labelled AS (
SELECT c.slug,
       c.checkpoint_s,
       c.horizon,
       c.snapshot_ts,
       c.evidence_class,
       c.eligible,
       coalesce(a.label_path_samples, 0)                   AS label_path_samples,
       -- Excursions are measured FROM the checkpoint price, and clipped at zero: an "upward
       -- excursion" that never went up is 0, not a negative number. The CASE is load-bearing -
       -- greatest() IGNORES NULL in DuckDB, so the bare form turned all 7,160 empty-path rows
       -- into a confident 0.0 "no move" instead of NULL.
       CASE WHEN a.future_high IS NULL THEN NULL
            ELSE greatest(a.future_high - c.btc_price, 0) END
                                                           AS label_remaining_max_up_usd,
       CASE WHEN a.future_low IS NULL THEN NULL
            ELSE greatest(c.btc_price - a.future_low, 0) END
                                                           AS label_remaining_max_down_usd,
       (a.future_high - a.future_low)                      AS label_remaining_range_usd,
       (a.terminal_price - c.anchor_price)                 AS label_terminal_distance_usd,
       (a.terminal_price - c.btc_price) / nullif(c.btc_price, 0)
                                                           AS label_terminal_return,
       a.label_future_crossings,
       a.label_time_to_next_cross_s,
       CASE WHEN a.label_future_crossings > 0
            THEN c.checkpoint_s - a.final_cross_seconds_left END
                                                           AS label_final_cross_after_s,
       CASE WHEN a.label_future_crossings > 0
            THEN CASE WHEN a.final_cross_side = 1 THEN 'UP' ELSE 'DOWN' END END
                                                           AS label_final_cross_direction,
       CASE WHEN a.terminal_side = 1 THEN 'UP' ELSE 'DOWN' END
                                                           AS label_terminal_side,
       -- Does the side leading AT the checkpoint still lead at the end of the path?
       (CASE WHEN c.btc_price >= c.anchor_price THEN 1 ELSE -1 END) = a.terminal_side
                                                           AS label_current_side_survives,
       (a.label_future_crossings = 0)                      AS label_no_further_crossing,
       -- Is the FIRST crossing after this checkpoint also the LAST one? This is the question a
       -- flip-persistence head answers: durable terminal flip, or temporary excursion?
       CASE WHEN f.crossings_after IS NULL THEN NULL
            ELSE f.crossings_after = 1 END                  AS label_first_cross_is_final,
       {flip_columns},
       {burst_columns},
       -- SETTLEMENT-GROUNDED, and the one most studies actually want: does the side leading at
       -- this checkpoint WIN THE CONTRACT? Distinct from label_current_side_survives, which
       -- only asks whether it survives to the end of the RECORDED PATH. Measured, those two
       -- disagree on 10.7% of rounds at the 15s checkpoint - the recorded path is not the
       -- settlement source, it ends before expiry, and the oracle is a different feed. Using
       -- the path as a settlement proxy would bake that 10.7% straight into a head.
       CASE WHEN c.settled_side IS NULL THEN NULL
            ELSE (CASE WHEN c.btc_price >= c.anchor_price THEN 'UP' ELSE 'DOWN' END)
                 = c.settled_side END                      AS label_checkpoint_side_wins,
       CASE WHEN c.settled_side IS NULL OR a.terminal_side IS NULL THEN NULL
            ELSE (CASE WHEN a.terminal_side = 1 THEN 'UP' ELSE 'DOWN' END)
                 = c.settled_side END                      AS label_path_agrees_with_settlement,
       -- Causal sigma proxy: vol_60s_pct was on the snapshot AT the checkpoint. Scaled to the
       -- remaining window by sqrt(t), which assumes a random walk and is declared, not hidden.
       (c.vol_60s_pct / 100.0) * c.btc_price / sqrt({VOL_WINDOW_S}) * sqrt(c.checkpoint_s)
                                                           AS sigma_remaining_usd
FROM checkpoints c
LEFT JOIN aggregated a ON a.slug = c.slug AND a.checkpoint_s = c.checkpoint_s
LEFT JOIN flip f ON f.slug = c.slug AND f.checkpoint_s = c.checkpoint_s
)
SELECT *,
       {sigma_columns}
FROM labelled
ORDER BY snapshot_ts, checkpoint_s
"""


def _prepared_query() -> str:
    """`marked` reads from a named source so the selftest can substitute a probe table."""
    return build_query().replace("FROM marked_src", "FROM path")


def build(con=None):
    """Compute labels. `con` lets the selftest supply an in-memory database."""
    import duckdb

    owned = con is None
    if owned:
        if not CHECKPOINTS.is_file():
            raise FileNotFoundError(
                f"{CHECKPOINTS} not found - run checkpoint_builder.py first")
        con = duckdb.connect(str(SOURCE_DB), read_only=True)
        con.execute("CREATE OR REPLACE TEMP VIEW checkpoints AS "
                    f"SELECT * FROM read_parquet('{CHECKPOINTS.as_posix()}')")
    try:
        return con.execute(_prepared_query()).df()
    finally:
        if owned:
            con.close()


def summarise(frame) -> dict:
    import pandas as pd

    labelled = frame[frame["label_path_samples"] > 0]
    crossing = labelled[labelled["label_future_crossings"] > 0]
    by_checkpoint = (labelled.groupby("checkpoint_s")
                     .agg(rows=("slug", "size"),
                          median_range_usd=("label_remaining_range_usd", "median"),
                          p90_range_usd=("label_remaining_range_usd",
                                         lambda s: float(s.quantile(0.90))),
                          any_crossing_rate=("label_no_further_crossing",
                                             lambda s: float(1.0 - s.mean())),
                          side_survives_rate=("label_current_side_survives", "mean"))
                     .reset_index())
    # Burst rates over the DECLARED grid, derived through the same helper research uses, so the
    # published number and the training target cannot come from different code paths.
    burst_rates = {}
    for window in BURST_WINDOWS_S:
        for usd in BURST_THRESHOLDS_USD:
            for direction in ("up", "down"):
                series = burst_indicator(frame, direction, usd, window).dropna()
                if len(series):
                    burst_rates[f"{direction}_{usd}usd_{window}s"] = round(
                        float(series.mean()), 5)
    flipped = frame[frame["label_first_cross_is_final"].notna()]
    flip_rates = {
        "rows_with_a_crossing": int(len(flipped)),
        "first_cross_is_final": round(float(flipped["label_first_cross_is_final"].mean()), 4)
        if len(flipped) else None,
        **{f"reverts_within_{w}s": round(float(flipped[f"label_flip_reverts_{w}s"].mean()), 4)
           for w in FLIP_REVERT_WINDOWS_S if len(flipped)},
    }
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "source_checkpoints": str(CHECKPOINTS),
        "sigma_multiples": list(SIGMA_MULTIPLES),
        "burst_windows_s": list(BURST_WINDOWS_S),
        "burst_thresholds_usd": list(BURST_THRESHOLDS_USD),
        "burst_rates": burst_rates,
        "flip_rates": flip_rates,
        "rows": int(len(frame)),
        "rows_with_forward_path": int(len(labelled)),
        "rows_with_empty_path": int((frame["label_path_samples"] == 0).sum()),
        "median_path_samples": float(labelled["label_path_samples"].median())
        if len(labelled) else None,
        "rows_with_a_crossing": int(len(crossing)),
        "label_columns": sorted(c for c in frame.columns if is_label(c)),
        "feature_columns_offered": feature_columns(frame.columns),
        "per_checkpoint": json.loads(by_checkpoint.to_json(orient="records"))
        if isinstance(by_checkpoint, pd.DataFrame) else [],
    }


def selftest() -> int:
    """A hand-built path with a known answer for every label."""
    import duckdb

    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    con = duckdb.connect(":memory:")
    con.execute("CREATE TABLE pm_round_snapshots (slug VARCHAR, seconds_left DOUBLE,"
                " btc_price DOUBLE, anchor_price DOUBLE)")
    # Anchor 100. Path after the 60s checkpoint, in TIME order:
    #   60s: 104 (above)   50s: 96 (BELOW  <- crossing 1)   40s: 108 (above <- crossing 2)
    #   20s: 112 (above, the high)         5s: 101 (above, terminal)
    rows = [("r1", 90.0, 102.0), ("r1", 60.0, 104.0), ("r1", 50.0, 96.0),
            ("r1", 40.0, 108.0), ("r1", 20.0, 112.0), ("r1", 5.0, 101.0)]
    for slug, left, price in rows:
        con.execute("INSERT INTO pm_round_snapshots VALUES (?,?,?,?)", [slug, left, price, 100.0])
    con.execute("CREATE TABLE checkpoints (slug VARCHAR, checkpoint_s INTEGER, horizon INTEGER,"
                " snapshot_ts DOUBLE, evidence_class VARCHAR, eligible BOOLEAN,"
                " btc_price DOUBLE, anchor_price DOUBLE, vol_60s_pct DOUBLE,"
                " settled_side VARCHAR)")
    con.execute("INSERT INTO checkpoints VALUES ('r1', 60, 5, 1783400000, 'LIVE_RESEARCH',"
                " TRUE, 104.0, 100.0, 1.0, 'DOWN')")
    # A checkpoint at the very end of the round: nothing follows it.
    con.execute("INSERT INTO checkpoints VALUES ('r1', 1, 5, 1783400059, 'LIVE_RESEARCH',"
                " TRUE, 101.0, 100.0, 1.0, 'DOWN')")
    # A second round that crosses ONCE and never comes back: the flip is durable.
    for left, price in ((60.0, 104.0), (50.0, 96.0), (5.0, 97.0)):
        con.execute("INSERT INTO pm_round_snapshots VALUES (?,?,?,?)", ["r2", left, price, 100.0])
    con.execute("INSERT INTO checkpoints VALUES ('r2', 60, 5, 1783400000, 'LIVE_RESEARCH',"
                " TRUE, 104.0, 100.0, 1.0, 'DOWN')")

    frame = build(con)
    con.close()

    row = frame[frame["checkpoint_s"] == 60].iloc[0]
    check(int(row["label_path_samples"]) == 4,
          "the forward path is STRICTLY after the checkpoint (4 samples, not 5)")
    check(abs(float(row["label_remaining_max_up_usd"]) - 8.0) < 1e-9,
          "max upward excursion is 112 - 104 = 8.0 measured FROM the checkpoint price")
    check(abs(float(row["label_remaining_max_down_usd"]) - 8.0) < 1e-9,
          "max downward excursion is 104 - 96 = 8.0")
    check(abs(float(row["label_remaining_range_usd"]) - 16.0) < 1e-9,
          "remaining range is 112 - 96 = 16.0")
    check(abs(float(row["label_terminal_distance_usd"]) - 1.0) < 1e-9,
          "terminal distance from the anchor is 101 - 100 = 1.0")
    check(int(row["label_future_crossings"]) == 2,
          "both anchor crossings are counted (down through 100, then back up)")
    check(abs(float(row["label_time_to_next_cross_s"]) - 10.0) < 1e-9,
          "the first crossing is 10s after the checkpoint, not the last one")
    check(abs(float(row["label_final_cross_after_s"]) - 20.0) < 1e-9,
          "the FINAL crossing is 20s after the checkpoint")
    check(str(row["label_final_cross_direction"]) == "UP",
          "the final crossing direction is UP - the side it crossed INTO")
    check(str(row["label_terminal_side"]) == "UP" and bool(row["label_current_side_survives"]),
          "the checkpoint side (UP) survives to the end of the path")
    check(not bool(row["label_no_further_crossing"]),
          "no_further_crossing is False when the path crosses twice")
    check(bool(row["label_move_exceeds_1p0_sigma"]) is True,
          "a 16.0 range clears 1.0 sigma on this path")

    check(bool(row["label_current_side_survives"]) is True
          and bool(row["label_checkpoint_side_wins"]) is False,
          "path survival (UP holds to path end) and settlement (contract settled DOWN) are "
          "SEPARATE labels and are allowed to disagree - conflating them hides oracle basis")
    check(bool(row["label_path_agrees_with_settlement"]) is False,
          "path/settlement disagreement is itself recorded, not silently absorbed")

    # --- bursts. Windows are measured back from the checkpoint at 60s left ------------------
    # 5s window covers seconds_left in [55, 60): the path's next sample is at 50, so the window
    # is EMPTY. 15s reaches the 96 sample; 30s adds 108; 60s adds 112 and 101.
    import pandas as pd
    check(pd.isna(row["label_max_up_usd_5s"]) and pd.isna(row["label_max_down_usd_5s"]),
          "a burst window containing NO samples is NULL - an unobserved 5s is not a calm 5s")
    check(abs(float(row["label_max_down_usd_15s"]) - 8.0) < 1e-9
          and float(row["label_max_up_usd_15s"]) == 0.0,
          "the 15s window sees only the drop to 96: down 8.0, up 0.0 (clipped, not negative)")
    check(abs(float(row["label_max_up_usd_30s"]) - 4.0) < 1e-9,
          "the 30s window reaches 108, so max up is 4.0 from the 104 checkpoint price")
    check(abs(float(row["label_max_up_usd_60s"]) - 8.0) < 1e-9,
          "the 60s window reaches the 112 high, so max up is 8.0")
    check(bool(burst_indicator(frame, "up", 5, 60).iloc[0]) is True
          and bool(burst_indicator(frame, "up", 10, 60).iloc[0]) is False,
          "burst_indicator derives any threshold from the stored excursion (>=5 yes, >=10 no)")
    check(pd.isna(burst_indicator(frame, "up", 5, 5).iloc[0]),
          "burst_indicator propagates NULL for an unobserved window rather than reading False")

    # --- flips. Crossings at 50s and 40s left, so the flip lasted 10 seconds ----------------
    check(bool(row["label_flip_reverts_5s"]) is False
          and bool(row["label_flip_reverts_15s"]) is True,
          "the flip reverted after 10s: not within 5s, yes within 15s - timed from the FIRST "
          "crossing, not from the checkpoint")
    check(bool(row["label_first_cross_is_final"]) is False,
          "the first crossing is not the final one when the path crosses back")

    single = frame[frame["slug"] == "r2"].iloc[0]
    check(bool(single["label_first_cross_is_final"]) is True
          and bool(single["label_flip_reverts_60s"]) is False,
          "a flip that never reverts is FALSE at every horizon, and is the final crossing")

    empty = frame[frame["checkpoint_s"] == 1].iloc[0]
    check(int(empty["label_path_samples"]) == 0,
          "a checkpoint with no forward path reports 0 samples")
    import pandas as pd
    check(pd.isna(empty["label_remaining_range_usd"])
          and pd.isna(empty["label_remaining_max_up_usd"])
          and pd.isna(empty["label_remaining_max_down_usd"]),
          "an empty forward path yields NULL for EVERY excursion, NOT zeros - unknown is not "
          "'no move', and greatest() would have quietly written 0.0")

    check(all(is_label(c) for c in frame.columns
              if c.startswith("label_")) and
          not any(c.startswith("label_") for c in feature_columns(frame.columns)),
          "no label_* column is ever offered as a feature")

    print(f"\nPATH LABEL SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    print("=" * 96)
    print("CANONICAL PATH LABELS - remaining move and anchor crossings")
    print("=" * 96)
    if args.selftest:
        return selftest()

    frame = build()
    if frame.empty:
        print("  no rows - build the checkpoint dataset first")
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

    print(f"  checkpoint rows        : {summary['rows']:,}")
    print(f"  with a forward path    : {summary['rows_with_forward_path']:,}")
    print(f"  EMPTY forward path     : {summary['rows_with_empty_path']:,}  (NULL, not zero)")
    print(f"  median path samples    : {summary['median_path_samples']:.0f}")
    print(f"  rows with a crossing   : {summary['rows_with_a_crossing']:,}")
    print(f"  label columns          : {len(summary['label_columns'])}")
    print(f"  offered as features    : {len(summary['feature_columns_offered'])}")
    print()
    print(f"{'checkpoint':>11}{'rows':>9}{'med range $':>13}{'p90 range $':>13}"
          f"{'any cross':>11}{'side holds':>12}")
    for row in summary["per_checkpoint"]:
        print(f"{row['checkpoint_s']:>10}s{row['rows']:>9,}"
              f"{row['median_range_usd']:>13.1f}{row['p90_range_usd']:>13.1f}"
              f"{row['any_crossing_rate']:>10.1%}{row['side_survives_rate']:>12.1%}")
    print()
    print("  BURST rate: P(move >= X USD within W seconds of the checkpoint), both directions")
    print(f"{'window':>8}" + "".join(f"{f'>=${usd}':>10}" for usd in BURST_THRESHOLDS_USD))
    for window in BURST_WINDOWS_S:
        cells = ""
        for usd in BURST_THRESHOLDS_USD:
            up = summary["burst_rates"].get(f"up_{usd}usd_{window}s")
            down = summary["burst_rates"].get(f"down_{usd}usd_{window}s")
            cells += f"{(up + down) / 2:>9.2%} " if up is not None else f"{'-':>10}"
        print(f"{window:>7}s{cells}")
    print()
    flips = summary["flip_rates"]
    print(f"  FLIP persistence over {flips['rows_with_a_crossing']:,} checkpoints that cross:")
    print(f"    first crossing is the FINAL one : {flips['first_cross_is_final']:.1%}")
    for window in FLIP_REVERT_WINDOWS_S:
        print(f"    reverts within {window:>2}s               : "
              f"{flips[f'reverts_within_{window}s']:.1%}")
    print()
    print(f"  wrote {OUTPUT.relative_to(REPO).as_posix()}")
    print(f"  wrote {manifest_path.relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
