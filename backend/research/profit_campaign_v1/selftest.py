"""Adversarial invariants for PROFIT_CAMPAIGN_V1."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pandas as pd

from .contracts import Protocol, implementation_sha256
from .execution import (
    build_executable_path,
    first_eligible_index,
    simulate_round_trip,
    walk_ladder,
)
from .features import build_causal_features
from .models import QuantileHead
from .validate_result import _reconcile_trade_accounting, _validate_selector
from .validation import TrialRegistry, chronological_splits, economic_metrics


def _books() -> pd.DataFrame:
    base = 1_800_000_000_000_000_000
    rows = []
    prices = (
        (99.0, 101.0),
        (100.0, 102.0),
        (103.0, 105.0),
        (97.0, 99.0),
        (106.0, 108.0),
    )
    for index, (bid, ask) in enumerate(prices):
        rows.append(
            {
                "exchange_ts_ns": base + index * 10_000_000_000,
                "receive_ts_ns": base + index * 10_000_000_000 + 1_000_000_000,
                "sequence_id": str(index + 1),
                "sequence_healthy": True,
                "best_bid": bid,
                "best_ask": ask,
                "bid_size": 20.0,
                "ask_size": 20.0,
                "mid": (bid + ask) / 2.0,
                "spread_bps": (ask - bid) / ((ask + bid) / 2.0) * 10_000.0,
                "top_imbalance": 0.0,
                "depth_imbalance_20": 0.0,
                "bid_prices": [bid, bid - 1],
                "bid_quantities": [20.0, 20.0],
                "ask_prices": [ask, ask + 1],
                "ask_quantities": [20.0, 20.0],
            }
        )
    return pd.DataFrame(rows)


def main() -> int:
    protocol = Protocol.load()
    assert protocol.raw["research_only"] is True
    assert "no_live_activation" in protocol.raw["prohibitions"]

    partial = walk_ladder([101.0, 102.0], [1.0, 1.0], 3.0)
    assert partial.filled_quantity == 2.0
    assert partial.fill_fraction == 2.0 / 3.0
    assert partial.average_price == 101.5

    books = _books()
    flow = pd.DataFrame(
        {
            "receive_ts_ns": books["receive_ts_ns"],
            "trade_count": [1, 2, 3, 4, 5],
            "trade_notional": [100.0, 200.0, 300.0, 400.0, 500.0],
            "signed_notional": [50.0, -50.0, 100.0, -100.0, 200.0],
        }
    )
    cached_books = books.copy()
    cached_books["bid_prices"] = cached_books["bid_prices"].map(np.asarray)
    cached_books["bid_quantities"] = cached_books["bid_quantities"].map(np.asarray)
    cached_books["ask_prices"] = cached_books["ask_prices"].map(np.asarray)
    cached_books["ask_quantities"] = cached_books["ask_quantities"].map(np.asarray)
    assert len(build_causal_features(cached_books, flow)) == len(cached_books)
    receive = books["receive_ts_ns"].to_numpy(np.int64)
    decision = int(receive[0])
    assert first_eligible_index(
        receive,
        decision_ts_ns=decision,
        latency_ms=500,
        signal_expiry_ms=20_000,
    ) == 1
    long = simulate_round_trip(
        books,
        decision_ts_ns=decision,
        action="LONG",
        horizon_seconds=10,
        latency_ms=500,
        capital_usd=100.0,
        fee_bps=5.0,
        impact_bps=1.0,
        signal_expiry_ms=20_000,
        minimum_fill_fraction=1.0,
    )
    short = simulate_round_trip(
        books,
        decision_ts_ns=decision,
        action="SHORT",
        horizon_seconds=10,
        latency_ms=500,
        capital_usd=100.0,
        fee_bps=5.0,
        impact_bps=1.0,
        signal_expiry_ms=20_000,
        minimum_fill_fraction=1.0,
    )
    assert long.entry_price == 102.0 and long.exit_price == 103.0
    assert short.entry_price == 100.0 and short.exit_price == 105.0
    assert long.net_pnl_usd is not None and long.net_pnl_usd > 0
    assert short.net_pnl_usd is not None and short.net_pnl_usd < 0
    assert (long.fee_usd or 0) > 0 and (long.impact_reserve_usd or 0) > 0

    path = build_executable_path(
        books,
        decision_ts_ns=decision,
        action="LONG",
        horizon_seconds=20,
        latency_ms=500,
        capital_usd=100.0,
        fee_bps=5.0,
        impact_bps=1.0,
        signal_expiry_ms=20_000,
        minimum_fill_fraction=1.0,
    )
    assert path is not None
    assert path.net_pnl_usd[0] > 0 and path.net_pnl_usd[1] < 0

    gapped_books = books.copy()
    gapped_books.loc[2:, "exchange_ts_ns"] += 60_000_000_000
    gapped_books.loc[2:, "receive_ts_ns"] += 60_000_000_000
    gapped_flow = flow.copy()
    gapped_flow.loc[2:, "receive_ts_ns"] += 60_000_000_000
    gapped_features = build_causal_features(
        gapped_books,
        gapped_flow,
        maximum_gap_ms=10_000,
    )
    assert np.isnan(gapped_features.loc[2, "ret_5s_bps"])
    rejected_gap = simulate_round_trip(
        gapped_books,
        decision_ts_ns=decision,
        action="LONG",
        horizon_seconds=10,
        latency_ms=500,
        capital_usd=100.0,
        fee_bps=5.0,
        impact_bps=1.0,
        signal_expiry_ms=20_000,
        minimum_fill_fraction=1.0,
        maximum_book_age_ms=10_000,
    )
    assert rejected_gap.status == "REJECTED"
    assert rejected_gap.reason == "stale_exit_book"
    assert build_executable_path(
        gapped_books,
        decision_ts_ns=decision,
        action="LONG",
        horizon_seconds=10,
        latency_ms=500,
        capital_usd=100.0,
        fee_bps=5.0,
        impact_bps=1.0,
        signal_expiry_ms=20_000,
        minimum_fill_fraction=1.0,
        maximum_book_age_ms=10_000,
    ) is None

    timestamps = np.arange(120, dtype=np.int64) * 15_000_000_000
    splits, final = chronological_splits(
        timestamps,
        development_fraction=0.8,
        folds=4,
        purge_seconds=30,
        embargo_seconds=15,
    )
    assert len(splits) >= 2
    for split in [*splits, final]:
        assert set(split.train_indices).isdisjoint(split.test_indices)
        assert max(split.train_indices) < min(split.test_indices)
    small_splits, small_final = chronological_splits(
        np.arange(86, dtype=np.int64) * 900_000_000_000,
        development_fraction=0.8,
        folds=4,
        purge_seconds=900,
        embargo_seconds=900,
    )
    assert len(small_splits) >= 2 and len(small_final.test_indices) >= 10

    rng = np.random.default_rng(7)
    features = rng.normal(size=(200, 4))
    target = features[:, 0] + rng.normal(scale=0.2, size=200)
    head = QuantileHead.fit(features[:150], target[:150], seed=7)
    prediction = head.predict(features[150:])
    assert np.all(prediction[0.10] <= prediction[0.20])
    assert np.all(prediction[0.20] <= prediction[0.50])
    assert np.all(prediction[0.50] <= prediction[0.80])
    assert np.all(prediction[0.80] <= prediction[0.90])

    empty_metrics = economic_metrics(pd.DataFrame())
    assert empty_metrics["trades"] == 0
    _reconcile_trade_accounting(
        pd.DataFrame(
            {
                "gross_pnl_usd": [2.0],
                "fee_usd": [0.5],
                "impact_reserve_usd": [0.25],
                "funding_usd": [0.1],
                "net_pnl_usd": [1.35],
            }
        ),
        "selftest",
    )
    _validate_selector(
        pd.DataFrame(
            {
                "long_net_q20": [1.0, -1.0, -1.0],
                "short_net_q20": [-1.0, 1.0, -2.0],
                "uncertainty_reserve_usd": [0.2, 0.2, 0.2],
                "selector_action": ["LONG", "SHORT", "WAIT"],
            }
        )
    )
    with TemporaryDirectory() as directory:
        registry = TrialRegistry(
            Path(directory) / "trials.jsonl",
            protocol.sha256,
            implementation_sha256(),
        )
        first = registry.append(
            campaign_id="TEST",
            family="A",
            parameters={"x": 1},
            metrics={"net": 1.0},
            dataset_sha256="a" * 64,
        )
        second = registry.append(
            campaign_id="TEST",
            family="A",
            parameters={"x": 1},
            metrics={"net": 1.0},
            dataset_sha256="a" * 64,
        )
        assert first == second
        assert len((Path(directory) / "trials.jsonl").read_text().splitlines()) == 1

    print("profit-campaign-v1: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
