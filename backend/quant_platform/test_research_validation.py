"""Tests for frozen research protocols and promotion gates."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from .promotion_gates import (
    PromotionStatus,
    StrategyEvidence,
    evaluate_promotion,
)
from .research_validation import (
    ExperimentProtocol,
    block_bootstrap_mean_interval,
    deflated_sharpe_ratio,
    probability_backtest_overfitting,
    profit_concentration,
    purged_walk_forward_splits,
)


def _passing_evidence() -> StrategyEvidence:
    return StrategyEvidence(
        forward_trades=600,
        forward_weeks=12,
        trading_days=50,
        mean_expectancy=0.02,
        day_block_lower_bound=0.005,
        profit_factor=1.5,
        positive_fee_stress_50=True,
        positive_slippage_stress_50=True,
        positive_latency_1000ms=True,
        majority_weeks_positive=True,
        single_day_profit_concentration=0.10,
        single_regime_profit_concentration=0.30,
        maximum_drawdown=0.05,
        pbo=0.10,
        deflated_sharpe_probability=0.99,
        paper_live_execution_divergence=0.10,
    )


def main() -> None:
    splits = purged_walk_forward_splits(100, 40, 10, 3, 2)
    assert splits[0].train_end == 40
    assert splits[0].test_start == 43
    assert set(splits[0].train_indices).isdisjoint(splits[0].test_indices)
    assert splits[1].test_start - splits[0].test_end == 2

    interval = block_bootstrap_mean_interval(
        [[1.0, 2.0], [2.0, 3.0], [3.0, 4.0]],
        iterations=500,
        seed=7,
    )
    assert interval["lower"] <= interval["mean"] <= interval["upper"]

    pbo = probability_backtest_overfitting(
        [
            [1.0, 0.0, -1.0],
            [0.8, 0.1, -0.5],
            [1.1, -0.1, -0.7],
            [0.9, 0.2, -0.8],
        ]
    )
    assert 0.0 <= pbo["pbo"] <= 1.0
    assert pbo["combinations"] == 6.0

    dsr = deflated_sharpe_ratio(1.5, 200, 20, 0.4)
    assert 0.0 <= dsr["probability"] <= 1.0
    assert dsr["expected_max_sharpe"] > 0
    assert profit_concentration([5.0, 3.0, -2.0, 2.0]) == 0.5

    protocol = ExperimentProtocol(
        strategy_id="test-v1",
        hypothesis="a causal, executable edge exists",
        instrument="BTCUSDT",
        feature_schema_sha256="a" * 64,
        data_period="2025-01-01/2026-01-01",
        entry_rule="frozen entry",
        exit_rule="frozen exit",
        parameters_json='{"threshold":0.5}',
        configurations_tried=1,
        cost_model="taker fees plus depth VWAP",
        latency_model="1000ms stress",
        promotion_gate="quant-platform-v1",
        code_sha256="b" * 64,
        dataset_sha256="c" * 64,
    )
    with TemporaryDirectory() as tmp:
        first = protocol.freeze(Path(tmp))
        second = protocol.freeze(Path(tmp))
        assert first == second and first.exists()

    passed = evaluate_promotion(_passing_evidence())
    assert passed.status is PromotionStatus.ELIGIBLE_FOR_LIVE_REVIEW
    failed_values = _passing_evidence().__dict__ if hasattr(_passing_evidence(), "__dict__") else None
    assert failed_values is None  # slots prevent accidental mutable evidence state
    failed = evaluate_promotion(
        StrategyEvidence(
            **{
                **{field: getattr(_passing_evidence(), field)
                   for field in _passing_evidence().__dataclass_fields__},
                "forward_trades": 100,
            }
        )
    )
    assert failed.status is PromotionStatus.PAPER_ONLY
    assert "gate_failed:forward_trades" in failed.reasons

    print("quant-platform research validation: ALL PASS")


if __name__ == "__main__":
    main()
