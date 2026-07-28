#!/usr/bin/env python
"""Forward-only complete-set arbitrage shadow over public Polymarket L2 books."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
import websockets

from backend.polymarket.l2_book import L2Book
from backend.polymarket.live_btc_updown_recorder import discover_rounds
from backend.research.poly_complete_set_arbitrage_v1.economics import (
    PairEvaluation,
    capacity_summary,
    evaluate_pair,
    fee_rate_from_base_bps,
    staggered_pair_net,
)
from backend.research.poly_complete_set_arbitrage_v1.shadow_store import (
    CompleteSetStore,
)

ROOT = Path(__file__).resolve().parents[3]
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_DB = (
    DATA / "research" / "poly_complete_set_arbitrage_v1" / "shadow.duckdb"
)
PROTOCOL_PATH = Path(__file__).with_name("frozen_protocol.json")
WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
FEE_URL = "https://clob.polymarket.com/fee-rate/{token_id}"
BOOK_URL = "https://clob.polymarket.com/book"


def _exchange_timestamp_ms(value: Any) -> int:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError):
        return 0
    if parsed <= 0:
        return 0
    if parsed < 1e11:
        parsed *= 1000.0
    return int(parsed)


def load_protocol() -> dict[str, Any]:
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    boundaries = protocol["boundaries"]
    if any(
        bool(protocol.get(key))
        for key in (
            "serving_enabled",
            "paper_enabled",
            "live_enabled",
            "order_submission_enabled",
        )
    ):
        raise RuntimeError("research-only protocol boundary was weakened")
    if any(
        bool(boundaries.get(key))
        for key in (
            "may_submit_orders",
            "may_read_credentials",
            "may_change_champion",
            "may_change_paper_ledger",
            "may_change_live_ui",
        )
    ):
        raise RuntimeError("complete-set boundary was weakened")
    return protocol


@dataclass(frozen=True)
class FeeQuote:
    token_id: str
    base_fee_bps: int
    fee_rate: float
    minimum_order_size: float
    tick_size: float
    neg_risk: bool
    fetched_at: float
    source: str = "clob_fee_rate_and_book_endpoints"


class FeeResolver:
    def __init__(self, maximum_age_seconds: float):
        self.maximum_age_seconds = float(maximum_age_seconds)
        self.cache: dict[str, FeeQuote] = {}
        self.http = requests.Session()
        self.http.headers.update(
            {"User-Agent": "btc-complete-set-arbitrage-shadow/1.0"}
        )

    def get(self, token_id: str, now: float | None = None) -> FeeQuote | None:
        now = float(now or time.time())
        cached = self.cache.get(str(token_id))
        if cached and now - cached.fetched_at <= self.maximum_age_seconds:
            return cached
        try:
            response = self.http.get(
                FEE_URL.format(token_id=token_id), timeout=8
            )
            response.raise_for_status()
            base_fee_bps = int(response.json()["base_fee"])
            book_response = self.http.get(
                BOOK_URL, params={"token_id": token_id}, timeout=8
            )
            book_response.raise_for_status()
            book_payload = book_response.json()
            quote = FeeQuote(
                token_id=str(token_id),
                base_fee_bps=base_fee_bps,
                fee_rate=fee_rate_from_base_bps(base_fee_bps),
                minimum_order_size=float(book_payload["min_order_size"]),
                tick_size=float(book_payload["tick_size"]),
                neg_risk=bool(book_payload.get("neg_risk")),
                fetched_at=now,
            )
            self.cache[str(token_id)] = quote
            return quote
        except Exception as exc:  # noqa: BLE001 - missing fees must fail closed
            print(
                f"[fee] token={str(token_id)[:12]} {type(exc).__name__}: {exc}",
                flush=True,
            )
            return None


@dataclass
class ActiveOpportunity:
    opportunity_id: str
    direction: str
    quantity: float
    slug: str
    started_ts_ns: int
    entry: PairEvaluation
    completed_delays: set[int]
    gap_open: bool = True


class CompleteSetShadow:
    def __init__(
        self,
        protocol: dict[str, Any],
        store: CompleteSetStore,
        *,
        duration_seconds: float = 0.0,
    ):
        self.protocol = protocol
        self.store = store
        self.duration_seconds = max(0.0, float(duration_seconds))
        self.books: dict[str, L2Book] = {}
        self.rounds: dict[str, dict[str, Any]] = {}
        self.asset_to_slug: dict[str, str] = {}
        self.tracked: set[str] = set()
        self.last_snapshot_ns: dict[str, int] = {}
        self.last_pair_hash: dict[str, tuple[str, str]] = {}
        self.active: dict[str, ActiveOpportunity] = {}
        self.open_by_key: dict[tuple[str, str, float], str] = {}
        self.started_monotonic = time.monotonic()
        self.messages = 0
        self.fees = FeeResolver(
            protocol["book"]["maximum_fee_age_seconds"]
        )
        self.store.set_meta("protocol", protocol)
        self.store.set_meta(
            "fee_provenance",
            {
                "endpoint": FEE_URL,
                "book_endpoint": BOOK_URL,
                "conversion": "base_fee_bps / 10000",
                "unknown_fee_behavior": "fail_closed",
            },
        )

    async def discover(self) -> set[str]:
        rows = await asyncio.to_thread(discover_rounds)
        now = time.time()
        selected = [
            row
            for row in rows
            if int(row["horizon"]) in self.protocol["horizons_minutes"]
            and row["start_ts"] <= now + 120
            and row["end_ts"] >= now - 30
        ]
        wanted: set[str] = set()
        for raw in selected:
            row = dict(raw)
            up_fee, down_fee = await asyncio.gather(
                asyncio.to_thread(self.fees.get, str(row["up"]), now),
                asyncio.to_thread(self.fees.get, str(row["down"]), now),
            )
            row["up_fee"] = up_fee
            row["down_fee"] = down_fee
            row["last_seen_at"] = now
            row["fee_fetched_at"] = (
                min(up_fee.fetched_at, down_fee.fetched_at)
                if up_fee and down_fee
                else None
            )
            row["up_base_fee_bps"] = (
                up_fee.base_fee_bps if up_fee else None
            )
            row["down_base_fee_bps"] = (
                down_fee.base_fee_bps if down_fee else None
            )
            row["up_min_order_size"] = (
                up_fee.minimum_order_size if up_fee else None
            )
            row["down_min_order_size"] = (
                down_fee.minimum_order_size if down_fee else None
            )
            row["up_tick_size"] = up_fee.tick_size if up_fee else None
            row["down_tick_size"] = down_fee.tick_size if down_fee else None
            row["neg_risk"] = (
                bool(up_fee.neg_risk or down_fee.neg_risk)
                if up_fee and down_fee
                else None
            )
            self.rounds[row["slug"]] = row
            for key in ("up", "down"):
                asset = str(row[key])
                wanted.add(asset)
                self.asset_to_slug[asset] = row["slug"]
            self.store.market(row)
        return wanted

    async def refresh_subscriptions(self, socket: Any) -> None:
        refresh = max(
            5.0, float(self.protocol["book"]["subscription_refresh_seconds"])
        )
        while True:
            await asyncio.sleep(refresh)
            wanted = await self.discover()
            add = sorted(wanted - self.tracked)
            remove = sorted(self.tracked - wanted)
            if add:
                await socket.send(
                    json.dumps(
                        {
                            "assets_ids": add,
                            "operation": "subscribe",
                            "custom_feature_enabled": True,
                        }
                    )
                )
            if remove:
                await socket.send(
                    json.dumps(
                        {"assets_ids": remove, "operation": "unsubscribe"}
                    )
                )
            self.tracked = wanted
            if add or remove:
                print(
                    f"[shadow] subscriptions +{len(add)} -{len(remove)} "
                    f"active={len(wanted)}",
                    flush=True,
                )

    async def heartbeat(self, socket: Any) -> None:
        while True:
            await asyncio.sleep(10.0)
            await socket.send("PING")

    def _qualification(
        self,
        row: dict[str, Any],
        up: L2Book,
        down: L2Book,
        now_ns: int,
    ) -> tuple[bool, str | None, dict[str, float]]:
        up_age = (now_ns - up.last_recv_ts_ns) / 1e6
        down_age = (now_ns - down.last_recv_ts_ns) / 1e6
        recv_skew = abs(up.last_recv_ts_ns - down.last_recv_ts_ns) / 1e6
        exchange_skew = abs(
            int(up.last_exchange_ts_ms) - int(down.last_exchange_ts_ms)
        )
        timing = {
            "up_recv_age_ms": up_age,
            "down_recv_age_ms": down_age,
            "receive_skew_ms": recv_skew,
            "exchange_skew_ms": float(exchange_skew),
        }
        if not up.valid or not down.valid:
            return False, "invalid_or_one_sided_book", timing
        if not up.last_exchange_ts_ms or not down.last_exchange_ts_ms:
            return False, "missing_exchange_timestamp", timing
        up_fee = row.get("up_fee")
        down_fee = row.get("down_fee")
        if up_fee is None or down_fee is None:
            return False, "unknown_fee", timing
        if up_fee.neg_risk or down_fee.neg_risk:
            return False, "negative_risk_market", timing
        fee_age = time.time() - min(up_fee.fetched_at, down_fee.fetched_at)
        if fee_age > self.protocol["book"]["maximum_fee_age_seconds"]:
            return False, "stale_fee", timing
        if max(up_age, down_age) > self.protocol["book"]["maximum_book_age_ms"]:
            return False, "stale_book", timing
        if recv_skew > self.protocol["book"]["maximum_pair_receive_skew_ms"]:
            return False, "receive_skew", timing
        if exchange_skew > self.protocol["book"]["maximum_pair_exchange_skew_ms"]:
            return False, "exchange_skew", timing
        now = now_ns / 1e9
        if not row["start_ts"] <= now <= row["end_ts"]:
            return False, "outside_round_window", timing
        return True, None, timing

    def _evaluate(
        self,
        row: dict[str, Any],
        up: L2Book,
        down: L2Book,
    ) -> tuple[dict[str, dict[str, PairEvaluation]], dict[str, Any], dict[str, Any]]:
        economics = self.protocol["economics"]
        up_fee: FeeQuote | None = row.get("up_fee")
        down_fee: FeeQuote | None = row.get("down_fee")
        if up_fee is None or down_fee is None:
            return {}, {}, {}
        size_results: dict[str, dict[str, PairEvaluation]] = {}
        for quantity in self.protocol["position_sizes"]:
            size_results[str(quantity)] = {}
            for direction in ("BUY_BOTH_MERGE", "SPLIT_SELL_BOTH"):
                size_results[str(quantity)][direction] = evaluate_pair(
                    up,
                    down,
                    direction,
                    float(quantity),
                    up_fee.fee_rate,
                    down_fee.fee_rate,
                    safety_margin_per_pair=economics[
                        "safety_margin_per_pair_usd"
                    ],
                    fixed_operational_cost=economics[
                        "fixed_operational_cost_usd"
                    ],
                )
        common = {
            "up_fee_rate": up_fee.fee_rate,
            "down_fee_rate": down_fee.fee_rate,
            "safety_margin_per_pair": economics[
                "safety_margin_per_pair_usd"
            ],
            "fixed_operational_cost": economics[
                "fixed_operational_cost_usd"
            ],
            "minimum_quantity": max(
                float(economics["minimum_order_size"]),
                float(up_fee.minimum_order_size),
                float(down_fee.minimum_order_size),
            ),
        }
        buy_capacity = capacity_summary(
            up, down, "BUY_BOTH_MERGE", **common
        )
        sell_capacity = capacity_summary(
            up, down, "SPLIT_SELL_BOTH", **common
        )
        return size_results, buy_capacity, sell_capacity

    def _stress_active(
        self,
        row: dict[str, Any],
        snapshot_id: str,
        now_ns: int,
        qualified: bool,
        reject_reason: str | None,
        size_results: dict[str, dict[str, PairEvaluation]],
    ) -> None:
        economics = self.protocol["economics"]
        targets = {int(value) for value in self.protocol["delay_stress_ms"]}
        for opportunity_id, active in list(self.active.items()):
            if active.slug != row["slug"]:
                continue
            elapsed_ms = (now_ns - active.started_ts_ns) / 1e6
            current = size_results.get(str(active.quantity), {}).get(
                active.direction
            )
            for target in self.protocol["delay_stress_ms"]:
                if target in active.completed_delays or elapsed_ms < target:
                    continue
                staggered = (
                    staggered_pair_net(
                        active.direction,
                        active.quantity,
                        active.entry,
                        current,
                        safety_margin_per_pair=economics[
                            "safety_margin_per_pair_usd"
                        ],
                        fixed_operational_cost=economics[
                            "fixed_operational_cost_usd"
                        ],
                    )
                    if current is not None
                    else {
                        "up_first_net_usd": None,
                        "down_first_net_usd": None,
                        "failed_leg_worst_net_usd": None,
                    }
                )
                current_net = (
                    current.conservative_net_usd
                    if current is not None and current.complete
                    else None
                )
                survives = bool(
                    qualified
                    and current_net is not None
                    and current_net > 0.0
                )
                self.store.delay_stress(
                    {
                        "opportunity_id": active.opportunity_id,
                        "target_delay_ms": int(target),
                        "actual_delay_ms": elapsed_ms,
                        "snapshot_id": snapshot_id,
                        "qualified": qualified,
                        "reject_reason": reject_reason,
                        "current_pair_net_usd": current_net,
                        "survives_positive": survives,
                        **staggered,
                        "evaluation": (
                            current.to_dict()
                            if current is not None
                            else {"reject_reason": "missing_evaluation"}
                        ),
                    }
                )
                active.completed_delays.add(int(target))

            still_positive = bool(
                qualified
                and current is not None
                and current.complete
                and current.conservative_net_usd is not None
                and current.conservative_net_usd > 0.0
            )
            key = (active.slug, active.direction, active.quantity)
            if active.gap_open:
                if still_positive:
                    self.store.touch_opportunity(
                        active.opportunity_id,
                        snapshot_id,
                        float(current.conservative_net_usd),
                    )
                else:
                    self.store.close_opportunity(
                        active.opportunity_id,
                        now_ns,
                        snapshot_id,
                        reject_reason or "economic_gap_closed",
                    )
                    active.gap_open = False
                    self.open_by_key.pop(key, None)
            if not active.gap_open and targets.issubset(active.completed_delays):
                self.active.pop(opportunity_id, None)

    def _open_new(
        self,
        row: dict[str, Any],
        snapshot_id: str,
        now_ns: int,
        qualified: bool,
        size_results: dict[str, dict[str, PairEvaluation]],
    ) -> None:
        if not qualified:
            return
        inventory_verified = bool(
            self.protocol["economics"]["prefunded_inventory_verified"]
        )
        minimum_quantity = max(
            float(self.protocol["economics"]["minimum_order_size"]),
            float(row["up_fee"].minimum_order_size),
            float(row["down_fee"].minimum_order_size),
        )
        for quantity_text, directions in size_results.items():
            quantity = float(quantity_text)
            if quantity < minimum_quantity:
                continue
            for direction, value in directions.items():
                key = (row["slug"], direction, quantity)
                if key in self.open_by_key:
                    continue
                if (
                    not value.complete
                    or value.conservative_net_usd is None
                    or value.conservative_net_usd <= 0.0
                ):
                    continue
                inventory_required = direction == "SPLIT_SELL_BOTH"
                execution_class = (
                    "BOOK_EXECUTABLE_INVENTORY_REQUIRED"
                    if inventory_required and not inventory_verified
                    else "BOOK_EXECUTABLE_TWO_LEG_NONATOMIC"
                )
                opportunity_id = uuid.uuid4().hex
                active = ActiveOpportunity(
                    opportunity_id=opportunity_id,
                    direction=direction,
                    quantity=quantity,
                    slug=row["slug"],
                    started_ts_ns=now_ns,
                    entry=value,
                    completed_delays=set(),
                )
                self.active[opportunity_id] = active
                self.open_by_key[key] = opportunity_id
                self.store.open_opportunity(
                    {
                        "opportunity_id": opportunity_id,
                        "direction": direction,
                        "quantity": quantity,
                        "slug": row["slug"],
                        "condition_id": row["condition_id"],
                        "horizon": row["horizon"],
                        "started_ts_ns": now_ns,
                        "first_snapshot_id": snapshot_id,
                        "entry_raw_net_usd": value.raw_net_usd,
                        "entry_conservative_net_usd": value.conservative_net_usd,
                        "execution_class": execution_class,
                        # Public-book survival is not proof that both real orders fill.
                        "promotion_eligible": False,
                        "entry_evaluation": value.to_dict(),
                    }
                )
                print(
                    f"[gap] {row['horizon']}m {direction} q={quantity:g} "
                    f"net=${value.conservative_net_usd:.4f} "
                    f"class={execution_class}",
                    flush=True,
                )

    def evaluate_slug(self, slug: str, now_ns: int) -> None:
        row = self.rounds.get(slug)
        if row is None:
            return
        up = self.books.get(str(row["up"]))
        down = self.books.get(str(row["down"]))
        if up is None or down is None:
            return
        minimum_ns = int(self.protocol["book"]["sample_interval_ms"] * 1e6)
        previous = self.last_snapshot_ns.get(slug, 0)
        if previous and now_ns - previous < minimum_ns:
            return
        pair_hash = (up.book_hash, down.book_hash)
        if pair_hash == self.last_pair_hash.get(slug):
            return
        self.last_snapshot_ns[slug] = now_ns
        self.last_pair_hash[slug] = pair_hash

        qualified, reject_reason, timing = self._qualification(
            row, up, down, now_ns
        )
        size_results, buy_capacity, sell_capacity = self._evaluate(row, up, down)
        snapshot_id = uuid.uuid4().hex
        raw_buy_gap = (
            1.0 - float(up.best_ask) - float(down.best_ask)
            if up.best_ask is not None and down.best_ask is not None
            else None
        )
        raw_sell_gap = (
            float(up.best_bid) + float(down.best_bid) - 1.0
            if up.best_bid is not None and down.best_bid is not None
            else None
        )
        self.store.snapshot(
            {
                "snapshot_id": snapshot_id,
                "observed_ts_ns": now_ns,
                "slug": slug,
                "condition_id": row["condition_id"],
                "horizon": row["horizon"],
                "seconds_left": max(0.0, row["end_ts"] - now_ns / 1e9),
                "qualified": qualified,
                "reject_reason": reject_reason,
                **timing,
                "up_book_hash": up.book_hash,
                "down_book_hash": down.book_hash,
                "up_base_fee_bps": row.get("up_base_fee_bps"),
                "down_base_fee_bps": row.get("down_base_fee_bps"),
                "raw_buy_gap_usd": raw_buy_gap,
                "raw_sell_gap_usd": raw_sell_gap,
                "size_results": {
                    quantity: {
                        direction: value.to_dict()
                        for direction, value in directions.items()
                    }
                    for quantity, directions in size_results.items()
                },
                "buy_capacity": buy_capacity,
                "sell_capacity": sell_capacity,
            }
        )
        self._stress_active(
            row,
            snapshot_id,
            now_ns,
            qualified,
            reject_reason,
            size_results,
        )
        self._open_new(
            row, snapshot_id, now_ns, qualified, size_results
        )

    def process(self, payload: Any, recv_ns: int | None = None) -> int:
        recv_ns = int(recv_ns or time.time_ns())
        if isinstance(payload, list):
            return sum(
                self.process(item, recv_ns)
                for item in payload
                if isinstance(item, dict)
            )
        if not isinstance(payload, dict):
            return 0
        event_type = str(payload.get("event_type", "unknown"))
        exchange_ms = _exchange_timestamp_ms(payload.get("timestamp"))
        market = str(payload.get("market", ""))
        asset = str(payload.get("asset_id", ""))
        touched: set[str] = set()
        if event_type == "book" and asset:
            book = self.books.setdefault(asset, L2Book(asset))
            book.load_snapshot(
                payload.get("bids", []),
                payload.get("asks", []),
                market=market,
                exchange_ts_ms=exchange_ms,
                recv_ts_ns=recv_ns,
                book_hash=str(payload.get("hash", "")),
            )
            if asset in self.asset_to_slug:
                touched.add(self.asset_to_slug[asset])
        elif event_type == "price_change":
            for update in payload.get("price_changes", []):
                update_asset = str(update.get("asset_id", ""))
                if not update_asset:
                    continue
                book = self.books.setdefault(update_asset, L2Book(update_asset))
                try:
                    book.apply_price_change(
                        str(update.get("side", "")).upper(),
                        update.get("price"),
                        update.get("size", 0),
                        exchange_ts_ms=exchange_ms,
                        recv_ts_ns=recv_ns,
                    )
                except ValueError:
                    continue
                if update_asset in self.asset_to_slug:
                    touched.add(self.asset_to_slug[update_asset])
        for slug in touched:
            self.evaluate_slug(slug, recv_ns)
        return len(touched)

    async def session(self) -> None:
        wanted = await self.discover()
        if not wanted:
            raise RuntimeError("no current BTC 5m/15m markets discovered")
        self.tracked = wanted
        async with websockets.connect(
            WS_URL,
            ping_interval=None,
            ping_timeout=None,
            open_timeout=15,
            close_timeout=5,
            max_size=8 * 1024 * 1024,
        ) as socket:
            self.books.clear()
            await socket.send(
                json.dumps(
                    {
                        "assets_ids": sorted(wanted),
                        "type": "market",
                        "custom_feature_enabled": True,
                    }
                )
            )
            print(
                f"[shadow] connected assets={len(wanted)} db={self.store.path}",
                flush=True,
            )
            heartbeat = asyncio.create_task(self.heartbeat(socket))
            refresh = asyncio.create_task(self.refresh_subscriptions(socket))
            try:
                while True:
                    if (
                        self.duration_seconds
                        and time.monotonic() - self.started_monotonic
                        >= self.duration_seconds
                    ):
                        return
                    try:
                        raw = await asyncio.wait_for(socket.recv(), timeout=2.0)
                    except asyncio.TimeoutError:
                        continue
                    if raw in ("PONG", b"PONG"):
                        continue
                    self.messages += 1
                    self.process(json.loads(raw))
                    if self.messages % 250 == 0:
                        print(
                            f"[shadow] messages={self.messages} "
                            f"rows={self.store.counts()} "
                            f"open_gaps={len(self.open_by_key)} "
                            f"delay_trackers={len(self.active)}",
                            flush=True,
                        )
                    if self.messages % 5000 == 0:
                        maximum = int(
                            float(self.protocol["book"]["maximum_database_gb"])
                            * 1024**3
                        )
                        if maximum and self.store.disk_bytes() >= maximum:
                            print(
                                "[shadow] database safety cap reached; stopping cleanly",
                                flush=True,
                            )
                            return
            finally:
                heartbeat.cancel()
                refresh.cancel()
                await asyncio.gather(
                    heartbeat, refresh, return_exceptions=True
                )


async def run_forever(shadow: CompleteSetShadow) -> None:
    backoff = 2.0
    while True:
        try:
            await shadow.session()
            return
        except KeyboardInterrupt:
            return
        except Exception as exc:  # noqa: BLE001 - reconnect public feed failures
            if (
                shadow.duration_seconds
                and time.monotonic() - shadow.started_monotonic
                >= shadow.duration_seconds
            ):
                return
            print(
                f"[shadow] {type(exc).__name__}: {exc}; retry {backoff:.0f}s",
                flush=True,
            )
            await asyncio.sleep(backoff)
            backoff = min(30.0, backoff * 1.7)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument(
        "--duration",
        type=float,
        default=0.0,
        help="seconds to record; zero runs until interrupted",
    )
    args = parser.parse_args()
    protocol = load_protocol()
    store = CompleteSetStore(Path(args.db))
    shadow = CompleteSetShadow(
        protocol, store, duration_seconds=args.duration
    )
    try:
        asyncio.run(run_forever(shadow))
    finally:
        for active in list(shadow.active.values()):
            if active.gap_open:
                store.close_opportunity(
                    active.opportunity_id,
                    time.time_ns(),
                    "",
                    "process_stopped",
                )
        print(f"[shadow] final rows={store.counts()}", flush=True)
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
