"""Apply the action-value engine to every causal checkpoint and report what each action paid.

THE QUESTION THIS ANSWERS FIRST
    Before building any head that PREDICTS the best action, establish the ceiling: what would a
    perfectly-timed exit have earned? ORACLE_BEST_EXIT sells at the best bid the round ever
    printed. It needs hindsight and nobody can trade it - which is exactly why it is the right
    thing to measure first.

        if the perfect exit is still negative after costs,
        no exit model can rescue the lane and none should be built

    That is a cheap answer to a question that would otherwise cost weeks of modelling.

ONE IMPLEMENTATION, NOT TWO
    SQL summarises each round's future quote path down to the handful of points the engine
    needs; the ENGINE then values every row. The arithmetic is never reimplemented in SQL. Two
    implementations that must agree are two implementations that will eventually disagree, and
    the one in the report is always the one nobody re-reads.

    python backend/research_data/action_value_builder.py --selftest
    python backend/research_data/action_value_builder.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "backend" / "polymarket_policy"))

from action_value import (  # noqa: E402
    EXIT_HORIZONS_S, Action, select, value_actions,
)
from checkpoint_builder import SOURCE_DB  # noqa: E402

DATA_DIR = Path(os.environ.get("BTC_DATA_DIR") or REPO / "data")
CHECKPOINTS = DATA_DIR / "research" / "causal_checkpoints_v1.parquet"
LABELS = DATA_DIR / "research" / "causal_checkpoint_labels_v1.parquet"
OUTPUT = DATA_DIR / "research" / "action_values_v1.parquet"


def load_rows():
    """Per checkpoint: the quote at decision time, plus the future path summarised."""
    import duckdb

    horizons = ",\n                   ".join(
        f"arg_max(f.bid, f.offset_s) FILTER (WHERE f.offset_s <= {h}) AS bid_{h}s"
        for h in EXIT_HORIZONS_S)
    con = duckdb.connect(str(SOURCE_DB), read_only=True)
    try:
        return con.execute(f"""
            WITH cp AS (
                SELECT k.slug, k.checkpoint_s, k.horizon, k.snapshot_ts, k.evidence_class,
                       k.current_side,
                       CASE WHEN k.current_side = 1 THEN k.up_ask ELSE k.down_ask END AS ask,
                       CASE WHEN k.current_side = 1 THEN k.up_bid ELSE k.down_bid END AS bid,
                       CASE WHEN k.current_side = 1 THEN k.down_ask ELSE k.up_ask END
                           AS opposite_ask,
                       CASE WHEN k.current_side = 1 THEN k.up_top_ask_size
                            ELSE k.down_top_ask_size END AS ask_size,
                       CASE WHEN k.current_side = 1 THEN k.down_top_ask_size
                            ELSE k.up_top_ask_size END AS opposite_ask_size,
                       l.label_checkpoint_side_wins AS won
                FROM read_parquet('{CHECKPOINTS.as_posix()}') k
                JOIN read_parquet('{LABELS.as_posix()}') l
                  ON l.slug = k.slug AND l.checkpoint_s = k.checkpoint_s
                WHERE k.eligible AND k.current_side IS NOT NULL
                  AND l.label_checkpoint_side_wins IS NOT NULL
            ),
            future AS (
                -- STRICTLY after the checkpoint, and the bid of the side actually bought.
                SELECT c.slug, c.checkpoint_s,
                       c.checkpoint_s - s.seconds_left AS offset_s,
                       CASE WHEN c.current_side = 1 THEN s.up_bid ELSE s.down_bid END AS bid
                FROM cp c
                JOIN pm_round_snapshots s
                  ON s.slug = c.slug AND s.seconds_left < c.checkpoint_s
            ),
            summary AS (
                SELECT f.slug, f.checkpoint_s,
                   {horizons},
                   max(f.bid)                       AS best_bid,
                   arg_max(f.offset_s, f.bid)       AS best_bid_offset_s,
                   count(*)                         AS future_points
                FROM future f GROUP BY f.slug, f.checkpoint_s
            )
            SELECT cp.*, s.* EXCLUDE (slug, checkpoint_s)
            FROM cp LEFT JOIN summary s
              ON s.slug = cp.slug AND s.checkpoint_s = cp.checkpoint_s
            ORDER BY cp.snapshot_ts, cp.checkpoint_s
        """).df()
    finally:
        con.close()


def future_points(row):
    """The summarised path, in the (offset_s, bid) form the engine consumes."""
    points = []
    for horizon in EXIT_HORIZONS_S:
        bid = row.get(f"bid_{horizon}s")
        if bid is not None and not (isinstance(bid, float) and np.isnan(bid)):
            points.append((float(horizon), float(bid)))
    best, offset = row.get("best_bid"), row.get("best_bid_offset_s")
    if best is not None and not (isinstance(best, float) and np.isnan(best)):
        points.append((float(offset), float(best)))
    return points


def evaluate(frame) -> dict:
    """Run the engine over every row and accumulate per-action outcomes."""
    totals = defaultdict(list)
    chosen = defaultdict(int)
    for row in frame.to_dict("records"):
        won = row.get("won")
        values = value_actions(
            ask=row["ask"], bid=row["bid"], opposite_ask=row["opposite_ask"],
            won=None if won is None else bool(won),
            future_bids=future_points(row),
            top_ask_size=row.get("ask_size"),
            opposite_ask_size=row.get("opposite_ask_size"))
        for value in values:
            if value.net_per_share is None:
                continue
            key = (f"{value.action.value}_{value.horizon_s}s"
                   if value.horizon_s is not None else value.action.value)
            totals[key].append(value.net_per_share)
        # select() maximises over REALISED values, so this is an oracle policy restricted to
        # TRADEABLE arms - the second ceiling, and the one that actually bounds what an
        # action-value head could win. Naming it anything shorter invites it being read as a
        # backtest of a strategy somebody could run.
        best = select(values)
        chosen[best.action.value] += 1
        if best.net_per_share is not None:
            totals["ORACLE_PICK_AMONG_TRADEABLE"].append(best.net_per_share)
    return {"totals": totals, "chosen": dict(chosen)}


def summarise(result, frame) -> dict:
    arms = []
    for name, values in sorted(result["totals"].items()):
        array = np.asarray(values, dtype=float)
        arms.append({
            "action": name,
            "n": int(len(array)),
            "mean_net_per_share": float(array.mean()),
            "median": float(np.median(array)),
            "p10": float(np.quantile(array, 0.10)),
            "p90": float(np.quantile(array, 0.90)),
            "share_positive": float((array > 0).mean()),
        })
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "checkpoints": int(len(frame)),
        "exit_horizons_s": list(EXIT_HORIZONS_S),
        "arms": arms,
        "selected_action_counts": result["chosen"],
    }


def selftest() -> int:
    """The summariser must reproduce the engine on a path with a known answer."""
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    row = {"ask": 0.60, "bid": 0.58, "opposite_ask": 0.44, "won": True,
           "bid_15s": 0.62, "bid_30s": 0.75, "bid_60s": 0.50,
           "best_bid": 0.75, "best_bid_offset_s": 30.0,
           "ask_size": 50.0, "opposite_ask_size": 50.0}
    points = future_points(row)
    check(sorted(points) == [(15.0, 0.62), (30.0, 0.75), (30.0, 0.75), (60.0, 0.50)],
          "the summarised path carries each horizon bid plus the best bid and its offset")

    values = value_actions(ask=0.60, bid=0.58, opposite_ask=0.44, won=True,
                           future_bids=points, top_ask_size=50.0, opposite_ask_size=50.0)
    ceiling = next(v for v in values if v.action is Action.ORACLE_BEST_EXIT)
    horizon60 = next(v for v in values
                     if v.action is Action.EXIT_AT_HORIZON and v.horizon_s == 60)
    check(ceiling.net_per_share > horizon60.net_per_share,
          "the hindsight ceiling beats the 60s exit that sold after the spike faded")

    import pandas as pd
    result = evaluate(pd.DataFrame([row]))
    check("EXIT_AT_HORIZON_30s" in result["totals"]
          and "ORACLE_BEST_EXIT" in result["totals"],
          "per-horizon exits and the ceiling are accumulated under distinct keys")
    check(result["chosen"].get("ORACLE_BEST_EXIT") is None,
          "the hindsight arm is never counted as a SELECTED action")

    blind = dict(row, bid_15s=None, bid_30s=None, bid_60s=None,
                 best_bid=None, best_bid_offset_s=None)
    blind_result = evaluate(pd.DataFrame([blind]))
    check("EXIT_AT_HORIZON_15s" not in blind_result["totals"],
          "a row with no future quotes contributes NOTHING to the exit arms, not a zero")
    check("HOLD_TO_SETTLEMENT" in blind_result["totals"],
          "the hold arm still values, because settlement does not need the quote path")

    print(f"\nACTION VALUE BUILDER SELFTEST: PASS ({checks} checks)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    print("=" * 100)
    print("POLYMARKET ACTION VALUES - what each action WOULD have paid, from the real ladder")
    print("=" * 100)
    if args.selftest:
        return selftest()
    for path in (CHECKPOINTS, LABELS, SOURCE_DB):
        if not path.is_file():
            print(f"  BLOCKED: {path.name} is missing.")
            return 0

    frame = load_rows()
    if frame.empty:
        print("  BLOCKED: no eligible settled checkpoints.")
        return 0
    result = evaluate(frame)
    summary = summarise(result, frame)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.with_suffix(".manifest.json").write_text(
        json.dumps(summary, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    print(f"  checkpoints valued : {summary['checkpoints']:,}")
    print()
    print(f"{'action':<28}{'n':>9}{'mean':>10}{'median':>10}{'p10':>10}{'p90':>10}{'win%':>8}")
    for arm in summary["arms"]:
        print(f"{arm['action']:<28}{arm['n']:>9,}{arm['mean_net_per_share']:>10.4f}"
              f"{arm['median']:>10.4f}{arm['p10']:>10.4f}{arm['p90']:>10.4f}"
              f"{arm['share_positive']:>8.1%}")
    print()
    print("  which action turned out best, choosing among TRADEABLE arms with hindsight:")
    for name, count in sorted(summary["selected_action_counts"].items(),
                              key=lambda item: -item[1]):
        print(f"    {name:<26}{count:>9,}")
    print()

    def arm(name):
        return next((a for a in summary["arms"] if a["action"] == name), None)

    ceiling = arm("ORACLE_BEST_EXIT")
    pick = arm("ORACLE_PICK_AMONG_TRADEABLE")
    realisable = [a for a in summary["arms"]
                  if a["action"] not in ("ORACLE_BEST_EXIT", "ORACLE_PICK_AMONG_TRADEABLE",
                                         "WAIT")]
    best_fixed = max(realisable, key=lambda a: a["mean_net_per_share"]) if realisable else None

    print("  TWO CEILINGS AND A FLOOR - all per share, none of them a strategy:")
    if ceiling:
        print(f"    perfect exit timing (untradeable) : {ceiling['mean_net_per_share']:+.4f}")
    if pick:
        print(f"    perfect choice among tradeable    : {pick['mean_net_per_share']:+.4f}")
    if best_fixed:
        print(f"    best FIXED rule, no foresight     : "
              f"{best_fixed['mean_net_per_share']:+.4f}  ({best_fixed['action']})")
    print("    standing aside (WAIT)             : +0.0000")
    if best_fixed and pick:
        print()
        print(f"    headroom an action head could win : "
              f"{pick['mean_net_per_share'] - best_fixed['mean_net_per_share']:+.4f}")
        if best_fixed["mean_net_per_share"] < 0:
            print("    Every fixed rule LOSES. WAIT beats all of them, so on this sample the")
            print("    only non-negative policy is to not trade. A head is worth building only")
            print("    if it can capture enough of the headroom above to cross zero.")
    print(f"\n  wrote {OUTPUT.with_suffix('.manifest.json').relative_to(REPO).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
