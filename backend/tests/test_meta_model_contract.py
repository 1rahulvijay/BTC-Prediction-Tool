"""Regression checks for the trust filter's economic target and release boundary."""
from __future__ import annotations

import runpy as _bootstrap_runpy
from pathlib import Path as _BootstrapPath

_bootstrap_runpy.run_path(str(_BootstrapPath(__file__).with_name("_bootstrap.py")))


import os
from pathlib import Path
import tempfile

import duckdb

from meta_model import TrainedMetaModel


DDL = """
CREATE TABLE predictions_5m (
    timestamp BIGINT, confidence DOUBLE, agreement DOUBLE, regime VARCHAR,
    ewma_vol DOUBLE, spread_norm DOUBLE, wall_imbalance DOUBLE,
    sr_compression DOUBLE, liq_imbalance DOUBLE, quantile_width_pct DOUBLE,
    quantile_asymmetry DOUBLE, quantile_spread DOUBLE, wf_accuracy DOUBLE,
    wf_accuracy_minus_0_5 DOUBLE, wf_fold_std DOUBLE, wf_sample_count DOUBLE,
    wf_age_minutes DOUBLE, tradeability DOUBLE, regime_score DOUBLE,
    liquidity_score DOUBLE, expected_edge DOUBLE, expectancy_usd DOUBLE,
    hit BOOLEAN, binance_price DOUBLE, endpoint_move DOUBLE,
    expected_slippage_usd DOUBLE, signal VARCHAR, raw_direction VARCHAR,
    release_id VARCHAR, target_contract VARCHAR, endpoint_price_basis VARCHAR,
    resolved BOOLEAN
)
"""


def main() -> int:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "meta.duckdb"
        conn = duckdb.connect(str(path))
        conn.execute(DDL)
        rows = []
        # One row per minute gives the timestamp purge a visible five-row gap. Half the rows
        # are a different release and deliberately more numerous; they must never enter the
        # fitted sample count.
        for release, count in (("release-under-test", 140), ("other-release", 180)):
            for index in range(count):
                endpoint_move = 150.0 if index % 2 == 0 else -150.0
                rows.append((
                    1_700_000_000_000 + index * 60_000,
                    0.70, 0.65, "RANGE", 0.01, 0.0001, 0.0, 0.0, 0.0,
                    0.01, 0.0, 0.01, 0.55, 0.05, 0.02, 100.0, 1.0,
                    0.8, 0.7, 0.8, 0.01, 10.0, index % 2 == 0,
                    100_000.0, endpoint_move, 0.0, "UP", "UP", release,
                    "first_touch_triple_barrier_v1", "ENDPOINT", True,
                ))
        conn.executemany(
            "INSERT INTO predictions_5m VALUES (" + ",".join(["?"] * 32) + ")",
            rows,
        )
        conn.close()

        meta = TrainedMetaModel()
        result = meta.train(
            str(path),
            5,
            release_id="release-under-test",
            target_contract="first_touch_triple_barrier_v1",
        )
        assert result.startswith("trained on 140 samples"), result
        assert meta.is_trained and meta.n_samples == 140
        assert meta.release_id == "release-under-test"
        assert meta.target_definition == "counterfactual_endpoint_net_after_decision_cost"

        old_evidence = os.environ.get("BTC_EVIDENCE_MODE")
        os.environ["BTC_EVIDENCE_MODE"] = "1"
        try:
            blocked = TrainedMetaModel()
            message = blocked.train(
                str(path),
                5,
                release_id="release-under-test",
                target_contract="first_touch_triple_barrier_v1",
                forward_status={
                    "forward_evidence": "DARK",
                    "banner": "test recorder is dark",
                },
            )
            assert "refused" in message and not blocked.is_trained, message
        finally:
            if old_evidence is None:
                os.environ.pop("BTC_EVIDENCE_MODE", None)
            else:
                os.environ["BTC_EVIDENCE_MODE"] = old_evidence

        class BrokenModel:
            def predict_proba(self, _features):
                raise RuntimeError("forced inference failure")

        meta.model = BrokenModel()
        assert meta.should_execute({"regime": "RANGE"}) == (False, 0.0)

    print("meta-model contract: ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
