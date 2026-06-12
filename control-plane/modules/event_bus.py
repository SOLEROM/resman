"""Internal pub/sub event bus.

Decouples modules that would otherwise import each other (e.g., WindowState
notifying TaskManager when the window activates). Subscribers register a
callable for an event name; emitters publish payloads. Subscriber failures
are logged and never propagate.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict
from typing import Callable, Dict, List

log = logging.getLogger(__name__)


class EventBus:
    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._lock = threading.RLock()

    def subscribe(self, event: str, handler: Callable) -> None:
        with self._lock:
            self._subscribers[event].append(handler)

    def unsubscribe(self, event: str, handler: Callable) -> None:
        with self._lock:
            handlers = self._subscribers.get(event, [])
            if handler in handlers:
                handlers.remove(handler)

    def emit(self, event: str, payload: dict | None = None) -> None:
        payload = payload or {}
        with self._lock:
            handlers = list(self._subscribers.get(event, []))
        for handler in handlers:
            try:
                handler(payload)
            except Exception:
                log.exception("event handler failed for %s", event)

    def clear(self) -> None:
        with self._lock:
            self._subscribers.clear()


_default_bus = EventBus()


def get_bus() -> EventBus:
    return _default_bus
