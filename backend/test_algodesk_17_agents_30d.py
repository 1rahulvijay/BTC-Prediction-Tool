"""
test_algodesk_17_agents_30d.py -- Standalone backtest of all 17 AlgoDesk trading agents
=======================================================================================

SUPERSEDED 2026-08-04 by research/algodesk_17_agents_v1.py and the research/algodesk/ package,
which add day-block confidence intervals, post-cost evaluation and real Bybit funding/OI. This
file is NOT a unit test despite the name, and is not run by CI. Kept for reference; prefer the
research versions for any number you intend to cite.

Tests every trading strategy described on https://algodesk-bot.pages.dev/landing
and the AlgoDesk GitHub README (algodesk-bot/algodesk-bot) over 30 days of live
Bybit 5-minute kline data.

The 17 agents:
  TREND, MOMO, BREAK, MEAN, FUND, VOL, OI, CONTRA, SCALP,
  LIQ, PAT, RANGE, STAT, SENT, FLOW, REGIME, OIDIV

Global Guards (applied inside every sig()):
  - vol < $50M 24h  -> force SKIP
  - fr >  0.005     -> block LONG
  - fr < -0.005     -> block SHORT

Derived Values:
  pos = (price - low24h) / (high24h - low24h)   # 0.0 = day low, 1.0 = day high
  rsi = round(pos * 100)                         # simplified RSI proxy (0-100)

Usage:
  python backend/test_algodesk_17_agents_30d.py [--test-days 30] [--pairs BTCUSDT,ETHUSDT]
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# Optional imports -- the script must work without heavyweight ML libraries
# ---------------------------------------------------------------------------
try:
    import requests
except ImportError:
    print("ERROR: requests is required.  pip install requests"); sys.exit(1)

# ===========================================================================
#  Constants
# ===========================================================================
BYBIT_BASE = "https://api.bybit.com"
DEFAULT_PAIRS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT",
                 "XRPUSDT", "AVAXUSDT", "LINKUSDT"]
LEV = 5
STOP_PCT = 0.015       # 1.5% stop-loss
TGT_PCT = 0.03         # 3.0% take-profit
STARTING_EQUITY = 10_000.0
KLINE_INTERVAL = "5"   # 5-minute candles
CANDLES_PER_DAY = 288  # 24h * 60 / 5
MAX_KLINE_LIMIT = 1000 # Bybit v5 max per request

# Global guard thresholds
MIN_VOL_24H = 50_000_000        # $50M minimum 24h volume
FUNDING_LONG_BLOCK = 0.005      # Block LONG when funding > 0.5%
FUNDING_SHORT_BLOCK = -0.005    # Block SHORT when funding < -0.5%

# Agent IDs matching the AlgoDesk README
AGENT_IDS = [
    "TREND", "MOMO", "BREAK", "MEAN", "FUND", "VOL", "OI",
    "CONTRA", "SCALP", "LIQ", "PAT", "RANGE", "STAT", "SENT",
    "FLOW", "REGIME", "OIDIV",
]


# ===========================================================================
#  Data structures
# ===========================================================================
@dataclass
class Candle:
    """A single OHLCV candle with optional market context."""
    ts: int             # timestamp ms
    open: float
    high: float
    low: float
    close: float
    volume: float       # quote volume (USDT)
    # rolling 24h context (computed later)
    high_24h: float = 0.0
    low_24h: float = 0.0
    vol_24h: float = 0.0
    change_24h_pct: float = 0.0
    # simulated funding rate (8h basis)
    funding_rate: float = 0.0
    # simulated open interest
    open_interest: float = 0.0
    # derived values (AlgoDesk spec)
    pos: float = 0.5        # position in day range [0,1]
    rsi: int = 50            # simplified RSI proxy


@dataclass
class Signal:
    """A trading signal from an agent."""
    agent: str
    direction: str      # "LONG", "SHORT", or "SKIP"
    symbol: str
    ts: int
    entry_price: float
    stop_price: float
    target_price: float
    candle_idx: int


@dataclass
class Trade:
    """A completed trade."""
    agent: str
    symbol: str
    direction: str
    entry_price: float
    exit_price: float
    stop_price: float
    target_price: float
    pnl_pct: float
    pnl_usd: float
    is_win: bool
    entry_ts: int
    exit_ts: int
    duration_candles: int


@dataclass
class AgentStats:
    """Aggregated stats for one agent."""
    agent: str
    total_signals: int = 0
    wins: int = 0
    losses: int = 0
    skips: int = 0
    gross_pnl_usd: float = 0.0
    max_drawdown_pct: float = 0.0
    trades: list = field(default_factory=list)

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total * 100 if total > 0 else 0.0

    @property
    def avg_pnl_per_trade(self) -> float:
        total = self.wins + self.losses
        return self.gross_pnl_usd / total if total > 0 else 0.0

    @property
    def sharpe(self) -> float:
        if not self.trades:
            return 0.0
        pnls = [t.pnl_pct for t in self.trades]
        mean = sum(pnls) / len(pnls)
        if len(pnls) < 2:
            return 0.0
        var = sum((p - mean) ** 2 for p in pnls) / (len(pnls) - 1)
        std = var ** 0.5
        return mean / std * (252 ** 0.5) if std > 0 else 0.0


# ===========================================================================
#  Bybit data fetcher
# ===========================================================================
def fetch_klines(symbol: str, interval: str, days: int) -> list[Candle]:
    """Fetch 5-minute klines from Bybit v5 public API."""
    total_candles = days * CANDLES_PER_DAY
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - (days * 86_400_000)

    all_candles: list[Candle] = []
    cursor_end = now_ms

    print(f"  Fetching {symbol} ({total_candles} candles, {days}d)...", end="", flush=True)
    retries = 0
    while len(all_candles) < total_candles and retries < 50:
        limit = min(MAX_KLINE_LIMIT, total_candles - len(all_candles))
        url = (f"{BYBIT_BASE}/v5/market/kline"
               f"?category=linear&symbol={symbol}&interval={interval}"
               f"&limit={limit}&end={cursor_end}")
        try:
            resp = requests.get(url, timeout=15)
            data = resp.json()
        except Exception as e:
            print(f"\n  WARNING: Request failed for {symbol}: {e}")
            retries += 1
            time.sleep(1)
            continue

        rows = data.get("result", {}).get("list", [])
        if not rows:
            break

        for row in rows:
            ts = int(row[0])
            if ts < start_ms:
                continue
            all_candles.append(Candle(
                ts=ts,
                open=float(row[1]),
                high=float(row[2]),
                low=float(row[3]),
                close=float(row[4]),
                volume=float(row[5]),
            ))

        # Move cursor backward (Bybit returns newest first)
        oldest_ts = int(rows[-1][0])
        if oldest_ts <= start_ms:
            break
        cursor_end = oldest_ts - 1
        retries += 1
        time.sleep(0.12)  # Rate limit courtesy

    # Sort by timestamp ascending
    all_candles.sort(key=lambda c: c.ts)
    # Deduplicate
    seen = set()
    deduped = []
    for c in all_candles:
        if c.ts not in seen:
            seen.add(c.ts)
            deduped.append(c)
    print(f" {len(deduped)} candles")
    return deduped


def compute_market_context(candles: list[Candle]) -> None:
    """
    Compute rolling 24h context, simulated funding rate, OI,
    and AlgoDesk derived values (pos, rsi) for each candle.
    """
    window = CANDLES_PER_DAY  # 288 candles = 24h at 5m

    for i, c in enumerate(candles):
        start = max(0, i - window)
        window_candles = candles[start:i + 1]

        # 24h high/low/volume
        c.high_24h = max(wc.high for wc in window_candles)
        c.low_24h = min(wc.low for wc in window_candles)
        c.vol_24h = sum(wc.volume for wc in window_candles)

        # 24h price change %
        if i >= window:
            prev_close = candles[i - window].close
            c.change_24h_pct = (c.close - prev_close) / prev_close * 100
        else:
            c.change_24h_pct = 0.0

        # Position in day range (AlgoDesk derived)
        range_24h = c.high_24h - c.low_24h
        c.pos = (c.close - c.low_24h) / range_24h if range_24h > 0 else 0.5
        c.rsi = round(c.pos * 100)

        # Simulated funding rate:
        # Based on 8h price change bias -- positive change -> positive funding
        if i >= 96:  # 8h = 96 candles at 5m
            change_8h = (c.close - candles[i - 96].close) / candles[i - 96].close
            c.funding_rate = change_8h * 0.05  # Scale down to realistic funding levels
        else:
            c.funding_rate = 0.0

        # Simulated open interest:
        # Proportional to volume with some persistence
        c.open_interest = c.vol_24h * 3.5  # Rough OI proxy


# ===========================================================================
#  Global Guards (applied inside every agent's sig())
# ===========================================================================
def apply_global_guards(direction: str, candle: Candle) -> str:
    """
    AlgoDesk Global Guards -- applied inside every sig() call.
    Returns the (possibly overridden) direction.
    """
    # Guard 1: Minimum volume
    if candle.vol_24h < MIN_VOL_24H:
        return "SKIP"

    # Guard 2: Funding rate blocks
    if direction == "LONG" and candle.funding_rate > FUNDING_LONG_BLOCK:
        return "SKIP"  # Funding too expensive to be long
    if direction == "SHORT" and candle.funding_rate < FUNDING_SHORT_BLOCK:
        return "SKIP"  # Funding too expensive to be short

    return direction


# ===========================================================================
#  The 17 Trading Agents -- sig() implementations
# ===========================================================================

def sig_trend(c: Candle) -> str:
    """
    TREND -- Trend Follower
    Rides strong directional moves.
    Key: 24h change >5-8%, position in day range >0.65, high volume.
    """
    abs_chg = abs(c.change_24h_pct)
    if abs_chg < 5.0:
        return "SKIP"
    if c.vol_24h < 100_000_000:  # Needs high volume
        return "SKIP"

    if c.change_24h_pct > 5.0 and c.pos > 0.65:
        return apply_global_guards("LONG", c)
    elif c.change_24h_pct < -5.0 and c.pos < 0.35:
        return apply_global_guards("SHORT", c)
    return "SKIP"


def sig_momo(c: Candle) -> str:
    """
    MOMO -- Momentum
    Catches price acceleration.
    Key: Change >8-12%, vol >$100M-200M, position extreme.
    """
    abs_chg = abs(c.change_24h_pct)
    if abs_chg < 8.0:
        return "SKIP"
    if c.vol_24h < 100_000_000:
        return "SKIP"

    if c.change_24h_pct > 8.0 and c.pos > 0.75:
        return apply_global_guards("LONG", c)
    elif c.change_24h_pct < -8.0 and c.pos < 0.25:
        return apply_global_guards("SHORT", c)
    return "SKIP"


def sig_break(c: Candle) -> str:
    """
    BREAK -- Breakout
    Enters on range breaks.
    Key: Price within 3% of day high/low, volume surge.
    """
    if c.vol_24h < 80_000_000:
        return "SKIP"

    range_24h = c.high_24h - c.low_24h
    if range_24h <= 0:
        return "SKIP"

    # Within 3% of day high
    dist_high = (c.high_24h - c.close) / c.high_24h * 100
    dist_low = (c.close - c.low_24h) / c.low_24h * 100

    if dist_high < 3.0 and c.pos > 0.90:
        return apply_global_guards("LONG", c)
    elif dist_low < 3.0 and c.pos < 0.10:
        return apply_global_guards("SHORT", c)
    return "SKIP"


def sig_mean(c: Candle) -> str:
    """
    MEAN -- Mean Reversion
    Fades overextended moves.
    Key: Change >15-20%, extreme position in range.
    CONTRARIAN: buys dips, sells rips.
    """
    abs_chg = abs(c.change_24h_pct)
    if abs_chg < 15.0:
        return "SKIP"

    # Fade the move -- buy oversold, sell overbought
    if c.change_24h_pct < -15.0 and c.pos < 0.15:
        return apply_global_guards("LONG", c)  # Buy the extreme dip
    elif c.change_24h_pct > 15.0 and c.pos > 0.85:
        return apply_global_guards("SHORT", c)  # Sell the extreme rip
    return "SKIP"


def sig_fund(c: Candle) -> str:
    """
    FUND -- Funding Arb
    Exploits funding rate extremes.
    Key: Funding rate above/below +/-0.0015-0.003.
    Goes opposite to funding (collect the premium).
    """
    fr = c.funding_rate
    if abs(fr) < 0.0015:
        return "SKIP"

    # Collect funding: short when funding is high (+), long when low (-)
    if fr > 0.003:
        return apply_global_guards("SHORT", c)  # Collect positive funding
    elif fr < -0.003:
        return apply_global_guards("LONG", c)   # Collect negative funding
    elif fr > 0.0015:
        return apply_global_guards("SHORT", c)
    elif fr < -0.0015:
        return apply_global_guards("LONG", c)
    return "SKIP"


def sig_vol(c: Candle) -> str:
    """
    VOL -- Volatility
    Trades volatility expansion.
    Key: Volume >$150M-300M, directional price move.
    """
    if c.vol_24h < 150_000_000:
        return "SKIP"

    abs_chg = abs(c.change_24h_pct)
    if abs_chg < 3.0:  # Needs some directional move
        return "SKIP"

    if c.change_24h_pct > 3.0:
        return apply_global_guards("LONG", c)
    elif c.change_24h_pct < -3.0:
        return apply_global_guards("SHORT", c)
    return "SKIP"


def sig_oi(c: Candle) -> str:
    """
    OI -- Open Interest
    OI momentum confirmation.
    Key: OI >$2B, directional change.
    """
    if c.open_interest < 2_000_000_000:
        return "SKIP"

    abs_chg = abs(c.change_24h_pct)
    if abs_chg < 2.0:
        return "SKIP"

    if c.change_24h_pct > 2.0:
        return apply_global_guards("LONG", c)
    elif c.change_24h_pct < -2.0:
        return apply_global_guards("SHORT", c)
    return "SKIP"


def sig_contra(c: Candle) -> str:
    """
    CONTRA -- Contrarian
    Fades crowded positioning.
    Key: Change >18-25%, extreme position, high funding.
    """
    abs_chg = abs(c.change_24h_pct)
    if abs_chg < 18.0:
        return "SKIP"
    if abs(c.funding_rate) < 0.001:
        return "SKIP"

    # Fade extreme moves when funding confirms crowding
    if c.change_24h_pct > 18.0 and c.funding_rate > 0.001 and c.pos > 0.85:
        return apply_global_guards("SHORT", c)  # Fade the crowd
    elif c.change_24h_pct < -18.0 and c.funding_rate < -0.001 and c.pos < 0.15:
        return apply_global_guards("LONG", c)   # Fade the panic
    return "SKIP"


def sig_scalp(c: Candle) -> str:
    """
    SCALP -- Scalper
    High-frequency micro moves.
    Key: Vol >$200M-500M, tight 0.7-2.8% range target.
    """
    if c.vol_24h < 200_000_000:
        return "SKIP"

    # Tight range micro-moves: RSI divergence from neutral
    if c.rsi > 60 and c.rsi < 80:
        return apply_global_guards("LONG", c)
    elif c.rsi < 40 and c.rsi > 20:
        return apply_global_guards("SHORT", c)
    return "SKIP"


def sig_liq(c: Candle) -> str:
    """
    LIQ -- Liquidation
    Hunts liquidation cascades.
    Key: Change >8-10%, vol >$200M-400M, position extreme.
    """
    abs_chg = abs(c.change_24h_pct)
    if abs_chg < 8.0:
        return "SKIP"
    if c.vol_24h < 200_000_000:
        return "SKIP"

    # Ride the liquidation cascade
    if c.change_24h_pct > 8.0 and c.pos > 0.85:
        return apply_global_guards("LONG", c)  # Shorts getting liquidated
    elif c.change_24h_pct < -8.0 and c.pos < 0.15:
        return apply_global_guards("SHORT", c)  # Longs getting liquidated
    return "SKIP"


def sig_pat(c: Candle) -> str:
    """
    PAT -- Pattern
    Range squeeze + flags.
    Key: Tight day range + directional break.
    """
    range_pct = (c.high_24h - c.low_24h) / c.low_24h * 100 if c.low_24h > 0 else 0
    if range_pct > 5.0:  # Not a tight range
        return "SKIP"
    if range_pct < 0.5:  # Too tight to be meaningful
        return "SKIP"

    # Directional break from tight range
    abs_chg = abs(c.change_24h_pct)
    if abs_chg < 1.5:
        return "SKIP"

    if c.change_24h_pct > 1.5 and c.pos > 0.70:
        return apply_global_guards("LONG", c)
    elif c.change_24h_pct < -1.5 and c.pos < 0.30:
        return apply_global_guards("SHORT", c)
    return "SKIP"


def sig_range(c: Candle) -> str:
    """
    RANGE -- Range Trader
    Buys support, sells resistance.
    Key: Price at range extremes, low volatility.
    """
    range_pct = (c.high_24h - c.low_24h) / c.low_24h * 100 if c.low_24h > 0 else 0
    if range_pct > 8.0:  # Too volatile for range trading
        return "SKIP"

    abs_chg = abs(c.change_24h_pct)
    if abs_chg > 5.0:  # Market trending, not ranging
        return "SKIP"

    # Buy near support (low of range), sell near resistance (high of range)
    if c.pos < 0.15:
        return apply_global_guards("LONG", c)
    elif c.pos > 0.85:
        return apply_global_guards("SHORT", c)
    return "SKIP"


def sig_stat(c: Candle) -> str:
    """
    STAT -- Statistical Arb
    Statistical edge detection.
    Key: OI divergence from price + funding rate.
    """
    # OI and price moving in different implied directions
    oi_signal = c.open_interest > 1_500_000_000
    if not oi_signal:
        return "SKIP"

    # When funding and price disagree -- statistical edge
    if c.change_24h_pct > 3.0 and c.funding_rate < 0:
        return apply_global_guards("LONG", c)   # Price up, funding negative = hidden demand
    elif c.change_24h_pct < -3.0 and c.funding_rate > 0:
        return apply_global_guards("SHORT", c)  # Price down, funding positive = hidden selling
    return "SKIP"


def sig_sent(c: Candle) -> str:
    """
    SENT -- Sentiment
    Funding + momentum divergence.
    Key: Extreme funding with opposite positioning.
    """
    abs_fr = abs(c.funding_rate)
    if abs_fr < 0.002:
        return "SKIP"

    # Extreme funding but price moving opposite = sentiment divergence
    if c.funding_rate > 0.002 and c.change_24h_pct < -2.0:
        # Everyone long (high funding) but price dropping -- fade longs
        return apply_global_guards("SHORT", c)
    elif c.funding_rate < -0.002 and c.change_24h_pct > 2.0:
        # Everyone short (negative funding) but price rising -- fade shorts
        return apply_global_guards("LONG", c)
    return "SKIP"


def sig_flow(c: Candle) -> str:
    """
    FLOW -- Order Flow
    Institutional order flow bias.
    Key: Vol >$300M-500M, strong directional flow with OI confirmation.
    """
    if c.vol_24h < 300_000_000:
        return "SKIP"

    abs_chg = abs(c.change_24h_pct)
    if abs_chg < 2.0:
        return "SKIP"

    # High volume + directional move + OI expanding = institutional flow
    if c.open_interest > 1_000_000_000:
        if c.change_24h_pct > 2.0:
            return apply_global_guards("LONG", c)
        elif c.change_24h_pct < -2.0:
            return apply_global_guards("SHORT", c)
    return "SKIP"


def sig_regime(c: Candle) -> str:
    """
    REGIME -- Regime Detector
    Adapts to market regime.
    Key: Identifies trending/ranging/volatile regime; trades only aligned setups.
    """
    abs_chg = abs(c.change_24h_pct)
    range_pct = (c.high_24h - c.low_24h) / c.low_24h * 100 if c.low_24h > 0 else 0

    # Classify regime
    is_trending = abs_chg > 4.0 and range_pct > 3.0

    if is_trending:
        # In trending regime, only trade aligned direction
        if c.change_24h_pct > 4.0:
            return apply_global_guards("LONG", c)
        else:
            return apply_global_guards("SHORT", c)
    return "SKIP"  # Only trades in trending regime


def sig_oidiv(c: Candle) -> str:
    """
    OIDIV -- OI Divergence
    Price and OI moving in opposite directions, funding rate confirms.
    """
    if c.open_interest < 1_000_000_000:
        return "SKIP"

    # Price up but OI declining (using volume as proxy) -- bearish divergence
    # Price down but OI rising -- bullish divergence
    # We use funding as a proxy for OI direction relative to price
    if c.change_24h_pct > 3.0 and c.funding_rate < -0.001:
        # Price up, but funding negative suggests OI divergence (shorts adding)
        return apply_global_guards("SHORT", c)  # Bearish divergence
    elif c.change_24h_pct < -3.0 and c.funding_rate > 0.001:
        # Price down, but funding positive suggests longs are accumulating
        return apply_global_guards("LONG", c)   # Bullish divergence
    return "SKIP"


# ===========================================================================
#  Agent registry
# ===========================================================================
AGENT_FUNCS: dict[str, Any] = {
    "TREND": sig_trend,
    "MOMO": sig_momo,
    "BREAK": sig_break,
    "MEAN": sig_mean,
    "FUND": sig_fund,
    "VOL": sig_vol,
    "OI": sig_oi,
    "CONTRA": sig_contra,
    "SCALP": sig_scalp,
    "LIQ": sig_liq,
    "PAT": sig_pat,
    "RANGE": sig_range,
    "STAT": sig_stat,
    "SENT": sig_sent,
    "FLOW": sig_flow,
    "REGIME": sig_regime,
    "OIDIV": sig_oidiv,
}

AGENT_NAMES: dict[str, str] = {
    "TREND": "Trend Follower",
    "MOMO": "Momentum",
    "BREAK": "Breakout",
    "MEAN": "Mean Reversion",
    "FUND": "Funding Arb",
    "VOL": "Volatility",
    "OI": "Open Interest",
    "CONTRA": "Contrarian",
    "SCALP": "Scalper",
    "LIQ": "Liquidation",
    "PAT": "Pattern",
    "RANGE": "Range Trader",
    "STAT": "Statistical Arb",
    "SENT": "Sentiment",
    "FLOW": "Order Flow",
    "REGIME": "Regime Detector",
    "OIDIV": "OI Divergence",
}

AGENT_STRATEGIES: dict[str, str] = {
    "TREND": "Rides strong directional moves",
    "MOMO": "Catches price acceleration",
    "BREAK": "Enters on range breaks",
    "MEAN": "Fades overextended moves",
    "FUND": "Exploits funding rate extremes",
    "VOL": "Trades volatility expansion",
    "OI": "OI momentum confirmation",
    "CONTRA": "Fades crowded positioning",
    "SCALP": "High-frequency micro moves",
    "LIQ": "Hunts liquidation cascades",
    "PAT": "Range squeeze + flags",
    "RANGE": "Buys support, sells resistance",
    "STAT": "Statistical edge detection",
    "SENT": "Funding + momentum divergence",
    "FLOW": "Institutional order flow bias",
    "REGIME": "Adapts to market regime",
    "OIDIV": "OI vs price divergence",
}

SCALP_TGT_PCT = 0.014   # Scalper uses tighter target (0.7-2.8%)
SCALP_STOP_PCT = 0.008   # Scalper uses tighter stop


# ===========================================================================
#  Backtest engine
# ===========================================================================
def run_agent_backtest(
    agent_id: str,
    candles_by_symbol: dict[str, list[Candle]],
    equity_share: float,
) -> AgentStats:
    """Run a single agent over all symbols, return its stats."""
    stats = AgentStats(agent=agent_id)
    sig_fn = AGENT_FUNCS[agent_id]

    is_scalp = agent_id == "SCALP"
    stop_pct = SCALP_STOP_PCT if is_scalp else STOP_PCT
    tgt_pct = SCALP_TGT_PCT if is_scalp else TGT_PCT

    for symbol, candles in candles_by_symbol.items():
        in_position = False
        entry_idx = 0
        entry_price = 0.0
        direction = "SKIP"

        for i, c in enumerate(candles):
            if i < CANDLES_PER_DAY:  # Skip warmup period
                continue

            if in_position:
                # Check exit conditions
                if direction == "LONG":
                    stop_price = entry_price * (1 - stop_pct)
                    target_price = entry_price * (1 + tgt_pct)
                    if c.low <= stop_price:
                        # Stop hit
                        pnl_pct = -stop_pct * LEV
                        pnl_usd = equity_share * pnl_pct
                        stats.losses += 1
                        stats.gross_pnl_usd += pnl_usd
                        stats.trades.append(Trade(
                            agent=agent_id, symbol=symbol, direction=direction,
                            entry_price=entry_price, exit_price=stop_price,
                            stop_price=stop_price, target_price=target_price,
                            pnl_pct=pnl_pct, pnl_usd=pnl_usd, is_win=False,
                            entry_ts=candles[entry_idx].ts, exit_ts=c.ts,
                            duration_candles=i - entry_idx,
                        ))
                        in_position = False
                    elif c.high >= target_price:
                        # Target hit
                        pnl_pct = tgt_pct * LEV
                        pnl_usd = equity_share * pnl_pct
                        stats.wins += 1
                        stats.gross_pnl_usd += pnl_usd
                        stats.trades.append(Trade(
                            agent=agent_id, symbol=symbol, direction=direction,
                            entry_price=entry_price, exit_price=target_price,
                            stop_price=stop_price, target_price=target_price,
                            pnl_pct=pnl_pct, pnl_usd=pnl_usd, is_win=True,
                            entry_ts=candles[entry_idx].ts, exit_ts=c.ts,
                            duration_candles=i - entry_idx,
                        ))
                        in_position = False
                    elif i - entry_idx > 288:  # Force exit after 24h
                        pnl_pct = ((c.close - entry_price) / entry_price) * LEV
                        pnl_usd = equity_share * pnl_pct
                        is_win = pnl_pct > 0
                        if is_win:
                            stats.wins += 1
                        else:
                            stats.losses += 1
                        stats.gross_pnl_usd += pnl_usd
                        stats.trades.append(Trade(
                            agent=agent_id, symbol=symbol, direction=direction,
                            entry_price=entry_price, exit_price=c.close,
                            stop_price=stop_price, target_price=target_price,
                            pnl_pct=pnl_pct, pnl_usd=pnl_usd, is_win=is_win,
                            entry_ts=candles[entry_idx].ts, exit_ts=c.ts,
                            duration_candles=i - entry_idx,
                        ))
                        in_position = False

                elif direction == "SHORT":
                    stop_price = entry_price * (1 + stop_pct)
                    target_price = entry_price * (1 - tgt_pct)
                    if c.high >= stop_price:
                        pnl_pct = -stop_pct * LEV
                        pnl_usd = equity_share * pnl_pct
                        stats.losses += 1
                        stats.gross_pnl_usd += pnl_usd
                        stats.trades.append(Trade(
                            agent=agent_id, symbol=symbol, direction=direction,
                            entry_price=entry_price, exit_price=stop_price,
                            stop_price=stop_price, target_price=target_price,
                            pnl_pct=pnl_pct, pnl_usd=pnl_usd, is_win=False,
                            entry_ts=candles[entry_idx].ts, exit_ts=c.ts,
                            duration_candles=i - entry_idx,
                        ))
                        in_position = False
                    elif c.low <= target_price:
                        pnl_pct = tgt_pct * LEV
                        pnl_usd = equity_share * pnl_pct
                        stats.wins += 1
                        stats.gross_pnl_usd += pnl_usd
                        stats.trades.append(Trade(
                            agent=agent_id, symbol=symbol, direction=direction,
                            entry_price=entry_price, exit_price=target_price,
                            stop_price=stop_price, target_price=target_price,
                            pnl_pct=pnl_pct, pnl_usd=pnl_usd, is_win=True,
                            entry_ts=candles[entry_idx].ts, exit_ts=c.ts,
                            duration_candles=i - entry_idx,
                        ))
                        in_position = False
                    elif i - entry_idx > 288:
                        pnl_pct = ((entry_price - c.close) / entry_price) * LEV
                        pnl_usd = equity_share * pnl_pct
                        is_win = pnl_pct > 0
                        if is_win:
                            stats.wins += 1
                        else:
                            stats.losses += 1
                        stats.gross_pnl_usd += pnl_usd
                        stats.trades.append(Trade(
                            agent=agent_id, symbol=symbol, direction=direction,
                            entry_price=entry_price, exit_price=c.close,
                            stop_price=stop_price, target_price=target_price,
                            pnl_pct=pnl_pct, pnl_usd=pnl_usd, is_win=is_win,
                            entry_ts=candles[entry_idx].ts, exit_ts=c.ts,
                            duration_candles=i - entry_idx,
                        ))
                        in_position = False
                continue

            # Not in position -- check for new signal
            # Only check every 12 candles (1 hour) to avoid over-trading
            if i % 12 != 0:
                continue

            direction = sig_fn(c)
            stats.total_signals += 1

            if direction == "SKIP":
                stats.skips += 1
                continue

            in_position = True
            entry_idx = i
            entry_price = c.close

    # Compute max drawdown
    if stats.trades:
        equity_curve = [0.0]
        for t in sorted(stats.trades, key=lambda x: x.entry_ts):
            equity_curve.append(equity_curve[-1] + t.pnl_usd)
        peak = 0.0
        max_dd = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq)
            if dd > max_dd:
                max_dd = dd
        stats.max_drawdown_pct = max_dd / equity_share * 100 if equity_share > 0 else 0

    return stats


# ===========================================================================
#  Report generation
# ===========================================================================
def print_separator(char: str = "=", width: int = 110) -> None:
    print(char * width)


def print_header(title: str, width: int = 110) -> None:
    print_separator()
    pad = (width - len(title) - 4) // 2
    print(f"{'|'}{' ' * pad} {title} {' ' * (width - pad - len(title) - 3)}{'|'}")
    print_separator()


def format_usd(val: float) -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}${abs(val):,.0f}"


def format_pct(val: float) -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"


def print_report(
    all_stats: list[AgentStats],
    pairs: list[str],
    test_days: int,
) -> None:
    """Print the comprehensive backtest report."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    print()
    print_header("ALGODESK 17-AGENT BACKTEST REPORT")
    print(f"  Generated: {now}")
    print(f"  Period:    Last {test_days} days of 5-minute Bybit kline data")
    print(f"  Pairs:     {', '.join(pairs)}")
    print(f"  Leverage:  {LEV}x")
    print(f"  Start $:   ${STARTING_EQUITY:,.0f}")
    print(f"  Stop/TP:   {STOP_PCT*100:.1f}% / {TGT_PCT*100:.1f}%")
    print()

    # -- Per-Agent Results --
    print_header("PER-AGENT RESULTS")
    header = (f"{'Agent':<8} {'Name':<18} {'Signals':>8} {'Trades':>7} "
              f"{'Wins':>5} {'Losses':>6} {'Win%':>6} {'PnL':>10} "
              f"{'Avg/Trade':>10} {'Sharpe':>7} {'MaxDD':>7}")
    print(header)
    print("-" * 110)

    total_trades = 0
    total_wins = 0
    total_losses = 0
    total_pnl = 0.0
    total_signals = 0

    for s in sorted(all_stats, key=lambda x: x.gross_pnl_usd, reverse=True):
        trades = s.wins + s.losses
        total_trades += trades
        total_wins += s.wins
        total_losses += s.losses
        total_pnl += s.gross_pnl_usd
        total_signals += s.total_signals

        print(
            f"{s.agent:<8} {AGENT_NAMES.get(s.agent, '?'):<18} "
            f"{s.total_signals:>8,} {trades:>7,} "
            f"{s.wins:>5,} {s.losses:>6,} "
            f"{s.win_rate:>5.1f}% "
            f"{format_usd(s.gross_pnl_usd):>10} "
            f"{format_usd(s.avg_pnl_per_trade):>10} "
            f"{s.sharpe:>7.2f} "
            f"{format_pct(-s.max_drawdown_pct):>7}"
        )
    print("-" * 110)

    overall_wr = total_wins / total_trades * 100 if total_trades > 0 else 0
    avg_pnl = total_pnl / total_trades if total_trades > 0 else 0
    print(
        f"{'TOTAL':<8} {'(All 17 Agents)':<18} "
        f"{total_signals:>8,} {total_trades:>7,} "
        f"{total_wins:>5,} {total_losses:>6,} "
        f"{overall_wr:>5.1f}% "
        f"{format_usd(total_pnl):>10} "
        f"{format_usd(avg_pnl):>10} "
        f"{'':>7} {'':>7}"
    )
    print()

    # -- Aggregate Summary --
    print_header("AGGREGATE SUMMARY")
    final_equity = STARTING_EQUITY + total_pnl
    total_return = (final_equity / STARTING_EQUITY - 1) * 100
    weekly_return = total_return / (test_days / 7) if test_days > 0 else 0
    daily_return = total_return / test_days if test_days > 0 else 0

    print(f"  Starting Equity:   ${STARTING_EQUITY:>12,.0f}")
    print(f"  Final Equity:      ${final_equity:>12,.0f}")
    print(f"  Total P&L:         {format_usd(total_pnl):>13}")
    print(f"  Total Return:      {format_pct(total_return):>13}")
    print(f"  Daily Return:      {format_pct(daily_return):>13}")
    print(f"  Weekly Return:     {format_pct(weekly_return):>13}")
    print(f"  Overall Win Rate:  {overall_wr:>12.1f}%")
    print(f"  Total Trades:      {total_trades:>12,}")
    print(f"  Avg Trades/Day:    {total_trades / test_days:>12.1f}")
    print()

    # -- Signal Distribution --
    print_header("SIGNAL DISTRIBUTION")
    print(f"  {'Agent':<8} {'LONG':>8} {'SHORT':>8} {'SKIP':>10} {'Signal Rate':>12}")
    print("  " + "-" * 50)
    for s in all_stats:
        longs = sum(1 for t in s.trades if t.direction == "LONG")
        shorts = sum(1 for t in s.trades if t.direction == "SHORT")
        skips = s.skips
        active = longs + shorts
        rate = active / s.total_signals * 100 if s.total_signals > 0 else 0
        print(f"  {s.agent:<8} {longs:>8,} {shorts:>8,} {skips:>10,} {rate:>11.1f}%")
    print()

    # -- Top Agents by Win Rate (min 5 trades) --
    print_header("TOP AGENTS BY WIN RATE (min 5 trades)")
    qualified = [s for s in all_stats if (s.wins + s.losses) >= 5]
    for rank, s in enumerate(sorted(qualified, key=lambda x: x.win_rate, reverse=True)[:5], 1):
        trades = s.wins + s.losses
        print(
            f"  #{rank}  {s.agent:<8} {AGENT_NAMES[s.agent]:<18}  "
            f"Win Rate: {s.win_rate:.1f}%  "
            f"({s.wins}W/{s.losses}L)  "
            f"PnL: {format_usd(s.gross_pnl_usd)}"
        )
    print()

    # -- Bottom Agents --
    print_header("BOTTOM AGENTS BY P&L")
    for rank, s in enumerate(sorted(all_stats, key=lambda x: x.gross_pnl_usd)[:5], 1):
        trades = s.wins + s.losses
        if trades == 0:
            print(f"  #{rank}  {s.agent:<8} {AGENT_NAMES[s.agent]:<18}  No trades")
        else:
            print(
                f"  #{rank}  {s.agent:<8} {AGENT_NAMES[s.agent]:<18}  "
                f"PnL: {format_usd(s.gross_pnl_usd)}  "
                f"Win Rate: {s.win_rate:.1f}%  "
                f"({s.wins}W/{s.losses}L)"
            )
    print()

    # -- Agent Strategy Reference --
    print_header("AGENT STRATEGY REFERENCE (from AlgoDesk README)")
    print(f"  {'ID':<8} {'Name':<18} {'Strategy'}")
    print("  " + "-" * 80)
    for aid in AGENT_IDS:
        print(f"  {aid:<8} {AGENT_NAMES[aid]:<18} {AGENT_STRATEGIES[aid]}")
    print()

    # -- Global Guards Summary --
    print_header("GLOBAL GUARDS")
    print(f"  * Volume Floor:       vol < ${MIN_VOL_24H/1e6:.0f}M 24h -> SKIP")
    print(f"  * Funding Long Block: fr > {FUNDING_LONG_BLOCK*100:.1f}% -> block LONG")
    print(f"  * Funding Short Block: fr < {FUNDING_SHORT_BLOCK*100:.1f}% -> block SHORT")
    print()

    print_separator()
    print("  DISCLAIMER: This backtest uses simulated signals over historical Bybit data.")
    print("  Past performance does not guarantee future results. Funding rates and OI are")
    print("  approximated from price/volume data since public Bybit API does not expose them.")
    print_separator()


# ===========================================================================
#  Main
# ===========================================================================
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backtest all 17 AlgoDesk trading agents over Bybit kline data."
    )
    parser.add_argument("--test-days", type=int, default=30,
                        help="Number of days to backtest (default: 30)")
    parser.add_argument("--pairs", type=str, default=",".join(DEFAULT_PAIRS),
                        help=f"Comma-separated trading pairs (default: {','.join(DEFAULT_PAIRS)})")
    parser.add_argument("--leverage", type=int, default=LEV,
                        help=f"Leverage multiplier (default: {LEV})")
    args = parser.parse_args()

    lev = args.leverage
    pairs = [p.strip().upper() for p in args.pairs.split(",")]

    print()
    print_header("ALGODESK 17-AGENT BACKTEST")
    print(f"  Fetching {args.test_days} days of 5m kline data from Bybit...")
    print(f"  Pairs: {', '.join(pairs)}")
    print(f"  Leverage: {lev}x")
    print()

    # -- Step 1: Fetch data --
    candles_by_symbol: dict[str, list[Candle]] = {}
    for pair in pairs:
        # Fetch extra days for warmup (24h window)
        candles = fetch_klines(pair, KLINE_INTERVAL, args.test_days + 2)
        if len(candles) < CANDLES_PER_DAY:
            print(f"  WARNING: Insufficient data for {pair} ({len(candles)} candles), skipping.")
            continue
        candles_by_symbol[pair] = candles

    if not candles_by_symbol:
        print("ERROR: No data fetched. Check network connection and Bybit API.")
        return

    # -- Step 2: Compute market context --
    print("\n  Computing market context (24h rolling stats, derived values)...")
    for symbol, candles in candles_by_symbol.items():
        compute_market_context(candles)

    # -- Step 3: Run each agent --
    equity_per_agent = STARTING_EQUITY / len(AGENT_IDS)
    print(f"\n  Running {len(AGENT_IDS)} agents across {len(candles_by_symbol)} pairs...")
    print(f"  Equity per agent: ${equity_per_agent:,.0f}")
    print()

    all_stats: list[AgentStats] = []
    for agent_id in AGENT_IDS:
        stats = run_agent_backtest(agent_id, candles_by_symbol, equity_per_agent)
        trades = stats.wins + stats.losses
        print(f"  {agent_id:<8} -> {trades:>4} trades  "
              f"({stats.wins}W/{stats.losses}L)  "
              f"WR: {stats.win_rate:.1f}%  "
              f"PnL: {format_usd(stats.gross_pnl_usd)}")
        all_stats.append(stats)

    # -- Step 4: Print report --
    print_report(all_stats, pairs, args.test_days)


if __name__ == "__main__":
    main()
