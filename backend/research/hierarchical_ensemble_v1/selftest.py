"""End-to-end invariant tests for the hierarchical ensemble research kernel."""
from __future__ import annotations

import math
from pathlib import Path
from tempfile import TemporaryDirectory

import duckdb

from backend.quant_platform.executable_ev import (
    ActionReturnForecast,
    WAIT,
    choose_action,
)
from backend.quant_platform.forecast_ledger import (
    EvidenceKind,
    ForecastLedger,
    ForecastOutcome,
    ForecastRecord,
)
from backend.quant_platform.model_reliability import (
    ReliabilityInputs,
    disagreement,
    reliability_adjusted_weights,
)
from backend.quant_platform.model_roles import (
    ModelRole,
    ModelRoleDefinition,
    ModelRoleRegistry,
    TargetContract,
)
from backend.quant_platform.multi_alpha_portfolio import (
    AlphaCandidate,
    allocate_promoted_alphas,
)
from backend.quant_platform.online_expert_weighting import OnlineExpertWeighting
from backend.quant_platform.target_ensemble import (
    EnsembleMethod,
    ForecastObservation,
    diversity_matrix,
    fit_probability_ensemble,
    fit_regime_mixture,
    observations_from_ledger_rows,
)
from backend.research.hierarchical_ensemble_v1.report import build_report


def _contract(
    target: str = "polymarket_up_settlement",
    role: ModelRole = ModelRole.SETTLEMENT,
    horizon: int = 3600,
) -> TargetContract:
    return TargetContract(
        target,
        role,
        "POLYMARKET",
        "BTC",
        horizon,
        "1 when UP token settles to one",
    )


def _forecast(
    forecast_id: str,
    contract: TargetContract,
    evidence: EvidenceKind,
    probability: float,
    model_id: str = "market-prior",
) -> ForecastRecord:
    return ForecastRecord(
        forecast_id=forecast_id,
        forecast_at_ns=2_000_000_000,
        market_id="market-1",
        candidate_id="candidate-1",
        model_id=model_id,
        model_version="v1",
        training_cutoff_ns=1_000_000_000,
        code_commit="a" * 40,
        dataset_sha256="b" * 64,
        feature_schema_sha256="c" * 64,
        protocol_sha256="d" * 64,
        contract=contract,
        evidence_kind=evidence,
        predicted_probability=probability,
        regime="NEAR_ANCHOR",
        data_quality=1.0,
    )


def _outcome(forecast_id: str, actual: float = 1.0) -> ForecastOutcome:
    return ForecastOutcome(
        forecast_id=forecast_id,
        resolved_at_ns=3_000_000_000,
        actual_outcome=actual,
        gross_return=0.2,
        net_return=0.18,
        fees=0.01,
        slippage=0.01,
        fill_quantity=5.0,
        latency_ms=50.0,
        resolution_source="official-settlement",
    )


def _alpha(strategy_id: str, evidence_id: str, venue: str) -> AlphaCandidate:
    return AlphaCandidate(
        strategy_id=strategy_id,
        alpha_family=strategy_id,
        evidence_id=evidence_id,
        venue=venue,
        independently_promoted=True,
        forward_decisions=1_200,
        forward_weeks=10,
        expectancy_lower_bound=0.01,
        q20_net_return=0.005,
        expected_shortfall=0.02,
        capacity_notional=100.0,
        maximum_drawdown_fraction=0.05,
        liquidity_score=0.9,
        calibration_score=0.9,
        btc_directional_exposure=0.5,
        settlement_exposure=0.5,
    )


def main() -> None:
    settlement = _contract()
    direction = _contract(
        "upper_barrier_before_lower",
        ModelRole.DIRECTION_BARRIER,
        5,
    )
    registry = ModelRoleRegistry()
    registry.register(
        ModelRoleDefinition(
            "market-prior",
            "v1",
            settlement,
            ("settlement_stacker",),
        )
    )
    registry.register(
        ModelRoleDefinition(
            "path-model",
            "v1",
            settlement,
            ("settlement_stacker",),
        )
    )
    registry.register(
        ModelRoleDefinition(
            "event-5s",
            "v1",
            direction,
            ("binance_entry",),
        )
    )
    assert registry.require_compatible(
        [("market-prior", "v1"), ("path-model", "v1")],
        "settlement_stacker",
    ) == settlement
    try:
        registry.require_compatible(
            [("market-prior", "v1"), ("event-5s", "v1")],
            "settlement_stacker",
        )
        raise AssertionError("target mismatch was accepted")
    except ValueError as exc:
        assert "target_mismatch" in str(exc)

    with TemporaryDirectory() as tmp:
        root = Path(tmp)
        ledger = ForecastLedger(root / "forecasts.duckdb")
        first = _forecast("f1", settlement, EvidenceKind.OOF, 0.8)
        digest = ledger.append_forecast(first)
        assert ledger.append_forecast(first) == digest
        try:
            ledger.append_forecast(
                _forecast("f1", settlement, EvidenceKind.OOF, 0.2)
            )
            raise AssertionError("immutable forecast collision was accepted")
        except ValueError:
            pass
        ledger.resolve(_outcome("f1"))
        second = _forecast(
            "f1-path", settlement, EvidenceKind.OOF, 0.75, "path-model"
        )
        ledger.append_forecast(second)
        ledger.resolve(_outcome("f1-path"))
        assert len(ledger.training_rows(settlement)) == 2
        assert ledger.verify_integrity() == (True, [])
        in_sample = _forecast(
            "f2", settlement, EvidenceKind.IN_SAMPLE, 0.7, "path-model"
        )
        ledger.append_forecast(in_sample)
        ledger.resolve(_outcome("f2"))
        assert len(ledger.training_rows(settlement)) == 2
        try:
            ledger.training_rows(
                settlement, evidence_kinds=(EvidenceKind.IN_SAMPLE,)
            )
            raise AssertionError("in-sample meta-training was accepted")
        except ValueError:
            pass
        report = build_report(ledger.path)
        assert report["forecasts"] == 3
        assert report["resolved_forecasts"] == 3
        oof_target = next(
            item
            for item in report["targets"]
            if item["evidence_kind"] == "OOF"
        )
        assert oof_target["aligned_resolved_candidates"] == 1
        pivoted = observations_from_ledger_rows(
            ledger.training_rows(settlement),
            settlement,
        )
        assert len(pivoted) == 1 and len(pivoted[0].probabilities) == 2

        observations = [
            ForecastObservation(
                observed_at_ns=index + 1,
                outcome=index % 2,
                probabilities={
                    "market-prior": 0.8 if index % 2 else 0.2,
                    "path-model": 0.7 if index % 2 else 0.3,
                },
                evidence_kind=EvidenceKind.OOF,
                contract_key=settlement.key,
                regime="NEAR" if index < 30 else "FAR",
            )
            for index in range(60)
        ]
        fit = fit_probability_ensemble(
            settlement,
            observations,
            method=EnsembleMethod.CONSTRAINED,
            market_prior_model_id="market-prior",
            market_prior_minimum_weight=0.50,
        )
        assert abs(sum(fit.weights) - 1.0) < 1e-9
        assert min(fit.weights) >= 0
        prior_index = fit.model_ids.index("market-prior")
        assert fit.weights[prior_index] >= 0.50
        assert 0 <= fit.predict(
            {"market-prior": 0.6, "path-model": 0.55},
            forecast_at_ns=100,
        ) <= 1
        try:
            fit.predict(
                {"market-prior": 0.6, "path-model": 0.55},
                forecast_at_ns=60,
            )
            raise AssertionError("same-period ensemble inference was accepted")
        except ValueError:
            pass
        rows = diversity_matrix(observations, "market-prior")
        assert len(rows) == 1
        try:
            fit_probability_ensemble(
                direction,
                observations,
                minimum_samples=20,
            )
            raise AssertionError("observation target mismatch was accepted")
        except ValueError as exc:
            assert "target_contract_mismatch" in str(exc)
        try:
            fit_probability_ensemble(
                settlement,
                observations,
                minimum_samples=20,
            )
            raise AssertionError("settlement stacker omitted the market prior")
        except ValueError as exc:
            assert "require a market prior" in str(exc)
        mixture = fit_regime_mixture(
            settlement,
            observations,
            minimum_global_samples=50,
            minimum_regime_samples=30,
            market_prior_model_id="market-prior",
            market_prior_minimum_weight=0.50,
        )
        assert set(mixture.regime_fits) == {"NEAR", "FAR"}

        qualities = {
            "market-prior": ReliabilityInputs(1, 1, 1, 1, 1),
            "path-model": ReliabilityInputs(0.5, 0.5, 0.5, 0.5, 0.5),
        }
        adjusted = reliability_adjusted_weights(
            {"market-prior": 0.5, "path-model": 0.5}, qualities
        )
        assert adjusted["market-prior"] > adjusted["path-model"]
        assert math.isclose(
            disagreement(
                {"market-prior": 0.8, "path-model": 0.5}
            ).range,
            0.3,
        )

        bad = ActionReturnForecast(
            "BUY_UP_TAKER",
            -0.01,
            -0.05,
            -0.02,
            0.0,
            0.02,
            0.05,
            0.05,
            0.4,
            0.01,
            0.9,
            1.0,
            True,
            True,
        )
        assert choose_action([bad]).action == WAIT
        good = ActionReturnForecast(
            "BUY_DOWN_MAKER",
            0.04,
            0.005,
            0.01,
            0.03,
            0.06,
            0.09,
            0.02,
            0.7,
            0.005,
            0.9,
            1.0,
            True,
            True,
        )
        assert choose_action([bad, good], tail_risk_reserve=0.005).action == (
            "BUY_DOWN_MAKER"
        )

        online = OnlineExpertWeighting(
            root / "online.duckdb",
            minimum_resolved_updates=50,
        )
        for index in range(50):
            online.append_resolved_losses(
                update_id=f"u{index:03d}",
                resolved_at_ns=index + 1,
                ensemble_key=settlement.key,
                regime="NEAR",
                losses={"market-prior": 0.05, "path-model": 0.30},
                evidence_kind=EvidenceKind.FORWARD,
            )
        weights = online.weights(
            ensemble_key=settlement.key,
            regime="NEAR",
            model_ids=("market-prior", "path-model"),
        )
        assert weights["market-prior"] > weights["path-model"]
        assert all(0.05 <= value <= 0.70 for value in weights.values())
        assert online.verify_chain() == (True, [])
        rolled_back = online.weights(
            ensemble_key=settlement.key,
            regime="NEAR",
            model_ids=("market-prior", "path-model"),
            through_ns=49,
        )
        assert rolled_back == {"market-prior": 0.5, "path-model": 0.5}
        try:
            online.append_resolved_losses(
                update_id="bad",
                resolved_at_ns=100,
                ensemble_key=settlement.key,
                regime="NEAR",
                losses={"market-prior": 0.1, "path-model": 0.1},
                evidence_kind=EvidenceKind.OOF,
            )
            raise AssertionError("OOF evidence changed online weights")
        except ValueError:
            pass
        online.append_resolved_losses(
            update_id="u000",
            resolved_at_ns=1,
            ensemble_key=settlement.key,
            regime="NEAR",
            losses={"market-prior": 0.05, "path-model": 0.30},
            evidence_kind=EvidenceKind.FORWARD,
        )
        try:
            online.append_resolved_losses(
                update_id="u000",
                resolved_at_ns=1,
                ensemble_key=settlement.key,
                regime="FAR",
                losses={"market-prior": 0.05, "path-model": 0.30},
                evidence_kind=EvidenceKind.FORWARD,
            )
            raise AssertionError("online update metadata collision was accepted")
        except ValueError:
            pass

        with duckdb.connect(str(ledger.path)) as con:
            con.execute(
                "UPDATE model_forecasts SET data_quality = 0.5 "
                "WHERE forecast_id = 'f1'"
            )
        integrity_ok, integrity_reasons = ledger.verify_integrity()
        assert not integrity_ok
        assert "forecast_column_mismatch:f1" in integrity_reasons

    one = [_alpha("alpha-a", "proof-a", "POLYMARKET")]
    assert allocate_promoted_alphas(
        one, correlations={}, capital=10_000
    ) == {}
    two = one + [_alpha("alpha-b", "proof-b", "BINANCE")]
    allocations = allocate_promoted_alphas(
        two,
        correlations={("alpha-a", "alpha-b"): 0.2},
        capital=10_000,
    )
    assert set(allocations) == {"alpha-a", "alpha-b"}
    assert all(0 < value <= 50 for value in allocations.values())
    duplicate_family = [
        one[0],
        AlphaCandidate(
            **{
                field: (
                    "alpha-a"
                    if field == "alpha_family"
                    else getattr(two[1], field)
                )
                for field in two[1].__dataclass_fields__
            }
        ),
    ]
    assert allocate_promoted_alphas(
        duplicate_family,
        correlations={("alpha-a", "alpha-b"): 0.2},
        capital=10_000,
    ) == {}

    print("hierarchical target-specific ensemble: ALL PASS")


if __name__ == "__main__":
    main()
