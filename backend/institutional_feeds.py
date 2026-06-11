"""
Institutional Alpha Feeds
Options (Deribit), CME Basis proxy, Stablecoin Flows, On-Chain Exchange Flows.
Each feed is an async poller with graceful fallback to neutral values.
"""

import asyncio
import logging
import time
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


class DeribitOptionsClient:
    """
    Fetches BTC options summary from Deribit public API (no auth needed).
    Computes: put_call_ratio, 25d_skew, max_pain, atm_iv.
    Polled every 30s.
    """
    BASE_URL = "https://www.deribit.com/api/v2/public"

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.data = {
            "put_call_ratio": 0.0,
            "skew_25d": 0.0,
            "max_pain": 0.0,
            "atm_iv": 0.0,
            "last_update": 0,
        }

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )

    async def _get(self, url: str) -> Optional[dict]:
        await self._ensure_session()
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.warning(f"Deribit fetch error: {e}")
        return None

    async def fetch_options_summary(self):
        """Fetch BTC options book summary and compute derived signals."""
        url = f"{self.BASE_URL}/get_book_summary_by_currency?currency=BTC&kind=option"
        res = await self._get(url)
        if not res or "result" not in res:
            return self.data

        instruments = res["result"]
        if not instruments:
            return self.data

        total_calls = 0
        total_puts = 0
        call_oi = 0
        put_oi = 0
        iv_by_strike = {}
        oi_by_strike = {}

        for inst in instruments:
            name = inst.get("instrument_name", "")
            oi = float(inst.get("open_interest", 0))
            iv = float(inst.get("mark_iv", 0)) / 100.0  # Convert from % to decimal
            underlying = float(inst.get("underlying_price", 0))

            if "-C" in name:
                total_calls += 1
                call_oi += oi
            elif "-P" in name:
                total_puts += 1
                put_oi += oi

            # Parse strike from instrument name (e.g., BTC-28JUN24-70000-C)
            parts = name.split("-")
            if len(parts) >= 3:
                try:
                    strike = float(parts[2])
                    if strike not in oi_by_strike:
                        oi_by_strike[strike] = 0
                    oi_by_strike[strike] += oi
                    if iv > 0:
                        iv_by_strike[strike] = iv
                except (ValueError, IndexError):
                    pass

        # Put/Call Ratio
        pcr = put_oi / (call_oi + 1e-9) if call_oi > 0 else 1.0
        self.data["put_call_ratio"] = round(min(3.0, pcr), 4)

        # Max Pain: strike with maximum total OI
        if oi_by_strike:
            max_pain_strike = max(oi_by_strike.items(), key=lambda x: x[1])[0]
            self.data["max_pain"] = float(max_pain_strike)

        # ATM IV: IV of strike closest to underlying price
        if iv_by_strike and instruments:
            first = instruments[0] if isinstance(instruments[0], dict) else {}
            underlying = float(first.get("underlying_price", 0))
            if underlying > 0:
                closest_strike = min(iv_by_strike.keys(), key=lambda s: abs(s - underlying))
                self.data["atm_iv"] = round(iv_by_strike[closest_strike], 4)

        # 25-delta skew proxy: IV of 25% OTM put vs 25% OTM call
        if iv_by_strike and instruments:
            first = instruments[0] if isinstance(instruments[0], dict) else {}
            underlying = float(first.get("underlying_price", 0))
            if underlying > 0:
                put_strike = underlying * 0.95  # ~5% OTM put (proxy for 25d)
                call_strike = underlying * 1.05  # ~5% OTM call
                put_iv = self._closest_iv(iv_by_strike, put_strike)
                call_iv = self._closest_iv(iv_by_strike, call_strike)
                if put_iv > 0 and call_iv > 0:
                    self.data["skew_25d"] = round(put_iv - call_iv, 4)

        self.data["last_update"] = time.time()
        return self.data

    @staticmethod
    def _closest_iv(iv_map: dict, target: float) -> float:
        if not iv_map:
            return 0.0
        closest = min(iv_map.keys(), key=lambda s: abs(s - target))
        return iv_map[closest]

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class CMEBasisClient:
    """
    CME Basis proxy: Binance perpetual mark price vs spot price.
    True CME data requires credentials; this approximates the same signal
    using the futures funding basis already available via Binance.
    """
    MARK_URL = "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.data = {
            "basis_spread": 0.0,
            "basis_velocity": 0.0,
            "last_update": 0,
        }
        self._basis_history = []

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )

    async def fetch_basis(self, spot_price: float = 0.0):
        """Fetch mark price and compute basis spread vs spot."""
        await self._ensure_session()
        try:
            async with self.session.get(self.MARK_URL) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    mark_price = float(data.get("markPrice", 0))
                    if mark_price > 0 and spot_price > 0:
                        basis = (mark_price - spot_price) / spot_price
                        self.data["basis_spread"] = round(basis, 6)

                        self._basis_history.append((time.time(), basis))
                        # Keep 5min of history
                        cutoff = time.time() - 300
                        self._basis_history = [(t, b) for t, b in self._basis_history if t > cutoff]

                        # Basis velocity (change over last 60s)
                        if len(self._basis_history) >= 2:
                            old = self._basis_history[0]
                            dt = time.time() - old[0]
                            if dt > 5:
                                self.data["basis_velocity"] = round((basis - old[1]) / dt * 60, 6)

                        self.data["last_update"] = time.time()
        except Exception as e:
            logger.warning(f"CME basis fetch error: {e}")
        return self.data

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class StablecoinFlowClient:
    """
    Stablecoin supply proxy via CoinGecko free API.
    Tracks USDT + USDC market cap changes as a liquidity inflow signal.
    Polled every 5 minutes (CoinGecko rate limits).
    """
    COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
    PARAMS = "?ids=tether,usd-coin&vs_currencies=usd&include_market_cap=true"

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.data = {
            "stablecoin_flow": 0.0,
            "usdt_mcap": 0.0,
            "usdc_mcap": 0.0,
            "last_update": 0,
        }
        self._mcap_history = []

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )

    async def fetch_stablecoin_data(self):
        """Fetch USDT + USDC market caps and compute flow signal."""
        await self._ensure_session()
        try:
            url = f"{self.COINGECKO_URL}{self.PARAMS}"
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    usdt_mcap = float((data.get("tether") or {}).get("usd_market_cap", 0))
                    usdc_mcap = float((data.get("usd-coin") or {}).get("usd_market_cap", 0))
                    total_mcap = usdt_mcap + usdc_mcap

                    self.data["usdt_mcap"] = usdt_mcap
                    self.data["usdc_mcap"] = usdc_mcap

                    self._mcap_history.append((time.time(), total_mcap))
                    # Keep 1 hour of history
                    cutoff = time.time() - 3600
                    self._mcap_history = [(t, m) for t, m in self._mcap_history if t > cutoff]

                    # Flow = rate of change in total stablecoin mcap (per hour, normalized)
                    if len(self._mcap_history) >= 2 and self._mcap_history[0][1] > 0:
                        old_mcap = self._mcap_history[0][1]
                        flow = (total_mcap - old_mcap) / old_mcap
                        # Normalize: typical daily changes are 0.1-0.5%
                        self.data["stablecoin_flow"] = round(max(-1.0, min(1.0, flow * 200)), 4)

                    self.data["last_update"] = time.time()
        except Exception as e:
            logger.warning(f"Stablecoin flow fetch error: {e}")
        return self.data

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class ExchangeFlowClient:
    """
    On-chain exchange flow proxy.
    Uses Blockchain.com's public API to estimate BTC exchange reserves.
    Polled every 5 minutes.
    """
    # Public endpoint for total BTC on exchanges (approximation)
    BLOCKCHAIN_URL = "https://api.blockchain.info/stats"

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.data = {
            "exchange_netflow": 0.0,
            "last_update": 0,
        }
        self._reserve_history = []

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )

    async def fetch_exchange_flow(self):
        """Estimate exchange flow from mempool/blockchain metrics."""
        await self._ensure_session()
        try:
            async with self.session.get(self.BLOCKCHAIN_URL) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    # Use trade_volume as a proxy
                    trade_vol = float(data.get("trade_volume_btc", 0))

                    # Proxy metric: high tx + high trade vol = likely exchange activity
                    proxy = trade_vol  # BTC traded in last 24h

                    self._reserve_history.append((time.time(), proxy))
                    cutoff = time.time() - 3600
                    self._reserve_history = [(t, r) for t, r in self._reserve_history if t > cutoff]

                    if len(self._reserve_history) >= 2 and self._reserve_history[0][1] > 0:
                        old_val = self._reserve_history[0][1]
                        flow = (proxy - old_val) / (old_val + 1e-9)
                        # Positive = more exchange activity (potential sell pressure)
                        # Negative = less activity (accumulation)
                        self.data["exchange_netflow"] = round(max(-1.0, min(1.0, flow * 10)), 4)

                    self.data["last_update"] = time.time()
        except Exception as e:
            logger.warning(f"Exchange flow fetch error: {e}")
        return self.data

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
