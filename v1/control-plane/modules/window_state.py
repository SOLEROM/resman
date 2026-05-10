"""WindowState — manually-managed Claude Code window state.

Persists to budget.json. The state can only transition to `active` via an
explicit user action (Start window now). When that happens, emit
`window_activated` on the EventBus; TaskManager subscribes and promotes
deferred tasks. WindowState never imports TaskManager — the coupling flows
only via the EventBus.

is_window_active() is a function (not a cached field): it compares
datetime.utcnow() against window_ends_at on every call so there is no lag
on the gate check.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .event_bus import EventBus, get_bus

log = logging.getLogger(__name__)

VALID_STATES = ("active", "between", "ended")
MAX_DURATION_HOURS = 12
MIN_DURATION_HOURS = 1


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


class WindowState:
    def __init__(self, budget_path: Path, bus: Optional[EventBus] = None) -> None:
        self.path = Path(budget_path)
        self.bus = bus or get_bus()
        self.state: str = "between"
        self.window_started_at: Optional[datetime] = None
        self.window_ends_at: Optional[datetime] = None
        self.weekly_synced_at: Optional[datetime] = None
        self.weekly_ends_at: Optional[datetime] = None

    def load(self) -> None:
        """Load from budget.json; corruption resets to safe state and never crashes."""
        if not self.path.exists():
            log.info("budget.json missing; initializing to between")
            self._reset()
            self._persist()
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("budget.json corrupt (%s); resetting to between", exc)
            self._reset()
            self._persist()
            return
        if not isinstance(data, dict):
            log.warning("budget.json not a mapping; resetting")
            self._reset()
            self._persist()
            return
        st = data.get("window_state")
        self.state = st if st in VALID_STATES else "between"
        self.window_started_at = _parse_iso(data.get("window_started_at"))
        self.window_ends_at = _parse_iso(data.get("window_ends_at"))
        self.weekly_synced_at = _parse_iso(data.get("weekly_synced_at"))
        self.weekly_ends_at = _parse_iso(data.get("weekly_ends_at"))

    def _reset(self) -> None:
        self.state = "between"
        self.window_started_at = None
        self.window_ends_at = None
        self.weekly_synced_at = None
        self.weekly_ends_at = None

    def _persist(self) -> None:
        data = self.to_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=".budget.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def to_dict(self) -> dict:
        return {
            "window_state": self.state,
            "window_started_at": _iso(self.window_started_at),
            "window_ends_at": _iso(self.window_ends_at),
            "weekly_synced_at": _iso(self.weekly_synced_at),
            "weekly_ends_at": _iso(self.weekly_ends_at),
            "is_active": self.is_window_active(),
            "overrun_seconds": self.overrun_seconds(),
        }

    def is_window_active(self) -> bool:
        """True if state is active AND window has not ended."""
        if self.state != "active":
            return False
        if self.window_ends_at is None:
            return False
        return _utcnow() < self.window_ends_at

    def overrun_seconds(self) -> int:
        """Seconds past window_ends_at while still in active state. 0 otherwise."""
        if self.state != "active" or self.window_ends_at is None:
            return 0
        delta = (_utcnow() - self.window_ends_at).total_seconds()
        return int(delta) if delta > 0 else 0

    def start_window(self, duration_hours: float) -> dict:
        if duration_hours is None:
            raise ValueError("duration_hours is required")
        try:
            d = float(duration_hours)
        except (TypeError, ValueError):
            raise ValueError("duration_hours must be a number")
        if d < MIN_DURATION_HOURS or d > MAX_DURATION_HOURS:
            raise ValueError(
                f"duration_hours must be between {MIN_DURATION_HOURS} and {MAX_DURATION_HOURS}"
            )
        was_active = self.state == "active"
        now = _utcnow()
        self.state = "active"
        self.window_started_at = now
        self.window_ends_at = now + timedelta(hours=d)
        self._persist()
        if not was_active:
            self.bus.emit("window_activated", {"state": self.state})
        self.bus.emit(
            "window_state_changed",
            {"state": self.state, "ends_at": _iso(self.window_ends_at)},
        )
        return self.to_dict()

    def end_window(self) -> dict:
        self.state = "between"
        self.window_ends_at = _utcnow()
        self._persist()
        self.bus.emit(
            "window_state_changed",
            {"state": self.state, "ends_at": _iso(self.window_ends_at)},
        )
        return self.to_dict()

    def start_weekly(self, period_hours: float = 24 * 7) -> dict:
        now = _utcnow()
        self.weekly_synced_at = now
        self.weekly_ends_at = now + timedelta(hours=period_hours)
        if self.state == "ended":
            self.state = "between"
        self._persist()
        self.bus.emit(
            "window_state_changed",
            {"state": self.state, "ends_at": _iso(self.window_ends_at)},
        )
        return self.to_dict()

    def end_weekly(self) -> dict:
        self.state = "ended"
        self._persist()
        self.bus.emit(
            "window_state_changed",
            {"state": self.state, "ends_at": _iso(self.window_ends_at)},
        )
        return self.to_dict()

    def poll_tick(self) -> Optional[dict]:
        """Called by the 60s server poll. Emits window_state_changed if the
        window has expired since the last tick. Never writes budget.json."""
        if self.state == "active" and self.window_ends_at is not None:
            if _utcnow() >= self.window_ends_at:
                self.bus.emit(
                    "window_state_changed",
                    {"state": "between", "ends_at": _iso(self.window_ends_at)},
                )
                return {"expired": True}
        return None
