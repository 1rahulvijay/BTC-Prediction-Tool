"""P(Hold) must read only closed bars, and no label may cross a split boundary.

WHAT WAS WRONG (both in train_persistence_model.py)

  1. SAME-MINUTE FEATURE LEAK
     Keeper features were joined on the minute the decision sits INSIDE:

         cur_min_ms = ((window_start_ms + seconds_elapsed*1000) // 60000) * 60000

     A research-matrix row keyed 12:30 describes 12:30:00-12:30:59, and its high/low/close/
     volume are known only when that minute ends. A decision at 12:30:15 was therefore fed
     44 seconds of its own future - the keeper features already contained the move the label
     was about to measure. At 12:30:15 the newest CLOSED bar is 12:29.

  2. SPLIT BY WINDOW START ONLY
         tr = kdf[kdf.window_start_ms < tr_cut]
     A round's label is decided at window_start + horizon. A 15m round opening at 12:55
     resolves at 13:10, so against a 13:00 boundary its label came from calibration-period
     prices while the row counted as training. The LONGER horizon leaked further - backwards
     from what you want.

  P(Hold) is the head the Polymarket path leans on hardest, so inflated skill here inflates
  everything downstream of it.

  This test recomputes the shipped arithmetic on synthetic rows rather than grepping for
  words: a comment survives any mutation that matters.

  python backend/tests/test_persistence_causal_and_purged.py
"""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import ast
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

MIN = 60_000
CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, f"FAILED: {text}"
    CHECKS += 1
    print(f"  PASS  {text}")


def _shipped_feature_minute(window_start_ms, seconds_elapsed):
    """Evaluate the trainer's OWN join expression, lifted from source."""
    src = (BACKEND / "train_persistence_model.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    expr = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            tgt = node.targets[0]
            if (isinstance(tgt, ast.Subscript) and isinstance(tgt.slice, ast.Constant)
                    and tgt.slice.value == "feat_min_ms"):
                expr = ast.unparse(node.value)
    if expr is None:
        raise AssertionError("trainer no longer assigns d['feat_min_ms'] - join changed shape")
    d = pd.DataFrame({"window_start_ms": [window_start_ms],
                      "seconds_elapsed": [seconds_elapsed]})
    _decision_ms = d["window_start_ms"] + d["seconds_elapsed"] * 1000
    return int(eval(expr, {"d": d, "_decision_ms": _decision_ms, "np": np}).iloc[0])


def main() -> int:
    # 1. CAUSALITY. A decision 15s into 12:30 must read the 12:29 bar.
    start = 1_700_000_000_000 // MIN * MIN          # aligned to a minute
    decision_minute = start + 30 * MIN
    got = _shipped_feature_minute(decision_minute, 15)
    check(got == decision_minute - MIN,
          f"a decision 15s into a minute joins the PREVIOUS closed bar "
          f"({got} == {decision_minute} - 60000) - the bar it sits inside has not closed yet")

    late = _shipped_feature_minute(decision_minute, 59)
    check(late == decision_minute - MIN,
          "and still at 59s elapsed - the bar closes at :59.999, so it is never readable from "
          "inside itself")

    for elapsed in (0, 1, 30, 59, 60, 61, 119):
        fm = _shipped_feature_minute(decision_minute, elapsed)
        dec = decision_minute + elapsed * 1000
        check(fm + MIN <= dec,
              f"at {elapsed:>3}s elapsed the joined bar closes at or before the decision "
              f"instant ({fm + MIN} <= {dec})")

    # 2. PURGE. Apply the shipped partition rule to rows that straddle a boundary.
    tr_cut = start + 100 * MIN
    ca_cut = start + 120 * MIN
    rows = pd.DataFrame({
        # a 15m round opening 5m before the train boundary resolves 10m AFTER it
        "window_start_ms": [tr_cut - 5 * MIN, tr_cut - 20 * MIN,
                            ca_cut - 5 * MIN, ca_cut - 20 * MIN, ca_cut + MIN],
        "horizon": [15, 15, 15, 15, 15],
        "label": [1, 0, 1, 0, 1],
    })
    # EVALUATE THE SHIPPED EXPRESSION, not a copy of it. Recomputing the rule here tests the
    # test: a mutation that replaced the row's own horizon with a fixed 5m survived until this
    # was lifted from source.
    fn0 = next(n for n in ast.walk(ast.parse(
        (BACKEND / "train_persistence_model.py").read_text(encoding="utf-8", errors="replace")))
        if isinstance(n, ast.FunctionDef) and n.name == "_train_keeper_model")
    oe_expr = None
    for node in ast.walk(fn0):
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", "") == "_outcome_end"):
            oe_expr = ast.unparse(node.value)
    assert oe_expr, "trainer no longer computes _outcome_end"
    outcome_end = eval(oe_expr, {"kdf": rows, "np": np, "pd": pd})
    tr = rows[(rows["window_start_ms"] < tr_cut) & (outcome_end <= tr_cut)]
    ca = rows[(rows["window_start_ms"] >= tr_cut) & (rows["window_start_ms"] < ca_cut)
              & (outcome_end <= ca_cut)]
    te = rows[rows["window_start_ms"] >= ca_cut]

    check(len(tr) == 1 and int(tr["window_start_ms"].iloc[0]) == tr_cut - 20 * MIN,
          "a 15m round opening 5m before the train boundary is DROPPED from training - its "
          "label is decided 10m into calibration")
    check((tr["window_start_ms"] + tr["horizon"] * MIN <= tr_cut).all(),
          "every surviving training row's outcome COMPLETES before the boundary")
    check((ca["window_start_ms"] + ca["horizon"] * MIN <= ca_cut).all() if len(ca) else True,
          "and every calibration row's outcome completes before the test boundary")
    check(len(te) == 1,
          "the TEST side keeps every row it started with - trimming the scored set is how a "
          "purge turns into a better-looking number")

    # 3. BOTH shipped splits must apply the rule, not merely describe it.
    #
    # main() is the BASE model - the one that produces the headline TEST AUC and the T3
    # precision table. _train_keeper_model is its twin. The keeper was purged first and the
    # base was left behind for two days, which is exactly the failure mode a per-function
    # check catches and a whole-file grep does not: the string "_outcome_end" was already in
    # the file, so any search for it passed while the base split still leaked.
    src = (BACKEND / "train_persistence_model.py").read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(src)
    for fname, label, frame in (("_train_keeper_model", "keeper twin", "kdf"),
                                ("main", "BASE model", "df")):
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == fname)
        body = ast.unparse(fn)
        check(body.count("_outcome_end <= tr_cut") == 1
              and body.count("_outcome_end <= ca_cut") == 1,
              f"the {label} ({fname}) applies the purge to BOTH the train and calibration "
              f"partitions")

        # EVALUATE the shipped expression. Asserting `"horizon" in body` is not enough: main()
        # mentions horizon elsewhere for per-horizon reporting, so replacing the outcome-end
        # formula with a fixed 5 minutes passed that check. Mutation testing found it.
        oe_expr = None
        for node in ast.walk(fn):
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and getattr(node.targets[0], "id", "") == "_outcome_end"):
                oe_expr = ast.unparse(node.value)
        assert oe_expr, f"{fname} no longer computes _outcome_end"
        probe = pd.DataFrame({"window_start_ms": [0, 0], "horizon": [5, 15]})
        got = eval(oe_expr, {frame: probe, "np": np, "pd": pd})
        check(list(got) == [5 * MIN, 15 * MIN],
              f"and the {label}'s outcome end is driven by each row's OWN horizon "
              f"({list(got)} == [{5*MIN}, {15*MIN}]) - a fixed width silently under-purges "
              f"every 15m round")

    # 4. BEHAVIOURAL, on the real boundary geometry that made this leak invisible.
    #
    # Windows are 5 minutes apart; a cut is safe only when it lands on a 15-minute mark, which
    # one window in three does. At the shipped sf=0.98 on the live dataset tr_cut WAS aligned
    # (0 straddling rows) while ca_cut was not (59 rows). Checking only tr_cut, as I first did,
    # reports a clean bill on a split that is leaking at the other end.
    # A 5m round opens on every 5-minute mark; a 15m round ONLY on 15-minute marks. Modelling
    # 15m rounds at every 5-minute mark instead makes every cut straddle and hides the real
    # geometry - my first version of this fixture did exactly that and asserted the opposite
    # of what the live data shows.
    grid = np.arange(0, 45) * 5 * MIN + start
    recs = [(t, 5) for t in grid] + [(t, 15) for t in grid if (t - start) % (15 * MIN) == 0]
    rows = pd.DataFrame(recs, columns=["window_start_ms", "horizon"])
    oe = rows["window_start_ms"] + rows["horizon"].astype("int64") * MIN
    unaligned = start + 20 * MIN                       # a 5m mark that is NOT a 15m mark
    aligned = start + 30 * MIN                         # a 15m mark
    straddle_unaligned = ((rows["window_start_ms"] < unaligned) & (oe > unaligned)).sum()
    straddle_aligned = ((rows["window_start_ms"] < aligned) & (oe > aligned)).sum()
    check(straddle_unaligned > 0,
          f"at a cut that is NOT 15m-aligned, {straddle_unaligned} row(s) straddle it - this is "
          f"the common case, since only one window in three is aligned")
    check(straddle_aligned == 0,
          "while a 15m-aligned cut has none - which is why a single lucky boundary made the "
          "unpurged split look correct")

    print(f"\nPERSISTENCE CAUSAL AND PURGED: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
