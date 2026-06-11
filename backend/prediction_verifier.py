"""
Real-Time Prediction Verification Engine
Stores ALL predictions, compares them against actual outcomes once the horizon elapses,
tracks full scrollable history and per-timeframe accuracy metrics.
Feeds accuracy data back into model for auto-learning.
"""

import time
import logging
from collections import deque, defaultdict

logger = logging.getLogger(__name__)

# All supported horizons
ALL_HORIZONS = [1, 3, 5, 7, 10, 15]


class PredictionVerifier:
    """
    Tracks prediction outcomes in real-time with full history.
    
    Features:
      - Per-horizon recording: each horizon records independently at its own interval
      - Full scrollable history per timeframe
      - Per-timeframe accuracy metrics (overall, directional, by confidence)
      - Accuracy feedback for auto-learning (feeds back to model retraining)
    """

    def __init__(self, max_history_per_horizon: int = 500):
        self.max_history = max_history_per_horizon
        self.pending_predictions: list[dict] = []
        
        # Per-horizon verified history
        self.verified_by_horizon: dict[int, deque] = {
            h: deque(maxlen=max_history_per_horizon) for h in ALL_HORIZONS
        }
        # All verified (combined, for recent log)
        self.all_verified: deque = deque(maxlen=max_history_per_horizon * len(ALL_HORIZONS))
        
        self.accuracy_cache = {}
        
        # Per-horizon last recording time (ensures each horizon records at its own cadence)
        self.last_record_time: dict[int, float] = {h: 0.0 for h in ALL_HORIZONS}
        
        # Accuracy trend tracking (for auto-learning feedback)
        self.accuracy_history: dict[int, list] = {h: [] for h in ALL_HORIZONS}

        # Live confidence recalibration: per-horizon isotonic map (raw conf -> hit rate),
        # refit from verified outcomes. Fixes the train-time calibration drifting live.
        self.conf_calibrators: dict[int, dict] = {}
        self._calib_dirty = 0

        # Per-model, per-regime rolling correctness (for learned regime-specific
        # ensemble weights). Structure: regime -> model -> deque[0/1].
        self.regime_model_stats: dict = defaultdict(lambda: defaultdict(lambda: deque(maxlen=200)))
        # map internal model-direction keys to the ensemble weight keys
        self._model_key_map = {
            "xgb": "xgboost",
            "lgb": "lightgbm",
            "lgbm": "lightgbm",
            "cat": "catboost",
            "rf": "rf",
            "lr": "lr",
            "sgd": "sgd",
        }

    def should_record(self, horizon: int, now_ms: int) -> bool:
        """
        Check if we should record a new prediction for this horizon.
        Each horizon records at its own cadence:
          1m → every 60s, 3m → every 180s, etc.
        """
        interval_ms = horizon * 60 * 1000
        last = self.last_record_time.get(horizon, 0)
        return (now_ms - last) >= interval_ms

    def restore_from_database(self, pending_rows: list[dict], last_timestamps: dict[int, int]) -> int:
        """
        Restore unresolved predictions and per-horizon cadence after a backend
        reload. Without this, predictions made before a reload never resolve.
        """
        existing_ids = {p.get("id") for p in self.pending_predictions if p.get("id")}
        restored = 0
        for row in pending_rows or []:
            if row.get("id") in existing_ids:
                continue
            self.pending_predictions.append(row)
            restored += 1
        for h, ts in (last_timestamps or {}).items():
            if h in self.last_record_time:
                self.last_record_time[h] = max(self.last_record_time[h], int(ts))
        return restored

    def record_prediction(self, prediction: dict, current_price: float, now_ms: int):
        """
        Record a new prediction for later verification.
        """
        h = prediction["horizon"]
        expected_move = abs(prediction.get("expectedMove", prediction["targetPrice"] - current_price))
        signed_expected_move = prediction["targetPrice"] - current_price
        entry = {
            "horizon": h,
            "direction": prediction["direction"],
            "raw_direction": prediction.get("rawDirection", prediction["direction"]),
            "skip_reason": prediction.get("skipReason", ""),
            "neutral_reason_code": prediction.get("neutralReasonCode", ""),
            "neutral_reason": prediction.get("neutralReason", prediction.get("skipReason", "")),
            "quality_status": prediction.get("qualityStatus", ""),
            "confidence": prediction["confidence"],
            "target_price": prediction["targetPrice"],
            "expected_move_usd": round(expected_move, 2),
            "signed_expected_move_usd": round(signed_expected_move, 2),
            "predicted_price": current_price,
            "timestamp": now_ms,
            "verify_at": now_ms + h * 60 * 1000,
            "signal": prediction["signal"],
            "prob_up": prediction.get("probUp", 0),
            "prob_down": prediction.get("probDown", 0),
            "agreement": prediction.get("agreement", 0),
            "cascade_active": prediction.get("cascade_active", False),
            "id": prediction.get("id", ""),
            "model_dirs": prediction.get("modelDirs", {}),
            "regime": prediction.get("regime", "UNKNOWN"),
            # Grade with the SAME cost-floored adaptive band the model trained on.
            "neutral_band": float(prediction.get("neutralBand", 0.0008) or 0.0008),
        }
        self.pending_predictions.append(entry)
        self.last_record_time[h] = now_ms

    def check_and_verify(self, current_price: float, current_time_ms: int):
        """
        Check all pending predictions. If their horizon has elapsed, verify them.
        Returns list of newly verified predictions.
        """
        newly_verified = []
        still_pending = []

        for pred in self.pending_predictions:
            if current_time_ms >= pred["verify_at"]:
                actual_change = (current_price - pred["predicted_price"]) / pred["predicted_price"]
                # Use the prediction's own neutral band (same cost-floored adaptive
                # threshold as training) so we grade the model on its real target, not a
                # hardcoded 0.01% that almost never classifies the outcome as NEUTRAL.
                threshold = float(pred.get("neutral_band", 0.0008) or 0.0008)

                if actual_change > threshold:
                    actual_direction = "UP"
                elif actual_change < -threshold:
                    actual_direction = "DOWN"
                else:
                    actual_direction = "NEUTRAL"

                hit = (pred["direction"] == actual_direction)
                avoid_success = False
                if pred["direction"] == "NEUTRAL":
                    raw_direction = pred.get("raw_direction", "NEUTRAL")
                    avoid_success = (
                        actual_direction == "NEUTRAL"
                        or (raw_direction in ("UP", "DOWN") and raw_direction != actual_direction)
                    )
                    hit = avoid_success
                actual_move_usd = current_price - pred["predicted_price"]
                actual_abs_move_usd = abs(actual_move_usd)
                # SIGNED expected move (target carries the predicted direction). Taking
                # abs() of each side separately made the magnitude error direction-BLIND:
                # predict +$500, market does -$500 → abs(500-500)=$0, logging a
                # catastrophic miss as a PERFECT magnitude. Compare signed-to-signed so a
                # wrong-direction call is penalized by the full distance from target.
                expected_signed_move = pred["target_price"] - pred["predicted_price"]
                expected_move_usd = abs(expected_signed_move)
                move_error_usd = abs(actual_move_usd - expected_signed_move)
                target_error_usd = current_price - pred["target_price"]
                move_error_pct = (move_error_usd / expected_move_usd * 100) if expected_move_usd > 0 else 0.0
                price_tolerance_usd = max(10.0, expected_move_usd * 0.20)
                price_match = hit and move_error_usd <= price_tolerance_usd

                verified = {
                    **pred,
                    "actual_price": current_price,
                    "actual_direction": actual_direction,
                    "avoid_success": avoid_success,
                    "actual_move_usd": round(actual_move_usd, 2),
                    "actual_abs_move_usd": round(actual_abs_move_usd, 2),
                    "move_error_usd": round(move_error_usd, 2),
                    "target_error_usd": round(target_error_usd, 2),
                    "move_error_pct": round(move_error_pct, 2),
                    "price_match": price_match,
                    "actual_change_pct": round(actual_change * 100, 4),
                    "hit": hit,
                    "verified_at": current_time_ms,
                }
                
                # Per-model-per-regime correctness for learned regime weights.
                model_dirs = pred.get("model_dirs") or {}
                regime = pred.get("regime", "UNKNOWN")
                _lbl = {0: "DOWN", 1: "NEUTRAL", 2: "UP"}
                for mkey, d in model_dirs.items():
                    pred_lbl = _lbl.get(int(d)) if d is not None else None
                    if pred_lbl is not None:
                        self.regime_model_stats[regime][mkey].append(1 if pred_lbl == actual_direction else 0)

                h = pred["horizon"]
                self.verified_by_horizon[h].append(verified)
                self.all_verified.append(verified)
                newly_verified.append(verified)
            else:
                still_pending.append(pred)

        self.pending_predictions = still_pending

        if newly_verified:
            self._update_accuracy_cache()
            # Refit confidence calibrators on a cadence (every ~12 new outcomes).
            self._calib_dirty += len(newly_verified)
            if self._calib_dirty >= 12:
                self._calib_dirty = 0
                try:
                    self.refit_confidence_calibrators()
                except Exception:
                    pass

        return newly_verified

    def _update_accuracy_cache(self):
        """Recompute rolling accuracy per horizon with full metrics."""
        for h in ALL_HORIZONS:
            h_preds = list(self.verified_by_horizon[h])
            if not h_preds:
                continue

            hits = sum(1 for v in h_preds if v["hit"])
            total = len(h_preds)

            # Directional accuracy (excluding neutral predictions)
            dir_preds = [v for v in h_preds if v["direction"] != "NEUTRAL"]
            dir_hits = sum(1 for v in dir_preds if v["hit"])
            dir_total = len(dir_preds)

            # UP accuracy
            up_preds = [v for v in h_preds if v["direction"] == "UP"]
            up_hits = sum(1 for v in up_preds if v["hit"])
            up_total = len(up_preds)

            # DOWN accuracy
            down_preds = [v for v in h_preds if v["direction"] == "DOWN"]
            down_hits = sum(1 for v in down_preds if v["hit"])
            down_total = len(down_preds)

            avoid_preds = [v for v in h_preds if v["direction"] == "NEUTRAL"]
            avoid_hits = sum(1 for v in avoid_preds if v.get("avoid_success") or v["hit"])
            avoid_total = len(avoid_preds)

            # By confidence level
            high_conf = [v for v in h_preds if v["confidence"] >= 0.65]
            high_hits = sum(1 for v in high_conf if v["hit"])
            mid_conf = [v for v in h_preds if 0.55 <= v["confidence"] < 0.65]
            mid_hits = sum(1 for v in mid_conf if v["hit"])

            # Magnitude accuracy: direction can be correct while the expected
            # dollar move is too small or too large. This tracks that separately.
            move_errors = [v.get("move_error_usd", 0.0) for v in h_preds]
            target_errors = [abs(v.get("target_error_usd", 0.0)) for v in h_preds]
            direction_hits = [v for v in h_preds if v["hit"]]
            price_matches = [v for v in h_preds if v.get("price_match")]
            direction_right_price_off = [v for v in direction_hits if not v.get("price_match")]

            up_move_errors = [v.get("move_error_usd", 0.0) for v in up_preds]
            down_move_errors = [v.get("move_error_usd", 0.0) for v in down_preds]

            def avg(vals):
                return round(sum(vals) / len(vals), 2) if vals else 0.0

            def median(vals):
                if not vals:
                    return 0.0
                vals = sorted(vals)
                mid = len(vals) // 2
                if len(vals) % 2:
                    return round(vals[mid], 2)
                return round((vals[mid - 1] + vals[mid]) / 2, 2)

            # Streak tracking
            streak = 0
            streak_type = None
            for v in reversed(h_preds):
                if streak_type is None:
                    streak_type = "hit" if v["hit"] else "miss"
                    streak = 1
                elif (v["hit"] and streak_type == "hit") or (not v["hit"] and streak_type == "miss"):
                    streak += 1
                else:
                    break

            acc = round(hits / total, 4) if total > 0 else 0
            
            # Calculate Expectancy (Expected Value)
            gross_profit = sum(abs(v.get("actual_move_usd", 0.0)) for v in dir_preds if v["hit"])
            gross_loss = sum(abs(v.get("actual_move_usd", 0.0)) for v in dir_preds if not v["hit"])
            expectancy_usd = round((gross_profit - gross_loss) / dir_total, 2) if dir_total > 0 else 0.0
            
            self.accuracy_cache[h] = {
                "accuracy": acc,
                "total": total,
                "hits": hits,
                "misses": total - hits,
                "expectancy_usd": expectancy_usd,
                "directional_accuracy": round(dir_hits / dir_total, 4) if dir_total > 0 else 0,
                "directional_total": dir_total,
                "up_accuracy": round(up_hits / up_total, 4) if up_total > 0 else 0,
                "up_total": up_total,
                "up_hits": up_hits,
                "down_accuracy": round(down_hits / down_total, 4) if down_total > 0 else 0,
                "down_total": down_total,
                "down_hits": down_hits,
                "avoid_accuracy": round(avoid_hits / avoid_total, 4) if avoid_total > 0 else 0,
                "avoid_total": avoid_total,
                "avoid_hits": avoid_hits,
                "high_conf_accuracy": round(high_hits / len(high_conf), 4) if high_conf else 0,
                "high_conf_total": len(high_conf),
                "mid_conf_accuracy": round(mid_hits / len(mid_conf), 4) if mid_conf else 0,
                "mid_conf_total": len(mid_conf),
                "miss_rate": round((total - hits) / total, 4) if total > 0 else 0,
                "price_match_rate": round(len(price_matches) / total, 4) if total > 0 else 0,
                "direction_right_price_off": len(direction_right_price_off),
                "avg_move_error_usd": avg(move_errors),
                "median_move_error_usd": median(move_errors),
                "avg_target_error_usd": avg(target_errors),
                "up_avg_move_error_usd": avg(up_move_errors),
                "down_avg_move_error_usd": avg(down_move_errors),
                "current_streak": streak,
                "streak_type": streak_type or "none",
            }
            
            # Track accuracy over time for auto-learning feedback
            self.accuracy_history[h].append({
                "time": int(time.time() * 1000),
                "accuracy": acc,
                "total": total,
            })
            # Keep last 100 accuracy snapshots
            if len(self.accuracy_history[h]) > 100:
                self.accuracy_history[h] = self.accuracy_history[h][-100:]

    def get_regime_model_weights(self, regime: str, min_samples: int = 20) -> dict:
        """
        Learned regime-specific model weights from live per-model accuracy in this
        regime. Returns weights keyed by the ensemble's model names (xgboost,
        lightgbm, rf, lr), normalized to sum to 1; or {} if there isn't enough data
        yet (caller then keeps its default/heuristic weights).
        """
        stats = self.regime_model_stats.get(regime, {})
        accs = {}
        for mkey, dq in stats.items():
            if len(dq) >= min_samples:
                out_key = self._model_key_map.get(mkey, mkey)
                accs[out_key] = sum(dq) / len(dq)
        if len(accs) < 2:
            return {}
        total = sum(accs.values())
        if total <= 0:
            return {}
        return {k: v / total for k, v in accs.items()}

    def get_regime_calibration(self, min_samples: int = 30) -> dict:
        """
        Per-regime confidence calibration factor = (realized hit rate) / (mean stated
        confidence), shrunk toward 1.0 by sample size. Confidence means different things
        in a trend vs chop; multiplying conviction by this factor down-weights regimes
        where the model is *overconfident* (high confidence, low hit rate) and up-weights
        where it is underconfident — directly improving the win rate of actioned signals.
        Returns {regime: factor in [0.6, 1.4]}.
        """
        buckets = {}
        for h in ALL_HORIZONS:
            for v in self.verified_by_horizon[h]:
                if v.get("direction") not in ("UP", "DOWN"):
                    continue
                reg = v.get("regime", "UNKNOWN")
                b = buckets.setdefault(reg, {"conf": 0.0, "hit": 0.0, "n": 0})
                b["conf"] += float(v.get("confidence", 0.0) or 0.0)
                b["hit"] += 1.0 if v.get("hit") else 0.0
                b["n"] += 1
        out = {}
        for reg, b in buckets.items():
            n = b["n"]
            if n < 10:
                out[reg] = 1.0
                continue
            mean_conf = b["conf"] / n
            mean_hit = b["hit"] / n
            raw = (mean_hit / mean_conf) if mean_conf > 0 else 1.0
            w = min(1.0, n / float(min_samples))  # shrink toward 1.0 when sparse
            factor = (1.0 * (1 - w)) + (raw * w)
            out[reg] = round(max(0.6, min(1.4, factor)), 3)
        return out

    def refit_confidence_calibrators(self, min_samples: int = 40):
        """
        Refit per-horizon isotonic calibrators mapping the model's raw confidence to
        the realized hit rate. Isotonic enforces monotonicity (higher confidence ⇒
        not-lower hit rate), which directly repairs the live inversion where a 0.5
        confidence was hitting *less* than a 0.4. Cheap; call on a cadence.
        """
        try:
            from sklearn.isotonic import IsotonicRegression
        except Exception:
            return
        for h in ALL_HORIZONS:
            confs, hits = [], []
            for v in self.verified_by_horizon[h]:
                if v.get("direction") in ("UP", "DOWN"):
                    confs.append(float(v.get("confidence", 0.0) or 0.0))
                    hits.append(1.0 if v.get("hit") else 0.0)
            n = len(confs)
            if n < min_samples or len(set(hits)) < 2:
                continue
            try:
                iso = IsotonicRegression(out_of_bounds="clip", y_min=0.05, y_max=0.95)
                iso.fit(confs, hits)
                self.conf_calibrators[h] = {"iso": iso, "n": n}
            except Exception:
                continue

    def get_confidence_calibrators(self) -> dict:
        """Per-horizon {iso, n} calibrators for the model to apply at inference."""
        return self.conf_calibrators

    def get_regime_model_accuracy(self) -> dict:
        """Per-regime, per-model live accuracy (for monitoring/UI)."""
        out = {}
        for regime, models in self.regime_model_stats.items():
            out[regime] = {
                self._model_key_map.get(m, m): {
                    "accuracy": round(sum(dq) / len(dq), 3) if dq else 0.0,
                    "n": len(dq),
                }
                for m, dq in models.items() if len(dq) > 0
            }
        return out

    def get_accuracy_summary(self) -> dict:
        """Get current accuracy summary for all horizons."""
        return dict(self.accuracy_cache)

    def get_signal_policy(
        self,
        regime: str = "UNKNOWN",
        min_samples: int = 25,
        target_precision: float = 0.57,
    ) -> dict:
        """
        Learn an adaptive confidence threshold from resolved raw UP/DOWN leans.

        This is intentionally based on raw_direction, not only executed direction,
        so the system can discover when a currently skipped class of signals would
        have worked. The policy is precision-first but keeps an action-rate term so
        it does not become "accurate" by trading only one sample.
        """
        def build_policy(rows: list[dict], label: str) -> dict:
            cand = [
                r for r in rows
                if r.get("raw_direction") in ("UP", "DOWN")
                and float(r.get("confidence", 0.0) or 0.0) > 0
                and r.get("actual_direction") in ("UP", "DOWN", "NEUTRAL")
            ]
            n = len(cand)
            if n < min_samples:
                return {
                    "ready": False,
                    "scope": label,
                    "samples": n,
                    "threshold": None,
                    "message": f"Need {min_samples - n} more resolved raw leans.",
                }

            best = None
            for i in range(38, 76):
                th = i / 100.0
                selected = [r for r in cand if float(r.get("confidence", 0.0) or 0.0) >= th]
                selected_n = len(selected)
                if selected_n < max(8, int(n * 0.06)):
                    continue
                hits = sum(1 for r in selected if r.get("raw_direction") == r.get("actual_direction"))
                precision = hits / selected_n if selected_n else 0.0
                rate = selected_n / n
                # Precision dominates, action rate breaks ties so the gate can allow
                # more calls when two thresholds are similarly accurate.
                score = (0.78 * precision) + (0.22 * min(rate, 0.65) / 0.65)
                if precision < target_precision:
                    score -= (target_precision - precision) * 0.7
                if best is None or score > best["score"]:
                    best = {
                        "threshold": th,
                        "precision": precision,
                        "selected": selected_n,
                        "samples": n,
                        "action_rate": rate,
                        "score": score,
                    }

            if not best:
                confs = sorted(float(r.get("confidence", 0.0) or 0.0) for r in cand)
                th = confs[int(0.70 * (len(confs) - 1))]
                selected = [r for r in cand if float(r.get("confidence", 0.0) or 0.0) >= th]
                hits = sum(1 for r in selected if r.get("raw_direction") == r.get("actual_direction"))
                best = {
                    "threshold": th,
                    "precision": hits / len(selected) if selected else 0.0,
                    "selected": len(selected),
                    "samples": n,
                    "action_rate": len(selected) / n if n else 0.0,
                    "score": 0.0,
                }

            return {
                "ready": True,
                "scope": label,
                "threshold": round(float(best["threshold"]), 3),
                "precision": round(float(best["precision"]), 4),
                "samples": int(best["samples"]),
                "selected": int(best["selected"]),
                "action_rate": round(float(best["action_rate"]), 4),
                "target_precision": target_precision,
                "message": (
                    f"{label}: threshold {best['threshold']:.2f}, "
                    f"raw precision {best['precision']:.1%}, "
                    f"action rate {best['action_rate']:.1%}."
                ),
            }

        out = {"regime": regime, "by_horizon": {}, "by_regime": {}}
        for h in ALL_HORIZONS:
            rows = list(self.verified_by_horizon[h])
            out["by_horizon"][h] = build_policy(rows, f"{h}m all regimes")
            reg_rows = [r for r in rows if r.get("regime", "UNKNOWN") == regime]
            out["by_regime"][h] = build_policy(reg_rows, f"{h}m {regime}")
        return out

    def get_neutral_reason_summary(self, window: int = 300) -> dict:
        """Count why recent final decisions became NEUTRAL/WAIT."""
        rows = list(self.all_verified)[-window:]
        neutral = [r for r in rows if r.get("direction") == "NEUTRAL"]
        counts = {}
        for r in neutral:
            code = r.get("neutral_reason_code") or "unknown"
            counts[code] = counts.get(code, 0) + 1
        total = len(neutral)
        ranked = [
            {
                "code": k,
                "count": v,
                "pct": round(v / total, 4) if total else 0.0,
            }
            for k, v in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
        ]
        return {"window": window, "total": total, "reasons": ranked}

    def get_data_quality_summary(self, min_ready: int = 100, strong: int = 500) -> dict:
        """Sample-size maturity for each horizon and the combined analysis view."""
        horizons = {}
        total_resolved = 0
        ready_count = 0
        for h in ALL_HORIZONS:
            row = self.accuracy_cache.get(h) or {}
            n = int(row.get("total", 0) if isinstance(row, dict) else 0)
            total_resolved += n
            if n >= min_ready:
                ready_count += 1
            if n < min_ready:
                label = "not_enough_data"
                message = f"Needs {min_ready - n} more verified {h}m predictions before this horizon is statistically useful."
            elif n < strong:
                label = "early_read"
                message = "Enough for a first read, but still not a strong sample."
            else:
                label = "usable"
                message = "Sample size is usable for live decision support."
            horizons[h] = {"resolved": n, "label": label, "message": message}
        if ready_count == len(ALL_HORIZONS):
            overall = "usable"
        elif total_resolved >= min_ready:
            overall = "mixed"
        else:
            overall = "not_enough_data"
        return {
            "overall": overall,
            "resolved_total": total_resolved,
            "ready_horizons": ready_count,
            "required_per_horizon": min_ready,
            "strong_per_horizon": strong,
            "horizons": horizons,
        }

    def get_regime_horizon_quality(self, min_samples: int = 50) -> dict:
        """LEAN accuracy by horizon/regime for regime-specific skip rules.

        Graded by raw_direction vs actual_direction (sign-truth), NOT the `hit` column:
        `hit` is dual-semantic — on gated rows (the majority) it equals avoid_success,
        which is TRUE when the lean was WRONG. Counting that here INVERTED the poor-regime
        blocker: regimes where leans were consistently wrong-but-gated read as high-accuracy
        (blocker stayed open exactly where the model is worst), while honest committed
        regimes could read lower and get blocked. Only raw-directional rows count; the
        question this answers is "do this regime's LEANS work", not "did the gate behave"."""
        out = {}
        for h in ALL_HORIZONS:
            h_preds = list(self.verified_by_horizon[h])
            by_regime = defaultdict(list)
            for v in h_preds:
                if v.get("raw_direction") in ("UP", "DOWN"):
                    by_regime[v.get("regime", "UNKNOWN")].append(v)
            out[h] = {}
            for regime, rows in by_regime.items():
                total = len(rows)
                hits = sum(1 for r in rows
                           if r.get("raw_direction") == r.get("actual_direction"))
                out[h][regime] = {
                    "total": total,
                    "accuracy": round(hits / total, 4) if total else 0.0,
                    "ready": total >= min_samples,
                }
        return out

    def get_recent_verifications(self, n: int = 30) -> list[dict]:
        """Get the N most recent verified predictions (all horizons combined)."""
        verified = list(self.all_verified)[-n:]
        return [self._format_verification(v) for v in reversed(verified)]

    def get_horizon_history(self, horizon: int, n: int = 50) -> list[dict]:
        """Get full scrollable history for a specific horizon."""
        h_preds = list(self.verified_by_horizon.get(horizon, []))[-n:]
        return [self._format_verification(v) for v in reversed(h_preds)]

    def get_all_horizon_histories(self, n_per_horizon: int = 50) -> dict:
        """Get scrollable history for ALL horizons."""
        return {
            h: self.get_horizon_history(h, n_per_horizon) for h in ALL_HORIZONS
        }

    def get_error_summary(self) -> dict:
        """Get combined direction and dollar-move error metrics for the analysis tab."""
        all_preds = [v for v in self.all_verified if v.get("direction") in ("UP", "DOWN")]
        if not all_preds:
            return {
                "total": 0,
                "direction_accuracy": 0.0,
                "miss_rate": 0.0,
                "price_match_rate": 0.0,
                "avg_move_error_usd": 0.0,
                "up_avg_move_error_usd": 0.0,
                "down_avg_move_error_usd": 0.0,
                "direction_right_price_off": 0,
            }

        total = len(all_preds)
        hits = sum(1 for v in all_preds if v["hit"])
        price_matches = sum(1 for v in all_preds if v.get("price_match"))
        direction_right_price_off = sum(1 for v in all_preds if v["hit"] and not v.get("price_match"))

        def avg(vals):
            return round(sum(vals) / len(vals), 2) if vals else 0.0

        up_errors = [v.get("move_error_usd", 0.0) for v in all_preds if v["direction"] == "UP"]
        down_errors = [v.get("move_error_usd", 0.0) for v in all_preds if v["direction"] == "DOWN"]

        return {
            "total": total,
            "direction_accuracy": round(hits / total, 4),
            "miss_rate": round((total - hits) / total, 4),
            "price_match_rate": round(price_matches / total, 4),
            "avg_move_error_usd": avg([v.get("move_error_usd", 0.0) for v in all_preds]),
            "up_avg_move_error_usd": avg(up_errors),
            "down_avg_move_error_usd": avg(down_errors),
            "direction_right_price_off": direction_right_price_off,
        }

    def get_action_accuracy_summary(self) -> dict:
        """Accuracy by plain-language action: buy/up, sell/down, avoid/skip."""
        rows = list(self.all_verified)

        def summarize(subset: list[dict], hit_key: str = "hit") -> dict:
            total = len(subset)
            hits = sum(1 for r in subset if r.get(hit_key) or (hit_key == "avoid_success" and r.get("hit")))
            
            capital_saved = 0.0
            if hit_key == "avoid_success":
                capital_saved = sum(r.get("actual_abs_move_usd", 0.0) for r in subset if r.get("hit") or r.get("avoid_success"))
                
            return {
                "total": total,
                "hits": hits,
                "misses": total - hits,
                "accuracy": round(hits / total, 4) if total else 0.0,
                "capital_saved_usd": round(capital_saved, 2)
            }

        buy = [r for r in rows if r.get("direction") == "UP"]
        sell = [r for r in rows if r.get("direction") == "DOWN"]
        avoid = [r for r in rows if r.get("direction") == "NEUTRAL"]
        directional = [r for r in rows if r.get("direction") in ("UP", "DOWN")]
        return {
            "all": summarize(rows),
            "directional": summarize(directional),
            "buy": summarize(buy),
            "up": summarize(buy),
            "sell": summarize(sell),
            "down": summarize(sell),
            "avoid": summarize(avoid, "avoid_success"),
            "skip": summarize(avoid, "avoid_success"),
        }

    def _format_verification(self, v: dict) -> dict:
        return {
            "horizon": v["horizon"],
            "direction": v["direction"],
            "raw_direction": v.get("raw_direction", v["direction"]),
            "skip_reason": v.get("skip_reason", ""),
            "actual_direction": v["actual_direction"],
            "confidence": v["confidence"],
            "hit": v["hit"],
            "avoid_success": v.get("avoid_success", False),
            "price_match": v.get("price_match", False),
            "expected_move_usd": round(v.get("expected_move_usd", 0), 2),
            "signed_expected_move_usd": round(v.get("signed_expected_move_usd", 0), 2),
            "actual_move_usd": round(v.get("actual_move_usd", 0), 2),
            "actual_abs_move_usd": round(v.get("actual_abs_move_usd", 0), 2),
            "move_error_usd": round(v.get("move_error_usd", 0), 2),
            "target_error_usd": round(v.get("target_error_usd", 0), 2),
            "move_error_pct": round(v.get("move_error_pct", 0), 2),
            "actual_change_pct": v["actual_change_pct"],
            "signal": v["signal"],
            "predicted_price": round(v["predicted_price"], 2),
            "actual_price": round(v["actual_price"], 2),
            "timestamp": v["timestamp"],
            "verified_at": v["verified_at"],
        }

    def get_pending_count(self) -> int:
        return len(self.pending_predictions)

    def get_pending_by_horizon(self) -> dict:
        """Get pending count per horizon."""
        counts = {h: 0 for h in ALL_HORIZONS}
        for p in self.pending_predictions:
            h = p["horizon"]
            if h in counts:
                counts[h] += 1
        return counts

    def get_learning_feedback(self) -> dict:
        """
        Provide accuracy feedback for auto-learning.
        Returns per-horizon accuracy trends and worst-performing areas.
        """
        feedback = {}
        for h in ALL_HORIZONS:
            acc = self.accuracy_cache.get(h, {})
            if not acc or acc.get("total", 0) < 5:
                continue
            
            # Compute accuracy trend (improving or degrading?)
            history = self.accuracy_history.get(h, [])
            trend = "stable"
            if len(history) >= 3:
                recent_acc = [x["accuracy"] for x in history[-3:]]
                older_acc = [x["accuracy"] for x in history[:max(1, len(history)-3)]]
                avg_recent = sum(recent_acc) / len(recent_acc)
                avg_older = sum(older_acc) / len(older_acc)
                if avg_recent > avg_older + 0.03:
                    trend = "improving"
                elif avg_recent < avg_older - 0.03:
                    trend = "degrading"

            feedback[h] = {
                "accuracy": acc["accuracy"],
                "trend": trend,
                "up_accuracy": acc.get("up_accuracy", 0),
                "down_accuracy": acc.get("down_accuracy", 0),
                "miss_rate": acc.get("miss_rate", 0),
                "price_match_rate": acc.get("price_match_rate", 0),
                "avg_move_error_usd": acc.get("avg_move_error_usd", 0),
                "total": acc["total"],
                "needs_retrain": (
                    trend == "degrading" and acc["accuracy"] < 0.45
                ) or (
                    acc.get("total", 0) >= 10
                    and acc.get("price_match_rate", 1) < 0.35
                    and acc.get("avg_move_error_usd", 0) > 75
                ),
            }
        
        return feedback
