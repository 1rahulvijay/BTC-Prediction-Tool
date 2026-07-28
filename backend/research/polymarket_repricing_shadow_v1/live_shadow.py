#!/usr/bin/env python
"""Forward-only repricing shadow.

Inputs are public Binance aggregate trades and the recorder-owned atomic
`pm_live_quotes.json` bridge. This process has no API keys and no order path.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

import joblib
import pandas as pd
import websockets

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESEARCH = BACKEND / "research"
for candidate in (RESEARCH,):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from polymarket_repricing_shadow_v1.event_features import (
    EventFeatureBuffer,
    score_event_bundle,
)
from polymarket_repricing_shadow_v1.routing import (
    Candidate,
    RouteState,
    create_routes,
    parse_ladder,
    update_route,
)
from polymarket_repricing_shadow_v1.shadow_store import ShadowStore

PROTOCOL_PATH = Path(__file__).with_name("frozen_protocol.json")
DATA = Path(os.environ.get("BTC_DATA_DIR") or ROOT / "data")
DEFAULT_DB = DATA / "research" / "polymarket_repricing_shadow_v1" / "shadow.duckdb"
DEFAULT_QUOTES = DATA / "pm_live_quotes.json"
SPOT_WS = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
PERP_WS = "wss://fstream.binance.com/ws/btcusdt@aggTrade"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(path_text: str) -> Path:
    path = Path(path_text)
    return path if path.is_absolute() else ROOT / path


def load_inputs() -> tuple[
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, Any],
    dict[str, str],
]:
    protocol = json.loads(PROTOCOL_PATH.read_text(encoding="utf-8"))
    boundaries = protocol["boundaries"]
    if any(
        (
            protocol["serving_enabled"],
            protocol["paper_enabled"],
            protocol["live_enabled"],
            boundaries["repricing_may_change_selected_side"],
            boundaries["repricing_may_change_trade_eligibility"],
            boundaries["repricing_may_change_position_size"],
            boundaries["may_submit_orders"],
        )
    ):
        raise RuntimeError("research-only protocol boundary was weakened")
    event_path = resolve(protocol["models"]["event_bundle"])
    contract_root = resolve(protocol["models"]["contract_run"]) / "models"
    if not event_path.is_file():
        raise FileNotFoundError(
            f"missing event bundle: {event_path}; run train_event_bundle.py explicitly"
        )
    up_path = contract_root / "E07_up_ask_worsens_1c_within_5s.joblib"
    down_path = contract_root / "E08_down_ask_worsens_1c_within_5s.joblib"
    for path in (up_path, down_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    event_bundle = joblib.load(event_path)
    up_bundle = joblib.load(up_path)
    down_bundle = joblib.load(down_path)
    protocol_hash = sha256_file(PROTOCOL_PATH)
    if event_bundle.get("protocol_sha256") != protocol_hash:
        raise ValueError(
            "event bundle protocol hash mismatch; retrain the research artifact explicitly"
        )
    if any(
        bool(event_bundle.get(key))
        for key in ("serving_enabled", "paper_enabled", "live_enabled")
    ):
        raise ValueError("event bundle has unsafe activation flags")
    for name, bundle in (("UP", up_bundle), ("DOWN", down_bundle)):
        if "evidence" not in bundle or not hasattr(bundle["evidence"], "predict"):
            raise ValueError(f"{name} repricing artifact is not portable")
    identities = {
        "protocol_sha256": protocol_hash,
        "event_bundle_sha256": sha256_file(event_path),
        "up_model_sha256": sha256_file(up_path),
        "down_model_sha256": sha256_file(down_path),
    }
    return protocol, event_bundle, up_bundle, down_bundle, identities


def quote_ladder(quote: dict[str, Any], side: str) -> dict[str, list[list[float]]]:
    full_key = "up_full_ladder" if side == "UP" else "down_full_ladder"
    return parse_ladder(quote.get(full_key))


def contract_features(
    quote: dict[str, Any],
    event: dict[str, float],
    event_score: float,
    persistent_score: float,
    spot_rms_60: float,
) -> pd.DataFrame:
    up_mid = (float(quote["up_bid"]) + float(quote["up_ask"])) / 2.0
    down_mid = (float(quote["down_bid"]) + float(quote["down_ask"])) / 2.0
    midpoint_sum = up_mid + down_mid
    seconds_left = max(1.0, float(quote["seconds_left"]))
    distance_bps = float(quote["distance_bps"])
    z_distance = distance_bps / max(0.25, spot_rms_60 * math.sqrt(seconds_left))
    up_ladder = quote_ladder(quote, "UP")
    down_ladder = quote_ladder(quote, "DOWN")
    values = {
        "market_prob_up": up_mid / midpoint_sum,
        "seconds_ratio": seconds_left / 300.0,
        "rv_60s_bps": spot_rms_60,
        "distance_bps": distance_bps,
        "z_distance": max(-12.0, min(12.0, z_distance)),
        "spread_up": float(quote["up_spread"]),
        "spread_down": float(quote["down_spread"]),
        "sau": float(quote["up_top_ask_size"]),
        "sad": float(quote["down_top_ask_size"]),
        "du": float(sum(size for _, size in up_ladder["b"] + up_ladder["a"])),
        "dd": float(sum(size for _, size in down_ladder["b"] + down_ladder["a"])),
        "au": float(quote["up_ask"]),
        "ad": float(quote["down_ask"]),
        "p_up_5": event["p_direction_5"],
        "p_move_5": event["p_movement_5"],
        "p_roundtrip_5": event["p_roundtrip_5"],
        "p_up_15": event["p_direction_15"],
        "p_move_15": event["p_movement_15"],
        "p_roundtrip_15": event["p_roundtrip_15"],
        "event_score": event_score,
        "event_persistent_score": persistent_score,
    }
    return pd.DataFrame([values]), values


class LiveShadow:
    def __init__(
        self,
        protocol: dict[str, Any],
        event_bundle: dict[str, Any],
        up_bundle: dict[str, Any],
        down_bundle: dict[str, Any],
        identities: dict[str, str],
        store: ShadowStore,
        quote_path: Path,
    ):
        self.protocol = protocol
        self.event_bundle = event_bundle
        self.up_baseline_model = up_bundle["baseline"]
        self.up_model = up_bundle["evidence"]
        self.down_baseline_model = down_bundle["baseline"]
        self.down_model = down_bundle["evidence"]
        self.store = store
        self.quote_path = quote_path
        self.buffer = EventFeatureBuffer()
        self.latest_event: dict[str, float] | None = None
        self.latest_features: dict[str, float] | None = None
        self.latest_score_second = 0
        self.persistent_score: float | None = None
        self.active: dict[str, tuple[Candidate, list[RouteState]]] = {}
        self.last_quote_mtime_ns = -1
        self.started = time.time()
        self.store.set_meta("protocol", protocol)
        self.store.set_meta("artifact_identities", identities)
        self.store.set_meta("boundaries", protocol["boundaries"])

    async def binance_stream(self, venue: str, url: str) -> None:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(
                    url,
                    ping_interval=20,
                    ping_timeout=20,
                    open_timeout=15,
                    close_timeout=5,
                ) as socket:
                    print(f"[feed] {venue} connected", flush=True)
                    backoff = 1.0
                    async for raw in socket:
                        message = json.loads(raw)
                        self.buffer.update(
                            venue,
                            int(message.get("T") or message.get("E") or 0),
                            float(message["p"]),
                            float(message["q"]),
                            bool(message["m"]),
                        )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - reconnect any feed failure
                print(
                    f"[feed] {venue} {type(exc).__name__}: {exc}; retry {backoff:.0f}s",
                    flush=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(30.0, backoff * 1.7)

    def update_event_score(self, now: float) -> None:
        completed = int(now) - 1
        if completed <= self.latest_score_second:
            return
        row = self.buffer.feature_row(completed)
        if row is None:
            return
        self.latest_event = score_event_bundle(self.event_bundle, row)
        self.latest_features = {name: float(row.iloc[0][name]) for name in row.columns}
        event_score = (
            0.70
            * math.log(
                max(1e-6, self.latest_event["p_direction_5"])
                / max(1e-6, 1.0 - self.latest_event["p_direction_5"])
            )
            * self.latest_event["p_movement_5"]
            + 0.30
            * math.log(
                max(1e-6, self.latest_event["p_direction_15"])
                / max(1e-6, 1.0 - self.latest_event["p_direction_15"])
            )
            * self.latest_event["p_movement_15"]
        )
        alpha = 1.0 - math.exp(math.log(0.5) / 5.0)
        self.persistent_score = (
            event_score
            if self.persistent_score is None
            else alpha * event_score + (1.0 - alpha) * self.persistent_score
        )
        self.latest_event["event_score"] = event_score
        self.latest_score_second = completed

    def read_quote(self) -> dict[str, Any] | None:
        try:
            stat = self.quote_path.stat()
            if stat.st_mtime_ns == self.last_quote_mtime_ns:
                return None
            self.last_quote_mtime_ns = stat.st_mtime_ns
            payload = json.loads(self.quote_path.read_text(encoding="utf-8"))
            quote = (payload.get("markets") or {}).get("5")
            if not isinstance(quote, dict):
                return None
            quote["_bridge_version"] = int(payload.get("version") or 0)
            return quote
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return None

    def make_candidate(
        self,
        quote: dict[str, Any],
        now: float,
    ) -> tuple[Candidate, float, float, float, float] | None:
        if self.latest_event is None or self.latest_features is None:
            return None
        if int(quote.get("_bridge_version") or 0) < 2:
            return None
        if not quote.get("up_full_ladder") or not quote.get("down_full_ladder"):
            return None
        age = now - float(quote.get("ts") or 0.0)
        if age < -1.0 or age > float(
            self.protocol["candidate"]["maximum_quote_age_seconds"]
        ):
            return None
        decision = str(quote.get("baseline_shadow_decision") or "")
        if decision not in {"BUY_UP_SHADOW", "BUY_DOWN_SHADOW"}:
            return None
        side = "UP" if decision == "BUY_UP_SHADOW" else "DOWN"
        edge = quote.get("up_edge_buffer3" if side == "UP" else "down_edge_buffer3")
        baseline_probability = quote.get("p_up" if side == "UP" else "p_down")
        if edge is None or float(edge) <= 0.0 or baseline_probability is None:
            return None
        event_score = float(self.latest_event["event_score"])
        frame, contract_values = contract_features(
            quote,
            self.latest_event,
            event_score,
            float(self.persistent_score or event_score),
            float(self.latest_features["spot_rms_60s_bps"]),
        )
        up_baseline_probability = float(self.up_baseline_model.predict(frame)[0])
        up_probability = float(self.up_model.predict(frame)[0])
        down_baseline_probability = float(self.down_baseline_model.predict(frame)[0])
        down_probability = float(self.down_model.predict(frame)[0])
        worsening = up_probability if side == "UP" else down_probability
        ladder = quote_ladder(quote, side)
        if not ladder["a"] or not ladder["b"]:
            return None
        prefix = "up" if side == "UP" else "down"
        market_id = str(quote.get("slug") or quote.get("condition_id") or "")
        if not market_id or self.store.has_market(market_id):
            return None
        candidate_id = hashlib.sha256(
            f"{market_id}|{side}|{float(quote['ts']):.6f}".encode()
        ).hexdigest()[:24]
        value = Candidate(
            candidate_id=candidate_id,
            timestamp=float(quote["ts"]),
            market_id=market_id,
            condition_id=str(quote.get("condition_id") or ""),
            selected_side=side,
            quantity=float(self.protocol["candidate"]["minimum_quantity"]),
            bid=float(quote[f"{prefix}_bid"]),
            ask=float(quote[f"{prefix}_ask"]),
            spread=float(quote[f"{prefix}_spread"]),
            top_ask_depth=float(quote[f"{prefix}_top_ask_size"]),
            ladder=ladder,
            baseline_probability=float(baseline_probability),
            baseline_edge=float(edge),
            worsening_probability=worsening,
            quote_age_seconds=max(0.0, age),
            seconds_left=float(quote["seconds_left"]),
            event_probabilities=dict(self.latest_event),
            feature_values=contract_values,
        )
        return (
            value,
            up_baseline_probability,
            up_probability,
            down_baseline_probability,
            down_probability,
        )

    def process_active(self, quote: dict[str, Any], now: float) -> None:
        market_id = str(quote.get("slug") or quote.get("condition_id") or "")
        active = self.active.get(market_id)
        if active is None:
            return
        candidate, routes = active
        elapsed = max(0.0, float(quote["ts"]) - candidate.timestamp)
        prefix = "up" if candidate.selected_side == "UP" else "down"
        ladder = quote_ladder(quote, candidate.selected_side)
        for offset in (1, 2, 5):
            if elapsed >= offset:
                self.store.observation(
                    candidate.candidate_id,
                    offset,
                    elapsed,
                    float(quote["ts"]),
                    float(quote[f"{prefix}_bid"]),
                    float(quote[f"{prefix}_ask"]),
                    float(quote[f"{prefix}_spread"]),
                    float(quote[f"{prefix}_top_ask_size"]),
                    ladder,
                )
        for route in routes:
            update_route(
                route,
                ladder,
                elapsed,
                fallback_cross=bool(
                    self.protocol["routing"]["fallback_cross_after_ttl"]
                ),
            )
            self.store.route(route, now)
        if elapsed >= 5 and all(
            route.status in {"FILLED", "PARTIAL", "MISSED", "SKIPPED"}
            for route in routes
        ):
            del self.active[market_id]

    def process_quote(self, quote: dict[str, Any], now: float) -> None:
        self.process_active(quote, now)
        created = self.make_candidate(quote, now)
        if created is None:
            return
        (
            candidate,
            up_baseline_probability,
            up_probability,
            down_baseline_probability,
            down_probability,
        ) = created
        routes = create_routes(candidate, self.protocol)
        self.store.candidate(
            candidate,
            up_baseline_probability,
            up_probability,
            down_baseline_probability,
            down_probability,
        )
        for route in routes:
            update_route(
                route,
                candidate.ladder,
                0.0,
                fallback_cross=bool(
                    self.protocol["routing"]["fallback_cross_after_ttl"]
                ),
            )
            self.store.route(route, now)
        self.active[candidate.market_id] = (candidate, routes)
        print(
            f"[candidate] {candidate.market_id} {candidate.selected_side} "
            f"ask={candidate.ask:.2f} edge={candidate.baseline_edge:+.3f} "
            f"p_worsen={candidate.worsening_probability:.3f}",
            flush=True,
        )

    async def run(self, duration: float = 0.0) -> None:
        tasks = [
            asyncio.create_task(self.binance_stream("spot", SPOT_WS)),
            asyncio.create_task(self.binance_stream("perp", PERP_WS)),
        ]
        started = time.monotonic()
        try:
            while not duration or time.monotonic() - started < duration:
                now = time.time()
                self.update_event_score(now)
                quote = self.read_quote()
                if quote is not None:
                    self.process_quote(quote, now)
                await asyncio.sleep(0.2)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


def selftest() -> None:
    protocol, event_bundle, up_bundle, down_bundle, identities = load_inputs()
    assert protocol["promotion_status"] == "research_only"
    assert not any(
        (
            protocol["serving_enabled"],
            protocol["paper_enabled"],
            protocol["live_enabled"],
        )
    )
    assert len(event_bundle["feature_names"]) == 86
    assert hasattr(up_bundle["baseline"], "predict")
    assert hasattr(up_bundle["evidence"], "predict")
    assert hasattr(down_bundle["baseline"], "predict")
    assert hasattr(down_bundle["evidence"], "predict")
    assert len(identities) == 4
    print("polymarket repricing live-shadow self-test: ALL PASS")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--quotes", type=Path, default=DEFAULT_QUOTES)
    parser.add_argument("--duration", type=float, default=0.0)
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()
    if args.selftest:
        selftest()
        return 0
    protocol, event_bundle, up_bundle, down_bundle, identities = load_inputs()
    store = ShadowStore(args.db.resolve())
    shadow = LiveShadow(
        protocol,
        event_bundle,
        up_bundle,
        down_bundle,
        identities,
        store,
        args.quotes.resolve(),
    )
    print(
        f"[shadow] research-only; no order path; db={store.path}; "
        f"quotes={args.quotes.resolve()}",
        flush=True,
    )
    try:
        asyncio.run(shadow.run(max(0.0, args.duration)))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
