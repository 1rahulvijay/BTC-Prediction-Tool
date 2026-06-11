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

logger = logging.getLogger(__name__)

# class index -> direction label (matches model._model_directions / training labels)
_CLASS_DIR = {0: "DOWN", 1: "NEUTRAL", 2: "UP"}
MODELS = ["xgb", "lgb", "cat", "histgb", "dl", "lr", "sgd"]


class PerModelVerifier:
    def __init__(self, horizons=(1, 3, 5, 7, 10, 15), neutral_band=0.0008):  # match main verifier (cost floor)
        self.horizons = list(horizons)
        self.neutral_band = neutral_band
        self.pending: list[dict] = []
        # history[model][horizon] -> deque of 1/0
        self.history = defaultdict(lambda: {h: deque(maxlen=500) for h in self.horizons})
        self.last_vote = defaultdict(dict)  # model -> {horizon: direction}

    def _direction(self, price: float, ref_price: float) -> str:
        if ref_price <= 0:
            return "NEUTRAL"
        chg = (price - ref_price) / ref_price
        if chg > self.neutral_band:
            return "UP"
        if chg < -self.neutral_band:
            return "DOWN"
        return "NEUTRAL"

    def record(self, model_dirs: dict, horizon: int, ref_price: float, now_ms: int):
        """Record each base model's directional vote for this horizon."""
        if not model_dirs or ref_price <= 0:
            return
        for name, cls in model_dirs.items():
            try:
                direction = _CLASS_DIR.get(int(cls), "NEUTRAL")
            except Exception:
                continue
            pid = f"{name}_{horizon}m_{now_ms}"
            entry = {
                "id": pid, "model": name, "horizon": horizon, "ref_price": ref_price,
                "direction": direction, "verify_at": now_ms + horizon * 60_000, "ts": now_ms,
            }
            self.pending.append(entry)
            self.last_vote[name][horizon] = direction
            try:
                database.log_model_prediction(pid, name, now_ms, horizon, ref_price,
                                              direction, now_ms + horizon * 60_000)
            except Exception as e:
                logger.debug(f"Model vote log failed: {e}")

    def check(self, current_price: float, now_ms: int):
        """Resolve any per-model votes whose horizon has elapsed."""
        still = []
        for p in self.pending:
            if now_ms >= p["verify_at"]:
                actual_dir = self._direction(current_price, p["ref_price"])
                hit = (p["direction"] == actual_dir)
                self.history[p["model"]][p["horizon"]].append(1 if hit else 0)
                try:
                    database.resolve_model_prediction(p["id"], current_price, actual_dir, hit)
                except Exception as e:
                    logger.debug(f"Model vote resolve failed: {e}")
            else:
                still.append(p)
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
