"""Fair, paper-only $500 venue competition.

Polymarket and Binance keep their native ledgers and economics. This module only
normalizes capital and reporting so realized after-cost PnL can be compared. It
cannot route a real order.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import tempfile
import time
import uuid
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("BTC_DATA_DIR", str(ROOT / "data"))).resolve()


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return min(high, max(low, value))


@dataclass(frozen=True)
class CompetitionConfig:
    bankroll_usd: float
    position_fraction: float
    exposure_fraction: float
    daily_loss_fraction: float
    weekly_loss_fraction: float
    maximum_drawdown_fraction: float
    polymarket_rule: str
    binance_strategy: str
    state_path: Path

    @classmethod
    def from_env(cls) -> "CompetitionConfig":
        return cls(
            bankroll_usd=_env_float(
                "BTC_PAPER_COMPETITION_BANKROLL_USD", 500.0, 25.0, 1_000_000.0
            ),
            position_fraction=_env_float(
                "BTC_PAPER_COMPETITION_POSITION_FRACTION", 0.10, 0.001, 0.25
            ),
            exposure_fraction=_env_float(
                "BTC_PAPER_COMPETITION_EXPOSURE_FRACTION", 0.20, 0.001, 0.50
            ),
            daily_loss_fraction=_env_float(
                "BTC_PAPER_COMPETITION_DAILY_LOSS_FRACTION", 0.05, 0.001, 0.25
            ),
            weekly_loss_fraction=_env_float(
                "BTC_PAPER_COMPETITION_WEEKLY_LOSS_FRACTION", 0.12, 0.001, 0.50
            ),
            maximum_drawdown_fraction=_env_float(
                "BTC_PAPER_COMPETITION_MAX_DRAWDOWN_FRACTION", 0.10, 0.01, 0.50
            ),
            polymarket_rule=os.getenv(
                "BTC_PAPER_COMPETITION_POLY_RULE", "CHAMPION_DYNAMIC_PAPER_V1"
            ).strip(),
            binance_strategy=os.getenv(
                "BTC_PAPER_COMPETITION_BINANCE_STRATEGY", "model_consensus"
            ).strip(),
            state_path=Path(
                os.getenv(
                    "BTC_PAPER_COMPETITION_STATE",
                    str(DATA_DIR / "paper_competition_500.json"),
                )
            ).resolve(),
        )

    def identity(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("state_path", None)
        return value


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _sample_status(n: int) -> str:
    if n <= 0:
        return "NO_RESOLVED_TRADES"
    if n < 30:
        return "EARLY_SAMPLE"
    if n < 200:
        return "COLLECTING"
    return "RESEARCH_SAMPLE_ONLY"


def _week_key(timestamp_ms: int) -> str:
    dt = datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc)
    iso = dt.isocalendar()
    return f"{iso.year:04d}-W{iso.week:02d}"


def _day_key(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(
        timestamp_ms / 1000.0, tz=timezone.utc
    ).date().isoformat()


def _profit_factor(pnls: list[float]) -> float | None:
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss = -sum(value for value in pnls if value < 0)
    if gross_loss <= 1e-12:
        return None if gross_profit <= 1e-12 else math.inf
    return gross_profit / gross_loss


def _realized_drawdown(starting_cash: float, pnls: list[float]) -> float:
    equity = peak = float(starting_cash)
    maximum = 0.0
    for pnl in pnls:
        equity += float(pnl)
        peak = max(peak, equity)
        maximum = max(maximum, peak - equity)
    return maximum


def _display_profit_factor(pnls: list[float]) -> float | str | None:
    value = _profit_factor(pnls)
    if value is None:
        return None
    return "INF" if math.isinf(value) else round(value, 4)


def summarize_polymarket(
    rows: list[dict[str, Any]], config: CompetitionConfig
) -> dict[str, Any]:
    """Replay the Champion ledger using bounded cash and recorded top-ask depth."""
    bankroll = float(config.bankroll_usd)
    per_trade_cap = bankroll * float(config.position_fraction)
    exposure_cap = bankroll * float(config.exposure_fraction)
    daily_limit = bankroll * float(config.daily_loss_fraction)
    weekly_limit = bankroll * float(config.weekly_loss_fraction)
    maximum_drawdown = bankroll * float(config.maximum_drawdown_fraction)

    events: list[tuple[int, int, str, dict[str, Any]]] = []
    for row in rows:
        round_id = str(row.get("round_id") or "")
        entry_ts = int(row.get("ts") or 0)
        if not round_id or entry_ts <= 0:
            continue
        events.append((entry_ts, 1, round_id, row))
        if row.get("settled_ts") is not None and row.get("pnl") is not None:
            events.append((int(row["settled_ts"]), 0, round_id, row))
    # A close releases capital before another entry at the same timestamp.
    events.sort(key=lambda item: (item[0], item[1], item[2]))

    cash = bankroll
    realized = 0.0
    realized_peak = bankroll
    open_positions: dict[str, dict[str, float]] = {}
    closed_pnls: list[float] = []
    day_pnl: dict[str, float] = {}
    week_pnl: dict[str, float] = {}
    fees = 0.0
    accepted = blocked = depth_limited = 0
    blocked_reasons: dict[str, int] = {}

    def block(reason: str) -> None:
        nonlocal blocked
        blocked += 1
        blocked_reasons[reason] = blocked_reasons.get(reason, 0) + 1

    for event_ts, event_kind, round_id, row in events:
        if event_kind == 0:
            position = open_positions.pop(round_id, None)
            if position is None:
                continue
            exit_fee = max(0.0, _finite(row.get("exit_fee")))
            exit_gross = row.get("exit_gross")
            if exit_gross is None:
                per_share_proceeds = max(
                    0.0, position["cost_per_share"] + _finite(row.get("pnl"))
                )
            else:
                per_share_proceeds = max(0.0, _finite(exit_gross) - exit_fee)
            proceeds = position["shares"] * per_share_proceeds
            pnl = proceeds - position["entry_cost"]
            cash += proceeds
            realized += pnl
            realized_peak = max(realized_peak, bankroll + realized)
            closed_pnls.append(pnl)
            fees += position["shares"] * exit_fee
            day = _day_key(event_ts)
            week = _week_key(event_ts)
            day_pnl[day] = day_pnl.get(day, 0.0) + pnl
            week_pnl[week] = week_pnl.get(week, 0.0) + pnl
            continue

        current_equity = bankroll + realized
        if realized_peak - current_equity >= maximum_drawdown - 1e-9:
            block("maximum_drawdown_stop")
            continue
        if day_pnl.get(_day_key(event_ts), 0.0) <= -daily_limit:
            block("daily_loss_stop")
            continue
        if week_pnl.get(_week_key(event_ts), 0.0) <= -weekly_limit:
            block("weekly_loss_stop")
            continue

        ask = _finite(row.get("ask"), -1.0)
        entry_fee = max(0.0, _finite(row.get("fee")))
        depth = max(0.0, _finite(row.get("depth")))
        cost_per_share = ask + entry_fee
        if not (0.0 < ask < 1.0) or cost_per_share <= 0.0:
            block("invalid_entry_price")
            continue
        if depth <= 0.0:
            block("no_recorded_ask_depth")
            continue
        if round_id in open_positions:
            block("duplicate_open_round")
            continue
        open_exposure = sum(
            item["entry_cost"] for item in open_positions.values()
        )
        budget = min(per_trade_cap, exposure_cap - open_exposure, cash)
        if budget <= 0.01:
            block("capital_or_exposure_unavailable")
            continue
        desired_shares = budget / cost_per_share
        shares = min(desired_shares, depth)
        if shares + 1e-12 < desired_shares:
            depth_limited += 1
        entry_cost = shares * cost_per_share
        if shares <= 0.0 or entry_cost <= 0.0:
            block("unfillable_position")
            continue
        cash -= entry_cost
        fees += shares * entry_fee
        accepted += 1
        open_positions[round_id] = {
            "shares": shares,
            "entry_cost": entry_cost,
            "cost_per_share": cost_per_share,
        }

    wins = sum(1 for value in closed_pnls if value > 0)
    open_exposure = sum(item["entry_cost"] for item in open_positions.values())
    settled_equity = bankroll + realized
    return {
        "venue": "POLYMARKET",
        "model": config.polymarket_rule,
        "trust_state": "PAPER_ACCOUNTING_ONLY",
        "sample_status": _sample_status(len(closed_pnls)),
        "starting_bankroll_usd": round(bankroll, 6),
        "settled_equity_usd": round(settled_equity, 6),
        "realized_net_pnl_usd": round(realized, 6),
        "roi_pct": round(realized / bankroll * 100.0, 4),
        "available_cash_usd": round(cash, 6),
        "open_exposure_at_cost_usd": round(open_exposure, 6),
        "open_positions": len(open_positions),
        "entry_signals": len(rows),
        "accepted_entries": accepted,
        "blocked_entries": blocked,
        "blocked_reasons": blocked_reasons,
        "closed_trades": len(closed_pnls),
        "wins": wins,
        "win_rate_pct": (
            round(wins / len(closed_pnls) * 100.0, 2) if closed_pnls else None
        ),
        "average_net_pnl_usd": (
            round(sum(closed_pnls) / len(closed_pnls), 6)
            if closed_pnls else None
        ),
        "profit_factor": _display_profit_factor(closed_pnls),
        "maximum_realized_drawdown_usd": round(
            _realized_drawdown(bankroll, closed_pnls), 6
        ),
        "fees_usd": round(fees, 6),
        "depth_limited_entries": depth_limited,
        "unrealized_mark_available": False,
        "accounting_note": (
            "Open Polymarket positions stay at cost until an executable exit or "
            "settlement; ranking uses realized PnL only."
        ),
    }


def summarize_binance(
    service: Any, state: dict[str, Any], config: CompetitionConfig
) -> dict[str, Any]:
    bankroll = float(config.bankroll_usd)
    base = {
        "venue": "BINANCE",
        "model": config.binance_strategy,
        "starting_bankroll_usd": round(bankroll, 6),
    }
    try:
        status = service.status()
        if not status.get("initialized"):
            return {
                **base,
                "trust_state": "ENGINE_NOT_INITIALIZED",
                "sample_status": "NO_RESOLVED_TRADES",
            }
        strategies = {
            item["strategy_id"]: item for item in service.strategy_statuses()
        }
        strategy = strategies.get(config.binance_strategy)
        if strategy is None:
            return {
                **base,
                "trust_state": "STRATEGY_MISSING",
                "sample_status": "NO_RESOLVED_TRADES",
            }
        account = strategy.get("account") or {}
        risk = strategy.get("risk") or {}
        configured_cash = _finite(account.get("starting_cash_usd"), -1.0)
        expected_db = str(state.get("binance_db_path") or "")
        active_db = str(service.config.db_path.resolve())
        expected_position_cap = bankroll * float(config.position_fraction)
        expected_exposure_cap = bankroll * float(config.exposure_fraction)
        expected_daily_limit = bankroll * float(config.daily_loss_fraction)
        expected_weekly_limit = bankroll * float(config.weekly_loss_fraction)
        config_ok = (
            abs(configured_cash - bankroll) <= 1e-6 and active_db == expected_db
            and abs(
                _finite(risk.get("max_position_notional_usd"), -1.0)
                - expected_position_cap
            ) <= 1e-6
            and abs(
                _finite(risk.get("max_account_exposure_usd"), -1.0)
                - expected_exposure_cap
            ) <= 1e-6
            and abs(
                _finite(risk.get("maximum_daily_loss_usd"), -1.0)
                - expected_daily_limit
            ) <= 1e-6
            and abs(
                _finite(risk.get("maximum_weekly_loss_usd"), -1.0)
                - expected_weekly_limit
            ) <= 1e-6
            and abs(
                _finite(risk.get("maximum_drawdown_fraction"), -1.0)
                - float(config.maximum_drawdown_fraction)
            ) <= 1e-9
        )
        since_ms = int(state["started_at_ms"])
        trades = service.persistence.competition_trades_since(
            config.binance_strategy, since_ms
        )
        pnls = [_finite(row.get("net_pnl_usd")) for row in trades]
        realized = sum(pnls)
        wins = sum(1 for value in pnls if value > 0)
        fees = sum(
            _finite(row.get("entry_fee_usd"))
            + _finite(row.get("exit_fee_usd"))
            for row in trades
        )
        funding = sum(_finite(row.get("funding_usd")) for row in trades)
        slippage = sum(_finite(row.get("slippage_usd")) for row in trades)
        position = strategy.get("position")
        position_in_race = (
            position
            if position
            and int(position.get("opened_at_ms") or 0) >= since_ms
            else None
        )
        open_exposure = (
            _finite(position_in_race.get("quantity"))
            * _finite(position_in_race.get("last_mark_price"))
            if position_in_race
            else 0.0
        )
        trust = (
            "PAPER_ACCOUNTING_ONLY"
            if config_ok
            else "BLOCKED_CONFIGURATION_MISMATCH"
        )
        return {
            **base,
            "trust_state": trust,
            "sample_status": _sample_status(len(pnls)),
            "runtime_state": status.get("runtime_state"),
            "strategy_enabled": bool(strategy.get("enabled")),
            "configuration_ok": config_ok,
            "configured_starting_cash_usd": configured_cash,
            "database_path": active_db,
            "settled_equity_usd": round(bankroll + realized, 6),
            "current_marked_equity_usd": round(
                _finite(account.get("equity_usd")), 6
            ),
            "realized_net_pnl_usd": round(realized, 6),
            "unrealized_net_pnl_usd": round(
                _finite(position_in_race.get("unrealized_pnl_usd"))
                if position_in_race else 0.0,
                6,
            ),
            "roi_pct": round(realized / bankroll * 100.0, 4),
            "available_cash_usd": round(
                _finite(account.get("available_cash_usd")), 6
            ),
            "open_exposure_at_cost_usd": round(open_exposure, 6),
            "open_positions": 1 if position_in_race else 0,
            "closed_trades": len(pnls),
            "wins": wins,
            "win_rate_pct": (
                round(wins / len(pnls) * 100.0, 2) if pnls else None
            ),
            "average_net_pnl_usd": (
                round(realized / len(pnls), 6) if pnls else None
            ),
            "profit_factor": _display_profit_factor(pnls),
            "maximum_realized_drawdown_usd": round(
                _realized_drawdown(bankroll, pnls), 6
            ),
            "maximum_marked_drawdown_usd": round(
                _finite(account.get("maximum_drawdown_usd")), 6
            ),
            "fees_usd": round(fees, 6),
            "funding_usd": round(funding, 6),
            "slippage_usd": round(slippage, 6),
            "latest_decision": strategy.get("latest_decision"),
            "inactive_reason": strategy.get("inactive_reason"),
            "unrealized_mark_available": True,
            "accounting_note": (
                "Competition ranking uses closed-trade net PnL; marked equity is "
                "diagnostic only."
            ),
        }
    except Exception as exc:
        return {
            **base,
            "trust_state": "ACCOUNTING_UNAVAILABLE",
            "sample_status": "NO_RESOLVED_TRADES",
            "error": str(exc),
        }


class PaperCompetition:
    def __init__(
        self,
        binance_service: Any,
        polymarket_rows: Callable[
            [str, int], list[dict[str, Any]] | None
        ],
        config: CompetitionConfig | None = None,
    ):
        self.binance_service = binance_service
        self.polymarket_rows = polymarket_rows
        self.config = config or CompetitionConfig.from_env()

    def ensure_started(self) -> dict[str, Any]:
        path = self.config.state_path
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        state = {
            "version": 1,
            "race_id": str(uuid.uuid4()),
            "started_at_ms": int(time.time() * 1000),
            "created_by": "paper_competition_v1",
            "paper_only": True,
            "configuration": self.config.identity(),
            "binance_db_path": str(
                self.binance_service.config.db_path.resolve()
            ),
        }
        fd, temporary = tempfile.mkstemp(
            prefix=path.name, suffix=".tmp", dir=str(path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return state

    def summary(self) -> dict[str, Any]:
        state = self.ensure_started()
        config_match = state.get("configuration") == self.config.identity()
        if config_match:
            rows = self.polymarket_rows(
                self.config.polymarket_rule, int(state["started_at_ms"])
            )
            if rows is None:
                poly = {
                    "venue": "POLYMARKET",
                    "model": self.config.polymarket_rule,
                    "starting_bankroll_usd": self.config.bankroll_usd,
                    "trust_state": "ACCOUNTING_UNAVAILABLE",
                    "sample_status": "NO_RESOLVED_TRADES",
                }
            else:
                poly = summarize_polymarket(rows, self.config)
        else:
            poly = {
                "venue": "POLYMARKET",
                "model": self.config.polymarket_rule,
                "starting_bankroll_usd": self.config.bankroll_usd,
                "trust_state": "BLOCKED_CONFIGURATION_MISMATCH",
                "sample_status": "NO_RESOLVED_TRADES",
            }
        binance = summarize_binance(self.binance_service, state, self.config)
        comparable = (
            config_match
            and poly.get("trust_state") == "PAPER_ACCOUNTING_ONLY"
            and binance.get("trust_state") == "PAPER_ACCOUNTING_ONLY"
        )
        poly_pnl = _finite(poly.get("realized_net_pnl_usd"))
        binance_pnl = _finite(binance.get("realized_net_pnl_usd"))
        total_closed = int(poly.get("closed_trades") or 0) + int(
            binance.get("closed_trades") or 0
        )
        if not comparable:
            leader = "NOT_COMPARABLE"
        elif total_closed == 0:
            leader = "NO_LEADER_YET"
        elif abs(poly_pnl - binance_pnl) <= 0.005:
            leader = "TIE"
        else:
            leader = "POLYMARKET" if poly_pnl > binance_pnl else "BINANCE"
        independently_ready = (
            int(poly.get("closed_trades") or 0) >= 30
            and int(binance.get("closed_trades") or 0) >= 30
        )
        return {
            "paper_only": True,
            "real_orders_disabled": True,
            "race_id": state.get("race_id"),
            "started_at_ms": state.get("started_at_ms"),
            "bankroll_per_model_usd": self.config.bankroll_usd,
            "total_paper_capital_usd": self.config.bankroll_usd * 2.0,
            "ranking_basis": "REALIZED_AFTER_COST_PNL_ONLY",
            "configuration_match": config_match,
            "comparable": comparable,
            "leader": leader,
            "leader_margin_usd": (
                round(abs(poly_pnl - binance_pnl), 6) if comparable else None
            ),
            "evidence_sufficient_for_comparison": independently_ready,
            "minimum_closed_trades_per_model": 30,
            "polymarket": poly,
            "binance": binance,
            "warning": (
                "Paper results are an experiment, not evidence of sustainable income "
                "or permission to use real capital."
            ),
        }


def selftest() -> int:
    config = CompetitionConfig(
        bankroll_usd=500.0,
        position_fraction=0.10,
        exposure_fraction=0.20,
        daily_loss_fraction=0.05,
        weekly_loss_fraction=0.12,
        maximum_drawdown_fraction=0.10,
        polymarket_rule="CHAMPION_DYNAMIC_PAPER_V1",
        binance_strategy="model_consensus",
        state_path=Path("unused.json"),
    )
    rows = [
        {
            "round_id": "win",
            "ts": 1_000,
            "ask": 0.49,
            "fee": 0.01,
            "depth": 200.0,
            "settled_ts": 2_000,
            "exit_gross": 1.0,
            "exit_fee": 0.0,
            "pnl": 0.50,
        },
        {
            "round_id": "loss",
            "ts": 1_100,
            "ask": 0.59,
            "fee": 0.01,
            "depth": 1.0,
            "settled_ts": 2_100,
            "exit_gross": 0.0,
            "exit_fee": 0.0,
            "pnl": -0.60,
        },
    ]
    summary = summarize_polymarket(rows, config)
    assert summary["accepted_entries"] == 2
    assert summary["depth_limited_entries"] == 1
    assert summary["closed_trades"] == 2 and summary["wins"] == 1
    assert abs(summary["realized_net_pnl_usd"] - 49.4) < 1e-9
    assert abs(summary["settled_equity_usd"] - 549.4) < 1e-9
    assert summary["open_positions"] == 0
    print("paper-competition: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
