"""Canonical Polymarket public-market client used by the live application.

The market WebSocket is an incremental protocol. A `book` event establishes state and
`price_change` events mutate individual levels. Treating every message as a full snapshot leaves
deleted orders in memory and creates false spreads/depth. This client therefore reuses the tested
Decimal L2 book, sends the required application heartbeat, refreshes rolling subscriptions and
surfaces content health independently from socket liveness.

No value from a market question is parsed into a trading reference price. Price-to-beat authority
lives in the dedicated oracle/round tracker, not in human-readable market text.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import math
import time
from datetime import datetime
from typing import Any

import requests
import websockets

from polymarket.l2_book import L2Book

logger = logging.getLogger(__name__)

GAMMA_EVENTS_URL = (
    "https://gamma-api.polymarket.com/events?active=true&closed=false&limit=100"
)
CLOB_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
HEARTBEAT_SECONDS = 10.0
DISCOVERY_SECONDS = 30.0
MAX_TRACKED_MARKETS = 10
STALE_AFTER_SECONDS = 30.0


def _json_list(value: Any) -> list:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _field(payload: dict, snake: str, camel: str | None = None, default=None):
    if snake in payload:
        return payload.get(snake)
    if camel and camel in payload:
        return payload.get(camel)
    return default


def _timestamp_ms(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        numeric = float(value)
        if not math.isfinite(numeric):
            return 0
        if numeric < 100_000_000_000:
            numeric *= 1000.0
        return int(numeric)
    except (TypeError, ValueError):
        try:
            return int(
                datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
                * 1000
            )
        except (TypeError, ValueError):
            return 0


class PolymarketClient:
    """Read-only market-state client. It has no pricing, sizing or order authority."""

    def __init__(self):
        self.markets: dict[str, dict] = {}
        self.orderbooks: dict[str, L2Book] = {}
        self.market_stats: dict[str, dict] = {}
        self.ws = None
        self.tracked_tokens: set[str] = set()
        self.pending_add: set[str] = set()
        self.pending_remove: set[str] = set()
        self.connected = False
        self.messages = 0
        self.valid_events = 0
        self.parse_errors = 0
        self.unknown_events = 0
        self.increment_before_snapshot = 0
        self.last_message_ts = 0.0
        self.last_valid_book_ts = 0.0
        self.last_error = ""

    @staticmethod
    def _is_btc_text(*values: Any) -> bool:
        text = " ".join(str(value or "") for value in values).lower()
        return "bitcoin" in text or "btc" in text

    @staticmethod
    def _explicit_reference_price(market: dict) -> float | None:
        """Only structured numeric metadata is admissible; question text is never parsed."""
        for key in ("line", "groupItemThreshold", "group_item_threshold"):
            value = market.get(key)
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number) and number > 0:
                return number
        return None

    @classmethod
    def _market_from_gamma(cls, event: dict, market: dict) -> dict | None:
        tokens = _json_list(
            market.get("clobTokenIds") or market.get("clob_token_ids")
        )
        outcomes = _json_list(market.get("outcomes"))
        if len(tokens) < 2 or len(outcomes) < 2:
            return None
        normalized = [str(value).strip().lower() for value in outcomes]
        pairs = (("yes", "no"), ("up", "down"))
        selected = next(
            (
                (normalized.index(left), normalized.index(right))
                for left, right in pairs
                if left in normalized and right in normalized
            ),
            None,
        )
        if selected is None:
            return None
        left_index, right_index = selected
        if not cls._is_btc_text(
            event.get("title"),
            event.get("ticker"),
            market.get("question"),
            market.get("slug"),
        ):
            return None
        reference = cls._explicit_reference_price(market)
        return {
            "id": str(market.get("id") or market.get("conditionId") or ""),
            "market_id": str(market.get("id") or ""),
            "condition_id": str(
                market.get("conditionId") or market.get("condition_id") or ""
            ),
            "event_id": str(event.get("id") or ""),
            "question": str(market.get("question") or event.get("title") or ""),
            "description": str(
                market.get("description") or event.get("description") or ""
            ),
            "resolution_rules": str(
                market.get("rules")
                or market.get("resolutionCriteria")
                or market.get("description")
                or ""
            ),
            "resolution_source": str(
                market.get("resolutionSource")
                or market.get("resolution_source")
                or ""
            ),
            "slug": str(market.get("slug") or ""),
            "start_date": market.get("startDate") or market.get("start_date"),
            "end_date": market.get("endDate") or market.get("end_date"),
            "yes_token": str(tokens[left_index]),
            "no_token": str(tokens[right_index]),
            "yes_outcome": str(outcomes[left_index]),
            "no_outcome": str(outcomes[right_index]),
            "reference_price": reference,
            "reference_source": "structured_line" if reference is not None else "none",
            "spread": market.get("spread"),
            "tick_size": market.get("orderPriceMinTickSize")
            or market.get("order_price_min_tick_size"),
            "minimum_order_size": market.get("orderMinSize")
            or market.get("minimum_order_size"),
            "fees_enabled": market.get("feesEnabled"),
            "fee_schedule": market.get("feeSchedule"),
            "taker_base_fee": market.get("takerBaseFee"),
            "active": bool(market.get("active", True)),
            "resolved": bool(market.get("closed", False)),
        }

    @staticmethod
    def _end_timestamp(market: dict) -> float:
        raw = market.get("end_date")
        if not raw:
            return float("inf")
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return float("inf")

    def _install_markets(self, market_rows: list[dict]) -> None:
        selected = sorted(market_rows, key=self._end_timestamp)[:MAX_TRACKED_MARKETS]
        replacement: dict[str, dict] = {}
        for market in selected:
            replacement[market["yes_token"]] = market
            replacement[market["no_token"]] = market
        old_tokens = set(self.markets)
        new_tokens = set(replacement)
        self.markets = replacement
        self.pending_add.update(new_tokens - old_tokens)
        self.pending_remove.update(old_tokens - new_tokens)
        for token in old_tokens - new_tokens:
            self.orderbooks.pop(token, None)
        try:
            import database

            for market in selected:
                database.log_polymarket_market(market)
        except Exception as exc:  # database failure must not corrupt in-memory book state
            logger.warning("Polymarket market metadata persistence failed: %s", exc)

    def discover_markets(self) -> list[dict]:
        """Fetch active BTC markets and atomically replace the tracked token set."""
        try:
            response = requests.get(GAMMA_EVENTS_URL, timeout=10)
            response.raise_for_status()
            events = response.json()
            candidates: list[dict] = []
            for event in events if isinstance(events, list) else []:
                if not isinstance(event, dict):
                    continue
                for raw_market in event.get("markets") or []:
                    if not isinstance(raw_market, dict):
                        continue
                    if not raw_market.get("active") or raw_market.get("closed"):
                        continue
                    parsed = self._market_from_gamma(event, raw_market)
                    if parsed is not None:
                        candidates.append(parsed)
            self._install_markets(candidates)
            logger.info(
                "Discovered %s BTC markets, tracking top %s",
                len(candidates),
                len(self.markets) // 2,
            )
            return sorted(candidates, key=self._end_timestamp)[:MAX_TRACKED_MARKETS]
        except Exception as exc:
            self.last_error = f"discovery:{type(exc).__name__}"
            logger.error("Failed to discover Polymarket markets: %s", exc)
            return []

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            await ws.send("PING")

    async def _refresh_subscriptions(self, ws) -> None:
        while True:
            await asyncio.sleep(DISCOVERY_SECONDS)
            await asyncio.to_thread(self.discover_markets)
            add = sorted(self.pending_add - self.tracked_tokens)
            remove = sorted(self.pending_remove & self.tracked_tokens)
            if add:
                await ws.send(
                    json.dumps(
                        {
                            "assets_ids": add,
                            "operation": "subscribe",
                            "custom_feature_enabled": True,
                        }
                    )
                )
            if remove:
                await ws.send(
                    json.dumps({"assets_ids": remove, "operation": "unsubscribe"})
                )
            self.tracked_tokens.update(add)
            self.tracked_tokens.difference_update(remove)
            self.pending_add.difference_update(add)
            self.pending_remove.difference_update(remove)

    async def connect_ws(self):
        """Maintain a canonical market stream with heartbeat and rolling subscriptions."""
        while True:
            heartbeat = None
            refresh = None
            try:
                if not self.markets:
                    await asyncio.to_thread(self.discover_markets)
                token_ids = sorted(self.markets)
                if not token_ids:
                    logger.warning("No Polymarket markets to subscribe to; retrying discovery.")
                    await asyncio.sleep(10)
                    continue
                async with websockets.connect(
                    CLOB_WS_URL,
                    ping_interval=None,
                    ping_timeout=None,
                    open_timeout=15,
                    close_timeout=5,
                    max_size=8 * 1024 * 1024,
                ) as ws:
                    self.ws = ws
                    self.connected = True
                    self.tracked_tokens = set(token_ids)
                    self.pending_add.difference_update(token_ids)
                    await ws.send(
                        json.dumps(
                            {
                                "assets_ids": token_ids,
                                "type": "market",
                                "custom_feature_enabled": True,
                            }
                        )
                    )
                    logger.info(
                        "Subscribed to canonical Polymarket market stream (%s assets)",
                        len(token_ids),
                    )
                    heartbeat = asyncio.create_task(self._heartbeat(ws))
                    refresh = asyncio.create_task(self._refresh_subscriptions(ws))
                    async for raw in ws:
                        if raw in ("PONG", b"PONG"):
                            continue
                        self.last_message_ts = time.time()
                        self.messages += 1
                        try:
                            message = json.loads(raw)
                            self.handle_ws_message(message)
                        except Exception as exc:
                            self._record_parse_error(raw, exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"socket:{type(exc).__name__}"
                logger.error("Polymarket WS error: %s; reconnecting in 5s", exc)
                await asyncio.sleep(5)
            finally:
                self.connected = False
                self.ws = None
                for task in (heartbeat, refresh):
                    if task is not None:
                        task.cancel()
                if heartbeat is not None or refresh is not None:
                    await asyncio.gather(
                        *(task for task in (heartbeat, refresh) if task is not None),
                        return_exceptions=True,
                    )

    def _record_parse_error(self, raw: Any, exc: Exception) -> None:
        self.parse_errors += 1
        self.last_error = f"parse:{type(exc).__name__}"
        preview = str(raw)[:500].replace("\r", " ").replace("\n", " ")
        logger.warning(
            "Polymarket message rejected type=%s error=%s preview=%r",
            type(raw).__name__,
            type(exc).__name__,
            preview,
        )

    @staticmethod
    def _normalize_event(message: dict) -> tuple[str, dict]:
        payload = message.get("payload")
        if isinstance(payload, dict) and (
            message.get("topic") == "market" or message.get("type")
        ):
            return str(
                payload.get("event_type")
                or payload.get("eventType")
                or message.get("type")
                or ""
            ), payload
        return str(
            message.get("event_type")
            or message.get("eventType")
            or message.get("type")
            or ""
        ), message

    def handle_ws_message(self, message: Any) -> int:
        if isinstance(message, list):
            return sum(
                self.handle_ws_message(item)
                for item in message
                if isinstance(item, dict)
            )
        if not isinstance(message, dict):
            self.unknown_events += 1
            return 0
        try:
            event_type, payload = self._normalize_event(message)
            handled = self._apply_event(event_type, payload)
            if handled:
                self.valid_events += handled
            elif event_type:
                self.unknown_events += 1
            return handled
        except Exception as exc:
            self._record_parse_error(message, exc)
            return 0

    def _asset_id(self, payload: dict) -> str:
        return str(
            _field(payload, "asset_id", "assetId")
            or payload.get("token_id")
            or payload.get("tokenId")
            or ""
        )

    def _apply_event(self, event_type: str, payload: dict) -> int:
        event = event_type.lower()
        if event == "book" or (not event and ("bids" in payload or "asks" in payload)):
            return self._apply_book(payload)
        if event == "price_change":
            return self._apply_price_changes(payload)
        if event == "last_trade_price":
            return self._apply_last_trade(payload)
        if event == "best_bid_ask":
            return self._apply_best_bid_ask(payload)
        if event == "tick_size_change":
            return self._apply_tick_size(payload)
        if event == "new_market":
            return self._apply_new_market(payload)
        if event == "market_resolved":
            return self._apply_market_resolved(payload)
        return 0

    def _apply_book(self, payload: dict) -> int:
        asset = self._asset_id(payload)
        if not asset or asset not in self.markets:
            return 0
        book = self.orderbooks.setdefault(asset, L2Book(asset))
        book.load_snapshot(
            payload.get("bids") or [],
            payload.get("asks") or [],
            market=str(payload.get("market") or ""),
            exchange_ts_ms=_timestamp_ms(payload.get("timestamp")),
            recv_ts_ns=time.time_ns(),
            book_hash=str(payload.get("hash") or ""),
        )
        market = self.markets[asset]
        tick = _field(payload, "tick_size", "tickSize")
        minimum = _field(payload, "min_order_size", "minOrderSize")
        if tick is not None:
            market["tick_size"] = tick
        if minimum is not None:
            market["minimum_order_size"] = minimum
        if not book.valid:
            return 0
        self.last_valid_book_ts = time.time()
        self._calculate_stats(asset)
        return 1

    def _apply_price_changes(self, payload: dict) -> int:
        updates = _field(payload, "price_changes", "priceChanges", []) or []
        exchange_ms = _timestamp_ms(payload.get("timestamp"))
        handled = 0
        for update in updates:
            if not isinstance(update, dict):
                continue
            asset = self._asset_id(update)
            if not asset or asset not in self.markets:
                continue
            book = self.orderbooks.setdefault(asset, L2Book(asset))
            if not book.synchronized:
                self.increment_before_snapshot += 1
                continue
            book.apply_price_change(
                str(update.get("side") or ""),
                update.get("price"),
                update.get("size", 0),
                exchange_ts_ms=exchange_ms,
                recv_ts_ns=time.time_ns(),
            )
            if book.valid:
                self.last_valid_book_ts = time.time()
                self._calculate_stats(asset)
                handled += 1
        return handled

    def _apply_last_trade(self, payload: dict) -> int:
        asset = self._asset_id(payload)
        if not asset or asset not in self.markets:
            return 0
        market = self.markets[asset]
        market["last_trade_price"] = payload.get("price")
        market["last_trade_side"] = payload.get("side")
        market["fee_rate_bps"] = _field(payload, "fee_rate_bps", "feeRateBps")
        market["last_trade_ts_ms"] = _timestamp_ms(payload.get("timestamp"))
        return 1

    def _apply_best_bid_ask(self, payload: dict) -> int:
        asset = self._asset_id(payload)
        if not asset or asset not in self.markets:
            return 0
        market = self.markets[asset]
        market["reported_best_bid"] = _field(payload, "best_bid", "bestBid")
        market["reported_best_ask"] = _field(payload, "best_ask", "bestAsk")
        market["reported_spread"] = payload.get("spread")
        market["best_quote_ts_ms"] = _timestamp_ms(payload.get("timestamp"))
        # Top-of-book events are diagnostic. Full-depth stats remain authoritative only after
        # a snapshot plus causally applied increments.
        return 1

    def _apply_tick_size(self, payload: dict) -> int:
        asset = self._asset_id(payload)
        if not asset or asset not in self.markets:
            return 0
        self.markets[asset]["tick_size"] = _field(
            payload, "new_tick_size", "newTickSize"
        )
        return 1

    def _apply_new_market(self, payload: dict) -> int:
        event_message = payload.get("event_message") or payload.get("eventMessage")
        if not self._is_btc_text(
            payload.get("question"),
            payload.get("slug"),
            event_message.get("title") if isinstance(event_message, dict) else "",
        ):
            return 0
        tokens = (
            _field(payload, "assets_ids", "tokenIds")
            or payload.get("assetsIds")
            or payload.get("token_ids")
            or payload.get("clobTokenIds")
            or []
        )
        outcomes = payload.get("outcomes") or []
        synthetic = self._market_from_gamma(
            event_message or {},
            {
                **payload,
                "id": payload.get("id") or payload.get("market"),
                "conditionId": _field(payload, "condition_id", "conditionId"),
                "clobTokenIds": tokens,
                "outcomes": outcomes,
                "active": payload.get("active", True),
                "closed": False,
            },
        )
        if synthetic is None:
            return 0
        for token in (synthetic["yes_token"], synthetic["no_token"]):
            self.markets[token] = synthetic
            self.pending_add.add(token)
        return 1

    def _apply_market_resolved(self, payload: dict) -> int:
        tokens = (
            _field(payload, "assets_ids", "tokenIds")
            or payload.get("assetsIds")
            or payload.get("token_ids")
            or []
        )
        winning = str(
            _field(payload, "winning_asset_id", "winningAssetId")
            or payload.get("winningTokenId")
            or ""
        )
        handled = 0
        for raw in tokens:
            token = str(raw)
            market = self.markets.get(token)
            if market is None:
                continue
            market["resolved"] = True
            market["winning_token"] = winning
            market["winning_outcome"] = _field(
                payload, "winning_outcome", "winningOutcome"
            )
            self.pending_remove.add(token)
            handled += 1
        return handled

    def _calculate_stats(self, asset_id: str) -> None:
        market = self.markets[asset_id]
        market_id = market["id"]
        is_yes = asset_id == market["yes_token"]
        book = self.orderbooks[asset_id]
        summary = book.summary()
        bid_depth = float(summary["bid_depth"])
        ask_depth = float(summary["ask_depth"])
        total_depth = bid_depth + ask_depth
        imbalance = (
            (bid_depth - ask_depth) / total_depth if total_depth > 0 else 0.0
        )
        stats = self.market_stats.setdefault(
            market_id,
            {
                "id": market_id,
                "condition_id": market.get("condition_id"),
                "event_id": market.get("event_id"),
                "slug": market.get("slug"),
            },
        )
        prefix = "yes" if is_yes else "no"
        stats.update(
            {
                f"{prefix}_best_bid": summary["best_bid"],
                f"{prefix}_best_ask": summary["best_ask"],
                f"{prefix}_spread": summary["spread"],
                f"{prefix}_imbalance": imbalance,
                f"{prefix}_depth": total_depth,
                f"{prefix}_book_valid": bool(summary["valid"]),
                "timestamp": time.time(),
                "question": market["question"],
                "reference_price": market.get("reference_price"),
                "reference_source": market.get("reference_source", "none"),
                "end_date": market.get("end_date"),
                "tick_size": market.get("tick_size"),
                "minimum_order_size": market.get("minimum_order_size"),
            }
        )
        if (
            stats.get("yes_book_valid")
            and stats.get("no_book_valid")
            and stats.get("yes_best_bid") is not None
            and stats.get("no_best_bid") is not None
        ):
            try:
                import database

                database.log_polymarket_quote(
                    {
                        "market_id": market_id,
                        "yes_best_bid": stats.get("yes_best_bid"),
                        "yes_best_ask": stats.get("yes_best_ask"),
                        "no_best_bid": stats.get("no_best_bid"),
                        "no_best_ask": stats.get("no_best_ask"),
                        "yes_spread": stats.get("yes_spread"),
                        "yes_imbalance": stats.get("yes_imbalance"),
                    }
                )
            except Exception as exc:
                logger.warning("Polymarket quote persistence failed: %s", exc)

    def status(self) -> dict:
        now = time.time()
        message_age = now - self.last_message_ts if self.last_message_ts else None
        book_age = (
            now - self.last_valid_book_ts if self.last_valid_book_ts else None
        )
        parse_rate = self.parse_errors / max(1, self.messages)
        stale = book_age is None or book_age > STALE_AFTER_SECONDS
        healthy = (
            self.connected
            and not stale
            and parse_rate <= 0.01
            and self.increment_before_snapshot <= max(5, self.messages // 100)
        )
        blockers = []
        if not self.connected:
            blockers.append("socket_disconnected")
        if stale:
            blockers.append("valid_book_stale")
        if parse_rate > 0.01:
            blockers.append("parse_error_rate")
        if self.increment_before_snapshot > max(5, self.messages // 100):
            blockers.append("increments_without_snapshot")
        return {
            "connected": self.connected,
            "healthy": healthy,
            "blockers": blockers,
            "tracked_markets": len(self.markets) // 2,
            "tracked_tokens": len(self.tracked_tokens),
            "messages": self.messages,
            "valid_events": self.valid_events,
            "parse_errors": self.parse_errors,
            "parse_error_rate": parse_rate,
            "unknown_events": self.unknown_events,
            "increment_before_snapshot": self.increment_before_snapshot,
            "last_message_age_s": message_age,
            "last_valid_book_age_s": book_age,
            "last_error": self.last_error,
        }

    def get_summary(self):
        return copy.deepcopy(list(self.market_stats.values()))
