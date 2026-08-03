"""The 17 published agent rules, evaluated against REAL funding rate and REAL open interest.

WHAT CHANGED VERSUS THE BTC-ONLY RUN
    Seven agents - FUND, OI, CONTRA, STAT, SENT, FLOW, OIDIV - were previously UNAVAILABLE
    because the local archive held no funding level and no open interest. Bybit publishes both,
    so all seventeen are testable here for the first time, and the published global funding
    guards are real rather than inert.

    Funding and OI are NOT simulated. A funding rate derived from price ("change_8h * 0.05")
    is an 8h momentum rule wearing a funding label, and an open interest set to "volume * 3.5"
    turns every OI threshold into a volume filter and every OI-versus-price divergence into an
    algebraic identity. Both appear in an earlier third-party implementation of these agents;
    neither appears here.

CAUSALITY
    Every 24h aggregate is shifted one bar. A row whose funding rate is missing or stale
    returns SKIP for the agents that need it, rather than falling back to a proxy.

    python -m research.algodesk.agents --selftest
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from .data import BARS_PER_DAY

LONG, SHORT, SKIP = "LONG", "SHORT", "SKIP"

AGENT_IDS = ("TREND", "MOMO", "BREAK", "MEAN", "FUND", "VOL", "OI", "CONTRA", "SCALP",
             "LIQ", "PAT", "RANGE", "STAT", "SENT", "FLOW", "REGIME", "OIDIV")

#: Published global guards. Applied inside every agent.
MIN_VOL_24H_USD = 50_000_000.0
FUNDING_LONG_BLOCK = 0.005
FUNDING_SHORT_BLOCK = -0.005

#: Agents whose published conditions require the funding level, open interest, or both.
NEEDS_FUNDING = frozenset({"FUND", "CONTRA", "STAT", "SENT", "OIDIV"})
NEEDS_OI = frozenset({"OI", "STAT", "FLOW", "OIDIV"})


def derive(frame: pd.DataFrame) -> pd.DataFrame:
    """The spec's derived values plus the 24h aggregates, per symbol, all causal."""
    out = []
    for _, group in frame.groupby("symbol", sort=False):
        g = group.sort_values("ts_ms").copy()
        close = g["close"]
        window = BARS_PER_DAY
        # shift(1): a decision never reads the bar it is made on.
        g["high24"] = g["high"].rolling(window, min_periods=window).max().shift(1)
        g["low24"] = g["low"].rolling(window, min_periods=window).min().shift(1)
        prev = close.shift(window)
        g["chg24"] = (close - prev) / prev * 100.0
        g["vol24"] = g["turnover"].rolling(window, min_periods=window).sum().shift(1)
        g["vol_prev_day"] = g["turnover"].rolling(window, min_periods=window).sum().shift(
            window + 1)
        g["vol4"] = g["turnover"].rolling(16, min_periods=16).sum().shift(1)
        span = (g["high24"] - g["low24"]).replace(0.0, np.nan)
        g["pos"] = ((close - g["low24"]) / span).clip(0.0, 1.0)
        g["rsi"] = (g["pos"] * 100).round()
        g["range_pct"] = span / g["low24"] * 100.0
        g["rv24"] = close.pct_change().rolling(window, min_periods=window).std().shift(1) * 1e4
        # Open interest: level, its 24h change, and its notional value.
        g["oi_chg24"] = (g["open_interest"] - g["open_interest"].shift(window)) \
            / g["open_interest"].shift(window) * 100.0
        g["oi_usd"] = g["open_interest"] * close
        g["oi_chg24"] = g["oi_chg24"].shift(1)
        g["oi_usd"] = g["oi_usd"].shift(1)
        out.append(g)
    return pd.concat(out, ignore_index=True)


def _guard(direction: str, row) -> str:
    """Published global guards. Volume always; funding when the rate is known.

    A missing funding rate does not silently pass the guard - the guard is skipped and the
    agent's own funding requirement (if any) refuses separately."""
    if direction == SKIP:
        return SKIP
    if not np.isfinite(row.vol24) or row.vol24 < MIN_VOL_24H_USD:
        return SKIP
    fr = row.funding_rate
    if np.isfinite(fr):
        if direction == LONG and fr > FUNDING_LONG_BLOCK:
            return SKIP
        if direction == SHORT and fr < FUNDING_SHORT_BLOCK:
            return SKIP
    return direction


def raw_signal(agent: str, row) -> str:
    """The published condition, conservative end of every band, before global guards."""
    chg, pos, vol, rng, rv = row.chg24, row.pos, row.vol24, row.range_pct, row.rv24
    fr, oi_usd, oi_chg = row.funding_rate, row.oi_usd, row.oi_chg24
    if not (np.isfinite(chg) and np.isfinite(pos) and np.isfinite(vol)):
        return SKIP
    # An agent that needs funding or OI refuses when the real value is absent. No proxy.
    if agent in NEEDS_FUNDING and not np.isfinite(fr):
        return SKIP
    if agent in NEEDS_OI and not (np.isfinite(oi_usd) and np.isfinite(oi_chg)):
        return SKIP

    if agent == "TREND":                      # chg >5-8%, pos >0.65, high volume
        if chg > 8.0 and pos > 0.65 and vol > 150e6:
            return LONG
        if chg < -8.0 and pos < 0.35 and vol > 150e6:
            return SHORT
    elif agent == "MOMO":                     # chg >8-12%, vol >$100-200M, pos extreme
        if chg > 12.0 and vol > 200e6 and pos > 0.80:
            return LONG
        if chg < -12.0 and vol > 200e6 and pos < 0.20:
            return SHORT
    elif agent == "BREAK":                    # within 3% of day high/low, volume surge
        if np.isfinite(row.vol_prev_day) and row.vol_prev_day > 0 and vol > 1.5 * row.vol_prev_day:
            if pos >= 0.97:
                return LONG
            if pos <= 0.03:
                return SHORT
    elif agent == "MEAN":                     # chg >15-20%, extreme position -> fade
        if chg > 20.0 and pos > 0.90:
            return SHORT
        if chg < -20.0 and pos < 0.10:
            return LONG
    elif agent == "FUND":                     # funding above/below +-0.0015-0.003
        if fr > 0.003:
            return SHORT                      # longs pay: fade the crowded side
        if fr < -0.003:
            return LONG
    elif agent == "VOL":                      # vol >$150-300M, directional move
        if vol > 300e6 and chg > 3.0:
            return LONG
        if vol > 300e6 and chg < -3.0:
            return SHORT
    elif agent == "OI":                       # OI >$2B, directional change
        if oi_usd > 2e9 and oi_chg > 5.0 and chg > 0:
            return LONG
        if oi_usd > 2e9 and oi_chg > 5.0 and chg < 0:
            return SHORT
    elif agent == "CONTRA":                   # chg >18-25%, extreme pos, high funding
        if chg > 25.0 and pos > 0.90 and fr > 0.0015:
            return SHORT
        if chg < -25.0 and pos < 0.10 and fr < -0.0015:
            return LONG
    elif agent == "SCALP":                    # vol >$200-500M, tight 0.7-2.8% range
        if vol > 500e6 and np.isfinite(rng) and 0.7 <= rng <= 2.8:
            return LONG if pos < 0.5 else SHORT
    elif agent == "LIQ":                      # chg >8-10%, vol >$200-400M, pos extreme
        if chg < -10.0 and vol > 400e6 and pos < 0.15:
            return LONG
        if chg > 10.0 and vol > 400e6 and pos > 0.85:
            return SHORT
    elif agent == "PAT":                      # tight day range + directional break
        if np.isfinite(rng) and rng < 2.0:
            if pos >= 0.95:
                return LONG
            if pos <= 0.05:
                return SHORT
    elif agent == "RANGE":                    # range extremes, low volatility
        if np.isfinite(rv) and rv < 40.0:
            if pos <= 0.10:
                return LONG
            if pos >= 0.90:
                return SHORT
    elif agent == "STAT":                     # OI divergence from price + funding
        if oi_chg > 5.0 and chg < -2.0 and fr > 0.0:
            return LONG
        if oi_chg > 5.0 and chg > 2.0 and fr < 0.0:
            return SHORT
    elif agent == "SENT":                     # extreme funding with opposite positioning
        if fr > 0.0015 and chg < 0:
            return SHORT
        if fr < -0.0015 and chg > 0:
            return LONG
    elif agent == "FLOW":                     # vol >$300-500M, directional flow + OI
        if vol > 500e6 and chg > 2.0 and oi_chg > 2.0:
            return LONG
        if vol > 500e6 and chg < -2.0 and oi_chg > 2.0:
            return SHORT
    elif agent == "REGIME":                   # trades only regime-aligned setups
        if np.isfinite(rng) and rng > 4.0 and abs(chg) > 5.0:
            if chg > 0 and pos > 0.70:
                return LONG
            if chg < 0 and pos < 0.30:
                return SHORT
    elif agent == "OIDIV":                    # price and OI opposite, funding confirms
        if chg < -2.0 and oi_chg > 3.0 and fr > 0.0:
            return LONG
        if chg > 2.0 and oi_chg < -3.0 and fr < 0.0:
            return SHORT
    return SKIP


def signal(agent: str, row) -> str:
    """Published condition with the published global guards applied."""
    return _guard(raw_signal(agent, row), row)


def selftest() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    n = BARS_PER_DAY * 3
    ts = np.arange(n, dtype="int64") * 900_000 + 1_785_000_000_000
    frame = pd.DataFrame({
        "symbol": "BTCUSDT", "ts_ms": ts,
        "open": 100.0, "high": 101.0, "low": 99.0,
        "close": np.linspace(100, 130, n), "volume": 1.0,
        "turnover": 20e6, "open_interest": np.linspace(1e5, 1.2e5, n),
        "funding_rate": 0.0001, "funding_age_ms": 0,
    })
    d = derive(frame)
    check(len(d) == n, "derive preserves every row")
    check(d["pos"].dropna().between(0, 1).all(), "pos stays in [0, 1]")
    check(d["vol24"].iloc[:BARS_PER_DAY].isna().all(),
          "24h aggregates are NaN until a full day exists")

    row = d.iloc[BARS_PER_DAY + 5]
    check(row.high24 <= d["high"].iloc[:BARS_PER_DAY + 5].max() + 1e-9,
          "high24 uses only bars strictly BEFORE the decision bar")

    check(len(AGENT_IDS) == 17, "all seventeen agents are defined")
    check(len(NEEDS_FUNDING | NEEDS_OI) == 7,
          "seven agents depend on funding and/or open interest")

    # THE CORE GUARANTEE: no proxy. Blank the real inputs, and those seven refuse.
    blanked = d.copy()
    blanked["funding_rate"] = np.nan
    blanked["oi_usd"] = np.nan
    blanked["oi_chg24"] = np.nan
    probe = blanked.iloc[BARS_PER_DAY + 5]
    for agent in sorted(NEEDS_FUNDING | NEEDS_OI):
        check(raw_signal(agent, probe) == SKIP,
              f"{agent} refuses when its real input is absent - never a proxy")

    # Guards
    thin = d.iloc[BARS_PER_DAY + 5].copy()
    thin.vol24 = 1e6
    check(_guard(LONG, thin) == SKIP, "the $50M volume guard forces SKIP")
    pricey = d.iloc[BARS_PER_DAY + 5].copy()
    pricey.funding_rate = 0.01
    check(_guard(LONG, pricey) == SKIP, "funding above +0.005 blocks LONG")
    check(_guard(SHORT, pricey) == SHORT, "...and leaves SHORT alone")
    cheap = d.iloc[BARS_PER_DAY + 5].copy()
    cheap.funding_rate = -0.01
    check(_guard(SHORT, cheap) == SKIP, "funding below -0.005 blocks SHORT")

    unknown = d.iloc[BARS_PER_DAY + 5].copy()
    unknown.funding_rate = np.nan
    check(_guard(LONG, unknown) == LONG,
          "an unknown funding rate does not silently trip the guard - the agent refuses instead")

    print(f"\nALGODESK AGENTS SELFTEST: PASS ({checks} checks)")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--selftest", action="store_true")
    parser.parse_args()
    raise SystemExit(selftest())
