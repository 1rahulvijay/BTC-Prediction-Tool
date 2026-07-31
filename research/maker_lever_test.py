"""LEVER 1 - can passive ENTRY plus taker EXIT improve a fixed signal?

WHY THIS CAN BE ANSWERED NOW
    Exact queue position needs sequenced L2 depth, which the archive does not have. But the
    decision-relevant question is narrower than "what is my queue position":

        Does resting the ENTRY, then crossing the EXIT at the fixed horizon,
        improve a losing strategy enough to justify full L2 research?

    That can be screened using only the 1-minute high/low already on disk, with a strict
    trade-through fill proxy.

THE FILL RULE, AND ITS LIMIT
    A passive buy resting at `limit` is filled only if the bar's LOW trades STRICTLY THROUGH it.
    Touching the price is not enough. This is conservative about entry eligibility:

      * it ignores queue position entirely by requiring the market to trade past the level, not
        merely to it - so no assumption about who is ahead of us is needed;
      * it tends to fill when the market is moving against the entry, exposing adverse selection
        rather than assuming it away.

    It is not a lower bound on real execution. A one-minute bar cannot identify order priority,
    cancellations, exact fill time or whether the horizon exit was available at its close.

    Unfilled signals become no trade, and the return they would have earned is reported
    separately as missed opportunity - because a maker strategy that never fills is not free,
    it simply earns nothing.

WHAT A RESULT MEANS
    If maker execution does NOT flip the sign under this rule, collecting true L2 will not
    rescue this exact signal cheaply. If it DOES flip the sign, that justifies forward
    sequenced-L2 testing. It is never a lower bound: bar high/low does not identify queue
    position, and a real fill process can be better or worse.

    python research/maker_lever_test.py
    python research/maker_lever_test.py --selftest
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Backtest, causal_frame, split  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = (
    ROOT
    / "backend"
    / "research"
    / "binance_maker_conversion_v1"
    / "frozen_protocol.json"
)
PROTOCOL = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))

HORIZON = 15
MAKER_FEE_BPS = float(PROTOCOL["execution"]["maker_fee_bps"])
TAKER_FEE_BPS = float(PROTOCOL["execution"]["taker_fee_bps"])
# The OHLC label exits at a bar close, not an executable bid/ask. Charge half a
# 1-bp spread for each taker leg rather than silently treating mid as a fill.
HALF_SPREAD_BPS = 0.5
TAKER_TAKER_BPS = 2.0 * TAKER_FEE_BPS + 2.0 * HALF_SPREAD_BPS
MAKER_TAKER_BPS = MAKER_FEE_BPS + TAKER_FEE_BPS + HALF_SPREAD_BPS
# How far inside the touch to rest. Deeper = fewer fills but a better price when filled.
OFFSETS_BPS = (1.0, 2.0, 5.0, 10.0)


def signal(part):
    """One causal signal, held fixed across execution styles so only EXECUTION varies."""
    return np.where(part["z_60"] < -2.0, 1, np.where(part["z_60"] > 2.0, -1, 0))


def taker_baseline(part) -> Backtest:
    book = Backtest(fee_bps=0.0, spread_bps=0.0)
    book.cost = TAKER_TAKER_BPS / 10_000.0
    direction = signal(part)
    active = direction != 0
    for gross, side in zip(part.loc[active, "fwd"], direction[active]):
        book.trade(float(gross) * float(side))
    return book


def maker_conservative(part, offset_bps: float):
    """Rest entry inside the touch; require trade-through, then cross the exit."""
    book = Backtest(fee_bps=0.0, spread_bps=0.0)
    book.cost = MAKER_TAKER_BPS / 10_000.0
    direction = signal(part)
    active = np.where(direction != 0)[0]

    close = part["close"].to_numpy()
    low = part["low"].to_numpy()
    high = part["high"].to_numpy()
    forward = part["fwd"].to_numpy()

    filled = 0
    missed = []
    offset = offset_bps / 1e4
    for i in active:
        exit_index = i + HORIZON
        if i + 1 >= len(part) or exit_index >= len(part):
            continue
        side = direction[i]
        if side > 0:
            limit = close[i] * (1.0 - offset)
            got_filled = low[i + 1] < limit          # STRICTLY through
        else:
            limit = close[i] * (1.0 + offset)
            got_filled = high[i + 1] > limit
        if not got_filled:
            missed.append(float(forward[i]) * float(side))
            continue
        # The entry is the resting limit; the exit is the horizon close before
        # the explicit taker-exit cost above. This is exact directional return
        # on entry notional, not the previous additive approximation.
        exit_price = close[exit_index]
        realised = (exit_price / limit - 1.0) * side
        book.trade(realised)
        filled += 1

    fill_rate = filled / max(len(active), 1)
    missed_mean = float(np.mean(missed)) * 1e4 if missed else 0.0
    return book, fill_rate, missed_mean, len(active)


def selftest() -> int:
    assert MAKER_TAKER_BPS == (
        MAKER_FEE_BPS + TAKER_FEE_BPS + HALF_SPREAD_BPS
    )
    assert TAKER_TAKER_BPS > MAKER_TAKER_BPS
    assert PROTOCOL["execution"]["account_fee_verified"] is False
    long_return = (110.0 / 100.0 - 1.0) * 1
    short_return = (90.0 / 100.0 - 1.0) * -1
    assert abs(long_return - 0.1) < 1e-12
    assert abs(short_return - 0.1) < 1e-12
    print("MAKER LEVER ACCOUNTING SELFTEST PASS")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        return selftest()

    frame = causal_frame(200_000, HORIZON)
    _, test = split(frame)

    print("=" * 92)
    print("LEVER 1 - MAKER EXECUTION, conservative fill rule (no L2 required)")
    print("=" * 92)

    baseline = taker_baseline(test)
    print(f"\n  TAKER baseline ({TAKER_TAKER_BPS:.1f} bps round trip)")
    print(f"    trades {baseline.trades}   OOS return {baseline.total_return_pct:+.2f}%   "
          f"win rate {baseline.win_rate_pct:.2f}%")

    print(
        "\n  PASSIVE ENTRY + TAKER EXIT "
        f"({MAKER_TAKER_BPS:.1f} bps assumed round trip)"
    )
    print("  Account-specific maker/taker fees are NOT verified; result is diagnostic.")
    print(f"{'offset':>9}{'signals':>10}{'fill rate':>12}{'fills':>8}"
          f"{'OOS return %':>15}{'missed bps':>13}")
    print("-" * 92)
    flipped = []
    for offset in OFFSETS_BPS:
        book, fill_rate, missed, signals = maker_conservative(test, offset)
        if book.trades < 30:
            print(f"{offset:>7.1f}b{signals:>10}{fill_rate:>11.1%}{book.trades:>8}"
                  f"{'(too few)':>15}{missed:>13.2f}")
            continue
        flag = "  <-- POSITIVE" if book.total_return_pct > 0 else ""
        if book.total_return_pct > 0:
            flipped.append((offset, book.total_return_pct, fill_rate, book.trades))
        print(f"{offset:>7.1f}b{signals:>10}{fill_rate:>11.1%}{book.trades:>8}"
              f"{book.total_return_pct:>15.2f}{missed:>13.2f}{flag}")

    print("\n" + "=" * 92)
    print("VERDICT")
    print("=" * 92)
    if flipped:
        print("  Passive-entry/taker-exit proxy FLIPS THE SIGN at:")
        for offset, ret, fill_rate, trades in flipped:
            print(f"    offset {offset:.1f} bps: {ret:+.2f}% on {trades} fills "
                  f"({fill_rate:.1%} fill rate)")
        print("\n  This is a BAR-LEVEL DIAGNOSTIC, not a lower bound and not promotion evidence.")
        print("  A real queue can fill less, cancellations are not identifiable here, and")
        print("  account-specific fees remain unverified.")
        print("\n  -> Test forward with BINANCE_SEQUENCED_L2_RECORDER_V1.")
    else:
        print("  Passive-entry/taker-exit does NOT flip the sign under this proxy.")
        print("  Note what this does and does not close:")
        print("    - it tests THIS signal at THIS horizon; a different signal may differ")
        print("    - the rule selects adverse trade-through states but is not a fill bound")
        print("  But the cheap version of lever 1 has been tested rather than assumed, and")
        print("  collecting L2 purely to rescue THIS signal is not justified by it.")
    print("")
    print("  Promotion status: REFUSED. This script has no observed queue priority,")
    print("  no account-specific fee proof, and no forward fill labels.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
