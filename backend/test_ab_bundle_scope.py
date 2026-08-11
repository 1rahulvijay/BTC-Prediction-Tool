"""A replacement A/B model may not inherit a reused label's outcomes or economics."""

from __future__ import annotations

import os
import tempfile


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["BTC_DATA_DIR"] = tmp
        os.environ["BTC_DB_PATH"] = os.path.join(tmp, "analytics.duckdb")
        import database as db

        db.init_db()
        try:
            with db._connect() as con:
                con.execute("""INSERT INTO predictions_5m
                    (id, timestamp, horizon, binance_price, actual_move, resolved)
                    VALUES ('old', 1000, 5, 100.0, -10.0, TRUE),
                           ('new', 2000, 5, 100.0,  10.0, TRUE)""")

            for pred_id, timestamp, bundle, primary_direction, challenger_direction in (
                ("old", 1000, "old_bundle", "UP", "DOWN"),
                ("new", 2000, "new_bundle", "DOWN", "UP"),
            ):
                db.log_ab_prediction(
                    "primary", pred_id, timestamp, 5, primary_direction, 0.6,
                    model_bundle_id=f"primary_{bundle}",
                )
                db.log_ab_prediction(
                    "challenger", pred_id, timestamp, 5, challenger_direction, 0.6,
                    model_bundle_id=bundle,
                )
                actual = "DOWN" if pred_id == "old" else "UP"
                db.resolve_ab_results(pred_id, actual)

            paired = db.fetch_ab_paired_outcomes(
                "primary", "challenger", "primary_new_bundle", "new_bundle"
            )
            assert paired == [(2000, False, True)], paired

            profit = db.fetch_ab_variant_profit_stats("challenger", "new_bundle")
            assert set(profit) == {"challenger"}, profit
            row = profit["challenger"]
            assert row["trades"] == 1 and row["expectancy_usd"] > 0, row
        finally:
            db.close_db()

    print("ab-bundle-scope: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
