"""Conservative, bounded configuration for Binance paper trading."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT / "data" / "binance_paper.duckdb"


def _float_env(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(high, max(low, value))


def _int_env(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(high, max(low, value))


@dataclass(frozen=True)
class EngineConfig:
    hard_enabled: bool
    db_path: Path
    starting_cash_usd: float
    fee_rate_bps: float
    slippage_bps: float
    latency_ms: int
    quote_stale_ms: int
    evaluation_interval_ms: int
    sample_interval_ms: int

    @classmethod
    def from_env(cls) -> "EngineConfig":
        return cls(
            hard_enabled=os.getenv("BTC_ENABLE_BINANCE_PAPER", "0") == "1",
            db_path=Path(
                os.getenv("BTC_BINANCE_PAPER_DB", str(DEFAULT_DB_PATH))
            ).resolve(),
            starting_cash_usd=_float_env(
                "BTC_BINANCE_PAPER_STARTING_CASH", 10_000.0, 100.0, 10_000_000.0
            ),
            fee_rate_bps=_float_env(
                "BTC_BINANCE_PAPER_FEE_BPS", 5.0, 0.0, 100.0
            ),
            slippage_bps=_float_env(
                "BTC_BINANCE_PAPER_SLIPPAGE_BPS", 1.0, 0.0, 100.0
            ),
            latency_ms=_int_env("BTC_BINANCE_PAPER_LATENCY_MS", 500, 0, 10_000),
            quote_stale_ms=_int_env(
                "BTC_BINANCE_PAPER_QUOTE_STALE_MS", 2_000, 100, 60_000
            ),
            evaluation_interval_ms=_int_env(
                "BTC_BINANCE_PAPER_EVAL_MS", 1_000, 250, 60_000
            ),
            sample_interval_ms=_int_env(
                "BTC_BINANCE_PAPER_SAMPLE_MS", 1_000, 250, 60_000
            ),
        )

    def public_dict(self) -> dict:
        value = asdict(self)
        value["db_path"] = str(self.db_path)
        return value


@dataclass(frozen=True)
class StrategyRiskConfig:
    enabled: bool = True
    allow_long: bool = True
    allow_short: bool = True
    leverage: float = 1.0
    max_leverage: float = 2.0
    max_position_notional_usd: float = 1_000.0
    max_account_exposure_usd: float = 1_000.0
    risk_per_trade_fraction: float = 0.005
    maximum_daily_loss_usd: float = 100.0
    maximum_drawdown_fraction: float = 0.10
    maximum_trades_per_hour: int = 4
    cooldown_seconds: int = 60
    maximum_spread_bps: float = 5.0
    minimum_fill_fraction: float = 1.0
    stop_required: bool = True

    def clamped(self) -> "StrategyRiskConfig":
        return StrategyRiskConfig(
            enabled=bool(self.enabled),
            allow_long=bool(self.allow_long),
            allow_short=bool(self.allow_short),
            leverage=min(2.0, max(1.0, float(self.leverage))),
            max_leverage=min(3.0, max(1.0, float(self.max_leverage))),
            max_position_notional_usd=min(
                100_000.0, max(10.0, float(self.max_position_notional_usd))
            ),
            max_account_exposure_usd=min(
                100_000.0, max(10.0, float(self.max_account_exposure_usd))
            ),
            risk_per_trade_fraction=min(
                0.05, max(0.0001, float(self.risk_per_trade_fraction))
            ),
            maximum_daily_loss_usd=min(
                100_000.0, max(1.0, float(self.maximum_daily_loss_usd))
            ),
            maximum_drawdown_fraction=min(
                0.50, max(0.01, float(self.maximum_drawdown_fraction))
            ),
            maximum_trades_per_hour=min(
                100, max(1, int(self.maximum_trades_per_hour))
            ),
            cooldown_seconds=min(86_400, max(0, int(self.cooldown_seconds))),
            maximum_spread_bps=min(
                100.0, max(0.1, float(self.maximum_spread_bps))
            ),
            minimum_fill_fraction=min(
                1.0, max(0.1, float(self.minimum_fill_fraction))
            ),
            stop_required=bool(self.stop_required),
        )

    def to_dict(self) -> dict:
        return asdict(self.clamped())
