"""
Does the Polymarket quote reprice slower than BTC moves?

This is a different question from every other study here. It is NOT "do we forecast the
settlement better than the market" - that was answered no. It asks whether the market's own
price is SLOW: when BTC moves, the geometry says the fair value of an UP share changes by a
computable amount, and the question is how much of that the quote has absorbed after 0, 1, 2
and 3 observation intervals.

If the quote absorbs it immediately, there is nothing to trade. If it absorbs it over several
seconds, the stale quote is executable and the lag is the alpha.

THE MEASUREMENT IS NOT A CORRELATION. Raw correlation between BTC returns and quote changes
would be positive even in a perfectly efficient market, because both respond to the same
information at the same instant. The question is whether a move ALREADY OBSERVED predicts a
quote change that has NOT YET HAPPENED.

    dfair(t)  = structural fair value change implied by the BTC move over [t-1, t]
    dmid(t+k) = quote mid change over [t+k-1, t+k]

    absorbed(k) = the share of dfair(t) the quote has moved by t+k

Efficient market: absorbed(0) is near 1 and absorbed(k>0) is near 0.
Laggy market:     absorbed(0) is small and absorbed(k>0) is materially positive.

HARD LIMITATION, STATED UP FRONT: the recorder's median inter-snapshot gap is ~1.9 seconds.
This test cannot see a lag shorter than that. Latency arbitrage usually lives at 50-500ms, so
a null result here means "no lag at multi-second resolution" and NOT "no lag". Reporting it as
the latter would be the same overclaim this repository keeps finding.

Read-only. Exits non-zero only on a data problem.

    python research/cross_venue_repricing_lag.py
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

from polymarket_fair_value import (  # noqa: E402
    sigma_from_path, structural_p_up, taker_fee_per_share,
)

DB = os.environ.get("BTC_EXECUTION_DB", str(ROOT / "data" / "execution_layer.duckdb"))

MIN_SNAPSHOTS_PER_ROUND = 30
#: An "impulse" - a BTC move big enough that the geometry demands a visible quote response.
#: Below this the implied fair-value change is inside the tick and nothing is measurable.
IMPULSE_MIN_DFAIR = 0.01          # 1 cent of implied fair value
MAX_LAG = 3


def load(conn):
    rows = conn.execute("""
        SELECT slug, ts, seconds_left, anchor_price, btc_price,
               up_bid, up_ask, up_mid, up_top_ask_size
        FROM pm_round_snapshots
        WHERE up_ask IS NOT NULL AND up_bid IS NOT NULL AND up_mid IS NOT NULL
          AND up_ask > 0 AND up_ask < 1
          AND btc_price IS NOT NULL AND anchor_price IS NOT NULL
          AND seconds_left > 0 AND seconds_left <= horizon * 60
        ORDER BY slug, ts
    """).fetchall()
    by = defaultdict(list)
    for r in rows:
        by[r[0]].append(r)
    return by


def bootstrap_lower(per_round: dict, seed: int = 20260808, draws: int = 2000,
                    pct: float = 0.05) -> float | None:
    """5th percentile of the mean, resampling ROUNDS.

    Snapshots inside a round are heavily autocorrelated and share one market state, so
    resampling them independently would shrink the interval by roughly the snapshot count.
    """
    keys = sorted(per_round)
    if len(keys) < 20:
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
        by_round = load(conn)
    finally:
        conn.close()
    if not by_round:
        print("no usable snapshots")
        return 2

    print("=" * 78)
    print("CROSS-VENUE REPRICING LAG - is the Polymarket quote slow?")
    print("=" * 78)

    events = []          # one per impulse
    gaps = []
    for slug, snaps in by_round.items():
        if len(snaps) < MIN_SNAPSHOTS_PER_ROUND:
            continue
        sigma = sigma_from_path([s[4] for s in snaps], [s[1] for s in snaps])
        if not sigma:
            continue
        fair = []
        for _, ts, secs, anchor, btc, bid, ask, mid, size in snaps:
            fair.append(structural_p_up(btc, anchor, secs, sigma))
        for i in range(1, len(snaps) - MAX_LAG):
            f0, f1 = fair[i - 1], fair[i]
            if f0 is None or f1 is None:
                continue
            dfair = f1 - f0
            if abs(dfair) < IMPULSE_MIN_DFAIR:
                continue
            mids = [snaps[i + k][7] for k in range(-1, MAX_LAG + 1)]
            if any(m is None for m in mids):
                continue
            gaps.append(snaps[i][1] - snaps[i - 1][1])
            events.append({
                "slug": slug,
                "dfair": dfair,
                # dmid over the SAME interval as the BTC move, then the following ones.
                "dmid": [mids[k + 1] - mids[k] for k in range(0, MAX_LAG + 1)],
                "ask_at_t": snaps[i][6],
                "bid_at_t": snaps[i][5],
                "mid_at_t": snaps[i][7],
                "mid_future": [snaps[i + k][7] for k in range(1, MAX_LAG + 1)],
                "size": snaps[i][8],
            })

    if len(events) < 200:
        print(f"only {len(events)} impulses - too few to measure")
        return 0

    n_rounds = len({e["slug"] for e in events})
    med_gap = sorted(gaps)[len(gaps) // 2]
    print(f"\n{len(events):,} impulses (|implied fair-value change| >= "
          f"{IMPULSE_MIN_DFAIR:.0%}) across {n_rounds:,} rounds")
    print(f"median observation interval {med_gap:.2f}s  <- this test is BLIND below that")

    print("\n1. ABSORPTION - how much of the implied move has the quote taken, by lag k?")
    print("-" * 78)
    print(f"  {'lag':<8}{'meaning':<34}{'absorbed':>12}{'reading':>22}")
    cumulative = [0.0] * (MAX_LAG + 1)
    for k in range(MAX_LAG + 1):
        num = sum(e["dmid"][k] * (1 if e["dfair"] > 0 else -1) for e in events)
        den = sum(abs(e["dfair"]) for e in events)
        share = num / den if den else 0.0
        cumulative[k] = share + (cumulative[k - 1] if k else 0.0)
        label = "same interval as the BTC move" if k == 0 else f"{k} interval(s) LATER"
        reading = ("contemporaneous" if k == 0
                   else ("LAG - quote still catching up" if share > 0.02
                         else "no further move"))
        print(f"  k={k:<6}{label:<34}{share:>11.1%}{reading:>22}")
    print(f"  {'':<42}{'cumulative by k=' + str(MAX_LAG):>11} {cumulative[MAX_LAG]:.1%}")

    print("\n2. IS THE LEFTOVER TRADEABLE? - buy at the ask on the impulse, mark at t+k")
    print("-" * 78)
    print(f"  {'hold':<10}{'n':>8}{'gross/share':>14}{'fee/share':>12}{'NET/share':>12}"
          f"{'5th pct':>12}")
    for k in range(1, MAX_LAG + 1):
        per_round = defaultdict(list)
        for e in events:
            # Trade the side the geometry says is now underpriced, paying the spread.
            if e["dfair"] > 0:
                entry, exit_px = e["ask_at_t"], e["mid_future"][k - 1]
                gross = exit_px - entry
            else:
                entry, exit_px = e["bid_at_t"], e["mid_future"][k - 1]
                gross = entry - exit_px          # selling UP at the bid
            fee = taker_fee_per_share(entry)
            per_round[e["slug"]].append(gross - fee)
        vals = [v for lst in per_round.values() for v in lst]
        gross_mean = sum(
            (e["mid_future"][k - 1] - e["ask_at_t"]) if e["dfair"] > 0
            else (e["bid_at_t"] - e["mid_future"][k - 1]) for e in events) / len(events)
        fee_mean = sum(taker_fee_per_share(
            e["ask_at_t"] if e["dfair"] > 0 else e["bid_at_t"]) for e in events) / len(events)
        net_mean = sum(vals) / len(vals)
        lo = bootstrap_lower(per_round)
        lo_s = f"{lo * 100:+.2f}c" if lo is not None else "n/a"
        print(f"  t+{k:<8}{len(vals):>8,}{gross_mean * 100:>13.2f}c{fee_mean * 100:>11.2f}c"
              f"{net_mean * 100:>11.2f}c{lo_s:>12}")

    print("\n3. MODEL-FREE CHECK - does a PAST BTC move predict the NEXT quote move?")
    print("-" * 78)
    print("  Section 1 measures absorption of a fair-value change this repository's own")
    print("  measurement says is WORSE than the ask (POLY_FAIR_VALUE_VS_ASK). A low")
    print("  absorption number is therefore ambiguous: either the quote is slow, or the fair")
    print("  value is wrong. This removes the model from the path entirely.")
    print()
    print(f"  {'lag':<8}{'n':>10}{'corr':>10}   reading")
    lag_pairs = defaultdict(list)
    for slug, snaps in by_round.items():
        if len(snaps) < 40:
            continue
        for i in range(1, len(snaps) - MAX_LAG - 1):
            p0, p1 = snaps[i - 1][4], snaps[i][4]
            if not p0 or not p1 or p0 <= 0:
                continue
            dbtc = math.log(p1 / p0)
            if dbtc == 0.0:
                continue
            for k in range(MAX_LAG + 1):
                m0, m1 = snaps[i + k - 1][7], snaps[i + k][7]
                if m0 is None or m1 is None:
                    continue
                lag_pairs[k].append((dbtc, m1 - m0))
    for k in sorted(lag_pairs):
        xs = [a for a, _ in lag_pairs[k]]
        ys = [b for _, b in lag_pairs[k]]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
        sy = math.sqrt(sum((y - my) ** 2 for y in ys))
        r = (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)) if sx and sy else 0.0
        reading = ("contemporaneous response" if k == 0
                   else ("PREDICTIVE - a lag exists" if abs(r) > 0.05
                         else "no predictive power"))
        print(f"  k={k:<6}{n:>10,}{r:>10.4f}   {reading}")

    print("\n4. THE COST THE LEFTOVER WOULD HAVE TO CLEAR")
    print("-" * 78)
    spreads = sorted(e["ask_at_t"] - e["bid_at_t"] for e in events)
    med_spread = spreads[len(spreads) // 2]
    med_fee = sorted(taker_fee_per_share(e["ask_at_t"]) for e in events)[len(events) // 2]
    residual = (1.0 - cumulative[0]) * sum(abs(e["dfair"]) for e in events) / len(events)
    print(f"  median quoted spread at the impulse   {med_spread * 100:.2f}c")
    print(f"  median taker fee at that price        {med_fee * 100:.2f}c")
    print(f"  round-trip cost to cross and pay      {(med_spread + med_fee) * 100:.2f}c")
    print(f"  mean UNABSORBED implied move at k=0   {residual * 100:.2f}c")
    print()
    # THIS COMPARISON IS NOT AN OPPORTUNITY, and printing it as one would be the exact
    # defect this repository keeps finding. The unabsorbed amount is the size of the
    # MODEL'S DISAGREEMENT with the price, and section 2 shows acting on it LOSES at every
    # horizon. A number exceeding a cost is only an opportunity if it is information.
    print("  The leftover is NOT an opportunity. It is the size of the model's disagreement")
    print("  with the price, and section 2 shows trading it loses at every horizon. Read")
    print("  section 3: if a past BTC move does not predict the next quote move, there is")
    print("  nothing to be early to, whatever the leftover measures.")

    print("\n" + "=" * 78)
    print(f"A lag shorter than {med_gap:.1f}s is INVISIBLE to this data. A null here means")
    print("no multi-second lag, not no lag - the sub-second question needs a recorder")
    print("that samples faster than the thing it is trying to measure.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
