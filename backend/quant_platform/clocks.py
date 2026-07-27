"""Injectable clocks for deterministic replay and live orchestration."""
from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Protocol


class Clock(Protocol):
    def time_ns(self) -> int: ...

    def monotonic_ns(self) -> int: ...


class SystemClock:
    def time_ns(self) -> int:
        return time.time_ns()

    def monotonic_ns(self) -> int:
        return time.monotonic_ns()


@dataclass
class ManualClock:
    wall_ns: int
    mono_ns: int = 0

    def time_ns(self) -> int:
        return self.wall_ns

    def monotonic_ns(self) -> int:
        return self.mono_ns

    def advance(self, seconds: float) -> None:
        delta = int(seconds * 1_000_000_000)
        if delta < 0:
            raise ValueError("manual clock cannot move backwards")
        self.wall_ns += delta
        self.mono_ns += delta
