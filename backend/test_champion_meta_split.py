"""The champion-meta split must group by ROUND, purge, and count independent evidence.

WHAT WAS WRONG
    Four defects in one 136-line trainer, all measured on the live store before fixing:

      186,955 eligible snapshots
        6,727 independent rounds        <- 27.8 snapshots share ONE outcome
           21 distinct days
        False physical row order was monotonic in ts
            6 rounds straddled the old positional split
         NONE release/identity columns exist on champion_snapshots

    1. The query had no ORDER BY, but the split was `df.iloc[:int(len*0.70)]`. SQL does not
       promise row order. Order happened to be near-insertion order, which is why only 6
       rounds straddled - the correctness of the evaluation rested on an implementation
       detail nobody had asked for.
    2. It split SNAPSHOTS. ~28 snapshots share a round_id, a resolution and an
       actual_direction, so a straddling round means the model is scored on an answer it
       trained on.
    3. `MIN_ROWS = 500` counted snapshots, so it was satisfied by ~18 independent rounds.
    4. Snapshots carry no release identity, so every head generation is pooled.

    (4) cannot be fixed here - the columns do not exist - so the bundle records it as an
    unmitigated limitation instead of implying release compatibility.

    python backend/test_champion_meta_split.py
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))

import train_champion_meta as tcm  # noqa: E402

CHECKS = 0


def check(cond, text):
    global CHECKS
    assert cond, f"FAILED: {text}"
    CHECKS += 1
    print(f"  PASS  {text}")


def _frame(n_rounds=40, per_round=28, start=1_700_000_000_000, step=30_000):
    """Rounds of many snapshots each, exactly like champion_snapshots."""
    rows = []
    ts = start
    for r in range(n_rounds):
        for s in range(per_round):
            rows.append({"round_id": f"r{r:03d}", "ts": ts + s * 1_000})
        ts += step
    return pd.DataFrame(rows)


def main() -> int:
    df = _frame()

    out = tcm.round_grouped_split(df, frac=0.70, purge_ms=0)
    assert out is not None
    train_mask, test_mask, meta = out
    tr, te = set(df.loc[train_mask, "round_id"]), set(df.loc[test_mask, "round_id"])

    check(not (tr & te),
          "no round_id appears on both sides - one resolution cannot be trained on and "
          "scored on, which a positional row split allowed")
    check(meta["independent_rounds_train"] + meta["independent_rounds_test"]
          <= df["round_id"].nunique(),
          "reported independent rounds never exceed the rounds that exist")
    check(meta["independent_rounds_train"] < len(df.loc[train_mask]),
          "independent rounds are reported SEPARATELY from row counts, and are far smaller "
          "- a row count was being read as a sample size")
    check(max(df.loc[train_mask, "ts"]) < min(df.loc[test_mask, "ts"]),
          "every train row precedes every test row - the split is chronological in fact, "
          "not merely by position in whatever order the DB returned")

    # PURGE: with a gap, train must lose rounds at the boundary and test must not shrink.
    purged = tcm.round_grouped_split(df, frac=0.70, purge_ms=90_000)
    assert purged is not None
    p_train, p_test, p_meta = purged
    check(p_meta["rounds_purged"] > 0,
          "a non-zero purge actually drops rounds at the boundary rather than being recorded "
          "and ignored")
    check(set(df.loc[p_test, "round_id"]) == te,
          "and the purge shrinks TRAIN only - trimming the TEST side is how a boundary gap "
          "turns into a better-looking metric")
    gap = min(df.loc[p_test, "ts"]) - max(df.loc[p_train, "ts"])
    check(gap >= 90_000,
          f"the realised train->test gap ({gap} ms) is at least the requested purge")

    # ROW ORDER INDEPENDENCE: the whole point of ORDER BY. Shuffling the frame the DB
    # returned must not change the split.
    shuffled = df.sample(frac=1.0, random_state=7).reset_index(drop=True)
    s_out = tcm.round_grouped_split(shuffled, frac=0.70, purge_ms=0)
    assert s_out is not None
    s_train, s_test, _ = s_out
    check(set(shuffled.loc[s_test, "round_id"]) == te
          and set(shuffled.loc[s_train, "round_id"]) == tr,
          "shuffling physical row order leaves the SAME rounds on each side - the split now "
          "derives from ts, so it no longer depends on how the DB happened to return rows")

    # The query must ask for the order it depends on.
    src = (BACKEND / "train_champion_meta.py").read_text(encoding="utf-8", errors="replace")
    sql = [n.value for n in ast.walk(ast.parse(src))
           if isinstance(n, ast.Constant) and isinstance(n.value, str)
           and "champion_snapshots" in n.value]
    check(sql and all("ORDER BY" in q.upper() for q in sql),
          "the snapshot query states ORDER BY, so row order is requested rather than assumed")

    # Gates must count resolutions, not rows.
    check(tcm.MIN_ROUNDS >= 500 and tcm.MIN_DAYS >= 1,
          f"gates count INDEPENDENT evidence (>={tcm.MIN_ROUNDS} rounds, >={tcm.MIN_DAYS} "
          f"days), not the ~28x larger snapshot count that satisfied the old gate with ~18 "
          f"rounds")

    # Grouped bootstrap vs row bootstrap, on the REAL dependence structure: the label is a
    # property of the ROUND, so all 28 snapshots of a round carry the identical y. Modest
    # signal, no ceiling effect - a near-perfect classifier would compress both bounds
    # against 1.0 and let a vacuous check pass.
    rng = np.random.default_rng(0)
    n_g, per = 60, 28
    groups = np.repeat(np.arange(n_g), per)
    y_round = rng.integers(0, 2, size=n_g)
    score_round = y_round * 0.45 + rng.normal(0, 0.5, n_g)      # weak, overlapping
    y = np.repeat(y_round, per)
    prob = np.repeat(score_round, per) + rng.normal(0, 0.01, n_g * per)
    lcb_group = tcm.grouped_auc_lcb(y, prob, groups, n_boot=400)
    lcb_row = tcm.grouped_auc_lcb(y, prob, np.arange(groups.size), n_boot=400)
    check(lcb_group is not None and lcb_row is not None and lcb_row - lcb_group > 0.05,
          f"with the label constant within a round, the ROUND bootstrap LCB ({lcb_group:.3f}) "
          f"is materially below the row bootstrap ({lcb_row:.3f}, gap "
          f"{lcb_row - lcb_group:.3f}) - resampling 1,680 rows drawn from 60 outcomes claims "
          f"precision the data does not contain")

    # The unfixable one must be declared, not implied away.
    check("UNMITIGATED_NO_IDENTITY_COLUMNS" in src and "release_scoped" in src,
          "release pooling is recorded as an UNMITIGATED limitation - snapshots carry no "
          "release identity, so the head must not read as release-compatible")

    print(f"\nCHAMPION META SPLIT: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
