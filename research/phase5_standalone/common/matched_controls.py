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


def matched_nonoverlapping_random_actions(
    actions: np.ndarray,
    *,
    timestamps_ms: np.ndarray,
    holding_seconds: int,
    seed: int,
) -> np.ndarray:
    """Randomize entry times within day while exactly preserving action count and signs."""
    original = np.asarray(actions).copy()
    ts = np.asarray(timestamps_ms, dtype=np.int64)
    if len(original) != len(ts):
        raise ValueError("actions and timestamps must align")
    result = np.zeros_like(original)
    rng = np.random.default_rng(seed)
    gap = int(holding_seconds) * 1000
    for day in np.unique(ts // 86_400_000):
        day_idx = np.flatnonzero(ts // 86_400_000 == day)
        signs = original[day_idx][original[day_idx] != 0]
        if not len(signs):
            continue
        selected: list[int] = []
        for candidate in rng.permutation(day_idx):
            stamp = ts[candidate]
            if all(abs(int(stamp) - int(ts[chosen])) >= gap for chosen in selected):
                selected.append(int(candidate))
                if len(selected) == len(signs):
                    break
        if len(selected) != len(signs):
            # The observed action timestamps are a guaranteed feasible same-day schedule.
            selected = list(day_idx[original[day_idx] != 0])
        shuffled_signs = rng.permutation(signs)
        result[np.asarray(selected, dtype=int)] = shuffled_signs
    if sorted(result[result != 0].tolist()) != sorted(original[original != 0].tolist()):
        raise AssertionError("matched control changed action count or sign balance")
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
    dense_ts = np.arange(60) * 60_000 + 1_700_000_000_000
    sparse = np.zeros(60, dtype=int)
    sparse[[0, 5, 10, 20]] = [1, -1, 1, -1]
    randomized = matched_nonoverlapping_random_actions(
        sparse, timestamps_ms=dense_ts, holding_seconds=300, seed=9)
    assert sorted(randomized[randomized != 0]) == sorted(sparse[sparse != 0])
    chosen = np.flatnonzero(randomized)
    assert np.all(np.diff(dense_ts[chosen]) >= 300_000)
