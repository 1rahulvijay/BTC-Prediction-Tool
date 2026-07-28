#!/usr/bin/env python
"""Isolation, parity, routing, and storage tests for the repricing shadow."""

from __future__ import annotations

import ast
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
BACKEND = ROOT / "backend"
RESEARCH = BACKEND / "research"
for candidate in (BACKEND, RESEARCH):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from train_event_time_specialists import build_causal_features

from polymarket_repricing_shadow_v1.event_features import (
    FEATURE_NAMES,
    EventFeatureBuffer,
)
from polymarket_repricing_shadow_v1.report import (
    delay_stress_report,
    gate_report,
    probability_report,
    route_report,
    size_stress_report,
)
from polymarket_repricing_shadow_v1.routing import (
    POLICY_NAMES,
    Candidate,
    create_routes,
    update_route,
)
from polymarket_repricing_shadow_v1.shadow_store import ShadowStore

PACKAGE = Path(__file__).parent
PROTOCOL = json.loads((PACKAGE / "frozen_protocol.json").read_text(encoding="utf-8"))


def test_boundaries() -> None:
    assert PROTOCOL["promotion_status"] == "research_only"
    assert PROTOCOL["serving_enabled"] is False
    assert PROTOCOL["paper_enabled"] is False
    assert PROTOCOL["live_enabled"] is False
    for key in (
        "repricing_may_change_selected_side",
        "repricing_may_change_trade_eligibility",
        "repricing_may_change_position_size",
        "may_submit_orders",
    ):
        assert PROTOCOL["boundaries"][key] is False
    assert PROTOCOL["routing"]["paper_promotion_candidates"] == ["C_MAKER_FIRST_TTL2"]
    forbidden_modules = {
        "decision_champion",
        "polymarket_simulator",
        "binance_paper",
        "trade_plan_optimizer",
    }
    forbidden_secret_tokens = {
        "create_order",
        "private_key",
        "api_secret",
        "requests.post",
    }
    for path in PACKAGE.glob("*.py"):
        if path.resolve() == Path(__file__).resolve():
            continue
        source = path.read_text(encoding="utf-8").lower()
        tree = ast.parse(source)
        imports = set()
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                function = node.func
                if isinstance(function, ast.Name):
                    calls.add(function.id.lower())
                elif isinstance(function, ast.Attribute):
                    calls.add(function.attr.lower())
        assert imports.isdisjoint(forbidden_modules), (path, imports)
        assert calls.isdisjoint({"create_order", "submit_order"}), (path, calls)
        assert all(token not in source for token in forbidden_secret_tokens), path
    for path in BACKEND.rglob("*.py"):
        if RESEARCH in path.parents:
            continue
        source = path.read_text(encoding="utf-8").lower()
        assert "polymarket_repricing_shadow_v1" not in source, path


def test_feature_parity() -> None:
    start = 1_700_000_000
    length = 137
    timestamp = np.arange(start, start + length, dtype=np.int64)
    spot = 60_000.0 + np.cumsum(np.sin(np.arange(length) / 7.0) + 0.2)
    perp = spot * (1.0001 + np.cos(np.arange(length) / 13.0) * 0.00001)
    events = {"timestamp_s": timestamp}
    buffer = EventFeatureBuffer(180)
    for venue, prices in (("spot", spot), ("perp", perp)):
        high = prices + 0.25
        low = prices - 0.20
        volume = 1.0 + (np.arange(length) % 5)
        signed = volume * np.where(np.arange(length) % 2 == 0, 1.0, -1.0)
        count = np.full(length, 3.0)
        events[f"{venue}_last"] = prices.astype(np.float32)
        events[f"{venue}_high"] = high.astype(np.float32)
        events[f"{venue}_low"] = low.astype(np.float32)
        events[f"{venue}_volume"] = volume.astype(np.float32)
        events[f"{venue}_signed"] = signed.astype(np.float32)
        events[f"{venue}_count"] = count.astype(np.float32)
        for index, second in enumerate(timestamp):
            quantity = float(
                events[f"{venue}_volume"][index] / events[f"{venue}_count"][index]
            )
            buyer_maker = bool(events[f"{venue}_signed"][index] < 0)
            for trade in range(int(count[index])):
                price = (
                    float(events[f"{venue}_high"][index])
                    if trade == 0
                    else float(events[f"{venue}_low"][index])
                    if trade == 1
                    else float(events[f"{venue}_last"][index])
                )
                buffer.update(
                    venue,
                    int(second * 1_000 + trade),
                    price,
                    quantity,
                    buyer_maker,
                )
    offline, anchors = build_causal_features(
        events, sample_every_seconds=5, max_horizon=15
    )
    assert anchors.tolist() == [120]
    live = buffer.feature_row(int(timestamp[120]))
    assert live is not None and list(live.columns) == FEATURE_NAMES
    np.testing.assert_allclose(
        live.to_numpy(float),
        offline.to_numpy(float),
        rtol=2e-5,
        atol=2e-5,
    )


def sample_candidate() -> Candidate:
    return Candidate(
        candidate_id="candidate-1",
        timestamp=100.0,
        market_id="market-1",
        condition_id="condition-1",
        selected_side="UP",
        quantity=1.0,
        bid=0.50,
        ask=0.53,
        spread=0.03,
        top_ask_depth=20.0,
        ladder={"b": [[0.50, 20.0]], "a": [[0.53, 20.0]]},
        baseline_probability=0.70,
        baseline_edge=0.10,
        worsening_probability=0.40,
        quote_age_seconds=0.2,
        seconds_left=60.0,
        event_probabilities={"p_direction_5": 0.6},
        feature_values={"market_prob_up": 0.55},
    )


def test_same_denominator_routing_and_store() -> None:
    candidate = sample_candidate()
    routes = create_routes(candidate, PROTOCOL)
    assert tuple(route.policy for route in routes) == POLICY_NAMES
    assert all(route.candidate_id == candidate.candidate_id for route in routes)
    assert candidate.selected_side == "UP"
    for route in routes:
        update_route(route, candidate.ladder, 0.0, fallback_cross=True)
    assert routes[0].status == "FILLED"
    assert routes[1].status == "FILLED"
    with tempfile.TemporaryDirectory() as directory:
        store = ShadowStore(Path(directory) / "shadow.duckdb")
        store.candidate(candidate, 0.35, 0.4, 0.55, 0.6)
        for route in routes:
            store.route(route, 100.0)
        count = store.conn.execute("SELECT count(*) FROM repricing_routes").fetchone()[
            0
        ]
        assert count == 4
        probabilities = store.conn.execute(
            """
            SELECT up_baseline_worsening_probability, up_worsening_probability,
                   down_baseline_worsening_probability, down_worsening_probability
            FROM repricing_candidates
            """
        ).fetchone()
        assert probabilities == (0.35, 0.4, 0.55, 0.6)
        store.close()


def test_forward_report_math() -> None:
    candidates = []
    observations = []
    settlements = []
    initial_ladder = json.dumps({"b": [[0.49, 20.0]], "a": [[0.50, 20.0]]})
    for index in range(20):
        side = "UP" if index < 10 else "DOWN"
        worsened = index % 2 == 0
        evidence = 0.8 if worsened else 0.2
        candidates.append(
            {
                "candidate_id": f"c-{index}",
                "decision_ts": 1_700_000_000.0 + index,
                "selected_side": side,
                "quantity": 1.0,
                "current_ask": 0.50,
                "up_baseline_worsening_probability": 0.5,
                "up_worsening_probability": evidence if side == "UP" else 0.5,
                "down_baseline_worsening_probability": 0.5,
                "down_worsening_probability": evidence if side == "DOWN" else 0.5,
                "selected_worsening_probability": evidence,
                "ladder_json": initial_ladder,
            }
        )
        delayed_ask = 0.51 if worsened else 0.49
        delayed_ladder = json.dumps(
            {
                "b": [[round(delayed_ask - 0.01, 2), 20.0]],
                "a": [[delayed_ask, 20.0]],
            }
        )
        for offset in (1, 2, 5):
            observations.append(
                {
                    "candidate_id": f"c-{index}",
                    "offset_seconds": offset,
                    "actual_elapsed_seconds": offset + 0.2,
                    "ask": delayed_ask,
                    "ladder_json": delayed_ladder,
                }
            )
        settlements.append(
            {
                "candidate_id": f"c-{index}",
                "settled_side": side
                if index % 3
                else ("DOWN" if side == "UP" else "UP"),
            }
        )
    candidate_frame = pd.DataFrame(candidates)
    observation_frame = pd.DataFrame(observations)
    settlement_frame = pd.DataFrame(settlements)
    calibration, deciles = probability_report(candidate_frame, observation_frame)
    assert set(calibration["side"]) == {"UP", "DOWN"}
    assert bool((calibration["brier_delta"] < 0).all())
    assert not deciles.empty
    size = size_stress_report(candidate_frame, [1.0, 5.0, 10.0])
    assert bool((size["complete_rate"] == 1.0).all())
    delay, detail = delay_stress_report(
        candidate_frame,
        observation_frame,
        settlement_frame,
        [1, 2],
    )
    assert set(delay["offset_seconds"]) == {1, 2}
    assert len(detail) == 40
    route_rows = []
    for candidate in candidates:
        for policy in POLICY_NAMES:
            route_rows.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "policy": policy,
                    "average_price": (0.49 if policy == "C_MAKER_FIRST_TTL2" else 0.50),
                    "filled_quantity": 1.0,
                    "fee": 0.0,
                    "requested_quantity": 1.0,
                    "fill_time_seconds": 0.0,
                }
            )
    policies, route_detail = route_report(
        candidate_frame,
        pd.DataFrame(route_rows),
        settlement_frame,
        observation_frame,
    )
    assert len(policies) == len(POLICY_NAMES) * 3
    gates = gate_report(
        PROTOCOL,
        candidate_frame,
        policies,
        calibration,
        delay,
        size,
        route_detail,
    )
    assert "C_MAKER_FIRST_TTL2:UP" in gates["policies"]
    assert (
        gates["policies"]["A_BASELINE_TAKER:UP"]["checks"][
            "policy_within_frozen_promotion_scope"
        ]
        is False
    )
    assert not any(
        value["paper_routing_eligible"] for value in gates["policies"].values()
    )


def main() -> int:
    tests = (
        test_boundaries,
        test_feature_parity,
        test_same_denominator_routing_and_store,
        test_forward_report_math,
    )
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print("polymarket repricing shadow self-test: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
