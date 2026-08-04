"""
A/B Testing Framework for Model Configurations
Runs two model variants side-by-side on the same live data,
logging predictions to separate DuckDB tables for comparative analysis.
Does NOT affect live signal output — both variants predict silently.
"""

import copy
import time
import logging
import numpy as np
from typing import Optional

import database

logger = logging.getLogger(__name__)


class ModelVariant:
    """Wraps a MultiModelEnsemble with a configuration label for A/B tracking."""

    def __init__(self, label: str, model, started_at: float = None):
        self.label = label
        self.model = model
        self.started_at = float(started_at or time.time())
        self.predictions = []
        self.verified = []
        self.total_correct = 0
        self.total_predictions = 0

    def predict(self, h: int, seq: np.ndarray, data_state: dict,
                acc_cache: dict = None, cascade_data: dict = None) -> dict:
        """Generate prediction and tag with variant label."""
        pred = self.model.generate_ensemble_prediction(h, seq, data_state, acc_cache, cascade_data)
        pred["variant"] = self.label
        pred["variant_ts"] = int(time.time() * 1000)
        self.predictions.append(pred)
        self.total_predictions += 1
        return pred

    def record_outcome(self, was_correct: bool):
        self.verified.append(was_correct)
        if was_correct:
            self.total_correct += 1

    @property
    def accuracy(self) -> float:
        if not self.verified:
            return 0.0
        return self.total_correct / len(self.verified)

    def get_stats(self) -> dict:
        return {
            "label": self.label,
            "total_predictions": self.total_predictions,
            "verified": len(self.verified),
            "accuracy": round(self.accuracy, 4),
            "started_at": self.started_at,
            "live_days": round(max(0.0, time.time() - self.started_at) / 86400.0, 3),
        }


#: Both variants' RAW model output is compared. The incumbent's post-policy direction is NOT
#: used, because the challenger has no way to run the same policy chain - comparing one
#: against the other measured the policy, not the model.
RAW_MODEL_COMPARISON = "raw_model_output"
#: Reserved for when the decision policy becomes a callable both variants can run.
FINAL_POLICY_COMPARISON = "final_policy_action"


class ABTestRunner:
    """
    Runs two model variants side-by-side.
    - Primary variant drives the dashboard (its predictions are returned)
    - Challenger variant predicts silently for comparison
    - Both variants' outcomes are logged to DuckDB
    """

    def __init__(self, primary: Optional[ModelVariant] = None,
                 challenger: Optional[ModelVariant] = None):
        self.primary = primary
        self.challenger = challenger
        self.enabled = challenger is not None
        self.comparison_log = []
        # Latest prediction per horizon per variant, and pending (pred_id -> dirs)
        # so outcomes can be attributed to the exact recorded prediction at resolve time.
        self.last_by_horizon: dict = {}
        self.pending: dict = {}
        #: pred_id -> what was actually compared. Never assume policy parity.
        self.comparison_basis: dict = {}

    def restore_from_db(self) -> int:
        """Seed in-memory variant accuracy from DuckDB so promotion survives restarts."""
        try:
            stats = database.fetch_ab_variant_stats()
        except Exception:
            return 0
        restored = 0
        for variant in (self.primary, self.challenger):
            if variant and variant.label in stats:
                s = stats[variant.label]
                variant.verified = [True] * s["hits"] + [False] * max(0, s["verified"] - s["hits"])
                variant.total_correct = s["hits"]
                restored += s["verified"]
        return restored

    def predict(self, h: int, seq: np.ndarray, data_state: dict,
                acc_cache: dict = None, cascade_data: dict = None) -> dict:
        """
        Run both variants and return the primary's prediction.
        The challenger predicts silently.
        """
        if not self.primary:
            return {}

        primary_pred = self.primary.predict(h, seq, data_state, acc_cache, cascade_data)
        # DEEP COPY, not the live reference. The dict returned here is handed to the server,
        # which then mutates it IN PLACE - meta filtering, the expectancy neutraliser, the
        # no-trade reason engine. Storing the reference meant persist() later read a
        # POST-POLICY primary direction and compared it against a RAW challenger direction,
        # so the "A/B test" was:
        #     incumbent model + full production policy   vs   challenger model alone
        # A challenger could win simply by making more raw directional calls while the
        # incumbent was neutralised by safety gates.
        self.last_by_horizon[h] = {
            "primary": copy.deepcopy(primary_pred),
            "challenger": None,
            # Both sides are RAW model output. Policy-level comparison is not available
            # because the server's filter chain is not a callable both variants can run.
            "comparison_basis": RAW_MODEL_COMPARISON,
        }

        if self.enabled and self.challenger and getattr(self.challenger.model, "is_trained", False):
            try:
                challenger_pred = self.challenger.predict(h, seq, data_state, acc_cache, cascade_data)
                self.last_by_horizon[h]["challenger"] = copy.deepcopy(challenger_pred)
                self.comparison_log.append({
                    "horizon": h,
                    "timestamp": int(time.time() * 1000),
                    "primary_direction": primary_pred.get("direction"),
                    "primary_confidence": primary_pred.get("confidence"),
                    "challenger_direction": challenger_pred.get("direction"),
                    "challenger_confidence": challenger_pred.get("confidence"),
                    "agree": primary_pred.get("direction") == challenger_pred.get("direction"),
                })
            except Exception as e:
                logger.debug(f"A/B challenger prediction failed: {e}")

        return primary_pred

    def persist(self, pred_id: str, h: int, timestamp: int):
        """Durably log each variant's prediction for this recorded pred_id to DuckDB."""
        latest = self.last_by_horizon.get(h)
        if not latest:
            return
        dirs = {}
        for key, variant in (("primary", self.primary), ("challenger", self.challenger)):
            pred = latest.get(key)
            if pred and variant:
                direction = pred.get("direction", "NEUTRAL")
                dirs[variant.label] = direction
                try:
                    from features import get_feature_schema
                    schema_hash = get_feature_schema()["schema_hash"]
                    bundle_id = pred.get("model_bundle_id", "")
                    database.log_ab_prediction(
                        variant.label, pred_id, timestamp, h, direction,
                        float(pred.get("confidence", 0.0)),
                        model_bundle_id=bundle_id,
                        feature_schema_hash=schema_hash
                    )
                except Exception as e:
                    logger.debug(f"A/B persist failed: {e}")
        if dirs:
            self.pending[pred_id] = dirs
            # Recorded so a promotion decision cannot silently claim a policy-level
            # comparison it never made.
            self.comparison_basis[pred_id] = latest.get(
                "comparison_basis", RAW_MODEL_COMPARISON)

    def resolve(self, pred_id: str, actual_direction: str):
        """Resolve a recorded A/B prediction in DuckDB and in-memory variant stats."""
        try:
            database.resolve_ab_results(pred_id, actual_direction)
        except Exception as e:
            logger.debug(f"A/B resolve failed: {e}")
        dirs = self.pending.pop(pred_id, None)
        if not dirs:
            return
        if self.primary and self.primary.label in dirs:
            self.primary.record_outcome(dirs[self.primary.label] == actual_direction)
        if self.challenger and self.challenger.label in dirs:
            self.challenger.record_outcome(dirs[self.challenger.label] == actual_direction)

    def record_outcomes(self, primary_correct: bool, challenger_correct: bool = None):
        """Legacy in-memory outcome recording (kept for backward compatibility)."""
        if self.primary:
            self.primary.record_outcome(primary_correct)
        if self.enabled and self.challenger and challenger_correct is not None:
            self.challenger.record_outcome(challenger_correct)

    def get_comparison(self) -> dict:
        """Get comparative metrics between the two variants."""
        if not self.enabled or not self.challenger:
            return {
                "enabled": False,
                "reason": "no_trained_challenger",
                "promotion_criteria": {
                    "min_verified": 500,
                    "min_paired": 500,
                    "min_live_days": 30,
                    "min_profit_factor": 1.20,
                    "requires_positive_ev": True,
                    "requires_positive_accuracy_delta": True,
                    "requires_positive_paired_bootstrap_lb": True,
                },
            }
        if not getattr(self.challenger.model, "is_trained", False):
            return {
                "enabled": False,
                "reason": "challenger_not_trained",
                "primary": self.primary.get_stats() if self.primary else {},
                "challenger": self.challenger.get_stats() if self.challenger else {},
                "promotion_recommendation": "keep_primary",
                "promotion_criteria": {
                    "min_verified": 500,
                    "min_paired": 500,
                    "min_live_days": 30,
                    "min_profit_factor": 1.20,
                    "requires_positive_ev": True,
                    "requires_positive_accuracy_delta": True,
                    "requires_positive_paired_bootstrap_lb": True,
                },
            }

        n = len(self.comparison_log)
        agree_count = sum(1 for c in self.comparison_log if c["agree"])
        disagreement_rate = 1 - (agree_count / max(1, n))
        accuracy_delta = (
            (self.challenger.accuracy - self.primary.accuracy)
            if self.primary and self.challenger else 0.0
        )
        criteria = {
            "min_verified": 500,
            "min_paired": 500,
            "min_live_days": 30,
            "min_profit_factor": 1.20,
            "requires_positive_ev": True,
            "requires_positive_accuracy_delta": True,
            "requires_positive_paired_bootstrap_lb": True,
        }

        # Compare exact pairs from DuckDB. Reconstructing [hits..., misses...] from aggregate
        # counts after a restart destroys pairing and can manufacture a false bootstrap win.
        paired = []
        if self.primary and self.challenger:
            try:
                paired = database.fetch_ab_paired_outcomes(
                    self.primary.label, self.challenger.label
                )
            except Exception:
                paired = []
        if paired:
            p_arr = np.asarray([p for p, _ in paired], dtype=float)
            c_arr = np.asarray([c for _, c in paired], dtype=float)
            accuracy_delta = float(c_arr.mean() - p_arr.mean())
        else:
            p_arr = c_arr = np.asarray([], dtype=float)

        # Calculate a paired 95% bootstrap lower bound for accuracy delta.
        bootstrap_lower = 0.0
        if len(paired) >= criteria["min_paired"]:
            n_min = len(paired)
            rng = np.random.default_rng(42)
            diffs = []
            for _ in range(1000):
                idx = rng.integers(0, n_min, n_min)
                diffs.append(c_arr[idx].mean() - p_arr[idx].mean())
            bootstrap_lower = float(np.percentile(diffs, 2.5))
            
        # Calendar evidence cannot be inferred from prediction count: horizons have
        # different cadences and restarts change throughput. Use actual elapsed time.
        simulated_live_days = max(0.0, time.time() - self.challenger.started_at) / 86400.0
        try:
            profit_stats = database.fetch_ab_variant_profit_stats()
        except Exception:
            profit_stats = {}
        challenger_profit = profit_stats.get(self.challenger.label, {}) if self.challenger else {}
        challenger_pf = float(challenger_profit.get("profit_factor", 0.0) or 0.0)
        challenger_ev = float(challenger_profit.get("expectancy_usd", 0.0) or 0.0)
        challenger_trades = int(challenger_profit.get("trades", 0) or 0)
        
        passes_sample_size = len(self.challenger.verified) >= criteria["min_verified"]
        passes_paired = len(paired) >= criteria["min_paired"]
        passes_accuracy_delta = accuracy_delta > 0.0
        passes_bootstrap = passes_paired and bootstrap_lower > 0.0
        passes_live_days = simulated_live_days >= criteria["min_live_days"]
        passes_profit_samples = challenger_trades >= criteria["min_verified"]
        passes_pf = passes_profit_samples and challenger_pf > criteria["min_profit_factor"]
        passes_ev = passes_profit_samples and (challenger_ev > 0.0 if criteria["requires_positive_ev"] else True)

        promotable = (
            passes_sample_size
            and passes_paired
            and passes_accuracy_delta
            and passes_bootstrap
            and passes_live_days
            and passes_profit_samples
            and passes_pf
            and passes_ev
        )

        return {
            "enabled": True,
            "primary": self.primary.get_stats() if self.primary else {},
            "challenger": self.challenger.get_stats() if self.challenger else {},
            "total_comparisons": n,
            "paired_resolved": len(paired),
            "agreement_rate": round(agree_count / max(1, n), 4),
            "disagreement_rate": round(disagreement_rate, 4),
            "accuracy_delta": round(accuracy_delta, 4),
            "bootstrap_lower_bound": round(bootstrap_lower, 4),
            "profit_factor": challenger_pf,
            "expectancy": challenger_ev,
            "profit_samples": challenger_trades,
            "profit_evidence": challenger_profit,
            "promotion_criteria": criteria,
            "gates_passed": {
                "sample_size": passes_sample_size,
                "paired_sample_size": passes_paired,
                "accuracy_delta": passes_accuracy_delta,
                "paired_bootstrap_lb": passes_bootstrap,
                "live_days": passes_live_days,
                "profit_samples": passes_profit_samples,
                "profit_factor": passes_pf,
                "positive_ev": passes_ev
            },
            "significant": bool(passes_bootstrap),
            "promotion_recommendation": "promote_challenger" if promotable else "keep_primary",
        }
