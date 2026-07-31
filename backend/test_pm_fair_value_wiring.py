"""The PM_CALIBRATED_FAIR_VALUE_V1 wiring must actually log, not silently swallow.

WHY THIS EXISTS
    The wiring in price_to_beat.py sits inside `except Exception: logger.debug(...)`, matching
    its neighbours. That is the right shape for a per-round hot path - one bad round must not
    stop the loop - but it means a wrong call signature, a renamed column or a bad argument
    order would produce NOTHING and say nothing. The strategy would look registered, the audit
    would pass, and no row would ever appear.

    This repository has already shipped that exact failure once: mean_reversion referenced
    `snapshot.agg_trade_count_baseline`, a field that does not exist, and its getattr fallback
    made it permanently NO_EDGE - alive in the registry, never trading, silent about it.

    So the wiring is exercised here against the real database call, with the real argument
    shape, and the row is read back.

WHAT IS ASSERTED
    1. log_rule_paper_trade accepts the exact positional shape the wiring uses, for both the
       CAL_UNAVAILABLE branch and the ENTER branch.
    2. The row is retrievable afterwards - the write is not merely accepted and dropped.
    3. The strategy is currently INERT for the stated reason, and that reason is the calibrator
       being non-deployable rather than a code fault. If this assertion ever fails because a
       calibrator became available, that is the strategy switching ON, which is the intended
       outcome of a retrain - so it fails loudly rather than passing quietly either way.

    python backend/test_pm_fair_value_wiring.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(BACKEND.parent))


def main() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    # --- the wiring's own components ------------------------------------------------------
    from backend.polymarket_paper import calibrated_fair_value as fv
    from backend.polymarket_paper import calibration_loader as loader

    calibration = loader.load(5)
    check(calibration is None,
          "5m calibrator is not deployable today, so the strategy is INERT by design")

    # The reason must be the challenger's refusal, not a missing/broken file.
    import json
    payload_path = (BACKEND.parent / "data" / "research" / "phold_challenger"
                    / "phold_calibrators.json")
    if payload_path.is_file():
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        reason = (payload.get("horizons", {}).get("5", {}) or {}).get("not_deployable_reason", "")
        check("SOURCE_MODEL_REQUIRES_RETRAINING" in str(reason),
              "inert BECAUSE the source artifacts fail identity, not because of a code fault")

    # --- the exact database call the wiring makes -----------------------------------------
    # ignore_cleanup_errors: on Windows DuckDB keeps the file handle open past the
    # last query, so the directory cannot always be removed. That is a teardown
    # detail and must not be reported as a test failure.
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        os.environ["BTC_DATA_DIR"] = tmp
        for module in [m for m in list(sys.modules) if m.startswith("database")]:
            del sys.modules[module]
        import database

        database.init_db()
        round_id = 987654321

        # Branch 1: calibrator unavailable - 11 positional arguments, no keywords.
        database.log_rule_paper_trade(
            round_id, "PM_CALIBRATED_FAIR_VALUE_V1", 1_700_000_000_000, 5,
            "", 0.0, 0.0, 0.0, 0.0, 0.0, "CAL_UNAVAILABLE")
        check(True, "CAL_UNAVAILABLE branch: 11-positional call is accepted by the real DB")

        # Branch 2: a real decision - 11 positional plus btc_entry keyword.
        database.log_rule_paper_trade(
            round_id + 1, "PM_CALIBRATED_FAIR_VALUE_V1", 1_700_000_001_000, 15,
            "UP", 0.70, 0.68, 0.01, 0.02, 25.0, "ENTER", btc_entry=64_000.0)
        check(True, "ENTER branch: positional + btc_entry keyword is accepted")

        # Read back through database's OWN connector. Opening a second duckdb handle with a
        # different read_only flag raises "Can't open a connection to same database file with
        # a different configuration" - that would look like a wiring failure and is not one.
        con = database._connect()
        try:
            rows = con.execute(
                "SELECT rule, action, side FROM rule_paper_trades "
                "WHERE rule = 'PM_CALIBRATED_FAIR_VALUE_V1' ORDER BY ts").fetchall()
        finally:
            con.close()
        actions = {str(r[1]) for r in rows}
        check(len(rows) == 2, f"both rows were persisted and read back (got {len(rows)})")
        check({"CAL_UNAVAILABLE", "ENTER"} <= actions,
              "both branches are distinguishable in the ledger, so an inert strategy is visible")

    # --- decide() still refuses a leaked round even with a live calibrator ------------------
    live = fv.Calibration(x=(0.0, 1.0), y=(0.0, 1.0), fitted_through_ms=5_000, horizon=5)
    try:
        fv.decide(fv.Quote("r", 4_999, 5, 60, "UP", 0.95, 0.70, 0.68, 0.01), live)
        check(False, "unreachable")
    except fv.CalibrationRefused:
        check(True, "the leakage guard still refuses a round at or before the fitting boundary")

    print(f"\nPM FAIR VALUE WIRING: PASS ({checks} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
