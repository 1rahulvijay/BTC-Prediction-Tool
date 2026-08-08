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
    #: Max age of the EXCHANGE EVENT itself (now - event_ts_ms). `quote_stale_ms` measures only
    #: how long ago this process RECEIVED the message, so a delayed old event received now
    #: scored ~0 and passed as fresh.
    source_stale_ms: int
    #: Max transport delay (received_at_ms - event_ts_ms). The fill simulator already computed
    #: this as `quote_age` and then discarded it without ever testing it.
    max_transport_lag_ms: int
    evaluation_interval_ms: int
    sample_interval_ms: int

    @classmethod
    def from_env(cls) -> "EngineConfig":
        return cls(
            hard_enabled=os.getenv("BTC_ENABLE_BINANCE_PAPER", "0") == "1",
            db_path=Path(
                os.getenv("BTC_BINANCE_PAPER_DB", str(DEFAULT_DB_PATH))
            ).resolve(),
            #: A FIXED STAKE. The paper account starts here and is never topped up, so the
            #: run has a definite end: if it reaches zero the strategy is ruined and says so
            #: (see `GovernorMode.EMERGENCY_FLATTEN` / `capital_exhausted`). A bankroll that
            #: silently refills answers no question.
            starting_cash_usd=_float_env(
                "BTC_BINANCE_PAPER_STARTING_CASH", DEFAULT_STARTING_CASH_USD,
                25.0, 10_000_000.0
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
            source_stale_ms=_int_env(
                "BTC_BINANCE_PAPER_SOURCE_STALE_MS", 3_000, 100, 60_000
            ),
            max_transport_lag_ms=_int_env(
                "BTC_BINANCE_PAPER_MAX_TRANSPORT_LAG_MS", 2_000, 50, 60_000
            ),
            evaluation_interval_ms=_int_env(
                "BTC_BINANCE_PAPER_EVAL_MS", 1_000, 250, 60_000
            ),
            sample_interval_ms=_int_env(
                "BTC_BINANCE_PAPER_SAMPLE_MS", 1_000, 250, 60_000
            ),
        )

    @property
    def round_trip_cost_bps(self) -> float:
        """Total cost of a complete round trip, in bps. ONE definition, shared.

        The post-fill admissibility check charged `2 * fee_rate_bps` - fees only - while the
        exit fill simulator ADDITIONALLY applies `slippage_bps`. A target could therefore pass
        "this still clears costs" at fill time and then fail to clear the cost the engine
        actually charges on exit.

        Two crossings, each paying a fee and slippage.
        """
        return 2.0 * (float(self.fee_rate_bps) + float(self.slippage_bps))

    def public_dict(self) -> dict:
        value = asdict(self)
        value["db_path"] = str(self.db_path)
        return value


#: The fixed paper stake. Every dollar limit below is derived from it rather than written
#: as a constant, because a constant limit stops being a limit when the capital changes.
DEFAULT_STARTING_CASH_USD = 250.0

#: Dollar risk limits AS FRACTIONS OF STARTING CAPITAL.
#:
#: The defaults used to be absolute: max_position_notional 1000, max_exposure 1000, daily
#: loss 100, weekly loss 250 - all sized for a 10,000 account. On a 250 account those become
#: 4x the entire account per position and a weekly loss limit equal to TOTAL RUIN, so a gate
#: that reads "maximum weekly loss" would never have stopped anything. A limit that does not
#: scale with the capital it protects is not a limit.
POSITION_NOTIONAL_FRACTION = 0.10      # one position risks at most a tenth of the book
ACCOUNT_EXPOSURE_FRACTION = 0.20       # all open positions together, at most a fifth
DAILY_LOSS_FRACTION = 0.05             # stop for the day after 5%
WEEKLY_LOSS_FRACTION = 0.12            # stop for the week after 12%


@dataclass(frozen=True)
class StrategyRiskConfig:
    enabled: bool = True
    allow_long: bool = True
    allow_short: bool = True
    leverage: float = 1.0
    max_leverage: float = 2.0
    max_position_notional_usd: float = DEFAULT_STARTING_CASH_USD * POSITION_NOTIONAL_FRACTION
    max_account_exposure_usd: float = DEFAULT_STARTING_CASH_USD * ACCOUNT_EXPOSURE_FRACTION
    risk_per_trade_fraction: float = 0.001
    maximum_daily_loss_usd: float = DEFAULT_STARTING_CASH_USD * DAILY_LOSS_FRACTION
    maximum_weekly_loss_usd: float = DEFAULT_STARTING_CASH_USD * WEEKLY_LOSS_FRACTION
    maximum_drawdown_fraction: float = 0.10
    maximum_trades_per_hour: int = 4
    cooldown_seconds: int = 60
    maximum_spread_bps: float = 5.0
    minimum_fill_fraction: float = 1.0
    stop_required: bool = True

    @classmethod
    def for_capital(cls, starting_cash_usd: float, **overrides) -> "StrategyRiskConfig":
        """Risk limits derived from the bankroll THIS ENGINE ACTUALLY STARTS WITH.

        The dataclass defaults are computed from `DEFAULT_STARTING_CASH_USD`, which is a
        CONSTANT. That fixed the original defect - absolute dollars sized for $10,000 that
        stopped being limits on a $250 stake - and introduced its mirror image: fractions of
        250 that stop being limits on anything larger.

            bankroll $10,000, defaults tied to the constant
              max_position_notional  $25.00   0.25% of the account
              maximum_daily_loss     $12.50   0.12% of the account

        A limit must be a fraction of the capital it protects, not of a number that happened
        to be the default. `starting_cash_usd` is configurable to $10M, so this reads it.
        """
        cash = max(0.0, float(starting_cash_usd))
        derived = {
            "max_position_notional_usd": cash * POSITION_NOTIONAL_FRACTION,
            "max_account_exposure_usd": cash * ACCOUNT_EXPOSURE_FRACTION,
            "maximum_daily_loss_usd": cash * DAILY_LOSS_FRACTION,
            "maximum_weekly_loss_usd": cash * WEEKLY_LOSS_FRACTION,
        }
        derived.update(overrides)
        return cls(**derived).clamped()

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
            maximum_weekly_loss_usd=min(
                500_000.0, max(1.0, float(self.maximum_weekly_loss_usd))
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
