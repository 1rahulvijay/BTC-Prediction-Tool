"""
FSR-PPO inspired challenger policy for BTC decision support.

This module adapts the paper idea to the existing app without taking over the
live ensemble:

- Financial Signal Representation (FSR): denoise recent BTC candles, estimate
  noise, persistence and clean trend pressure.
- PPO-style policy surface: action includes direction and size, and the reward
  model penalizes costs, overtrading and noisy states.

Until a trained PPO checkpoint exists, the policy runs as a deterministic
warm-start challenger. That makes it auditable in DuckDB before it is allowed to
influence the production ensemble.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np


HORIZONS = (1, 3, 5, 7, 10, 15, 30)


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
        return out if math.isfinite(out) else default
    except Exception:
        return default


def _ema(values: np.ndarray, span: int) -> np.ndarray:
    if values.size == 0:
        return values
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(values, dtype=np.float64)
    out[0] = values[0]
    for i in range(1, len(values)):
        out[i] = alpha * values[i] + (1.0 - alpha) * out[i - 1]
    return out


def _hurst_rs(values: np.ndarray) -> float:
    """Small modified rescaled-range proxy. Returns 0.5 for insufficient data."""
    if values.size < 32:
        return 0.5
    x = values.astype(np.float64)
    x = x - np.mean(x)
    y = np.cumsum(x)
    r = float(np.max(y) - np.min(y))
    s = float(np.std(x) + 1e-12)
    rs = max(r / s, 1e-9)
    return float(np.clip(np.log(rs) / np.log(len(x)), 0.0, 1.0))


@dataclass
class FSRPPOStrategy:
    """
    Lightweight PPO-ready policy layer.

    The active policy is intentionally conservative. It uses the same concepts
    as the paper - denoised signal state, flexible action sizing, and a reward
    that discourages overtrading - but it stays a challenger until live evidence
    proves it adds value.
    """

    policy_path: str = field(
        default_factory=lambda: os.path.join(
            os.path.dirname(__file__), "saved_models", "fsr_ppo_policy.json"
        )
    )
    min_confidence: float = 0.50
    min_expected_reward: float = 0.0
    cost_bps: float = 5.0
    last_actions: Dict[int, str] = field(default_factory=dict)
    last_action_ts: Dict[int, float] = field(default_factory=dict)
    policy_weights: Dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._load_policy()

    def _load_policy(self) -> None:
        """Load optional trained policy weights if a future PPO trainer saves them."""
        try:
            if os.path.exists(self.policy_path):
                with open(self.policy_path, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                if isinstance(payload, dict):
                    self.policy_weights = payload.get("weights") or {}
        except Exception:
            self.policy_weights = {}

    @property
    def mode(self) -> str:
        return "trained_policy" if self.policy_weights else "warm_start_challenger"

    def status(self) -> dict:
        return {
            "enabled": True,
            "mode": self.mode,
            "policy_path": self.policy_path,
            "trained": bool(self.policy_weights),
            "paper_adaptation": "FSR denoising + PPO-style action sizing/reward",
        }

    def signal_representation(self, klines: List[dict]) -> dict:
        closes = np.asarray([_safe_float(k.get("close")) for k in klines[-180:]], dtype=np.float64)
        volumes = np.asarray([_safe_float(k.get("volume")) for k in klines[-180:]], dtype=np.float64)
        if closes.size < 32 or closes[-1] <= 0:
            return {
                "ready": False,
                "denoised_price": float(closes[-1]) if closes.size else 0.0,
                "noise_ratio": 1.0,
                "clean_momentum": 0.0,
                "trend_strength": 0.0,
                "hurst": 0.5,
                "persistence": 0.0,
                "volume_pressure": 0.0,
                "signal_quality": 0.0,
            }

        fast = _ema(closes, 9)
        mid = _ema(closes, 21)
        slow = _ema(closes, 55)
        denoised = 0.50 * fast + 0.32 * mid + 0.18 * slow
        residual = closes - denoised
        returns = np.diff(closes) / np.maximum(closes[:-1], 1e-9)

        price_scale = max(float(np.std(np.diff(closes))), 1e-9)
        noise_ratio = float(np.clip(np.std(residual[-60:]) / price_scale, 0.0, 3.0) / 3.0)
        clean_momentum = float(np.clip((denoised[-1] - denoised[-10]) / closes[-1] * 1200.0, -1.0, 1.0))
        trend_strength = float(np.clip(abs(denoised[-1] - denoised[-30]) / closes[-1] * 900.0, 0.0, 1.0))
        hurst = _hurst_rs(returns[-96:])
        persistence = float(np.clip((hurst - 0.5) * 2.0, -1.0, 1.0))

        if volumes.size >= 30:
            v_now = float(np.mean(volumes[-5:]))
            v_base = float(np.mean(volumes[-30:]) + 1e-9)
            volume_pressure = float(np.clip((v_now / v_base - 1.0) / 2.0, -1.0, 1.0))
        else:
            volume_pressure = 0.0

        signal_quality = float(
            np.clip(
                0.45 * (1.0 - noise_ratio)
                + 0.30 * trend_strength
                + 0.15 * max(persistence, 0.0)
                + 0.10 * abs(volume_pressure),
                0.0,
                1.0,
            )
        )

        return {
            "ready": True,
            "denoised_price": float(denoised[-1]),
            "noise_ratio": round(noise_ratio, 4),
            "clean_momentum": round(clean_momentum, 4),
            "trend_strength": round(trend_strength, 4),
            "hurst": round(hurst, 4),
            "persistence": round(persistence, 4),
            "volume_pressure": round(volume_pressure, 4),
            "signal_quality": round(signal_quality, 4),
        }

    def _state_vector(self, prediction: dict, fsr: dict, data_state: dict, accuracy_summary: dict) -> dict:
        h = int(prediction.get("horizon") or 0)
        acc = accuracy_summary.get(h) or accuracy_summary.get(str(h)) or {}
        order_flow = data_state.get("order_flow") or {}
        derivatives = data_state.get("derivatives") or {}
        regime = data_state.get("regime_info") or {}
        return {
            "horizon": h,
            "prob_up": _safe_float(prediction.get("probUp")),
            "prob_down": _safe_float(prediction.get("probDown")),
            "confidence": _safe_float(prediction.get("confidence")),
            "agreement": _safe_float(prediction.get("agreement")),
            "expectancy_usd": _safe_float(prediction.get("expectancy_usd")),
            "expected_move_usd": abs(_safe_float(prediction.get("expectedMove"))),
            "noise_ratio": _safe_float(fsr.get("noise_ratio"), 1.0),
            "clean_momentum": _safe_float(fsr.get("clean_momentum")),
            "trend_strength": _safe_float(fsr.get("trend_strength")),
            "signal_quality": _safe_float(fsr.get("signal_quality")),
            "hurst": _safe_float(fsr.get("hurst"), 0.5),
            "order_book_imbalance": _safe_float(order_flow.get("imbalance")),
            "spread_expansion": _safe_float(order_flow.get("spread_expansion_ratio"), 1.0),
            "coinbase_premium": _safe_float(derivatives.get("coinbase_premium")),
            "regime_confidence": _safe_float(regime.get("confidence"), 0.0),
            "live_accuracy": _safe_float(acc.get("accuracy"), 0.0),
            "live_samples": _safe_float(acc.get("total"), 0.0),
        }

    def _alignment(self, direction: str, state: dict) -> float:
        side = 1.0 if direction == "UP" else -1.0 if direction == "DOWN" else 0.0
        if side == 0.0:
            return 0.0
        clean = side * state["clean_momentum"]
        obi = side * state["order_book_imbalance"]
        premium = side * np.clip(state["coinbase_premium"] / 20.0, -1.0, 1.0)
        return float(np.clip(0.55 * clean + 0.25 * obi + 0.20 * premium, -1.0, 1.0))

    def _overtrade_penalty(self, horizon: int, action: str, now: float) -> float:
        last = self.last_actions.get(horizon, "AVOID")
        last_ts = self.last_action_ts.get(horizon, 0.0)
        if action == "AVOID":
            return 0.0
        if last == action and now - last_ts < horizon * 60:
            return 0.02
        if last not in ("", "AVOID", action) and now - last_ts < horizon * 120:
            return 0.08
        return 0.0

    def _choose_action(self, prediction: dict, state: dict, price: float, now: float) -> dict:
        direction = prediction.get("direction") or prediction.get("signal") or "NEUTRAL"
        horizon = int(prediction.get("horizon") or 0)
        confidence = state["confidence"]
        expected_move = max(state["expected_move_usd"], 0.0)
        cost_usd = price * (self.cost_bps / 10000.0)
        alignment = self._alignment(direction, state)
        signal_quality = state["signal_quality"]
        noise = state["noise_ratio"]
        spread_penalty = max(0.0, state["spread_expansion"] - 1.0) * 0.04

        edge = max(0.0, (2.0 * confidence - 1.0))
        quality_edge = edge * max(0.0, alignment) * max(0.15, signal_quality)
        raw_reward = expected_move * quality_edge - cost_usd

        if direction not in ("UP", "DOWN"):
            action = "AVOID"
            size = 0.0
            reason = "The ensemble is neutral, so the PPO challenger preserves capital."
        elif confidence < self.min_confidence:
            action = "AVOID"
            size = 0.0
            reason = "Confidence is below the PPO challenger minimum."
        elif noise > 0.72 and signal_quality < 0.45:
            action = "AVOID"
            size = 0.0
            reason = "Financial signal representation says the move is too noisy."
        elif alignment < 0.05:
            action = "AVOID"
            size = 0.0
            reason = "Clean trend/order-flow alignment does not support the model side."
        elif raw_reward <= self.min_expected_reward:
            action = "AVOID"
            size = 0.0
            reason = "Expected reward after cost is not positive."
        else:
            side = "BUY" if direction == "UP" else "SELL"
            size_score = np.clip(0.40 * confidence + 0.25 * signal_quality + 0.20 * alignment + 0.15 * state["agreement"], 0.0, 1.0)
            if size_score >= 0.72 and raw_reward > cost_usd * 1.5:
                suffix = "MEDIUM"
                size = 0.50
            else:
                suffix = "SMALL"
                size = 0.25
            action = f"{side}_{suffix}"
            reason = "Model edge, denoised trend and expected reward are aligned."

        penalty = self._overtrade_penalty(horizon, action, now)
        expected_reward = raw_reward * max(size, 0.0) - penalty * cost_usd - spread_penalty * cost_usd
        if action == "AVOID":
            expected_reward = 0.0

        side = "BUY" if action.startswith("BUY") else "SELL" if action.startswith("SELL") else "AVOID"
        confidence_score = float(np.clip(0.35 * confidence + 0.30 * signal_quality + 0.20 * max(alignment, 0.0) + 0.15 * state["agreement"], 0.0, 1.0))

        # Only COMMITTED actions update the overtrade memory: recording AVOID here
        # overwrote the last real trade, so a BUY→AVOID→SELL flip-flop within the
        # penalty window looked like a fresh trade (last="AVOID" → no penalty) and
        # the anti-churn logic never fired. AVOID also must not refresh the timestamp.
        if action != "AVOID":
            self.last_actions[horizon] = action
            self.last_action_ts[horizon] = now

        return {
            "horizon": horizon,
            "action": action,
            "side": side,
            "size_fraction": round(size, 3),
            "confidence": round(confidence_score, 4),
            "expected_reward_usd": round(float(expected_reward), 4),
            "alignment": round(alignment, 4),
            "reason": reason,
            "risk_note": self._risk_note(noise, state, action),
            "state": state,
        }

    def _risk_note(self, noise: float, state: dict, action: str) -> str:
        if action == "AVOID":
            return "No trade. The challenger is trying to avoid noisy or low-edge conditions."
        if noise > 0.55:
            return "Trade size is capped because the denoised signal still has elevated noise."
        if state["live_samples"] < 100:
            return "Live evidence is young; treat this PPO challenger action as experimental."
        return "Reward estimate is positive after cost and overtrade penalties."

    def recommend(self, data_state: dict, predictions: List[dict], accuracy_summary: Optional[dict] = None) -> dict:
        now = time.time()
        klines = data_state.get("klines") or []
        price = _safe_float((klines[-1] or {}).get("close") if klines else data_state.get("price"))
        fsr = self.signal_representation(klines)
        accuracy_summary = accuracy_summary or {}
        by_horizon = {}
        for p in predictions or []:
            h = int(p.get("horizon") or 0)
            if h not in HORIZONS:
                continue
            state = self._state_vector(p, fsr, data_state, accuracy_summary)
            by_horizon[str(h)] = self._choose_action(p, state, price, now)

        ranked = sorted(
            by_horizon.values(),
            key=lambda r: (r.get("expected_reward_usd", 0.0), r.get("confidence", 0.0)),
            reverse=True,
        )
        best = next((r for r in ranked if r.get("action") != "AVOID"), ranked[0] if ranked else None)
        return {
            "status": self.status(),
            "fsr": fsr,
            "by_horizon": by_horizon,
            "best": best,
            "summary": self._summary(best, fsr),
        }

    def _summary(self, best: Optional[dict], fsr: dict) -> str:
        if not best:
            return "PPO challenger is waiting for model predictions."
        action = best.get("action", "AVOID")
        if action == "AVOID":
            return f"PPO challenger says AVOID: {best.get('reason', 'risk is not worth the trade')}"
        return (
            f"PPO challenger says {action}: denoised signal quality "
            f"{int(_safe_float(fsr.get('signal_quality')) * 100)}%, "
            f"expected reward ${_safe_float(best.get('expected_reward_usd')):.2f}."
        )

