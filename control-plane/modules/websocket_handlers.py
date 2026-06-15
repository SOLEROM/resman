"""Socket.IO event wiring.

Subscribes to EventBus events and re-emits them to connected browser clients.
"""
from __future__ import annotations

import logging
from .event_bus import EventBus

log = logging.getLogger(__name__)

EVENT_NAMES = (
    "window_state_changed",
    "session_crashed",
    "session_error",
    "child_state_changed",
    "config_reloaded",
    "task_updated",
    "task_log_appended",
    "task_scheduled",
    "cron_skip_warning",
    "activity_logged",
    "window_sample_added",
)


def attach_socketio(socketio, bus: EventBus) -> None:
    def make_forwarder(name: str):
        def forward(payload):
            try:
                socketio.emit(name, payload or {})
            except Exception:
                log.exception("socketio.emit failed for %s", name)
        return forward

    for name in EVENT_NAMES:
        bus.subscribe(name, make_forwarder(name))
