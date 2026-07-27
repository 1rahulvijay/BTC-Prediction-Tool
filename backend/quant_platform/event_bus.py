"""Small thread-safe in-process event bus with explicit failure reporting."""
from __future__ import annotations

from collections import defaultdict
from threading import RLock
from typing import Callable

from .events import MarketEvent


EventHandler = Callable[[MarketEvent], None]


class EventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[EventHandler]] = defaultdict(list)
        self._lock = RLock()

    def subscribe(self, event_type: str, handler: EventHandler) -> None:
        if not event_type or not callable(handler):
            raise ValueError("event_type and callable handler are required")
        with self._lock:
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: str, handler: EventHandler) -> None:
        with self._lock:
            handlers = self._handlers.get(event_type, [])
            if handler in handlers:
                handlers.remove(handler)

    def publish(self, event: MarketEvent) -> list[Exception]:
        with self._lock:
            handlers = list(self._handlers.get(event.event_type, []))
            handlers += list(self._handlers.get("*", []))
        failures: list[Exception] = []
        for handler in handlers:
            try:
                handler(event)
            except Exception as exc:
                failures.append(exc)
        return failures
