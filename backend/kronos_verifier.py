"""
Kronos Directional Verifier
============================
Kronos produces a future candlestick *path*. This records the direction that path
implies at each horizon (forecast price vs current price), then checks — once the
horizon elapses — whether price actually moved that way. Results are stored in DuckDB
(`kronos_predictions`) so Kronos can be compared head-to-head with our ensemble.
"""

import logging
from collections import deque

import database

logger = logging.getLogger(__name__)


class KronosDirectionVerifier:
    def __init__(self, horizons=(5, 15), neutral_band=0.0008):  # match main verifier (cost floor)
        self.horizons = list(horizons)
        self.neutral_band = neutral_band  # ~0.08% flat band — same as ensemble verifier
        self.pending: list[dict] = []
        self.history = {h: deque(maxlen=500) for h in self.horizons}
        self.errors = {h: deque(maxlen=500) for h in self.horizons}  # |forecast - actual| USD
        self.last_forecast = {}  # latest direction/price per horizon for the UI
        self._last_record_ms = 0

    def _direction(self, future_price: float, ref_price: float) -> str:
        if ref_price <= 0:
            return "NEUTRAL"
        chg = (future_price - ref_price) / ref_price
        if chg > self.neutral_band:
            return "UP"
        if chg < -self.neutral_band:
            return "DOWN"
        return "NEUTRAL"

    def record(self, forecast: list, ref_price: float, now_ms: int):
        """Record Kronos's directional view at each horizon (≈ once per minute)."""
        if not forecast or ref_price <= 0:
            return
        if now_ms - self._last_record_ms < 55_000:
            return
        self._last_record_ms = now_ms
        for h in self.horizons:
            if len(forecast) < h:
                continue
            fc = forecast[h - 1]
            fc_price = float(fc.get("close", ref_price)) if isinstance(fc, dict) else float(fc)
            direction = self._direction(fc_price, ref_price)
            pid = f"kronos_{h}m_{now_ms}"
            entry = {
                "id": pid, "horizon": h, "ref_price": ref_price,
                "forecast_price": round(fc_price, 2), "direction": direction,
                "verify_at": now_ms + h * 60_000, "ts": now_ms,
            }
            self.pending.append(entry)
            self.last_forecast[h] = {
                "direction": direction, "forecast_price": round(fc_price, 2),
                "ref_price": round(ref_price, 2),
                "expected_move": round(fc_price - ref_price, 2),
            }
            try:
                database.log_kronos_prediction(pid, now_ms, h, ref_price, fc_price,
                                               direction, now_ms + h * 60_000)
            except Exception as e:
                logger.debug(f"Kronos log failed: {e}")

    def check(self, current_price: float, now_ms: int):
        """Resolve any Kronos predictions whose horizon has elapsed."""
        still = []
        for p in self.pending:
            if now_ms >= p["verify_at"]:
                actual_dir = self._direction(current_price, p["ref_price"])
                hit = (p["direction"] == actual_dir)
                self.history[p["horizon"]].append(1 if hit else 0)
                self.errors[p["horizon"]].append(abs(p["forecast_price"] - current_price))
                try:
                    database.resolve_kronos_prediction(p["id"], current_price, actual_dir, hit)
                except Exception as e:
                    logger.debug(f"Kronos resolve failed: {e}")
            else:
                still.append(p)
        self.pending = still

    def accuracy(self) -> dict:
        out = {}
        for h in self.horizons:
            hh = list(self.history[h])
            ee = list(self.errors[h])
            n = len(hh)
            out[h] = {
                "total": n,
                "hits": int(sum(hh)),
                "accuracy": round(sum(hh) / n, 4) if n else 0.0,
                "avg_error_usd": round(sum(ee) / len(ee), 1) if ee else 0.0,
                "pending": sum(1 for p in self.pending if p["horizon"] == h),
                "latest": self.last_forecast.get(h),
            }
        return out
