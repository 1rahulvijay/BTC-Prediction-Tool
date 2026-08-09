"""Cost-aware paper strategy driven by the Binance endpoint-direction head.

The broad ensemble predicts first barrier touched and is inadmissible for endpoint EV. This
strategy consumes only the separately calibrated rolling exchange-return head, plus the
existing magnitude forecast. The lane is paper-only and exists to measure whether that exact
endpoint question adds economic value over the zero-information control.
"""
from __future__ import annotations

import math
from typing import Any

# `target_contract` lives in backend/, which is on sys.path when a backend script is run
# directly but NOT when this package is imported as `backend.binance_paper.*`. Both
# invocations are used (CI runs the package with -m), so resolve it either way rather than
# assuming one - a bare `import target_contract` broke seven CI steps.
import sys as _sys
from pathlib import Path as _Path

_BACKEND = str(_Path(__file__).resolve().parents[2])
if _BACKEND not in _sys.path:
    _sys.path.insert(0, _BACKEND)
import target_contract as _tc  # noqa: E402

from ..schemas import Action, DataQuality, MarketSnapshot, PositionSide
from ..strategy_base import StrategyBase



def _allow_heuristic_ev() -> bool:
    """Deliberate research opt-in. Absent, a heuristic haircut may not authorise capital."""
    import os
    return os.environ.get(
        "BTC_BINANCE_PAPER_ALLOW_HEURISTIC_EV", "0"
    ).strip().lower() in ("1", "true", "yes")


def _allow_ungrouped_head() -> bool:
    """Research-only opt-in for a head whose holdout rows are not independent rounds."""
    import os
    return os.environ.get(
        "BTC_BINANCE_PAPER_ALLOW_UNGROUPED_HEAD", "0"
    ).strip().lower() in ("1", "true", "yes")


class ModelConsensusStrategy(StrategyBase):
    strategy_id = "model_consensus"
    strategy_name = "Model Consensus (calibrated, cost-aware)"
    strategy_version = "paper-v2"
    timeframe = "5m ensemble / max 5m hold"
    required_inputs = (
        "perpetual_book",
        "endpoint_direction_head",
        "endpoint_probability_calibration",
        "endpoint_model_bundle_identity",
        "magnitude_forecast",
    )

    horizon = 5
    maximum_model_age_ms = 90_000
    minimum_calibrated_probability = 0.58
    conservative_probability_haircut = 0.05
    target_capture_fraction = 0.80
    minimum_stop_bps = 8.0
    maximum_stop_bps = 60.0
    maximum_target_bps = 100.0
    requested_notional_usd = 500.0
    maximum_holding_seconds = 300
    minimum_heuristic_haircut_ev_bps = 0.0
    #: A fixed 5pp haircut is NOT a confidence interval, a conformal bound, a bootstrap bound or
    #: any empirically justified uncertainty estimate. Until a measured interval exists this
    #: strategy may compute and report its EV but MAY NOT authorise an entry on it. Opt in
    #: deliberately with BTC_BINANCE_PAPER_ALLOW_HEURISTIC_EV=1 for research; it is not a
    #: default any other engine gets.
    uncertainty_method = "fixed_haircut"
    profit_lock_buffer_bps = 2.0

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "maximum_model_age_ms": self.maximum_model_age_ms,
            "minimum_calibrated_probability": self.minimum_calibrated_probability,
            "conservative_probability_haircut": self.conservative_probability_haircut,
            "target_capture_fraction": self.target_capture_fraction,
            "minimum_stop_bps": self.minimum_stop_bps,
            "maximum_stop_bps": self.maximum_stop_bps,
            "maximum_target_bps": self.maximum_target_bps,
            "requested_notional_usd": self.requested_notional_usd,
            "maximum_holding_seconds": self.maximum_holding_seconds,
            "minimum_heuristic_haircut_ev_bps": self.minimum_heuristic_haircut_ev_bps,
            "uncertainty_method": self.uncertainty_method,
            "allow_heuristic_ev": _allow_heuristic_ev(),
            "allow_ungrouped_head": _allow_ungrouped_head(),
            "profit_lock_buffer_bps": self.profit_lock_buffer_bps,
        }

    def _prediction(self, snapshot: MarketSnapshot) -> tuple[dict, int]:
        context = snapshot.model_context or {}
        try:
            updated_at_ms = int(context.get("updated_at_ms") or 0)
        except (TypeError, ValueError, OverflowError):
            updated_at_ms = 0
        prediction = context.get("endpoint_prediction") or {}
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
            "calibrated_confidence": self._finite_float(
                prediction.get("calibratedConfidence")
            ),
            "prob_up": self._finite_float(prediction.get("probUp")),
            "prob_down": self._finite_float(prediction.get("probDown")),
            "expected_move": self._finite_float(prediction.get("expectedMove")),
            "expected_move_range": safe_range,
            "target_contract": str(prediction.get("targetContract") or ""),
            "holdout_metrics": dict(prediction.get("holdout_metrics") or {}),
            "independence_validated": bool(prediction.get("independence_validated")),
            "confidence_lower_95": self._finite_float(
                prediction.get("confidenceLower95")
            ),
            "uncertainty_method": str(prediction.get("uncertaintyMethod") or ""),
            "uncertainty_bucket": dict(prediction.get("uncertaintyBucket") or {}),
            "research_only": bool(prediction.get("research_only")),
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
        missing = []
        if snapshot.feed_health is not DataQuality.HEALTHY:
            missing.append("perpetual_book")
        if not prediction or prediction.get("endpointHeadReady") is not True:
            missing.append("endpoint_direction_head")
        calibrated = self._finite_float(prediction.get("calibratedConfidence"))
        if calibrated is None:
            missing.append("endpoint_probability_calibration")
        if not prediction.get("model_bundle_id"):
            missing.append("endpoint_model_bundle_identity")
        if self._finite_float(prediction.get("expectedMove")) is None:
            missing.append("magnitude_forecast")
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
                ("endpoint_direction_head",),
                features,
                "model_context_newer_than_market_snapshot",
            )
        if age_ms > self.maximum_model_age_ms:
            return self.no_data(
                snapshot,
                ("endpoint_direction_head",),
                features,
                "model_context_stale",
            )

        direction = str(prediction.get("finalDirection") or prediction.get("direction") or "")
        if direction not in ("UP", "DOWN"):
            return self._no_edge(snapshot, features, "model_direction_unavailable")

        # CONTRACT ADMISSIBILITY. The EV below is (2p - 1) * move - costs, which treats p as
        # the probability that the ENDPOINT lands on the predicted side. A first-touch
        # probability answers "which barrier is touched first" - a different random variable
        # that disagrees on roughly a quarter of paths. Both are floats in [0, 1], so nothing
        # about the value would have revealed the substitution.
        try:
            _tc.assert_admissible(_tc.BINANCE_DIRECTIONAL_PAPER_EV,
                                  prediction.get("targetContract"))
        except _tc.ContractMisuse as exc:
            features["target_contract"] = prediction.get("targetContract")
            features["contract_misuse"] = str(exc)[:300]
            return self._no_edge(snapshot, features, "target_contract_inadmissible")

        probability = calibrated
        if not 0.0 <= probability <= 1.0:
            return self._no_edge(snapshot, features, "model_evidence_out_of_range")
        if probability < self.minimum_calibrated_probability:
            return self._no_edge(snapshot, features, "calibrated_probability_below_gate")

        if prediction.get("research_only") is not True:
            return self._no_edge(snapshot, features, "endpoint_head_not_research_scoped")
        if prediction.get("independence_validated") is not True and not _allow_ungrouped_head():
            return self._no_edge(
                snapshot, features,
                "endpoint_holdout_not_independent: enable only the isolated paper research "
                "opt-in after accepting that overlapping rows overstate evidence",
            )

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
        empirical_probability_lower = self._finite_float(
            prediction.get("confidenceLower95"))
        probability_uncertainty_method = str(
            prediction.get("uncertaintyMethod") or "")
        has_empirical_probability_bound = (
            empirical_probability_lower is not None
            and 0.0 <= empirical_probability_lower <= probability
            and probability_uncertainty_method == "group_bootstrap_95"
        )
        haircut_probability = (
            empirical_probability_lower if has_empirical_probability_bound
            else max(0.0, probability - self.conservative_probability_haircut)
        )
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
        haircut_ev_bps = (
            (2.0 * haircut_probability - 1.0) * conservative_move_bps
            - self.assumed_round_trip_bps
        )
        features.update(
            {
                "calibrated_probability": probability,
                # RENAMED. This was `probability_lower` / `lower_bound_ev_bps`, which claimed a
                # statistical property the arithmetic does not have: it is `probability` minus a
                # hand-chosen constant, not an interval derived from data.
                "heuristic_haircut_probability": haircut_probability,
                "heuristic_haircut_ev_bps": haircut_ev_bps,
                "uncertainty_method": (
                    probability_uncertainty_method if has_empirical_probability_bound
                    else self.uncertainty_method
                ),
                "probability_bound_is_empirical": has_empirical_probability_bound,
                # The EV above is (2p-1) * move - costs, which assumes the correct-side and
                # wrong-side moves have the SAME average magnitude. A 60% model still loses if
                # wins average 8 bps and losses average 25 bps. Recorded so the assumption is
                # visible in the ledger rather than buried in a formula.
                "ev_payoff_assumption": "symmetric_magnitude",
                "stop_bps": stop_bps,
                "target_bps": target_bps,
                "conservative_horizon_move_bps": conservative_move_bps,
                "expected_ev_bps": expected_ev_bps,
            }
        )
        if haircut_ev_bps <= self.minimum_heuristic_haircut_ev_bps:
            return self._no_edge(snapshot, features, "heuristic_haircut_ev_not_positive")

        # AUTHORITY GATE. Everything above is a computation; this decides whether it may open a
        # position. A pessimism constant is not an uncertainty estimate, and the symmetric-payoff
        # EV compounds that. Until a measured interval and an asymmetric payoff model exist, this
        # strategy reports and abstains.
        # A group-bootstrap hit-rate bound validates the probability bucket only. It does NOT
        # validate this strategy's stop/target/latency path or the symmetric-payoff EV formula.
        # Only a replay of THIS exact policy may emit policyValueLowerBps and authorise it.
        policy_value_lower = self._finite_float(prediction.get("policyValueLowerBps"))
        policy_value_method = str(prediction.get("policyValueMethod") or "")
        policy_value_id = str(prediction.get("policyValueId") or "")
        policy_value_valid = (
            policy_value_lower is not None
            and policy_value_lower > 0.0
            and policy_value_method == "policy_cluster_bootstrap_95"
            and policy_value_id == f"{self.strategy_id}:{self.strategy_version}"
        )
        features.update({
            "policy_value_lower_bps": policy_value_lower,
            "policy_value_method": policy_value_method,
            "policy_value_id": policy_value_id,
            "policy_value_valid": policy_value_valid,
        })
        if not policy_value_valid and not _allow_heuristic_ev():
            return self._no_edge(
                snapshot, features,
                "exact_policy_value_interval_unavailable: probability calibration alone does "
                "not validate stop/target/latency PnL or asymmetric win/loss magnitude")

        sign = 1.0 if side is PositionSide.LONG else -1.0
        stop_price = snapshot.mark_price * (1.0 - sign * stop_bps / 10_000.0)
        target_price = snapshot.mark_price * (1.0 + sign * target_bps / 10_000.0)
        expected_net = self.requested_notional_usd * expected_ev_bps / 10_000.0
        authority_lower_ev_bps = policy_value_lower if policy_value_valid else haircut_ev_bps
        lower_net = self.requested_notional_usd * authority_lower_ev_bps / 10_000.0
        score = sign * min(1.0, authority_lower_ev_bps / max(1.0, target_bps))
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
            reason_codes=("endpoint_head_trade", "positive_exact_policy_value_lower_bound"),
            probability_calibrated=True,
            # "LIVE_CALIBRATED" read like an empirical uncertainty method. The probability
            # is calibrated; the UNCERTAINTY is a hand-chosen constant, and the
            # label now says which is which.
            uncertainty_status=("EXACT_POLICY_CLUSTER_BOOTSTRAP_95"
                                if policy_value_valid
                                else "RESEARCH_OVERRIDE_UNMEASURED_POLICY_VALUE"),
            expected_net_pnl_usd=expected_net,
            expected_net_pnl_heuristic_haircut_usd=lower_net,
        )

    def position_exit_reason(
        self, position: dict[str, Any], snapshot: MarketSnapshot
    ) -> str | None:
        prediction, updated_at_ms = self._prediction(snapshot)
        if updated_at_ms > snapshot.received_at_ms:
            return "MODEL_CONTEXT_INVALID"
        age_ms = max(0, snapshot.received_at_ms - updated_at_ms) if updated_at_ms else 2**31
        if (
            not prediction
            or prediction.get("endpointHeadReady") is not True
            or not prediction.get("model_bundle_id")
            or age_ms > self.maximum_model_age_ms
        ):
            return "MODEL_CONTEXT_STALE"
        try:
            _tc.assert_admissible(
                _tc.BINANCE_DIRECTIONAL_PAPER_EV,
                prediction.get("targetContract"),
            )
        except _tc.ContractMisuse:
            return "MODEL_CONTEXT_INVALID"
        side = PositionSide(position["side"])
        expected_direction = "UP" if side is PositionSide.LONG else "DOWN"
        direction = str(prediction.get("finalDirection") or prediction.get("direction") or "")
        if direction in ("UP", "DOWN") and direction != expected_direction:
            return "MODEL_DIRECTION_FLIP"

        executable = snapshot.best_bid if side is PositionSide.LONG else snapshot.best_ask
        sign = 1.0 if side is PositionSide.LONG else -1.0
        gross_bps = sign * (executable - float(position["entry_price"])) / float(position["entry_price"]) * 10_000.0
        # PROBABILITY NAMESPACE. Entry admits on `calibratedConfidence` against
        # `minimum_calibrated_probability`. This path read RAW probUp/probDown and compared
        # them to that same calibrated threshold - two different quantities against one
        # number. Calibration exists precisely because raw and calibrated disagree, so a
        # position could be opened at calibrated .61 and closed on raw .54 that calibrates to
        # .60, or held on raw .59 that calibrates to .52. Neither direction is intended.
        #
        # The direction-flip check above has already returned if the model no longer favours
        # the held side, so `calibratedConfidence` here IS the calibrated probability for that
        # side. Raw values are kept only for reporting, never compared to a calibrated bound.
        #
        # Fail-closed on absence, matching the entry gate: it refuses to open without a
        # calibration, so exiting on an uncalibrated number would apply a stricter standard to
        # entry than to the capital already at risk.
        side_probability = self._finite_float(prediction.get("calibratedConfidence"))
        raw_side_probability = self._finite_float(
            prediction.get("probUp" if side is PositionSide.LONG else "probDown")
        )
        if side_probability is None:
            return "MODEL_CALIBRATION_UNAVAILABLE"
        if raw_side_probability is None:
            return "MODEL_CONTEXT_INVALID"
        # The collapse floor is a CALIBRATED bound too - mixing namespaces on one of the two
        # comparisons would leave the same defect, halved.
        if side_probability < 0.45:
            return "MODEL_CONFIDENCE_COLLAPSE"
        if side_probability < self.minimum_calibrated_probability and gross_bps >= (
            self.assumed_round_trip_bps + self.profit_lock_buffer_bps
        ):
            return "MODEL_EDGE_DECAY_PROFIT_LOCK"
        return None
