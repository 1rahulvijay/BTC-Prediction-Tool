"""
Per-Model Directional Verifier
===============================
Every recorded prediction already carries each base model's argmax vote in
``p["modelDirs"]`` (``{name: class_index}`` where 0=DOWN, 1=NEUTRAL, 2=UP — see
``model._model_directions``). This records each model's vote at prediction time and,
once the horizon elapses, checks whether price actually moved that way — so XGBoost,
LightGBM, CatBoost, etc. can be compared head-to-head on *live* accuracy.

Mirrors ``kronos_verifier.KronosDirectionVerifier`` (record → check → accuracy) and
persists to DuckDB (``model_predictions``).
"""

import logging
from collections import defaultdict, deque

import database
import target_contract as _tc

logger = logging.getLogger(__name__)

# class index -> direction label (matches model._model_directions / training labels)
_CLASS_DIR = {0: "DOWN", 1: "NEUTRAL", 2: "UP"}
MODELS = ["xgb", "lgb", "cat", "histgb", "dl", "lr", "rf"]  # sgd retired in v6


class PerModelVerifier:
    def __init__(self, horizons=(5, 15), neutral_band=0.0008):  # pruned 2026-06-21; match main verifier (cost floor)
        self.horizons = list(horizons)
        self.neutral_band = neutral_band
        self.pending: list[dict] = []
        # history[model][horizon] -> deque of 1/0
        self.history = defaultdict(lambda: {h: deque(maxlen=500) for h in self.horizons})
        self.last_vote = defaultdict(dict)  # model -> {horizon: direction}
        #: Counted, not hidden. A panel that silently stops grading looks identical to one
        #: with nothing to grade, which is how the loop-time rule survived unnoticed.
        self.invalid_late = 0
        self.ungraded = 0

    def _direction(self, price: float, ref_price: float, band: float | None = None) -> str:
        if ref_price <= 0:
            return "NEUTRAL"
        chg = (price - ref_price) / ref_price
        _b = float(band) if band else float(self.neutral_band)
        if chg > _b:
            return "UP"
        if chg < -_b:
            return "DOWN"
        return "NEUTRAL"

    def record(self, model_dirs: dict, horizon: int, ref_price: float, now_ms: int,
               prediction_id: str = "", neutral_band: float | None = None):
        """Record each base model's directional vote for this horizon.

        ``prediction_id`` is the canonical parent prediction id.  Keeping the child id
        identical to ``database.log_prediction`` makes both persistence paths idempotent
        instead of writing a legacy row and a second ``parent::model`` row for one vote.
        """
        if not model_dirs or ref_price <= 0:
            return
        # THE PARENT'S BAND, not this class's 8bps constant (scan-5 item 5.4).
        #
        # `self.neutral_band` is a fixed 0.0008 floor. The parent prediction carries an ADAPTIVE
        # `neutralBand` derived from EWMA ATR - measured up to 0.0029 in a violent regime, 3.6x
        # the floor. Grading every seat vote at the floor while the parent is graded at the real
        # band means the two disagree about the same market, and any seat-complementarity or
        # error-correlation study inherits that disagreement.
        #
        # Recorded per row so a vote can never be re-graded later at a different width.
        band = float(neutral_band) if neutral_band else float(self.neutral_band)
        for name, cls in model_dirs.items():
            try:
                direction = _CLASS_DIR.get(int(cls), "NEUTRAL")
            except Exception:
                continue
            pid = (f"{prediction_id}::{name}" if prediction_id
                   else f"{name}_{horizon}m_{now_ms}")
            entry = {
                "id": pid, "model": name, "horizon": horizon, "ref_price": ref_price,
                "direction": direction, "verify_at": now_ms + horizon * 60_000, "ts": now_ms,
                # The contract is stamped ON the vote, so a row recorded under one target is
                # never graded by whatever the default happens to be when it resolves.
                "target_contract": _tc.TRAINING_CONTRACT,
                # The PARENT's adaptive band, carried on the row so this vote is graded at the
                # same barrier width as the prediction it belongs to - and can never be
                # re-graded later at a different one.
                "neutral_band": band,
            }
            self.pending.append(entry)
            self.last_vote[name][horizon] = direction
            try:
                database.log_model_prediction(pid, name, now_ms, horizon, ref_price,
                                              direction, now_ms + horizon * 60_000)
            except Exception as e:
                logger.debug(f"Model vote log failed: {e}")

    def restore_from_database(self, unresolved_predictions: list[dict],
                              resolved_outcomes: list[dict]) -> dict:
        """Restore durable accuracy and pending votes after a backend restart.

        Resolved history contains committed UP/DOWN votes only.  Pending child votes
        are reconstructed from the canonical parent prediction rows, so they resolve
        with the same ids used by ``database.update_outcome``.
        """
        resolved = 0
        pending = 0
        for row in resolved_outcomes or []:
            try:
                model = str(row["model"])
                horizon = int(row["horizon"])
                if horizon not in self.horizons or model not in MODELS:
                    continue
                self.history[model][horizon].append(1 if bool(row["hit"]) else 0)
                resolved += 1
            except (KeyError, TypeError, ValueError):
                continue

        for parent in unresolved_predictions or []:
            try:
                parent_id = str(parent["id"])
                horizon = int(parent["horizon"])
                ref_price = float(parent["predicted_price"])
                timestamp = int(parent["timestamp"])
                verify_at = int(parent["verify_at"])
                model_dirs = parent.get("model_dirs") or {}
            except (KeyError, TypeError, ValueError):
                continue
            if horizon not in self.horizons or ref_price <= 0 or not parent_id:
                continue
            for name, cls in model_dirs.items():
                if name not in MODELS:
                    continue
                try:
                    direction = _CLASS_DIR.get(int(cls), "NEUTRAL")
                except (TypeError, ValueError):
                    continue
                self.pending.append({
                    "id": f"{parent_id}::{name}",
                    "model": name,
                    "horizon": horizon,
                    "ref_price": ref_price,
                    "direction": direction,
                    "verify_at": verify_at,
                    "ts": timestamp,
                })
                self.last_vote[name][horizon] = direction
                pending += 1
        return {"resolved": resolved, "pending": pending}

    def check(self, current_price: float, now_ms: int, klines=None):
        """Resolve any per-model votes whose horizon has elapsed.

        A base model's argmax is NEUTRAL on the majority of ticks (it ABSTAINS,
        especially at short horizons). NEUTRAL is not a directional call — but over a
        5–15m horizon the market almost always moves, so grading NEUTRAL votes against
        a (near-always) UP/DOWN outcome scored every abstention as a miss and dragged
        the per-model panel to a meaningless ~0–20%. FIX (2026-06-13, sign-truth): grade
        only COMMITTED (UP/DOWN) votes and exclude NEUTRAL from the accuracy denominator.
        NEUTRAL rows are still resolved (hit=NULL) so they don't sit pending forever;
        `latest_vote` still shows the raw argmax incl. NEUTRAL.

        P1-3 (2026-08-05). This used to grade with:

            actual_dir = "UP" if current_price >= p["ref_price"] else "DOWN"

        Three separate defects in one line. `current_price` is the MAIN LOOP's price, not the
        price at the horizon end, so a loop delayed by training or a feed stall graded against
        a moment minutes past the horizon. The rule is an ENDPOINT sign while these models are
        trained on `TRAINING_CONTRACT` (first touch) - the exact mismatch the main verifier was
        fixed to remove, still live one panel over. And with no lateness bound there was no
        moment at which a stale row was refused instead of graded.

        It also had no NEUTRAL outcome and no abstention: `>=` forces every bar to UP or DOWN,
        so a flat bar was always scored as UP, and a model voting DOWN on it always missed.

        Now resolved through `target_contract.grade`, the same function the main verifier uses,
        so the two panels cannot describe one vote with two different random variables.
        `klines` must cover entry..verify_at; without them the vote stays pending rather than
        being graded by a rule these models were not trained on.
        """
        import target_contract as tc

        still = []
        for p in self.pending:
            if now_ms < p["verify_at"]:
                still.append(p)
                continue

            lateness = int(now_ms) - int(p["verify_at"])
            if lateness > tc.MAX_RESOLUTION_LATENESS_MS:
                # Dropped, not graded. Same bound as the main verifier.
                self.invalid_late = getattr(self, "invalid_late", 0) + 1
                continue

            result = tc.grade(
                contract=p.get("target_contract") or tc.TRAINING_CONTRACT,
                entry=float(p["ref_price"]),
                threshold=tc.resolve_neutral_band(p.get("neutral_band"), self.neutral_band),
                klines=klines,
                entry_ts=int(p.get("ts") or 0),
                verify_ts=int(p["verify_at"]),
            )
            if not result.graded:
                # Cannot grade under the declared contract yet. Stay pending rather than
                # emit a label produced by a different rule.
                self.ungraded = getattr(self, "ungraded", 0) + 1
                still.append(p)
                continue

            committed = p["direction"] in ("UP", "DOWN")
            hit = (p["direction"] == result.direction) if committed else None
            if committed:
                self.history[p["model"]][p["horizon"]].append(1 if hit else 0)
            try:
                database.resolve_model_prediction(
                    p["id"], result.resolution_price, result.direction, hit)
            except Exception as e:
                logger.debug(f"Model vote resolve failed: {e}")
        self.pending = still

    def accuracy(self) -> dict:
        """-> {model: {horizon: {total, hits, accuracy, pending, latest_vote}}}."""
        out = {}
        for name in MODELS:
            hist = self.history.get(name)
            row = {}
            for h in self.horizons:
                hh = list(hist[h]) if hist else []
                n = len(hh)
                row[h] = {
                    "total": n,
                    "hits": int(sum(hh)),
                    "accuracy": round(sum(hh) / n, 4) if n else 0.0,
                    "pending": sum(1 for p in self.pending
                                   if p["model"] == name and p["horizon"] == h),
                    "latest_vote": self.last_vote.get(name, {}).get(h),
                }
            out[name] = row
        return out
