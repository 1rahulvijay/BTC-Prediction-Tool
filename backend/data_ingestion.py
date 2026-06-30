"""
Binance Data Ingestion
- WebSocket client for real-time aggTrade, depth, kline streams
- REST client for historical klines (multi-timeframe), derivatives, sentiment
- Improved error handling with exponential backoff
"""

import asyncio
import json
import time
import logging
from typing import Callable, Optional

import aiohttp
import websockets

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
#  Binance WebSocket Client
# ──────────────────────────────────────────────
class BinanceWebSocketClient:
    """Connects to Binance combined WebSocket streams for BTCUSDT."""

    SPOT_WS = "wss://stream.binance.com:9443/stream"
    STREAMS = ["btcusdt@aggTrade", "btcusdt@depth20@100ms", "btcusdt@kline_1m"]

    def __init__(self):
        self.ws = None
        self.running = False
        self.callbacks: dict[str, list[Callable]] = {
            "trade": [],
            "depth": [],
            "kline": [],
            "status": [],
        }
        self.reconnect_delay = 1.0
        self.max_reconnect_delay = 30.0

    def on(self, event: str, callback: Callable):
        if event in self.callbacks:
            self.callbacks[event].append(callback)

    def _emit(self, event: str, data):
        for cb in self.callbacks.get(event, []):
            try:
                cb(data)
            except Exception as e:
                logger.error(f"Callback error for {event}: {e}")

    async def connect(self):
        self.running = True
        url = f"{self.SPOT_WS}?streams={'/'.join(self.STREAMS)}"

        while self.running:
            try:
                async with websockets.connect(
                    url, ping_interval=None, ping_timeout=None
                ) as ws:
                    logger.info("Connected to Binance WebSocket")
                    self._emit("status", {"connected": True})
                    self.reconnect_delay = 1.0

                    async for message in ws:
                        stream = ""
                        try:
                            msg = json.loads(message)
                            stream = msg.get("stream", "")
                            data = msg.get("data", {})

                            if stream == "btcusdt@aggTrade":
                                self._emit(
                                    "trade",
                                    {
                                        "price": float(data["p"]),
                                        "quantity": float(data["q"]),
                                        "is_buyer_maker": data["m"],
                                        "time": data["T"],
                                        "trade_id": data["a"],
                                        "receive_time": int(time.time() * 1000),
                                    },
                                )
                            elif stream == "btcusdt@depth20@100ms":
                                self._emit(
                                    "depth",
                                    {
                                        "bids": [
                                            [float(p), float(q)]
                                            for p, q in data["bids"]
                                        ],
                                        "asks": [
                                            [float(p), float(q)]
                                            for p, q in data["asks"]
                                        ],
                                        "receive_time": int(time.time() * 1000),
                                    },
                                )
                            elif stream == "btcusdt@kline_1m":
                                k = data["k"]
                                self._emit(
                                    "kline",
                                    {
                                        "time": k["t"] // 1000,
                                        "open": float(k["o"]),
                                        "high": float(k["h"]),
                                        "low": float(k["l"]),
                                        "close": float(k["c"]),
                                        "volume": float(k["v"]),
                                        "is_closed": k["x"],
                                        "trades": k["n"],
                                    },
                                )
                        except Exception as e:
                            logger.warning(f"[ws] parse/emit error on stream={stream!r}: {type(e).__name__}: {e}")

            except Exception as e:
                logger.warning(f"WebSocket disconnected: {e}")
                self._emit("status", {"connected": False})
                if self.running:
                    await asyncio.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(
                        self.reconnect_delay * 1.5, self.max_reconnect_delay
                    )

    def stop(self):
        self.running = False


class BinanceFuturesWebSocketClient:
    """Connects to Binance Futures WebSocket stream for Liquidations."""

    FUTURES_WS = "wss://fstream.binance.com/stream"
    STREAMS = ["btcusdt@forceOrder", "btcusdt@aggTrade"]

    def __init__(self):
        self.ws = None
        self.running = False
        self.callbacks: dict[str, list[Callable]] = {
            "liquidation": [],
            "status": [],
            "perp_bar": [],   # A4 parity: one finalized 1m perp-CVD bar per minute
        }
        self.reconnect_delay = 1.0
        # A4 live PERP-CVD accumulator (parity twin of build_crossvenue_flow's per-bar CVD;
        # same sign convention: taker-buy (is_buyer_maker=False) positive).
        self._pb_ms = None   # None = no bar open yet (a real bar_ms is never None)
        self._pb_cvd = 0.0
        self._pb_vol = 0.0
        self._pb_last = 0.0
        self.last_perp_bar = None

    def on(self, event: str, callback: Callable):
        if event in self.callbacks:
            self.callbacks[event].append(callback)

    def _emit(self, event: str, data):
        for cb in self.callbacks.get(event, []):
            try:
                cb(data)
            except Exception as e:
                logger.error(f"Callback error for {event}: {e}")

    def _ingest_perp_trade(self, price: float, qty: float, m: bool, T: int):
        """Accumulate PERP CVD per clock-aligned 1m bar; emit the finalized bar on rollover.
        Sign convention: taker-buy (is_buyer_maker False) positive — IDENTICAL to
        build_crossvenue_flow._per_bar, so live == offline (train/serve parity)."""
        bar = (T // 60_000) * 60_000
        if self._pb_ms is None:
            self._pb_ms = bar
        elif bar != self._pb_ms:
            self.last_perp_bar = {
                "ts": self._pb_ms, "cvd_perp": round(self._pb_cvd, 4),
                "vol_perp": round(self._pb_vol, 4), "perp_price": self._pb_last,
            }
            self._emit("perp_bar", self.last_perp_bar)
            self._pb_ms = bar
            self._pb_cvd = 0.0
            self._pb_vol = 0.0
        self._pb_cvd += (-qty if m else qty)
        self._pb_vol += qty
        self._pb_last = price

    async def connect(self):
        self.running = True
        url = f"{self.FUTURES_WS}?streams={'/'.join(self.STREAMS)}"

        while self.running:
            try:
                async with websockets.connect(
                    url, ping_interval=None, ping_timeout=None
                ) as ws:
                    logger.info("Connected to Binance Futures WebSocket")
                    self._emit("status", {"connected": True})
                    self.reconnect_delay = 1.0

                    async for message in ws:
                        stream = ""
                        try:
                            msg = json.loads(message)
                            stream = msg.get("stream", "")
                            data = msg.get("data", {})

                            if stream == "btcusdt@forceOrder":
                                o = data.get("o", {})
                                self._emit(
                                    "liquidation",
                                    {
                                        "side": o.get("S"),
                                        "price": float(o.get("ap", 0)),
                                        "qty": float(o.get("q", 0)),
                                        "time": o.get("T"),
                                    },
                                )
                            elif stream == "btcusdt@aggTrade":
                                self._ingest_perp_trade(
                                    float(data["p"]), float(data["q"]),
                                    bool(data["m"]), int(data["T"]))
                        except Exception:
                            pass
            except Exception as e:
                logger.warning(f"Futures WebSocket disconnected: {e}")
                self._emit("status", {"connected": False})
                if self.running:
                    await asyncio.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(self.reconnect_delay * 1.5, 30.0)

    def stop(self):
        self.running = False


# ──────────────────────────────────────────────
#  Binance REST Client
# ──────────────────────────────────────────────
class BinanceRESTClient:
    """Fetches historical klines, derivatives data, and 24hr ticker."""

    SPOT_BASE = "https://api.binance.com"
    FUTURES_BASE = "https://fapi.binance.com"

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.data = {
            "klines": [],
            "ticker_24h": None,
            "funding_rate": None,
            "open_interest": None,
            "oi_history": [],
            "long_short_ratio": [],
            "liquidations": [],
        }
        self._retry_counts = {}

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )

    async def _get(self, url: str, max_retries: int = 3):
        await self._ensure_session()
        for attempt in range(max_retries):
            try:
                async with self.session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    elif resp.status == 429:
                        # Rate limited — back off
                        wait = min(2 ** (attempt + 1), 30)
                        logger.warning(f"Rate limited on {url}, waiting {wait}s")
                        await asyncio.sleep(wait)
                        continue
                    logger.warning(f"REST {resp.status} for {url}")
            except asyncio.TimeoutError:
                logger.warning(f"Timeout for {url} (attempt {attempt + 1})")
                await asyncio.sleep(1)
            except Exception as e:
                logger.warning(f"REST error for {url}: {e}")
                await asyncio.sleep(1)
        return None

    async def fetch_historical_klines(
        self,
        interval: str = "1m",
        days: int = 90,
        start_time_ms: Optional[int] = None,
        end_time_ms: Optional[int] = None,
    ) -> list[dict]:
        import datetime

        interval_ms = {
            "1m": 60_000,
            "3m": 180_000,
            "5m": 300_000,
            "15m": 900_000,
            "1h": 3_600_000,
        }.get(interval, 60_000)
        end_time = end_time_ms or int(datetime.datetime.now().timestamp() * 1000)
        start_time = start_time_ms or int(
            (datetime.datetime.now() - datetime.timedelta(days=days)).timestamp()
            * 1000
        )

        all_klines = []
        limit = 1000

        if start_time_ms or end_time_ms:
            logger.info(
                "Fetching %s klines range %s -> %s...",
                interval,
                start_time,
                end_time,
            )
        else:
            logger.info(f"Fetching {days} days of {interval} klines...")

        while start_time < end_time:
            url = f"{self.SPOT_BASE}/api/v3/klines?symbol=BTCUSDT&interval={interval}&startTime={start_time}&endTime={end_time}&limit={limit}"
            data = await self._get(url)
            if not data or len(data) == 0:
                break

            klines = [
                {
                    "time": int(k[0]) // 1000,
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]),
                    "close_time": int(k[6]) // 1000,
                    "quote_volume": float(k[7]),
                    "trades": int(k[8]),
                    "taker_buy_base": float(k[9]),
                    "taker_buy_quote": float(k[10]),
                }
                for k in data
            ]
            all_klines.extend(klines)
            start_time = int(data[-1][0]) + interval_ms

            # Avoid overwhelming the API
            await asyncio.sleep(0.05)

        if interval == "1m":
            self.data["klines"] = all_klines
        return all_klines

    async def fetch_ticker_24h(self):
        url = f"{self.SPOT_BASE}/api/v3/ticker/24hr?symbol=BTCUSDT"
        data = await self._get(url)
        if data:
            self.data["ticker_24h"] = {
                "last_price": float(data["lastPrice"]),
                "price_change": float(data["priceChange"]),
                "price_change_percent": float(data["priceChangePercent"]),
                "high_price": float(data["highPrice"]),
                "low_price": float(data["lowPrice"]),
                "volume": float(data["volume"]),
                "quote_volume": float(data["quoteVolume"]),
            }
        return self.data["ticker_24h"]

    async def fetch_funding_rate(self):
        url = f"{self.FUTURES_BASE}/fapi/v1/fundingRate?symbol=BTCUSDT&limit=1"
        data = await self._get(url)
        if data and len(data) > 0:
            self.data["funding_rate"] = {
                "rate": float(data[0]["fundingRate"]),
                "time": data[0]["fundingTime"],
            }
        return self.data["funding_rate"]

    async def fetch_open_interest(self):
        url = f"{self.FUTURES_BASE}/fapi/v1/openInterest?symbol=BTCUSDT"
        data = await self._get(url)
        if data:
            self.data["open_interest"] = {
                "open_interest": float(data["openInterest"]),
                "time": data.get("time"),
            }
        return self.data["open_interest"]

    async def fetch_oi_history(self):
        url = f"{self.FUTURES_BASE}/futures/data/openInterestHist?symbol=BTCUSDT&period=5m&limit=30"
        data = await self._get(url)
        if data:
            self.data["oi_history"] = [
                {
                    "sum_oi": float(d["sumOpenInterest"]),
                    "sum_oi_value": float(d["sumOpenInterestValue"]),
                    "timestamp": d["timestamp"],
                }
                for d in data
            ]
        return self.data["oi_history"]

    async def fetch_long_short_ratio(self):
        url = f"{self.FUTURES_BASE}/futures/data/globalLongShortAccountRatio?symbol=BTCUSDT&period=5m&limit=10"
        data = await self._get(url)
        if data:
            self.data["long_short_ratio"] = [
                {
                    "ratio": float(d["longShortRatio"]),
                    "long_account": float(d["longAccount"]),
                    "short_account": float(d["shortAccount"]),
                    "timestamp": d["timestamp"],
                }
                for d in data
            ]
        return self.data["long_short_ratio"]

    async def fetch_liquidations(self):
        # The REST endpoint for allForceOrders is deprecated and returns 400 errors.
        # We rely on the WebSocket stream (!forceOrder@arr) for real-time liquidations.
        if "liquidations" not in self.data:
            self.data["liquidations"] = {
                "recent": [],
                "long_vol": 0.0,
                "short_vol": 0.0,
                "imbalance": 0.0,
            }
        return self.data["liquidations"]

    async def fetch_all_derivatives(self):
        await asyncio.gather(
            self.fetch_ticker_24h(),
            self.fetch_funding_rate(),
            self.fetch_open_interest(),
            self.fetch_oi_history(),
            self.fetch_long_short_ratio(),
            self.fetch_liquidations(),
        )

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


# ──────────────────────────────────────────────
#  Sentiment Client
# ──────────────────────────────────────────────
class SentimentClient:
    """Fetches Fear & Greed Index and BTC dominance."""

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.data = {
            "fear_greed": None,
            "btc_dominance": None,
        }

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )

    async def fetch_fear_greed(self):
        await self._ensure_session()
        try:
            url = "https://api.alternative.me/fng/?limit=1&format=json"
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and "data" in data and len(data["data"]) > 0:
                        self.data["fear_greed"] = {
                            "value": int(data["data"][0]["value"]),
                            "classification": data["data"][0]["value_classification"],
                        }
        except Exception as e:
            logger.warning(f"Fear & Greed fetch failed: {e}")
        return self.data["fear_greed"]

    async def fetch_btc_dominance(self):
        """Fetch BTC dominance from CoinGecko."""
        await self._ensure_session()
        try:
            url = "https://api.coingecko.com/api/v3/global"
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and "data" in data:
                        market_caps = (data.get("data") or {}).get(
                            "market_cap_percentage"
                        ) or {}
                        self.data["btc_dominance"] = round(
                            market_caps.get("btc", 0), 2
                        )
        except Exception as e:
            logger.warning(f"BTC dominance fetch failed: {e}")
        return self.data["btc_dominance"]

    async def fetch_all(self):
        await asyncio.gather(
            self.fetch_fear_greed(),
            self.fetch_btc_dominance(),
        )

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


# ──────────────────────────────────────────────
#  Cross-Exchange Clients
# ──────────────────────────────────────────────


class CoinbaseWebSocketClient:
    """Connects to Coinbase WebSocket to track lead-lag vs Binance."""

    WS_URL = "wss://ws-feed.exchange.coinbase.com"

    def __init__(self):
        self.ws = None
        self.running = False
        self.callbacks = {"ticker": [], "status": []}
        self.reconnect_delay = 1.0

    def on(self, event: str, callback: Callable):
        if event in self.callbacks:
            self.callbacks[event].append(callback)

    def _emit(self, event: str, data):
        for cb in self.callbacks.get(event, []):
            try:
                cb(data)
            except Exception as e:
                logger.error(f"Coinbase callback error: {e}")

    async def connect(self):
        self.running = True

        subscribe_msg = {
            "type": "subscribe",
            "product_ids": ["BTC-USD"],
            "channels": ["ticker"],
        }

        while self.running:
            try:
                async with websockets.connect(
                    self.WS_URL, ping_interval=None, ping_timeout=None
                ) as ws:
                    logger.info("Connected to Coinbase WebSocket")
                    self._emit("status", {"connected": True})
                    self.reconnect_delay = 1.0

                    await ws.send(json.dumps(subscribe_msg))

                    async for message in ws:
                        try:
                            msg = json.loads(message)
                            if msg.get("type") == "ticker" and "price" in msg:
                                self._emit(
                                    "ticker",
                                    {
                                        "price": float(msg["price"]),
                                        "time": msg.get("time"),
                                        "receive_time": int(time.time() * 1000),
                                    },
                                )
                        except Exception:
                            pass  # malformed/unknown WS message — skip, keep the stream

            except Exception as e:
                logger.warning(f"Coinbase WS disconnected: {e}")
                self._emit("status", {"connected": False})
                if self.running:
                    await asyncio.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(self.reconnect_delay * 1.5, 30.0)

    def stop(self):
        self.running = False


class BybitRESTClient:
    """Fetches Bybit Open Interest and Funding Data."""

    REST_URL = "https://api.bybit.com/v5/market"

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.data = {
            "open_interest": None,
            "funding_rate": None,
            "receive_time": None,
        }

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )

    async def _get(self, url: str):
        await self._ensure_session()
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.warning(f"Bybit fetch error: {e}")
        return None

    async def fetch_open_interest(self):
        url = f"{self.REST_URL}/open-interest?category=linear&symbol=BTCUSDT&intervalTime=5min"
        res = await self._get(url)
        if res and res.get("retCode") == 0:
            result = res.get("result", {}) or {}
            lst = result.get("list", []) or []
            row = lst[0] if lst and isinstance(lst[0], dict) else None
            if row:
                self.data["open_interest"] = float(row.get("openInterest", 0))
                self.data["receive_time"] = int(time.time() * 1000)
        return self.data["open_interest"]

    async def fetch_funding_rate(self):
        url = f"{self.REST_URL}/funding/history?category=linear&symbol=BTCUSDT&limit=1"
        res = await self._get(url)
        if res and res.get("retCode") == 0:
            result = res.get("result", {}) or {}
            lst = result.get("list", []) or []
            row = lst[0] if lst and isinstance(lst[0], dict) else None
            if row:
                self.data["funding_rate"] = float(row.get("fundingRate", 0))
                self.data["receive_time"] = int(time.time() * 1000)
        return self.data["funding_rate"]

    async def fetch_all(self):
        await asyncio.gather(self.fetch_open_interest(), self.fetch_funding_rate())

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class ChainlinkRESTClient:
    """Fetches BTC/USD price to act as the Chainlink Oracle resolution source."""

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.data = {"btc_usd": None}

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            )

    async def fetch_price(self):
        await self._ensure_session()
        try:
            # Using CoinGecko as public proxy for Chainlink BTC/USD stream
            url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    if data and "bitcoin" in data:
                        self.data["btc_usd"] = float(data["bitcoin"]["usd"])
        except Exception as e:
            logger.warning(f"Chainlink fetch error: {e}")
        return self.data["btc_usd"]

    async def fetch_all(self):
        await self.fetch_price()

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class CrossAssetWebSocketClient:
    """Connects to Binance combined WebSocket streams for ETH and SOL."""

    SPOT_WS = "wss://stream.binance.com:9443/stream"
    STREAMS = [
        "ethusdt@aggTrade", "ethusdt@depth20@100ms", "ethusdt@kline_1m",
        "solusdt@aggTrade", "solusdt@depth20@100ms", "solusdt@kline_1m"
    ]

    def __init__(self):
        self.ws = None
        self.running = False
        self.callbacks: dict[str, list[Callable]] = {
            "cross_asset_trade": [],
            "cross_asset_depth": [],
            "cross_asset_kline": [],
            "status": [],
        }
        self.reconnect_delay = 1.0
        self.max_reconnect_delay = 30.0

    def on(self, event: str, callback: Callable):
        if event in self.callbacks:
            self.callbacks[event].append(callback)

    def _emit(self, event: str, data):
        for cb in self.callbacks.get(event, []):
            try:
                cb(data)
            except Exception as e:
                logger.error(f"Callback error for {event}: {e}")

    async def connect(self):
        self.running = True
        url = f"{self.SPOT_WS}?streams={'/'.join(self.STREAMS)}"

        while self.running:
            try:
                async with websockets.connect(
                    url, ping_interval=None, ping_timeout=None
                ) as ws:
                    logger.info("Connected to Binance Cross-Asset WebSocket")
                    self._emit("status", {"connected": True})
                    self.reconnect_delay = 1.0

                    async for message in ws:
                        stream = ""
                        try:
                            msg = json.loads(message)
                            stream = msg.get("stream", "")
                            data = msg.get("data", {})
                            
                            asset = "ETH" if "eth" in stream else "SOL" if "sol" in stream else "UNKNOWN"

                            if "@aggTrade" in stream:
                                self._emit(
                                    "cross_asset_trade",
                                    {
                                        "asset": asset,
                                        "price": float(data["p"]),
                                        "quantity": float(data["q"]),
                                        "is_buyer_maker": data["m"],
                                        "time": data["T"],
                                    },
                                )
                            elif "@depth20" in stream:
                                self._emit(
                                    "cross_asset_depth",
                                    {
                                        "asset": asset,
                                        "bids": [[float(p), float(q)] for p, q in data["bids"]],
                                        "asks": [[float(p), float(q)] for p, q in data["asks"]],
                                    },
                                )
                            elif "@kline_1m" in stream:
                                k = data["k"]
                                self._emit(
                                    "cross_asset_kline",
                                    {
                                        "asset": asset,
                                        "time": k["t"] // 1000,
                                        "close": float(k["c"]),
                                        "volume": float(k["v"]),
                                        "is_closed": k["x"],
                                    },
                                )
                        except Exception as e:
                            logger.debug(f"CrossAsset Parse error: {e}")

            except Exception as e:
                logger.warning(f"CrossAsset WebSocket disconnected: {e}")
                self._emit("status", {"connected": False})
                if self.running:
                    await asyncio.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(
                        self.reconnect_delay * 1.5, self.max_reconnect_delay
                    )

    def stop(self):
        self.running = False


class TradFiMacroClient:
    """TradFi macro (DXY, US 10Y yield) from Yahoo Finance — free, no API key.

    Range-validated with fallback to the last good value: a missing/garbage/out-of-range
    response NEVER injects noise, it simply keeps the previous value (so worst case is
    the same neutral constant as before). Macro moves slowly, so it is polled infrequently
    and contributes mainly to longer-horizon/regime context, not 1m scalps.
    """

    DXY_URL = "https://query1.finance.yahoo.com/v8/finance/chart/DX-Y.NYB?interval=1d&range=1d"
    US10Y_URL = "https://query1.finance.yahoo.com/v8/finance/chart/%5ETNX?interval=1d&range=1d"

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.data = {"dxy": 104.5, "us10y": 4.25, "time": time.time()}

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=8),
                headers={"User-Agent": "Mozilla/5.0"},
            )

    async def _fetch_price(self, url: str):
        await self._ensure_session()
        try:
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
            meta = data["chart"]["result"][0]["meta"]
            return float(meta.get("regularMarketPrice"))
        except Exception:
            return None

    async def fetch_all(self):
        dxy = await self._fetch_price(self.DXY_URL)
        if dxy is not None and 80.0 <= dxy <= 130.0:
            self.data["dxy"] = round(dxy, 3)
        y = await self._fetch_price(self.US10Y_URL)
        if y is not None and 0.5 <= y <= 10.0:
            self.data["us10y"] = round(y, 3)
        self.data["time"] = time.time()

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()


class MultiExchangePriceClient:
    """
    Fetches BTC spot last-price from multiple exchanges (Bybit, KuCoin) via public
    REST. Combined with Binance/Coinbase/Chainlink (already in the payload) this gives
    a multi-venue consensus. Per-exchange deviation from consensus is a real lead/lag
    signal: a venue persistently above consensus shows where aggressive demand is.
    Fully defensive — a failed feed simply returns the last good value or None.
    """

    BYBIT_URL = "https://api.bybit.com/v5/market/tickers?category=spot&symbol=BTCUSDT"
    KUCOIN_URL = "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=BTC-USDT"

    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.data = {"bybit": None, "kucoin": None, "time": 0}

    async def _ensure_session(self):
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8))

    async def _get(self, url: str):
        await self._ensure_session()
        try:
            async with self.session.get(url) as resp:
                if resp.status == 200:
                    return await resp.json()
        except Exception as e:
            logger.debug(f"MultiExchange fetch error {url}: {e}")
        return None

    async def fetch_all(self):
        bybit = await self._get(self.BYBIT_URL)
        try:
            if bybit and bybit.get("retCode") == 0:
                result = bybit.get("result") or {}
                lst = result.get("list", []) if isinstance(result, dict) else []
                first = lst[0] if lst and isinstance(lst[0], dict) else {}
                if first:
                    self.data["bybit"] = float(first.get("lastPrice", 0)) or self.data["bybit"]
        except Exception:
            pass
        kucoin = await self._get(self.KUCOIN_URL)
        try:
            if kucoin and kucoin.get("data"):
                self.data["kucoin"] = float(kucoin["data"].get("price", 0)) or self.data["kucoin"]
        except Exception:
            pass
        self.data["time"] = time.time()
        return self.data

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
