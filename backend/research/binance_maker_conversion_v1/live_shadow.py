#!/usr/bin/env python
"""Forward-only Binance USD-M maker-conversion shadow.

This process consumes public market data only. It has no API-key handling and
contains no order-submission path.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
import joblib
import websockets

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESEARCH = BACKEND / "research"
for candidate in (RESEARCH,):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from polymarket_repricing_shadow_v1.event_features import (
    FEATURE_NAMES,
    EventFeatureBuffer,
    score_event_bundle,
)

from binance_maker_conversion_v1.order_book import (
    BookSequenceGap,
    LocalOrderBook,
)
from binance_maker_conversion_v1.simulator import ExecutionSimulator, Route
from binance_maker_conversion_v1.store import EvidenceStore

PROTOCOL_PATH = Path(__file__).with_name("frozen_protocol.json")
SOURCE_PROTOCOL_PATH = (
    RESEARCH / "event_execution_v1" / "frozen_protocol.json"
)
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_DB = DATA / "research" / "binance_maker_conversion_v1" / "shadow.duckdb"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_json(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def resolve(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def code_identity() -> tuple[str, str]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        commit = f"{commit}-dirty" if dirty else commit
    except (OSError, subprocess.SubprocessError):
        commit = "unknown"
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return commit, digest.hexdigest()


def load_inputs() -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    unsafe = (
        protocol["serving_enabled"],
        protocol["paper_enabled"],
        protocol["live_enabled"],
        protocol["may_submit_orders"],
    )
    if any(unsafe):
        raise RuntimeError("research-only protocol boundary was weakened")
    if int(protocol["execution"]["decision_latency_ms"]) != 0:
        raise ValueError(
            "non-zero decision latency requires an explicit delayed route; "
            "change the protocol only with a version bump"
        )
    if "/market/" not in protocol["instrument"]["perp_trade_stream"]:
        raise ValueError("perpetual aggregate trades must use Binance /market")
    if "/public/" not in protocol["instrument"]["depth_stream"]:
        raise ValueError("diff depth must use Binance /public")
    source = json.loads(SOURCE_PROTOCOL_PATH.read_text(encoding="utf-8"))
    candidate = protocol["source_model"]
    source_gate = source["btc_proxy_execution"]
    if (
        float(candidate["minimum_movement_probability"])
        != float(source_gate["minimum_movement_probability"])
        or float(candidate["minimum_direction_margin"])
        != float(source_gate["minimum_direction_margin"])
        or list(candidate["horizons_seconds"]) != [5, 15]
    ):
        raise ValueError("candidate rule drifted from frozen E09/E10")
    bundle_path = resolve(candidate["bundle"])
    bundle_protocol_path = resolve(candidate["bundle_protocol"])
    bundle = joblib.load(bundle_path)
    if (
        bundle.get("promotion_status") != "research_only"
        or any(
            bool(bundle.get(key))
            for key in ("serving_enabled", "paper_enabled", "live_enabled")
        )
    ):
        raise ValueError("source event bundle activation boundary is unsafe")
    if list(bundle.get("feature_names") or []) != FEATURE_NAMES:
        raise ValueError("source event bundle feature schema mismatch")
    bundle_protocol_hash = sha256_file(bundle_protocol_path)
    if bundle.get("protocol_sha256") != bundle_protocol_hash:
        raise ValueError("source event bundle protocol hash mismatch")
    commit, code_hash = code_identity()
    identities = {
        "protocol_hash": sha256_file(PROTOCOL_PATH),
        "source_protocol_hash": sha256_file(SOURCE_PROTOCOL_PATH),
        "event_bundle_protocol_hash": bundle_protocol_hash,
        "model_bundle_hash": sha256_file(bundle_path),
        "feature_schema_hash": sha256_json(FEATURE_NAMES),
        "code_commit": commit,
        "code_hash": code_hash,
    }
    return protocol, bundle, identities


class MakerConversionShadow:
    def __init__(
        self,
        protocol: dict[str, Any],
        bundle: dict[str, Any],
        identities: dict[str, str],
        store: EvidenceStore,
    ):
        self.protocol = protocol
        self.bundle = bundle
        self.identities = identities
        self.store = store
        self.buffer = EventFeatureBuffer()
        self.book = LocalOrderBook()
        self.simulator = ExecutionSimulator(protocol)
        self.active: dict[str, list[Route]] = {}
        self.latest_probabilities: dict[str, float] | None = None
        self.latest_score_second = 0
        self.next_allowed = {5: 0, 15: 0}
        self.sequence_gap_count = 0
        self.last_health_ms = 0
        self.last_trade_received_ms = {"spot": 0, "perp": 0}
        self.last_aggregate_trade_id = {"spot": -1, "perp": -1}
        self.route_state_cache: dict[tuple[str, str], tuple[Any, ...]] = {}
        self.candidate_marks: dict[str, set[int]] = {}
        self.stop_reason: str | None = None
        self.checkpoints = tuple(
            int(value)
            for value in protocol["execution"]["maker_fill_checkpoints_ms"]
        )
        self.store.set_meta("protocol", protocol)
        self.store.set_meta("identities", identities)

    async def fetch_snapshot(self) -> tuple[dict[str, Any], int]:
        timeout = aiohttp.ClientTimeout(total=10)
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(
                self.protocol["instrument"]["rest_depth_url"]
            ) as response,
        ):
            response.raise_for_status()
            payload = await response.json()
            return payload, int(time.time() * 1000)

    async def depth_stream(self) -> None:
        url = self.protocol["instrument"]["depth_stream"]
        maximum = float(
            self.protocol["reliability"]["reconnect_maximum_seconds"]
        )
        backoff = 1.0
        while True:
            try:
                self.book.ready = False
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    open_timeout=15,
                    close_timeout=5,
                    max_queue=4096,
                ) as socket:
                    snapshot_task = asyncio.create_task(self.fetch_snapshot())
                    buffered: list[tuple[dict[str, Any], int]] = []
                    while not snapshot_task.done():
                        try:
                            raw = await asyncio.wait_for(socket.recv(), timeout=0.25)
                            buffered.append(
                                (json.loads(raw), int(time.time() * 1000))
                            )
                        except asyncio.TimeoutError:
                            continue
                    snapshot, received = await snapshot_task
                    self.book.initialize(snapshot, received)
                    for event, event_received in buffered:
                        self.book.apply(event, event_received)
                    self.store.health(
                        "depth",
                        "CONNECTED",
                        f"snapshot={self.book.last_update_id}",
                        sequence_gap_count=self.sequence_gap_count,
                    )
                    print(
                        f"[depth] ready u={self.book.last_update_id} "
                        f"buffered={len(buffered)}",
                        flush=True,
                    )
                    backoff = 1.0
                    async for raw in socket:
                        received_ms = int(time.time() * 1000)
                        event = json.loads(raw)
                        self.book.apply(event, received_ms)
            except asyncio.CancelledError:
                raise
            except BookSequenceGap as exc:
                self.sequence_gap_count += 1
                self.store.health(
                    "depth",
                    "SEQUENCE_GAP",
                    str(exc),
                    sequence_gap_count=self.sequence_gap_count,
                )
                print(f"[depth] sequence gap: {exc}; rebuilding", flush=True)
                await asyncio.sleep(0.2)
            except Exception as exc:  # noqa: BLE001 - feed must reconnect
                self.store.health("depth", "DISCONNECTED", f"{type(exc).__name__}: {exc}")
                print(
                    f"[depth] {type(exc).__name__}: {exc}; retry {backoff:.0f}s",
                    flush=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(maximum, backoff * 1.7)

    async def trade_stream(self, venue: str, url: str) -> None:
        maximum = float(
            self.protocol["reliability"]["reconnect_maximum_seconds"]
        )
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    open_timeout=15,
                    close_timeout=5,
                    max_queue=4096,
                ) as socket:
                    self.store.health(venue, "CONNECTED")
                    print(f"[feed] {venue} connected", flush=True)
                    backoff = 1.0
                    async for raw in socket:
                        received_ms = int(time.time() * 1000)
                        message = json.loads(raw)
                        event_ts = int(message.get("T") or message.get("E") or 0)
                        price = float(message["p"])
                        quantity = float(message["q"])
                        buyer_is_maker = bool(message["m"])
                        aggregate_id = int(message.get("a") or -1)
                        previous_id = self.last_aggregate_trade_id[venue]
                        if aggregate_id >= 0 and aggregate_id <= previous_id:
                            continue
                        if (
                            aggregate_id >= 0
                            and previous_id >= 0
                            and aggregate_id > previous_id + 1
                        ):
                            self.store.health(
                                venue,
                                "TRADE_SEQUENCE_GAP",
                                f"expected>{previous_id}, received={aggregate_id}",
                            )
                        if aggregate_id >= 0:
                            self.last_aggregate_trade_id[venue] = aggregate_id
                        self.last_trade_received_ms[venue] = received_ms
                        self.buffer.update(
                            venue,
                            event_ts,
                            price,
                            quantity,
                            buyer_is_maker,
                        )
                        if venue == "perp":
                            for routes in tuple(self.active.values()):
                                for route in routes:
                                    self.simulator.on_trade(
                                        route,
                                        price=price,
                                        quantity=quantity,
                                        buyer_is_maker=buyer_is_maker,
                                        trade_ts_ms=event_ts,
                                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - feed must reconnect
                self.store.health(
                    venue, "DISCONNECTED", f"{type(exc).__name__}: {exc}"
                )
                print(
                    f"[feed] {venue} {type(exc).__name__}: {exc}; "
                    f"retry {backoff:.0f}s",
                    flush=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(maximum, backoff * 1.7)

    def update_score(self, now_ms: int) -> None:
        completed = now_ms // 1000 - 1
        if completed <= self.latest_score_second:
            return
        row = self.buffer.feature_row(completed)
        if row is None:
            return
        self.latest_probabilities = score_event_bundle(self.bundle, row)
        self.latest_score_second = completed

    def maybe_create_candidates(self, now_ms: int) -> None:
        if self.latest_probabilities is None or not self.book.ready:
            return
        completed = self.latest_score_second
        stale_limit = int(
            float(self.protocol["reliability"]["stale_alarm_seconds"]) * 1000
        )
        if now_ms - completed * 1000 > stale_limit:
            return
        if any(
            received <= 0 or now_ms - received > stale_limit
            for received in self.last_trade_received_ms.values()
        ):
            return
        top = self.book.top()
        if top is None:
            return
        book_age = max(0, now_ms - top.received_ts_ms)
        maximum_age = int(self.protocol["execution"]["maximum_book_age_ms"])
        if book_age > maximum_age:
            return
        source = self.protocol["source_model"]
        minimum_move = float(source["minimum_movement_probability"])
        minimum_margin = float(source["minimum_direction_margin"])
        notional = float(self.protocol["execution"]["notional_usd"])
        for horizon in source["horizons_seconds"]:
            horizon = int(horizon)
            if completed < self.next_allowed[horizon]:
                continue
            p_direction = float(
                self.latest_probabilities[f"p_direction_{horizon}"]
            )
            p_movement = float(
                self.latest_probabilities[f"p_movement_{horizon}"]
            )
            margin = abs(p_direction - 0.5)
            if p_movement < minimum_move or margin < minimum_margin:
                continue
            self.next_allowed[horizon] = completed + horizon
            side = "LONG" if p_direction >= 0.5 else "SHORT"
            candidate_id = hashlib.sha256(
                (
                    f"{self.identities['protocol_hash']}|{completed}|"
                    f"{horizon}|{side}"
                ).encode()
            ).hexdigest()[:24]
            quantity = notional / top.mid
            row = {
                "candidate_id": candidate_id,
                "decision_second": completed,
                "decision_ts_ms": now_ms,
                "horizon_seconds": horizon,
                "side": side,
                "p_direction": p_direction,
                "p_movement": p_movement,
                "p_roundtrip": self.latest_probabilities.get(
                    f"p_roundtrip_{horizon}"
                ),
                "model_margin": margin,
                "quantity": quantity,
                "notional_usd": notional,
                "best_bid": top.best_bid,
                "best_ask": top.best_ask,
                "bid_quantity": top.bid_quantity,
                "ask_quantity": top.ask_quantity,
                "spread_bps": top.spread_bps,
                "book_update_id": top.update_id,
                "book_event_ts_ms": top.event_ts_ms,
                "book_received_ts_ms": top.received_ts_ms,
                "book_age_ms": book_age,
                "protocol_hash": self.identities["protocol_hash"],
                "model_bundle_hash": self.identities["model_bundle_hash"],
                "feature_schema_hash": self.identities["feature_schema_hash"],
                "code_commit": self.identities["code_commit"],
                "created_ts_ms": now_ms,
            }
            if not self.store.candidate(row):
                continue
            routes = self.simulator.create_routes(
                candidate_id=candidate_id,
                side=side,
                horizon_seconds=horizon,
                decision_ts_ms=now_ms,
                quantity=quantity,
                book=self.book,
            )
            self.active[candidate_id] = routes
            self.store.candidate_book_checkpoint(
                candidate_id,
                0,
                now_ms,
                quantity,
                self.book,
            )
            self.candidate_marks[candidate_id] = {0}
            for route in routes:
                self.persist_route(route, force=True)
            print(
                f"[candidate] {horizon}s {side} p={p_direction:.3f} "
                f"move={p_movement:.3f} spread={top.spread_bps:.3f}bps "
                f"id={candidate_id}",
                flush=True,
            )

    def process_routes(self, now_ms: int) -> None:
        top = self.book.top()
        if top is None:
            return
        completed: list[str] = []
        for candidate_id, routes in tuple(self.active.items()):
            decision_ts_ms = routes[0].decision_ts_ms
            quantity = routes[0].quantity
            horizon_ms = routes[0].horizon_seconds * 1000
            elapsed_candidate = now_ms - decision_ts_ms
            marks = self.candidate_marks.setdefault(candidate_id, set())
            for checkpoint in (0, 250, 500, horizon_ms):
                if elapsed_candidate >= checkpoint and checkpoint not in marks:
                    self.store.candidate_book_checkpoint(
                        candidate_id,
                        checkpoint,
                        now_ms,
                        quantity,
                        self.book,
                    )
                    marks.add(checkpoint)
            for route in routes:
                self.simulator.on_clock(route, self.book, now_ms)
                for leg, queue, base_ts in (
                    ("ENTRY", route.entry_queue, route.decision_ts_ms),
                    ("EXIT", route.exit_queue, route.exit_due_ts_ms),
                ):
                    if queue is None:
                        continue
                    elapsed = now_ms - base_ts
                    for checkpoint in self.checkpoints:
                        marker = (
                            100_000
                            + (10_000 if leg == "EXIT" else 0)
                            + checkpoint
                        )
                        if elapsed >= checkpoint and marker not in route.marks:
                            self.store.checkpoint(
                                route,
                                leg,
                                checkpoint,
                                now_ms,
                                self.book,
                            )
                            route.marks[marker] = top.mid
                for leg, fill_ts in (
                    ("ENTRY", route.entry_ts_ms),
                    ("EXIT", route.exit_ts_ms),
                ):
                    if fill_ts is None:
                        continue
                    for offset in self.checkpoints:
                        marker = (1 if leg == "ENTRY" else 2) * 10_000 + offset
                        if now_ms - fill_ts >= offset and marker not in route.marks:
                            self.store.post_fill_mark(
                                route, leg, offset, now_ms, top.mid
                            )
                            route.marks[marker] = top.mid
                self.persist_route(route)
            if all(route.status != "ACTIVE" for route in routes):
                status = (
                    "RESOLVED"
                    if any(route.status == "RESOLVED" for route in routes)
                    else "NO_FILL"
                )
                self.store.resolve_candidate(candidate_id, status)
                completed.append(candidate_id)
        for candidate_id in completed:
            del self.active[candidate_id]
            self.candidate_marks.pop(candidate_id, None)

    def persist_route(self, route: Route, force: bool = False) -> None:
        entry_queue = route.entry_queue
        exit_queue = route.exit_queue
        signature = (
            route.status,
            route.reason,
            route.entry_status,
            route.entry_price,
            route.entry_ts_ms,
            round(route.entry_filled_quantity, 12),
            round(entry_queue.queue_ahead, 12) if entry_queue else None,
            route.exit_status,
            route.exit_price,
            route.exit_ts_ms,
            round(route.exit_filled_quantity, 12),
            round(exit_queue.queue_ahead, 12) if exit_queue else None,
        )
        key = (route.candidate_id, route.policy)
        if force or self.route_state_cache.get(key) != signature:
            self.store.route(route, self.simulator.economics(route))
            self.route_state_cache[key] = signature

    def heartbeat(self, now_ms: int) -> None:
        interval = int(self.protocol["reliability"]["heartbeat_seconds"]) * 1000
        if now_ms - self.last_health_ms < interval:
            return
        self.last_health_ms = now_ms
        top = self.book.top()
        book_age = now_ms - top.received_ts_ms if top else None
        clock_drift = now_ms - top.event_ts_ms if top and top.event_ts_ms else None
        status = "OK"
        stale_limit = int(
            float(self.protocol["reliability"]["stale_alarm_seconds"]) * 1000
        )
        trade_ages = {
            venue: now_ms - received if received > 0 else None
            for venue, received in self.last_trade_received_ms.items()
        }
        if (
            not top
            or book_age is None
            or book_age > stale_limit
            or any(age is None or age > stale_limit for age in trade_ages.values())
            or now_ms - self.latest_score_second * 1000 > stale_limit
        ):
            status = "STALE"
        self.store.health(
            "campaign",
            status,
            (
                f"active={len(self.active)} score_second={self.latest_score_second} "
                f"spot_age_ms={trade_ages['spot']} "
                f"perp_age_ms={trade_ages['perp']}"
            ),
            sequence_gap_count=self.sequence_gap_count,
            clock_drift_ms=clock_drift,
            book_age_ms=book_age,
        )
        maximum = int(self.protocol["reliability"]["maximum_database_bytes"])
        try:
            wal_path = Path(f"{self.store.path}.wal")
            size = self.store.path.stat().st_size + (
                wal_path.stat().st_size if wal_path.exists() else 0
            )
            if size > maximum:
                self.stop_reason = "database_size_limit_reached"
        except OSError:
            pass

    async def run(self, duration_seconds: float = 0.0) -> None:
        instrument = self.protocol["instrument"]
        tasks = [
            asyncio.create_task(self.depth_stream()),
            asyncio.create_task(
                self.trade_stream("spot", instrument["spot_trade_stream"])
            ),
            asyncio.create_task(
                self.trade_stream("perp", instrument["perp_trade_stream"])
            ),
        ]
        started = time.monotonic()
        try:
            while (
                not duration_seconds
                or time.monotonic() - started < duration_seconds
            ):
                now_ms = int(time.time() * 1000)
                self.update_score(now_ms)
                self.maybe_create_candidates(now_ms)
                self.process_routes(now_ms)
                self.heartbeat(now_ms)
                if self.stop_reason:
                    print(f"[shadow] stopping: {self.stop_reason}", flush=True)
                    break
                await asyncio.sleep(0.02)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self.store.health(
                "campaign",
                "STOPPED",
                self.stop_reason or "graceful_shutdown",
                sequence_gap_count=self.sequence_gap_count,
            )


def selftest() -> None:
    protocol, bundle, identities = load_inputs()
    assert protocol["protocol_id"] == "BINANCE_MAKER_CONVERSION_V1"
    assert not protocol["may_submit_orders"]
    assert list(bundle["horizons_seconds"]) == [5, 15]
    assert len(bundle["feature_names"]) == 86
    assert len(identities["protocol_hash"]) == 64
    print("binance maker-conversion live-shadow self-test: ALL PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0
    protocol, bundle, identities = load_inputs()
    store = EvidenceStore(args.db.resolve())
    shadow = MakerConversionShadow(protocol, bundle, identities, store)
    print(
        f"[shadow] BINANCE_MAKER_CONVERSION_V1 research-only; no order path; "
        f"db={store.path}",
        flush=True,
    )
    print(
        f"[identity] commit={identities['code_commit']} "
        f"protocol={identities['protocol_hash'][:12]} "
        f"model={identities['model_bundle_hash'][:12]}",
        flush=True,
    )
    try:
        asyncio.run(shadow.run(max(0.0, args.duration)))
    except KeyboardInterrupt:
        print("[shadow] interrupted; closing cleanly", flush=True)
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
