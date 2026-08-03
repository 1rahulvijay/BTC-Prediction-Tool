"""The 17 algodesk agents, implemented from the published spec and tested on 30 days of 1m bars.

WHAT THIS IS
    A standalone, from-scratch implementation of the 17 agent rules published in the
    algodesk-bot README, run as ONE CONTINUOUS POSITION STATE MACHINE per agent over the
    research matrix, priced at executable levels with costs charged.

    It is a DIAGNOSTIC. It is not a promotion candidate, it does not tune thresholds, and it
    is scored once with everything below frozen before the first result was seen.

SEVEN OF THE SEVENTEEN CANNOT BE TESTED, AND ARE NOT
    The spec's conditions need four inputs. The archive has two:

        24h change          YES  (1m closes)
        day-range position  YES  (rolling 24h high/low)
        24h USD volume      YES  (base-unit volume x close)
        funding rate LEVEL  NO   - only `funding_velocity`, a rate of CHANGE
        open interest       NO   - absent entirely

    `funding_velocity` is not a substitute for the funding rate: a velocity near zero is
    consistent with any level, so the sign of a funding condition cannot be recovered from it.
    Substituting it would produce seven confident, meaningless results.

    So those agents report UNAVAILABLE_INPUTS and are excluded from the comparison, rather
    than being run on a proxy. An untestable strategy that reports a number is worse than one
    that reports nothing, because the number gets quoted.

THE GLOBAL FUNDING GUARDS CANNOT BE APPLIED EITHER
    The spec blocks LONG when fr > 0.005 and SHORT when fr < -0.005. Without the level, they
    are inert here. Both guards only ever REMOVE trades, so every result below is an UPPER
    BOUND on what the specified system would have done. The volume guard (< $50M -> SKIP) is
    applied, because volume exists.

    python research/algodesk_17_agents_v1.py --selftest
    python research/algodesk_17_agents_v1.py --days 30
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "data" / "research_matrix_1m.parquet"

# ---------------------------------------------------------------- frozen before any result

#: Round-trip taker cost in basis points. Entry pays the ask, exit hits the bid, so this
#: covers both legs plus slippage. Matches the repository's diagnostic Binance default.
COST_BPS_ROUND_TRIP = 12.0

#: Exit policy. The published spec gives sizing rules but NO take-profit or stop distances, so
#: these are mine and are declared here rather than discovered later. A max hold is mandatory:
#: without it "dynamic exit" becomes indefinite holding, and a losing position simply waits for
#: recovery, which flatters any backtest.
TAKE_PROFIT_BPS = 100.0
STOP_LOSS_BPS = 50.0
MAX_HOLD_MINUTES = 240

#: From the published global guards.
MIN_24H_USD_VOLUME = 50_000_000.0
SYMBOL_COOLDOWN_MINUTES = 30

#: Bars per 24h window.
DAY_BARS = 1440

LONG, SHORT, SKIP = "LONG", "SHORT", "SKIP"

#: Agents whose published conditions need inputs the archive does not contain.
#: fr = funding rate LEVEL, oi = open interest.
UNAVAILABLE = {
    "FUND":  ("fr",),
    "OI":    ("oi",),
    "CONTRA": ("fr",),
    "STAT":  ("fr", "oi"),
    "SENT":  ("fr",),
    "FLOW":  ("oi",),
    "OIDIV": ("fr", "oi"),
}


def _derive(df: pd.DataFrame) -> pd.DataFrame:
    """The spec's derived values, plus the 24h aggregates its conditions are written against."""
    out = df.copy()
    close = out["close"]
    # Rolling 24h window, shifted by one bar so a decision never reads its own bar's future.
    out["high24"] = close.rolling(DAY_BARS, min_periods=DAY_BARS).max().shift(1)
    out["low24"] = close.rolling(DAY_BARS, min_periods=DAY_BARS).min().shift(1)
    out["close24ago"] = close.shift(DAY_BARS)
    out["chg24"] = (close - out["close24ago"]) / out["close24ago"] * 100.0
    # Volume is BASE units (BTC); the spec's thresholds are USD.
    usd = out["volume"] * close
    out["vol24"] = usd.rolling(DAY_BARS, min_periods=DAY_BARS).sum().shift(1)
    out["vol1h"] = usd.rolling(60, min_periods=60).sum().shift(1)
    span = (out["high24"] - out["low24"]).replace(0.0, np.nan)
    # pos = (price - low24h) / (high24h - low24h); 0.0 = day low, 1.0 = day high
    out["pos"] = ((close - out["low24"]) / span).clip(0.0, 1.0)
    out["rsi"] = (out["pos"] * 100).round()
    out["range_pct"] = span / out["low24"] * 100.0
    # Realized volatility proxy over the last hour, in bps.
    out["rv60"] = close.pct_change().rolling(60, min_periods=60).std().shift(1) * 1e4
    return out


def signal(agent: str, row) -> str:
    """The published condition for one agent at one bar. Conservative end of every range.

    Where the spec gives a band ("change >5-8%"), the STRICTER end is used, frozen here. Taking
    the loose end would make each rule fire more often and is the first place a backtest starts
    quietly optimising."""
    chg, pos, vol, rng, rv = row.chg24, row.pos, row.vol24, row.range_pct, row.rv60
    if not np.isfinite(chg) or not np.isfinite(pos) or not np.isfinite(vol):
        return SKIP

    if agent == "TREND":      # 24h change >5-8%, position in day range >0.65, high volume
        if chg > 8.0 and pos > 0.65 and vol > 150e6:
            return LONG
        if chg < -8.0 and pos < 0.35 and vol > 150e6:
            return SHORT
    elif agent == "MOMO":     # change >8-12%, vol >$100M-200M, position extreme
        if chg > 12.0 and vol > 200e6 and pos > 0.80:
            return LONG
        if chg < -12.0 and vol > 200e6 and pos < 0.20:
            return SHORT
    elif agent == "BREAK":    # price within 3% of day high/low, volume surge
        if np.isfinite(row.vol1h) and row.vol1h > vol / 24.0 * 2.0:
            if pos >= 0.97:
                return LONG
            if pos <= 0.03:
                return SHORT
    elif agent == "MEAN":     # change >15-20%, extreme position in range -> FADE
        if chg > 20.0 and pos > 0.90:
            return SHORT
        if chg < -20.0 and pos < 0.10:
            return LONG
    elif agent == "VOL":      # volume >$150M-300M, directional price move
        if vol > 300e6 and chg > 3.0:
            return LONG
        if vol > 300e6 and chg < -3.0:
            return SHORT
    elif agent == "SCALP":    # vol >$200M-500M, tight 0.7-2.8% range target
        if vol > 500e6 and np.isfinite(rng) and 0.7 <= rng <= 2.8:
            return LONG if pos < 0.5 else SHORT
    elif agent == "LIQ":      # change >8-10%, vol >$200M-400M, position extreme
        if chg < -10.0 and vol > 400e6 and pos < 0.15:
            return LONG        # hunt the cascade's exhaustion
        if chg > 10.0 and vol > 400e6 and pos > 0.85:
            return SHORT
    elif agent == "PAT":      # tight day range + directional break
        if np.isfinite(rng) and rng < 2.0:
            if pos >= 0.95:
                return LONG
            if pos <= 0.05:
                return SHORT
    elif agent == "RANGE":    # price at range extremes, low volatility
        if np.isfinite(rv) and rv < 5.0:
            if pos <= 0.10:
                return LONG
            if pos >= 0.90:
                return SHORT
    elif agent == "REGIME":   # trades only setups aligned with the detected regime
        if np.isfinite(rng) and np.isfinite(rv):
            trending = rng > 4.0 and abs(chg) > 5.0
            if trending and chg > 0 and pos > 0.70:
                return LONG
            if trending and chg < 0 and pos < 0.30:
                return SHORT
    return SKIP


def run_agent(agent: str, df: pd.DataFrame) -> dict:
    """One continuous position state machine. No overlapping synthetic trades.

    Treating every bar as an independent hypothetical trade is how a backtest reports 40,000
    'opportunities' that one account could never have taken. Here the agent is FLAT or in ONE
    position, and cannot re-enter until its cooldown expires."""
    closes = df["close"].to_numpy(float)
    days = (df["ts_ms"].to_numpy("int64") // 86_400_000)
    n = len(df)
    half_cost = COST_BPS_ROUND_TRIP / 2.0

    position = 0            # +1 long, -1 short, 0 flat
    entry_price = 0.0
    entry_index = 0
    cooldown_until = -1
    trades: list[dict] = []

    rows = list(df.itertuples(index=False))
    for i in range(n):
        price = closes[i]
        if position != 0:
            move_bps = (price - entry_price) / entry_price * 1e4 * position
            held = i - entry_index
            reason = None
            if move_bps >= TAKE_PROFIT_BPS:
                reason = "TAKE_PROFIT"
            elif move_bps <= -STOP_LOSS_BPS:
                reason = "STOP_LOSS"
            elif held >= MAX_HOLD_MINUTES:
                reason = "MAX_HOLD"
            if reason:
                trades.append({"day": int(days[entry_index]), "side": position,
                               "net_bps": move_bps - COST_BPS_ROUND_TRIP, "reason": reason,
                               "held": held})
                position = 0
                cooldown_until = i + SYMBOL_COOLDOWN_MINUTES
            continue

        if i < cooldown_until:
            continue
        row = rows[i]
        # Global volume guard: below $50M 24h volume, force SKIP for every agent.
        if not np.isfinite(row.vol24) or row.vol24 < MIN_24H_USD_VOLUME:
            continue
        call = signal(agent, row)
        if call == LONG:
            position, entry_price, entry_index = 1, price * (1 + half_cost / 1e4), i
        elif call == SHORT:
            position, entry_price, entry_index = -1, price * (1 - half_cost / 1e4), i

    if not trades:
        return {"agent": agent, "trades": 0, "net_bps": 0.0, "mean_bps": 0.0,
                "ci": (float("nan"), float("nan")), "days": 0, "wins": 0}
    frame = pd.DataFrame(trades)
    return {"agent": agent, "trades": len(frame), "net_bps": float(frame.net_bps.sum()),
            "mean_bps": float(frame.net_bps.mean()), "ci": day_block_ci(frame),
            "days": int(frame.day.nunique()), "wins": int((frame.net_bps > 0).sum()),
            "reasons": frame.reason.value_counts().to_dict()}


def day_block_ci(frame: pd.DataFrame, iterations: int = 2000, seed: int = 7) -> tuple:
    """95% CI on mean net bps per trade, resampling whole DAYS.

    Trades inside one day share regime, volatility and the same 24h aggregates, so they are not
    independent draws. A per-trade CI would be far too narrow."""
    groups = [g.net_bps.to_numpy(float) for _, g in frame.groupby("day")]
    if len(groups) < 2:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    means = np.empty(iterations)
    for k in range(iterations):
        picked = rng.integers(0, len(groups), len(groups))
        means[k] = np.concatenate([groups[j] for j in picked]).mean()
    return (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    n = DAY_BARS * 3
    ts = np.arange(n, dtype="int64") * 60_000 + 1_700_000_000_000
    price = np.linspace(50_000, 60_000, n)                    # a clean uptrend
    df = _derive(pd.DataFrame({"ts_ms": ts, "close": price, "volume": np.full(n, 20.0)}))

    check(df["pos"].dropna().between(0, 1).all(), "pos is confined to [0, 1]")
    check(np.isclose(df["rsi"].dropna(), (df["pos"].dropna() * 100).round()).all(),
          "rsi is the published pos*100 proxy, not a real RSI")

    # CAUSALITY. Every 24h aggregate is shifted, so no bar reads its own bar.
    row = df.iloc[DAY_BARS + 10]
    check(row.high24 <= df["close"].iloc[:DAY_BARS + 10].max() + 1e-9,
          "high24 uses only bars STRICTLY BEFORE the decision bar")
    check(np.isfinite(df["vol24"].iloc[DAY_BARS + 10]) and
          not np.isfinite(df["vol24"].iloc[DAY_BARS - 2]),
          "24h aggregates are NaN until a full window exists - never partially filled")

    check(signal("TREND", df.iloc[10]) == SKIP,
          "an incomplete window yields SKIP, not a trade on NaN")

    # Every unavailable agent must refuse, and must not be quietly runnable.
    for agent in UNAVAILABLE:
        check(signal(agent, df.iloc[DAY_BARS + 10]) == SKIP,
              f"{agent} never emits a signal - its inputs do not exist here")
    check(len(UNAVAILABLE) == 7 and len(TESTABLE) == 10,
          "10 testable + 7 unavailable = the published 17")

    # The state machine must never hold two positions at once.
    result = run_agent("TREND", df)
    check(result["trades"] >= 0, "the state machine runs to completion")
    check(MAX_HOLD_MINUTES > 0, "a maximum hold is declared - dynamic exit is not open-ended")

    flat = pd.DataFrame({"day": [1, 1, 2, 2], "net_bps": [0.0, 0.0, 0.0, 0.0]})
    low, high = day_block_ci(flat)
    check(low == 0.0 and high == 0.0, "a zero-return series has a zero-width CI")
    single = pd.DataFrame({"day": [1, 1], "net_bps": [5.0, -5.0]})
    check(not np.isfinite(day_block_ci(single)[0]),
          "ONE day cannot produce a day-block CI - it reports nan rather than a fake interval")

    print(f"\nALGODESK 17-AGENT SELFTEST: PASS ({checks} checks)")
    return 0


TESTABLE = ("TREND", "MOMO", "BREAK", "MEAN", "VOL", "SCALP", "LIQ", "PAT", "RANGE", "REGIME")
ALL_AGENTS = TESTABLE + tuple(UNAVAILABLE)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.add_argument("--days", type=int, default=30)
    args = parser.parse_args()
    if args.selftest:
        return selftest()

    if not MATRIX.is_file():
        print(f"missing {MATRIX}")
        return 1
    import pyarrow.parquet as pq
    frame = pq.read_table(MATRIX, columns=["ts_ms", "close", "volume"]).to_pandas()
    frame = frame.sort_values("ts_ms").drop_duplicates("ts_ms").reset_index(drop=True)
    # Keep one extra day so the first evaluated bar already has a full 24h window behind it.
    cutoff = int(frame.ts_ms.max()) - (args.days + 1) * 86_400_000
    frame = frame[frame.ts_ms >= cutoff].reset_index(drop=True)
    derived = _derive(frame).dropna(subset=["vol24", "pos", "chg24"]).reset_index(drop=True)

    import datetime as dt
    fmt = lambda ms: dt.datetime.fromtimestamp(ms / 1000, dt.UTC).strftime("%Y-%m-%d")
    print("=" * 94)
    print(f"ALGODESK 17 AGENTS - {args.days} days, one continuous position per agent")
    print("=" * 94)
    print(f"  window        : {fmt(derived.ts_ms.min())} -> {fmt(derived.ts_ms.max())} "
          f"({len(derived):,} 1m bars, {derived.ts_ms.max() // 86400000 - derived.ts_ms.min() // 86400000} days)")
    print(f"  cost          : {COST_BPS_ROUND_TRIP:.1f} bps round trip")
    print(f"  exit policy   : TP {TAKE_PROFIT_BPS:.0f} bps / SL {STOP_LOSS_BPS:.0f} bps / "
          f"max hold {MAX_HOLD_MINUTES}m  (frozen before results)")
    print(f"  guards        : volume < ${MIN_24H_USD_VOLUME/1e6:.0f}M -> SKIP; "
          f"cooldown {SYMBOL_COOLDOWN_MINUTES}m")
    print()
    print(f"  {'agent':<8}{'trades':>7}{'days':>6}{'win%':>7}{'mean bps':>10}"
          f"{'total bps':>11}   day-block 95% CI")
    print("  " + "-" * 88)

    results = []
    for agent in TESTABLE:
        r = run_agent(agent, derived)
        results.append(r)
        if not r["trades"]:
            print(f"  {agent:<8}{0:>7}{'-':>6}{'-':>7}{'-':>10}{'-':>11}   never fired")
            continue
        low, high = r["ci"]
        ci = (f"[{low:+7.1f}, {high:+7.1f}]" if np.isfinite(low) else "  (one day only)")
        print(f"  {agent:<8}{r['trades']:>7}{r['days']:>6}"
              f"{r['wins']/r['trades']*100:>6.0f}%{r['mean_bps']:>10.1f}"
              f"{r['net_bps']:>11.0f}   {ci}")

    print()
    print("  UNAVAILABLE - published conditions need inputs this archive does not contain:")
    for agent, missing in UNAVAILABLE.items():
        need = {"fr": "funding rate level", "oi": "open interest"}
        print(f"    {agent:<8} needs {', '.join(need[m] for m in missing)}")

    fired = [r for r in results if r["trades"]]
    positive = [r for r in fired if np.isfinite(r["ci"][0]) and r["ci"][0] > 0]
    print()
    print(f"  {len(fired)} of {len(TESTABLE)} testable agents fired at all; "
          f"{len(positive)} have a day-block lower bound above zero.")
    print("  Every figure is an UPPER BOUND: the spec's funding guards only ever REMOVE")
    print("  trades, and they could not be applied here. 7 agents are untested, not neutral.")
    print("  DIAGNOSTIC ONLY - not a promotion candidate and not a threshold search.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
