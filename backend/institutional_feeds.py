"""
Institutional Alpha Feeds
Options (Deribit), CME Basis proxy, Stablecoin Flows, On-Chain Exchange Flows.
Each feed is an async poller with graceful fallback to neutral values.
"""

import datetime as _dt
import logging
import math
import statistics
import time
from typing import Optional

import aiohttp

logger = logging.getLogger(__name__)


def _bs_gamma(S: float, K: float, T: float, sigma: float) -> float:
    """Black-Scholes gamma (r=0, fine for crypto). 0 for degenerate inputs.
    gamma = phi(d1) / (S*sigma*sqrt(T)); phi = standard-normal pdf (math only, no scipy)."""
    if S <= 0 or K <= 0 or T <= 0 or sigma <= 0:
        return 0.0
    try:
        d1 = (math.log(S / K) + 0.5 * sigma * sigma * T) / (sigma * math.sqrt(T))
        pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
        return pdf / (S * sigma * math.sqrt(T))
    except (ValueError, ZeroDivisionError):
        return 0.0


def compute_gex(instruments: list, now_ms: int) -> dict:
    """Net dealer Gamma EXposure ($ per 1% move) from a Deribit BTC option book summary.

    Calls +, puts - (naive dealer convention: long calls / short puts). Positive GEX =
    dealers long gamma -> they fade moves -> price pins / mean-reverts; negative GEX =
    dealers short gamma -> they chase moves -> trending/explosive. This is a REGIME
    signal that is NOT price- or order-book-derived (the kind of new information that can
    move the model off the coin-flip ceiling). BS gamma computed analytically from the
    fields the book-summary already returns (OI, mark_iv, underlying, strike, expiry) —
    no per-instrument ticker call."""
    net_g = 0.0   # signed Sum gamma*OI (calls +, puts -)
    abs_g = 0.0   # Sum |gamma|*OI (total positioning magnitude)
    spot = 0.0
    now_s = now_ms / 1000.0
    for inst in instruments:
        try:
            name = inst.get("instrument_name", "")
            parts = name.split("-")          # BTC-28JUN24-70000-C
            if len(parts) < 4:
                continue
            S = float(inst.get("underlying_price", 0) or 0)
            if S > 0:
                spot = S
            oi = float(inst.get("open_interest", 0) or 0)
            sigma = float(inst.get("mark_iv", 0) or 0) / 100.0
            K = float(parts[2])
            is_call = parts[3].upper().startswith("C")
            exp = _dt.datetime.strptime(parts[1].title(), "%d%b%y").replace(
                tzinfo=_dt.timezone.utc, hour=8)   # Deribit expiry = 08:00 UTC
            T = (exp.timestamp() - now_s) / (365.25 * 86400.0)
            g = _bs_gamma(S, K, T, sigma)
            net_g += g * oi * (1.0 if is_call else -1.0)
            abs_g += g * oi
        except (ValueError, IndexError, KeyError):
            continue
    scale = spot * spot * 0.01     # dollar-gamma per 1% move
    return {"gex": round(scale * net_g, 2),
            "total_gamma": round(scale * abs_g, 2),
            "spot": round(spot, 2)}


def compute_option_surface_metrics(instruments: list, now_ms: int) -> dict:
    """Compute expiry-consistent BTC option summary metrics.

    ATM IV, skew and max pain must come from one expiry. Combining equal strikes
    across expiries or allowing a call IV to overwrite a put IV creates values
    that do not describe any tradeable option surface. Prefer an expiry with at
    least 24 hours remaining so expiry-hour microstructure does not dominate the
    dashboard.
    """
    records = []
    now_s = now_ms / 1000.0
    for instrument in instruments:
        try:
            name = str(instrument.get("instrument_name") or "")
            parts = name.split("-")
            if len(parts) < 4 or parts[0] != "BTC":
                continue
            expiry = _dt.datetime.strptime(parts[1].title(), "%d%b%y").replace(
                tzinfo=_dt.timezone.utc,
                hour=8,
            )
            if expiry.timestamp() <= now_s:
                continue
            option_type = parts[3].upper()
            if option_type not in {"C", "P"}:
                continue
            records.append({
                "expiry_ms": int(expiry.timestamp() * 1000),
                "strike": float(parts[2]),
                "type": option_type,
                "oi": max(0.0, float(instrument.get("open_interest", 0.0) or 0.0)),
                "iv": max(0.0, float(instrument.get("mark_iv", 0.0) or 0.0) / 100.0),
                "underlying": max(
                    0.0,
                    float(instrument.get("underlying_price", 0.0) or 0.0),
                ),
            })
        except (TypeError, ValueError, IndexError, KeyError):
            continue

    if not records:
        return {
            "put_call_ratio": 1.0,
            "max_pain": 0.0,
            "atm_iv": 0.0,
            "skew_25d": 0.0,
            "option_expiry_ms": 0,
        }

    call_oi = sum(row["oi"] for row in records if row["type"] == "C")
    put_oi = sum(row["oi"] for row in records if row["type"] == "P")
    expiries = sorted({row["expiry_ms"] for row in records})
    stable_expiries = [
        expiry for expiry in expiries
        if expiry - now_ms >= 24 * 60 * 60 * 1000
    ]
    nearest_expiry = stable_expiries[0] if stable_expiries else expiries[0]
    surface = [row for row in records if row["expiry_ms"] == nearest_expiry]
    spots = [row["underlying"] for row in surface if row["underlying"] > 0]
    spot = float(statistics.median(spots)) if spots else 0.0

    def side_iv(option_type: str, target: float) -> float:
        candidates = [
            row for row in surface
            if row["type"] == option_type and row["iv"] > 0
        ]
        if not candidates or target <= 0:
            return 0.0
        nearest = min(candidates, key=lambda row: abs(row["strike"] - target))
        return float(nearest["iv"])

    atm_iv = 0.0
    skew = 0.0
    if spot > 0:
        strikes = sorted({row["strike"] for row in surface})
        if strikes:
            atm_strike = min(strikes, key=lambda strike: abs(strike - spot))
            atm_values = [
                row["iv"] for row in surface
                if row["strike"] == atm_strike and row["iv"] > 0
            ]
            if atm_values:
                atm_iv = float(statistics.fmean(atm_values))
        put_iv = side_iv("P", spot * 0.95)
        call_iv = side_iv("C", spot * 1.05)
        if put_iv > 0 and call_iv > 0:
            skew = put_iv - call_iv

    max_pain = 0.0
    candidate_strikes = sorted({row["strike"] for row in surface})
    if candidate_strikes:
        def expiry_payout(settlement: float) -> float:
            payout = 0.0
            for row in surface:
                intrinsic = (
                    max(0.0, settlement - row["strike"])
                    if row["type"] == "C"
                    else max(0.0, row["strike"] - settlement)
                )
                payout += row["oi"] * intrinsic
            return payout

        max_pain = min(candidate_strikes, key=expiry_payout)

    pcr = put_oi / call_oi if call_oi > 0 else 1.0
    return {
        "put_call_ratio": round(min(3.0, pcr), 4),
        "max_pain": float(max_pain),
        "atm_iv": round(atm_iv, 4),
        "skew_25d": round(skew, 4),
        "option_expiry_ms": nearest_expiry,
    }


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
            "gex": 0.0,            # net dealer gamma exposure ($/1% move)
            "total_gamma": 0.0,    # total gamma positioning magnitude
            "option_expiry_ms": 0,
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

        self.data.update(
            compute_option_surface_metrics(instruments, int(time.time() * 1000))
        )

        # Net dealer gamma exposure (regime signal; analytic BS gamma, no extra API call).
        try:
            _g = compute_gex(instruments, int(time.time() * 1000))
            self.data["gex"] = _g["gex"]
            self.data["total_gamma"] = _g["total_gamma"]
        except Exception as _ge:
            logger.debug(f"GEX compute skipped: {_ge}")

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
