"""
Kronos forecast wrapper.

The wrapper is intentionally lazy-loaded so the app can boot without waiting on
large model imports, downloads or GPU allocation. If Kronos is unavailable, the
chart still receives a stable volatility-based forecast path.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


class KronosPredictorWrapper:
    def __init__(self, max_context: int = 256, max_pred_len: int = 60):
        self.max_context = max_context
        self.max_pred_len = max_pred_len
        self.is_loaded = False
        self.load_attempted = False
        self.model = None
        self.tokenizer = None
        self.predictor = None
        self.device = "cpu"
        self.status_message = "Kronos not loaded yet"
        self._lock = asyncio.Lock()

    async def generate_forecast(self, klines: list[dict], pred_len: int = 60) -> list:
        """
        Generate future candlestick path.

        Returns a list of dicts:
        {"time": ..., "open": ..., "high": ..., "low": ..., "close": ...}
        """
        if not klines or len(klines) < 10:
            return []

        pred_len = max(1, min(int(pred_len), self.max_pred_len))

        async with self._lock:
            if not self.load_attempted:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(None, self._load_sync)

            if self.is_loaded:
                try:
                    loop = asyncio.get_running_loop()
                    return await loop.run_in_executor(
                        None, self._run_kronos_sync, klines[-self.max_context :], pred_len
                    )
                except Exception as e:
                    self.status_message = f"Kronos inference failed; fallback active: {e}"
                    logger.error("Kronos inference error: %s", e)

        return self._generate_fallback_forecast(klines, pred_len)

    def status(self) -> dict:
        return {
            "loaded": self.is_loaded,
            "load_attempted": self.load_attempted,
            "device": self.device,
            "max_context": self.max_context,
            "max_pred_len": self.max_pred_len,
            "message": self.status_message,
        }

    def _load_sync(self) -> None:
        self.load_attempted = True
        try:
            try:
                import torch

                cuda_available = bool(torch.cuda.is_available())
            except Exception:
                torch = None
                cuda_available = False

            from kronos import Kronos, KronosPredictor, KronosTokenizer

            self.device = "cuda" if cuda_available else "cpu"
            self.tokenizer = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer")
            self.model = Kronos.from_pretrained("NeoQuasar/Kronos-small")

            if self.device == "cuda" and torch is not None:
                if hasattr(self.model, "half"):
                    self.model = self.model.half()
                if hasattr(self.model, "to"):
                    self.model = self.model.to(self.device)

            self.predictor = KronosPredictor(
                self.model,
                self.tokenizer,
                device=self.device,
                max_context=self.max_context,
            )
            self.is_loaded = True
            self.status_message = f"Kronos loaded on {self.device}"
            logger.info(self.status_message)
        except Exception as e:
            self.is_loaded = False
            self.status_message = f"Kronos unavailable; fallback forecast active: {e}"
            logger.warning(self.status_message)

    def _run_kronos_sync(self, klines: list[dict], pred_len: int) -> list:
        """
        Best-effort adapter around Kronos APIs.

        The local Kronos package can vary by install source, so this accepts
        DataFrame, list-of-dicts and common predict/forecast method names.
        """
        import pandas as pd

        frame = pd.DataFrame(
            [
                {
                    "time": k["time"],
                    "open": float(k["open"]),
                    "high": float(k["high"]),
                    "low": float(k["low"]),
                    "close": float(k["close"]),
                    "volume": float(k.get("volume", 0.0)),
                }
                for k in klines
            ]
        )

        result: Any = None
        for method_name in ("predict", "forecast", "generate"):
            method = getattr(self.predictor, method_name, None)
            if method is None:
                continue
            try:
                result = method(frame, pred_len=pred_len)
                break
            except TypeError:
                result = method(frame, pred_len)
                break

        if result is None:
            raise RuntimeError("Kronos predictor exposes no supported forecast method")

        return self._normalize_forecast_result(result, klines, pred_len)

    def _normalize_forecast_result(self, result: Any, klines: list[dict], pred_len: int) -> list:
        if hasattr(result, "to_dict"):
            rows = result.to_dict("records")
        elif isinstance(result, tuple) and result and hasattr(result[0], "to_dict"):
            rows = result[0].to_dict("records")
        elif isinstance(result, list):
            rows = result
        else:
            raise RuntimeError(f"Unsupported Kronos result type: {type(result).__name__}")

        normalized = []
        last_time = klines[-1]["time"]
        interval = self._infer_interval(klines)
        last_close = float(klines[-1]["close"])
        for i, row in enumerate(rows[:pred_len], start=1):
            open_p = float(row.get("open", last_close))
            close_p = float(row.get("close", open_p))
            high_p = float(row.get("high", max(open_p, close_p)))
            low_p = float(row.get("low", min(open_p, close_p)))
            normalized.append(
                {
                    "time": int(row.get("time", last_time + i * interval)),
                    "open": round(open_p, 2),
                    "high": round(high_p, 2),
                    "low": round(low_p, 2),
                    "close": round(close_p, 2),
                }
            )
            last_close = close_p
        return normalized

    def _generate_fallback_forecast(self, klines: list[dict], pred_len: int) -> list:
        """
        Deterministic volatility path used when Kronos is not installed.

        The seeded noise only changes when the latest candle changes, so the chart
        does not flicker randomly every WebSocket tick.
        """
        last_kline = klines[-1]
        current_time = int(last_kline["time"])
        current_price = float(last_kline["close"])
        interval = self._infer_interval(klines)

        closes = np.array([float(k["close"]) for k in klines[-60:]], dtype=np.float64)
        if len(closes) > 2:
            returns = np.diff(closes)
            volatility = float(np.std(returns))
            drift = float(np.mean(returns[-20:])) if len(returns) >= 20 else float(np.mean(returns))
        else:
            volatility = max(current_price * 0.0005, 1.0)
            drift = 0.0

        volatility = max(volatility, current_price * 0.00015, 1.0)
        seed = int(current_time) ^ int(round(current_price * 100))
        rng = np.random.default_rng(seed)

        forecasts = []
        for i in range(1, pred_len + 1):
            step_noise = rng.normal(0, volatility * 0.35)
            step_drift = np.clip(drift, -volatility * 0.6, volatility * 0.6)
            open_p = current_price
            close_p = current_price + step_drift + step_noise
            wick = abs(rng.normal(0, volatility * 0.18))
            high_p = max(open_p, close_p) + wick
            low_p = min(open_p, close_p) - wick
            forecasts.append(
                {
                    "time": current_time + i * interval,
                    "open": round(open_p, 2),
                    "high": round(high_p, 2),
                    "low": round(low_p, 2),
                    "close": round(close_p, 2),
                }
            )
            current_price = close_p

        return forecasts

    @staticmethod
    def _infer_interval(klines: list[dict]) -> int:
        if len(klines) >= 2:
            interval = int(klines[-1]["time"] - klines[-2]["time"])
            if interval > 0:
                return interval
        return 60


kronos_wrapper = KronosPredictorWrapper()
