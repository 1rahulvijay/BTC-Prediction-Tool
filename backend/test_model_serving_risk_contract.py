"""Serving risk inputs are name-bound and fail closed when incomplete."""
from __future__ import annotations

import numpy as np

import features
import model


def main() -> int:
    checks = 0

    def check(condition, label):
        nonlocal checks
        assert condition, label
        checks += 1
        print(f"  PASS  {label}")

    expected = {
        "spread_norm": model.SPREAD_FEATURE_INDEX,
        "vol_acceleration": model.VOL_ACCEL_FEATURE_INDEX,
        "ewma_vol": model.EWMA_VOL_FEATURE_INDEX,
        "vacuum_detected": model.VACUUM_FEATURE_INDEX,
    }
    check(all(features.FEATURE_NAMES[index] == name for name, index in expected.items()),
          "serving risk ordinals are derived from canonical feature names")

    seq = np.zeros((2, len(features.FEATURE_NAMES)), dtype=float)
    seq[-1, model.SPREAD_FEATURE_INDEX] = 0.2
    seq[-1, model.VOL_ACCEL_FEATURE_INDEX] = 0.1
    seq[-1, model.EWMA_VOL_FEATURE_INDEX] = 0.3
    values, reason = model._serving_risk_inputs(seq)
    check(reason is None and values["spread"] == 0.2,
          "complete finite feature rows produce measured risk inputs")

    values, reason = model._serving_risk_inputs(np.zeros((2, 20), dtype=float))
    check(values is None and reason == "risk_inputs_unavailable:IndexError",
          "short feature rows deny risk inputs instead of disabling controls")

    seq[-1, model.EWMA_VOL_FEATURE_INDEX] = np.nan
    values, reason = model._serving_risk_inputs(seq)
    check(values is None and reason == "risk_inputs_unavailable:ValueError",
          "non-finite feature rows deny risk inputs instead of fabricating values")

    print(f"\nMODEL SERVING RISK CONTRACT: PASS ({checks} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
