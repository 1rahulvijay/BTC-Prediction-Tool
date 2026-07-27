from __future__ import annotations

import unittest

import numpy as np
import pandas as pd
from train_120d_trade_policy_heads import (
    build_side_labels,
    economic_metrics,
    make_expanding_folds,
)


class TradePolicyHeadTests(unittest.TestCase):
    def test_side_labels_apply_round_trip_cost_and_exact_horizon(self) -> None:
        timestamps = np.arange(8, dtype=np.int64) * 60_000
        close = np.array([100.0, 100.2, 99.8, 100.4, 100.1, 100.5, 100.0, 101.0])
        frame = pd.DataFrame({"ts_ms": timestamps, "close": close})
        labels = build_side_labels(frame, horizon=1, round_trip_cost_bps=10.0)

        expected_return = (100.2 / 100.0 - 1.0) * 10_000.0
        self.assertAlmostEqual(labels.loc[0, "long_net_bps"], expected_return - 10.0)
        self.assertEqual(labels.loc[0, "long_profitable"], 1.0)
        self.assertEqual(labels.loc[0, "short_profitable"], 0.0)
        self.assertFalse(bool(labels.loc[7, "valid"]))
        self.assertTrue(np.isnan(labels.loc[7, "long_profitable"]))

        frame.loc[4:, "ts_ms"] += 60_000
        labels_with_gap = build_side_labels(
            frame, horizon=1, round_trip_cost_bps=10.0
        )
        self.assertFalse(bool(labels_with_gap.loc[3, "valid"]))

    def test_expanding_folds_are_temporal_and_purged(self) -> None:
        timestamps = np.arange(12 * 1440, dtype=np.int64) * 60_000
        folds = make_expanding_folds(
            timestamps,
            folds=2,
            test_days=2,
            embargo_minutes=15,
        )
        self.assertEqual(len(folds), 2)
        for fold in folds:
            self.assertLess(
                int(timestamps[fold.train_idx[-1]]) + 15 * 60_000,
                int(timestamps[fold.test_idx[0]]),
            )
        self.assertGreater(len(folds[1].train_idx), len(folds[0].train_idx))

    def test_economic_metrics_use_only_acted_trades(self) -> None:
        timestamps = np.arange(6, dtype=np.int64) * 86_400_000
        pnl = np.array([10.0, -5.0, 8.0, -2.0, 4.0, -1.0])
        act = np.array([True, True, False, False, True, False])
        result = economic_metrics(timestamps, pnl, act)
        self.assertEqual(result["trades"], 3)
        self.assertAlmostEqual(result["coverage"], 0.5)
        self.assertAlmostEqual(result["mean_net_bps"], 3.0)
        self.assertAlmostEqual(result["total_net_bps"], 9.0)
        self.assertAlmostEqual(result["profit_factor"], 14.0 / 5.0)
        self.assertAlmostEqual(result["max_drawdown_bps"], 5.0)


if __name__ == "__main__":
    unittest.main()
