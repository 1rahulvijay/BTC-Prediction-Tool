"""Fixed registry: two continuation strategies, one mean-reversion, one zero-information control.

`random_control` is not decoration. Every apparent edge in this repository's research died on
contact with a matched control, and the paper lane previously had none - trend_following and
breakout reported P&L against nothing. A strategy that does not beat random_control over the
same period, with the same notional and holding period, has established nothing.
"""
from __future__ import annotations

from dataclasses import fields
import json
import os
from typing import Any

from .config import StrategyRiskConfig
from .strategies import (
    BreakoutStrategy,
    MeanReversionStrategy,
    ModelConsensusStrategy,
    RandomControlStrategy,
    TrendFollowingStrategy,
)
from .strategy_base import canonical_hash

# The benchmark every other strategy is read against. Named here so tooling can find it without
# hardcoding the string in several places.
CONTROL_STRATEGY_ID = "random_control"


class StrategyRegistry:
    def __init__(self):
        self._strategies = {
            "trend_following": TrendFollowingStrategy(),
            "breakout": BreakoutStrategy(),
            "mean_reversion": MeanReversionStrategy(),
            "model_consensus": ModelConsensusStrategy(),
            CONTROL_STRATEGY_ID: RandomControlStrategy(),
        }
        competition_only = os.getenv("BTC_BINANCE_COMPETITION_ONLY", "0") == "1"
        self._enabled = {
            strategy_id: (
                strategy_id in {"model_consensus", CONTROL_STRATEGY_ID}
                if competition_only else True
            )
            for strategy_id in self._strategies
        }

    def all(self):
        return tuple(self._strategies.values())

    def get(self, strategy_id: str):
        try:
            return self._strategies[strategy_id]
        except KeyError as exc:
            raise KeyError(f"unknown strategy: {strategy_id}") from exc

    def is_enabled(self, strategy_id: str) -> bool:
        return bool(self._enabled.get(strategy_id, False))

    def persist_defaults(self, persistence, starting_cash_usd: float) -> None:
        """Persist each strategy's defaults, with the DOLLAR limits bound to THIS bankroll.

        The dataclass defaults are fractions of `DEFAULT_STARTING_CASH_USD`, a constant. This
        is the one place the ACTUAL starting capital is known, so it is where the derivation
        belongs - otherwise a $10,000 engine runs limits sized for $250 (a 0.25% position
        cap), which is the same "a limit that does not scale with the capital it protects"
        defect that the absolute-dollar version had, pointing the other way.

        Only the four capital-derived dollar fields are rebound. Everything a strategy set
        deliberately - leverage, side permissions, cooldown, trade rate - is preserved, so
        this cannot silently undo a strategy's own risk intent.
        """
        for strategy in self.all():
            values = strategy.risk.to_dict()
            scaled = StrategyRiskConfig.for_capital(starting_cash_usd)
            for field_name in ("max_position_notional_usd", "max_account_exposure_usd",
                               "maximum_daily_loss_usd", "maximum_weekly_loss_usd"):
                values[field_name] = getattr(scaled, field_name)
            strategy.risk = StrategyRiskConfig(**values).clamped()
            config = {"risk": strategy.risk.to_dict()}
            persistence.ensure_strategy(
                strategy.strategy_id,
                strategy.strategy_name,
                strategy.strategy_version,
                self._enabled[strategy.strategy_id],
                json.dumps(config, sort_keys=True),
                canonical_hash(config),
                starting_cash_usd,
            )

    def load(self, persistence) -> None:
        valid_risk_keys = {item.name for item in fields(StrategyRiskConfig)}
        for row in persistence.strategy_configs():
            strategy = self.get(row["strategy_id"])
            payload = json.loads(row["config_json"])
            risk_values = {
                key: value
                for key, value in (payload.get("risk") or {}).items()
                if key in valid_risk_keys
            }
            strategy.risk = StrategyRiskConfig(**risk_values).clamped()
            self._enabled[strategy.strategy_id] = bool(row["enabled"])

    def update(
        self,
        persistence,
        strategy_id: str,
        *,
        enabled: bool | None = None,
        risk_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        strategy = self.get(strategy_id)
        values = strategy.risk.to_dict()
        allowed = set(values)
        for key, value in (risk_patch or {}).items():
            if key not in allowed:
                raise ValueError(f"unsupported risk setting: {key}")
            values[key] = value
        strategy.risk = StrategyRiskConfig(**values).clamped()
        if enabled is not None:
            self._enabled[strategy_id] = bool(enabled)
        config = {"risk": strategy.risk.to_dict()}
        persistence.update_strategy_config(
            strategy_id,
            self._enabled[strategy_id],
            json.dumps(config, sort_keys=True),
            canonical_hash(config),
        )
        return self.describe(strategy_id)

    def describe(self, strategy_id: str) -> dict[str, Any]:
        strategy = self.get(strategy_id)
        return {
            "strategy_id": strategy.strategy_id,
            "name": strategy.strategy_name,
            "version": strategy.strategy_version,
            "enabled": self.is_enabled(strategy_id),
            "timeframe": strategy.timeframe,
            "required_inputs": list(strategy.required_inputs),
            "risk": strategy.risk.to_dict(),
            "config_hash": strategy.config_hash,
        }

    def descriptions(self) -> list[dict[str, Any]]:
        return [self.describe(strategy.strategy_id) for strategy in self.all()]
