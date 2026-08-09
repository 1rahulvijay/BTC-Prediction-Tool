from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import price_to_beat
import binance_endpoint_serving

from backend.binance_paper.schemas import Action, DataQuality, MarketSnapshot
from backend.binance_paper.service import BinancePaperService
from backend.binance_paper.strategies.model_consensus import ModelConsensusStrategy
from backend.polymarket.model_dynamic_paper import entry_decision, exit_decision
import target_contract as _tc


NOW = 1_800_000_000_000
MARK = 64_000.0


def _prediction(**overrides):
    value = {
        "horizon": 5,
        "direction": "UP",
        "finalDirection": "UP",
        "endpointHeadReady": True,
        "calibratedConfidence": 0.72,
        "probUp": 0.72,
        "probDown": 0.28,
        "expectedMove": 420.0,
        "expectedMoveRange": {"low": 400.0, "median": 430.0, "high": 500.0},
        "stopLoss": MARK * 0.998,
        "model_bundle_id": "bundle-v14-test",
        "research_only": True,
        "independence_validated": True,
        "holdout_metrics": {"beats_prior": True},
        # The EV this strategy computes is an ENDPOINT question, so the fixture
        # supplies a settlement-contract probability. Omitting it is now itself a
        # refusal, which the dedicated test below pins.
        "targetContract": _tc.ROLLING_EXCHANGE_RETURN_SIGN_V1,
    }
    value.update(overrides)
    return value


def _snapshot(prediction=None, *, updated_at_ms=NOW, bid=None, ask=None):
    best_bid = float(bid if bid is not None else MARK - 3.2)
    best_ask = float(ask if ask is not None else MARK + 3.2)
    return MarketSnapshot(
        symbol="BTCUSDT",
        event_ts_ms=NOW,
        received_at_ms=NOW,
        mark_price=(best_bid + best_ask) / 2.0,
        best_bid=best_bid,
        best_ask=best_ask,
        bid_size=5.0,
        ask_size=5.0,
        spread=best_ask - best_bid,
        spread_bps=(best_ask - best_bid) / MARK * 10_000.0,
        feed_age_ms=10,
        feed_health=DataQuality.HEALTHY,
        update_id=1,
        funding_rate=0.0001,
        funding_time_ms=NOW + 3_600_000,
        agg_trade_age_ms=20,
        agg_trade_message_count=1_000,
        agg_trade_count_60s=800,
        last_completed_perp_cvd_bar_ts_ms=NOW - 60_000,
        feature_availability={"endpoint_direction_head": bool(prediction)},
        model_context={
            "updated_at_ms": updated_at_ms,
            "model_trained": True,
            "endpoint_prediction": prediction if prediction else None,
        },
    )


def test_binance_model_consensus_refuses_a_path_probability():
    """A first-touch probability may not price an endpoint EV.

    The EV is (2p - 1) * move - costs, which treats p as the probability that the ENDPOINT
    lands on the predicted side. A first-touch probability answers "which barrier is touched
    first" and disagrees on roughly a quarter of random-walk paths. Both are floats in [0, 1],
    so nothing about the value would have revealed the substitution - only the declared
    contract can.
    """
    strategy = ModelConsensusStrategy()

    path = _prediction(targetContract=_tc.FIRST_TOUCH_TRIPLE_BARRIER_V1)
    decision = strategy.decide(_snapshot(path))
    assert decision.action is Action.NO_EDGE
    assert "target_contract_inadmissible" in decision.reason_codes, decision.reason_codes

    unlabelled = _prediction()
    del unlabelled["targetContract"]
    decision = strategy.decide(_snapshot(unlabelled))
    assert decision.action is Action.NO_EDGE, "an UNLABELLED probability must be refused too"
    assert "target_contract_inadmissible" in decision.reason_codes

    # THE OPERATIONAL CONSEQUENCE, pinned. The models currently trained produce first-touch
    # labels, so this strategy cannot act until a SETTLEMENT head exists. That is the honest
    # state of the mismatch, not a test artefact.
    assert _tc.TRAINING_CONTRACT in _tc.PATH_CONTRACTS
    try:
        _tc.assert_admissible(_tc.BINANCE_DIRECTIONAL_PAPER_EV, _tc.TRAINING_CONTRACT)
        raise AssertionError("the current training contract was accepted for an endpoint EV")
    except _tc.ContractMisuse:
        pass


def test_binance_model_consensus_abstains_on_heuristic_uncertainty():
    """A fixed 5pp haircut may not authorise capital.

    Before 2026-08-04 this strategy opened a position whenever `probability - 0.05` produced a
    positive EV, and that quantity was named `lower_bound_ev_bps`. It is not a lower bound - it
    is a hand-chosen pessimism constant, and the EV it feeds assumes wins and losses have the
    same average magnitude. This test previously asserted OPEN_LONG, so it PINNED that.
    """
    strategy = ModelConsensusStrategy()
    decision = strategy.decide(_snapshot(_prediction()))
    assert decision.action is Action.NO_EDGE
    assert any("uncertainty_is_heuristic_not_measured" in code
               for code in decision.reason_codes), decision.reason_codes
    assert strategy.uncertainty_method == "fixed_haircut"


def test_binance_model_consensus_ev_path_still_computes(monkeypatch):
    """With the research opt-in, the ORIGINAL economics still hold end to end.

    Keeps the coverage the old test provided - the EV, target and stop are still built and
    still sane - while the abstention above owns the authority question.
    """
    monkeypatch.setenv("BTC_BINANCE_PAPER_ALLOW_HEURISTIC_EV", "1")
    strategy = ModelConsensusStrategy()
    decision = strategy.decide(_snapshot(_prediction()))
    assert decision.action is Action.OPEN_LONG
    assert decision.probability_calibrated is True
    assert decision.expected_net_pnl_heuristic_haircut_usd > 0.0
    assert decision.take_profit_price > MARK
    assert decision.stop_price < MARK
    assert decision.decision_mark_price == MARK
    target_bps = (decision.take_profit_price - MARK) / MARK * 10_000.0
    assert target_bps >= strategy.minimum_take_profit_bps
    # The renamed fields carry the honest names.
    assert "heuristic_haircut_ev_bps" in decision.features
    assert "lower_bound_ev_bps" not in decision.features
    assert decision.features["ev_payoff_assumption"] == "symmetric_magnitude"


def test_binance_model_consensus_ungrouped_head_requires_explicit_paper_opt_in(
    monkeypatch,
):
    monkeypatch.setenv("BTC_BINANCE_PAPER_ALLOW_HEURISTIC_EV", "1")
    strategy = ModelConsensusStrategy()
    prediction = _prediction(independence_validated=False)
    refused = strategy.decide(_snapshot(prediction))
    assert refused.action is Action.NO_EDGE
    assert any("endpoint_holdout_not_independent" in code for code in refused.reason_codes)

    monkeypatch.setenv("BTC_BINANCE_PAPER_ALLOW_UNGROUPED_HEAD", "1")
    admitted = strategy.decide(_snapshot(prediction))
    assert admitted.action is Action.OPEN_LONG
    assert admitted.features["independence_validated"] is False


def test_binance_endpoint_serving_uses_gated_endpoint_probability(monkeypatch):
    bundle = {
        "metrics": {5: {"beats_prior": True, "holdout_rows": 200}},
        "independence_validated": False,
    }
    monkeypatch.setattr(
        binance_endpoint_serving,
        "_load",
        lambda model_dir: (bundle, {"status": "READY", "artifact_sha256": "abc"}),
    )
    monkeypatch.setattr(
        binance_endpoint_serving,
        "settlement_probability",
        lambda loaded, row, horizon: {
            "p_up": 0.68,
            "p_down": 0.32,
            "target_contract": _tc.ROLLING_EXCHANGE_RETURN_SIGN_V1,
        },
    )
    prediction, status = binance_endpoint_serving.predict(
        ".",
        sequence=[[1.0, 2.0], [3.0, 4.0]],
        feature_selector=lambda value: value,
        magnitude_prediction={
            "expectedMove": 120.0,
            "expectedMoveRange": {"low": 80.0, "median": 120.0, "high": 180.0},
            "stopLoss": MARK - 100.0,
        },
    )
    assert status["status"] == "READY"
    assert prediction is not None
    assert prediction["finalDirection"] == "UP"
    assert prediction["calibratedConfidence"] == 0.68
    assert prediction["targetContract"] == _tc.ROLLING_EXCHANGE_RETURN_SIGN_V1
    assert prediction["independence_validated"] is False

    monkeypatch.setattr(
        binance_endpoint_serving,
        "settlement_probability",
        lambda loaded, row, horizon: {
            "p_up": 0.70,
            "p_down": 0.40,
            "target_contract": _tc.ROLLING_EXCHANGE_RETURN_SIGN_V1,
        },
    )
    prediction, invalid_mass = binance_endpoint_serving.predict(
        ".", [[1.0]], lambda value: value,
        {"expectedMove": 10.0, "expectedMoveRange": {"low": 5.0}},
    )
    assert prediction is None
    assert invalid_mass["status"] == "PROBABILITY_MASS_INVALID"

    blocked = {"metrics": {5: {"beats_prior": False}}}
    monkeypatch.setattr(
        binance_endpoint_serving,
        "_load",
        lambda model_dir: (blocked, {"status": "READY"}),
    )
    prediction, status = binance_endpoint_serving.predict(
        ".", [[1.0]], lambda value: value,
        {"expectedMove": 10.0, "expectedMoveRange": {"low": 5.0}},
    )
    assert prediction is None
    assert status["status"] == "HOLDOUT_GATE_FAILED"


def test_binance_endpoint_serving_reloads_replaced_artifact(tmp_path, monkeypatch):
    artifact = tmp_path / "settlement_head.pkl"
    artifact.write_bytes(b"one")
    loads = []

    def fake_load(path):
        loads.append(path.read_bytes())
        return {
            "target_contract": _tc.ROLLING_EXCHANGE_RETURN_SIGN_V1,
            "metrics": {5: {"beats_prior": True}},
        }

    monkeypatch.setattr(
        binance_endpoint_serving,
        "artifact_matches_current_training",
        lambda path: (True, []),
    )
    monkeypatch.setattr(binance_endpoint_serving, "verified_load", fake_load)
    binance_endpoint_serving.reset_cache_for_tests()
    first, _ = binance_endpoint_serving._load(tmp_path)
    cached, _ = binance_endpoint_serving._load(tmp_path)
    assert first is cached
    assert loads == [b"one"]

    artifact.write_bytes(b"replacement")
    replaced, _ = binance_endpoint_serving._load(tmp_path)
    assert replaced is not cached
    assert loads == [b"one", b"replacement"]
    binance_endpoint_serving.reset_cache_for_tests()


def test_binance_model_consensus_rejects_bad_inputs(monkeypatch):
    monkeypatch.setenv("BTC_BINANCE_PAPER_ALLOW_HEURISTIC_EV", "1")
    strategy = ModelConsensusStrategy()

    uncalibrated = _prediction(calibratedConfidence=None)
    assert strategy.decide(_snapshot(uncalibrated)).action is Action.NO_DATA
    unavailable = _prediction(endpointHeadReady=False)
    assert strategy.decide(_snapshot(unavailable)).action is Action.NO_DATA
    stale = strategy.decide(
        _snapshot(_prediction(), updated_at_ms=NOW - strategy.maximum_model_age_ms - 1)
    )
    assert stale.action is Action.NO_DATA
    future = strategy.decide(_snapshot(_prediction(), updated_at_ms=NOW + 1))
    assert future.action is Action.NO_DATA
    assert "model_context_newer_than_market_snapshot" in future.reason_codes
    malformed = strategy.decide(
        _snapshot(_prediction(calibratedConfidence="not-a-number"))
    )
    assert malformed.action is Action.NO_DATA
    ungrouped = strategy.decide(_snapshot(_prediction(independence_validated=False)))
    assert ungrouped.action is Action.NO_EDGE


def test_binance_model_consensus_dynamic_exits_are_causal_and_fail_closed():
    strategy = ModelConsensusStrategy()
    position = {"side": "LONG", "entry_price": MARK}
    flipped = _prediction(direction="DOWN", finalDirection="DOWN")
    assert strategy.position_exit_reason(position, _snapshot(flipped)) == "MODEL_DIRECTION_FLIP"

    decayed = _prediction(
        calibratedConfidence=0.54,
        probUp=0.54,
        probDown=0.46,
    )
    profitable = _snapshot(decayed, bid=MARK * 1.002, ask=MARK * 1.0021)
    assert (
        strategy.position_exit_reason(position, profitable)
        == "MODEL_EDGE_DECAY_PROFIT_LOCK"
    )
    stale = _snapshot(
        _prediction(), updated_at_ms=NOW - strategy.maximum_model_age_ms - 1
    )
    assert strategy.position_exit_reason(position, stale) == "MODEL_CONTEXT_STALE"


def test_binance_service_routes_model_dynamic_exit_through_normal_close_lifecycle():
    class Client:
        pass

    class Persistence:
        @staticmethod
        def open_positions():
            return [
                {
                    "position_id": "p1",
                    "strategy_id": "model_consensus",
                    "side": "LONG",
                    "entry_price": MARK,
                }
            ]

    service = BinancePaperService(Client())
    service.persistence = Persistence()
    service._pending = {}
    captured = []

    def capture(position, snapshot, *, signal_id, exit_reason, reversal_decision=None):
        captured.append((position["position_id"], signal_id, exit_reason))

    service._queue_exit = capture
    service._queue_triggered_exits(
        _snapshot(_prediction(direction="DOWN", finalDirection="DOWN"))
    )
    assert len(captured) == 1
    assert captured[0][0] == "p1"
    assert captured[0][2] == "MODEL_DIRECTION_FLIP"


def test_binance_market_status_reports_all_model_inputs_available():
    class Client:
        @staticmethod
        def health_snapshot(now_ms=None):
            return {
                "book_message_count": 1,
                "book_age_ms": 0,
                "agg_trade_message_count": 1,
                "agg_trade_age_ms": 0,
                "last_completed_perp_cvd_bar_ts_ms": None,
            }

    prediction = _prediction()
    service = BinancePaperService(
        Client(),
        model_context_provider=lambda: {
            "updated_at_ms": NOW,
            "model_trained": True,
            "endpoint_prediction": prediction,
        },
    )
    service.adapter.ingest_book(
        {
            "symbol": "BTCUSDT",
            "best_bid": MARK - 3.2,
            "best_ask": MARK + 3.2,
            "bid_size": 5.0,
            "ask_size": 5.0,
            "event_ts_ms": NOW - 1,
            "received_at_ms": NOW,
            "update_id": 1,
        }
    )
    availability = service.adapter.snapshot(NOW).feature_availability
    assert availability["endpoint_direction_head"] is True
    assert availability["endpoint_probability_calibration"] is True
    assert availability["endpoint_model_bundle_identity"] is True
    assert availability["magnitude_forecast"] is True


def _pm_round(**overrides):
    value = {
        "horizon": 5,
        "seconds_left": 45.0,
        "current_position": "UP",
        "p_hold": 0.90,
        "champion": {
            "action": "PAPER_BET",
            "bet_candidate": True,
            "edge": 0.08,
            "confidence": 90,
            "risk_flags": [],
        },
    }
    value.update(overrides)
    return value


def _pm_entry_quote():
    return {
        "side": "UP",
        "ask": 0.60,
        "bid": 0.59,
        "fee": 0.0168,
        "spread": 0.01,
        "depth": 10.0,
    }


def test_polymarket_champion_dynamic_entry_uses_existing_authority_and_costs():
    result = entry_decision(_pm_round(), _pm_entry_quote())
    assert result["action"] == "ENTER"
    state = result["state"]
    assert state["target_net"] == 0.04
    assert state["stop_net"] == -0.025
    assert state["side"] == "UP"

    blocked = _pm_round(
        champion={"action": "NO_EDGE", "bet_candidate": False, "edge": 0.08}
    )
    result = entry_decision(blocked, _pm_entry_quote())
    assert result["action"] == "NO_TRADE"
    assert "champion_did_not_authorize_paper_entry" in result["reason_codes"]
    malformed_quote = {**_pm_entry_quote(), "fee": float("nan")}
    assert entry_decision(_pm_round(), malformed_quote)["action"] == "NO_TRADE"
    assert entry_decision(_pm_round(p_hold=float("nan")), _pm_entry_quote())[
        "action"
    ] == "NO_TRADE"


def test_polymarket_champion_dynamic_exit_uses_executable_bid_and_exact_fees():
    state = entry_decision(_pm_round(), _pm_entry_quote())["state"]
    target = exit_decision(
        state,
        _pm_round(),
        {"side": "UP", "bid": 0.70, "fee_out": 0.0147},
    )
    assert target["action"] == "EXIT"
    assert target["exit_reason"] == "DYNAMIC_TARGET"
    assert abs(target["net_pnl"] - (0.70 - 0.0147 - 0.60 - 0.0168)) < 1e-12

    invalidated = exit_decision(
        state,
        _pm_round(current_position="DOWN", p_hold=0.80),
        {"side": "UP", "bid": 0.62, "fee_out": 0.01649},
    )
    assert invalidated["action"] == "EXIT"
    assert invalidated["exit_reason"] == "MODEL_INVALIDATED"

    missing = exit_decision(state, _pm_round(), None)
    assert missing["action"] == "HOLD"
    assert "fresh_exit_quote_unavailable" in missing["reason_codes"]
    corrupt = exit_decision(
        {**state, "entry": "corrupt"},
        _pm_round(),
        {"side": "UP", "bid": 0.70, "fee_out": 0.0147},
    )
    assert corrupt["action"] == "HOLD"
    assert "position_state_invalid" in corrupt["reason_codes"]


def test_polymarket_side_quote_preserves_side_for_dynamic_exit(monkeypatch):
    anchor_ts = NOW // 1000
    monkeypatch.setattr(
        price_to_beat,
        "_PM_QUOTES",
        {
            "markets": {
                "5": {
                    "anchor_ts": anchor_ts,
                    "ts": anchor_ts + 10,
                    "fees_enabled": True,
                    "fee_rate": 0.07,
                    "up_bid": 0.61,
                    "up_ask": 0.62,
                    "up_top_ask_size": 7.0,
                }
            }
        },
    )
    quote = price_to_beat._side_quote(
        {"horizon": 5, "window_start": NOW},
        NOW + 10_000,
        "UP",
    )
    assert quote is not None
    assert quote["side"] == "UP"
    assert quote["fee_out"] == round(0.07 * 0.61 * 0.39, 5)
