from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from backend.paper_competition import (
    CompetitionConfig,
    PaperCompetition,
    summarize_polymarket,
)


def _config(path: Path) -> CompetitionConfig:
    return CompetitionConfig(
        bankroll_usd=500.0,
        position_fraction=0.10,
        exposure_fraction=0.20,
        daily_loss_fraction=0.05,
        weekly_loss_fraction=0.12,
        maximum_drawdown_fraction=0.10,
        polymarket_rule="CHAMPION_DYNAMIC_PAPER_V1",
        binance_strategy="model_consensus",
        state_path=path,
    )


def _poly_row(source: str) -> dict:
    return {
        "round_id": "round-1",
        "ts": 1_000,
        "ask": 0.49,
        "fee": 0.01,
        "depth": 100.0,
        "settled_ts": 2_000,
        "exit_gross": 1.0,
        "exit_fee": 0.0,
        "exit_reason": "SETTLED",
        "settlement_source": source,
        "pnl": 0.50,
        "model_bundle_id": "main-release-a",
        "target_contract": "first_touch_triple_barrier_v1",
        "head_identity_json": json.dumps({"p_hold": {"sha256": "a" * 64}}),
    }


def test_proxy_settlement_is_not_ranked_as_realized(tmp_path: Path):
    summary = summarize_polymarket(
        [_poly_row("pyth_proxy")], _config(tmp_path / "race.json")
    )
    assert summary["closed_trades"] == 0
    assert summary["realized_net_pnl_usd"] == 0.0
    assert summary["open_positions"] == 1
    assert summary["provisional_settlements"] == 1


def test_official_settlement_is_ranked(tmp_path: Path):
    summary = summarize_polymarket(
        [_poly_row("official:polymarket")], _config(tmp_path / "race.json")
    )
    assert summary["closed_trades"] == 1
    assert summary["realized_net_pnl_usd"] == 50.0
    assert summary["provisional_settlements"] == 0


def test_empty_or_incomplete_head_identity_blocks_ranking(tmp_path: Path):
    row = _poly_row("official:polymarket")
    row["head_identity_json"] = "{}"
    summary = summarize_polymarket([row], _config(tmp_path / "race.json"))
    assert summary["trust_state"] == "BLOCKED_MODEL_PROVENANCE_MISSING"
    assert summary["missing_provenance_entries"] == 1


def test_main_bundle_change_is_a_mixed_release(tmp_path: Path):
    first = _poly_row("official:polymarket")
    second = {**first, "round_id": "round-2", "model_bundle_id": "main-release-b"}
    summary = summarize_polymarket([first, second], _config(tmp_path / "race.json"))
    assert summary["trust_state"] == "BLOCKED_MIXED_MODEL_RELEASES"
    assert summary["model_release_count"] == 2


class _Persistence:
    @staticmethod
    def trades(*_args):
        return []

    @staticmethod
    def competition_trades_since(*_args):
        return []


class _Service:
    def __init__(self, db_path: Path):
        self.config = SimpleNamespace(
            db_path=db_path.resolve(),
            starting_cash_usd=500.0,
            fee_rate_bps=5.0,
            slippage_bps=1.0,
            latency_ms=500,
            quote_stale_ms=2_000,
            source_stale_ms=3_000,
            max_transport_lag_ms=2_000,
        )
        self.persistence = _Persistence()

    @staticmethod
    def status():
        return {"initialized": True, "runtime_state": "PAUSED"}

    @staticmethod
    def strategy_statuses():
        return [{
            "strategy_id": "model_consensus",
            "version": "v1",
            "config_hash": "config-a",
            "enabled": True,
            "risk": {
                "max_position_notional_usd": 50.0,
                "max_account_exposure_usd": 100.0,
                "maximum_daily_loss_usd": 25.0,
                "maximum_weekly_loss_usd": 60.0,
                "maximum_drawdown_fraction": 0.10,
            },
            "account": {
                "starting_cash_usd": 500.0,
                "available_cash_usd": 500.0,
                "equity_usd": 500.0,
                "realized_pnl_usd": 0.0,
                "trading_fees_usd": 0.0,
                "funding_usd": 0.0,
                "maximum_drawdown_usd": 0.0,
            },
            "position": None,
            "latest_decision": None,
            "inactive_reason": "Engine paused",
        }]


def test_execution_contract_change_blocks_comparison(tmp_path: Path):
    service = _Service(tmp_path / "binance.duckdb")
    race = PaperCompetition(
        service, lambda _rule, _since: [], _config(tmp_path / "race.json")
    )
    first = race.summary()
    assert first["comparable"] is True
    state = json.loads((tmp_path / "race.json").read_text(encoding="utf-8"))
    assert "backend/binance_endpoint_serving.py" in state["binance_contract"]["source_files"]
    assert "backend/price_to_beat.py" in state["polymarket_contract"]["source_files"]
    service.config.fee_rate_bps = 4.0
    changed = race.summary()
    assert changed["comparable"] is False
    assert changed["binance"]["trust_state"] == "BLOCKED_CONFIGURATION_MISMATCH"


def test_corrupt_state_fails_closed_without_api_exception(tmp_path: Path):
    state_path = tmp_path / "race.json"
    state_path.write_text(json.dumps({"version": 2}), encoding="utf-8")
    race = PaperCompetition(
        _Service(tmp_path / "binance.duckdb"),
        lambda _rule, _since: [],
        _config(state_path),
    )
    summary = race.summary()
    assert summary["comparable"] is False
    assert summary["leader"] == "NOT_COMPARABLE"
    assert summary["polymarket"]["trust_state"] == "ACCOUNTING_UNAVAILABLE"
