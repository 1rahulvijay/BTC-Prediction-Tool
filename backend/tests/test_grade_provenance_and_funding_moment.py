"""
Scan-5 claims 5.29 and 5.31 - two rows that described two moments, or two questions.

5.31  The price-to-beat mirror publishes a "model win rate". It grades endpoint sign
      against the anchor; the model forecasts a first-touch barrier. Nothing recorded
      either contract, and nothing bounded how much of the model's own forecast window
      the graded round covered.

5.29  A funding cashflow is stamped with the settlement time and priced with the mark
      observed hours later.

Neither fix changes a number. Both make the row say which moment and which question it
describes, which is the only way the number can be read correctly later.

Run directly:  python backend/tests/test_grade_provenance_and_funding_moment.py
"""

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import sys
import tempfile
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

FAILURES = []


def chk(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def test_grade_provenance():
    print("\n5.31 a graded round records WHICH QUESTION and OVER WHICH INTERVAL")
    import target_contract as tc
    from price_to_beat import PriceToBeatTracker

    t = PriceToBeatTracker(horizons=(5, 15), persist=False)
    win_start = 1_800_000_000_000

    fresh = t._grade_provenance(
        {"timestamp": win_start - 3_000, "targetContract": tc.TRAINING_CONTRACT},
        5, win_start)
    chk(fresh["pred_contract"] == tc.FIRST_TOUCH_TRIPLE_BARRIER_V1
        and fresh["grading_contract"] == PriceToBeatTracker.GRADING_CONTRACT,
        "both contracts are on the row - the forecast's and the grader's")
    chk(fresh["contract_match"] is False,
        "and under the SHIPPED configuration they differ: every round in the win-rate "
        "strip is a cross-contract grade, because 'touches +band before -band' is not "
        "'ends above the anchor'. Making that standing fact visible is the fix; changing "
        "the grading is not - endpoint sign against the anchor is the venue's own rule "
        "and the tradeable question")
    chk(fresh["horizon_overlap"] == 0.99 and fresh["grade_usable"] is True,
        f"a forecast 3s before the boundary covers {fresh['horizon_overlap']:.2%} of a "
        f"5m round and is usable")

    stale = t._grade_provenance(
        {"timestamp": win_start - 120_000, "targetContract": tc.TRAINING_CONTRACT},
        5, win_start)
    chk(stale["horizon_overlap"] == 0.6 and stale["grade_usable"] is False,
        "a forecast 2 minutes before the boundary covers only 60% of the round it is "
        "graded on - `_ptb_preds` holds whatever the heavy loop last produced, so a "
        "retrain or a throttled machine silently widens this")

    unknown = t._grade_provenance({"targetContract": tc.TRAINING_CONTRACT}, 5, win_start)
    chk(unknown["horizon_overlap"] is None and unknown["grade_usable"] is False,
        "an undateable forecast is NOT usable - absence of evidence about the interval "
        "is not evidence the interval matched")
    no_contract = t._grade_provenance({"timestamp": win_start}, 5, win_start)
    chk(no_contract["contract_match"] is None,
        "and an unknown contract is unknown, never a match")

    print("\n     ... and the published accuracy names the question it answers")
    t.history[5].extend([(1, "model", True), (0, "model", True), (1, "model", False),
                         (1, "fallback", True)])
    acc = t.accuracy()[5]
    chk(acc["grading_contract"] == PriceToBeatTracker.GRADING_CONTRACT,
        "the strip carries the grading contract beside the rate")
    chk(acc["model_total"] == 3 and acc["model_interval_covered_total"] == 2,
        "and separates the rounds whose forecast actually covered the interval (2 of 3) "
        "from the blended number")
    chk(acc["model_accuracy"] == round(2 / 3, 4)
        and acc["model_interval_covered_accuracy"] == 0.5,
        f"which are different numbers - {acc['model_accuracy']} blended against "
        f"{acc['model_interval_covered_accuracy']} on matched intervals")

    print("\n     ... and legacy rows do not inherit a guarantee they were never measured against")
    t2 = PriceToBeatTracker(horizons=(5, 15), persist=False)
    t2.history[5].extend([1, (1, "model")])          # pre-lean_source, pre-interval shapes
    a2 = t2.accuracy()[5]
    chk(a2["total"] == 2 and a2["interval_covered_total"] == 0,
        "both older shapes still count toward the win rate and neither is reported as "
        "interval-covered")


def test_funding_moment():
    print("\n5.29 a funding cashflow records WHICH MARK PRICED IT")
    from binance_paper.persistence import (
        BinancePaperPersistence, MARK_AT_FUNDING_TOLERANCE_MS)
    from binance_paper.schemas import DataQuality, MarketSnapshot

    def snapshot(received_ms, mark, funding_ms, rate=0.0001):
        return MarketSnapshot(
            "BTCUSDT", received_ms - 5, received_ms, mark, mark - 1.0, mark + 1.0,
            10.0, 10.0, 2.0, 3.0, 0, DataQuality.HEALTHY, 1, rate, funding_ms,
            0, 100, 100, None)

    tmp = Path(tempfile.mkdtemp(prefix="ab_funding_")) / f"{uuid.uuid4().hex}.duckdb"
    store = BinancePaperPersistence(tmp)
    try:
        store.ensure_strategy("trend_following", "trend_following", "v1", True,
                              "{}", "hash", 10_000.0)
        funding_ms = 1_800_000_000_000
        with store.transaction() as conn:
            conn.execute(
                """
                INSERT INTO binance_paper_positions
                (position_id, strategy_id, symbol, side, quantity, entry_price,
                 entry_notional_usd, leverage, margin_usd, entry_fee_usd, stop_price,
                 take_profit_price, maximum_holding_seconds, entry_signal_id,
                 entry_order_id, entry_fill_id, opened_at_ms, last_mark_price,
                 unrealized_pnl_usd, status, updated_at_ms)
                VALUES ('pos1', 'trend_following', 'BTCUSDT', 'LONG', 1.0, 60000.0,
                        60000.0, 1.0, 60000.0, 0.0, 0.0, 0.0, 3600, 'sig', 'ord',
                        'fill', ?, 60000.0, 0.0, 'OPEN', ?)
                """,
                (funding_ms - 3_600_000, funding_ms),
            )

        # Observed four hours after settlement, with BTC 3% higher by then.
        store.apply_observed_funding(
            snapshot(funding_ms + 4 * 3_600_000, 61_800.0, funding_ms))
        with store.transaction() as conn:
            basis, lag, notional, funding = conn.execute(
                "SELECT mark_basis, mark_lag_ms, notional_usd, funding_usd "
                "FROM binance_paper_funding_events"
            ).fetchone()
        chk(basis == "OBSERVATION_TIME_MARK_ESTIMATED",
            "a mark observed long after settlement is labelled ESTIMATED - the exchange "
            "charges on the notional at the funding timestamp, and this engine holds no "
            "mark from that instant")
        chk(lag == 4 * 3_600_000,
            f"and the row carries the actual lag ({lag / 3_600_000:.0f}h), so the error "
            f"is measurable instead of silent")
        chk(abs(notional - 61_800.0) < 1e-6 and funding < 0,
            "the charge is still applied on the best mark available - skipping a real "
            "cashflow would flatter paper P&L, which is the worse error")

        # A second position settled at a funding time we did observe closely.
        near_ms = funding_ms + 8 * 3_600_000
        with store.transaction() as conn:
            conn.execute(
                """
                INSERT INTO binance_paper_positions
                (position_id, strategy_id, symbol, side, quantity, entry_price,
                 entry_notional_usd, leverage, margin_usd, entry_fee_usd, stop_price,
                 take_profit_price, maximum_holding_seconds, entry_signal_id,
                 entry_order_id, entry_fill_id, opened_at_ms, last_mark_price,
                 unrealized_pnl_usd, status, updated_at_ms)
                VALUES ('pos2', 'trend_following', 'BTCUSDT', 'LONG', 1.0, 60000.0,
                        60000.0, 1.0, 60000.0, 0.0, 0.0, 0.0, 3600, 'sig', 'ord',
                        'fill', ?, 60000.0, 0.0, 'OPEN', ?)
                """,
                (near_ms - 3_600_000, near_ms),
            )
        store.apply_observed_funding(
            snapshot(near_ms + 1_000, 62_000.0, near_ms))
        with store.transaction() as conn:
            rows = dict(conn.execute(
                "SELECT position_id, mark_basis FROM binance_paper_funding_events "
                "JOIN binance_paper_positions USING (position_id)"
            ).fetchall())
        chk(rows.get("pos2") == "FUNDING_TIME_MARK",
            f"an observation within {MARK_AT_FUNDING_TOLERANCE_MS}ms of settlement IS the "
            f"funding-time mark and says so - the two cases are distinguishable, which is "
            f"the whole point")
    finally:
        store.close()


def test_legacy_funding_table_migrates():
    print("\n     ... and a database created before those columns is migrated, not broken")
    import duckdb
    from binance_paper.persistence import BinancePaperPersistence

    tmp = Path(tempfile.mkdtemp(prefix="ab_funding_old_")) / f"{uuid.uuid4().hex}.duckdb"
    conn = duckdb.connect(str(tmp))
    conn.execute("""
        CREATE TABLE binance_paper_funding_events (
            funding_event_id VARCHAR PRIMARY KEY, position_id VARCHAR NOT NULL,
            strategy_id VARCHAR NOT NULL, funding_time_ms BIGINT NOT NULL,
            observed_at_ms BIGINT NOT NULL, funding_rate DOUBLE NOT NULL,
            mark_price DOUBLE NOT NULL, notional_usd DOUBLE NOT NULL,
            funding_usd DOUBLE NOT NULL, source VARCHAR NOT NULL,
            created_at_ms BIGINT NOT NULL)
    """)
    conn.close()

    store = BinancePaperPersistence(tmp)
    try:
        with store.transaction() as c:
            cols = {r[1] for r in c.execute(
                "PRAGMA table_info('binance_paper_funding_events')").fetchall()}
        chk({"mark_basis", "mark_lag_ms"} <= cols,
            "the columns are added to an existing table - the funding INSERT is "
            "POSITIONAL, so without this migration every funding event on a live "
            "database would raise and the engine would quietly stop charging funding")
    finally:
        store.close()


def main():
    print("=" * 78)
    print("GRADE PROVENANCE AND FUNDING MOMENT (5.31 / 5.29)")
    print("=" * 78)
    test_grade_provenance()
    test_funding_moment()
    test_legacy_funding_table_migrates()
    print("\n" + "=" * 78)
    if FAILURES:
        print(f"GRADE PROVENANCE AND FUNDING MOMENT: FAIL ({len(FAILURES)})")
        for f in FAILURES:
            print("  - " + f)
        return 1
    print("GRADE PROVENANCE AND FUNDING MOMENT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
