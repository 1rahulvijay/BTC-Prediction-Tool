#!/usr/bin/env python
"""Forward-only 1h BTC fair-value and path recorder.

Public data only. This module has no credentials and no order-submission path.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.research.poly_1h_digital_fair_value_v1.core import (
    RoundPathState,
    digital_up_probability,
    fee_per_share,
    ladder_json,
    mixture_probability,
    normalized_market_probability,
    parse_book,
    realized_annualized_volatility,
    settled_side,
    vwap_with_fee,
)
from backend.research.poly_1h_digital_fair_value_v1.store import (
    FairValueStore,
)

PROTOCOL_PATH = Path(__file__).with_name("frozen_protocol.json")
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_DB = DATA / "research" / "poly_1h_digital_fair_value_v1" / "shadow.duckdb"
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
CLOB_BOOK_URL = "https://clob.polymarket.com/book"
CLOB_FEE_URL = "https://clob.polymarket.com/fee-rate/{token_id}"
CLOB_MARKET_URL = "https://clob.polymarket.com/markets/{condition_id}"
GAMMA_MARKET_URL = "https://gamma-api.polymarket.com/markets"
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"
BINANCE_AGG_TRADES_URL = "https://api.binance.com/api/v3/aggTrades"


def sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def parse_timestamp_ms(value: str) -> int:
    return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)


def load_protocol() -> dict[str, Any]:
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    if any(
        bool(protocol.get(key))
        for key in ("serving_enabled", "paper_enabled", "live_enabled", "may_submit_orders")
    ):
        raise RuntimeError("research-only protocol boundary was weakened")
    boundary = protocol["boundaries"]
    if any(
        bool(boundary.get(key))
        for key in (
            "may_read_credentials",
            "may_submit_orders",
            "may_change_champion",
            "may_change_paper_ledger",
            "may_change_live_ui",
        )
    ):
        raise RuntimeError("1h campaign boundary was weakened")
    if float(protocol["probability_baselines"]["residual_lambda"]) != 0.0:
        raise RuntimeError("residual model cannot be enabled in V1")
    return protocol


def _outcome_tokens(market: dict[str, Any]) -> tuple[str, str] | None:
    try:
        outcomes = json.loads(market.get("outcomes") or "[]")
        token_ids = json.loads(market.get("clobTokenIds") or "[]")
    except (TypeError, json.JSONDecodeError):
        return None
    if len(outcomes) != len(token_ids):
        return None
    mapped = {
        str(outcome).strip().lower(): str(token_id)
        for outcome, token_id in zip(outcomes, token_ids)
    }
    if mapped.get("up") and mapped.get("down"):
        return mapped["up"], mapped["down"]
    return None


def parse_hourly_event(
    event: dict[str, Any], protocol: dict[str, Any], now_ms: int
) -> dict[str, Any] | None:
    markets = event.get("markets") or []
    if (
        event.get("seriesSlug") != protocol["market"]["series_slug"]
        or len(markets) != 1
    ):
        return None
    market = markets[0]
    tokens = _outcome_tokens(market)
    if tokens is None:
        return None
    description = str(market.get("description") or event.get("description") or "")
    source = str(market.get("resolutionSource") or event.get("resolutionSource") or "")
    required_rule_parts = (
        'resolve to "Up" if the close price is greater than or equal to the open price',
        "BTC/USDT 1 hour candle",
        '"1H" candle',
    )
    if (
        not all(part in description for part in required_rule_parts)
        or "binance.com/en/trade/BTC_USDT" not in source
    ):
        return None
    try:
        candle_open_ms = parse_timestamp_ms(market["eventStartTime"])
        candle_close_ms = parse_timestamp_ms(market["endDate"])
        gamma_start_ms = parse_timestamp_ms(market["startDate"])
    except (KeyError, TypeError, ValueError):
        return None
    duration_ms = int(protocol["market"]["duration_seconds"]) * 1000
    if candle_close_ms - candle_open_ms != duration_ms:
        return None
    fee_schedule = market.get("feeSchedule") or {}
    return {
        "slug": str(market["slug"]),
        "market_id": str(market["id"]),
        "event_id": str(event.get("id") or ""),
        "condition_id": str(market["conditionId"]),
        "up_token_id": tokens[0],
        "down_token_id": tokens[1],
        "candle_open_ts_ms": candle_open_ms,
        "candle_close_ts_ms": candle_close_ms,
        "gamma_start_ts_ms": gamma_start_ms,
        "resolution_source": source,
        "rule_text": description,
        "rule_sha256": hashlib.sha256(description.encode()).hexdigest(),
        "gamma_fee_rate": (
            float(fee_schedule["rate"]) if fee_schedule.get("rate") is not None else None
        ),
        "gamma_fee_exponent": (
            float(fee_schedule["exponent"])
            if fee_schedule.get("exponent") is not None
            else None
        ),
        "gamma_taker_only": (
            bool(fee_schedule["takerOnly"])
            if fee_schedule.get("takerOnly") is not None
            else None
        ),
        "first_seen_ts_ms": int(now_ms),
        "last_seen_ts_ms": int(now_ms),
    }


async def get_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    params: dict[str, Any] | None = None,
) -> tuple[Any, int, float]:
    started = time.perf_counter()
    async with session.get(url, params=params) as response:
        response.raise_for_status()
        payload = await response.json()
    received_ms = int(time.time() * 1000)
    return payload, received_ms, (time.perf_counter() - started) * 1000.0


class OneHourFairValueShadow:
    def __init__(
        self,
        protocol: dict[str, Any],
        store: FairValueStore,
        *,
        duration_seconds: float = 0.0,
    ):
        self.protocol = protocol
        self.store = store
        self.duration_seconds = max(0.0, float(duration_seconds))
        self.started_monotonic = time.monotonic()
        self.markets: dict[str, dict[str, Any]] = {}
        self.path_states: dict[str, RoundPathState] = {}
        self.volatility: dict[str, tuple[float, float]] = {}
        self.last_discovery = 0.0
        self.last_vol_refresh: dict[str, float] = {}
        self.samples = 0
        self.store.set_meta("protocol", protocol)
        self.store.set_meta(
            "provenance",
            {
                "gamma_events": GAMMA_EVENTS_URL,
                "polymarket_book": CLOB_BOOK_URL,
                "polymarket_fee": CLOB_FEE_URL,
                "binance_klines": BINANCE_KLINES_URL,
                "binance_agg_trades": BINANCE_AGG_TRADES_URL,
                "protocol_sha256": hashlib.sha256(PROTOCOL_PATH.read_bytes()).hexdigest(),
                "code_sha256": sha256_json(
                    {
                        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in sorted(Path(__file__).parent.glob("*.py"))
                    }
                ),
            },
        )

    async def discover(self, session: aiohttp.ClientSession) -> None:
        params = {
            "series_id": self.protocol["market"]["gamma_series_id"],
            "active": "true",
            "closed": "false",
            "limit": 100,
            "order": "endDate",
            "ascending": "true",
        }
        events, _received, latency = await get_json(
            session, GAMMA_EVENTS_URL, params=params
        )
        now_ms = int(time.time() * 1000)
        selected: list[dict[str, Any]] = []
        for event in events:
            row = parse_hourly_event(event, self.protocol, now_ms)
            if row is None:
                continue
            if row["candle_open_ts_ms"] - 120_000 <= now_ms <= row["candle_close_ts_ms"] + 120_000:
                selected.append(row)
        for row in selected:
            up_fee, _up_received, _up_latency = await get_json(
                session, CLOB_FEE_URL.format(token_id=row["up_token_id"])
            )
            down_fee, _down_received, _down_latency = await get_json(
                session, CLOB_FEE_URL.format(token_id=row["down_token_id"])
            )
            row["up_base_fee_bps"] = int(up_fee["base_fee"])
            row["down_base_fee_bps"] = int(down_fee["base_fee"])
            existing = self.markets.get(row["slug"])
            if existing:
                row["first_seen_ts_ms"] = existing["first_seen_ts_ms"]
            self.markets[row["slug"]] = row
            self.store.market(row)
            if row["slug"] not in self.path_states and now_ms >= row["candle_open_ts_ms"]:
                open_price, finalized = await self.fetch_hour_kline(
                    session, row["candle_open_ts_ms"]
                )
                if open_price is None:
                    continue
                state = RoundPathState(open_price["open"])
                for timestamp, price in self.store.path_samples(row["slug"]):
                    state.update(timestamp, price)
                self.path_states[row["slug"]] = state
                await self.refresh_volatility(session, row, force=True)
                print(
                    f"[market] {row['slug']} open={open_price['open']:.2f} "
                    f"final={finalized} fee={row['up_base_fee_bps']}/{row['down_base_fee_bps']}bps",
                    flush=True,
                )
        retained = {row["slug"] for row in selected}
        for slug in list(self.markets):
            if slug in retained:
                continue
            self.markets.pop(slug, None)
            self.path_states.pop(slug, None)
            self.volatility.pop(slug, None)
            self.last_vol_refresh.pop(slug, None)
        self.last_discovery = time.monotonic()
        self.store.health(
            "gamma",
            "OK" if selected else "NO_ACTIVE_HOURLY_MARKET",
            f"eligible={len(selected)} events={len(events)}",
            latency_ms=latency,
        )

    async def fetch_hour_kline(
        self, session: aiohttp.ClientSession, open_ts_ms: int
    ) -> tuple[dict[str, float] | None, bool]:
        payload, _received, _latency = await get_json(
            session,
            BINANCE_KLINES_URL,
            params={
                "symbol": self.protocol["market"]["symbol"],
                "interval": "1h",
                "startTime": int(open_ts_ms),
                "limit": 1,
            },
        )
        if not payload or int(payload[0][0]) != int(open_ts_ms):
            return None, False
        row = payload[0]
        return (
            {
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
                "close_ts_ms": int(row[6]),
            },
            int(row[6]) < int(time.time() * 1000),
        )

    async def refresh_volatility(
        self,
        session: aiohttp.ClientSession,
        market: dict[str, Any],
        *,
        force: bool = False,
    ) -> None:
        slug = market["slug"]
        now = time.monotonic()
        if not force and now - self.last_vol_refresh.get(slug, 0.0) < 60.0:
            return
        settings = self.protocol["probability_baselines"]
        slow_window = int(settings["slow_volatility_minutes"])
        payload, _received, latency = await get_json(
            session,
            BINANCE_KLINES_URL,
            params={
                "symbol": self.protocol["market"]["symbol"],
                "interval": "1m",
                "limit": slow_window + 1,
            },
        )
        closes = [float(row[4]) for row in payload]
        lower = float(settings["minimum_annualized_volatility"])
        upper = float(settings["maximum_annualized_volatility"])
        slow = realized_annualized_volatility(
            closes[-(slow_window + 1) :],
            minimum=lower,
            maximum=upper,
        )
        fast_window = int(settings["fast_volatility_minutes"])
        fast = realized_annualized_volatility(
            closes[-(fast_window + 1) :],
            minimum=lower,
            maximum=upper,
        )
        self.volatility[slug] = (slow, fast)
        self.last_vol_refresh[slug] = now
        self.store.health(
            "binance_volatility",
            "OK",
            f"{slug} slow={slow:.4f} fast={fast:.4f}",
            latency_ms=latency,
        )

    async def fetch_market_inputs(
        self, session: aiohttp.ClientSession, market: dict[str, Any]
    ) -> tuple[Any, Any, dict[str, Any], int, float]:
        up_task = get_json(
            session, CLOB_BOOK_URL, params={"token_id": market["up_token_id"]}
        )
        down_task = get_json(
            session, CLOB_BOOK_URL, params={"token_id": market["down_token_id"]}
        )
        btc_task = get_json(
            session,
            BINANCE_AGG_TRADES_URL,
            params={"symbol": self.protocol["market"]["symbol"], "limit": 1},
        )
        up_raw, down_raw, btc_raw = await asyncio.gather(up_task, down_task, btc_task)
        up_book = parse_book(up_raw[0], up_raw[1], up_raw[2])
        down_book = parse_book(down_raw[0], down_raw[1], down_raw[2])
        if not btc_raw[0]:
            raise ValueError("Binance aggregate-trade response is empty")
        trade = btc_raw[0][-1]
        return up_book, down_book, trade, btc_raw[1], btc_raw[2]

    def _vwap_payload(
        self, market: dict[str, Any], up_book: Any, down_book: Any
    ) -> str:
        output: dict[str, Any] = {}
        for quantity in self.protocol["economics"]["quantities"]:
            for name, book in (("up", up_book), ("down", down_book)):
                base_fee_bps = market[f"{name}_base_fee_bps"]
                buy_price, buy_fill, buy_fee = vwap_with_fee(
                    book.asks, quantity, base_fee_bps
                )
                sell_price, sell_fill, sell_fee = vwap_with_fee(
                    book.bids, quantity, base_fee_bps
                )
                output[f"{name}_{quantity:g}"] = {
                    "minimum_order_size": book.minimum_order_size,
                    "order_size_eligible": quantity + 1e-12
                    >= book.minimum_order_size,
                    "buy_vwap": buy_price,
                    "buy_fill": buy_fill,
                    "buy_fee_per_share": buy_fee,
                    "sell_vwap": sell_price,
                    "sell_fill": sell_fill,
                    "sell_fee_per_share": sell_fee,
                }
        return json.dumps(output, separators=(",", ":"), sort_keys=True)

    async def sample_market(
        self, session: aiohttp.ClientSession, market: dict[str, Any]
    ) -> None:
        now_ms = int(time.time() * 1000)
        if not (
            market["candle_open_ts_ms"] <= now_ms < market["candle_close_ts_ms"]
        ):
            return
        state = self.path_states.get(market["slug"])
        if state is None:
            return
        await self.refresh_volatility(session, market)
        up_book, down_book, trade, btc_received_ms, btc_latency = (
            await self.fetch_market_inputs(session, market)
        )
        observed_ms = max(
            up_book.receive_timestamp_ms,
            down_book.receive_timestamp_ms,
            btc_received_ms,
        )
        source_ts_ms = int(trade["T"])
        btc_age_ms = max(0, observed_ms - source_ts_ms)
        pair_skew_ms = abs(
            up_book.receive_timestamp_ms - down_book.receive_timestamp_ms
        )
        invalid_reasons = []
        sampling = self.protocol["sampling"]
        for name, book in (("up", up_book), ("down", down_book)):
            receive_age_ms = max(0, observed_ms - book.receive_timestamp_ms)
            if not book.exchange_timestamp_ms:
                invalid_reasons.append(f"{name}_missing_exchange_timestamp")
            elif receive_age_ms > float(sampling["maximum_quote_age_seconds"]) * 1000.0:
                invalid_reasons.append(f"{name}_stale_book")
            if book.neg_risk:
                invalid_reasons.append(f"{name}_neg_risk")
        if pair_skew_ms > float(sampling["maximum_pair_receive_skew_ms"]):
            invalid_reasons.append("pair_receive_skew")
        if btc_age_ms > float(sampling["maximum_binance_age_seconds"]) * 1000.0:
            invalid_reasons.append("stale_binance_trade")
        open_price = state.open_price
        btc_price = float(trade["p"])
        seconds_elapsed = max(
            0.0, (observed_ms - market["candle_open_ts_ms"]) / 1000.0
        )
        seconds_left = max(
            0.0, (market["candle_close_ts_ms"] - observed_ms) / 1000.0
        )
        features = state.update(observed_ms / 1000.0, btc_price)
        slow_vol, fast_vol = self.volatility[market["slug"]]
        probability = self.protocol["probability_baselines"]
        jump_vol = min(
            float(probability["maximum_annualized_volatility"]),
            fast_vol * float(probability["jump_volatility_multiplier"]),
        )
        weights = probability["mixture_weights"]
        p_b = digital_up_probability(
            btc_price,
            open_price,
            seconds_left,
            slow_vol,
            probability_clip=float(probability["probability_clip"]),
        )
        p_c = mixture_probability(
            btc_price,
            open_price,
            seconds_left,
            [fast_vol, slow_vol, jump_vol],
            [weights["fast"], weights["slow"], weights["jump"]],
            probability_clip=float(probability["probability_clip"]),
        )
        p_a = normalized_market_probability(up_book.midpoint, down_book.midpoint)
        up_fee_rate = float(market["up_base_fee_bps"]) / 10_000.0
        down_fee_rate = float(market["down_base_fee_bps"]) / 10_000.0
        row = {
            "slug": market["slug"],
            "observed_second": observed_ms // 1000,
            "observed_ts_ms": observed_ms,
            "candle_open_ts_ms": market["candle_open_ts_ms"],
            "candle_close_ts_ms": market["candle_close_ts_ms"],
            "seconds_elapsed": seconds_elapsed,
            "seconds_left": seconds_left,
            "binance_open": open_price,
            "binance_price": btc_price,
            "binance_distance_bps": (btc_price / open_price - 1.0) * 10_000.0,
            "binance_price_source": "binance_spot_aggtrade",
            "binance_source_ts_ms": source_ts_ms,
            "binance_age_ms": btc_age_ms,
            "slow_volatility": slow_vol,
            "fast_volatility": fast_vol,
            "jump_volatility": jump_vol,
            "p_a_market": p_a,
            "p_b_distance_time": p_b,
            "p_c_volatility_mixture": p_c,
            "up_bid": up_book.bid,
            "up_ask": up_book.ask,
            "up_mid": up_book.midpoint,
            "up_spread": up_book.spread,
            "up_bid_size": up_book.bid_size,
            "up_ask_size": up_book.ask_size,
            "down_bid": down_book.bid,
            "down_ask": down_book.ask,
            "down_mid": down_book.midpoint,
            "down_spread": down_book.spread,
            "down_bid_size": down_book.bid_size,
            "down_ask_size": down_book.ask_size,
            "up_book_ts_ms": up_book.exchange_timestamp_ms,
            "down_book_ts_ms": down_book.exchange_timestamp_ms,
            "up_receive_latency_ms": up_book.receive_latency_ms,
            "down_receive_latency_ms": down_book.receive_latency_ms,
            "pair_receive_skew_ms": pair_skew_ms,
            "up_fee_rate": up_fee_rate,
            "down_fee_rate": down_fee_rate,
            "up_fee_at_ask": fee_per_share(
                up_book.ask, market["up_base_fee_bps"]
            ),
            "down_fee_at_ask": fee_per_share(
                down_book.ask, market["down_base_fee_bps"]
            ),
            "up_ladder_json": ladder_json(up_book),
            "down_ladder_json": ladder_json(down_book),
            "vwap_json": self._vwap_payload(market, up_book, down_book),
            **features,
            "valid": not invalid_reasons,
            "invalid_reason": ",".join(sorted(set(invalid_reasons))) or None,
        }
        if self.store.snapshot(row):
            self.samples += 1
            if self.samples % 30 == 0:
                print(
                    f"[sample] n={self.samples} {market['slug']} "
                    f"left={seconds_left:.0f}s d={row['binance_distance_bps']:+.1f}bps "
                    f"A/B/C={p_a:.3f}/{p_b:.3f}/{p_c:.3f} "
                    f"valid={row['valid']}",
                    flush=True,
                )
        if invalid_reasons or self.samples % 30 == 0:
            self.store.health(
                "sample",
                "OK" if not invalid_reasons else "INVALID",
                row["invalid_reason"] or market["slug"],
                age_ms=btc_age_ms,
                latency_ms=max(
                    btc_latency,
                    up_book.receive_latency_ms,
                    down_book.receive_latency_ms,
                ),
            )

    async def _polymarket_winner(
        self, session: aiohttp.ClientSession, market: dict[str, Any]
    ) -> tuple[str | None, str | None]:
        try:
            payload, _received, _latency = await get_json(
                session,
                CLOB_MARKET_URL.format(condition_id=market["condition_id"]),
            )
            if payload.get("closed"):
                winners = [
                    str(token.get("outcome") or "").upper()
                    for token in payload.get("tokens") or []
                    if token.get("winner")
                ]
                if len(winners) == 1 and winners[0] in {"UP", "DOWN"}:
                    return winners[0], "polymarket_clob"
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            self.store.health(
                "polymarket_resolution",
                "CLOB_ERROR",
                f"{market.get('slug')} {type(exc).__name__}: {exc}",
            )
        try:
            payload, _received, _latency = await get_json(
                session, GAMMA_MARKET_URL, params={"slug": market["slug"], "closed": "true"}
            )
            if payload:
                item = payload[0]
                outcomes = json.loads(item.get("outcomes") or "[]")
                prices = [float(value) for value in json.loads(item.get("outcomePrices") or "[]")]
                if item.get("closed") and len(outcomes) == len(prices) == 2:
                    winner = max(range(2), key=prices.__getitem__)
                    side = str(outcomes[winner]).upper()
                    if prices[winner] >= 0.99 and side in {"UP", "DOWN"}:
                        return side, "polymarket_gamma"
        except (
            aiohttp.ClientError,
            asyncio.TimeoutError,
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            self.store.health(
                "polymarket_resolution",
                "GAMMA_ERROR",
                f"{market.get('slug')} {type(exc).__name__}: {exc}",
            )
        return None, None

    async def resolve_due(self, session: aiohttp.ClientSession) -> None:
        now_ms = int(time.time() * 1000)
        for due in self.store.unresolved_markets(now_ms - 2000):
            market = self.markets.get(due["slug"], due)
            kline, finalized = await self.fetch_hour_kline(
                session, due["candle_open_ts_ms"]
            )
            if not kline or not finalized:
                continue
            side = settled_side(kline["open"], kline["close"])
            poly_side, poly_source = await self._polymarket_winner(session, market)
            sides_match = side == poly_side if poly_side is not None else None
            self.store.resolution(
                {
                    **due,
                    "finalized_open": kline["open"],
                    "finalized_high": kline["high"],
                    "finalized_low": kline["low"],
                    "finalized_close": kline["close"],
                    "finalized_volume": kline["volume"],
                    "binance_side": side,
                    "polymarket_side": poly_side,
                    "polymarket_resolution_source": poly_source,
                    "sides_match": sides_match,
                    "finalized_kline": True,
                    "resolved_ts_ms": now_ms,
                }
            )
            status = "MATCH" if sides_match else "PENDING" if poly_side is None else "MISMATCH"
            self.store.health(
                "resolution",
                status,
                f"{due['slug']} binance={side} polymarket={poly_side}",
            )
            print(
                f"[resolve] {due['slug']} Binance={side} Polymarket={poly_side or 'pending'}",
                flush=True,
            )

    def disk_limit_reached(self) -> bool:
        limit = int(self.protocol["sampling"]["maximum_database_bytes"])
        current = sum(
            path.stat().st_size
            for path in (self.store.path, Path(f"{self.store.path}.wal"))
            if path.exists()
        )
        return current >= limit

    async def run(self) -> None:
        timeout = aiohttp.ClientTimeout(total=12)
        headers = {"User-Agent": "btc-poly-1h-fair-value-shadow/1.0"}
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
            while True:
                if self.disk_limit_reached():
                    raise RuntimeError("database safety cap reached")
                if (
                    not self.last_discovery
                    or time.monotonic() - self.last_discovery
                    >= float(self.protocol["sampling"]["discovery_interval_seconds"])
                ):
                    await self.discover(session)
                await self.resolve_due(session)
                active = list(self.markets.values())
                for market in active:
                    try:
                        await self.sample_market(session, market)
                    except Exception as exc:  # noqa: BLE001 - isolate one sample
                        self.store.health(
                            "sample",
                            "ERROR",
                            f"{market.get('slug')} {type(exc).__name__}: {exc}",
                        )
                        print(
                            f"[sample] {type(exc).__name__}: {exc}",
                            flush=True,
                        )
                if (
                    self.duration_seconds
                    and time.monotonic() - self.started_monotonic >= self.duration_seconds
                ):
                    break
                await asyncio.sleep(float(self.protocol["sampling"]["interval_seconds"]))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--smoke", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    protocol = load_protocol()
    duration = 5.0 if args.smoke and args.duration <= 0.0 else args.duration
    store = FairValueStore(args.db)
    try:
        shadow = OneHourFairValueShadow(
            protocol, store, duration_seconds=duration
        )
        print(
            "POLY_1H_DIGITAL_FAIR_VALUE_V1 research shadow; "
            "public data only; no orders",
            flush=True,
        )
        asyncio.run(shadow.run())
        print(f"[done] counts={store.counts()}", flush=True)
        return 0
    except KeyboardInterrupt:
        print("[stop] interrupted", flush=True)
        return 130
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
