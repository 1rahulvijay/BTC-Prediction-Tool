"""
Binance Data Ingestion
- WebSocket client for real-time aggTrade, depth, kline streams
- REST client for historical klines (multi-timeframe), derivatives, sentiment
- Improved error handling with exponential backoff
"""

import asyncio
from collections import deque
import json
import math
import os
import time

# ONE canonical candle shape for every producer. See kline_schema for why:
# REST emitted close_time and no is_closed, WS emitted is_closed and no
# close_time, and consumers disagreed about what a missing key meant.
from kline_schema import canonical_kline
import logging
from pathlib import Path
from typing import Callable, Optional

import aiohttp
import websockets

#: P0-23. KEEPALIVE, so a socket that is open but DEAD is detected.
#:
#: These were both None, which disables websockets' ping/pong entirely. A TCP connection can
#: stay established while the venue has stopped sending anything, and with no ping there is
#: nothing to notice: the task stays pending, the supervisor sees a live coroutine, and the
#: feed is silently stale. Staleness then looks like a quiet market rather than a dead feed.
#:
#: With pings enabled the library raises ConnectionClosed when pongs stop arriving, which the
#: existing reconnect loops already handle.
WS_PING_INTERVAL_S = float(os.environ.get("BTC_WS_PING_INTERVAL_S", "20"))
WS_PING_TIMEOUT_S = float(os.environ.get("BTC_WS_PING_TIMEOUT_S", "20"))

logger = logging.getLogger(__name__)

_QUARANTINE_DIR = Path(__file__).resolve().parents[1] / "data" / "quarantine"


class _ProtocolHealth:
    """Content health for a public feed, independent of socket connectivity."""

    def __init__(self, source: str):
        self.source = source
        self.connected = False
        self.messages = 0
        self.valid_messages = 0
        self.unknown_messages = 0
        self.parse_errors = 0
        self.last_message_ms: int | None = None
        self.last_valid_ms: int | None = None
        self.last_error = ""
        self.errors_by_stream: dict[str, int] = {}
        self._recent = deque(maxlen=1_000)

    def message(self) -> None:
        self.messages += 1
        self.last_message_ms = int(time.time() * 1000)

    def valid(self) -> None:
        self.valid_messages += 1
        self.last_valid_ms = int(time.time() * 1000)
        self._recent.append(0)

    def unknown(self) -> None:
        self.unknown_messages += 1
        self._recent.append(0)

    def error(self, stream: str, raw, exc: Exception) -> None:
        stream_name = str(stream or "unknown")
        self.parse_errors += 1
        self.errors_by_stream[stream_name] = (
            self.errors_by_stream.get(stream_name, 0) + 1
        )
        self.last_error = f"{stream_name}:{type(exc).__name__}"
        self._recent.append(1)
        preview = str(raw)[:1_000].replace("\r", " ").replace("\n", " ")
        logger.warning(
            "%s message rejected stream=%r error=%s preview=%r",
            self.source,
            stream_name,
            type(exc).__name__,
            preview[:300],
        )
        try:
            _QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
            row = {
                "received_at_ms": int(time.time() * 1000),
                "source": self.source,
                "stream": stream_name,
                "error_type": type(exc).__name__,
                "error": str(exc)[:500],
                "preview": preview,
            }
            with (_QUARANTINE_DIR / f"{self.source}.jsonl").open(
                "a", encoding="utf-8"
            ) as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
        except OSError as quarantine_error:
            logger.error(
                "%s quarantine write failed: %s", self.source, quarantine_error
            )

    def snapshot(self, stale_after_ms: int = 15_000) -> dict:
        now = int(time.time() * 1000)
        age = (
            max(0, now - self.last_valid_ms)
            if self.last_valid_ms is not None
            else None
        )
        recent_rate = sum(self._recent) / max(1, len(self._recent))
        blockers = []
        if not self.connected:
            blockers.append("socket_disconnected")
        if age is None or age > stale_after_ms:
            blockers.append("valid_content_stale")
        if recent_rate > 0.01:
            blockers.append("parse_error_rate")
        return {
            "source": self.source,
            "connected": self.connected,
            "healthy": not blockers,
            "blockers": blockers,
            "messages": self.messages,
            "valid_messages": self.valid_messages,
            "unknown_messages": self.unknown_messages,
            "parse_errors": self.parse_errors,
            "recent_parse_error_rate": recent_rate,
            "errors_by_stream": dict(self.errors_by_stream),
            "last_valid_age_ms": age,
            "last_error": self.last_error,
        }


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
        self.protocol_health = _ProtocolHealth("binance_spot_ws")

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
                    url, ping_interval=WS_PING_INTERVAL_S, ping_timeout=WS_PING_TIMEOUT_S
                ) as ws:
                    logger.info("Connected to Binance WebSocket")
                    self.protocol_health.connected = True
                    self._emit("status", {"connected": True})
                    self.reconnect_delay = 1.0

                    async for message in ws:
                        stream = ""
                        self.protocol_health.message()
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
                                    canonical_kline(
                                        open_ts_ms=int(k["t"]),
                                        close_ts_ms=int(k["T"]),
                                        open_=float(k["o"]),
                                        high=float(k["h"]),
                                        low=float(k["l"]),
                                        close=float(k["c"]),
                                        volume=float(k["v"]),
                                        source="binance_ws",
                                        is_closed=bool(k["x"]),
                                        source_event_ts_ms=int(data.get("E") or k["t"]),
                                        received_ts_ms=int(time.time() * 1000),
                                        trades=k["n"],
                                    ),
                                )
                            else:
                                self.protocol_health.unknown()
                                continue
                            self.protocol_health.valid()
                        except Exception as e:
                            self.protocol_health.error(stream, message, e)

            except Exception as e:
                self.protocol_health.connected = False
                logger.warning(f"WebSocket disconnected: {e}")
                self._emit("status", {"connected": False})
                if self.running:
                    await asyncio.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(
                        self.reconnect_delay * 1.5, self.max_reconnect_delay
                    )

    def stop(self):
        self.running = False
        self.protocol_health.connected = False

    def health_snapshot(self) -> dict:
        return self.protocol_health.snapshot(stale_after_ms=10_000)


class BinanceFuturesWebSocketClient:
    """Connects to Binance Futures WebSocket stream for Liquidations."""

    FUTURES_WS = "wss://fstream.binance.com/stream"
    STREAMS = ["btcusdt@forceOrder", "btcusdt@aggTrade", "btcusdt@bookTicker"]

    def __init__(self):
        self.ws = None
        self.running = False
        self.callbacks: dict[str, list[Callable]] = {
            "liquidation": [],
            "status": [],
            "perp_bar": [],   # A4 parity: one finalized 1m perp-CVD bar per minute
            "book": [],
        }
        self.reconnect_delay = 1.0
        # A4 live PERP-CVD accumulator (parity twin of build_crossvenue_flow's per-bar CVD;
        # same sign convention: taker-buy (is_buyer_maker=False) positive).
        self._pb_ms = None   # None = no bar open yet (a real bar_ms is never None)
        self._pb_cvd = 0.0
        self._pb_vol = 0.0
        self._pb_last = 0.0
        self.last_perp_bar = None
        self.last_book = None
        self.last_book_receive_ts_ms = None
        self.book_message_count = 0
        self.last_agg_trade_receive_ts_ms = None
        self.agg_trade_message_count = 0
        self.protocol_health = _ProtocolHealth("binance_futures_ws")

    def on(self, event: str, callback: Callable):
        if event in self.callbacks:
            self.callbacks[event].append(callback)

    def _emit(self, event: str, data):
        for cb in self.callbacks.get(event, []):
            try:
                cb(data)
            except Exception as e:
                logger.error(f"Callback error for {event}: {e}")

    def health_snapshot(self, now_ms: int | None = None) -> dict:
        """Return observational feed health without fabricating missing inputs."""
        now = int(now_ms if now_ms is not None else time.time() * 1000)

        def age(ts):
            return max(0, now - int(ts)) if ts is not None else None

        return {
            **self.protocol_health.snapshot(stale_after_ms=10_000),
            "last_book_receive_ts_ms": self.last_book_receive_ts_ms,
            "book_message_count": int(self.book_message_count),
            "book_age_ms": age(self.last_book_receive_ts_ms),
            "last_agg_trade_receive_ts_ms": self.last_agg_trade_receive_ts_ms,
            "agg_trade_message_count": int(self.agg_trade_message_count),
            "agg_trade_age_ms": age(self.last_agg_trade_receive_ts_ms),
            "last_completed_perp_cvd_bar_ts_ms": (
                int(self.last_perp_bar["ts"]) if self.last_perp_bar else None
            ),
        }

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

    @staticmethod
    def _parse_book_ticker(data: dict, received_at_ms: int) -> dict:
        """Validate and normalize one public USD-M BTCUSDT bookTicker message."""
        book = {
            "symbol": str(data["s"]),
            "best_bid": float(data["b"]),
            "best_ask": float(data["a"]),
            "bid_size": float(data["B"]),
            "ask_size": float(data["A"]),
            "event_ts_ms": int(data.get("E") or data.get("T") or 0),
            "received_at_ms": int(received_at_ms),
            "update_id": int(data["u"]) if data.get("u") is not None else None,
        }
        numeric = (
            book["best_bid"],
            book["best_ask"],
            book["bid_size"],
            book["ask_size"],
        )
        if (
            book["symbol"] != "BTCUSDT"
            or not all(math.isfinite(value) for value in numeric)
            or book["best_bid"] <= 0
            or book["best_ask"] <= book["best_bid"]
            or book["bid_size"] < 0
            or book["ask_size"] < 0
            or book["event_ts_ms"] <= 0
            or book["received_at_ms"] <= 0
        ):
            raise ValueError("invalid BTCUSDT perpetual bookTicker")
        return book

    async def connect(self):
        self.running = True
        url = f"{self.FUTURES_WS}?streams={'/'.join(self.STREAMS)}"

        while self.running:
            try:
                async with websockets.connect(
                    url, ping_interval=WS_PING_INTERVAL_S, ping_timeout=WS_PING_TIMEOUT_S
                ) as ws:
                    logger.info("Connected to Binance Futures WebSocket")
                    self.protocol_health.connected = True
                    self._emit("status", {"connected": True})
                    self.reconnect_delay = 1.0

                    async for message in ws:
                        stream = ""
                        self.protocol_health.message()
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
                                self.last_agg_trade_receive_ts_ms = int(time.time() * 1000)
                                self.agg_trade_message_count += 1
                                self._ingest_perp_trade(
                                    float(data["p"]), float(data["q"]),
                                    bool(data["m"]), int(data["T"]))
                            elif stream == "btcusdt@bookTicker":
                                received_at_ms = int(time.time() * 1000)
                                book = self._parse_book_ticker(data, received_at_ms)
                                self.last_book = book
                                self.last_book_receive_ts_ms = received_at_ms
                                self.book_message_count += 1
                                self._emit("book", dict(book))
                            else:
                                self.protocol_health.unknown()
                                continue
                            self.protocol_health.valid()
                        except Exception as exc:
                            self.protocol_health.error(stream, message, exc)
            except Exception as e:
                self.protocol_health.connected = False
                logger.warning(f"Futures WebSocket disconnected: {e}")
                self._emit("status", {"connected": False})
                if self.running:
                    await asyncio.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(self.reconnect_delay * 1.5, 30.0)

    def stop(self):
        self.running = False
        self.protocol_health.connected = False


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
                canonical_kline(
                    open_ts_ms=int(k[0]),
                    close_ts_ms=int(k[6]),
                    open_=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                    source="binance_rest",
                    received_ts_ms=int(time.time() * 1000),
                    quote_volume=float(k[7]),
                    trades=int(k[8]),
                    taker_buy_base=float(k[9]),
                    taker_buy_quote=float(k[10]),
                )
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
        self.protocol_health = _ProtocolHealth("coinbase_ws")

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
                    self.WS_URL, ping_interval=WS_PING_INTERVAL_S, ping_timeout=WS_PING_TIMEOUT_S
                ) as ws:
                    logger.info("Connected to Coinbase WebSocket")
                    self.protocol_health.connected = True
                    self._emit("status", {"connected": True})
                    self.reconnect_delay = 1.0

                    await ws.send(json.dumps(subscribe_msg))

                    async for message in ws:
                        self.protocol_health.message()
                        message_type = "unknown"
                        try:
                            msg = json.loads(message)
                            message_type = str(msg.get("type") or "unknown")
                            if msg.get("type") == "ticker" and "price" in msg:
                                self._emit(
                                    "ticker",
                                    {
                                        "price": float(msg["price"]),
                                        "time": msg.get("time"),
                                        "receive_time": int(time.time() * 1000),
                                    },
                                )
                                self.protocol_health.valid()
                            else:
                                self.protocol_health.unknown()
                        except Exception as exc:
                            self.protocol_health.error(message_type, message, exc)

            except Exception as e:
                self.protocol_health.connected = False
                logger.warning(f"Coinbase WS disconnected: {e}")
                self._emit("status", {"connected": False})
                if self.running:
                    await asyncio.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(self.reconnect_delay * 1.5, 30.0)

    def stop(self):
        self.running = False
        self.protocol_health.connected = False

    def health_snapshot(self) -> dict:
        return self.protocol_health.snapshot(stale_after_ms=15_000)


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
                    url, ping_interval=WS_PING_INTERVAL_S, ping_timeout=WS_PING_TIMEOUT_S
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
        self.data = {"bybit": None, "kucoin": None, "time": 0,
                     "bybit_observed_ts": 0.0, "kucoin_observed_ts": 0.0}

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
        """Poll each venue and stamp EACH ONE with its own observation time.

        The client kept one shared `time` for both venues and retained the previous price on a
        failed poll (`= float(...) or self.data["bybit"]`). So a venue that stopped responding
        kept contributing its last price to the consensus median indefinitely, while the shared
        `time` was refreshed every cycle and made the whole client look current.

        A price is now only as fresh as ITS OWN successful fetch. The retain-on-failure
        behaviour is kept - a brief blip should not blank a venue - but it is now DATED, so a
        consumer can age it out.
        """
        _now = time.time()
        bybit = await self._get(self.BYBIT_URL)
        try:
            if bybit and bybit.get("retCode") == 0:
                result = bybit.get("result") or {}
                lst = result.get("list", []) if isinstance(result, dict) else []
                first = lst[0] if lst and isinstance(lst[0], dict) else {}
                if first:
                    _px = float(first.get("lastPrice", 0))
                    if _px:
                        self.data["bybit"] = _px
                        self.data["bybit_observed_ts"] = _now
        except Exception:
            pass
        kucoin = await self._get(self.KUCOIN_URL)
        try:
            if kucoin and kucoin.get("data"):
                _px = float(kucoin["data"].get("price", 0))
                if _px:
                    self.data["kucoin"] = _px
                    self.data["kucoin_observed_ts"] = _now
        except Exception:
            pass
        # Retained for back-compat. It means "when the client last POLLED", not "when either
        # price was last OBSERVED" - the distinction that made a dead venue look alive.
        self.data["time"] = time.time()
        return self.data

    async def close(self):
        if self.session and not self.session.closed:
            await self.session.close()
