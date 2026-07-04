"""
probe_fade_entry_exit.py - backtest the FADE entry/exit LEVEL model (buy points + take-profit).
================================================================================================
DEPRECATED DIAGNOSTIC: this script does not establish a tradable edge. One-minute OHLC
cannot order target/stop events inside the touch candle; ambiguous touch candles are excluded.

It examines a concrete entry/exit rule:

  * BUY DOWN from anchor+$L   (fade a spike UP)  -> TP at the anchor, STOP at anchor+2*$L
  * BUY UP   from anchor-$L   (fade a spike DOWN) -> TP at the anchor, STOP at anchor-2*$L
  * BUY BOTH WAYS = fade both extremes in a chop/round-trip window.

This backtests the DRIVER (does price revert to the anchor before extending?) on the 1-minute
research matrix. A random walk from +$L with barriers {anchor, anchor+2L} reverts 50% of the
time (gambler's ruin) -> EV 0 before costs. The EDGE is any regime where the revert rate > 50%
(chop / early touch). Reported by style + touch timing for research only.

NOTE: this measures the BTC-price reversion, not the exact Polymarket share P&L (no share-price
history yet). It tells you whether the LEVELS are sound; the recorder proves the after-cost edge.
Leak-free: entry/exit strictly inside the future window. ASCII output.

Usage:
  python backend/probe_fade_entry_exit.py
  python backend/probe_fade_entry_exit.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_ta_matrix as TA  # noqa: E402

warnings.filterwarnings("ignore")
MATRIX = os.path.join(TA.ROOT, "data", "research_matrix_1m.parquet")
FEATURES = ["rv_15m", "rv_30m", "rv_60m", "compression_ratio", "shock_magnitude"]


def _first_passage_fade(hi, lo, anchor, L, side):
    """After a touch of anchor +/-L, does price hit the TP (anchor) before the STOP (anchor +/- 2L)?
    side='down' = we BUY DOWN after an up-touch (TP below at anchor, stop above at anchor+2L).
    Returns (entered, win, exit_minute) scanning minute-by-minute from the touch."""
    n = len(hi)
    tp = anchor
    stop = anchor + 2 * L if side == "down" else anchor - 2 * L
    lvl = anchor + L if side == "down" else anchor - L
    touched = -1
    for k in range(n):
        if (side == "down" and hi[k] >= lvl) or (side == "up" and lo[k] <= lvl):
            touched = k
            break
    if touched < 0:
        return (0, 0, -1, touched)
    for k in range(touched + 1, n):
        if side == "down":
            if lo[k] <= tp:                 # reverted to anchor -> fade WIN
                return (1, 1, k, touched)
            if hi[k] >= stop:               # extended up -> fade LOSS
                return (1, 0, k, touched)
        else:
            if hi[k] >= tp:
                return (1, 1, k, touched)
            if lo[k] <= stop:
                return (1, 0, k, touched)
    # Neither barrier hit by expiry: no realized take-profit, therefore a loss.
    return (1, 0, n - 1, touched)


def run():
    df = pd.read_parquet(MATRIX)
    c = df["close"].values
    for w in (5, 15):
        L = 50.0
        H = np.column_stack([df["high"].shift(-k).values for k in range(1, w + 1)])
        Lo = np.column_stack([df["low"].shift(-k).values for k in range(1, w + 1)])
        up = (np.nanmax(H, axis=1) - c) >= L
        dn = (c - np.nanmin(Lo, axis=1)) >= L
        rt = up & dn                                  # round-trip window (chop)
        # style proxy from keepers is overkill here; use round-trip realized as the chop label
        rows_d = []; rows_u = []
        ok = ~np.isnan(H).any(axis=1) & ~np.isnan(Lo).any(axis=1)
        idx = np.where(ok)[0]
        for i in idx:
            e, win, xm, tm = _first_passage_fade(H[i], Lo[i], c[i], L, "down")
            if e and not (Lo[i, tm] <= c[i] or H[i, tm] >= c[i] + 2 * L):
                rows_d.append((win, tm, rt[i]))
            e, win, xm, tm = _first_passage_fade(H[i], Lo[i], c[i], L, "up")
            if e and not (H[i, tm] >= c[i] or Lo[i, tm] <= c[i] - 2 * L):
                rows_u.append((win, tm, rt[i]))
        rd = np.array(rows_d); ru = np.array(rows_u)
        print("\n" + "=" * 88)
        print(f"BTC {w}m  -  FADE entry/exit backtest (buy at anchor+/-${L:.0f}, TP=anchor, stop=2x)  n_up={len(rd)} n_dn={len(ru)}")
        half = w / 2.0
        for name, R in (("BUY-DOWN (fade up-spike)", rd), ("BUY-UP (fade down-spike)", ru)):
            if not len(R):
                continue
            wr = R[:, 0].mean()
            # R[:,1] is the TOUCH MINUTE (tm): small tm = touched EARLY in the window. (Was inverted here,
            # which is how the false "early touch reverts ~2x" claim arose -- it was measuring LATE touches,
            # whose settle-generous win rate is a NO-TIME-TO-STOP artifact. See probe_roundtrip_and_timing.py
            # for the strict grade: strictly, EARLY touches reach anchor MORE, late touches ~never do.)
            early = R[R[:, 1] <= half]; late = R[R[:, 1] > half]
            chop = R[R[:, 2] == 1]; trend = R[R[:, 2] == 0]
            print(f"  {name}: revert-to-anchor WIN {wr*100:.1f}%  (vs 50% coin-flip -> edge {(wr-0.5)*100:+.1f}pt)")
            if len(early):
                print(f"      EARLY touch: {early[:,0].mean()*100:.1f}% (n{len(early)})   LATE touch: {late[:,0].mean()*100:.1f}% (n{len(late)})")
            if len(chop):
                print(f"      CHOP window: {chop[:,0].mean()*100:.1f}% (n{len(chop)})   TREND: {trend[:,0].mean()*100:.1f}% (n{len(trend)})")
            # net expectancy in $ (win +L, loss -L), before costs
            ev = (2 * wr - 1) * L
            print(f"      symmetric BTC-path proxy = ${ev:+.1f} (NOT binary-share P&L)")
    print("\nREAD: this is a causal BTC-path diagnostic after excluding ambiguous touch candles. It does not "
          "establish Polymarket expectancy; that requires executable entry asks, exit bids/settlement, fees, "
          "slippage, and one independent entry per round.")


def selftest():
    hi = np.array([1., 2., 3., 2., 1.]); lo = np.array([0., 1., 2., 1., 0.])
    e, win, xm, tm = _first_passage_fade(hi + 100, lo + 100, 100.0, 2.0, "down")
    print(f"selftest: touch at min {tm}, entered={e}, win={win} (reverts to anchor -> win expected)")
    print("PASS" if e == 1 else "FAIL")
    return e == 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if not os.path.exists(MATRIX):
        print(f"missing {MATRIX}"); sys.exit(2)
    run()


if __name__ == "__main__":
    main()
