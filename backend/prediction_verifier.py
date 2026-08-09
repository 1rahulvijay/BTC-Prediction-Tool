"""
Real-Time Prediction Verification Engine
Stores ALL predictions, compares them against actual outcomes once the horizon elapses,
tracks full scrollable history and per-timeframe accuracy metrics.
Feeds accuracy data back into model for auto-learning.
"""

import time
import logging
import os
from collections import deque, defaultdict

logger = logging.getLogger(__name__)

# All supported horizons
ALL_HORIZONS = [5, 15]   # pruned 2026-06-21: dropped 3/7/10/30


import target_contract as _tc  # noqa: E402


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
        #: Rows that reached a TERMINAL non-graded state and must be persisted as such. Drained
        #: by the server each cycle. Without this, a row left memory and stayed PENDING on disk.
        self.terminal_invalid: list[dict] = []
        self.invalid_late = 0
        
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

        # Per-horizon, per-regime, per-model rolling correctness. Five-minute outcomes
        # arrive three times as often as 15-minute outcomes; pooling them made the 5m model
        # silently control both horizons' expert weights.
        self.regime_model_stats: dict = defaultdict(
            lambda: defaultdict(lambda: defaultdict(lambda: deque(maxlen=200)))
        )
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
        now_ms = int(time.time() * 1000)
        for row in pending_rows or []:
            if row.get("id") in existing_ids:
                continue
            # A missed verification boundary cannot be graded at the restart price.
            # The database janitor retains it as INVALID evidence instead.
            if int(row.get("verify_at") or 0) <= now_ms:
                continue
            self.pending_predictions.append(row)
            restored += 1
        for h, ts in (last_timestamps or {}).items():
            if h in self.last_record_time:
                self.last_record_time[h] = max(self.last_record_time[h], int(ts))
        return restored

    def restore_verified_from_database(self, resolved_rows: list[dict]) -> int:
        """Rehydrate current-model live metrics without mixing model eras."""
        restored = 0
        for row in resolved_rows or []:
            try:
                horizon = int(row["horizon"])
            except (KeyError, TypeError, ValueError):
                continue
            if horizon not in self.verified_by_horizon:
                continue
            self.verified_by_horizon[horizon].append(row)
            self.all_verified.append(row)
            restored += 1

            model_dirs = row.get("model_dirs") or {}
            regime = row.get("regime", "UNKNOWN")
            actual_strict = str(row.get("actual_direction") or "")
            if actual_strict not in ("UP", "DOWN", "NEUTRAL"):
                # Legacy rows without a persisted contract grade are not valid expert
                # feedback. Re-deriving from move sign would answer a different target.
                continue
            labels = {0: "DOWN", 1: "NEUTRAL", 2: "UP"}
            for model_key, direction in model_dirs.items():
                try:
                    predicted = labels.get(int(direction))
                except (TypeError, ValueError):
                    predicted = None
                if predicted in ("UP", "DOWN"):
                    self.regime_model_stats[horizon][regime][model_key].append(
                        1 if predicted == actual_strict else 0
                    )

        if restored:
            self._update_accuracy_cache()
            try:
                self.refit_confidence_calibrators()
            except Exception:
                pass
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
            "model_raw_direction": prediction.get("modelRawDirection", prediction.get("rawDirection", prediction["direction"])),
            "raw_direction": prediction.get("rawDirection", prediction["direction"]),
            "pre_server_direction": prediction.get("preServerDirection", prediction["direction"]),
            "final_direction": prediction.get("finalDirection", prediction["direction"]),
            # The contract this model was TRAINED under. The grader dispatches on it instead of
            # assuming endpoint settlement, which is how a first-touch model came to be
            # corrected by settlement feedback.
            "target_contract": _tc.TRAINING_CONTRACT,
            "predicted_at": now_ms,
            "trade_verdict": prediction.get("trade_verdict", ""),
            "no_trade_reasons": prediction.get("no_trade_reasons", []),
            "skip_reason": prediction.get("skipReason", ""),
            "neutral_reason_code": prediction.get("neutralReasonCode", ""),
            "neutral_reason": prediction.get("neutralReason", prediction.get("skipReason", "")),
            "quality_status": prediction.get("qualityStatus", ""),
            "confidence": prediction["confidence"],
            # The PRE-CALIBRATION score. `confidence` has already been through regime
            # calibration and the live isotonic map, so fitting a calibrator on it means
            # calibrating a calibrator's own previous output - and feeding that back in. This
            # is the immutable score the calibrator must be fitted against.
            "confidence_raw": float(
                prediction.get("confidenceRaw", prediction.get("confidence", 0.0)) or 0.0),
            "confidence_raw_available": prediction.get("confidenceRaw") is not None,
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
            "neutral_band": _tc.resolve_neutral_band(prediction.get("neutralBand")),
        }
        self.pending_predictions.append(entry)
        self.last_record_time[h] = now_ms


    #: P0-11. A "5 minute" prediction resolved by whatever `current_price` happened to be on the
    #: first main-loop iteration past verify_at. The loop can be delayed by training, CPU
    #: contention, a blocking query or a feed stall, so the graded price could be seconds or
    #: minutes late - and near the threshold that changes the label. Beyond this bound the row is
    #: marked INVALID_LATE rather than graded on a price from the wrong moment.
    MAX_RESOLUTION_LATENESS_MS = 30_000

    @staticmethod
    def _as_of_price(klines, verify_at_ms: int):
        """Close of the last CLOSED bar at or before verify_at, with its timestamp.

        Resolving from `current_price` grades at loop time, not at horizon end."""
        best = None
        best_ms = None
        for k in (klines or []):
            # NORMALISED. Production klines carry SECONDS; verify_at is MILLISECONDS.
            # Comparing them raw made this select the newest bar rather than the one at the
            # horizon boundary, on every single grade.
            ts = _tc.kline_open_ms(k)
            if ts <= verify_at_ms and k.get("is_closed") is not False:
                if best_ms is None or ts > best_ms:
                    best, best_ms = k, ts
        if best is None:
            return None, None
        return float(best["close"]), int(best_ms)

    #: Grading rules, keyed by the contract a prediction was trained under.
    def _grade(self, pred: dict, current_price: float, threshold: float, klines):
        """Return (direction, status). `direction` is None when the row must not be graded.

        P1-3. The rule itself now lives in `target_contract.grade`, which the per-model panel
        also calls. Two copies of "how a prediction is graded" is how the panels came to
        describe the same vote with two different random variables."""
        result = _tc.grade(
            contract=pred.get("target_contract") or _tc.TRAINING_CONTRACT,
            entry=pred["predicted_price"],
            threshold=threshold,
            klines=klines,
            entry_ts=int(pred.get("predicted_at") or pred.get("created_at") or 0),
            verify_ts=int(pred.get("verify_at") or 0),
        )
        if result.resolution_event_ts is not None:
            pred["resolution_price_source"] = result.resolution_basis
            pred["resolution_event_ts"] = result.resolution_event_ts
            pred["resolution_price"] = result.resolution_price
        return result

    def check_and_verify(self, current_price: float, current_time_ms: int, klines=None):
        """
        Check all pending predictions. If their horizon has elapsed, verify them.
        Returns list of newly verified predictions.

        P0-1 TARGET CONTRACT. This used to grade EVERY prediction by comparing the price at the
        horizon end against entry, while the model was trained on a first-touch triple barrier.
        Those are different random variables and they disagree on ~25% of random-walk paths
        (measured in target_contract.selftest). A first-touch model could be graded wrong for
        being right, and that wrong grade then fed confidence recalibration, regime weights,
        auto-learning and the accuracy panels.

        Each prediction now carries the NAME of the contract it was trained under, and is graded
        by the matching rule. Grading a first-touch prediction needs the intrabar path, so
        `klines` (1m bars covering entry..verify_at) must be supplied; without them the row is
        marked GRADE_UNAVAILABLE rather than silently graded by the wrong rule.
        """
        newly_verified = []
        still_pending = []

        for pred in self.pending_predictions:
            if current_time_ms >= pred["verify_at"]:
                # `actual_change` is deliberately NOT computed here. It used to be derived from
                # the loop-time price at the top of the block and then reported beside a
                # direction graded from a different moment; it is now computed once, below,
                # from the resolution observation. One definition, one moment.
                # Use the prediction's own neutral band (same cost-floored adaptive
                # threshold as training) so we grade the model on its real target, not a
                # hardcoded 0.01% that almost never classifies the outcome as NEUTRAL.
                threshold = _tc.resolve_neutral_band(pred.get("neutral_band"))

                # P0-11 LATENESS. Record how late this resolution actually is and refuse to
                # grade beyond the declared bound instead of pretending the delay did not
                # happen. Recorded on every row so the distribution is visible, not assumed.
                lateness_ms = int(current_time_ms) - int(pred.get("verify_at") or 0)
                pred["scheduled_resolution_ts"] = int(pred.get("verify_at") or 0)
                pred["resolution_lateness_ms"] = lateness_ms
                if lateness_ms > self.MAX_RESOLUTION_LATENESS_MS:
                    pred["grade_status"] = f"INVALID_LATE:{lateness_ms}ms"
                    self.invalid_late = getattr(self, "invalid_late", 0) + 1
                    # TERMINAL, AND RECORDED. Refusing to grade a late row is right; dropping
                    # it was not. `continue` skips both still_pending and newly_verified, and
                    # newly_verified is the ONLY thing the server persists - so the row left
                    # memory while staying PENDING in DuckDB, reconciled only by the next
                    # restart's orphan sweep, which then attributes it to
                    # RESTART_MISSED_BOUNDARY: a different and untrue cause.
                    #
                    # Queued rather than written here: this class does no I/O, and a DB call
                    # inside the grading loop would put a lock on the hot path.
                    self.terminal_invalid.append({
                        "id": pred.get("id"),
                        "horizon": int(pred.get("horizon") or 0),
                        "reason": f"INVALID_LATE:{lateness_ms}ms",
                    })
                    continue          # dropped: not graded, not silently mislabelled

                result = self._grade(pred, current_price, threshold, klines)
                actual_direction, grade_status = result.direction, result.status
                pred["grade_status"] = grade_status
                if actual_direction is None:
                    # Cannot grade under the declared contract. Leave PENDING rather than
                    # emitting a label produced by a rule the model was not trained on.
                    still_pending.append(pred)
                    continue

                hit = (pred["direction"] == actual_direction)
                avoid_success = False
                if pred["direction"] == "NEUTRAL":
                    raw_direction = pred.get("raw_direction", "NEUTRAL")
                    avoid_success = (
                        actual_direction == "NEUTRAL"
                        or (raw_direction in ("UP", "DOWN") and raw_direction != actual_direction)
                    )
                    hit = avoid_success
                # P1-1. THE RESOLUTION OBSERVATION. Every field below is measured from this one
                # price at this one timestamp - the observation that produced `actual_direction`
                # itself. It used to be `current_price`, the main loop's price at whatever
                # moment it reached this line, so a single row could carry a DOWN direction
                # taken at the horizon boundary next to a POSITIVE move measured twenty seconds
                # later. That contaminated magnitude error, target error, expectancy, lean-hit,
                # the calibration labels and the learned regime weights simultaneously.
                resolution_price = float(result.resolution_price)
                # TWO OBSERVATIONS, KEPT SEPARATE (scan-5 items 5.5/5.6/5.7).
                #
                # Under FIRST TOUCH, `resolution_price` is the BARRIER - the observation that
                # defined the outcome. target_contract says so at its own definition: "under
                # first touch, |move| is always the barrier distance, so magnitude error on
                # these rows measures the barrier, not a magnitude forecast ... endpoint_price,
                # which is carried for exactly that purpose."
                #
                # It was nonetheless used for actual_move_usd, target/move error, the forward-EV
                # ledger and the live gate's `expectancy_usd` - which the UI calls "historical
                # EV". Four consumers computing trading economics from a classification barrier,
                # where |move| is a CONSTANT by construction.
                #
                # THE FIX IS NOT TO OVERWRITE actual_price. P1-1 requires that ONE GRADED ROW
                # DESCRIBES ONE MOMENT: `actual_price`, `actual_move_usd`, `target_error_usd`
                # and `actual_change_pct` all belong to the moment named by
                # `resolution_event_ts`, which under first touch IS the barrier touch. Swapping
                # the price to the endpoint while leaving that timestamp made the row describe
                # two moments - the exact defect P1-1 exists to catch, and it did.
                #
                # So the endpoint economics are ADDED as their own clearly-named fields. The
                # classification row stays internally consistent; economic consumers read
                # `endpoint_*`; and `endpoint_price_basis` says whether a real endpoint existed,
                # so a barrier fallback can never be mistaken for endpoint economics.
                _endpoint = getattr(result, "endpoint_price", None)
                endpoint_price = float(_endpoint) if _endpoint is not None else resolution_price
                endpoint_basis = "ENDPOINT" if _endpoint is not None else "BARRIER_FALLBACK"
                endpoint_move_usd = endpoint_price - pred["predicted_price"]
                actual_move_usd = resolution_price - pred["predicted_price"]
                actual_abs_move_usd = abs(actual_move_usd)
                actual_change = actual_move_usd / pred["predicted_price"]
                # Pure LEAN sign-truth for the DB's lean_hit column. Unlike `hit`
                # (dual-semantic: avoid_success on gated rows), this is always
                # "did the raw lean match the realized outcome" - the betting truth, and the
                # column the schema itself names as the one a betting-accuracy consumer must
                # read.
                #
                # IT COMPARED AGAINST THE MOVE SIGN, NOT THE CONTRACT'S OUTCOME:
                #
                #     lean_hit = (_raw_lean == "UP") == (actual_move_usd > 0)
                #
                # On a TOUCHING row that is right by accident - `resolution_price` is the
                # barrier, so its sign IS the graded direction. On a TIMEOUT row it is wrong:
                # `resolution_price` is the last bar's close, `actual_move_usd` is a small
                # residual drift, and a lean was credited as CORRECT whenever that drift
                # happened to agree with it. The contract graded that row NEUTRAL - no barrier
                # was reached, so the bet did not win.
                #
                # Timeouts are not rare. The 5m training distribution is
                # DOWN 23,009 / NEUTRAL 40,206 / UP 23,110 - NEUTRAL is 46.6% of rows, so
                # nearly half the betting-accuracy column was a coin flip on residual drift
                # that the contract says was not a win.
                #
                # The graded direction is right here, in `actual_direction`, produced by the
                # same `grade()` call as the price. Comparing against it makes lean_hit mean
                # what its own docstring says.
                _raw_lean = pred.get("raw_direction", pred.get("direction"))
                lean_hit = None
                if _raw_lean in ("UP", "DOWN") and actual_direction in ("UP", "DOWN", "NEUTRAL"):
                    lean_hit = (_raw_lean == actual_direction)
                # SIGNED expected move (target carries the predicted direction). Taking
                # abs() of each side separately made the magnitude error direction-BLIND:
                # predict +$500, market does -$500 → abs(500-500)=$0, logging a
                # catastrophic miss as a PERFECT magnitude. Compare signed-to-signed so a
                # wrong-direction call is penalized by the full distance from target.
                expected_signed_move = pred["target_price"] - pred["predicted_price"]
                expected_move_usd = abs(expected_signed_move)
                move_error_usd = abs(actual_move_usd - expected_signed_move)
                target_error_usd = resolution_price - pred["target_price"]
                move_error_pct = (move_error_usd / expected_move_usd * 100) if expected_move_usd > 0 else 0.0
                price_tolerance_usd = max(10.0, expected_move_usd * 0.20)
                price_match = hit and move_error_usd <= price_tolerance_usd

                verified = {
                    **pred,
                    "actual_price": resolution_price,
                    # ECONOMIC observation, separate from the classification one above. Under
                    # first touch |actual_move_usd| is the barrier distance - a CONSTANT by
                    # construction - so magnitude error, forward EV and "historical EV" computed
                    # from it measured the barrier, not a forecast.
                    "endpoint_price": endpoint_price,
                    "endpoint_move_usd": round(endpoint_move_usd, 2),
                    "endpoint_price_basis": endpoint_basis,
                    # Recorded ON the row: which observation every number here came from, and
                    # when. A price without its basis is unauditable after the fact.
                    "resolution_price": resolution_price,
                    "resolution_event_ts": result.resolution_event_ts,
                    "resolution_basis": result.resolution_basis,
                    "loop_price_at_verification": current_price,
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
                    "lean_hit": lean_hit,
                    "verified_at": current_time_ms,
                }
                
                # Per-model-per-regime correctness for the LEARNED regime weights.
                # §5ba / external-review #8 (2026-06-14): grade ONLY committed (UP/DOWN) votes, by
                # STRICT close-vs-ref sign — the SAME definition model_verifier uses for the UI panel.
                # Previously this counted NEUTRAL abstentions as misses (and used a neutral BAND for the
                # outcome), so the regime weights learned from a neutral-poisoned definition that
                # disagreed with the displayed per-model accuracy and penalized models for abstaining.
                model_dirs = pred.get("model_dirs") or {}
                regime = pred.get("regime", "UNKNOWN")
                _lbl = {0: "DOWN", 1: "NEUTRAL", 2: "UP"}
                # P1-1 / P1-3. This was:
                #     "UP" if current_price >= pred["predicted_price"] else "DOWN"
                # a loop-time ENDPOINT sign, while the models are trained on first touch. The
                # learned regime weights - which decide how much each seat is trusted per
                # regime - were therefore fitted against the very target mismatch the verifier
                # was rebuilt to remove, and against a price from the wrong moment on top.
                # Now the graded outcome under the declared contract, which is also exactly
                # what model_verifier's panel uses, so the weights and the panel cannot
                # disagree about whether a seat was right.
                _actual_strict = actual_direction
                h = pred["horizon"]
                for mkey, d in model_dirs.items():
                    try:
                        pred_lbl = _lbl.get(int(d)) if d is not None else None
                    except Exception:
                        pred_lbl = None
                    if pred_lbl in ("UP", "DOWN"):           # committed votes only; NEUTRAL excluded
                        self.regime_model_stats[h][regime][mkey].append(
                            1 if pred_lbl == _actual_strict else 0
                        )

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

            # LEAN accuracy - the headline betting metric. Counts every raw lean (committed
            # AND gated), graded by the CONTRACT'S outcome. Without this, a cautious gate
            # (all WAITs) leaves the accuracy panel empty forever while leans resolve in
            # plain sight.
            #
            # The fallback below is for rows verified by older code, and it used the move
            # SIGN - which credits a lean on a row the contract graded NEUTRAL, the same
            # defect `lean_hit` itself carried. Where the graded direction is on the row it
            # is used; where it is not, the row is UNKNOWN and excluded, rather than given
            # an answer from a rule the contract does not use.
            def _lean_ok(v):
                if v.get("lean_hit") is not None:
                    return v["lean_hit"]
                rd = v.get("raw_direction")
                ad = v.get("actual_direction")
                if rd in ("UP", "DOWN") and ad in ("UP", "DOWN", "NEUTRAL"):
                    return rd == ad
                return None
            _lean_rows = [(v, _lean_ok(v)) for v in h_preds]
            lean_rows = [(v, ok) for v, ok in _lean_rows if ok is not None]
            lean_total = len(lean_rows)
            lean_hits = sum(1 for _, ok in lean_rows if ok)
            # TWO NAMESPACES, BECAUSE CORRECTING THE METRIC WITHOUT THEM WOULD BREAK EVERY
            # THRESHOLD WRITTEN AGAINST IT.
            #
            # `lean_accuracy` above now counts a NEUTRAL contract outcome as a miss, which is
            # what the contract says: no barrier was reached, the bet did not win. But NEUTRAL
            # is ~46.6% of 5m rows, so the ceiling for a directional lean is ~0.534 and a
            # zero-skill model scores ~0.27 rather than ~0.50.
            #
            # Consumers whose constants assume a COIN-FLIP baseline must not read that number.
            # `CASCADE_MIN_ACCURACY = 0.62` and `bias_strength = (recent_accuracy - 0.5) * 0.6`
            # both take 0.5 as no-skill explicitly; so does the `< 0.45` retrain trigger.
            # Feeding them the all-rows rate would make the cascade permanently inert and the
            # retrain trigger permanently on - a bound derived from one quantity enforced
            # against another, which is defect 5.21.
            #
            # `lean_decisive_*` is the rate over rows the contract actually decided. Its
            # no-skill point IS 0.5, so those constants keep meaning what they were chosen to
            # mean.
            _decisive = [(v, ok) for v, ok in lean_rows
                         if v.get("actual_direction") in ("UP", "DOWN")]
            lean_decisive_total = len(_decisive)
            lean_decisive_hits = sum(1 for _, ok in _decisive if ok)
            lean_up = [(v, ok) for v, ok in lean_rows if v.get("raw_direction") == "UP"]
            lean_down = [(v, ok) for v, ok in lean_rows if v.get("raw_direction") == "DOWN"]

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
            
            # EXPECTANCY, from ENDPOINT economics (the 5.5/5.6/5.7 family - this consumer
            # was named in that work and not converted with the others).
            #
            # It averaged |actual_move_usd|, and under first touch `actual_move_usd` is
            # `resolution_price - entry` where `resolution_price` is the BARRIER. That
            # distance is entry * threshold - IDENTICAL on every touching row - so the whole
            # statistic collapsed to
            #
            #     expectancy_usd = barrier_distance * (2 * accuracy - 1)
            #
            # a linear rescaling of accuracy wearing a dollar sign, displayed as "historical
            # EV". On timeout rows the same expression instead used the closing residual, so
            # it was not even one thing consistently.
            #
            # The real per-trade return is the ENDPOINT move signed by the side actually
            # served, which `endpoint_move_usd` carries for exactly this purpose.
            def _signed_endpoint_pnl(v):
                # ECONOMIC ADMISSIBILITY. `endpoint_move_usd` exists on BARRIER_FALLBACK rows
                # too: when no real horizon-end observation was available, the first-touch
                # BARRIER price is substituted and the move computed from it. The row is
                # correctly LABELLED `endpoint_price_basis = "BARRIER_FALLBACK"` - and this
                # consumer never read the label, so a classification barrier entered the
                # economic expectancy as if it were a realised endpoint return.
                #
                # That is the safeguard-exists-but-nobody-consults-it pattern. `meta_model`
                # already filters `AND endpoint_price_basis = 'ENDPOINT'`; this path did not.
                #
                # The row stays perfectly good CLASSIFICATION evidence. It simply carries no
                # economic outcome, and returning None keeps it out of expectancy_n rather
                # than contributing a barrier distance dressed as PnL.
                if str(v.get("endpoint_price_basis") or "") != "ENDPOINT":
                    return None
                mv = v.get("endpoint_move_usd")
                if mv is None:
                    return None
                d = v.get("direction")
                if d == "UP":
                    return float(mv)
                if d == "DOWN":
                    return -float(mv)
                return None

            _pnls = [x for x in (_signed_endpoint_pnl(v) for v in dir_preds) if x is not None]
            if _pnls:
                expectancy_usd = round(sum(_pnls) / len(_pnls), 2)
                expectancy_basis = "ENDPOINT_SIGNED_PNL"
                expectancy_n = len(_pnls)
            else:
                # No row carries an endpoint observation - legacy rows, or a contract that
                # never produced one. There is no expected value to report, and reporting the
                # barrier-derived number would be reporting accuracy in dollars again.
                expectancy_usd = None
                expectancy_basis = "UNAVAILABLE_NO_ENDPOINT_ROWS"
                expectancy_n = 0
            
            self.accuracy_cache[h] = {
                "accuracy": acc,
                "total": total,
                "hits": hits,
                "misses": total - hits,
                "expectancy_usd": expectancy_usd,
                #: WHICH quantity the number above is, and over how many rows. A consumer
                #: that cannot tell "negative EV" from "no EV was measurable" will treat the
                #: second as the first.
                "expectancy_basis": expectancy_basis,
                "expectancy_n": expectancy_n,
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
                "lean_accuracy": round(lean_hits / lean_total, 4) if lean_total else 0,
                "lean_total": lean_total,
                #: Over rows the contract DECIDED. No-skill is 0.5 here; in `lean_accuracy`
                #: it is not. Any threshold chosen against a coin flip belongs on this one.
                "lean_decisive_accuracy": (round(lean_decisive_hits / lean_decisive_total, 4)
                                           if lean_decisive_total else None),
                "lean_decisive_total": lean_decisive_total,
                "lean_up_accuracy": (round(sum(1 for _, ok in lean_up if ok) / len(lean_up), 4)
                                     if lean_up else 0),
                "lean_up_total": len(lean_up),
                "lean_down_accuracy": (round(sum(1 for _, ok in lean_down if ok) / len(lean_down), 4)
                                       if lean_down else 0),
                "lean_down_total": len(lean_down),
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
            
            # Track accuracy over time for auto-learning feedback. lean_* is the
            # clean sign-truth series the trend must be computed on; the legacy
            # blended `accuracy` stays for display/back-compat only.
            self.accuracy_history[h].append({
                "time": int(time.time() * 1000),
                "accuracy": acc,
                "total": total,
                "lean_accuracy": round(lean_hits / lean_total, 4) if lean_total else None,
                "lean_total": lean_total,
                #: Recorded so the TREND can be computed in the same namespace as the 0.45
                #: bar it is paired with. A trend measured on one metric and compared against
                #: a threshold chosen for another is the same mistake one level up.
                "lean_decisive_accuracy": (round(lean_decisive_hits / lean_decisive_total, 4)
                                           if lean_decisive_total else None),
                "lean_decisive_total": lean_decisive_total,
            })
            # Keep last 100 accuracy snapshots
            if len(self.accuracy_history[h]) > 100:
                self.accuracy_history[h] = self.accuracy_history[h][-100:]

    def get_regime_model_weights(
        self,
        horizon: int,
        regime: str,
        min_samples: int = 20,
        forward_status: dict | None = None,
    ) -> dict:
        """
        Learned regime-specific model weights from live per-model accuracy in this
        regime. Returns weights keyed by the ensemble's model names (xgboost,
        lightgbm, rf, lr), normalized to sum to 1; or {} if there isn't enough data
        yet (caller then keeps its default/heuristic weights).
        """
        if os.environ.get("BTC_EVIDENCE_MODE", "0") == "1":
            try:
                from forward_evidence_gate import may_adapt

                allowed, _ = may_adapt("regime_weight_update", forward_status)
            except Exception:
                return {}
            if not allowed:
                return {}
        stats = self.regime_model_stats.get(int(horizon), {}).get(regime, {})
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
        Returns {horizon: {regime: factor in [0.6, 1.4]}}, plus a "_pooled" key holding the
        old cross-horizon shape for any consumer that has not been updated.

        5.10. THIS POOLED 5m AND 15m INTO ONE FACTOR PER REGIME.

        The outer `for h in ALL_HORIZONS` accumulated both horizons into the same regime bucket,
        so five-minute outcomes recalibrated fifteen-minute confidence and vice versa. That is
        not a small mixing: 5m resolves three times as often, so its rows dominate the bucket,
        and the factor a 15m prediction is multiplied by is mostly a statement about 5m.

        Confidence "means something different in a trend vs chop" - the docstring's own
        argument - and it means something different at 5m than at 15m for exactly the same
        reason. The fix is to stop averaging across the axis the calibration exists to separate.
        """
        def _factor(bucket: dict) -> float:
            n = bucket["n"]
            if n < 10:
                return 1.0
            mean_conf = bucket["conf"] / n
            mean_hit = bucket["hit"] / n
            raw = (mean_hit / mean_conf) if mean_conf > 0 else 1.0
            w = min(1.0, n / float(min_samples))  # shrink toward 1.0 when sparse
            return round(max(0.6, min(1.4, (1.0 * (1 - w)) + (raw * w))), 3)

        per_h: dict = {}
        pooled: dict = {}
        for h in ALL_HORIZONS:
            buckets = {}
            for v in self.verified_by_horizon[h]:
                if v.get("direction") not in ("UP", "DOWN"):
                    continue
                reg = v.get("regime", "UNKNOWN")
                b = buckets.setdefault(reg, {"conf": 0.0, "hit": 0.0, "n": 0})
                b["conf"] += float(v.get("confidence", 0.0) or 0.0)
                b["hit"] += 1.0 if v.get("hit") else 0.0
                b["n"] += 1
                pb = pooled.setdefault(reg, {"conf": 0.0, "hit": 0.0, "n": 0})
                pb["conf"] += float(v.get("confidence", 0.0) or 0.0)
                pb["hit"] += 1.0 if v.get("hit") else 0.0
                pb["n"] += 1
            per_h[h] = {reg: _factor(b) for reg, b in buckets.items()}
        out = per_h
        out["_pooled"] = {reg: _factor(b) for reg, b in pooled.items()}
        return out

    def refit_confidence_calibrators(
        self,
        min_samples: int = 40,
        forward_status: dict | None = None,
    ):
        """
        Refit per-horizon isotonic calibrators mapping the model's raw confidence to
        the realized hit rate. Isotonic enforces monotonicity (higher confidence ⇒
        not-lower hit rate), which directly repairs the live inversion where a 0.5
        confidence was hitting *less* than a 0.4. Cheap; call on a cadence.

        TWO DEFECTS THIS NOW AVOIDS

        1. RECURSION. The docstring said "raw confidence" and the code fitted on
           `prediction["confidence"]` - a value that had ALREADY been through regime
           calibration and this very isotonic map. Each refit therefore calibrated its own
           previous output and fed the result back in. `confidence_raw` is the immutable
           pre-calibration score, and `model.py` now predicts from `conf_raw` so the apply end
           matches the fit end; feeding it post-calibration `conf` would reintroduce the loop
           at the other side.

        2. SELECTION BIAS. It trained only on rows whose FINAL direction stayed UP/DOWN -
           i.e. rows that survived the server gates - and the resulting map was then applied
           BEFORE those gates to every future prediction. That estimates
           P(correct | survived the gates, score) and uses it as P(correct | score).

        CONSEQUENCE, STATED: rows recorded before `confidence_raw` existed are skipped, so the
        calibrator is UNAVAILABLE until enough new rows accumulate. Raw score cannot be
        recovered retroactively, and an unavailable calibrator is the honest state - the model
        shrinks toward its own confidence when none is supplied. Logged below rather than left
        to look like a silently idle component.
        """
        if os.environ.get("BTC_EVIDENCE_MODE", "0") == "1":
            try:
                from forward_evidence_gate import may_adapt

                allowed, _ = may_adapt("confidence_recalibration", forward_status)
            except Exception:
                return False
            if not allowed:
                return False
        try:
            from sklearn.isotonic import IsotonicRegression
        except Exception:
            return False
        for h in ALL_HORIZONS:
            confs, hits = [], []
            for v in self.verified_by_horizon[h]:
                # SELECTION. Every scoreable RAW lean, not only the ones that survived the
                # server gates. Filtering on the FINAL `direction` estimated
                # P(correct | survived the gates, score) and then applied it BEFORE those
                # gates to future predictions - a different quantity wearing the same name.
                raw_dir = v.get("raw_direction") or v.get("model_raw_direction")
                actual = v.get("actual_direction")
                if raw_dir not in ("UP", "DOWN") or actual not in ("UP", "DOWN", "NEUTRAL"):
                    continue
                # SCORE. The pre-calibration value. Fitting on `confidence` calibrated this
                # map's own previous output and fed the result back in on the next refit.
                if not v.get("confidence_raw_available"):
                    continue
                # TARGET. Whether the RAW lean was right - not `hit`, which is dual-semantic
                # and credits correct abstentions the raw score never proposed.
                confs.append(float(v.get("confidence_raw", 0.0) or 0.0))
                hits.append(1.0 if raw_dir == actual else 0.0)
            n = len(confs)
            if n < min_samples or len(set(hits)) < 2:
                # Visible, and rate-limited so it does not spam a healthy cadence.
                _seen = len(self.verified_by_horizon[h])
                if _seen and _seen % 50 == 0:
                    logger.info(
                        "[CALIB] %sm confidence calibrator UNAVAILABLE: %s/%s rows carry a raw "
                        "score (of %s verified). Rows recorded before confidence_raw existed "
                        "cannot be used; it accumulates from here.",
                        h, n, min_samples, _seen)
                continue
            try:
                iso = IsotonicRegression(out_of_bounds="clip", y_min=0.05, y_max=0.95)
                iso.fit(confs, hits)
                self.conf_calibrators[h] = {"iso": iso, "n": n}
            except Exception:
                continue
        return True

    def get_confidence_calibrators(self) -> dict:
        """Per-horizon {iso, n} calibrators for the model to apply at inference."""
        return self.conf_calibrators

    def get_regime_model_accuracy(self) -> dict:
        """Per-horizon, per-regime, per-model live accuracy for monitoring/UI."""
        out = {}
        for horizon, regimes in self.regime_model_stats.items():
            out[int(horizon)] = {}
            for regime, models in regimes.items():
                out[int(horizon)][regime] = {
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
        forward_status: dict | None = None,
    ) -> dict:
        """
        Learn an adaptive confidence threshold from resolved raw UP/DOWN leans.

        This is intentionally based on raw_direction, not only executed direction,
        so the system can discover when a currently skipped class of signals would
        have worked. The policy is precision-first but keeps an action-rate term so
        it does not become "accurate" by trading only one sample.
        """
        if os.environ.get("BTC_EVIDENCE_MODE", "0") == "1":
            try:
                from forward_evidence_gate import may_adapt

                allowed, reason = may_adapt("threshold_adaptation", forward_status)
            except Exception as exc:
                allowed, reason = False, f"forward gate unavailable: {exc}"
            if not allowed:
                return {
                    "regime": regime,
                    "by_horizon": {},
                    "by_regime": {},
                    "ready": False,
                    "message": reason,
                }

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
            "lean_hit": v.get("lean_hit"),   # pure lean sign-truth (betting correctness)
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
            # AUTO-LEARNING runs on LEAN SIGN-TRUTH, not the blended `hit` accuracy.
            # The blended metric counts avoid_success as a hit, so a cautious gate in
            # chop scored >0.6 while the leans were WRONG — auto-learning then LOWERED
            # the confidence threshold (more trades on a coin-flip model). Exactly
            # backwards. (8th consumer of the dual-semantic hit bug — see §5z/§5ai.)
            if not acc or acc.get("lean_total", 0) < 5:
                continue

            # Trend on the clean lean series (legacy history rows fall back to blended).
            history = self.accuracy_history.get(h, [])
            trend = "stable"
            # The DECISIVE series, because `needs_retrain` below compares this trend against
            # 0.45 - a coin-flip bar. Older snapshots that predate the field fall back, and a
            # snapshot with neither is skipped rather than contributing a number from a
            # different scale to an average.
            _hv = [x.get("lean_decisive_accuracy")
                   if x.get("lean_decisive_accuracy") is not None
                   else (x.get("lean_accuracy") if x.get("lean_accuracy") is not None
                         else x.get("accuracy"))
                   for x in history]
            _hv = [v for v in _hv if isinstance(v, (int, float))]
            if len(_hv) >= 3:
                avg_recent = sum(_hv[-3:]) / 3
                older = _hv[:max(1, len(_hv) - 3)]
                avg_older = sum(older) / len(older)
                if avg_recent > avg_older + 0.03:
                    trend = "improving"
                elif avg_recent < avg_older - 0.03:
                    trend = "degrading"

            feedback[h] = {
                "accuracy": acc["lean_accuracy"],
                "trend": trend,
                "up_accuracy": acc.get("lean_up_accuracy", 0),
                "down_accuracy": acc.get("lean_down_accuracy", 0),
                "miss_rate": acc.get("miss_rate", 0),
                "price_match_rate": acc.get("price_match_rate", 0),
                "avg_move_error_usd": acc.get("avg_move_error_usd", 0),
                "total": acc["lean_total"],
                # 0.45 is a bar chosen against a COIN FLIP, so it reads the decisive rate.
                # `lean_accuracy` counts NEUTRAL contract outcomes as misses and sits near
                # 0.27 for a zero-skill model, which would have left this trigger latched on
                # permanently and made "needs_retrain" mean nothing.
                "needs_retrain": (
                    trend == "degrading"
                    and (acc.get("lean_decisive_accuracy") is not None)
                    and acc["lean_decisive_accuracy"] < 0.45
                ) or (
                    acc.get("lean_total", 0) >= 10
                    and acc.get("price_match_rate", 1) < 0.35
                    and acc.get("avg_move_error_usd", 0) > 75
                ),
            }

        return feedback
