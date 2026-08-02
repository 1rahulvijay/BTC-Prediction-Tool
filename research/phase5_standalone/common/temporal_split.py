"""Four-way chronological split with purge boundaries."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

import numpy as np


@dataclass(frozen=True, slots=True)
class FourWaySplit:
    train: np.ndarray
    calibration: np.ndarray
    policy: np.ndarray
    test: np.ndarray
    boundaries: dict[str, int]


def _to_epoch_ms(value: str | None) -> int | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp() * 1000)
    if abs(number) < 1e11:
        number *= 1000.0
    return int(number)


def chronological_four_way_split(
    timestamps_ms: Iterable[int | float],
    *,
    purge_rows: int = 1,
    train_end: str | None = None,
    calibration_end: str | None = None,
    policy_end: str | None = None,
    test_end: str | None = None,
) -> FourWaySplit:
    ts = np.asarray(list(timestamps_ms), dtype=np.int64)
    if len(ts) < 40:
        raise ValueError("at least 40 chronological rows are required")
    if np.any(ts[1:] < ts[:-1]):
        raise ValueError("timestamps must be sorted chronologically")
    explicit = [_to_epoch_ms(v) for v in (train_end, calibration_end, policy_end, test_end)]
    if any(v is not None for v in explicit):
        if any(v is None for v in explicit):
            raise ValueError("all four explicit split endpoints must be provided together")
        assert all(v is not None for v in explicit)
        if not (explicit[0] < explicit[1] < explicit[2] < explicit[3]):
            raise ValueError("explicit split endpoints must increase strictly")
        cuts = [int(np.searchsorted(ts, value, side="right")) for value in explicit]
    else:
        n = len(ts)
        cuts = [int(n * 0.55), int(n * 0.70), int(n * 0.85), n]
    p = max(0, int(purge_rows))
    train = np.arange(0, max(0, cuts[0] - p))
    calibration = np.arange(min(len(ts), cuts[0] + p), max(cuts[0] + p, cuts[1] - p))
    policy = np.arange(min(len(ts), cuts[1] + p), max(cuts[1] + p, cuts[2] - p))
    test = np.arange(min(len(ts), cuts[2] + p), cuts[3])
    groups = (train, calibration, policy, test)
    if any(len(group) < 5 for group in groups):
        raise ValueError("split or purge leaves fewer than five rows in a partition")
    if max(train) >= min(calibration) or max(calibration) >= min(policy) or max(policy) >= min(test):
        raise AssertionError("temporal partitions overlap")
    return FourWaySplit(train, calibration, policy, test, {
        "train_end_ms": int(ts[train[-1]]),
        "calibration_end_ms": int(ts[calibration[-1]]),
        "policy_end_ms": int(ts[policy[-1]]),
        "test_end_ms": int(ts[test[-1]]),
        "purge_rows": p,
    })


def selftest() -> None:
    split = chronological_four_way_split(range(100), purge_rows=2)
    assert len(split.train) and len(split.test)
    assert max(split.train) < min(split.calibration) < min(split.policy) < min(split.test)
    try:
        chronological_four_way_split(range(20))
    except ValueError:
        pass
    else:
        raise AssertionError("small samples must be refused")
