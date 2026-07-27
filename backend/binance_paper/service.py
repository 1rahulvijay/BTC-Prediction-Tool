"""Lazy, read-only integration surface for the Binance paper domain."""
from __future__ import annotations

import os
from pathlib import Path
from threading import RLock
import time

from .engine import BinancePaperEngine, DEFAULT_DB


_ENGINE: BinancePaperEngine | None = None
_LOCK = RLock()


def _engine() -> BinancePaperEngine:
    global _ENGINE
    with _LOCK:
        if _ENGINE is None:
            db_path = Path(os.getenv("BTC_BINANCE_PAPER_DB", str(DEFAULT_DB)))
            _ENGINE = BinancePaperEngine(
                db_path=db_path,
                starting_capital=float(
                    os.getenv("BTC_BINANCE_PAPER_STARTING_CAPITAL", "10000")
                ),
                taker_fee_bps=float(
                    os.getenv("BTC_BINANCE_PAPER_TAKER_FEE_BPS", "5")
                ),
                max_slippage_bps=float(
                    os.getenv("BTC_BINANCE_PAPER_MAX_SLIPPAGE_BPS", "20")
                ),
            )
            enabled = os.getenv("BTC_BINANCE_PAPER_ENABLED", "0") == "1"
            _ENGINE.set_paper_enabled(
                enabled,
                "explicit_env_enable" if enabled else "disabled_by_default",
            )
        return _ENGINE


def status(mark_price: float | None = None) -> dict:
    engine = _engine()
    account = None
    if mark_price is not None:
        try:
            account = engine.account(float(mark_price), persist=False)
        except (TypeError, ValueError):
            account = None
    known, reconciliation_reasons = engine.reconcile()
    return {
        "mode": "PAPER_ONLY",
        "live_orders_available": False,
        "authenticated_exchange_client_loaded": False,
        "strategy_order_generation": "NOT_WIRED",
        "database": str(engine.store.path),
        "database_isolated": engine.store.path.name == "binance_paper.duckdb",
        "position_known": known,
        "reconciliation_reasons": reconciliation_reasons,
        "paper_enabled": not engine.kill_switch,
        "kill_reason": engine.kill_reason,
        "account": account,
        "ledger": engine.store.summary(),
        "generated_at_s": time.time(),
    }
