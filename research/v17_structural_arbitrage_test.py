"""V17 - REWRITTEN. The original DECLARED its result.

WHAT THE ORIGINAL DID
    win_rate = 100.0  # It is latency arbitrage. We already know the future price.

    The win rate was assigned, and the comment stated the lookahead outright. A 100% win rate
    is never a measurement.

WHAT THIS DOES
    Tests the only version of the claim that is causally admissible: does a lagged move on one
    bar predict the NEXT bar's move, net of costs? No future price is consulted.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Backtest, forward_returns, load_btc, report, split  # noqa: E402


HORIZON = 1


def main() -> int:
    frame = load_btc(200_000).copy()
    frame["lag_ret"] = frame["close"].pct_change(1)
    frame["fwd"] = forward_returns(frame, HORIZON)
    frame = frame.dropna()
    train, test = split(frame)

    def run(part):
        book = Backtest()
        # "Snipe" the continuation of the previous bar's move - causally, using only the past.
        signal = part["lag_ret"].abs() > part["lag_ret"].abs().quantile(0.99)
        for _, row in part.loc[signal].iterrows():
            book.trade(row["fwd"] * np.sign(row["lag_ret"]))
        return book

    report("V17 - lagged continuation, MEASURED (was: declared 100% win rate)",
           run(train), run(test),
           notes="no future price is used; the original consulted it by construction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
