"""Causal adapter over the existing in-process Binance futures feed."""
from __future__ import annotations

from collections import deque
import math
import threading
import time
from typing import Callable

from .config import EngineConfig
from .schemas import DataQuality, MarketSnapshot
from .sample_window import (continuity as _continuity,
                            contiguous_tail as _contiguous_tail,
                            trade_count_window as _trade_count_window,
                            window as _sample_window)


class BinancePaperMarketAdapter:
    """Build typed snapshots without opening another socket or imputing data."""

    def __init__(
        self,
        futures_client,
        derivatives_provider: Callable[[], dict] | None,
        config: EngineConfig,
        model_context_provider: Callable[[], dict] | None = None,
    ):
        self.futures_client = futures_client
        self.derivatives_provider = derivatives_provider or (lambda: {})
        self.model_context_provider = model_context_provider or (lambda: {})
        self.config = config
        self._lock = threading.RLock()
        self._last_book: dict | None = None
        #: Books dropped for arriving out of EXCHANGE order. Counted, not silently discarded:
        #: a rising number means the feed is reordering, which is itself a health signal.
        self.stale_book_drops = 0
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
            # MONOTONIC EXCHANGE ORDER. `_last_book` was overwritten on every message, so a
            # delayed or out-of-order event replaced a NEWER one and then became the executable
            # quote - with `received_at_ms = now` making it look perfectly fresh.
            #
            # Ordering is judged on the EXCHANGE's clock, never on ours: arrival order is a
            # fact about the network, event order is a fact about the market. `update_id` is
            # preferred where the venue supplies it, because two events can share a millisecond.
            previous = self._last_book
            if previous is not None:
                prev_update = previous.get("update_id")
                this_update = normalized.get("update_id")
                if prev_update is not None and this_update is not None:
                    if int(this_update) <= int(prev_update):
                        self.stale_book_drops += 1
                        return
                elif event_ts_ms < int(previous.get("event_ts_ms") or 0):
                    self.stale_book_drops += 1
                    return
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

    def _model_context(self) -> dict:
        try:
            value = self.model_context_provider() or {}
            return dict(value) if isinstance(value, dict) else {}
        except Exception:
            return {}

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
        model_context = self._model_context()
        model_predictions = model_context.get("predictions") or {}
        if isinstance(model_predictions, dict):
            model_prediction = (
                model_predictions.get(5) or model_predictions.get("5") or {}
            )
        else:
            model_prediction = {}
        model_prediction = model_prediction if isinstance(model_prediction, dict) else {}
        # #4: `agg_trade_count_60s` used to be published whenever TWO samples fell inside the
        # last minute, with no requirement that they SPAN it. Two samples a second apart at
        # startup produced a "60-second" count, which mean reversion compares against an
        # absolute 1,700 and breakout against a minimum of 20 - so warm-up read as a dead
        # market. The count is now NULL until the window it names has actually elapsed.
        trade_window = _trade_count_window(samples, now, 60.0)
        trade_count_60s = trade_window["count"]

        # #3: strategies read mid_history POSITIONALLY and none of them read the timestamps, so
        # a feed outage looked like adjacent one-second samples. Rather than patching four
        # strategies, the coverage verdict is folded into the availability flag they already
        # gate on - a gapped history now presents as missing history, which every strategy
        # already handles by returning NO_DATA.
        mid_window = _sample_window(
            [s[1] for s in samples], [s[0] for s in samples], now, 60.0)
        # CONTINUITY, not 60s coverage. A strategy with a 30-sample lookback is well served by
        # 35 contiguous seconds; demanding a full minute would block good decisions. What
        # breaks them is a HOLE, and that is visible over the retained buffer even after it
        # scrolls out of the last minute.
        mid_continuity = _continuity([s[1] for s in samples], [s[0] for s in samples])
        _contiguous_mids, _contiguous_ts = _contiguous_tail(
            [s[1] for s in samples], [s[0] for s in samples])
        history_usable = mid_continuity["continuous"]
        history_reason = mid_continuity["reason"]
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
            source_age_ms=max(0, now - int(book.get("event_ts_ms") or 0)),
            transport_lag_ms=max(0, received_at_ms - int(book.get("event_ts_ms") or 0)),
            feed_health=feed_health,
            update_id=book.get("update_id"),
            funding_rate=funding_rate,
            funding_time_ms=funding_time_ms,
            agg_trade_age_ms=agg_age,
            agg_trade_message_count=int(health["agg_trade_message_count"]),
            agg_trade_count_60s=trade_count_60s,
            agg_trade_coverage_seconds=trade_window["coverage_seconds"],
            agg_trades_per_second=trade_window["trades_per_second"],
            agg_trade_window_complete=trade_window["window_complete"],
            mid_history_coverage_ratio=mid_window["coverage_ratio"],
            # From CONTINUITY, not from the 60s window: the trimmed window only holds
            # post-outage samples, so it would report a 1s gap while the buffer holds a
            # five-minute hole. Reporting a different number from the one the decision
            # uses is how a guard becomes unfalsifiable.
            mid_history_max_gap_ms=mid_continuity["max_gap_ms"],
            mid_history_usable=history_usable,
            mid_history_unusable_reason=history_reason,
            last_completed_perp_cvd_bar_ts_ms=health.get(
                "last_completed_perp_cvd_bar_ts_ms"
            ),
            # Only the CONTIGUOUS TAIL is published. Strategies check `len(history)`, not any
            # availability flag, so truncating here is what turns an outage into a refusal
            # through the length check they already perform - and none of them can forget to.
            mid_history=_contiguous_mids,
            sample_ts_history=_contiguous_ts,
            feature_availability={
                "perpetual_book": feed_health is DataQuality.HEALTHY,
                "perpetual_mid_history": len(samples) >= 2 and history_usable,
                "perpetual_trade_intensity": bool(agg_available),
                "funding_rate": funding_rate is not None,
                "ensemble_prediction": bool(model_prediction),
                "live_probability_calibration": (
                    model_prediction.get("calibratedConfidence") is not None
                ),
                "model_bundle_identity": bool(
                    model_context.get("model_trained")
                    and model_prediction.get("model_bundle_id")
                ),
            },
            source_identifiers={
                "book": "binance_futures_ws_bookTicker",
                "trade_activity": "binance_futures_ws_aggTrade",
                "funding": "binance_futures_public_rest",
                "model_context": "main_ensemble_final_decision",
            },
            model_context=model_context,
        )
