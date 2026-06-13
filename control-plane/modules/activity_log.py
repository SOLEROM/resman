"""Volatile activity log — a live view of what the server is doing.

This is **not** a durable audit trail. It is created fresh when the server
starts and deleted when it stops (atexit); it lives in RAM (a ring buffer) and
is mirrored to a volatile ``/tmp`` file so it can also be tailed from a shell.
The footer **Log** window reads it; new entries stream to the browser live.

Three ways entries arrive — callers need no wiring beyond the first:

* **explicit** — emit ``"activity"`` on the bus with
  ``{level, source, message, detail}`` (see :func:`emit_activity`).
* **auto** — a curated set of existing bus events (tasks, window, sessions,
  config, cron) is mirrored in, so those modules stay untouched.
* **errors** — :class:`ActivityLogHandler` forwards ``WARNING``+ records from
  resman's loggers, so unexpected failures surface here too.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import deque
from pathlib import Path
from typing import Optional

from .event_bus import EventBus, get_bus

log = logging.getLogger(__name__)

LEVELS = ("debug", "info", "warn", "error")
DEFAULT_MAX = 2000
# Per-run volatile files are named activity-<pid>.log; used to sweep the leftovers
# of runs that were hard-killed (SIGKILL/SIGTERM skip atexit).
_PID_FILE_RE = re.compile(r"^activity-(\d+)\.log$")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours
    except OSError:
        return False
    return True


def _short(task_id) -> str:
    s = str(task_id or "")
    return s[:8] if s else "?"


# Existing bus events mirrored into the log: name -> (level, source, formatter).
# Formatters are defensive — payload keys vary by emitter.
_AUTO = {
    "task_updated": ("info", "task",
                     lambda p: f"task {_short(p.get('task_id'))} → {p.get('state', '?')}"),
    "window_state_changed": ("info", "window",
                             lambda p: (f"window → {p['state']}" if p.get("state")
                                        else f"window schedule updated ({p.get('source', 'manual')})")),
    "session_crashed": ("error", "session",
                        lambda p: f"session crashed: {p.get('vault', '?')} — {p.get('message', '')}".strip()),
    "session_error": ("error", "session",
                      lambda p: f"session error: {p.get('message', '')}".strip()),
    "config_reloaded": ("info", "config",
                        lambda p: f"config reloaded ({p.get('file', '')})".strip()),
    "cron_skip_warning": ("warn", "cron",
                          lambda p: f"cron '{p.get('cron_name', '?')}' skipped "
                                    f"{p.get('skip_count', '?')}× (window inactive)"),
}


def emit_activity(bus: EventBus, message: str, *, level: str = "info",
                  source: str = "app", detail: Optional[str] = None) -> None:
    """Convenience: publish an explicit activity entry on the bus."""
    bus.emit("activity", {"level": level, "source": source,
                          "message": message, "detail": detail})


class ActivityLog:
    def __init__(self, path: Path, bus: Optional[EventBus] = None,
                 maxlen: int = DEFAULT_MAX) -> None:
        self.path = Path(path)
        self.bus = bus or get_bus()
        self.entries: deque = deque(maxlen=maxlen)
        self._seq = 0
        self._start_fresh_file()
        self._sweep_stale()
        # Explicit channel + auto-captured events. ActivityLog is the *producer*
        # of "activity_logged", so it never subscribes to that (no loop).
        self.bus.subscribe("activity", self._on_activity)
        for name in _AUTO:
            self.bus.subscribe(name, self._make_auto_handler(name))
        self.record("activity log started", source="server")

    # ----- volatile file -----
    def _start_fresh_file(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Truncate any stale file from a previous run.
            self.path.write_text("", encoding="utf-8")
        except OSError as exc:
            log.warning("activity log file unavailable (%s); RAM-only", exc)
            self.path = None  # type: ignore[assignment]

    def _sweep_stale(self) -> None:
        """Delete leftover activity-<pid>.log files of runs that are no longer
        alive (a hard kill skips atexit, so the file can outlive the server)."""
        if not self.path:
            return
        mine = _PID_FILE_RE.match(self.path.name)
        if not mine:
            return  # not a per-pid volatile file (e.g. the test path)
        my_pid = int(mine.group(1))
        try:
            siblings = list(self.path.parent.glob("activity-*.log"))
        except OSError:
            return
        for sib in siblings:
            m = _PID_FILE_RE.match(sib.name)
            if not m or int(m.group(1)) == my_pid:
                continue
            if not _pid_alive(int(m.group(1))):
                try:
                    sib.unlink()
                except OSError:
                    pass

    def _append_file(self, entry: dict) -> None:
        if not self.path:
            return
        try:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass  # never let logging break the operation being logged

    def close(self) -> None:
        """Delete the volatile file — the log is gone when the server stops."""
        if self.path:
            try:
                os.unlink(self.path)
            except OSError:
                pass

    # ----- recording -----
    def record(self, message: str, *, level: str = "info", source: str = "app",
               detail: Optional[str] = None) -> dict:
        lvl = level if level in LEVELS else "info"
        self._seq += 1
        entry = {
            "seq": self._seq,
            "ts": time.time(),
            "level": lvl,
            "source": str(source or "app"),
            "message": str(message),
            "detail": str(detail) if detail else None,
        }
        self.entries.append(entry)
        self._append_file(entry)
        # Stream to the browser (forwarded by the socket bridge).
        self.bus.emit("activity_logged", entry)
        return entry

    def _on_activity(self, payload: dict) -> None:
        payload = payload or {}
        self.record(
            payload.get("message", ""),
            level=payload.get("level", "info"),
            source=payload.get("source", "app"),
            detail=payload.get("detail"),
        )

    def _make_auto_handler(self, name: str):
        level, source, fmt = _AUTO[name]

        def handler(payload: dict) -> None:
            try:
                message = fmt(payload or {})
            except Exception:  # a malformed payload must not break the emitter
                message = name
            self.record(message, level=level, source=source)
        return handler

    # ----- reading -----
    def list(self, *, limit: int = 300, level: Optional[str] = None,
             source: Optional[str] = None) -> list[dict]:
        items = list(self.entries)
        if level in LEVELS:
            order = LEVELS.index(level)
            items = [e for e in items if LEVELS.index(e["level"]) >= order]
        if source:
            items = [e for e in items if e["source"] == source]
        if limit and limit > 0:
            items = items[-limit:]
        return items

    def clear(self) -> None:
        self.entries.clear()
        if self.path:
            try:
                self.path.write_text("", encoding="utf-8")
            except OSError:
                pass
        self.record("log cleared", source="server")


class ActivityLogHandler(logging.Handler):
    """A logging.Handler that mirrors WARNING+ log records into the activity log."""

    _PYLEVEL = {logging.WARNING: "warn", logging.ERROR: "error",
                logging.CRITICAL: "error"}

    def __init__(self, activity: ActivityLog) -> None:
        super().__init__(level=logging.WARNING)
        self._activity = activity
        self._busy = False

    def emit(self, record: logging.LogRecord) -> None:
        # Re-entrancy guard: recording emits on the bus, whose handlers may log
        # (e.g. a socket-emit failure under the "modules" logger). Without this
        # such a failure would recurse into the handler indefinitely.
        if self._busy:
            return
        try:
            self._busy = True
            level = self._PYLEVEL.get(record.levelno, "warn")
            self._activity.record(record.getMessage(), level=level,
                                  source=record.name.split(".")[-1])
        except Exception:
            pass  # logging must never raise
        finally:
            self._busy = False


def install_logging_bridge(activity: ActivityLog,
                           logger_names=("modules", "server")) -> ActivityLogHandler:
    """Attach an :class:`ActivityLogHandler` to resman's logger namespaces.

    Idempotent: any prior ActivityLogHandler is removed first, so calling
    :func:`build_app` more than once in a process doesn't stack handlers.
    """
    handler = ActivityLogHandler(activity)
    for name in logger_names:
        lg = logging.getLogger(name)
        lg.handlers = [h for h in lg.handlers if not isinstance(h, ActivityLogHandler)]
        lg.addHandler(handler)
    return handler
