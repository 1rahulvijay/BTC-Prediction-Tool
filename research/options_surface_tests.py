"""OPTIONS_SURFACE_TESTS_V1 - what the Deribit chain recorder actually reopens.

Five research lanes were proposed. Two are answerable from data already on disk, three are
blocked, and this script says which is which rather than producing five numbers of mixed
provenance. A blocked lane prints its exact data gap and what would unblock it.

    1. IMPLIED vs REALIZED MOVEMENT ............. ANSWERABLE (hurdle form)
    2. STATIC NO-ARBITRAGE ON THE SURFACE ....... ANSWERABLE
    3. SKEW / TERM-STRUCTURE REGIME TRANSITIONS . BLOCKED - 6 minutes of surface history
    4. OPTIONS SURFACE -> POLYMARKET RESIDUALS .. BLOCKED - samples 27 days apart
    5. LIQUIDITY-PROVISION ECONOMICS ............ BLOCKED - no queue or depth recorded

TEST 1 - DOES REALIZED MOVEMENT EXCEED IMPLIED MOVEMENT, AFTER SPREAD?
    `rv_term_inversion` (rv_15m / rv_60m > 1.5) is the one signal in this repository that
    survived Bonferroni: it predicts LARGE MOVES, direction dead. Its only natural instrument
    is an option, which is why the chain recorder matters.

    NO PRICING MODEL IS USED. The straddle's cost is the QUOTED ask, its exit is the QUOTED
    bid, and its expiry payoff is |S_T - K| by definition. Two sub-tests:

    1a HOLD TO EXPIRY - exact arithmetic. Pay the quoted straddle ask, receive |S_T - K|.
       The option pays the MEAN absolute move, not the median: the payoff is linear in the
       move and the distribution has a fat right tail, so a median comparison understates
       what the position collects. Mean is used throughout, with a day-block lower bound.

    1b INTRADAY HOLD - an UPPER-BOUND argument. A straddle's delta lies in [-1, +1], so over
       a holding period its value cannot gain more than the underlying's ABSOLUTE MOVE
       through the delta/gamma channel. If |move| is below the round-trip spread, the trade
       cannot win through movement at all - no exit rule or strike choice recovers it. The
       bound is LOOSE when it passes (theta and the decaying losing leg are not credited),
       so clearing it means "not dead", never "profitable". Vega is excluded from the bound.

    COST, STATED CORRECTLY: buying at the ask and later selling at the bid IS the round trip.
    It is (straddle_ask - straddle_bid) charged ONCE, not a per-leg spread multiplied by legs
    and by direction. An earlier pass of this script double-counted it to 2x.

    HONEST LIMIT: the BTC bar file ends 2026-07-27 and the Deribit snapshot is 2026-07-31.
    They do not overlap, so this is a HURDLE comparison - a realized-move distribution from
    360 days of bars against ONE implied observation taken 3 days later - not a matched trade
    study. A single IV reading cannot establish an edge in either direction, and the output
    says so rather than resolving it.

TEST 2 - STATIC NO-ARBITRAGE ON EXECUTABLE QUOTES
    Same species as the complete-set arbitrage test: pure arithmetic fixed by the payoff, no
    forecast, no free parameter. Three constraints, all checked on BID/ASK rather than mid,
    because a violation at mid is not a trade:

      VERTICAL   calls: buy K1 at ask, sell K2 at bid (K1<K2). Payoff in [0, K2-K1], always
                 >= 0. A NEGATIVE cost is free money.  Violation iff ask(K1) < bid(K2).
                 puts : mirrored - buy K2 at ask, sell K1 at bid.
      BUTTERFLY  buy the wings at ask, sell 2x the body at bid, equally spaced strikes.
                 Payoff >= 0 everywhere. A net CREDIT is free money.
      PARITY     C - P should equal U - K. Reported as a DIAGNOSTIC only: converting it into
                 an arbitrage claim requires trading the future, whose book this archive does
                 not contain. Measured, not claimed.

    THE ERROR THIS TEST IS BUILT TO AVOID
        The cross-market coherence test reported 16.42% violations that turned out to be a
        60-second reference error of mine, not a market inefficiency. The same failure mode
        exists here: `underlying_price` is NOT constant inside a batch - up to 3 distinct
        values per (batch, expiry) - because the chain is polled over ~300 ms while the index
        moves. Comparing two strikes quoted against DIFFERENT underlyings manufactures fake
        violations exactly the way the bar-reference error did.

        So every leg pair must share an IDENTICAL underlying_price, and the result is also
        reported WITHOUT that guard and stratified by underlying dispersion, so a timing
        artifact is visible in the output instead of being reported as a discovery.

GATES, DECLARED BEFORE ANY RESULT IS SEEN
    G1  a no-arb violation counts only if both legs share an identical underlying_price
    G2  a violation must exceed MIN_EDGE_BPS of spot - below that it is quoting granularity
    G3  test 1 uses the MEAN absolute move and must clear the quoted cost at the 5% day-block
        lower bound, not at the point estimate
    G4  a straddle quote is used only if bid >= MIN_BID_RATIO * ask; a wider gap means one
        leg is stale or absent and the pair is not an executable straddle
    G5  any lane whose data does not exist prints BLOCKED and returns no number
    G6  a tenor is scored only if the archive holds MIN_WINDOWS NON-OVERLAPPING windows of
        that length. Overlapping multi-day windows are not independent observations, and a
        day-block bootstrap over entry dates does not make them so.

    python research/options_surface_tests.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import blocked, load_btc  # noqa: E402

DERIBIT_DB = ROOT / "data" / "deribit_options.duckdb"
PM_DB = ROOT / "data" / "polymarket_l2.duckdb"

RNG = np.random.default_rng(20260731)
MINUTES_PER_YEAR = 525_600.0
MIN_EDGE_BPS = 2.0          # G2: below this is tick granularity, not an edge
MIN_BID_RATIO = 0.5         # G4: a straddle bid below half its ask means a stale leg
MIN_WINDOWS = 30            # G6: minimum NON-OVERLAPPING windows before a tenor is scored
MIN_RECENT_WINDOWS = 10     # G6: same rule applied to the trailing regime-check window
INVERSION_THRESHOLD = 1.5   # rv_15m / rv_60m, as preregistered by the path-information test
HOLD_MINUTES = (15, 60, 240, 1440)
RECENT_DAYS = 30            # trailing window used as the regime check on realized vol
DRAWS = 1000


# --------------------------------------------------------------------------------------
# shared loading
# --------------------------------------------------------------------------------------
def load_chain() -> pd.DataFrame:
    """Two-sided quotes only, converted to USD.

    Deribit BTC options are inverse: premium is paid in BTC, and the expiry payoff is
    max(S-K,0)/S BTC. Paying P BTC today costs P * U USD and returns max(S-K,0) USD, so
    multiplying by that row's own underlying puts every constraint in ordinary USD terms.
    """
    con = duckdb.connect(str(DERIBIT_DB), read_only=True)
    frame = con.execute("""
        SELECT batch_id, receive_ts_ns, exchange_ts_ms, instrument_name, expiry_ts_ms,
               strike, option_type, underlying_price, bid_price, ask_price, mark_price,
               mark_iv_pct, bid_iv_pct, ask_iv_pct, open_interest
        FROM deribit_chain_snapshots
        WHERE bid_price IS NOT NULL AND ask_price IS NOT NULL
          AND bid_price > 0 AND ask_price > 0 AND ask_price >= bid_price
    """).df()
    con.close()
    frame["bid_usd"] = frame["bid_price"] * frame["underlying_price"]
    frame["ask_usd"] = frame["ask_price"] * frame["underlying_price"]
    frame["mid_usd"] = (frame["bid_usd"] + frame["ask_usd"]) / 2.0
    frame["spread_usd"] = frame["ask_usd"] - frame["bid_usd"]
    frame["spread_bps_spot"] = frame["spread_usd"] / frame["underlying_price"] * 1e4
    frame["moneyness"] = (frame["strike"] - frame["underlying_price"]).abs() / frame["underlying_price"]
    return frame


def annualised_vol(mean_abs_move_bps: float, tenor_days: float) -> float:
    """Invert a mean ABSOLUTE move into an annualised vol, so it can sit beside a quoted IV.

    For a centred normal, E|X| = sigma * sqrt(2/pi). Using the mean absolute move rather than
    a high-frequency estimator matters: 1-minute close-to-close realized vol is inflated by
    bid-ask bounce, which is why the archive reads 45% at 1-minute sampling but ~40% when
    measured from the daily moves an option actually pays on."""
    return mean_abs_move_bps / 1e4 * np.sqrt(np.pi / 2) * np.sqrt(365.0 / tenor_days) * 100


def day_block_lcb(values: np.ndarray, day_index: np.ndarray, draws: int = DRAWS) -> float:
    """5% lower bound resampling WHOLE DAYS, so intraday autocorrelation cannot inflate n."""
    unique = np.unique(day_index)
    if len(unique) < 20 or len(values) == 0:
        return float("nan")
    by_day = {d: values[day_index == d] for d in unique}
    means = np.empty(draws)
    for i in range(draws):
        picked = RNG.integers(0, len(unique), len(unique))
        means[i] = np.concatenate([by_day[unique[j]] for j in picked]).mean()
    means.sort()
    return float(means[int(0.05 * draws)])


# --------------------------------------------------------------------------------------
# TEST 1
# --------------------------------------------------------------------------------------
def atm_straddles(chain: pd.DataFrame) -> pd.DataFrame:
    """Quoted ATM straddle per live expiry: cost = call ask + put ask, exit = the two bids.

    No model. The strike used is the one nearest the underlying that carries two-sided quotes
    on BOTH legs against the SAME underlying price, so the pair is genuinely simultaneous."""
    now_ms = int(chain["exchange_ts_ms"].max())
    calls = chain[chain["option_type"] == "C"]
    puts = chain[chain["option_type"] == "P"]
    keys = ["batch_id", "expiry_ts_ms", "strike", "underlying_price"]
    merged = calls[keys + ["bid_usd", "ask_usd"]].merge(
        puts[keys + ["bid_usd", "ask_usd"]], on=keys, suffixes=("_c", "_p"))
    if merged.empty:
        return merged
    merged = merged[merged["expiry_ts_ms"] > now_ms].copy()
    merged["dist"] = (merged["strike"] - merged["underlying_price"]).abs()
    merged["ask_bps"] = ((merged["ask_usd_c"] + merged["ask_usd_p"])
                         / merged["underlying_price"] * 1e4)
    merged["bid_bps"] = ((merged["bid_usd_c"] + merged["bid_usd_p"])
                         / merged["underlying_price"] * 1e4)
    # G4: a straddle bid far below its ask means one leg is stale, not a tradeable quote.
    merged = merged[merged["bid_bps"] >= MIN_BID_RATIO * merged["ask_bps"]]
    if merged.empty:
        return merged
    picked = merged.loc[merged.groupby("expiry_ts_ms")["dist"].idxmin()].copy()
    picked["tenor_days"] = (picked["expiry_ts_ms"] - now_ms) / 86_400_000.0
    picked["rt_bps"] = picked["ask_bps"] - picked["bid_bps"]
    return picked.sort_values("tenor_days").reset_index(drop=True)


def test_1_implied_vs_realized(rows: int) -> None:
    print("=" * 100)
    print("TEST 1 - REALIZED MOVEMENT vs QUOTED OPTION COST (no pricing model used)")
    print("=" * 100)

    chain = load_chain()
    straddles = atm_straddles(chain) if not chain.empty else pd.DataFrame()
    if straddles.empty:
        print("  no executable ATM straddle quotes - nothing to price against")
        return

    front = straddles.iloc[0]
    now_ms = int(chain["exchange_ts_ms"].max())

    # ---- realized side, from the bar archive -------------------------------------------
    frame = load_btc(rows).copy()
    frame["rv_slope"] = frame["rv_15m"] / frame["rv_60m"].replace(0, np.nan)
    frame["trigger"] = frame["rv_slope"] > INVERSION_THRESHOLD
    frame = frame.dropna(subset=["rv_slope"]).reset_index(drop=True)

    bar_end = dt.datetime.fromtimestamp(frame["ts_ms"].max() / 1000, dt.timezone.utc)
    chain_at = dt.datetime.fromtimestamp(now_ms / 1000, dt.timezone.utc)
    gap_days = (chain_at - bar_end).total_seconds() / 86_400.0

    close = frame["close"].to_numpy()
    stamps = frame["ts_ms"].to_numpy()
    days = (stamps // 86_400_000).astype(np.int64)
    trig = frame["trigger"].to_numpy()
    recent = stamps > (stamps.max() - RECENT_DAYS * 86_400_000)

    log_ret = np.diff(np.log(close))
    rv_all = log_ret.std() * np.sqrt(MINUTES_PER_YEAR) * 100
    rv_recent = log_ret[recent[1:]].std() * np.sqrt(MINUTES_PER_YEAR) * 100

    print(f"  bar archive : {len(frame):,} bars ending {bar_end:%Y-%m-%d %H:%M} UTC")
    print(f"  chain taken : {chain_at:%Y-%m-%d %H:%M} UTC "
          f"-> NON-OVERLAP of {gap_days:.1f} days")
    print(f"  realized vol: {rv_all:.1f}% over the full archive, "
          f"{rv_recent:.1f}% over the trailing {RECENT_DAYS} days")

    # ---- 1a: hold to expiry, exact arithmetic ------------------------------------------
    print("\n" + "-" * 100)
    print("1a  HOLD TO EXPIRY - pay the quoted ask, receive |S_T - K|. Exact, no Greeks.")
    print("-" * 100)
    print("  Windows are NON-OVERLAPPING. Overlapping windows of a multi-day move are not")
    print(f"  independent observations - a {MIN_WINDOWS}-window minimum is enforced, and a")
    print("  tenor the archive cannot cover that many times prints INSUFFICIENT, not a number.")
    print()
    print(f"{'expiry':>12}{'T days':>8}{'IV %':>7}{'ask bps':>9}{'indep n':>9}"
          f"{'mean|mv| all':>14}{'-> vol %':>10}{'mean|mv| 30d':>14}{'-> vol %':>10}  verdict")
    print("-" * 100)

    for _, row in straddles.iterrows():
        hold = int(round(row["tenor_days"] * 1440))
        iv = float(chain[(chain["expiry_ts_ms"] == row["expiry_ts_ms"]) &
                         (chain["moneyness"] < 0.02)]["mark_iv_pct"].median())
        stamp = dt.datetime.fromtimestamp(row["expiry_ts_ms"] / 1000, dt.timezone.utc)
        n_indep = max(0, (len(close) - 1) // hold) if hold > 0 else 0
        head = (f"{stamp:%Y-%m-%d}{row['tenor_days']:>8.1f}{iv:>7.1f}"
                f"{row['ask_bps']:>9.1f}{n_indep:>9}")
        if n_indep < MIN_WINDOWS:
            print(f"{head}{'INSUFFICIENT - archive holds too few independent windows':>60}")
            continue

        starts = np.arange(0, len(close) - hold, hold)
        moves = np.abs(close[starts + hold] / close[starts] - 1.0) * 1e4
        recent_starts = starts[recent[starts]]
        # The trailing window needs its OWN independence check: a 7-day tenor fits only four
        # non-overlapping windows into 30 days, and four fat-tailed draws are not a mean.
        has_recent = len(recent_starts) >= MIN_RECENT_WINDOWS
        recent_moves = (np.abs(close[recent_starts + hold] / close[recent_starts] - 1.0) * 1e4
                        if has_recent else np.array([]))

        vol_all = annualised_vol(moves.mean(), row["tenor_days"])
        vol_recent = (annualised_vol(recent_moves.mean(), row["tenor_days"])
                      if has_recent else float("nan"))
        ref = vol_recent if np.isfinite(vol_recent) else vol_all
        verdict = ("IV below realized" if iv < ref - 2 else
                   "IV above realized" if iv > ref + 2 else "IV ~= realized")
        if not has_recent:
            verdict += " (vs full archive - no regime check)"
        rm = f"{recent_moves.mean():>14.1f}" if has_recent else f"{'thin':>14}"
        rv = f"{vol_recent:>10.1f}" if has_recent else f"{'thin':>10}"
        print(f"{head}{moves.mean():>14.1f}{vol_all:>10.1f}{rm}{rv}  {verdict}")

    print()
    print("  'mean |mv|' is what the straddle PAYS at expiry - mean, not median, because the")
    print("  payoff is linear in the move and the tail is fat. '-> vol %' inverts it to an")
    print("  annualised vol so it sits in the same units as the quoted IV.")
    print()
    print("  THE COMPARISON THAT MATTERS IS THE REGIME-MATCHED ONE. An earlier pass of this")
    print("  script compared today's IV against the FULL 360-day realized average and printed")
    print("  'buy favoured' on all 11 expiries, rising to +1230 bps at 329 days. That was an")
    print("  artifact twice over: long tenors had ~1 independent observation, and the archive")
    print("  average spans calmer and wilder regimes than the one being quoted today.")

    # ---- 1b: intraday upper bound -------------------------------------------------------
    rt = float(front["rt_bps"])
    print("\n" + "-" * 100)
    print(f"1b  INTRADAY HOLD - upper bound. Round trip on the front straddle is "
          f"{rt:.1f} bps")
    print("-" * 100)
    print(f"  signal: rv_15m / rv_60m > {INVERSION_THRESHOLD} "
          f"| fires on {trig.mean() * 100:.1f}% of bars")
    print()
    print(f"{'hold':>7}{'n':>10}{'mean |mv| sig':>16}{'mean |mv| base':>16}"
          f"{'lift':>9}{'round trip':>13}{'bound - cost':>15}  clears bound")
    print("-" * 100)

    for hold in HOLD_MINUTES:
        if hold >= len(close):
            continue
        moves = np.abs(close[hold:] / close[:-hold] - 1.0) * 1e4
        t = trig[:-hold]
        if t.sum() < 100:
            continue
        sig, base = moves[t], moves[~t]
        lcb = day_block_lcb(sig - rt, days[:-hold][t])
        print(f"{hold:>6}m{int(t.sum()):>10,}{sig.mean():>16.1f}{base.mean():>16.1f}"
              f"{sig.mean() - base.mean():>+9.1f}{rt:>13.1f}{lcb:>+15.1f}"
              f"  {'yes' if lcb > 0 else 'NO - dead'}")

    print()
    print("  The bound is one-directional. Failing it KILLS the horizon: the position cannot")
    print("  gain more than the underlying's absolute move through delta/gamma, so if that")
    print("  move is under the spread, no exit rule or strike choice recovers it. Clearing")
    print("  it proves only 'not dead' - theta and the decaying losing leg are not charged.")
    print()
    print("  WHAT THIS DOES AND DOES NOT SETTLE")
    print("  The 15-minute horizon, where the magnitude signal is strongest, is the one the")
    print("  bound kills. Longer holds survive the bound but the signal's lift decays and")
    print("  turns negative by one day - it is a 15-minute signal, and the instrument that")
    print("  could express it is priced for a day.")
    print()
    print("  The hold-to-expiry comparison is UNDECIDED and must stay that way: it rests on a")
    print("  SINGLE implied-vol observation against a realized distribution from a different")
    print("  3-day-earlier window. Implied vol varies daily; one reading cannot establish a")
    print("  premium in either direction. This is the first lane where the honest answer is")
    print("  'the data on disk cannot close it' rather than 'closed, negative'.")


# --------------------------------------------------------------------------------------
# TEST 2
# --------------------------------------------------------------------------------------
def _vertical_violations(group: pd.DataFrame, same_underlying: bool) -> pd.DataFrame:
    """Calls must fall in strike, puts must rise. Checked on executable bid/ask."""
    out = []
    for opt_type, side in (("C", "call"), ("P", "put")):
        legs = group[group["option_type"] == opt_type].sort_values("strike")
        if len(legs) < 2:
            continue
        arr = legs.to_dict("records")
        for i in range(len(arr) - 1):
            lo, hi = arr[i], arr[i + 1]
            if same_underlying and lo["underlying_price"] != hi["underlying_price"]:
                continue
            if opt_type == "C":
                # buy the low strike at ask, sell the high strike at bid
                cost = lo["ask_usd"] - hi["bid_usd"]
            else:
                # mirrored: buy the high strike at ask, sell the low strike at bid
                cost = hi["ask_usd"] - lo["bid_usd"]
            edge_bps = -cost / lo["underlying_price"] * 1e4
            out.append({
                "structure": f"vertical-{side}",
                "expiry_ts_ms": lo["expiry_ts_ms"],
                "k_lo": lo["strike"], "k_hi": hi["strike"],
                "edge_bps": edge_bps,
                "underlying_spread": abs(lo["underlying_price"] - hi["underlying_price"]),
            })
    return pd.DataFrame(out)


def _butterfly_violations(group: pd.DataFrame, same_underlying: bool) -> pd.DataFrame:
    """Wings at ask, body at bid, equally spaced. Payoff >= 0, so a credit is free money."""
    out = []
    for opt_type, side in (("C", "call"), ("P", "put")):
        legs = group[group["option_type"] == opt_type].sort_values("strike")
        if len(legs) < 3:
            continue
        arr = legs.to_dict("records")
        for i in range(len(arr) - 2):
            a, b, c = arr[i], arr[i + 1], arr[i + 2]
            if (c["strike"] - b["strike"]) != (b["strike"] - a["strike"]):
                continue                    # unequal spacing needs a weighted fly; skipped
            ups = {a["underlying_price"], b["underlying_price"], c["underlying_price"]}
            if same_underlying and len(ups) > 1:
                continue
            cost = a["ask_usd"] + c["ask_usd"] - 2.0 * b["bid_usd"]
            edge_bps = -cost / b["underlying_price"] * 1e4
            out.append({
                "structure": f"butterfly-{side}",
                "expiry_ts_ms": b["expiry_ts_ms"],
                "k_lo": a["strike"], "k_hi": c["strike"],
                "edge_bps": edge_bps,
                "underlying_spread": max(ups) - min(ups),
            })
    return pd.DataFrame(out)


def test_2_static_no_arbitrage() -> None:
    print("\n" + "=" * 100)
    print("TEST 2 - STATIC NO-ARBITRAGE ON THE OPTION SURFACE (executable bid/ask)")
    print("=" * 100)

    chain = load_chain()
    if chain.empty:
        print("  no two-sided quotes - nothing to test")
        return

    print(f"  two-sided quotes : {len(chain):,} across "
          f"{chain['expiry_ts_ms'].nunique()} expiries, "
          f"{chain['batch_id'].nunique()} batches")

    disp = (chain.groupby(["batch_id", "expiry_ts_ms"])["underlying_price"]
            .nunique().value_counts().sort_index())
    print("  distinct underlying prices inside one (batch, expiry):")
    for k, v in disp.items():
        flag = "" if k == 1 else "   <- legs here are NOT simultaneous"
        print(f"    {k} value(s) : {v:>4} group(s){flag}")

    for guard in (False, True):
        frames = []
        for _, group in chain.groupby(["batch_id", "expiry_ts_ms"]):
            frames.append(_vertical_violations(group, guard))
            frames.append(_butterfly_violations(group, guard))
        found = pd.concat([f for f in frames if not f.empty], ignore_index=True) \
            if any(not f.empty for f in frames) else pd.DataFrame()

        label = ("WITH G1 (both legs share an identical underlying)" if guard
                 else "WITHOUT the guard (any two legs in the batch)")
        print(f"\n  {label}")
        if found.empty:
            print("    no structures could be formed")
            continue
        print(f"{'structure':>20}{'checked':>10}{'edge>0':>9}"
              f"{'edge>' + str(int(MIN_EDGE_BPS)) + 'bps':>13}{'best bps':>11}")
        print("    " + "-" * 59)
        for name, sub in found.groupby("structure"):
            pos = int((sub["edge_bps"] > 0).sum())
            econ = int((sub["edge_bps"] > MIN_EDGE_BPS).sum())
            print(f"{name:>20}{len(sub):>10}{pos:>9}{econ:>13}"
                  f"{sub['edge_bps'].max():>11.1f}")
        total_econ = int((found["edge_bps"] > MIN_EDGE_BPS).sum())
        print(f"    {'TOTAL':>16}{len(found):>10}"
              f"{int((found['edge_bps'] > 0).sum()):>9}{total_econ:>13}"
              f"{found['edge_bps'].max():>11.1f}")

        if guard:
            if total_econ == 0:
                print()
                print("    VERDICT: the surface is internally CONSISTENT. No vertical or")
                print("    butterfly can be opened for a credit at executable prices once the")
                print("    legs are required to be simultaneous. Same answer the complete-set")
                print("    and coherence tests gave for Polymarket: these books are priced.")
            else:
                print()
                print(f"    {total_econ} structure(s) survive the guard and clear "
                      f"{MIN_EDGE_BPS:.0f} bps.")
                print("    NOT yet a claim: a single 6-minute snapshot cannot show whether")
                print("    these are persistent or a stale quote about to be pulled. Needs")
                print("    the recorder running to establish lifetime and repeatability.")

    print("\n  PUT-CALL PARITY - measured as a diagnostic, deliberately NOT an arb claim")
    pairs = []
    for (_, _, strike), group in chain.groupby(["batch_id", "expiry_ts_ms", "strike"]):
        calls = group[group["option_type"] == "C"]
        puts = group[group["option_type"] == "P"]
        if calls.empty or puts.empty:
            continue
        c, p = calls.iloc[0], puts.iloc[0]
        if c["underlying_price"] != p["underlying_price"]:
            continue
        residual = (c["mid_usd"] - p["mid_usd"]) - (c["underlying_price"] - strike)
        pairs.append(residual / c["underlying_price"] * 1e4)
    if pairs:
        arr = np.asarray(pairs)
        print(f"    matched call/put pairs : {len(arr)}")
        print(f"    |C - P - (U - K)|      : median {np.median(np.abs(arr)):.1f} bps of spot, "
              f"p90 {np.percentile(np.abs(arr), 90):.1f} bps")
        print("    Converting this into an arbitrage requires trading the FUTURE, whose book")
        print("    is not in this archive. Reported as a surface-quality diagnostic only -")
        print("    residuals of this size are consistent with ordinary discounting and the")
        print("    forward basis, not with a mispricing.")
    else:
        print("    no simultaneous call/put pairs at a shared strike")


# --------------------------------------------------------------------------------------
# TESTS 3-5: blocked, with the exact gap
# --------------------------------------------------------------------------------------
def test_3_regime_transitions() -> None:
    print()
    chain = load_chain()
    span_s = (chain["receive_ts_ns"].max() - chain["receive_ts_ns"].min()) / 1e9
    batches = chain["batch_id"].nunique()
    blocked(
        "TEST 3 - VOLATILITY SKEW AND TERM-STRUCTURE REGIME TRANSITIONS",
        f"the surface archive spans {span_s / 60:.1f} MINUTES across {batches} batches. A "
        f"transition study needs the surface to move between regimes; {span_s / 60:.1f} "
        f"minutes cannot contain a single transition, let alone enough to measure one.",
        "the Deribit recorder running continuously for weeks. It is built and its provenance "
        "is clean; it is simply not scheduled.",
    )

    # A snapshot still establishes the CURRENT shape, which is a legitimate baseline.
    live = chain[chain["expiry_ts_ms"] > chain["exchange_ts_ms"].max()]
    atm = live[live["moneyness"] < 0.02]
    if atm.empty:
        return
    print("\n  What the single snapshot DOES establish - the term structure baseline:")
    print(f"{'expiry':>18}{'days':>7}{'ATM IV %':>11}{'ATM spread bps':>17}{'strikes':>9}")
    now_ms = int(chain["exchange_ts_ms"].max())
    for expiry, group in atm.groupby("expiry_ts_ms"):
        days = (expiry - now_ms) / 86_400_000.0
        stamp = dt.datetime.fromtimestamp(expiry / 1000, dt.timezone.utc)
        print(f"{stamp:%Y-%m-%d %H:%M}{days:>7.1f}"
              f"{group['mark_iv_pct'].median():>11.1f}"
              f"{group['spread_bps_spot'].median():>17.1f}"
              f"{group['strike'].nunique():>9}")
    print("  This is a BASELINE, not a result. It is the reference the first real transition")
    print("  study will be measured against once the recorder has run.")


def test_4_options_to_polymarket() -> None:
    print()
    chain = load_chain()
    con = duckdb.connect(str(PM_DB), read_only=True)
    pm = con.execute("SELECT min(exchange_ts_ms), max(exchange_ts_ms) "
                     "FROM pm_l2_book_summaries").fetchone()
    con.close()
    pm_end = dt.datetime.fromtimestamp(pm[1] / 1000, dt.timezone.utc)
    dr_start = dt.datetime.fromtimestamp(chain["exchange_ts_ms"].min() / 1000, dt.timezone.utc)
    gap = (dr_start - pm_end).total_seconds() / 86_400.0

    blocked(
        "TEST 4 - OPTIONS-SURFACE CHANGES vs LATER POLYMARKET MISPRICING",
        f"the two archives do not overlap by {gap:.0f} days. Polymarket books end "
        f"{pm_end:%Y-%m-%d %H:%M} UTC and the option chain begins {dr_start:%Y-%m-%d %H:%M} "
        f"UTC. A lead-lag study requires the SAME wall-clock minutes on both venues, and "
        f"there is not one shared minute.",
        "both recorders running at once. This lane is the strongest of the five on paper - "
        "the option surface is the deepest, best-informed BTC vol market and Polymarket "
        "binaries are a thin retail venue pricing the same distribution - but it is "
        "unanswerable until the clocks overlap.",
    )
    print("\n  Also required before this is meaningful: Polymarket forward settlement joins")
    print("  on ACTUAL ask, depth and executable VWAP. Mid-price residuals against an option")
    print("  surface would reproduce the taker-cost error that closed the last five lanes.")


def test_5_liquidity_provision() -> None:
    print()
    con = duckdb.connect(str(ROOT / "data" / "multi_venue.duckdb"), read_only=True)
    row = con.execute("SELECT count(*), max(recv_ts) FROM venue_events "
                      "WHERE stream = 'orderbook.1'").fetchone()
    con.close()
    age_h = (dt.datetime.now(dt.timezone.utc).timestamp() - row[1]) / 3600.0

    blocked(
        "TEST 5 - LIQUIDITY-PROVISION ECONOMICS (queue position, adverse selection)",
        f"nothing recorded carries QUEUE POSITION. `orderbook.1` holds {row[0]:,} top-of-book "
        f"observations - a price, not a place in a queue - and its last write was {age_h:.0f} "
        f"hours ago. Fill probability, queue delay and partial fills cannot be derived from "
        f"top-of-book snapshots; they need sequenced L2 with per-order updates.",
        "BINANCE_SEQUENCED_L2_RECORDER_V1 - depth diffs with sequence numbers, replayed into "
        "a book where a simulated resting order holds a position. Then adverse selection is "
        "measurable as the drift AFTER a fill, which is the number that decides this lane.",
    )
    print("\n  This is the last hypothesis five negatives have not closed. Every structure")
    print("  tested so far died paying the spread; nothing yet has tested EARNING it. It is")
    print("  also the most expensive to build, which is why it should be built only after")
    print("  the recorder above has produced weeks of data rather than before.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rows", type=int, default=520_000,
                        help="BTC bars to load for the realized-move distribution")
    args = parser.parse_args()

    if not DERIBIT_DB.exists():
        print(f"missing {DERIBIT_DB} - run the Deribit chain recorder first")
        return 1

    test_1_implied_vs_realized(args.rows)
    test_2_static_no_arbitrage()
    test_3_regime_transitions()
    test_4_options_to_polymarket()
    test_5_liquidity_provision()

    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)
    print("  2 of 5 lanes were answerable from data on disk. 3 print BLOCKED and no number.")
    print("  Every blocked lane is blocked on COLLECTION, not on ideas - which is the finding")
    print("  that matters more than any single test here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
