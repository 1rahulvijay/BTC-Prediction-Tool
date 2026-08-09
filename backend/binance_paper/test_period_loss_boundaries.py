"""A position held across midnight must still count against today's loss limit.

THE DEFECT
    `net_pnl_since()` added unrealised P&L only for positions matching
    `opened_at_ms >= since_ms`. So:

        Sunday 23:55   LONG opens,           unrealised   $0
        Monday 00:00   still open
        Monday 10:00   still open,           unrealised -$20

    Monday's gate saw $0. The position was opened before Monday, so it was excluded from
    Monday's open-position P&L entirely - while the account was in fact down $20 that day.
    The same hole existed at the weekly boundary, and holding through a boundary evaded the
    limit that exists precisely to stop a large open loser running.

DIRECTION OF THE REMAINING APPROXIMATION
    Every open position now contributes its whole unrealised P&L, including the part accrued
    on earlier days, so the gate can trip slightly EARLY. That is the safe direction: firing
    too soon costs an opportunity, firing too late costs the limit its entire purpose. The
    exact remedy is equity-at-boundary, which needs a snapshot that does not exist yet.

    python -m backend.binance_paper.test_period_loss_boundaries
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CHECKS = 0
DAY = 86_400_000

#: Every NOT NULL column, so the insert cannot silently fail and leave the test asserting
#: against an empty table - which would pass whether or not the fix exists.
_COLS = ("position_id", "strategy_id", "symbol", "side", "quantity", "entry_price",
         "entry_notional_usd", "leverage", "margin_usd", "entry_fee_usd", "stop_price",
         "take_profit_price", "maximum_holding_seconds", "entry_signal_id", "entry_order_id",
         "entry_fill_id", "opened_at_ms", "last_mark_price", "unrealized_pnl_usd", "status",
         "updated_at_ms")


def _row(pid, sid, opened_ms, status, unreal):
    return (pid, sid, "BTCUSDT", "LONG", 0.01, 60000.0, 600.0, 1.0, 600.0, 0.3,
            59700.0, 60300.0, 900, "sig", "ord", "fill", opened_ms, 60000.0,
            unreal, status, opened_ms)


def check(cond, text):
    global CHECKS
    assert cond, text
    CHECKS += 1
    print(f"  PASS  {text}")


def main() -> int:
    import tempfile as _tf
    from binance_paper.persistence import BinancePaperPersistence

    tmp = _tf.mkdtemp()
    store = BinancePaperPersistence(Path(tmp) / "paper.duckdb")
    sql = f"INSERT INTO binance_paper_positions ({','.join(_COLS)}) VALUES ({','.join('?' * len(_COLS))})"
    try:
        sid = "test-strategy"
        monday = 1_785_000_000_000 // DAY * DAY + DAY
        sunday_late = monday - 5 * 60_000

        store._conn.execute(sql, _row("p1", sid, sunday_late, "OPEN", -20.0))
        check(store.net_pnl_since(sid, monday) == -20.0,
              "a position opened 23:55 Sunday and STILL OPEN contributes its -$20 to MONDAY's "
              "period P&L - it previously contributed $0, so holding across midnight evaded "
              "the daily loss gate entirely")

        store._conn.execute(sql, _row("p2", sid, monday + 3_600_000, "OPEN", -5.0))
        check(store.net_pnl_since(sid, monday) == -25.0,
              "and one opened inside the window ADDS to it rather than replacing it")

        store._conn.execute(sql, _row("p3", sid, sunday_late, "CLOSED", -999.0))
        check(store.net_pnl_since(sid, monday) == -25.0,
              "while a CLOSED position stays excluded - dropping the opened_at filter must "
              "not also drop the status filter, or realised P&L would be double counted")

        check(store.net_pnl_since(sid, monday - 3 * DAY) == -25.0,
              "the same holds at a WEEKLY boundary, which had the identical hole")
    finally:
        try:
            store._conn.close()
        except Exception:
            pass

    print("")
    print(f"PERIOD LOSS BOUNDARIES: PASS ({CHECKS} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
