"""
A/B Testing Framework for Model Configurations
Runs two model variants side-by-side on the same live data,
logging predictions to separate DuckDB tables for comparative analysis.
Does NOT affect live signal output — both variants predict silently.
"""

import copy
import time
from collections import deque
import logging
import numpy as np
from typing import Optional

import database

logger = logging.getLogger(__name__)

#: How many recent items each bounded buffer keeps for inspection. The COUNTS are exact and
#: unbounded; only the retained detail is capped.
RECENT_RETAINED = 200


class ModelVariant:
    """Wraps a MultiModelEnsemble with a configuration label for A/B tracking."""

    def __init__(self, label: str, model, started_at: float = None,
                 model_bundle_id: str = None):
        self.label = label
        self.model = model
        self.started_at = float(started_at or time.time())
        #: The identity of the model under test. The LABEL is not that identity - two of
        #: the three construction sites use fixed strings ("baseline_v9",
        #: "challenger_cat_v1"), so a replaced model reusing a label would inherit its
        #: predecessor's durable record.
        self.model_bundle_id = str(
            model_bundle_id
            if model_bundle_id is not None
            else (getattr(model, "model_bundle_id", "") or "")
        )
        #: How `started_at` was obtained. A promotion gate that requires 30 calendar days
        #: must be able to tell a restored clock from one that started at this boot.
        self.started_at_source = "process_start" if started_at is None else "caller"
        self.evidence_scope = "none"
        # BOUNDED. P1-C ("A/B testing: unbounded memory") verified 2026-08-08.
        #
        # `predictions` retained the FULL prediction dict for every cycle, for every horizon,
        # for every variant - ~4 KB each, ~4,800/hour across two horizons and two variants,
        # which is ~19 MB/hour and ~3.2 GB/week. Nothing ever read it: the only consumer of
        # this attribute was `len()`, and `total_predictions` already counts.
        #
        # `verified` was a list of BOOLEANS where only the count is used - `total_correct`
        # is a separate counter and `accuracy` divides by the length. `restore_from_db` even
        # materialised `[True] * hits + [False] * misses` purely to be measured.
        #
        # Both are now counters, with a small bounded tail kept for inspection. A count is
        # what the code asked for; the list was the part nobody needed.
        self.predictions = deque(maxlen=RECENT_RETAINED)
        self.verified = deque(maxlen=RECENT_RETAINED)
        self.total_verified = 0
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
        self.total_verified += 1
        if was_correct:
            self.total_correct += 1

    @property
    def accuracy(self) -> float:
        # Over EVERY outcome, not just the retained tail - bounding the buffer must not
        # silently narrow the denominator of the accuracy a promotion gate reads.
        if not self.total_verified:
            return 0.0
        return self.total_correct / self.total_verified

    def get_stats(self) -> dict:
        return {
            "label": self.label,
            "total_predictions": self.total_predictions,
            "verified": self.total_verified,
            "accuracy": round(self.accuracy, 4),
            "started_at": self.started_at,
            "live_days": round(max(0.0, time.time() - self.started_at) / 86400.0, 3),
            "model_bundle_id": self.model_bundle_id,
            "started_at_source": self.started_at_source,
            "evidence_scope": self.evidence_scope,
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
        # Same treatment: only `len()` and the agree-count are read, so those are counted
        # and the per-cycle detail is a bounded tail.
        self.comparison_log = deque(maxlen=RECENT_RETAINED)
        self.total_comparisons = 0
        self.total_agreements = 0
        # Latest prediction per horizon per variant, and pending (pred_id -> dirs)
        # so outcomes can be attributed to the exact recorded prediction at resolve time.
        self.last_by_horizon: dict = {}
        self.pending: dict = {}
        #: pred_id -> what was actually compared. Never assume policy parity.
        self.comparison_basis: dict = {}
        #: The challenger's OWN cascade inputs, by horizon. See `predict`.
        self.challenger_cascade: dict = {}
        #: label -> {horizon: {lean_accuracy, lean_total}}, from that variant's own
        #: resolved rows. The cascade gate is a skill test; each variant must sit it.
        self.variant_accuracy: dict = {}
        self.restore_report: dict = {}
        self._last_cascade_obj = False   # sentinel: never equals a real dict or None

    def reset_comparisons(self) -> None:
        """Clear the agreement record AND the counters that summarise it.

        Callers used `comparison_log.clear()` when a challenger was replaced. With the log
        bounded and the aggregates counted, clearing only the list would carry the previous
        challenger's comparison count and agreement rate into the new one - the same
        label-vs-model identity defect as 5.14, one attribute over.
        """
        self.comparison_log.clear()
        self.total_comparisons = 0
        self.total_agreements = 0

    def restore_from_db(self) -> int:
        """Rebuild every piece of durable A/B state a restart would otherwise destroy.

        Three separate losses, all of which made a promotion decision read the wrong
        evidence:

        counts        restored before, but keyed by LABEL, so a new challenger reusing
                      a label inherited its predecessor's record.
        the clock     `simulated_live_days` is measured from `started_at`, which was set
                      to `time.time()` at construction. A `min_live_days: 30` gate could
                      therefore never be reached by any process restarted more often than
                      monthly - a permanently closed gate, the 5.21 shape again.
        in flight     `pending` is the pred_id -> direction map that attributes an
                      outcome to the variant that predicted it. It lived only in memory,
                      so every prediction open at shutdown resolved in DuckDB while the
                      in-memory counters never saw it.

        The clock is only restored from a BUNDLE-SCOPED record. Unidentifiable evidence
        does not earn calendar credit: crediting a fresh model with a predecessor's age
        is worse than making it wait.
        """
        restored = 0
        self.restore_report = {}
        for variant in (self.primary, self.challenger):
            if not variant:
                continue
            try:
                ev = database.fetch_ab_variant_evidence(
                    variant.label, variant.model_bundle_id)
            except Exception:
                continue
            verified, hits = int(ev.get("verified") or 0), int(ev.get("hits") or 0)
            variant.total_verified = verified
            variant.total_correct = hits
            variant.verified.clear()
            variant.evidence_scope = str(ev.get("scope_reason") or "none")
            restored += verified

            first_ts = ev.get("first_ts_ms")
            if ev.get("bundle_scoped") and first_ts:
                # Never move the clock FORWARD - a later restart must not shorten the
                # evidence window a caller already established.
                restored_start = float(first_ts) / 1000.0
                if restored_start < variant.started_at:
                    variant.started_at = restored_start
                    variant.started_at_source = "db_first_prediction"

            for pred_id, direction in ev.get("unresolved") or []:
                self.pending.setdefault(pred_id, {})[variant.label] = direction

            by_h = ev.get("by_horizon") or {}
            if by_h:
                self.variant_accuracy[variant.label] = by_h

            self.restore_report[variant.label] = {
                "verified": verified,
                "scope": variant.evidence_scope,
                "reopened_pending": len(ev.get("unresolved") or []),
                "started_at_source": variant.started_at_source,
            }
        return restored

    def predict(self, h: int, seq: np.ndarray, data_state: dict,
                acc_cache: dict = None, cascade_data: dict = None) -> dict:
        """
        Run both variants and return the primary's prediction.
        The challenger predicts silently.
        """
        if not self.primary:
            return {}

        # The server rebuilds `cascade_data = {}` once per prediction cycle and fills it
        # horizon by horizon, so the primary can never read a stale lower horizon. The
        # challenger's mirror must expire on exactly the same boundary or it would - a
        # cycle where the challenger's 5m call failed would leave the previous cycle's 5m
        # conditioning its 15m. Identity is the boundary, and the reference is RETAINED so
        # `is` cannot be fooled by a recycled id.
        if cascade_data is not self._last_cascade_obj:
            self.challenger_cascade = {}
            self._last_cascade_obj = cascade_data

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
                # THE CHALLENGER IS CONDITIONED ON ITSELF, NOT ON THE INCUMBENT.
                #
                # `cascade_data` is built by the server as `cascade_data[h] = p` AFTER the
                # full policy chain runs, and the same object was handed to both variants.
                # The model's hierarchical cascade reads `cascade_data[5]["direction"]` to
                # bias its 15m probabilities - so the challenger's 15m forecast was partly
                # the incumbent's post-policy 5m call. A challenger cannot be evaluated on
                # a forecast the incumbent partly made.
                #
                # `acc_cache` is the second, quieter half: it is the PRIMARY verifier's
                # live record, and the cascade only fires when the lower horizon has
                # demonstrated directional skill. The challenger was borrowing the
                # incumbent's track record to decide whether to trust its own call.
                #
                # Each variant now sits its own skill test. A challenger with no resolved
                # directional rows yet has no `lean_accuracy`, the gate reads absence as
                # unknown, and its cascade stays inert - which is the true answer, not a
                # defect. That asymmetry is RECORDED rather than papered over, because a
                # promotion decision must know whether it compared cascade-active against
                # cascade-inert.
                challenger_acc = self.variant_accuracy.get(self.challenger.label)
                # When the server passes no cascade at all, the challenger gets none
                # either - the point is symmetry of KIND, not handing the challenger an
                # input the incumbent did not have.
                own_cascade = self.challenger_cascade if cascade_data is not None else None
                challenger_pred = self.challenger.predict(
                    h, seq, data_state, challenger_acc, own_cascade)
                self.challenger_cascade[h] = copy.deepcopy(challenger_pred)
                self.last_by_horizon[h]["challenger"] = copy.deepcopy(challenger_pred)
                self.last_by_horizon[h]["challenger_evidence"] = (
                    "own_record" if challenger_acc else "none_cascade_inert")
                self.comparison_log.append({
                    "horizon": h,
                    "timestamp": int(time.time() * 1000),
                    "primary_direction": primary_pred.get("direction"),
                    "primary_confidence": primary_pred.get("confidence"),
                    "challenger_direction": challenger_pred.get("direction"),
                    "challenger_confidence": challenger_pred.get("confidence"),
                    "agree": primary_pred.get("direction") == challenger_pred.get("direction"),
                })
                self.total_comparisons += 1
                if primary_pred.get("direction") == challenger_pred.get("direction"):
                    self.total_agreements += 1
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

        n = self.total_comparisons
        agree_count = self.total_agreements
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
        
        passes_sample_size = self.challenger.total_verified >= criteria["min_verified"]
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

        # What this comparison actually IS, carried beside the numbers. Every field here
        # names a parity the test does NOT have; a promotion decision that reads the
        # verdict without them is claiming an experiment that was never run.
        evidence_integrity = {
            "comparison_basis": RAW_MODEL_COMPARISON,
            "challenger_cascade": "isolated_from_primary",
            "challenger_cascade_evidence": (
                "own_record" if self.variant_accuracy.get(self.challenger.label)
                else "none_cascade_inert"),
            "primary_evidence_scope": self.primary.evidence_scope if self.primary else "none",
            "challenger_evidence_scope": self.challenger.evidence_scope,
            "challenger_clock_source": self.challenger.started_at_source,
            "restore": dict(self.restore_report),
        }

        return {
            "enabled": True,
            "primary": self.primary.get_stats() if self.primary else {},
            "challenger": self.challenger.get_stats() if self.challenger else {},
            "evidence_integrity": evidence_integrity,
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
