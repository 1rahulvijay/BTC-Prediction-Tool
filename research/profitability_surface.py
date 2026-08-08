"""
Is there ANY state where the market's own price is mispriced?

This tests the premise underneath the whole selectivity architecture - reliability models,
OOD detectors, error predictors, contradiction engines, state specialists. Every one of them
is machinery for CONCENTRATING capital into a profitable subset. None of them creates one.
So before building any of it: does a profitable subset exist?

DELIBERATELY MODEL-FREE. It does not ask "where is our model right", because this
repository's own measurements say the model is worse than the ask (POLY_FAIR_VALUE_VS_ASK).
Using it to select states would just be selecting where its error happens to be favourable,
which is how a backtest finds alpha that does not exist.

Instead, for every cell of the state space it asks the only question that cannot be gamed:

    if you had bought this side at the recorded ask, in this state, every time -
    what did it actually pay after the real Polymarket taker fee?

A cell with positive net EV is a genuine mispricing in the market's own quote, independent of
any forecast. That is a thing worth building selectivity for. A surface with no such cell means
selectivity has nothing to find, and the reliability model would be sorting noise.

The state space follows the structure that matters for a barrier-at-expiry contract:

    X   seconds remaining          (a 12-minute round and a 12-second round are different
                                     financial products)
    Y   |distance| / expected remaining move
                                    (the scale-free version of "how far from the anchor",
                                     which is what the geometry actually depends on)

Read-only. Exits non-zero only on a data problem, never on an unfavourable finding.

    python research/profitability_surface.py
"""

from __future__ import annotations

import math
import os
import random
import sys
from collections import defaultdict
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from polymarket_fair_value import sigma_from_path, taker_fee_per_share  # noqa: E402

DB = os.environ.get("BTC_EXECUTION_DB", str(ROOT / "data" / "execution_layer.duckdb"))

MIN_SNAPSHOTS_PER_ROUND = 30
MIN_CELL_N = 200          # below this a cell is noise, and it is reported as such
MIN_CELL_ROUNDS = 15      # and it needs enough independent rounds, not just rows

TIME_BINS = [(0, 10), (10, 30), (30, 60), (60, 120), (120, 300), (300, 900)]
#: |distance| in units of the expected remaining move, sigma*sqrt(T). Scale-free: it is what
#: the barrier probability actually depends on, so a $50 move at 10s and a $200 move at 300s
#: land in the same cell when they are equally decisive.
GEOM_BINS = [(0.0, 0.25), (0.25, 0.75), (0.75, 1.5), (1.5, 3.0), (3.0, 1e9)]


def bin_of(value, bins):
    for i, (lo, hi) in enumerate(bins):
        if lo <= value < hi:
            return i
    return None


def bootstrap_lower(per_round, seed=20260808, draws=1500, pct=0.05):
    keys = sorted(per_round)
    if len(keys) < MIN_CELL_ROUNDS:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        pick = [rng.choice(keys) for _ in keys]
        vals = [v for k in pick for v in per_round[k]]
        if vals:
            means.append(sum(vals) / len(vals))
    if not means:
        return None
    means.sort()
    return means[int(pct * (len(means) - 1))]


def main() -> int:
    if not Path(DB).exists():
        print(f"no execution database at {DB}")
        return 2
    conn = duckdb.connect(DB, read_only=True)
    try:
        rows = conn.execute("""
            SELECT s.slug, s.ts, s.seconds_left, s.anchor_price, s.btc_price,
                   s.up_ask, s.down_ask, t.up_win
            FROM pm_round_snapshots s
            JOIN pm_round_settlements t USING (slug)
            WHERE s.up_ask > 0 AND s.up_ask < 1 AND s.down_ask > 0 AND s.down_ask < 1
              AND s.btc_price IS NOT NULL AND s.anchor_price IS NOT NULL
              AND s.seconds_left > 0 AND s.seconds_left <= s.horizon * 60
              AND t.up_win IS NOT NULL
            ORDER BY s.slug, s.ts
        """).fetchall()
    finally:
        conn.close()
    if not rows:
        print("no settled rounds with quotes")
        return 2

    by_round = defaultdict(list)
    for r in rows:
        by_round[r[0]].append(r)

    # cell -> side -> {slug: [net per share]}
    cells = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    total = 0
    for slug, snaps in by_round.items():
        if len(snaps) < MIN_SNAPSHOTS_PER_ROUND:
            continue
        sigma = sigma_from_path([s[4] for s in snaps], [s[1] for s in snaps])
        if not sigma:
            continue
        for _, ts, secs, anchor, btc, up_ask, down_ask, up_win in snaps:
            expected_move = sigma * math.sqrt(secs) * anchor
            if expected_move <= 0:
                continue
            geom = abs(btc - anchor) / expected_move
            ti, gi = bin_of(secs, TIME_BINS), bin_of(geom, GEOM_BINS)
            if ti is None or gi is None:
                continue
            y_up = 1.0 if up_win else 0.0
            # Buy the side at its ask, hold to settlement, pay the taker fee.
            cells[(ti, gi)]["UP"][slug].append(
                y_up - float(up_ask) - taker_fee_per_share(float(up_ask)))
            cells[(ti, gi)]["DOWN"][slug].append(
                (1.0 - y_up) - float(down_ask) - taker_fee_per_share(float(down_ask)))
            total += 1

    print("=" * 78)
    print("PROFITABILITY SURFACE - buying at the recorded ask, by state, after fees")
    print("=" * 78)
    print(f"\n{total:,} state observations across {len(by_round):,} settled rounds")
    print("Net cents per share from buying that side at the ask and holding to settlement.")
    print("A positive cell with a positive lower bound is a real mispricing in the market's")
    print("own quote - no forecast required.\n")

    geom_labels = ["<0.25", "0.25-.75", "0.75-1.5", "1.5-3", ">3"]
    time_labels = ["<10s", "10-30s", "30-60s", "1-2m", "2-5m", "5-15m"]

    winners = []
    for side in ("UP", "DOWN"):
        print(f"  {side} side   (net c/share; n too small shown as '-')")
        header = f"    {'seconds left':<14}" + "".join(f"{g:>11}" for g in geom_labels)
        print(header)
        print("    " + "-" * (14 + 11 * len(geom_labels)))
        for ti, tlabel in enumerate(time_labels):
            line = f"    {tlabel:<14}"
            for gi in range(len(GEOM_BINS)):
                per_round = cells.get((ti, gi), {}).get(side, {})
                vals = [v for lst in per_round.values() for v in lst]
                if len(vals) < MIN_CELL_N or len(per_round) < MIN_CELL_ROUNDS:
                    line += f"{'-':>11}"
                    continue
                mean = sum(vals) / len(vals) * 100
                line += f"{mean:>10.2f}c"
                if mean > 0:
                    lo = bootstrap_lower(per_round)
                    winners.append((side, tlabel, geom_labels[gi], mean, lo,
                                    len(vals), len(per_round)))
            print(line)
        print()

    print("  CELLS WITH POSITIVE MEAN - do any survive a round-clustered lower bound?")
    print("  " + "-" * 74)
    if not winners:
        print("    none. No state in the surface has positive net EV at the ask.")
    else:
        print(f"    {'side':<6}{'time':<9}{'geometry':<11}{'mean':>9}{'5th pct':>10}"
              f"{'n':>9}{'rounds':>8}")
        survivors = 0
        for side, t, g, mean, lo, n, nr in sorted(winners, key=lambda x: -x[3]):
            lo_s = f"{lo * 100:+.2f}c" if lo is not None else "n/a"
            mark = ""
            if lo is not None and lo > 0:
                mark = "   <- SURVIVES"
                survivors += 1
            print(f"    {side:<6}{t:<9}{g:<11}{mean:>8.2f}c{lo_s:>10}{n:>9,}{nr:>8}{mark}")
        print()
        print(f"    {survivors} of {len(winners)} positive cells have a positive 5th "
              f"percentile.")

    # THE CONTROL THAT STOPS THE TABLE BEING MISREAD.
    #
    # The DOWN column looks systematically better than UP, and it is not a state effect: in
    # this sample DOWN simply won more often than its ask implied. A reader scanning the
    # surface would build a DOWN-biased strategy out of the sample's own coin flips.
    print("  BASE-RATE CONTROL - what a blanket, state-blind buyer earns here")
    print("  " + "-" * 74)
    up_wins = sum(1 for snaps in by_round.values() if snaps and snaps[0][7])
    n_rounds = len(by_round)
    down_share = 1.0 - up_wins / n_rounds
    all_up_ask = all_down_ask = 0.0
    cnt = 0
    for snaps in by_round.values():
        for _s, _ts, _secs, _a, _b, ua, da, _w in snaps:
            all_up_ask += float(ua)
            all_down_ask += float(da)
            cnt += 1
    mean_up_ask, mean_down_ask = all_up_ask / cnt, all_down_ask / cnt
    print(f"    settled rounds {n_rounds:,}   UP won {up_wins / n_rounds:.1%}   "
          f"DOWN won {down_share:.1%}")
    print(f"    mean up_ask {mean_up_ask:.4f}   mean down_ask {mean_down_ask:.4f}")
    print(f"    blanket DOWN buyer, NO state selection:  "
          f"{(down_share - mean_down_ask) * 100:+.2f}c/share gross")
    print(f"    blanket UP buyer,   NO state selection:  "
          f"{(up_wins / n_rounds - mean_up_ask) * 100:+.2f}c/share gross")
    se = math.sqrt(0.25 / n_rounds)
    z = (down_share - 0.5) / se if se else 0.0
    print(f"    that imbalance is {abs(z):.1f} standard errors from an even split "
          f"({'NOISE' if abs(z) < 2 else 'possibly real'})")
    print("    -> the DOWN column above is largely THIS, not a state effect. A cell whose")
    print("       mean is below the blanket number is worse than not selecting at all.")
    print()

    print("\n" + "=" * 78)
    print("Selectivity concentrates capital into a profitable subset. It does not create")
    print("one. If no cell survives, a reliability model, an OOD detector and an error")
    print("predictor would all be sorting noise - they would find the cells where the")
    print("noise was favourable, which is what an overfit backtest looks like.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
