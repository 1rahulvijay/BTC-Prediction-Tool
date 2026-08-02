"""Matched random and placebo controls that preserve exposure mechanics."""
from __future__ import annotations

import numpy as np


def matched_random_actions(
    actions: np.ndarray,
    *,
    timestamps_ms: np.ndarray,
    seed: int,
) -> np.ndarray:
    """Shuffle actions within UTC day, preserving count and long/short balance."""
    actions = np.asarray(actions).copy()
    ts = np.asarray(timestamps_ms, dtype=np.int64)
    if len(actions) != len(ts):
        raise ValueError("actions and timestamps must align")
    rng = np.random.default_rng(seed)
    result = actions.copy()
    for day in np.unique(ts // 86_400_000):
        idx = np.flatnonzero(ts // 86_400_000 == day)
        result[idx] = rng.permutation(actions[idx])
    return result


def sign_shuffled(actions: np.ndarray, *, seed: int) -> np.ndarray:
    values = np.asarray(actions, dtype=float).copy()
    active = np.flatnonzero(values != 0)
    rng = np.random.default_rng(seed)
    values[active] *= rng.choice(np.array([-1.0, 1.0]), size=len(active))
    return values


def time_shifted(values: np.ndarray, rows: int) -> np.ndarray:
    data = np.asarray(values)
    result = np.full(data.shape, np.nan, dtype=float)
    if rows == 0:
        return data.astype(float, copy=True)
    if abs(rows) >= len(data):
        return result
    if rows > 0:
        result[rows:] = data[:-rows]
    else:
        result[:rows] = data[-rows:]
    return result


def selftest() -> None:
    ts = np.array([1, 2, 3, 86_400_001, 86_400_002]) * 1000
    actions = np.array([1, -1, 0, 1, 0])
    shuffled = matched_random_actions(actions, timestamps_ms=ts, seed=7)
    for day in np.unique(ts // 86_400_000):
        idx = ts // 86_400_000 == day
        assert sorted(shuffled[idx]) == sorted(actions[idx])
    assert np.isnan(time_shifted(np.arange(5), 1)[0])

