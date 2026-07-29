"""V19 - REWRITTEN. The original tested nothing.

WHAT THE ORIGINAL DID
    if np.random.rand() < 0.78:
        capital += capital * 0.0060
    else:
        capital -= capital * 0.0020

    The 78% win rate was an INPUT. No model, feature or prediction appeared anywhere in the
    loop, so the reported +5,209,276.4% was that assumption compounded - reproducible by
    arithmetic alone, and identical if the price series were replaced with noise. Signals were
    also chosen with df.nlargest(volatility) over the WHOLE series, which is lookahead.

WHAT THIS DOES
    Builds the "5-head confluence" from causal features only, MEASURES the win rate that
    results, and reports in-sample against out-of-sample with costs applied.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import Backtest, forward_returns, load_btc, report, split  # noqa: E402

HORIZON = 15


def add_causal_features(frame):
    """Every column uses information available AT that bar - no shift(-n) anywhere."""
    frame = frame.copy()
    close = frame["close"]
    frame["ret_5"] = close.pct_change(5)
    frame["ret_15"] = close.pct_change(15)
    frame["vol_30"] = close.pct_change().rolling(30).std()
    frame["rng"] = (frame["high"] - frame["low"]) / close
    frame["rng_z"] = ((frame["rng"] - frame["rng"].rolling(240).mean())
                      / frame["rng"].rolling(240).std())
    frame["vol_z"] = ((frame["vol_30"] - frame["vol_30"].rolling(240).mean())
                      / frame["vol_30"].rolling(240).std())
    frame["agree"] = np.sign(frame["ret_5"]) == np.sign(frame["ret_15"])
    frame["fwd"] = forward_returns(frame, HORIZON)
    return frame.dropna()


def five_head_signal(frame):
    """The confluence the original only claimed to compute, from causal inputs."""
    elevated = (frame["vol_z"] > 0.5) & (frame["vol_z"] < 3.0)
    expanding = frame["rng_z"] > 0.5
    longs = frame["agree"] & (frame["ret_5"] > 0) & elevated & expanding
    shorts = frame["agree"] & (frame["ret_5"] < 0) & elevated & expanding
    return longs, shorts


def run(frame):
    book = Backtest()
    longs, shorts = five_head_signal(frame)
    for gross in frame.loc[longs, "fwd"]:
        book.trade(gross)
    for gross in frame.loc[shorts, "fwd"]:
        book.trade(-gross)
    return book


def main() -> int:
    print("[V19] loading real BTC 1m data (no assumed outcomes)...")
    frame = add_causal_features(load_btc(200_000))
    train, test = split(frame)
    print(f"[V19] train {len(train):,} bars | test {len(test):,} bars, chronological")
    report("V19 - 5-head confluence, MEASURED (was: assumed 78% win rate)",
           run(train), run(test),
           notes="the original +5,209,276% was np.random.rand() < 0.78 compounded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
