"""Fail-closed service orchestration and global kill state."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import RLock
import time


@dataclass(slots=True)
class ServiceState:
    name: str
    required: bool
    healthy: bool = False
    detail: str = "not_started"
    updated_at_s: float = 0.0


class PlatformOrchestrator:
    def __init__(self) -> None:
        self._services: dict[str, ServiceState] = {}
        self._kill_switch = True
        self._kill_reason = "startup_fail_closed"
        self._lock = RLock()

    def register(self, name: str, required: bool = True) -> None:
        with self._lock:
            if name in self._services:
                raise ValueError(f"service already registered: {name}")
            self._services[name] = ServiceState(name=name, required=required)

    def update(self, name: str, healthy: bool, detail: str) -> None:
        with self._lock:
            state = self._services[name]
            state.healthy = bool(healthy)
            state.detail = str(detail)
            state.updated_at_s = time.time()

    def set_kill_switch(self, active: bool, reason: str) -> None:
        with self._lock:
            self._kill_switch = bool(active)
            self._kill_reason = str(reason)

    def ready(self) -> tuple[bool, list[str]]:
        with self._lock:
            reasons = []
            if self._kill_switch:
                reasons.append(f"kill_switch:{self._kill_reason}")
            for state in self._services.values():
                if state.required and not state.healthy:
                    reasons.append(f"service_unhealthy:{state.name}:{state.detail}")
            return not reasons, reasons

    def snapshot(self) -> dict:
        with self._lock:
            ready, reasons = self.ready()
            return {
                "ready": ready,
                "reasons": reasons,
                "kill_switch": self._kill_switch,
                "kill_reason": self._kill_reason,
                "services": {
                    name: asdict(state) for name, state in self._services.items()
                },
            }
