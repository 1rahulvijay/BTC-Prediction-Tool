"""Fair, paper-only $500 venue competition.

Polymarket and Binance keep their native ledgers and economics. This module only
normalizes capital and reporting so realized after-cost PnL can be compared. It
cannot route a real order.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
import uuid
from typing import Any, Callable


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("BTC_DATA_DIR", str(ROOT / "data"))).resolve()
STATE_VERSION = 2


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_hashes(paths: tuple[Path, ...]) -> dict[str, str]:
    return {
        str(path.relative_to(ROOT)).replace("\\", "/"): _file_sha256(path)
        for path in paths
    }


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
                    str(DATA_DIR / "paper_competition_500_v2.json"),
                )
            ).resolve(),
        )

    def identity(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("state_path", None)
        return value


def _polymarket_contract(config: CompetitionConfig) -> dict[str, Any]:
    source = ROOT / "backend" / "polymarket" / "model_dynamic_paper.py"
    source_files = _source_hashes((
        source,
        ROOT / "backend" / "price_to_beat.py",
        ROOT / "backend" / "polymarket_fair_value.py",
        ROOT / "backend" / "database.py",
        ROOT / "backend" / "target_contract.py",
    ))
    return {
        "rule_id": config.polymarket_rule,
        "source_path": str(source.relative_to(ROOT)),
        "source_sha256": _file_sha256(source),
        "source_files": source_files,
        "settlement_policy": "official_for_settlement_live_bid_for_early_exit_v1",
    }


def _binance_contract(service: Any, config: CompetitionConfig) -> dict[str, Any]:
    strategies = {
        item["strategy_id"]: item for item in service.strategy_statuses()
    }
    strategy = strategies.get(config.binance_strategy) or {}
    engine = service.config
    source = ROOT / "backend" / "binance_paper" / "strategies" / "model_consensus.py"
    source_files = _source_hashes((
        source,
        ROOT / "backend" / "binance_endpoint_serving.py",
        ROOT / "backend" / "target_contract.py",
        ROOT / "backend" / "binance_paper" / "market_adapter.py",
        ROOT / "backend" / "binance_paper" / "service.py",
        ROOT / "backend" / "binance_paper" / "execution.py",
        ROOT / "backend" / "binance_paper" / "fill_simulator.py",
        ROOT / "backend" / "binance_paper" / "risk_engine.py",
        ROOT / "backend" / "binance_paper" / "portfolio.py",
        ROOT / "backend" / "binance_paper" / "persistence.py",
    ))
    return {
        "strategy_id": config.binance_strategy,
        "strategy_version": str(strategy.get("version") or ""),
        "strategy_config_hash": str(strategy.get("config_hash") or ""),
        "source_path": str(source.relative_to(ROOT)),
        "source_sha256": _file_sha256(source),
        "source_files": source_files,
        "database_path": str(engine.db_path.resolve()),
        "starting_cash_usd": float(engine.starting_cash_usd),
        "fee_rate_bps": float(engine.fee_rate_bps),
        "slippage_bps": float(engine.slippage_bps),
        "latency_ms": int(engine.latency_ms),
        "quote_stale_ms": int(engine.quote_stale_ms),
        "source_stale_ms": int(engine.source_stale_ms),
        "max_transport_lag_ms": int(engine.max_transport_lag_ms),
        "allow_heuristic_ev": os.getenv(
            "BTC_BINANCE_PAPER_ALLOW_HEURISTIC_EV", "0"
        ).strip().lower() in ("1", "true", "yes"),
        "allow_ungrouped_endpoint_head": os.getenv(
            "BTC_BINANCE_PAPER_ALLOW_UNGROUPED_HEAD", "0"
        ).strip().lower() in ("1", "true", "yes"),
    }


def _state_error(state: Any) -> str | None:
    if not isinstance(state, dict):
        return "race state is not an object"
    if int(state.get("version") or 0) != STATE_VERSION:
        return f"race state version must be {STATE_VERSION}"
    if state.get("paper_only") is not True:
        return "race state is not explicitly paper-only"
    if not str(state.get("race_id") or "").strip():
        return "race id is missing"
    if int(state.get("started_at_ms") or 0) <= 0:
        return "race start timestamp is invalid"
    for field in ("configuration", "polymarket_contract", "binance_contract"):
        if not isinstance(state.get(field), dict) or not state[field]:
            return f"race state is missing {field}"
    return None


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


def _verified_polymarket_close(row: dict[str, Any]) -> bool:
    """Only executable bid exits or official settlements become realized PnL."""
    source = str(row.get("settlement_source") or "")
    reason = str(row.get("exit_reason") or "")
    if reason == "SETTLED":
        return source.startswith("official:")
    return (
        row.get("exit_gross") is not None
        and source == "live_bid"
        and reason not in ("", "SETTLED")
    )


def _polymarket_release_fingerprint(row: dict[str, Any]) -> tuple[str, str] | None:
    """Combined main-model + specialist-head identity, or None when incomplete."""
    model_bundle = str(row.get("model_bundle_id") or "").strip()
    target_contract = str(row.get("target_contract") or "").strip()
    raw_heads = row.get("head_identity_json")
    try:
        heads = json.loads(raw_heads) if isinstance(raw_heads, str) else raw_heads
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not model_bundle or not target_contract or not isinstance(heads, dict) or not heads:
        return None
    canonical_heads: dict[str, dict[str, str]] = {}
    for name, identity in sorted(heads.items()):
        if not isinstance(identity, dict) or identity.get("error"):
            return None
        sha256 = str(identity.get("sha256") or "").strip().lower()
        if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256):
            return None
        canonical_heads[str(name)] = {
            "sha256": sha256,
            "version": str(identity.get("version") or ""),
            "label_basis": str(identity.get("label_basis") or ""),
        }
    if "p_hold" not in canonical_heads:
        return None
    release = {
        "model_bundle_id": model_bundle,
        "target_contract": target_contract,
        "heads": canonical_heads,
    }
    encoded = json.dumps(release, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest(), model_bundle


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
    provisional_settlements = 0
    entered_release_identities: set[str] = set()
    entered_model_bundles: set[str] = set()
    missing_provenance = 0
    for row in rows:
        round_id = str(row.get("round_id") or "")
        entry_ts = int(row.get("ts") or 0)
        if not round_id or entry_ts <= 0:
            continue
        events.append((entry_ts, 1, round_id, row))
        release = _polymarket_release_fingerprint(row)
        if release is not None:
            release_fingerprint, model_bundle = release
            entered_release_identities.add(release_fingerprint)
            entered_model_bundles.add(model_bundle)
        else:
            missing_provenance += 1
        if (
            row.get("settled_ts") is not None
            and row.get("pnl") is not None
            and _verified_polymarket_close(row)
        ):
            events.append((int(row["settled_ts"]), 0, round_id, row))
        elif row.get("settled_ts") is not None and row.get("pnl") is not None:
            provisional_settlements += 1
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
    release_count = len(entered_release_identities)
    if missing_provenance:
        trust_state = "BLOCKED_MODEL_PROVENANCE_MISSING"
    elif release_count > 1:
        trust_state = "BLOCKED_MIXED_MODEL_RELEASES"
    else:
        trust_state = "PAPER_ACCOUNTING_ONLY"
    return {
        "venue": "POLYMARKET",
        "model": config.polymarket_rule,
        "trust_state": trust_state,
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
        "provisional_settlements": provisional_settlements,
        "model_release_count": release_count,
        "model_bundle_ids": sorted(entered_model_bundles),
        "missing_provenance_entries": missing_provenance,
        "unrealized_mark_available": False,
        "accounting_note": (
            "Open Polymarket positions stay at cost until an executable bid exit or "
            "official settlement; proxy settlements never enter ranked realized PnL."
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
        current_contract = _binance_contract(service, config)
        contract_match = state.get("binance_contract") == current_contract
        config_ok = (
            state.get("binance_clean_start") is True
            and contract_match
            and abs(configured_cash - bankroll) <= 1e-6 and active_db == expected_db
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
        model_bundle_ids = sorted({
            str(row.get("model_bundle_id") or "").strip()
            for row in trades
            if str(row.get("model_bundle_id") or "").strip()
        })
        missing_provenance = sum(
            1 for row in trades
            if not str(row.get("model_bundle_id") or "").strip()
            or not str(row.get("entry_strategy_config_hash") or "").strip()
        )
        mixed_releases = len(model_bundle_ids) > 1
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
        if not config_ok:
            trust = "BLOCKED_CONFIGURATION_MISMATCH"
        elif missing_provenance:
            trust = "BLOCKED_MODEL_PROVENANCE_MISSING"
        elif mixed_releases:
            trust = "BLOCKED_MIXED_MODEL_RELEASES"
        else:
            trust = "PAPER_ACCOUNTING_ONLY"
        return {
            **base,
            "trust_state": trust,
            "sample_status": _sample_status(len(pnls)),
            "runtime_state": status.get("runtime_state"),
            "strategy_enabled": bool(strategy.get("enabled")),
            "configuration_ok": config_ok,
            "execution_contract_match": contract_match,
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
            "model_release_count": len(model_bundle_ids),
            "model_bundle_ids": model_bundle_ids,
            "missing_provenance_trades": missing_provenance,
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
        self._state_lock = threading.RLock()

    def ensure_started(self) -> dict[str, Any]:
        with self._state_lock:
            path = self.config.state_path
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                with path.open("r", encoding="utf-8") as handle:
                    state = json.load(handle)
                error = _state_error(state)
                if error:
                    raise RuntimeError(f"invalid paper-competition state: {error}")
                return state
            strategies = {
                item["strategy_id"]: item
                for item in self.binance_service.strategy_statuses()
            }
            strategy = strategies.get(self.config.binance_strategy) or {}
            account = strategy.get("account") or {}
            prior_trades = self.binance_service.persistence.trades(
                1, self.config.binance_strategy
            )
            state = {
                "version": STATE_VERSION,
                "race_id": str(uuid.uuid4()),
                "started_at_ms": int(time.time() * 1000),
                "created_by": "paper_competition_v2",
                "paper_only": True,
                "configuration": self.config.identity(),
                "polymarket_contract": _polymarket_contract(self.config),
                "binance_contract": _binance_contract(
                    self.binance_service, self.config
                ),
                "binance_db_path": str(
                    self.binance_service.config.db_path.resolve()
                ),
            }
            state["binance_clean_start"] = bool(
                not prior_trades
                and not strategy.get("position")
                and abs(
                    _finite(account.get("starting_cash_usd"), -1.0)
                    - self.config.bankroll_usd
                ) <= 1e-6
                and abs(_finite(account.get("realized_pnl_usd"))) <= 1e-9
                and abs(_finite(account.get("trading_fees_usd"))) <= 1e-9
                and abs(_finite(account.get("funding_usd"))) <= 1e-9
            )
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
        try:
            state = self.ensure_started()
        except Exception as exc:
            blocked = {
                "trust_state": "ACCOUNTING_UNAVAILABLE",
                "sample_status": "NO_RESOLVED_TRADES",
                "starting_bankroll_usd": self.config.bankroll_usd,
                "error": str(exc),
            }
            return {
                "paper_only": True,
                "real_orders_disabled": True,
                "comparable": False,
                "leader": "NOT_COMPARABLE",
                "polymarket": {
                    **blocked,
                    "venue": "POLYMARKET",
                    "model": self.config.polymarket_rule,
                },
                "binance": {
                    **blocked,
                    "venue": "BINANCE",
                    "model": self.config.binance_strategy,
                },
                "warning": "Race state is invalid; no score is trusted or displayed.",
            }
        config_match = state.get("configuration") == self.config.identity()
        polymarket_contract_match = (
            state.get("polymarket_contract") == _polymarket_contract(self.config)
        )
        if config_match and polymarket_contract_match:
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
            "polymarket_contract_match": polymarket_contract_match,
            "binance_contract_match": (
                state.get("binance_contract")
                == _binance_contract(self.binance_service, self.config)
            ),
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
            "exit_reason": "SETTLED",
            "settlement_source": "official:test",
            "model_bundle_id": "main-one",
            "target_contract": "first_touch_triple_barrier_v1",
            "head_identity_json": json.dumps({"p_hold": {"sha256": "1" * 64}}),
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
            "exit_reason": "SETTLED",
            "settlement_source": "official:test",
            "model_bundle_id": "main-one",
            "target_contract": "first_touch_triple_barrier_v1",
            "head_identity_json": json.dumps({"p_hold": {"sha256": "1" * 64}}),
        },
    ]
    summary = summarize_polymarket(rows, config)
    assert summary["accepted_entries"] == 2
    assert summary["depth_limited_entries"] == 1
    assert summary["closed_trades"] == 2 and summary["wins"] == 1
    assert abs(summary["realized_net_pnl_usd"] - 49.4) < 1e-9
    assert abs(summary["settled_equity_usd"] - 549.4) < 1e-9
    assert summary["open_positions"] == 0

    proxy_row = {
        **rows[0],
        "round_id": "proxy",
        "settlement_source": "pyth_proxy",
    }
    proxy = summarize_polymarket([proxy_row], config)
    assert proxy["closed_trades"] == 0
    assert proxy["open_positions"] == 1
    assert proxy["provisional_settlements"] == 1

    mixed = summarize_polymarket([
        rows[0],
        {
            **rows[1],
            "round_id": "other-release",
            "head_identity_json": json.dumps({"p_hold": {"sha256": "2" * 64}}),
        },
    ], config)
    assert mixed["trust_state"] == "BLOCKED_MIXED_MODEL_RELEASES"

    class FakePersistence:
        @staticmethod
        def competition_trades_since(strategy_id: str, since_ms: int):
            assert strategy_id == "model_consensus" and since_ms == 900
            return [
                {
                    "trade_id": "b1",
                    "entry_time_ms": 1_000,
                    "exit_time_ms": 2_000,
                    "net_pnl_usd": 5.0,
                    "entry_fee_usd": 0.10,
                    "exit_fee_usd": 0.10,
                    "funding_usd": -0.05,
                    "slippage_usd": 0.15,
                    "model_bundle_id": "bundle-1",
                    "entry_strategy_config_hash": "config-1",
                },
                {
                    "trade_id": "b2",
                    "entry_time_ms": 3_000,
                    "exit_time_ms": 4_000,
                    "net_pnl_usd": -2.0,
                    "entry_fee_usd": 0.10,
                    "exit_fee_usd": 0.10,
                    "funding_usd": 0.02,
                    "slippage_usd": 0.15,
                    "model_bundle_id": "bundle-1",
                    "entry_strategy_config_hash": "config-1",
                },
            ]

    risk = {
        "max_position_notional_usd": 50.0,
        "max_account_exposure_usd": 100.0,
        "maximum_daily_loss_usd": 25.0,
        "maximum_weekly_loss_usd": 60.0,
        "maximum_drawdown_fraction": 0.10,
    }

    class FakeService:
        config = type("Config", (), {
            "db_path": Path("fake.duckdb").resolve(),
            "starting_cash_usd": 500.0,
            "fee_rate_bps": 5.0,
            "slippage_bps": 1.0,
            "latency_ms": 500,
            "quote_stale_ms": 2_000,
            "source_stale_ms": 3_000,
            "max_transport_lag_ms": 2_000,
        })()
        persistence = FakePersistence()

        @staticmethod
        def status():
            return {"initialized": True, "runtime_state": "RUNNING"}

        @staticmethod
        def strategy_statuses():
            return [{
                "strategy_id": "model_consensus",
                "version": "1",
                "config_hash": "config-1",
                "enabled": True,
                "risk": risk,
                "account": {
                    "starting_cash_usd": 500.0,
                    "available_cash_usd": 503.0,
                    "equity_usd": 503.0,
                    "maximum_drawdown_usd": 2.0,
                },
                "position": None,
                "latest_decision": {"action": "HOLD"},
                "inactive_reason": None,
            }]

    race_state = {
        "started_at_ms": 900,
        "binance_db_path": str(FakeService.config.db_path),
        "binance_clean_start": True,
        "binance_contract": _binance_contract(FakeService(), config),
    }
    binance = summarize_binance(FakeService(), race_state, config)
    assert binance["trust_state"] == "PAPER_ACCOUNTING_ONLY"
    assert binance["closed_trades"] == 2 and binance["wins"] == 1
    assert abs(binance["realized_net_pnl_usd"] - 3.0) < 1e-9
    assert abs(binance["settled_equity_usd"] - 503.0) < 1e-9
    race_state["binance_clean_start"] = False
    blocked = summarize_binance(FakeService(), race_state, config)
    assert blocked["trust_state"] == "BLOCKED_CONFIGURATION_MISMATCH"
    print("paper-competition: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
