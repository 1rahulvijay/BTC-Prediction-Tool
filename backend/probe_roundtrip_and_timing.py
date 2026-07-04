"""
probe_roundtrip_and_timing.py - the two numbers the LIVE fade gate needs (honest, leak-free).
==============================================================================================
Answers two operator questions on the 1-min research matrix:

  1. EARLY-TOUCH FADE: fade win% (revert to anchor before the 2x stop) bucketed by touch TIMING
     quartile. The gate should require a *genuinely early* touch -- this shows the honest win% of
     the earliest quartile (Q1) vs the rest, at $20 / $30 / $50 barriers. (Polymarket share prices
     move on $20-30, not just $50, so smaller barriers are tested too.)

  2. ROUND-TRIP (bet-both-ways): how often BOTH the +L and -L barriers are touched in one window
     (price goes both directions) -- the setup where you fade both extremes, take profit on each,
     and exit. Reports the round-trip rate and, when it happens, whether BOTH fades revert to anchor
     (the both-ways win), by barrier size.

Leak-free: extremes/first-passage strictly inside the future window. Reuses probe_fade_entry_exit.
ASCII output.

Usage:
  python backend/probe_roundtrip_and_timing.py
  python backend/probe_roundtrip_and_timing.py --selftest
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_fade_entry_exit as FE  # noqa: E402

warnings.filterwarnings("ignore")
MATRIX = os.path.join(FE.TA.ROOT, "data", "research_matrix_1m.parquet")
LEVELS = (20.0, 30.0, 50.0)


def _fade_strict(hi, lo, anc, L, side):
    """STRICT fade grade: win ONLY if the anchor TP is reached before the 2L stop. Unresolved by
    expiry = NO PROFIT (loss). This is what a Polymarket take-profit-at-anchor actually needs -- it
    removes the settle-by-close fallback that inflates late touches in _first_passage_fade.
    Returns (entered, win_strict, touch_minute)."""
    n = len(hi)
    tp = anc
    stop = anc + 2 * L if side == "down" else anc - 2 * L
    lvl = anc + L if side == "down" else anc - L
    touched = -1
    for k in range(n):
        if (side == "down" and hi[k] >= lvl) or (side == "up" and lo[k] <= lvl):
            touched = k
            break
    if touched < 0:
        return (0, 0, -1)
    for k in range(touched + 1, n):
        if side == "down":
            if lo[k] <= tp:
                return (1, 1, touched)     # reached anchor -> real TP
            if hi[k] >= stop:
                return (1, 0, touched)      # hit stop
        else:
            if hi[k] >= tp:
                return (1, 1, touched)
            if lo[k] <= stop:
                return (1, 0, touched)
    return (1, 0, touched)                   # never reverted to anchor by expiry -> no profit


def run(w, stride=2):
    df = pd.read_parquet(MATRIX).sort_values("ts_ms").drop_duplicates("ts_ms").reset_index(drop=True)
    c = df["close"].values
    H = np.column_stack([df["high"].shift(-k).values for k in range(1, w + 1)])
    Lo = np.column_stack([df["low"].shift(-k).values for k in range(1, w + 1)])
    ok = (~np.isnan(H).any(1)) & (~np.isnan(Lo).any(1))
    idx = np.where(ok)[0][::stride]
    print("\n" + "=" * 92)
    print(f"BTC {w}m  -  early-touch fade + round-trip (n_windows={len(idx)})")
    for L in LEVELS:
        # per-window: first-touch fade (either side) with its touch minute; and round-trip flag
        fade_rows = []      # (win_settle, win_strict, touch_frac) for the first touched side
        rt_hits = 0; rt_both_win = 0; any_touch = 0
        for i in idx:
            anc = c[i]
            up_t = (np.nanmax(H[i]) - anc) >= L
            dn_t = (anc - np.nanmin(Lo[i])) >= L
            if up_t or dn_t:
                any_touch += 1
            # first-passage fade for each touched side (settle-generous AND strict)
            wins = {}
            for side, cond in (("down", up_t), ("up", dn_t)):   # side='down' => fade an UP touch
                if cond:
                    e, win, xm, tm = FE._first_passage_fade(H[i], Lo[i], anc, L, side)
                    _, ws, _ = _fade_strict(H[i], Lo[i], anc, L, side)
                    if e:
                        wins[side] = (win, ws, (w - tm) / w, tm)
            if wins:
                first = min(wins.values(), key=lambda t: t[3])   # earliest touch = first opportunity
                fade_rows.append((first[0], first[1], first[2]))
            if up_t and dn_t:                                    # round-trip window (both barriers)
                rt_hits += 1
                if len(wins) == 2 and wins["down"][1] and wins["up"][1]:   # BOTH strict-win
                    rt_both_win += 1
        fr = np.array(fade_rows) if fade_rows else np.empty((0, 3))
        base = fr[:, 0].mean() if len(fr) else float("nan")
        base_s = fr[:, 1].mean() if len(fr) else float("nan")
        print(f"\n  --- ${L:.0f} barrier ---   touch rate {any_touch/len(idx)*100:4.1f}%   "
              f"fade base-win {base*100:4.1f}% (settle) / {base_s*100:4.1f}% (STRICT)   n_fades={len(fr)}")
        if len(fr):
            tf = fr[:, 2]
            qs = np.quantile(tf, [0.75, 0.5, 0.25])      # touch_frac quartile cuts (high=early)
            names = ["Q1 earliest (top-quartile timing)", "Q2", "Q3", "Q4 latest"]
            cuts = [(tf >= qs[0]), (tf >= qs[1]) & (tf < qs[0]), (tf >= qs[2]) & (tf < qs[1]), (tf < qs[2])]
            for nm, m in zip(names, cuts):
                if m.sum():
                    print(f"      {nm:<38} win {fr[m,0].mean()*100:4.1f}% settle / {fr[m,1].mean()*100:4.1f}% STRICT   (n{int(m.sum())})")
        rt_rate = rt_hits / len(idx) * 100
        rt_bw = (rt_both_win / rt_hits * 100) if rt_hits else float("nan")
        print(f"      ROUND-TRIP (both +/-${L:.0f} touched): {rt_rate:4.1f}% of windows;  "
              f"both fades revert-to-anchor {rt_bw:4.1f}% (n_rt={rt_hits})")
    print("\nREAD: gate the live fade on Q1 (earliest touch) -> that is the honest high-win bucket. Round-trip"
          " rate rises fast as the barrier shrinks -> the bet-both-ways setup is far more frequent at $20-30.")


def selftest():
    global MATRIX
    df = pd.DataFrame({"close": 60000 + np.cumsum(np.random.default_rng(0).normal(0, 8, 800))})
    df["high"] = df["close"] + 25; df["low"] = df["close"] - 25; df["ts_ms"] = np.arange(800) * 60000
    tmp = os.path.join(os.path.dirname(MATRIX), "_selftest_rt.parquet")   # temp matrix to exercise run()
    df.to_parquet(tmp)
    old = MATRIX; MATRIX = tmp
    try:
        run(5, stride=1)
        ok = True
    except Exception as e:
        print(f"selftest error: {e}"); ok = False
    finally:
        MATRIX = old
        try:
            os.remove(tmp)
        except OSError:
            pass
    print("PASS" if ok else "FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--h", type=int, default=0, help="0 = both 5m and 15m")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(0 if selftest() else 1)
    if not os.path.exists(MATRIX):
        print(f"missing {MATRIX}"); sys.exit(2)
    for w in ((a.h,) if a.h else (5, 15)):
        run(w)


if __name__ == "__main__":
    main()
