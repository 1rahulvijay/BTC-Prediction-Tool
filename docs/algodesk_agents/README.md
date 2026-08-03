# AlgoDesk 17-Agent Strategy Documentation

## Source

All strategy specifications extracted from:
- **Landing page**: [algodesk-bot.pages.dev/landing](https://algodesk-bot.pages.dev/landing)
- **GitHub README**: `algodesk-bot/algodesk-bot` (user screenshot)
- **JavaScript backtest engine**: Embedded in the landing page `<script>` block

---

## Important: These Are Rule-Based Strategies, NOT ML Models

The 17 AlgoDesk agents are **threshold-based signal generators**. Each agent's
`sig()` function checks market conditions against fixed thresholds and returns
`LONG`, `SHORT`, or `SKIP`. No machine learning, no training, no model files.

Example (TREND agent):
```python
if change_24h > 5.0% and position_in_range > 0.65:
    return "LONG"
elif change_24h < -5.0% and position_in_range < 0.35:
    return "SHORT"
else:
    return "SKIP"
```

---

## How the Landing Page Uses These Strategies

The AlgoDesk landing page contains a **JavaScript backtest engine** that:

1. **Fetches 180 days** of daily klines from `api.bybit.com/v5/market/kline` for 7 pairs:
   - BTCUSDT, ETHUSDT, SOLUSDT, BNBUSDT, XRPUSDT, AVAXUSDT, LINKUSDT

2. **Runs a simplified backtest** using only 7 of the 17 agents:
   - TREND, MOMO, BREAK, MEAN, VOL, OI, SCALP

3. **Signal logic** (simplified from the README):
   - If daily `close > open` by >1% -> LONG signal (bullish day)
   - If daily `close < open` by >1% -> SHORT signal (bearish day)
   - Win = intraday high/low touched target (3% TP)
   - Loss = stop hit (1.5% SL)

4. **Equity curve**: Starts at $10,000, compounds at `(1 + dayReturn / signalsPerDay * 0.012)`

5. **Trade history table**: Fetches live Bybit ticker prices and generates randomized trades

---

## The 17 Trading Agents

### Agent Specifications

| # | ID | Name | Strategy | Key Conditions |
|---|------|------|----------|----------------|
| 1 | **TREND** | Trend Follower | Rides strong directional moves | 24h change >5-8%, pos >0.65, high volume |
| 2 | **MOMO** | Momentum | Catches price acceleration | Change >8-12%, vol >$100M-200M, pos extreme |
| 3 | **BREAK** | Breakout | Enters on range breaks | Price within 3% of day high/low, volume surge |
| 4 | **MEAN** | Mean Reversion | Fades overextended moves | Change >15-20%, extreme position in range |
| 5 | **FUND** | Funding Arb | Exploits funding rate extremes | FR above/below +/-0.0015-0.003 |
| 6 | **VOL** | Volatility | Trades volatility expansion | Volume >$150M-300M, directional move |
| 7 | **OI** | Open Interest | OI momentum confirmation | OI >$2B, directional change |
| 8 | **CONTRA** | Contrarian | Fades crowded positioning | Change >18-25%, extreme pos, high funding |
| 9 | **SCALP** | Scalper | High-frequency micro moves | Vol >$200M-500M, tight 0.7-2.8% range |
| 10 | **LIQ** | Liquidation | Hunts liquidation cascades | Change >8-10%, vol >$200M-400M, pos extreme |
| 11 | **PAT** | Pattern | Range squeeze + flags | Tight day range + directional break |
| 12 | **RANGE** | Range Trader | Buys support, sells resistance | Price at range extremes, low volatility |
| 13 | **STAT** | Statistical Arb | Statistical edge detection | OI divergence from price + funding rate |
| 14 | **SENT** | Sentiment | Funding + momentum divergence | Extreme funding with opposite positioning |
| 15 | **FLOW** | Order Flow | Institutional order flow bias | Vol >$300M-500M, directional flow + OI |
| 16 | **REGIME** | Regime Detector | Adapts to market regime | Trending/ranging/volatile classification |
| 17 | **OIDIV** | OI Divergence | OI vs price divergence | Price and OI opposite, FR confirms |

---

## Global Guards

Applied inside every agent's `sig()` function **before** returning a signal:

```
if (vol < 50_000_000)  -> force SKIP      // minimum $50M 24h volume
if (fr  >  0.005)      -> block LONG       // funding too expensive to be long
if (fr  < -0.005)      -> block SHORT      // funding too expensive to be short
```

## Derived Values

Computed once per candle, shared by all agents:

```
pos = (price - low24h) / (high24h - low24h)   // 0.0 = day low, 1.0 = day high
rsi = round(pos * 100)                        // simplified RSI proxy (0-100)
```

---

## Test Script

### File

`backend/test_algodesk_17_agents_30d.py`

### Usage

```powershell
# Default: 30 days, all 7 pairs, 5x leverage
python backend/test_algodesk_17_agents_30d.py

# Custom: 14 days, BTC+ETH only, 10x leverage
python backend/test_algodesk_17_agents_30d.py --test-days 14 --pairs BTCUSDT,ETHUSDT --leverage 10
```

### Backtest Parameters

| Parameter | Value |
|-----------|-------|
| Start equity | $10,000 |
| Leverage | 5x |
| Stop-loss | 1.5% (Scalp: 0.8%) |
| Take-profit | 3.0% (Scalp: 1.4%) |
| Timeframe | 5-minute candles |
| Signal check interval | Every 12 candles (1 hour) |
| Max hold time | 24 hours (forced exit) |
| Equity per agent | $10,000 / 17 = $588.24 |

---

## 30-Day Backtest Results (2026-08-03)

### Summary

| Metric | Value |
|--------|-------|
| Starting Equity | $10,000 |
| Final Equity | $10,593 |
| **Total Return** | **+5.9%** |
| Weekly Return | +1.4% |
| Overall Win Rate | 46.1% |
| Total Trades | 152 |

### Per-Agent Results

| Agent | Trades | W/L | Win% | P&L | Sharpe |
|-------|--------|-----|------|-----|--------|
| PAT | 25 | 11/14 | 44.0% | +$269 | 3.00 |
| BREAK | 24 | 14/10 | 58.3% | +$257 | 3.61 |
| SCALP | 33 | 15/18 | 45.5% | +$194 | 2.85 |
| FUND | 8 | 5/3 | 62.5% | +$153 | 5.29 |
| REGIME | 6 | 3/3 | 50.0% | +$50 | 2.54 |
| TREND | 3 | 1/2 | 33.3% | +$0 | 0.00 |
| VOL | 13 | 6/7 | 46.2% | -$12 | -0.28 |
| FLOW | 2 | 0/2 | 0.0% | -$88 | 0.00 |
| RANGE | 38 | 15/23 | 39.5% | -$230 | -1.98 |

8 agents (MOMO, MEAN, OI, CONTRA, LIQ, STAT, SENT, OIDIV) produced zero
trades -- their extreme thresholds were never triggered in this 30-day window.

### Limitations

- **Funding rates** approximated from 8h price change (no historical FR API)
- **Open interest** approximated as `vol_24h * 3.5` (no historical OI API)
- Signal frequency throttled to 1 check/hour to prevent over-trading
