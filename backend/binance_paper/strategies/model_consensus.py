"""Cost-aware paper strategy driven by the final main-ensemble decision.

This strategy does not reinterpret raw model votes. It consumes the same final decision shown
by the app after meta filtering, live-quality filtering, expectancy filtering and the do-not-
trade reason engine. Missing live calibration, stale context or any blocking reason produces
NO_DATA/NO_EDGE. The lane is paper-only and exists to measure whether the full model stack adds
economic value over the zero-information control.
"""
from __future__ import annotations

import math
from typing import Any

from ..schemas import Action, DataQuality, MarketSnapshot, PositionSide
from ..strategy_base import StrategyBase


class ModelConsensusStrategy(StrategyBase):
    strategy_id = "model_consensus"
    strategy_name = "Model Consensus (calibrated, cost-aware)"
    strategy_version = "paper-v1"
    timeframe = "5m ensemble / max 5m hold"
    required_inputs = (
        "perpetual_book",
        "ensemble_prediction",
        "live_probability_calibration",
        "model_bundle_identity",
    )

    horizon = 5
    maximum_model_age_ms = 90_000
    minimum_calibrated_probability = 0.58
    minimum_agreement = 0.67
    minimum_meta_trust = 0.55
    conservative_probability_haircut = 0.05
    target_capture_fraction = 0.80
    minimum_stop_bps = 8.0
    maximum_stop_bps = 60.0
    maximum_target_bps = 100.0
    requested_notional_usd = 500.0
    maximum_holding_seconds = 300
    minimum_lower_bound_ev_bps = 0.0
    profit_lock_buffer_bps = 2.0

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "maximum_model_age_ms": self.maximum_model_age_ms,
            "minimum_calibrated_probability": self.minimum_calibrated_probability,
            "minimum_agreement": self.minimum_agreement,
            "minimum_meta_trust": self.minimum_meta_trust,
            "conservative_probability_haircut": self.conservative_probability_haircut,
            "target_capture_fraction": self.target_capture_fraction,
            "minimum_stop_bps": self.minimum_stop_bps,
            "maximum_stop_bps": self.maximum_stop_bps,
            "maximum_target_bps": self.maximum_target_bps,
            "requested_notional_usd": self.requested_notional_usd,
            "maximum_holding_seconds": self.maximum_holding_seconds,
            "minimum_lower_bound_ev_bps": self.minimum_lower_bound_ev_bps,
            "profit_lock_buffer_bps": self.profit_lock_buffer_bps,
        }

    def _prediction(self, snapshot: MarketSnapshot) -> tuple[dict, int]:
        context = snapshot.model_context or {}
        try:
            updated_at_ms = int(context.get("updated_at_ms") or 0)
        except (TypeError, ValueError, OverflowError):
            updated_at_ms = 0
        predictions = context.get("predictions") or {}
        if isinstance(predictions, dict):
            prediction = (
                predictions.get(self.horizon)
                or predictions.get(str(self.horizon))
                or {}
            )
        else:
            prediction = {}
        return (prediction if isinstance(prediction, dict) else {}, updated_at_ms)

    @staticmethod
    def _finite_float(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        return number if math.isfinite(number) else None

    def _base_features(self, snapshot: MarketSnapshot, prediction: dict, age_ms: int) -> dict:
        raw_range = prediction.get("expectedMoveRange") or {}
        safe_range = {
            key: self._finite_float(raw_range.get(key))
            for key in ("low", "median", "high")
        } if isinstance(raw_range, dict) else {}
        return {
            "mark_price": snapshot.mark_price,
            "spread_bps": snapshot.spread_bps,
            "model_age_ms": age_ms,
            "model_bundle_id": str(prediction.get("model_bundle_id") or ""),
            "direction": str(
                prediction.get("finalDirection", prediction.get("direction")) or ""
            ),
            "trade_verdict": str(
                prediction.get("finalAction", prediction.get("trade_verdict")) or ""
            ),
            "calibrated_confidence": self._finite_float(
                prediction.get("calibratedConfidence")
            ),
            "agreement": self._finite_float(prediction.get("agreement")),
            "meta_trust": self._finite_float(prediction.get("metaTrust")),
            "expected_move": self._finite_float(prediction.get("expectedMove")),
            "expected_move_range": safe_range,
            "regime": str(prediction.get("regime") or ""),
        }

    def _no_edge(self, snapshot: MarketSnapshot, features: dict, *reasons: str):
        return self._decision(
            snapshot,
            action=Action.NO_EDGE,
            side=None,
            score=0.0,
            confidence=0.0,
            requested_notional_usd=0.0,
            stop_price=None,
            take_profit_price=None,
            maximum_holding_seconds=self.maximum_holding_seconds,
            features=features,
            reason_codes=tuple(reasons),
        )

    def decide(self, snapshot: MarketSnapshot):
        prediction, updated_at_ms = self._prediction(snapshot)
        age_ms = max(0, snapshot.received_at_ms - updated_at_ms) if updated_at_ms else 2**31
        features = self._base_features(snapshot, prediction, age_ms)
        context = snapshot.model_context or {}
        missing = []
        if snapshot.feed_health is not DataQuality.HEALTHY:
            missing.append("perpetual_book")
        if not prediction:
            missing.append("ensemble_prediction")
        calibrated = self._finite_float(prediction.get("calibratedConfidence"))
        if calibrated is None:
            missing.append("live_probability_calibration")
        if not prediction.get("model_bundle_id") or context.get("model_trained") is not True:
            missing.append("model_bundle_identity")
        if missing:
            return self.no_data(
                snapshot,
                tuple(missing),
                features,
                "model_consensus_inputs_unavailable",
            )
        if updated_at_ms > snapshot.received_at_ms:
            return self.no_data(
                snapshot,
                ("ensemble_prediction",),
                features,
                "model_context_newer_than_market_snapshot",
            )
        if age_ms > self.maximum_model_age_ms:
            return self.no_data(
                snapshot,
                ("ensemble_prediction",),
                features,
                "model_context_stale",
            )

        direction = str(prediction.get("finalDirection") or prediction.get("direction") or "")
        verdict = str(prediction.get("finalAction") or prediction.get("trade_verdict") or "")
        blocking = tuple(prediction.get("no_trade_reasons") or ())
        if verdict != "TRADE" or prediction.get("actionable") is not True or blocking:
            return self._no_edge(snapshot, features, "final_model_gate_rejected")
        if direction not in ("UP", "DOWN"):
            return self._no_edge(snapshot, features, "model_direction_unavailable")

        probability = calibrated
        agreement = self._finite_float(prediction.get("agreement"))
        meta_trust = self._finite_float(prediction.get("metaTrust"))
        if agreement is None or meta_trust is None:
            return self._no_edge(snapshot, features, "non_finite_model_evidence")
        if not 0.0 <= probability <= 1.0 or not 0.0 <= agreement <= 1.0 or not 0.0 <= meta_trust <= 1.0:
            return self._no_edge(snapshot, features, "model_evidence_out_of_range")
        if probability < self.minimum_calibrated_probability:
            return self._no_edge(snapshot, features, "calibrated_probability_below_gate")
        if agreement < self.minimum_agreement:
            return self._no_edge(snapshot, features, "ensemble_agreement_below_gate")
        if meta_trust < self.minimum_meta_trust:
            return self._no_edge(snapshot, features, "meta_trust_below_gate")

        expected_move_raw = self._finite_float(prediction.get("expectedMove"))
        move_range = prediction.get("expectedMoveRange") or {}
        if not isinstance(move_range, dict):
            move_range = {}
        expected_move = abs(expected_move_raw) if expected_move_raw is not None else 0.0
        conservative_raw = self._finite_float(move_range.get("low"))
        conservative_move = abs(
            conservative_raw if conservative_raw is not None else expected_move * 0.5
        )
        if expected_move <= 0.0 or conservative_move <= 0.0 or snapshot.mark_price <= 0.0:
            return self._no_edge(snapshot, features, "magnitude_head_unavailable")

        side = PositionSide.LONG if direction == "UP" else PositionSide.SHORT
        model_stop = self._finite_float(prediction.get("stopLoss"))
        model_stop_bps = (
            abs(model_stop - snapshot.mark_price) / snapshot.mark_price * 10_000.0
            if model_stop is not None
            else self.minimum_stop_bps
        )
        stop_bps = max(self.minimum_stop_bps, min(self.maximum_stop_bps, model_stop_bps))
        target_bps = max(
            self.minimum_take_profit_bps,
            min(
                self.maximum_target_bps,
                conservative_move / snapshot.mark_price * 10_000.0 * self.target_capture_fraction,
            ),
        )
        probability_lower = max(0.0, probability - self.conservative_probability_haircut)
        # The calibrated head predicts endpoint DIRECTION, not which TP/SL barrier is touched
        # first. Therefore EV must be computed on the direction target it was trained on; using
        # `p * target - (1-p) * stop` would silently reinterpret it as a barrier-order model.
        # The magnitude head supplies the conservative horizon move and the stop remains a risk
        # control, not a relabelled probability outcome.
        conservative_move_bps = conservative_move / snapshot.mark_price * 10_000.0
        expected_ev_bps = (
            (2.0 * probability - 1.0) * conservative_move_bps
            - self.assumed_round_trip_bps
        )
        lower_ev_bps = (
            (2.0 * probability_lower - 1.0) * conservative_move_bps
            - self.assumed_round_trip_bps
        )
        features.update(
            {
                "calibrated_probability": probability,
                "probability_lower": probability_lower,
                "stop_bps": stop_bps,
                "target_bps": target_bps,
                "conservative_horizon_move_bps": conservative_move_bps,
                "expected_ev_bps": expected_ev_bps,
                "lower_bound_ev_bps": lower_ev_bps,
            }
        )
        if lower_ev_bps <= self.minimum_lower_bound_ev_bps:
            return self._no_edge(snapshot, features, "conservative_net_ev_not_positive")

        sign = 1.0 if side is PositionSide.LONG else -1.0
        stop_price = snapshot.mark_price * (1.0 - sign * stop_bps / 10_000.0)
        target_price = snapshot.mark_price * (1.0 + sign * target_bps / 10_000.0)
        expected_net = self.requested_notional_usd * expected_ev_bps / 10_000.0
        lower_net = self.requested_notional_usd * lower_ev_bps / 10_000.0
        score = sign * min(1.0, lower_ev_bps / max(1.0, target_bps))
        return self._decision(
            snapshot,
            action=Action.OPEN_LONG if side is PositionSide.LONG else Action.OPEN_SHORT,
            side=side,
            score=score,
            confidence=probability,
            requested_notional_usd=self.requested_notional_usd,
            stop_price=stop_price,
            take_profit_price=target_price,
            maximum_holding_seconds=self.maximum_holding_seconds,
            features=features,
            reason_codes=("final_ensemble_trade", "positive_conservative_net_ev"),
            probability_calibrated=True,
            uncertainty_status="LIVE_CALIBRATED_WITH_PROBABILITY_HAIRCUT",
            expected_net_pnl_usd=expected_net,
            expected_net_pnl_lower_bound_usd=lower_net,
        )

    def position_exit_reason(
        self, position: dict[str, Any], snapshot: MarketSnapshot
    ) -> str | None:
        prediction, updated_at_ms = self._prediction(snapshot)
        if updated_at_ms > snapshot.received_at_ms:
            return None
        age_ms = max(0, snapshot.received_at_ms - updated_at_ms) if updated_at_ms else 2**31
        if not prediction or age_ms > self.maximum_model_age_ms:
            return "MODEL_CONTEXT_STALE"
        side = PositionSide(position["side"])
        expected_direction = "UP" if side is PositionSide.LONG else "DOWN"
        direction = str(prediction.get("finalDirection") or prediction.get("direction") or "")
        verdict = str(prediction.get("finalAction") or prediction.get("trade_verdict") or "")
        if direction in ("UP", "DOWN") and direction != expected_direction and verdict == "TRADE":
            return "MODEL_DIRECTION_FLIP"

        executable = snapshot.best_bid if side is PositionSide.LONG else snapshot.best_ask
        sign = 1.0 if side is PositionSide.LONG else -1.0
        gross_bps = sign * (executable - float(position["entry_price"])) / float(position["entry_price"]) * 10_000.0
        calibrated = self._finite_float(prediction.get("calibratedConfidence"))
        if calibrated is not None and calibrated < 0.45:
            return "MODEL_CONFIDENCE_COLLAPSE"
        blocking = tuple(prediction.get("no_trade_reasons") or ())
        if (verdict != "TRADE" or blocking) and gross_bps >= (
            self.assumed_round_trip_bps + self.profit_lock_buffer_bps
        ):
            return "MODEL_EDGE_DECAY_PROFIT_LOCK"
        return None
