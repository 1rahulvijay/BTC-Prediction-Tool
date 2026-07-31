"""BREAKOUT_BRACKET_V1 - can the magnitude signal be monetised without knowing direction?

THE ONLY POSITIVE RESULT SO FAR, AND ITS PROBLEM
    path_information_test found that `rv_term_inversion` (rv_15m / rv_60m > 1.5) predicts LARGE
    MOVES at every threshold, surviving Bonferroni over 56 comparisons, with the lift growing
    with size (1.54x at 40 bps). Direction remained dead.

    Magnitude without direction needs an instrument that pays on magnitude. Polymarket binaries
    do not - they settle on direction, and buying YES+NO costs ~$1 for a $1 payoff. The natural
    expression on Binance is a BREAKOUT BRACKET: resting stop-entries on both sides, whichever
    triggers rides the move. That is synthetic long gamma, and it is what this tests.

THE STRUCTURAL COST THIS MUST OVERCOME
    A bracket enters only AFTER price has already travelled `entry_bps`. You buy the move late,
    so the tradeable part is what REMAINS. A 40 bps move entered at 20 bps leaves 20 bps to
    cover a 9 bps round trip. The arithmetic is tight, which is exactly why it needs measuring
    rather than assuming.

THREE PESSIMISTIC CONVENTIONS, STATED UP FRONT
    1. WHIPSAW. If both stops are touched inside the same one-minute bar, a bar cannot say which
       came first. This charges a full whipsaw: entered one side, stopped out, entered the other.
       That is the worst realistic case, and brackets fail precisely in fast two-sided tape.
    2. Entry and exit are both TAKER. A stop order crosses the book by construction.
    3. Ties on the exit go against the position.

THE BAR IT MUST CLEAR
    Test 4 of the path study produced a +5.8 "edge" that appeared for a ZERO-INFORMATION
    baseline too - it was path structure, not signal. So a positive number here means nothing
    until it beats a control matched on BOTH trigger count AND holding time, and clears a
    day-block lower confidence bound. Both are enforced below.

    python research/breakout_bracket_test.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import load_btc, split  # noqa: E402

RNG = np.random.default_rng(20260730)
COST_BPS = 9.0                 # 2 x 4 bps taker + 1 bp spread; stop orders cross by construction
HORIZON = 15
DRAWS = 1000


def build(frame):
    frame = frame.copy()
    frame["rv_slope"] = frame["rv_15m"] / frame["rv_60m"].replace(0, np.nan)
    frame["trigger"] = (frame["rv_slope"] > 1.5).astype(int)
    return frame.dropna(subset=["rv_slope"]).reset_index(drop=True)


def run_bracket(frame, entries, entry_bps: float, trail_bps: float):
    """Resting stop-entries both sides; whichever triggers rides the move to a trailing stop.

    Returns net bps per trade after costs, plus diagnostics."""
    high = frame["high"].to_numpy()
    low = frame["low"].to_numpy()
    close = frame["close"].to_numpy()
    n = len(frame)

    results, whipsaws, no_fills = [], 0, 0
    up = entry_bps / 1e4
    trail = trail_bps / 1e4

    for i in entries:
        if i + HORIZON >= n:
            continue
        anchor = close[i]
        buy_stop = anchor * (1 + up)
        sell_stop = anchor * (1 - up)

        side, entry_price, start = 0, 0.0, 0
        for t in range(i + 1, i + 1 + HORIZON):
            touched_up = high[t] >= buy_stop
            touched_dn = low[t] <= sell_stop
            if touched_up and touched_dn:
                # PESSIMISTIC: both in one bar is a whipsaw - filled, stopped, refilled.
                whipsaws += 1
                results.append(-(2.0 * entry_bps + 2.0 * COST_BPS))
                side = -99
                break
            if touched_up:
                side, entry_price, start = 1, buy_stop, t
                break
            if touched_dn:
                side, entry_price, start = -1, sell_stop, t
                break
        if side == -99:
            continue
        if side == 0:
            no_fills += 1
            continue

        # Trail from the best price reached since entry. Exit at the trailing stop, or at the
        # window close if it is never hit.
        best = entry_price
        exit_price = close[i + HORIZON]
        for t in range(start, i + 1 + HORIZON):
            if side > 0:
                best = max(best, high[t])
                if low[t] <= best * (1 - trail):
                    exit_price = best * (1 - trail)
                    break
            else:
                best = min(best, low[t])
                if high[t] >= best * (1 + trail):
                    exit_price = best * (1 + trail)
                    break
        gross = (exit_price / entry_price - 1.0) * side * 1e4
        results.append(gross - COST_BPS)

    return (np.asarray(results, dtype=float), whipsaws, no_fills,
            len(entries))


def day_block_lcb(net_bps, timestamps, draws: int = DRAWS):
    days = (timestamps // 86_400_000).astype(int)
    unique = np.unique(days)
    if len(unique) < 5 or len(net_bps) == 0:
        return float("nan"), 0
    by_day = {d: net_bps[days == d] for d in unique}
    means = []
    for _ in range(draws):
        picked = RNG.integers(0, len(unique), len(unique))
        sample = np.concatenate([by_day[unique[i]] for i in picked])
        means.append(sample.mean())
    means = np.sort(np.asarray(means))
    return float(means[int(0.05 * len(means))]), len(unique)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=200_000)
    args = parser.parse_args()

    frame = build(load_btc(args.rows))
    _, test = split(frame)
    test = test.reset_index(drop=True)

    triggered = np.where(test["trigger"].to_numpy() == 1)[0]
    stamps_all = test["ts_ms"].to_numpy()

    print("=" * 100)
    print("BREAKOUT_BRACKET_V1 - monetising magnitude without direction")
    print("=" * 100)
    print(f"  out-of-sample bars {len(test):,} | triggers {len(triggered):,} "
          f"| cost {COST_BPS:.1f} bps round trip | horizon {HORIZON}m")
    print("  entry and exit are BOTH taker; both-stops-in-one-bar is charged as a whipsaw\n")

    print(f"{'entry':>7}{'trail':>7}{'fills':>8}{'no-fill%':>10}{'whip%':>8}"
          f"{'net bps':>10}{'ctrl bps':>10}{'lift':>8}{'day LCB':>10}  verdict")
    print("-" * 100)

    survivors = []
    grid = [(e, t) for e in (10.0, 20.0, 30.0) for t in (10.0, 20.0, 40.0)]
    for entry_bps, trail_bps in grid:
        net, whip, nofill, attempted = run_bracket(test, triggered, entry_bps, trail_bps)
        if len(net) < 30:
            print(f"{entry_bps:>6.0f}b{trail_bps:>6.0f}b{len(net):>8}"
                  f"{'':>10}{'':>8}{'(too few)':>10}")
            continue

        # CONTROL: same number of entries, random bars, IDENTICAL bracket and horizon.
        # This is the comparison that exposed test 4's +5.8 as path structure rather than signal.
        control_means = []
        for _ in range(50):
            idx = RNG.integers(0, len(test) - HORIZON - 1, len(triggered))
            c_net, _, _, _ = run_bracket(test, idx, entry_bps, trail_bps)
            if len(c_net):
                control_means.append(c_net.mean())
        control = float(np.mean(control_means)) if control_means else float("nan")

        stamps = stamps_all[[i for i in triggered
                             if i + HORIZON < len(test)]][:len(net)]
        lcb, days = day_block_lcb(net, stamps)

        lift = net.mean() - control
        passes = net.mean() > 0 and lift > 0 and lcb > 0
        verdict = "CANDIDATE" if passes else ("no lift" if lift <= 0 else "LCB<=0")
        if passes:
            survivors.append((entry_bps, trail_bps, net.mean(), lift, lcb))
        print(f"{entry_bps:>6.0f}b{trail_bps:>6.0f}b{len(net):>8}"
              f"{nofill / max(attempted,1) * 100:>9.1f}%{whip / max(attempted,1) * 100:>7.1f}%"
              f"{net.mean():>10.2f}{control:>10.2f}{lift:>+8.2f}{lcb:>10.2f}  {verdict}")

    print("\n" + "=" * 100)
    print("VERDICT")
    print("=" * 100)
    if survivors:
        print(f"  {len(survivors)} configuration(s) pass all three gates:")
        for e, t, mean, lift, lcb in survivors:
            print(f"    entry {e:.0f} bps / trail {t:.0f} bps: {mean:+.2f} bps net, "
                  f"lift {lift:+.2f} over control, day LCB {lcb:+.2f}")
        print("\n  9 configurations were searched, so apply Bonferroni: a single survivor at")
        print("  p just under 0.05 is not a discovery. Required next: forward shadow evidence")
        print("  on data that took no part in this search.")
    else:
        print("  NO configuration passes. The magnitude signal is real but this instrument")
        print("  does not monetise it.")
        print()
        print("  The reason is structural, not a tuning failure: a bracket enters only AFTER")
        print("  price has moved `entry` bps, so it pays the cost of being late on EVERY trade")
        print("  while collecting only the remainder. Whipsaws charge double. Widening the")
        print("  entry to cut whipsaws also cuts the remaining move.")
        print()
        print("  Instruments that pay on magnitude directly - a Deribit straddle - do not have")
        print("  this problem, because they do not need to pick a side at all. That needs the")
        print("  options chain, which is not collected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
