"""Shared leakage and ordering safeguards for production quantile heads."""
from __future__ import annotations

import math

import numpy as np


def purged_train_cal_test_slices(
    n: int,
    horizon: int,
    train_fraction: float = 0.98,
) -> tuple[slice, slice, slice]:
    """Return chronological train/calibration/test slices separated by horizon purges.

    ``train_fraction`` describes the older train+calibration population. The newest
    ``1-train_fraction`` is an untouched test set and an equally sized block immediately
    before it is reserved for calibration. Purges prevent a forward label in one block from
    reaching into the next block.
    """
    n = int(n)
    horizon = max(1, int(horizon))
    fraction = min(max(float(train_fraction), 0.50), 0.98)
    holdout = max(1, int(math.ceil(n * (1.0 - fraction))))
    test_start = n - holdout
    cal_end = test_start - horizon
    cal_start = cal_end - holdout
    train_end = cal_start - horizon
    if train_end < 100 or cal_start < 0 or cal_end <= cal_start or test_start >= n:
        raise ValueError(
            f"not enough rows for purged train/cal/test split: n={n}, horizon={horizon}, "
            f"holdout={holdout}"
        )
    return slice(0, train_end), slice(cal_start, cal_end), slice(test_start, n)


def purged_train_test_slices(
    n: int,
    horizon: int,
    train_fraction: float = 0.98,
) -> tuple[slice, slice]:
    """Return chronological train/test slices with one forward-label purge."""
    n = int(n)
    horizon = max(1, int(horizon))
    fraction = min(max(float(train_fraction), 0.50), 0.98)
    holdout = max(1, int(math.ceil(n * (1.0 - fraction))))
    test_start = n - holdout
    train_end = test_start - horizon
    if train_end < 100 or test_start >= n:
        raise ValueError(
            f"not enough rows for purged train/test split: n={n}, horizon={horizon}, "
            f"holdout={holdout}"
        )
    return slice(0, train_end), slice(test_start, n)


def conformal_adjustment(scores, coverage: float = 0.80) -> float:
    """Finite-sample conformal quantile using the conservative 'higher' order statistic."""
    values = np.asarray(scores, dtype=float)
    values = values[np.isfinite(values)]
    if not len(values):
        raise ValueError("cannot calibrate an empty conformity-score array")
    coverage = min(max(float(coverage), 0.0), 1.0)
    rank = min(len(values), max(1, int(math.ceil((len(values) + 1) * coverage))))
    return float(np.partition(values, rank - 1)[rank - 1])


def monotone_quantiles(*predictions) -> tuple[np.ndarray, ...]:
    """Project independent quantile predictions onto an ordered per-row sequence."""
    if not predictions:
        return ()
    arrays = [np.asarray(p, dtype=float).reshape(-1) for p in predictions]
    if len({len(a) for a in arrays}) != 1:
        raise ValueError("quantile predictions must have equal lengths")
    ordered = np.sort(np.column_stack(arrays), axis=1)
    return tuple(ordered[:, i] for i in range(ordered.shape[1]))


def selftest() -> int:
    tr, ca, te = purged_train_cal_test_slices(10_000, 15, 0.98)
    assert tr.stop + 15 == ca.start
    assert ca.stop + 15 == te.start
    assert te.stop == 10_000
    assert len(range(ca.start, ca.stop)) == len(range(te.start, te.stop)) == 201

    tr2, te2 = purged_train_test_slices(10_000, 15, 0.98)
    assert tr2.stop + 15 == te2.start
    assert te2.stop == 10_000
    assert len(range(te2.start, te2.stop)) == 201

    adjustment = conformal_adjustment([0.0, 1.0, 2.0, 3.0], 0.80)
    assert adjustment == 3.0

    q10, q50, q90 = monotone_quantiles([3, -1], [1, 5], [2, 0])
    assert np.array_equal(q10, [1, -1])
    assert np.array_equal(q50, [2, 0])
    assert np.array_equal(q90, [3, 5])
    print("quantile-safety: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(selftest())
