from __future__ import annotations

import unittest

import numpy as np
import pandas as pd
from train_120d_conditional_ev_pipeline import (
    json_safe,
    normalize_quantiles,
    policy_columns,
    promotion_result,
)


class ConditionalEVTests(unittest.TestCase):
    def test_manifest_serialization_preserves_booleans(self) -> None:
        result = json_safe({"pass": True, "fail": np.bool_(False), "value": np.nan})
        self.assertIs(result["pass"], True)
        self.assertIs(result["fail"], False)
        self.assertIsNone(result["value"])

    def test_quantiles_are_ordered_and_crossing_reported(self) -> None:
        q10, q50, q90, crossing = normalize_quantiles(
            np.array([5.0, -2.0]),
            np.array([1.0, 0.0]),
            np.array([3.0, 2.0]),
        )
        self.assertEqual(crossing, 0.5)
        np.testing.assert_array_equal(q10, np.array([1.0, -2.0]))
        np.testing.assert_array_equal(q50, np.array([3.0, 0.0]))
        np.testing.assert_array_equal(q90, np.array([5.0, 2.0]))

    def test_primary_policy_uses_adverse_quantile(self) -> None:
        frame = pd.DataFrame(
            {
                "p_move": [0.8, 0.8, 0.8, 0.4],
                "p_up_given_move": [0.8, 0.2, 0.8, 0.8],
                "q10_return_bps": [15.0, -30.0, 5.0, 20.0],
                "q50_return_bps": [20.0, -20.0, 20.0, 25.0],
                "q90_return_bps": [30.0, -15.0, 30.0, 35.0],
                "mean_return_bps": [20.0, -20.0, 20.0, 25.0],
                "long_net_bps": [8.0, -22.0, 8.0, 8.0],
                "short_net_bps": [-32.0, 8.0, -32.0, -32.0],
                "gross_return_bps": [20.0, -20.0, 20.0, 20.0],
            }
        )
        out = policy_columns(frame, cost_bps=12.0)
        self.assertEqual(
            out["act_primary_q10"].tolist(),
            [True, True, False, False],
        )

    def test_promotion_fails_closed_without_enough_trades(self) -> None:
        count = 20
        pooled = pd.DataFrame(
            {
                "timestamp_ms": np.arange(count) * 86_400_000,
                "candidate_net_bps": np.full(count, 5.0),
                "act_primary_q10": np.ones(count, dtype=bool),
            }
        )
        folds = [
            {"policy": "primary_q10", "mean_net_bps": 5.0}
            for _ in range(4)
        ]
        result = promotion_result(pooled, folds, stress_extra_bps=1.0)
        self.assertFalse(result["promote"])
        self.assertFalse(result["checks"]["trades_ge_200"])
        self.assertTrue(result["checks"]["mean_net_positive"])


if __name__ == "__main__":
    unittest.main()
