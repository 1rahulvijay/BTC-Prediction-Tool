"""Causal adapter over the existing in-process Binance futures feed."""
from __future__ import annotations

from collections import deque
import math
import threading
import time
from typing import Callable

from .config import EngineConfig
from .schemas import DataQuality, MarketSnapshot


class BinancePaperMarketAdapter:
    """Build typed snapshots without opening another socket or imputing data."""

    def __init__(
        self,
        futures_client,
        derivatives_provider: Callable[[], dict] | None,
        config: EngineConfig,
    ):
        self.futures_client = futures_client
        self.derivatives_provider = derivatives_provider or (lambda: {})
        self.config = config
        self._lock = threading.RLock()
        self._last_book: dict | None = None
        self._samples: deque[tuple[int, float, int]] = deque(maxlen=900)

    def ingest_book(self, book: dict) -> None:
        bid = float(book["best_bid"])
        ask = float(book["best_ask"])
        bid_size = float(book["bid_size"])
        ask_size = float(book["ask_size"])
        event_ts_ms = int(book["event_ts_ms"])
        received_at_ms = int(book["received_at_ms"])
        if (
            book.get("symbol") != "BTCUSDT"
            or not all(math.isfinite(value) for value in (bid, ask, bid_size, ask_size))
            or bid <= 0
            or ask <= bid
            or bid_size < 0
            or ask_size < 0
            or event_ts_ms <= 0
            or received_at_ms <= 0
        ):
            raise ValueError("invalid BTCUSDT perpetual book")
        normalized = dict(book)
        normalized.update(
            {
                "best_bid": bid,
                "best_ask": ask,
                "bid_size": bid_size,
                "ask_size": ask_size,
                "event_ts_ms": event_ts_ms,
                "received_at_ms": received_at_ms,
            }
        )
        with self._lock:
            self._last_book = normalized
            last_sample_ms = self._samples[-1][0] if self._samples else None
            if (
                last_sample_ms is None
                or received_at_ms - last_sample_ms >= self.config.sample_interval_ms
            ):
                health = self.futures_client.health_snapshot(received_at_ms)
                self._samples.append(
                    (
                        received_at_ms,
                        (bid + ask) / 2.0,
                        int(health["agg_trade_message_count"]),
                    )
                )

    def _funding(self) -> tuple[float | None, int | None]:
        try:
            derivatives = self.derivatives_provider() or {}
            value = derivatives.get("funding_rate")
            if isinstance(value, dict):
                rate = value.get("rate")
                timestamp = value.get("time")
                return (
                    float(rate) if rate is not None else None,
                    int(timestamp) if timestamp is not None else None,
                )
            if value is not None:
                return float(value), None
        except (TypeError, ValueError):
            return None, None
        return None, None

    def snapshot(self, now_ms: int | None = None) -> MarketSnapshot | None:
        now = int(now_ms if now_ms is not None else time.time() * 1000)
        with self._lock:
            if self._last_book is None:
                return None
            book = dict(self._last_book)
            samples = tuple(self._samples)
        health = self.futures_client.health_snapshot(now)
        received_at_ms = int(book["received_at_ms"])
        age_ms = max(0, now - received_at_ms)
        feed_health = (
            DataQuality.HEALTHY
            if age_ms <= self.config.quote_stale_ms
            else DataQuality.STALE
        )
        bid = float(book["best_bid"])
        ask = float(book["best_ask"])
        mark = (bid + ask) / 2.0
        spread = ask - bid
        funding_rate, funding_time_ms = self._funding()
        cutoff = now - 60_000
        eligible = [sample for sample in samples if sample[0] >= cutoff]
        trade_count_60s = None
        if len(eligible) >= 2:
            trade_count_60s = max(0, eligible[-1][2] - eligible[0][2])
        agg_age = health.get("agg_trade_age_ms")
        agg_available = (
            agg_age is not None
            and agg_age <= self.config.quote_stale_ms
            and trade_count_60s is not None
        )
        return MarketSnapshot(
            symbol="BTCUSDT",
            event_ts_ms=int(book.get("event_ts_ms") or 0),
            received_at_ms=received_at_ms,
            mark_price=mark,
            best_bid=bid,
            best_ask=ask,
            bid_size=float(book["bid_size"]),
            ask_size=float(book["ask_size"]),
            spread=spread,
            spread_bps=spread / mark * 10_000.0,
            feed_age_ms=age_ms,
            feed_health=feed_health,
            update_id=book.get("update_id"),
            funding_rate=funding_rate,
            funding_time_ms=funding_time_ms,
            agg_trade_age_ms=agg_age,
            agg_trade_message_count=int(health["agg_trade_message_count"]),
            agg_trade_count_60s=trade_count_60s,
            last_completed_perp_cvd_bar_ts_ms=health.get(
                "last_completed_perp_cvd_bar_ts_ms"
            ),
            mid_history=tuple(sample[1] for sample in samples),
            sample_ts_history=tuple(sample[0] for sample in samples),
            feature_availability={
                "perpetual_book": feed_health is DataQuality.HEALTHY,
                "perpetual_mid_history": len(samples) >= 2,
                "perpetual_trade_intensity": bool(agg_available),
                "funding_rate": funding_rate is not None,
            },
            source_identifiers={
                "book": "binance_futures_ws_bookTicker",
                "trade_activity": "binance_futures_ws_aggTrade",
                "funding": "binance_futures_public_rest",
            },
        )
