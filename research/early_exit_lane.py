"""
Can you make money by SELLING before settlement instead of holding?

Every study here so far scores a position at settlement. A round that trades to 0.67 and then
settles DOWN is a total loss under that accounting, and could have been a gain with an exit.
This asks whether that difference is real and, more importantly, whether it is REACHABLE by a
rule that only ever looks backwards.

TWO PARTS, AND THE ORDER MATTERS.

  1. THE CEILING. Maximum favourable excursion of the BID above the entry ASK. This is what a
     perfect oracle exit would capture and NO rule can beat it. If it does not clear the
     round-trip cost, the lane is dead and no cleverness rescues it.

  2. WHAT IS REACHABLE. A frozen grid of take-profit / stop-loss thresholds, evaluated
     CAUSALLY: walk the path forward, exit at the first snapshot where the bid crosses the
     threshold, and if it never does, settle. No peeking.

THE COST IS TWO SPREAD CROSSINGS, NOT ONE. Enter by paying the ask, exit by receiving the bid,
and pay the taker fee on BOTH legs. Marking an exit at the mid - or worse, at the ask you paid
- is the single easiest way to manufacture an early-exit strategy that does not exist.

THE ORACLE TRAP, STATED PLAINLY: "exit at the maximum" is not a strategy, it is a measurement
of the maximum. It is reported here only as a ceiling, clearly labelled, because a ceiling that
fails to clear costs is decisive and a ceiling that clears them proves nothing on its own.

Read-only. Exits non-zero only on a data problem.

    python research/early_exit_lane.py
"""

from __future__ import annotations

import os
import random
import sys
from collections import defaultdict
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from polymarket_fair_value import taker_fee_per_share  # noqa: E402

DB = os.environ.get("BTC_EXECUTION_DB", str(ROOT / "data" / "execution_layer.duckdb"))

MIN_SNAPSHOTS_PER_ROUND = 30
#: Only consider entries with enough round left that an exit is even possible.
MIN_SECONDS_LEFT_AT_ENTRY = 30
#: One entry per round, at a fixed point in the path, so the sample is rounds and not
#: thousands of overlapping windows inside the same round sharing one outcome.
ENTRY_FRACTION = 0.25

TAKE_PROFITS = [0.02, 0.03, 0.05, 0.08, 0.12]
STOP_LOSSES = [0.03, 0.05, 0.10, 1.00]     # 1.00 == no stop


def bootstrap_lower(values_by_round, seed=20260808, draws=2000, pct=0.05):
    keys = sorted(values_by_round)
    if len(keys) < 20:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        pick = [rng.choice(keys) for _ in keys]
        vals = [v for k in pick for v in values_by_round[k]]
        if vals:
            means.append(sum(vals) / len(vals))
    means.sort()
    return means[int(pct * (len(means) - 1))] if means else None


def main() -> int:
    if not Path(DB).exists():
        print(f"no execution database at {DB}")
        return 2
    conn = duckdb.connect(DB, read_only=True)
    try:
        rows = conn.execute("""
            SELECT s.slug, s.ts, s.seconds_left, s.up_bid, s.up_ask,
                   s.down_bid, s.down_ask, t.up_win
            FROM pm_round_snapshots s
            JOIN pm_round_settlements t USING (slug)
            WHERE s.up_ask > 0 AND s.up_ask < 1 AND s.up_bid > 0 AND s.up_bid < 1
              AND s.down_ask > 0 AND s.down_ask < 1 AND s.down_bid > 0 AND s.down_bid < 1
              AND s.seconds_left > 0 AND s.seconds_left <= s.horizon * 60
              AND t.up_win IS NOT NULL
            ORDER BY s.slug, s.ts
        """).fetchall()
    finally:
        conn.close()
    if not rows:
        print("no settled rounds with a two-sided quote")
        return 2

    by_round = defaultdict(list)
    for r in rows:
        by_round[r[0]].append(r)

    trades = []
    for slug, snaps in by_round.items():
        if len(snaps) < MIN_SNAPSHOTS_PER_ROUND:
            continue
        snaps = sorted(snaps, key=lambda r: -r[2])          # descending seconds_left
        idx = int(len(snaps) * ENTRY_FRACTION)
        if idx >= len(snaps) - 5:
            continue
        entry = snaps[idx]
        if entry[2] < MIN_SECONDS_LEFT_AT_ENTRY:
            continue
        path = snaps[idx + 1:]
        up_win = bool(entry[7])
        for side in ("UP", "DOWN"):
            if side == "UP":
                entry_px = float(entry[4])                   # pay the ask
                bids = [float(r[3]) for r in path]           # exit receives the bid
                settle = 1.0 if up_win else 0.0
            else:
                entry_px = float(entry[6])
                bids = [float(r[5]) for r in path]
                settle = 0.0 if up_win else 1.0
            if not bids:
                continue
            trades.append({
                "slug": slug, "side": side, "entry": entry_px, "bids": bids,
                "settle": settle, "seconds_left": float(entry[2]),
                "entry_fee": taker_fee_per_share(entry_px),
            })

    if len(trades) < 100:
        print(f"only {len(trades)} entries - too few")
        return 0

    n_rounds = len({t["slug"] for t in trades})
    print("=" * 78)
    print("EARLY EXIT LANE - selling before settlement instead of holding")
    print("=" * 78)
    print(f"\n{len(trades):,} entries across {n_rounds:,} settled rounds "
          f"(one entry per side per round, at {ENTRY_FRACTION:.0%} into the path)")
    print("Enter by PAYING THE ASK. Exit by RECEIVING THE BID. Taker fee on both legs.")

    print("\n1. THE CEILING - a perfect oracle exit at the best bid ever seen")
    print("-" * 78)
    print("   NOT a strategy. It is the maximum any rule could capture, and it is reported")
    print("   only because a ceiling below cost is decisive.")
    per_round_oracle = defaultdict(list)
    per_round_hold = defaultdict(list)
    mfe_list = []
    for t in trades:
        best_bid = max(t["bids"])
        mfe = best_bid - t["entry"]
        mfe_list.append(mfe)
        oracle_net = mfe - t["entry_fee"] - taker_fee_per_share(best_bid)
        per_round_oracle[t["slug"]].append(oracle_net)
        per_round_hold[t["slug"]].append(t["settle"] - t["entry"] - t["entry_fee"])
    mfe_list.sort()
    q = lambda p: mfe_list[int(p * (len(mfe_list) - 1))]
    print("   MFE of the bid over the entry ask:")
    print(f"     median {q(0.5) * 100:+.2f}c   p75 {q(0.75) * 100:+.2f}c   "
          f"p90 {q(0.90) * 100:+.2f}c   p99 {q(0.99) * 100:+.2f}c")
    oracle_vals = [v for lst in per_round_oracle.values() for v in lst]
    hold_vals = [v for lst in per_round_hold.values() for v in lst]
    print(f"   ORACLE exit  mean {sum(oracle_vals) / len(oracle_vals) * 100:+.2f}c/share")
    print(f"   HOLD to settle mean {sum(hold_vals) / len(hold_vals) * 100:+.2f}c/share")
    print(f"   the oracle is worth {(sum(oracle_vals) / len(oracle_vals) - sum(hold_vals) / len(hold_vals)) * 100:+.2f}c "
          f"more than holding - this is the ENTIRE budget any real rule competes for")

    print("\n1b. WHY THE BUDGET IS UNREACHABLE - you start the trade underwater")
    print("-" * 78)
    spreads = sorted(t["entry"] - t["bids"][0] for t in trades if t["bids"])
    med_spread = spreads[len(spreads) // 2]
    print(f"   median spread at entry              {med_spread * 100:.2f}c")
    print(f"   the exit bid starts                 {-med_spread * 100:.2f}c below the ask you paid")
    print(f"   so a nominal +2c target needs       {(0.02 + med_spread) * 100:.2f}c of favourable move")
    print(f"   while a nominal -3c stop needs only {(0.03 - med_spread) * 100:.2f}c against you")
    print("   A symmetric-LOOKING band is asymmetric AGAINST the position by the spread,")
    print("   before any forecasting. That is structural, not a property of the rule.")

    print("\n2. WHAT IS REACHABLE - frozen take-profit / stop grid, walked causally")
    print("-" * 78)
    print(f"   {'take profit':<13}{'stop':<8}{'n':>8}{'exit rate':>11}"
          f"{'mean net':>11}{'5th pct':>11}{'vs hold':>10}")
    hold_mean = sum(hold_vals) / len(hold_vals)
    best = None
    for tp in TAKE_PROFITS:
        for sl in STOP_LOSSES:
            per_round = defaultdict(list)
            exits = 0
            for t in trades:
                target = t["entry"] + tp
                floor_px = t["entry"] - sl
                exit_px = None
                for b in t["bids"]:                     # causal walk, first crossing wins
                    if b >= target or b <= floor_px:
                        exit_px = b
                        break
                if exit_px is None:
                    net = t["settle"] - t["entry"] - t["entry_fee"]
                else:
                    exits += 1
                    net = exit_px - t["entry"] - t["entry_fee"] - taker_fee_per_share(exit_px)
                per_round[t["slug"]].append(net)
            vals = [v for lst in per_round.values() for v in lst]
            mean = sum(vals) / len(vals)
            lo = bootstrap_lower(per_round)
            lo_s = f"{lo * 100:+.2f}c" if lo is not None else "n/a"
            sl_label = "none" if sl >= 1.0 else f"{sl:.0%}"
            print(f"   {tp:<13.0%}{sl_label:<8}{len(vals):>8,}{exits / len(trades):>10.0%}"
                  f"{mean * 100:>10.2f}c{lo_s:>11}{(mean - hold_mean) * 100:>9.2f}c")
            if lo is not None and (best is None or lo > best[0]):
                best = (lo, tp, sl, mean)

    print("\n3. VERDICT")
    print("-" * 78)
    hold_lo = bootstrap_lower(per_round_hold)
    print(f"   hold to settlement          mean {hold_mean * 100:+.2f}c   "
          f"5th pct {hold_lo * 100:+.2f}c" if hold_lo is not None else "")
    if best:
        lo, tp, sl, mean = best
        sl_label = "none" if sl >= 1.0 else f"{sl:.0%}"
        print(f"   best exit rule by lower bound  tp={tp:.0%} stop={sl_label}  "
              f"mean {mean * 100:+.2f}c   5th pct {lo * 100:+.2f}c")
        if lo > 0:
            print("   -> A CAUSAL EXIT RULE WITH A POSITIVE LOWER BOUND. Worth pursuing.")
        else:
            print("   -> no causal exit rule has a positive lower bound on this evidence.")
    print("\n" + "=" * 78)
    print("An exit rule chosen because it scored best on this grid is fitted to this grid.")
    print("A positive cell here is a CANDIDATE for a pre-registered forward test, never a")
    print("finding on its own - the grid was searched, and the search costs significance.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
