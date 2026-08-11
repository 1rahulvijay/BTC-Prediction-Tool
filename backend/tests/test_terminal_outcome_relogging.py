"""Re-logging an ID must never erase its resolved evidence."""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import os
import tempfile


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["BTC_DATA_DIR"] = tmp
        os.environ["BTC_DB_PATH"] = os.path.join(tmp, "analytics.duckdb")
        import database as db

        db.init_db()
        try:

            db.log_kronos_prediction("k", 1, 5, 100.0, 101.0, "UP", 2)
            db.resolve_kronos_prediction("k", 102.0, "UP", True)
            db.log_kronos_prediction("k", 9, 5, 90.0, 80.0, "DOWN", 10)

            db.log_model_prediction("m", "xgb", 1, 5, 100.0, "UP", 2)
            db.resolve_model_prediction("m", 102.0, "UP", True)
            db.log_model_prediction("m", "xgb", 9, 5, 90.0, "DOWN", 10)

            fsr = {
                "id": "f", "prediction_id": "parent", "timestamp": 1, "horizon": 5,
                "price": 100.0, "action": "HOLD", "side": "UP", "size_fraction": 0.1,
                "confidence": 0.6, "expected_reward_usd": 1.0, "reason": "test",
                "risk_note": "paper", "fsr": {}, "state": {}, "verify_at": 2,
            }
            db.log_fsr_ppo_decision(fsr)
            with db._connect() as con:
                con.execute("""UPDATE fsr_ppo_decisions SET actual_price=102,
                             actual_direction='UP', reward_usd=1, hit=TRUE, resolved=TRUE,
                             resolution_status='RESOLVED' WHERE id='f'""")
            db.log_fsr_ppo_decision({**fsr, "timestamp": 9, "action": "SELL"})

            db.log_ab_prediction("primary", "p", 1, 5, "UP", 0.6)
            db.resolve_ab_results("p", "UP")
            db.log_ab_prediction("primary", "p", 9, 5, "DOWN", 0.9)

            with db._connect() as con:
                k = con.execute("""SELECT actual_price, actual_direction, hit, resolved,
                                  resolution_status FROM kronos_predictions WHERE id='k'""").fetchone()
                m = con.execute("""SELECT actual_price, actual_direction, hit, resolved,
                                  resolution_status FROM model_predictions WHERE id='m'""").fetchone()
                f = con.execute("""SELECT actual_price, actual_direction, hit, resolved,
                                  resolution_status FROM fsr_ppo_decisions WHERE id='f'""").fetchone()
                a = con.execute("""SELECT actual_direction, hit, resolved, resolution_status
                                  FROM ab_results WHERE id='primary_p'""").fetchone()
            assert k == (102.0, "UP", True, True, "RESOLVED"), k
            assert m == (102.0, "UP", True, True, "RESOLVED"), m
            assert f == (102.0, "UP", True, True, "RESOLVED"), f
            assert a == ("UP", True, True, "RESOLVED"), a
        finally:
            db.close_db()

    print("terminal-outcome-relogging: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
