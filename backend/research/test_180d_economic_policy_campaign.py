from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd
from train_180d_economic_policy_campaign import (
    Boundaries,
    Config,
    PolicySpec,
    apply_policy,
    benjamini_hochberg,
    checkpoint_rows,
    day_block_stats,
    locked_model_diagnostics,
    make_boundaries,
    policy_catalog,
    shadow_gate,
)


def fixture_frame() -> pd.DataFrame:
    timestamps = pd.date_range("2026-01-01", periods=120, freq="6h", tz="UTC")
    gross = np.tile([20.0, -20.0, 4.0, -4.0], 30)
    return pd.DataFrame(
        {
            "timestamp_ms": timestamps.astype("int64") // 1_000_000,
            "gross_return_bps": gross,
            "long_net_bps": gross - 12.0,
            "short_net_bps": -gross - 12.0,
            "p_long_ensemble": np.where(gross > 0, 0.8, 0.2),
            "p_short_ensemble": np.where(gross < 0, 0.8, 0.2),
            "mean_long_ensemble": gross - 12.0,
            "mean_short_ensemble": -gross - 12.0,
            "q20_long_ensemble": gross - 14.0,
            "q20_short_ensemble": -gross - 14.0,
            "p_act_logreg": np.where(np.abs(gross) > 12, 0.8, 0.2),
        }
    )


class EconomicCampaignTests(unittest.TestCase):
    def test_split_boundaries_cover_window_exactly(self) -> None:
        start = pd.Timestamp("2025-01-01", tz="UTC").value // 1_000_000
        frame = pd.DataFrame(
            {"ts_ms": start + np.arange(180 * 1_440, dtype=np.int64) * 60_000}
        )
        config = Config(
            matrix="fixture",
            horizons=[5, 15],
            window_days=180,
            base_train_days=120,
            meta_train_days=15,
            selection_days=15,
            locked_test_days=30,
            fee_bps_per_side=5.0,
            slippage_bps_per_side=1.0,
            max_features=80,
            max_train_rows=0,
            threads=1,
            classifier_families=["logreg"],
            regressor_families=["ridge"],
            quantile_families=["histgb"],
            meta_families=["logreg"],
            run_name="fixture",
        )
        bounds = make_boundaries(frame, config)
        self.assertIsInstance(bounds, Boundaries)
        self.assertEqual(bounds.train_end_ms - bounds.start_ms, 120 * 86_400_000)
        self.assertEqual(bounds.test_end_ms - bounds.selection_end_ms, 30 * 86_400_000)
        self.assertEqual(
            bounds.test_end_ms,
            int(frame.ts_ms.iloc[-1]) + 60_000,
        )

    def test_catalog_is_finite_and_contains_side_specialists(self) -> None:
        catalog = policy_catalog(["ensemble"], ["ensemble"], ["ensemble"], ["logreg"])
        self.assertEqual(len(catalog), 69)
        modes = {spec.side_mode for spec in catalog}
        self.assertEqual(modes, {"BOTH", "LONG", "SHORT"})

    def test_long_and_short_policy_accounting(self) -> None:
        frame = fixture_frame()
        long_spec = PolicySpec("probability", "ensemble", 0.6, 0.1, "LONG")
        short_spec = PolicySpec("probability", "ensemble", 0.6, 0.1, "SHORT")
        long_result = apply_policy(frame, long_spec)
        short_result = apply_policy(frame, short_spec)
        self.assertTrue((long_result.loc[long_result.act, "side"] == "LONG").all())
        self.assertTrue((short_result.loc[short_result.act, "side"] == "SHORT").all())
        np.testing.assert_allclose(
            long_result.loc[long_result.act, "net_bps"],
            frame.loc[long_result.act, "long_net_bps"],
        )
        np.testing.assert_allclose(
            short_result.loc[short_result.act, "net_bps"],
            frame.loc[short_result.act, "short_net_bps"],
        )

    def test_block_interval_requires_real_day_diversity(self) -> None:
        frame = fixture_frame()
        act = np.ones(len(frame), dtype=bool)
        result = day_block_stats(
            frame.timestamp_ms.to_numpy(),
            np.full(len(frame), 2.0),
            act,
            draws=500,
        )
        self.assertGreater(result["lower"], 0.0)
        self.assertLess(result["p_value"], 0.01)

    def test_bh_adjustment_is_monotone(self) -> None:
        adjusted = benjamini_hochberg({"5": 0.01, "15": 0.08})
        self.assertAlmostEqual(adjusted["5"], 0.02)
        self.assertAlmostEqual(adjusted["15"], 0.08)

    def test_shadow_gate_fails_closed_on_small_sample(self) -> None:
        frame = fixture_frame().iloc[:20].copy()
        spec = PolicySpec("probability", "ensemble", 0.6, 0.1, "BOTH")
        result = shadow_gate(frame, spec, stress_extra_bps=1.0)
        self.assertFalse(result["historical_shadow_candidate"])
        self.assertFalse(result["checks"]["trades_ge_100"])
        self.assertTrue(math.isfinite(result["metrics"]["mean_net_bps"]))

    def test_checkpoint_target_uses_only_remaining_path(self) -> None:
        start = pd.Timestamp("2026-01-01", tz="UTC").value // 1_000_000
        close = np.array([100.0, 101.0, 102.0, 101.0, 103.0, 104.0])
        frame = pd.DataFrame(
            {
                "ts_ms": start + np.arange(6, dtype=np.int64) * 60_000,
                "close": close,
                "high": close + 0.5,
                "low": close - 0.5,
            }
        )
        features = pd.DataFrame(index=frame.index)
        entry = pd.DataFrame(
            {
                "row_index": [0],
                "timestamp_ms": [start],
                "policy_side": ["LONG"],
                "policy_net_bps": [1_028.0],
                "p_long_ensemble": [0.7],
                "p_short_ensemble": [0.3],
                "mean_long_ensemble": [4.0],
                "mean_short_ensemble": [-8.0],
                "q20_long_ensemble": [1.0],
                "q20_short_ensemble": [-12.0],
            }
        )
        rows = checkpoint_rows(entry, frame, features, horizon=5)
        first = rows.iloc[0]
        self.assertEqual(first.elapsed_minutes, 1)
        self.assertAlmostEqual(first.current_signed_return_bps, 100.0)
        self.assertAlmostEqual(
            first.remaining_signed_return_bps,
            (104.0 / 101.0 - 1.0) * 10_000.0,
        )
        self.assertEqual(len(rows), 4)

    def test_locked_diagnostics_cover_all_head_types(self) -> None:
        frame = fixture_frame()
        frame["horizon"] = 5
        frame["p_long_disagreement"] = 0.1
        diagnostics = locked_model_diagnostics(frame)
        self.assertEqual(
            set(diagnostics.layer),
            {
                "economic_classifier",
                "expected_net_regression",
                "q20_net",
                "act_skip",
            },
        )
        classifier_models = diagnostics[
            diagnostics.layer == "economic_classifier"
        ].model
        self.assertNotIn("disagreement", set(classifier_models))


if __name__ == "__main__":
    unittest.main()
