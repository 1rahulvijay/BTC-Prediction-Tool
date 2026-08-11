"""Regression test for durable per-model metrics and restart semantics."""

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import os
import tempfile

import duckdb

import database
from model_verifier import PerModelVerifier
import model_metrics_logger
from prediction_verifier import PredictionVerifier


def _prediction(parent_id: str, timestamp: int) -> dict:
    return {
        "id": parent_id,
        "horizon": 5,
        "predicted_price": 100.0,
        "timestamp": timestamp,
        "verify_at": timestamp + 300_000,
        "model_dirs": {"xgb": 1, "lgb": 2},
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        database.close_db()
        original_path = database.DB_PATH
        database.DB_PATH = os.path.join(tmp, "analytics.duckdb")
        metrics_path = os.path.join(tmp, "model_metrics.duckdb")
        try:
            database.init_db()
            parent_id = "pred_5m_1000000"
            timestamp = 1_000_000
            verifier = PerModelVerifier(horizons=(5, 15))

            # These are the two production write paths.  They must converge on two
            # canonical child rows, not create four observations for two model votes.
            verifier.record(
                {"xgb": 1, "lgb": 2}, 5, 100.0, timestamp,
                prediction_id=parent_id,
            )
            database.log_prediction(
                # Required provenance: a row that cannot say which question it
                # answers is refused by the writer (P0-14).
                target_contract="first_touch_triple_barrier_v1",
                release_id="test_bundle",
                pred_id=parent_id,
                timestamp=timestamp,
                horizon=5,
                binance_price=100.0,
                target_price=101.0,
                expected_move=1.0,
                confidence=0.6,
                signal="UP",
                chainlink_price=100.0,
                chainlink_target=101.0,
                model_dirs={"xgb": 1, "lgb": 2},
                verify_at=timestamp + 300_000,
            )
            conn = duckdb.connect(database.DB_PATH)
            child_ids = [row[0] for row in conn.execute(
                "SELECT id FROM model_predictions ORDER BY id"
            ).fetchall()]
            conn.close()
            assert child_ids == [f"{parent_id}::lgb", f"{parent_id}::xgb"], child_ids

            # WITHOUT the contract's direction, main resolution grades NOTHING.
            #
            # It used to compute `"UP" if actual_move >= 0 else "DOWN"` and grade every seat
            # vote against it - the endpoint rule applied to a first-touch model, three lines
            # under a docstring naming that exact defect. And `actual_move` is
            # `resolution_price - entry`, which under first touch is the BARRIER, so that
            # "endpoint sign" was the barrier side on touching rows and the closing residual
            # on timeouts. `model_verifier` already grades these rows through `tc.grade()`
            # under each vote's declared contract, so two writers shared one column and the
            # later one won.
            database.update_outcome(
                parent_id, 5, actual_price=102.0, actual_move=2.0,
                hit=True, price_match=False, move_error=1.0, lean_hit=True,
            )
            conn = duckdb.connect(database.DB_PATH)
            rows = conn.execute(
                "SELECT model, direction, hit, resolved FROM model_predictions ORDER BY model"
            ).fetchall()
            assert rows == [
                ("lgb", "UP", None, False),
                ("xgb", "NEUTRAL", None, False),
            ], rows
            assert all(r[2] is None for r in rows), (
                "a positive actual_move must not grade a seat vote: the contract decides, "
                f"and it was not supplied. {rows}")

            # WITH the contract's direction, it grades committed UP/DOWN votes only. NEUTRAL
            # is an abstention and remains NULL rather than becoming a false miss.
            database.update_outcome(
                parent_id, 5, actual_price=102.0, actual_move=2.0,
                hit=True, price_match=False, move_error=1.0, lean_hit=True,
                actual_direction="UP",
            )
            rows = conn.execute(
                "SELECT model, direction, hit, resolved FROM model_predictions ORDER BY model"
            ).fetchall()
            assert rows == [
                ("lgb", "UP", True, True),
                ("xgb", "NEUTRAL", None, True),
            ], rows

            # And a row the CONTRACT grader already resolved is never re-graded: a second
            # pass with the opposite direction must not overwrite it.
            database.update_outcome(
                parent_id, 5, actual_price=98.0, actual_move=-2.0,
                hit=False, price_match=False, move_error=1.0, lean_hit=False,
                actual_direction="DOWN",
            )
            rows = conn.execute(
                "SELECT model, direction, hit, resolved FROM model_predictions ORDER BY model"
            ).fetchall()
            assert rows == [
                ("lgb", "UP", True, True),
                ("xgb", "NEUTRAL", None, True),
            ], f"an already-resolved seat vote was re-graded: {rows}"

            # That probe also rewrote the PARENT row, which has no such guard - restore it,
            # because the assertions below read the parent's accuracy. The seat rows are
            # already resolved so this pass leaves them untouched, which is the point.
            database.update_outcome(
                parent_id, 5, actual_price=102.0, actual_move=2.0,
                hit=True, price_match=False, move_error=1.0, lean_hit=True,
                actual_direction="UP",
            )

            # Simulate one legacy duplicate from the historical dual-write period.
            conn.execute("""
                INSERT INTO model_predictions
                (id, model, timestamp, horizon, ref_price, direction, verify_at,
                 actual_price, actual_direction, hit, resolved)
                VALUES (?, 'lgb', ?, 5, 100.0, 'UP', ?, 102.0, 'UP', TRUE, TRUE)
            """, ("lgb_5m_1000000", timestamp, timestamp + 300_000))
            conn.close()

            accuracy = database.fetch_model_accuracy()
            assert accuracy["lgb"][5] == {"total": 1, "hits": 1, "accuracy": 1.0}
            assert "xgb" not in accuracy or 5 not in accuracy["xgb"]
            history = database.fetch_model_verifier_history(500)
            assert len(history) == 1 and history[0]["model"] == "lgb" and history[0]["hit"]

            restored = PerModelVerifier(horizons=(5, 15))
            state = restored.restore_from_database(
                [_prediction("pending_5m_2000000", 2_000_000)], history
            )
            assert state == {"resolved": 1, "pending": 2}, state
            assert restored.accuracy()["lgb"][5]["accuracy"] == 1.0
            assert {item["id"] for item in restored.pending} == {
                "pending_5m_2000000::xgb", "pending_5m_2000000::lgb"
            }

            verified_history = database.fetch_prediction_verifier_history(500)
            assert len(verified_history) == 1 and verified_history[0]["id"] == parent_id
            main_verifier = PredictionVerifier(max_history_per_horizon=500)
            assert main_verifier.restore_verified_from_database(verified_history) == 1
            main_accuracy = main_verifier.get_accuracy_summary()[5]
            assert main_accuracy["total"] == 1 and main_accuracy["hits"] == 1

            # A restart may restore a prediction only while its original deadline is
            # still ahead.  Missing that boundary makes the result unknowable.
            import time
            now_ms = int(time.time() * 1000)
            for suffix, verify_at in (("expired", now_ms - 1_000), ("future", now_ms + 300_000)):
                database.log_prediction(
                # Required provenance: a row that cannot say which question it
                # answers is refused by the writer (P0-14).
                target_contract="first_touch_triple_barrier_v1",
                release_id="test_bundle",
                    pred_id=f"pred_{suffix}", timestamp=now_ms - 10_000, horizon=5,
                    binance_price=100.0, target_price=101.0, expected_move=1.0,
                    confidence=0.6, signal="UP", chainlink_price=100.0,
                    chainlink_target=101.0, model_dirs={"lgb": 2}, verify_at=verify_at,
                )
            pending = database.fetch_unresolved_predictions()
            assert [row["id"] for row in pending] == ["pred_future"], pending
            cleanup = database.cleanup_orphan_pending_rows()
            assert cleanup["predictions_5m_invalidated"] == 1, cleanup
            conn = duckdb.connect(database.DB_PATH)
            expired_state = conn.execute("""
                SELECT resolved,resolution_status,invalid_reason
                FROM predictions_5m WHERE id='pred_expired'
            """).fetchone()
            future_state = conn.execute("""
                SELECT resolution_status FROM predictions_5m WHERE id='pred_future'
            """).fetchone()[0]
            conn.close()
            assert expired_state == (False, "INVALID", "RESTART_MISSED_BOUNDARY"), expired_state
            assert future_state == "PENDING", future_state

            head_identity = {"p_hold": {"sha256": "abc123", "version": "test-v1"}}
            snapshot = {
                "id": "round-1", "horizon": 5, "seconds_left": 60,
                "current_position": "UP", "current_move": 12.0,
                "p_hold": 0.8, "head_identity": head_identity,
                "champion": {"action": "WAIT", "confidence": 0.7, "label": "WATCH"},
                "round_state": {
                    "horizon": 5, "seconds_left": 60, "leader": "UP",
                    "leader_move_usd": 12.0, "p_leader_holds": 0.8,
                    "flip_risk": {"probability": 0.2, "status": "available"},
                    "late_shock": {}, "next_three_rounds": {},
                    "execution": {"status": "NO_EDGE"}, "action": "WAIT",
                },
            }
            database.log_champion_snapshot(snapshot, now_ms)
            assert database.log_round_state_snapshot(snapshot, now_ms)
            conn = duckdb.connect(database.DB_PATH)
            champion_identity = conn.execute(
                "SELECT head_identity_json FROM champion_snapshots WHERE round_id='round-1'"
            ).fetchone()[0]
            round_identity = conn.execute(
                "SELECT head_identity_json FROM round_state_snapshots WHERE round_id='round-1'"
            ).fetchone()[0]
            conn.close()
            assert '"sha256": "abc123"' in champion_identity
            assert champion_identity == round_identity

            # The server passes the full regime object.  The dedicated metrics DB
            # must store its label, not a stringified dictionary.
            model_metrics_logger.close()
            model_metrics_logger._STATE.update({
                "path": None, "failed": False, "last_error": "", "last_write_ms": 0,
            })
            wrote = model_metrics_logger.log_direction(
                [{"horizon": 5, "direction": "UP", "probUp": 0.6}],
                regime={"regime": "RANGE", "confidence": 0.75},
                ts_ms=3_000_000,
                path=metrics_path,
            )
            assert wrote is True
            model_metrics_logger.close()
            conn = duckdb.connect(metrics_path)
            stored_regime = conn.execute(
                "SELECT regime FROM direction_log"
            ).fetchone()[0]
            conn.close()
            assert stored_regime == "RANGE", stored_regime
        finally:
            model_metrics_logger.close()
            database.close_db()
            database.DB_PATH = original_path

    print("model metrics integrity: ALL PASS")


if __name__ == "__main__":
    main()
