"""Does the signal carry PATH information even when it has no SETTLEMENT information?

THE QUESTION ALL 31 PREVIOUS SCRIPTS STRUCTURALLY COULD NOT ASK
    Every earlier test measured one thing: is close(t+H) above or below close(t)? That came
    back at roughly AUC 0.50, and the conclusion drawn - including by me - was "no signal".

    But settlement and path are different random variables:

        settlement     close(t+H) > close(t)                  <- measured, ~0.50
        first passage  max/min over (t, t+H]                  <- NEVER MEASURED

    A signal can be worthless at settlement and still say something true about the path.
    Worked example: signal says DOWN at minute 0, price is down at minute 7, back up by
    minute 15. Settlement scores that WRONG. There was still a tradeable excursion, and on a
    venue where either side can be taken it does not even need the direction to be right - only
    that a move of size k occurs.

FOUR TESTS, ALL DIAGNOSTIC
    1  MFE / MAE distribution shift versus a matched-random control
    2  WHEN the excursion happens (is exit timing learnable at all?)
    3  TWO-SIDED magnitude - P(|move| >= k) with the sign discarded entirely
    4  First-passage asymmetry - P(touch +k BEFORE -j), the barrier payoff form

THE TRAP, STATED ONCE
    MFE is an ORACLE quantity. You cannot exit at it. Everything here measures whether
    information EXISTS - an upper bound on what a perfect exit could capture. None of it is a
    strategy, and a causal exit rule capturing a fraction of it is a separate, later question.
    MFE/MAE appear only as LABELS. No feature is ever derived from the future.

    Every comparison is against a matched-random control with the same entry count and the same
    holding period, because "more trades" and "longer holds" both inflate excursion statistics
    on their own.

    python research/path_information_test.py
    python research/path_information_test.py --horizon 15 --rows 200000
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import load_btc, split  # noqa: E402

RNG = np.random.default_rng(20260730)
DRAWS = 2000
LEVELS_BPS = (5.0, 10.0, 20.0, 40.0)


# ---------------------------------------------------------------------------------------------
# Causal features. These use ONLY information available at the bar. The 19 columns the previous
# suite never touched are the point of this file - signed aggressor flow and VPIN are the
# standard short-horizon microstructure predictors and none of them had been tried.
# ---------------------------------------------------------------------------------------------
def build_features(frame):
    frame = frame.copy()
    close = frame["close"]
    frame["ret_1"] = close.pct_change(1)
    frame["ret_5"] = close.pct_change(5)
    frame["vol_30"] = frame["ret_1"].rolling(30).std()

    # Signed aggressor flow - never used by any earlier script.
    total = (frame["taker_buy"] + frame["taker_sell"]).replace(0, np.nan)
    frame["flow_imb"] = (frame["taker_buy"] - frame["taker_sell"]) / total
    frame["flow_imb_z"] = ((frame["flow_imb"] - frame["flow_imb"].rolling(240).mean())
                           / frame["flow_imb"].rolling(240).std())

    # Toxicity / informed-trading proxy - never used.
    frame["vpin_z"] = ((frame["vpin_30m"] - frame["vpin_30m"].rolling(240).mean())
                       / frame["vpin_30m"].rolling(240).std())

    # Volatility TERM STRUCTURE slope, not level - a regime signal.
    frame["rv_slope"] = frame["rv_15m"] / frame["rv_60m"].replace(0, np.nan)

    # Compression: coiled range that historically precedes expansion.
    frame["compression_z"] = ((frame["compression_ratio"]
                               - frame["compression_ratio"].rolling(240).mean())
                              / frame["compression_ratio"].rolling(240).std())
    frame["shock_z"] = ((frame["shock_magnitude"]
                         - frame["shock_magnitude"].rolling(240).mean())
                        / frame["shock_magnitude"].rolling(240).std())
    frame["count_accel"] = frame["count_accel_5m"]
    return frame.dropna().reset_index(drop=True)


def signals(frame):
    """Directional and non-directional candidates, all causal."""
    return {
        # Directional - do these shift the excursion distribution?
        "flow_imbalance": np.where(frame["flow_imb_z"] > 1.5, 1,
                                   np.where(frame["flow_imb_z"] < -1.5, -1, 0)),
        "flow_reversal": np.where(frame["flow_imb_z"] > 2.0, -1,
                                  np.where(frame["flow_imb_z"] < -2.0, 1, 0)),
        "momentum_baseline": np.where(frame["ret_5"] > 0, 1, -1),
        # Non-directional - fire when a MOVE is expected, sign unknown. Tested two-sided.
        "compression_release": (frame["compression_z"] < -1.5).astype(int),
        "vpin_spike": (frame["vpin_z"] > 2.0).astype(int),
        "rv_term_inversion": (frame["rv_slope"] > 1.5).astype(int),
        "shock": (frame["shock_z"] > 2.0).astype(int),
    }


# ---------------------------------------------------------------------------------------------
# Path labels. These read the FUTURE and are labels only - never inputs to any signal above.
# ---------------------------------------------------------------------------------------------
def path_labels(frame, horizon: int):
    """MFE, MAE, argmax time and first-passage order for every bar.

    First passage uses a deliberately PESSIMISTIC intrabar convention: when a bar's high and
    low both breach their barriers, the ADVERSE one is assumed to have been touched first. A
    one-minute bar cannot say which came first, and the optimistic reading is how backtests
    invent money."""
    high = frame["high"].to_numpy()
    low = frame["low"].to_numpy()
    close = frame["close"].to_numpy()
    n = len(frame)

    mfe_up = np.full(n, np.nan)
    mfe_dn = np.full(n, np.nan)
    argmax_up = np.full(n, np.nan)
    argmax_dn = np.full(n, np.nan)

    for i in range(n - horizon):
        window_hi = high[i + 1:i + 1 + horizon]
        window_lo = low[i + 1:i + 1 + horizon]
        entry = close[i]
        up = (window_hi.max() / entry - 1.0) * 1e4
        dn = (window_lo.min() / entry - 1.0) * 1e4
        mfe_up[i] = up
        mfe_dn[i] = dn
        argmax_up[i] = int(np.argmax(window_hi)) + 1
        argmax_dn[i] = int(np.argmin(window_lo)) + 1

    frame = frame.copy()
    frame["up_bps"] = mfe_up          # best UP excursion in bps
    frame["dn_bps"] = mfe_dn          # worst DOWN excursion in bps (negative)
    frame["abs_bps"] = np.maximum(np.abs(mfe_up), np.abs(mfe_dn))
    frame["argmax_up"] = argmax_up
    frame["argmax_dn"] = argmax_dn
    return frame.dropna(subset=["up_bps", "dn_bps"]).reset_index(drop=True)


def first_passage(frame, horizon: int, k_bps: float, j_bps: float, side: np.ndarray):
    """P(touch +k before -j), pessimistic on intrabar ordering. Returns per-signal outcomes."""
    high = frame["high"].to_numpy()
    low = frame["low"].to_numpy()
    close = frame["close"].to_numpy()
    outcomes = []
    for i in np.where(side != 0)[0]:
        if i + horizon >= len(frame):
            continue
        direction = side[i]
        entry = close[i]
        target = entry * (1 + direction * k_bps / 1e4)
        stop = entry * (1 - direction * j_bps / 1e4)
        result = 0                                     # neither barrier touched
        for t in range(i + 1, i + 1 + horizon):
            hit_stop = (low[t] <= stop) if direction > 0 else (high[t] >= stop)
            hit_target = (high[t] >= target) if direction > 0 else (low[t] <= target)
            if hit_stop:                               # PESSIMISTIC: stop wins a tie
                result = -1
                break
            if hit_target:
                result = 1
                break
        outcomes.append(result)
    return np.asarray(outcomes)


# ---------------------------------------------------------------------------------------------
def matched_random_lift(values, signal_mask, statistic, draws: int = DRAWS):
    """Empirical p-value against random entries of the SAME COUNT.

    Both 'more entries' and 'longer holds' inflate excursion statistics by themselves, so the
    control fixes the count and the horizon and varies only WHICH bars are chosen."""
    observed = statistic(values[signal_mask])
    count = int(signal_mask.sum())
    if count < 30:
        return observed, float("nan"), float("nan")
    control = np.empty(draws)
    for d in range(draws):
        idx = RNG.integers(0, len(values), count)
        control[d] = statistic(values[idx])
    baseline = float(control.mean())
    p_value = (1 + int((control >= observed).sum())) / (draws + 1)
    return observed, baseline, p_value


def test_1_excursion_shift(frame, sigs, corrections: int) -> None:
    print("=" * 96)
    print("TEST 1 - does the signal shift the MFE distribution while settlement stays ~0.50?")
    print("=" * 96)
    settle = (frame["close"].shift(-1).to_numpy() > frame["close"].to_numpy())
    print(f"{'signal':<22}{'n':>7}{'settle acc':>12}"
          + "".join(f"{'P(MFE>=' + str(int(k)) + ')':>16}" for k in LEVELS_BPS))
    print("-" * 96)
    for name, side in sigs.items():
        directional = set(np.unique(side)) - {0} == {-1, 1}
        if not directional:
            continue
        mask = side != 0
        n = int(mask.sum())
        if n < 30:
            continue
        # MFE in the SIGNAL's direction
        favourable = np.where(side > 0, frame["up_bps"], -frame["dn_bps"])
        # settlement accuracy over the full horizon, for contrast
        realised_up = frame["close"].shift(-HORIZON).to_numpy() > frame["close"].to_numpy()
        agree = (realised_up == (side > 0))[mask]
        acc = float(np.nanmean(agree)) * 100
        cells = ""
        for k in LEVELS_BPS:
            hit = (favourable >= k).astype(float)
            observed, baseline, p = matched_random_lift(
                hit, mask, lambda v: float(np.nanmean(v)))
            flag = "*" if p == p and p < 0.05 / corrections else " "
            cells += f"{observed * 100:>8.1f}/{baseline * 100:<5.1f}{flag}"
        print(f"{name:<22}{n:>7}{acc:>11.1f}%{cells}")
    print("\n  each cell: P(MFE>=k | signal)% / P(MFE>=k | random)%")
    print(f"  * = beats the matched-random control at Bonferroni {0.05 / corrections:.5f}")
    _ = settle


def test_2_timing(frame, sigs) -> None:
    print("\n" + "=" * 96)
    print("TEST 2 - WHEN does the excursion happen? (is exit timing learnable at all?)")
    print("=" * 96)
    print(f"{'signal':<22}{'n':>7}{'median argmax':>16}{'q25':>8}{'q75':>8}   shape")
    print("-" * 96)
    for name, side in sigs.items():
        mask = side != 0
        if int(mask.sum()) < 30:
            continue
        argmax = np.where(side > 0, frame["argmax_up"], frame["argmax_dn"])[mask]
        q25, med, q75 = np.nanpercentile(argmax, [25, 50, 75])
        spread = q75 - q25
        shape = ("clustered EARLY" if med <= HORIZON * 0.35 else
                 "clustered LATE" if med >= HORIZON * 0.65 else
                 "uniform - no timing edge" if spread > HORIZON * 0.5 else "mid-window")
        print(f"{name:<22}{int(mask.sum()):>7}{med:>15.1f}m{q25:>8.1f}{q75:>8.1f}   {shape}")
    print("\n  a uniform argmax means no clock-based exit can work; clustering means it can")


def test_3_two_sided(frame, sigs, corrections: int) -> None:
    print("\n" + "=" * 96)
    print("TEST 3 - TWO-SIDED magnitude. Sign discarded: does a MOVE happen at all?")
    print("=" * 96)
    print("  Direction is hard; realised volatility is autocorrelated. On a venue where either")
    print("  side can be taken, P(|move| >= k) is the tradeable quantity - and it is untested.")
    print()
    print(f"{'signal':<22}{'n':>7}"
          + "".join(f"{'P(|mv|>=' + str(int(k)) + ')':>16}" for k in LEVELS_BPS))
    print("-" * 96)
    magnitude = frame["abs_bps"].to_numpy()
    for name, side in sigs.items():
        mask = side != 0
        n = int(mask.sum())
        if n < 30:
            continue
        cells = ""
        for k in LEVELS_BPS:
            hit = (magnitude >= k).astype(float)
            observed, baseline, p = matched_random_lift(
                hit, mask, lambda v: float(np.nanmean(v)))
            flag = "*" if p == p and p < 0.05 / corrections else " "
            cells += f"{observed * 100:>8.1f}/{baseline * 100:<5.1f}{flag}"
        print(f"{name:<22}{n:>7}{cells}")
    print("\n  each cell: P(|move|>=k | signal)% / P(|move|>=k | random)%")


def test_4_first_passage(frame, sigs, corrections: int) -> None:
    print("\n" + "=" * 96)
    print("TEST 4 - FIRST PASSAGE: P(touch +k BEFORE -j). The barrier payoff form.")
    print("=" * 96)
    print("  At 50% settlement accuracy a barrier trade can still pay if +k is reached before")
    print("  -j more often than the payoff ratio requires. Intrabar ties go to the STOP.")
    print()
    pairs = [(10.0, 10.0), (20.0, 10.0), (30.0, 10.0), (10.0, 20.0)]
    print(f"{'signal':<22}{'+k/-j':>10}{'n':>7}{'win%':>8}{'timeout%':>10}"
          f"{'breakeven%':>12}{'edge':>9}")
    print("-" * 96)
    for name, side in sigs.items():
        if set(np.unique(side)) - {0} != {-1, 1}:
            continue
        for k, j in pairs:
            outcomes = first_passage(frame, HORIZON, k, j, side)
            if len(outcomes) < 30:
                continue
            wins = int((outcomes == 1).sum())
            losses = int((outcomes == -1).sum())
            timeouts = int((outcomes == 0).sum())
            decided = wins + losses
            if decided == 0:
                continue
            win_rate = wins / decided * 100
            breakeven = j / (k + j) * 100          # win rate needed at this payoff ratio
            edge = win_rate - breakeven
            mark = "  <--" if edge > 0 else ""
            print(f"{name:<22}{f'+{int(k)}/-{int(j)}':>10}{decided:>7}{win_rate:>7.1f}%"
                  f"{timeouts / len(outcomes) * 100:>9.1f}%{breakeven:>11.1f}%{edge:>+8.1f}{mark}")
    print("\n  edge = win% - breakeven%. Positive means the barrier structure pays BEFORE costs.")
    print("  Costs are NOT deducted here: this is a diagnostic of path structure, not a PnL.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--horizon", type=int, default=15)
    parser.add_argument("--rows", type=int, default=200_000)
    args = parser.parse_args()

    global HORIZON
    HORIZON = args.horizon

    print("loading real BTC 1m data and building CAUSAL features from the 19 unused columns...")
    frame = build_features(load_btc(args.rows))
    frame = path_labels(frame, HORIZON)
    _, test = split(frame)
    test = test.reset_index(drop=True)
    sigs = {name: fn[test.index.to_numpy()] if len(fn) == len(frame) else fn
            for name, fn in signals(frame).items()}
    sigs = {name: np.asarray(signals(test)[name]) for name in signals(test)}

    print(f"out-of-sample bars: {len(test):,}   horizon: {HORIZON}m\n")

    corrections = len(sigs) * len(LEVELS_BPS) * 2      # tests 1 and 3 share the correction
    test_1_excursion_shift(test, sigs, corrections)
    test_2_timing(test, sigs)
    test_3_two_sided(test, sigs, corrections)
    test_4_first_passage(test, sigs, corrections)

    print("\n" + "=" * 96)
    print("HOW TO READ THIS")
    print("=" * 96)
    print("  MFE unshifted vs random          -> no path information; the lane really is dead")
    print("  MFE shifted, MAE unshifted       -> real asymmetry; barrier trade viable")
    print("  |move| predictable, direction not-> straddle lane; trade both sides, ignore sign")
    print("  argmax clustered                 -> a clock-based exit is learnable")
    print()
    print("  NONE of this is a strategy. MFE is an ORACLE quantity that no live rule can")
    print("  capture. These measure whether information EXISTS - the upper bound. A causal")
    print("  exit rule capturing a fraction of it, net of costs, is a separate later question,")
    print("  and it must beat random exits with matched holding time before it means anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
