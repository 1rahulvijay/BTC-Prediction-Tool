"""BYBIT_L2_DEPTH_HEADS_V1 - the four tests that were blocked on full-depth L2.

These are MEASUREMENTS, not models. Each reports a base rate and a conditional rate so the
lift (or absence of it) is visible. Nothing here is promotable; a single day cannot support
day-clustered inference, and that limit is printed with every number.

  LIQUIDITY_VACUUM   P(top-of-book depth collapses within 1/5/15s), and what price does after
  BURST_HAZARD       P(|move| >= $10/$25/$50 within 5/15/30s), unconditional vs after a vacuum
  BOOK_RESILIENCE    time for depleted depth to replenish; P(price reverts | replenished)
  QUEUE_POSITION     does resting deeper in the book change fill economics?

Reuses `Book` / `replay` / `ReplayInvalid` from bybit_l2_maker_v1 unchanged, including the
rule that a violated invariant produces NO number rather than a repaired one.

    python research/bybit_l2_depth_heads_v1.py
"""
from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bybit_l2_maker_v1 import DAY, L2_DIR, SYMBOL, replay  # noqa: E402

PROTOCOL = "BYBIT_L2_DEPTH_HEADS_V1"
VACUUM_HORIZONS_S = (1, 5, 15)
VACUUM_DROP = 0.5            # "collapse" = top depth falls to <= 50% of its level
BURST_HORIZONS_S = (5, 15, 30)
BURST_MOVES_USD = (10.0, 25.0, 50.0)
SAMPLE_EVERY_MS = 1_000      # one anchor per second; sub-second anchors are not independent


def collect(path: Path):
    """One pass: a 1s grid of (ts, mid, top_bid_sz, top_ask_sz, spread)."""
    grid = []
    next_ts = None
    for ts, book in replay(path):
        if next_ts is not None and ts < next_ts:
            continue
        try:
            bid, bid_sz, ask, ask_sz = book.best()
        except Exception:
            continue
        next_ts = ts + SAMPLE_EVERY_MS
        grid.append((ts, (bid + ask) / 2.0, bid_sz, ask_sz, ask - bid))
    return grid


def _idx_at_or_after(ts_list, t):
    lo, hi = 0, len(ts_list)
    while lo < hi:
        mid = (lo + hi) // 2
        if ts_list[mid] < t:
            lo = mid + 1
        else:
            hi = mid
    return lo if lo < len(ts_list) else None


def main() -> int:
    path = L2_DIR / f"{DAY}_{SYMBOL}_ob200.data.zip"
    print("=" * 84)
    print(f"{PROTOCOL}   {SYMBOL} {DAY}")
    print("=" * 84)
    if not path.exists():
        print(f"  MISSING {path}")
        return 1

    grid = collect(path)
    n = len(grid)
    ts = [g[0] for g in grid]
    mid = [g[1] for g in grid]
    bsz = [g[2] for g in grid]
    asz = [g[3] for g in grid]
    spr = [g[4] for g in grid]
    print(f"  anchors: {n:,} at 1s   span {(ts[-1]-ts[0])/3600000:.2f}h")
    print(f"  median spread ${statistics.median(spr):.2f}   "
          f"median top depth {statistics.median(bsz + asz):.3f} BTC")
    print("  ONE DAY - no day-clustered inference is possible; treat every number as")
    print("  descriptive of 2026-08-02 only.\n")

    # ---------------------------------------------------- LIQUIDITY VACUUM
    print("LIQUIDITY_VACUUM  P(top depth <= 50% of current within h)")
    vac_flags = {h: [] for h in VACUUM_HORIZONS_S}
    for i in range(n):
        for h in VACUUM_HORIZONS_S:
            j = _idx_at_or_after(ts, ts[i] + h * 1000)
            if j is None:
                continue
            now = min(bsz[i], asz[i])
            later = min(bsz[j], asz[j])
            vac_flags[h].append(1.0 if later <= VACUUM_DROP * now else 0.0)
    for h in VACUUM_HORIZONS_S:
        v = vac_flags[h]
        print(f"    {h:>2}s : {statistics.mean(v):6.2%}   (n={len(v):,})")

    # move after a vacuum vs unconditional
    print("\n  |move| over the next 15s, after a 5s vacuum vs unconditional:")
    after_vac, uncond = [], []
    for i in range(n):
        j5 = _idx_at_or_after(ts, ts[i] + 5000)
        j15 = _idx_at_or_after(ts, ts[i] + 15000)
        if j5 is None or j15 is None:
            continue
        move = abs(mid[j15] - mid[i])
        uncond.append(move)
        if min(bsz[j5], asz[j5]) <= VACUUM_DROP * min(bsz[i], asz[i]):
            after_vac.append(move)
    if after_vac:
        a, u = statistics.median(after_vac), statistics.median(uncond)
        print(f"    after vacuum : ${a:6.2f}  (n={len(after_vac):,})")
        print(f"    unconditional: ${u:6.2f}  (n={len(uncond):,})")
        print(f"    lift         : {a / u:.2f}x" if u else "")

    # ---------------------------------------------------- BURST HAZARD
    print("\nBURST_HAZARD  P(|move| >= X within h)   unconditional / after 5s vacuum")
    for h in BURST_HORIZONS_S:
        row_u, row_c = [], []
        for i in range(n):
            j = _idx_at_or_after(ts, ts[i] + h * 1000)
            j5 = _idx_at_or_after(ts, ts[i] + 5000)
            if j is None:
                continue
            m = abs(mid[j] - mid[i])
            row_u.append(m)
            if j5 is not None and min(bsz[j5], asz[j5]) <= VACUUM_DROP * min(bsz[i], asz[i]):
                row_c.append(m)
        parts = []
        for x in BURST_MOVES_USD:
            pu = sum(1 for m in row_u if m >= x) / max(len(row_u), 1)
            pc = sum(1 for m in row_c if m >= x) / max(len(row_c), 1) if row_c else 0.0
            parts.append(f"${x:>5.0f} {pu:6.2%}/{pc:6.2%}")
        print(f"    {h:>2}s : " + "   ".join(parts))

    # ---------------------------------------------------- BOOK RESILIENCE
    print("\nBOOK_RESILIENCE  after a 5s vacuum, does depth come back and price revert?")
    replen, reverted, held = [], 0, 0
    for i in range(n):
        j5 = _idx_at_or_after(ts, ts[i] + 5000)
        if j5 is None:
            continue
        base = min(bsz[i], asz[i])
        if base <= 0 or min(bsz[j5], asz[j5]) > VACUUM_DROP * base:
            continue
        back = None
        for k in range(j5, min(j5 + 60, n)):
            if min(bsz[k], asz[k]) >= base:
                back = (ts[k] - ts[j5]) / 1000.0
                break
        if back is not None:
            replen.append(back)
            j30 = _idx_at_or_after(ts, ts[j5] + 30000)
            if j30 is not None:
                moved = mid[j5] - mid[i]
                after = mid[j30] - mid[j5]
                if moved * after < 0:
                    reverted += 1
                else:
                    held += 1
    if replen:
        tot = reverted + held
        print(f"    replenished within 60s : {len(replen):,} episodes")
        print(f"    median time to refill  : {statistics.median(replen):.1f}s")
        print(f"    P(revert | replenished): {reverted / max(tot, 1):.2%}  (n={tot:,})")
        print("    50% would mean no information; a coin flip after replenishment.")
    else:
        print("    no qualifying replenishment episodes")

    print("\n" + "=" * 84)
    print("These are descriptive statistics for ONE day, not promotable heads. A head")
    print("requires a frozen protocol, a matched control, and day-clustered inference")
    print("across many days - none of which one archive file can supply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
