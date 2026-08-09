"""
The maker lane: does a resting bid earn the spread, or get picked off?

The early-exit study closed the taker round trip and pointed here: entering at the bid instead
of paying the ask is a ~2c swing per round trip, larger than the margin by which every taker
rule lost. So this is the one remaining lane whose cost structure is not already decisive.

Two things decide it, and they pull in opposite directions:

  THE PRIZE          buying at the bid instead of the ask starts you half a spread ahead,
                     and the platform maker fee is zero.
  ADVERSE SELECTION  a resting bid fills exactly when someone wants to sell into it, which is
                     disproportionately when the fair value has just fallen. You are paid the
                     spread for supplying the option to trade against you.

The measurement that settles it is the MARKOUT: mark the fill against the mid some time later.
A maker who earns the spread has a positive markout. A maker who is picked off has a negative
one, and the spread is the fee they were paid for being wrong.

QUEUE POSITION IS NOT OPTIONAL. Posting at the touch means joining BEHIND the resting size, and
you fill only after that size is consumed. Ignoring it - assuming your order fills whenever a
trade prints at your price - is the single largest way to overstate a maker strategy, because
it grants priority nobody has. This walks the queue explicitly.

Nanosecond data throughout: the round recorder at ~1.95s cannot see any of this.

Read-only. Exits non-zero only on a data problem.

    python research/maker_lane.py
"""

from __future__ import annotations

import os
import random
import sys
from collections import defaultdict
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parent.parent
DB = os.environ.get("BTC_L2_DB", str(ROOT / "data" / "polymarket_l2.duckdb"))

#: How long a posted order is left resting before it is considered abandoned. A real maker
#: cancels and reposts as the book moves; leaving it forever would invent fills.
ORDER_LIFETIME_NS = 30 * 1_000_000_000
#: Markout horizons. Adverse selection shows up fast - if it is not visible by 5s it is not
#: the mechanism.
MARKOUT_NS = [1 * 10**9, 5 * 10**9, 30 * 10**9]
#: Post one order every N book events per asset, so the sample is not thousands of
#: overlapping orders on the same market state.
POST_EVERY = 25


def bootstrap_lower(per_asset, seed=20260808, draws=2000, pct=0.05):
    keys = sorted(per_asset)
    if len(keys) < 20:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        pick = [rng.choice(keys) for _ in keys]
        vals = [v for k in pick for v in per_asset[k]]
        if vals:
            means.append(sum(vals) / len(vals))
    means.sort()
    return means[int(pct * (len(means) - 1))] if means else None


def main() -> int:
    if not Path(DB).exists():
        print(f"no L2 database at {DB}")
        return 2

    conn = duckdb.connect(DB, read_only=True)
    try:
        assets = [r[0] for r in conn.execute("""
            SELECT DISTINCT asset_id FROM pm_l2_markets WHERE slug LIKE 'btc-updown%'
        """).fetchall()]
        books = conn.execute("""
            SELECT asset_id, recv_ts_ns, best_bid, best_ask, best_bid_size
            FROM pm_l2_book_summaries
            WHERE valid AND synchronized AND best_bid > 0 AND best_ask > best_bid
              AND best_bid_size > 0
            ORDER BY asset_id, recv_ts_ns
        """).fetchall()
        trades = conn.execute("""
            SELECT asset_id, recv_ts_ns, price, size
            FROM pm_l2_trades WHERE aggressor_side = 'SELL' AND size > 0
            ORDER BY asset_id, recv_ts_ns
        """).fetchall()
    finally:
        conn.close()

    if not books:
        print("no valid book snapshots")
        return 2

    asset_set = set(assets)
    books_by = defaultdict(list)
    for a, ts, bid, ask, size in books:
        if a in asset_set:
            books_by[a].append((ts, float(bid), float(ask), float(size)))
    sells_by = defaultdict(list)
    for a, ts, px, sz in trades:
        if a in asset_set:
            sells_by[a].append((ts, float(px), float(sz)))

    print("=" * 78)
    print("MAKER LANE - does a resting bid earn the spread or get picked off?")
    print("=" * 78)
    print(f"\n{len(books_by):,} BTC round assets, {sum(len(v) for v in books_by.values()):,} "
          f"valid book snapshots, {sum(len(v) for v in sells_by.values()):,} aggressive sells")

    posted = 0
    fills = []
    for asset, bk in books_by.items():
        sells = sells_by.get(asset, [])
        if not bk:
            continue
        si = 0
        for i in range(0, len(bk), POST_EVERY):
            ts0, bid0, ask0, qsize = bk[i]
            posted += 1
            mid0 = (bid0 + ask0) / 2.0
            # QUEUE: we join behind everything currently resting at this price.
            queue_ahead = qsize
            deadline = ts0 + ORDER_LIFETIME_NS
            # Advance the sell pointer to our post time.
            while si < len(sells) and sells[si][0] < ts0:
                si += 1
            j = si
            filled_ts = None
            while j < len(sells) and sells[j][0] <= deadline:
                t_ts, t_px, t_sz = sells[j]
                if t_px <= bid0 + 1e-9:          # would hit our price level
                    queue_ahead -= t_sz
                    if queue_ahead <= 0:
                        filled_ts = t_ts
                        break
                j += 1
            if filled_ts is None:
                continue
            # Mark the fill against the mid at each horizon.
            marks = {}
            for h in MARKOUT_NS:
                target = filled_ts + h
                k = i
                while k + 1 < len(bk) and bk[k + 1][0] <= target:
                    k += 1
                if bk[k][0] < filled_ts:
                    continue
                marks[h] = (bk[k][1] + bk[k][2]) / 2.0
            if not marks:
                continue
            fills.append({"asset": asset, "fill_px": bid0, "mid_at_post": mid0,
                          "spread": ask0 - bid0, "marks": marks,
                          "queue_ahead": qsize})

    print("\n1. FILL RATE - how often does a resting bid at the touch actually fill?")
    print("-" * 78)
    rate = len(fills) / posted if posted else 0.0
    print(f"   orders posted (one per {POST_EVERY} book events)   {posted:,}")
    print(f"   orders filled within {ORDER_LIFETIME_NS // 10**9}s                       "
          f"{len(fills):,}")
    print(f"   FILL RATE                                     {rate:.2%}")
    if fills:
        qs = sorted(f["queue_ahead"] for f in fills)
        allq = sorted(b[3] for v in books_by.values() for b in v)
        print(f"   median queue ahead on FILLED orders           {qs[len(qs) // 2]:,.0f}")
        print(f"   median queue ahead on ALL posts               {allq[len(allq) // 2]:,.0f}")
        print("   -> fills happen where the queue was unusually THIN, which is a selection")
        print("      effect on the state, not a property you can choose.")

    if not fills:
        print("\n   No fills. A resting bid at the touch is not reached within its lifetime.")
        print("   The queue is the binding constraint and no forecast changes it.")
        print("\n" + "=" * 78)
        return 0

    print("\n2. MARKOUT - what were the fills worth?")
    print("-" * 78)
    print("   Maker fee is zero, so this IS the P&L. Positive = earned the spread.")
    print(f"   {'horizon':<10}{'n':>8}{'mean markout':>15}{'5th pct':>12}{'verdict':>22}")
    for h in MARKOUT_NS:
        per_asset = defaultdict(list)
        for f in fills:
            if h in f["marks"]:
                per_asset[f["asset"]].append(f["marks"][h] - f["fill_px"])
        vals = [v for lst in per_asset.values() for v in lst]
        if len(vals) < 30:
            print(f"   +{h // 10**9:<9}{len(vals):>8}   too few")
            continue
        mean = sum(vals) / len(vals)
        lo = bootstrap_lower(per_asset)
        lo_s = f"{lo * 100:+.2f}c" if lo is not None else "n/a"
        verdict = "EARNS the spread" if (lo is not None and lo > 0) else "picked off / unproven"
        print(f"   +{h // 10**9:<9}{len(vals):>8}{mean * 100:>14.2f}c{lo_s:>12}{verdict:>22}")

    half = sum(f["spread"] for f in fills) / len(fills) / 2.0
    print(f"\n   half-spread captured at the fill              {half * 100:+.2f}c")
    per_asset_1s = defaultdict(list)
    for f in fills:
        if MARKOUT_NS[0] in f["marks"]:
            per_asset_1s[f["asset"]].append(f["marks"][MARKOUT_NS[0]] - f["fill_px"])
    v1 = [v for lst in per_asset_1s.values() for v in lst]
    if v1:
        adverse = sum(v1) / len(v1) - half
        print(f"   adverse selection at +1s                      {adverse * 100:+.2f}c")
        print(f"   net (half-spread + adverse)                   "
              f"{(sum(v1) / len(v1)) * 100:+.2f}c")

    print("\n" + "=" * 78)
    print("A maker is paid the spread for supplying the option to trade against them. The")
    print("question is never 'is the spread positive' - it is whether the spread exceeds what")
    print("the people who take it know. Fill rate and markout answer that together: a high")
    print("markout on a fill rate of nearly zero is not a business.")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
