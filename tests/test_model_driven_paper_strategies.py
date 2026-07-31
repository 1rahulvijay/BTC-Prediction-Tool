from __future__ import annotations

import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import price_to_beat

from backend.binance_paper.schemas import Action, DataQuality, MarketSnapshot
from backend.binance_paper.service import BinancePaperService
from backend.binance_paper.strategies.model_consensus import ModelConsensusStrategy
from backend.polymarket.model_dynamic_paper import entry_decision, exit_decision


NOW = 1_800_000_000_000
MARK = 64_000.0


def _prediction(**overrides):
    value = {
        "horizon": 5,
        "direction": "UP",
        "finalDirection": "UP",
        "trade_verdict": "TRADE",
        "finalAction": "TRADE",
        "actionable": True,
        "no_trade_reasons": [],
        "calibratedConfidence": 0.72,
        "agreement": 0.80,
        "metaTrust": 0.70,
        "expectedMove": 420.0,
        "expectedMoveRange": {"low": 400.0, "median": 430.0, "high": 500.0},
        "stopLoss": MARK * 0.998,
        "model_bundle_id": "bundle-v14-test",
        "regime": "TRENDING_UP",
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
        feature_availability={"ensemble_prediction": bool(prediction)},
        model_context={
            "updated_at_ms": updated_at_ms,
            "model_trained": True,
            "predictions": {5: prediction} if prediction else {},
        },
    )


def test_binance_model_consensus_requires_final_calibrated_positive_ev_trade():
    strategy = ModelConsensusStrategy()
    decision = strategy.decide(_snapshot(_prediction()))
    assert decision.action is Action.OPEN_LONG
    assert decision.probability_calibrated is True
    assert decision.expected_net_pnl_lower_bound_usd > 0.0
    assert decision.take_profit_price > MARK
    assert decision.stop_price < MARK
    target_bps = (decision.take_profit_price - MARK) / MARK * 10_000.0
    assert target_bps >= strategy.minimum_take_profit_bps

    uncalibrated = _prediction(calibratedConfidence=None)
    assert strategy.decide(_snapshot(uncalibrated)).action is Action.NO_DATA
    rejected = _prediction(finalAction="NO_TRADE", trade_verdict="NO_TRADE", actionable=False)
    assert strategy.decide(_snapshot(rejected)).action is Action.NO_EDGE
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
    invalid_agreement = strategy.decide(_snapshot(_prediction(agreement=float("nan"))))
    assert invalid_agreement.action is Action.NO_EDGE


def test_binance_model_consensus_dynamic_exits_are_causal_and_fail_closed():
    strategy = ModelConsensusStrategy()
    position = {"side": "LONG", "entry_price": MARK}
    flipped = _prediction(direction="DOWN", finalDirection="DOWN")
    assert strategy.position_exit_reason(position, _snapshot(flipped)) == "MODEL_DIRECTION_FLIP"

    decayed = _prediction(
        finalAction="NO_TRADE",
        trade_verdict="NO_TRADE",
        actionable=False,
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
            "predictions": {5: prediction},
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
    assert availability["ensemble_prediction"] is True
    assert availability["live_probability_calibration"] is True
    assert availability["model_bundle_identity"] is True


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
