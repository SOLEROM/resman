"""WindowSchedule — cld20-style daily/weekly work-window model.

Ported (in concept) from the garage ``cld20`` window manager. cld20 tiles the
day into fixed-length "windows" aligned to Claude's session windows (default
five 5-hour windows starting at 00:00, 05:00, 10:00, 15:00, 20:00) and tracks
a weekly cycle anchored to a chosen weekday/hour. Each window can be flagged a
``night_window`` so work can be steered into it.

This is an **additive layer** over the manual :class:`WindowState` gate: the
manual active/between/ended state still gates task execution; this module adds
the *schedule* — which window is current, which is next, weekly progress, and
the next night window — used by the footer, the top-bar config, and
night-window task scheduling.

The live **limit** readout (session + weekly utilization) is pulled on demand by
:meth:`sync` via an injected ``usage_provider`` — see :mod:`modules.claude_usage`,
which ports cld20's read-only ``GET /api/oauth/usage`` call. Only cld20's heavy
cron → JSONL → charts sampling pipeline (tied to garage's install infra) stays
out of scope. Window times are interpreted in the **server's local time** (the
hours a user thinks in when they say "my 9am window").
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .event_bus import EventBus, get_bus

log = logging.getLogger(__name__)

DEFAULT_WINDOW_STARTS = [0, 5, 10, 15, 20]
DEFAULT_WINDOW_LENGTH_HOURS = 5
MAX_WINDOWS = 12

# Client-side poll cadences (minutes), surfaced in the ⊞ Windows config and used
# by the frontend timers. "refresh" redraws the footer bars from cached state
# (no claude.ai call); "sync" pulls fresh session/weekly limits from claude.ai.
DEFAULT_REFRESH_INTERVAL_MINUTES = 1
DEFAULT_SYNC_INTERVAL_MINUTES = 10
MIN_REFRESH_INTERVAL_MINUTES = 1
MAX_REFRESH_INTERVAL_MINUTES = 60
MIN_SYNC_INTERVAL_MINUTES = 1
MAX_SYNC_INTERVAL_MINUTES = 1440
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday"]  # Python convention: Monday=0


class ScheduleError(ValueError):
    """Raised on invalid schedule config; surfaced to the API as HTTP 400."""


def _now_local() -> datetime:
    return datetime.now().replace(microsecond=0)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat(timespec="seconds") if dt else None


class WindowSchedule:
    def __init__(self, path: Path, bus: Optional[EventBus] = None,
                 usage_provider=None) -> None:
        self.path = Path(path)
        self.bus = bus or get_bus()
        # Optional callable() -> usage dict (see claude_usage.fetch_usage). When
        # None (e.g. in tests) the limit figures stay unknown and render "?".
        self._usage_provider = usage_provider
        self.windows: list[dict] = [
            {"server_start": h, "night_window": False} for h in DEFAULT_WINDOW_STARTS
        ]
        self.weekly_anchor: dict = {"weekday": 0, "hour": 0}  # Monday 00:00
        self.operator_hour_offset: int = 0
        self.window_length_hours: int = DEFAULT_WINDOW_LENGTH_HOURS
        self.refresh_interval_minutes: int = DEFAULT_REFRESH_INTERVAL_MINUTES
        self.sync_interval_minutes: int = DEFAULT_SYNC_INTERVAL_MINUTES
        self.events: deque = deque(maxlen=50)
        # Cached usage-limit readout (populated by sync(); see _usage()).
        self._usage_data: dict = _empty_usage()
        # Record manual window transitions so the checks/logs view has history.
        self.bus.subscribe("window_state_changed", self._on_state_changed)

    # ----- persistence -----
    def load(self) -> None:
        if not self.path.exists():
            self._log_event("schedule initialized to defaults")
            self._persist()
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("window_schedule.json unreadable (%s); using defaults", exc)
            self._persist()
            return
        if not isinstance(data, dict):
            self._persist()
            return
        try:
            self._apply(
                windows=data.get("windows"),
                weekly_anchor=data.get("weekly_anchor"),
                operator_hour_offset=data.get("operator_hour_offset"),
                window_length_hours=data.get("window_length_hours"),
                refresh_interval_minutes=data.get("refresh_interval_minutes"),
                sync_interval_minutes=data.get("sync_interval_minutes"),
            )
        except ScheduleError as exc:
            log.warning("window_schedule.json invalid (%s); using defaults", exc)

    def _persist(self) -> None:
        data = self.config_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".window_schedule.", suffix=".tmp",
                                   dir=str(self.path.parent))
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

    # ----- config -----
    def config_dict(self) -> dict:
        return {
            "windows": [dict(w) for w in self.windows],
            "weekly_anchor": dict(self.weekly_anchor),
            "operator_hour_offset": self.operator_hour_offset,
            "window_length_hours": self.window_length_hours,
            "refresh_interval_minutes": self.refresh_interval_minutes,
            "sync_interval_minutes": self.sync_interval_minutes,
        }

    def _apply(self, *, windows=None, weekly_anchor=None,
               operator_hour_offset=None, window_length_hours=None,
               refresh_interval_minutes=None, sync_interval_minutes=None) -> None:
        """Validate + assign. Raises ScheduleError on bad input. No persist/emit."""
        if window_length_hours is not None:
            n = _as_int(window_length_hours, "window_length_hours")
            if not 1 <= n <= 24:
                raise ScheduleError("window_length_hours must be between 1 and 24")
            self.window_length_hours = n
        if refresh_interval_minutes is not None:
            r = _as_int(refresh_interval_minutes, "refresh_interval_minutes")
            if not MIN_REFRESH_INTERVAL_MINUTES <= r <= MAX_REFRESH_INTERVAL_MINUTES:
                raise ScheduleError(
                    f"refresh_interval_minutes must be between "
                    f"{MIN_REFRESH_INTERVAL_MINUTES} and {MAX_REFRESH_INTERVAL_MINUTES}")
            self.refresh_interval_minutes = r
        if sync_interval_minutes is not None:
            s = _as_int(sync_interval_minutes, "sync_interval_minutes")
            if not MIN_SYNC_INTERVAL_MINUTES <= s <= MAX_SYNC_INTERVAL_MINUTES:
                raise ScheduleError(
                    f"sync_interval_minutes must be between "
                    f"{MIN_SYNC_INTERVAL_MINUTES} and {MAX_SYNC_INTERVAL_MINUTES}")
            self.sync_interval_minutes = s
        if operator_hour_offset is not None:
            o = _as_int(operator_hour_offset, "operator_hour_offset")
            if not -12 <= o <= 14:
                raise ScheduleError("operator_hour_offset must be between -12 and 14")
            self.operator_hour_offset = o
        if weekly_anchor is not None:
            if not isinstance(weekly_anchor, dict):
                raise ScheduleError("weekly_anchor must be an object")
            wd = _as_int(weekly_anchor.get("weekday"), "weekly_anchor.weekday")
            hr = _as_int(weekly_anchor.get("hour"), "weekly_anchor.hour")
            if not 0 <= wd <= 6:
                raise ScheduleError("weekly_anchor.weekday must be 0 (Mon) … 6 (Sun)")
            if not 0 <= hr <= 23:
                raise ScheduleError("weekly_anchor.hour must be between 0 and 23")
            self.weekly_anchor = {"weekday": wd, "hour": hr}
        if windows is not None:
            self.windows = _validate_windows(windows)

    def update(self, **kwargs) -> dict:
        """Validate + assign + persist + emit. Returns the full state dict."""
        self._apply(**kwargs)
        self._persist()
        self._log_event("window config updated")
        self.bus.emit("window_state_changed", {"source": "schedule"})
        return self.to_dict()

    def sync(self, now: Optional[datetime] = None) -> dict:
        """Refresh live state on demand (the footer ``⟳`` sync button).

        Recomputes the live status, stamps ``synced_at``, and — when a
        ``usage_provider`` is configured — pulls the session/weekly limit
        utilization from claude.ai. The fetch is classified, never fatal: an
        auth/network failure leaves the percentages ``None`` (rendered ``?``).
        Returns the full state dict.
        """
        now = now or _now_local()
        self.bus.emit("activity", {"source": "window-sync", "level": "info",
                                   "message": "window limit sync started"})
        usage = _empty_usage()
        usage["synced_at"] = _iso(now)
        if self._usage_provider:
            try:
                fetched = self._usage_provider() or {}
            except Exception as exc:  # provider must not break the sync
                log.warning("usage provider failed: %s", exc)
                fetched = {"reason": "fetch_error"}
            usage["window_limit_pct"] = fetched.get("session_pct")
            usage["weekly_limit_pct"] = fetched.get("weekly_pct")
            usage["session_resets_at"] = fetched.get("session_resets_at")
            usage["weekly_resets_at"] = fetched.get("weekly_resets_at")
            usage["reason"] = fetched.get("reason")
        self._usage_data = usage
        self._log_event(_sync_message(usage))
        self.bus.emit("activity", _sync_activity(usage))
        return self.to_dict()

    # ----- derivation -----
    def _instances(self, now: datetime) -> list[dict]:
        length = timedelta(hours=self.window_length_hours)
        out = []
        ordered = sorted(self.windows, key=lambda w: w["server_start"])
        for day_off in range(-1, 8):
            base = (now + timedelta(days=day_off)).replace(
                hour=0, minute=0, second=0, microsecond=0)
            for idx, w in enumerate(ordered):
                start = base + timedelta(hours=w["server_start"])
                out.append({
                    "index": idx + 1,
                    "count": len(ordered),
                    "server_start": w["server_start"],
                    "night": bool(w["night_window"]),
                    "start": start,
                    "end": start + length,
                })
        out.sort(key=lambda i: i["start"])
        return out

    def status(self, now: Optional[datetime] = None) -> dict:
        now = now or _now_local()
        insts = self._instances(now)
        current = next((i for i in insts if i["start"] <= now < i["end"]), None)
        future = [i for i in insts if i["start"] > now]
        night = next((i for i in future if i["night"]), None)
        return {
            "now": _iso(now),
            "current": self._fmt(current, now),
            "next": self._fmt(future[0] if future else None, now),
            "next_night": self._fmt(night, now),
            "upcoming": [self._fmt(i, now) for i in future[:8]],
            "weekly": self._weekly(now),
            "usage": self._usage(),
        }

    def _fmt(self, inst: Optional[dict], now: datetime) -> Optional[dict]:
        if inst is None:
            return None
        total = (inst["end"] - inst["start"]).total_seconds()
        elapsed = (now - inst["start"]).total_seconds()
        return {
            "index": inst["index"],
            "count": inst["count"],
            "server_start": inst["server_start"],
            "night": inst["night"],
            "start": _iso(inst["start"]),
            "end": _iso(inst["end"]),
            "seconds_until_start": int((inst["start"] - now).total_seconds()),
            "seconds_until_end": int((inst["end"] - now).total_seconds()),
            # Fraction of this window's clock elapsed at `now` (0..1). Drives the
            # green window meter in the footer; only meaningful for the current
            # window but harmless (0 or 1) for past/future instances.
            "fraction": max(0.0, min(1.0, elapsed / total)) if total else 0.0,
        }

    def _usage(self) -> dict:
        """Limit-usage figures shown *after* the footer meters.

        Populated on demand by :meth:`sync`, which pulls the session (5-hour)
        and weekly (7-day) utilization from claude.ai via the injected
        ``usage_provider``. Until the first successful sync the percentages are
        ``None`` (the UI renders ``None`` as ``?``); ``synced_at`` records the
        last sync and ``reason`` carries ``ok``/``auth_error``/``fetch_error``.
        """
        return dict(self._usage_data)

    def _weekly(self, now: datetime) -> dict:
        wd = self.weekly_anchor["weekday"]
        hr = self.weekly_anchor["hour"]
        days_since = (now.weekday() - wd) % 7
        start = (now - timedelta(days=days_since)).replace(
            hour=hr, minute=0, second=0, microsecond=0)
        if start > now:
            start -= timedelta(days=7)
        end = start + timedelta(days=7)
        total = (end - start).total_seconds()
        elapsed = (now - start).total_seconds()
        return {
            "start": _iso(start),
            "end": _iso(end),
            "fraction": max(0.0, min(1.0, elapsed / total)) if total else 0.0,
            "seconds_remaining": max(0, int((end - now).total_seconds())),
            "weekday_name": WEEKDAY_NAMES[wd],
            "hour": hr,
        }

    def next_night_window_iso(self, now: Optional[datetime] = None) -> Optional[str]:
        """ISO start of the next night window (used to schedule night tasks)."""
        now = now or _now_local()
        night = next((i for i in self._instances(now)
                      if i["start"] > now and i["night"]), None)
        return _iso(night["start"]) if night else None

    # ----- logs / checks -----
    def _on_state_changed(self, payload: dict) -> None:
        if (payload or {}).get("source") == "schedule":
            return  # don't log our own config-update echo
        st = (payload or {}).get("state")
        self._log_event(f"manual window state → {st}" if st else "window state changed")

    def _log_event(self, message: str) -> None:
        self.events.appendleft({"at": _iso(_now_local()), "message": message})

    def to_dict(self) -> dict:
        d = self.config_dict()
        d["status"] = self.status()
        d["log"] = list(self.events)
        d["weekday_names"] = WEEKDAY_NAMES
        return d


# ----- helpers -----
def _empty_usage() -> dict:
    return {
        "window_limit_pct": None,
        "weekly_limit_pct": None,
        "session_resets_at": None,
        "weekly_resets_at": None,
        "synced_at": None,
        "reason": None,
    }


def _sync_message(usage: dict) -> str:
    """Human log line for a sync — always contains the literal 'manual sync'."""
    sp, wp = usage["window_limit_pct"], usage["weekly_limit_pct"]
    if sp is not None or wp is not None:
        s = f"{round(sp)}%" if sp is not None else "?"
        w = f"{round(wp)}%" if wp is not None else "?"
        prefix = "manual sync — at usage limit" if usage.get("reason") == "limit_reached" \
            else "manual sync —"
        return f"{prefix} session {s}, weekly {w}"
    reason = usage.get("reason")
    return f"manual sync — {reason}" if reason else "manual sync"


def _sync_activity(usage: dict) -> dict:
    """Activity-log entry describing a sync result (success or failure)."""
    sp, wp = usage["window_limit_pct"], usage["weekly_limit_pct"]
    if sp is not None or wp is not None:
        s = f"{round(sp)}%" if sp is not None else "?"
        w = f"{round(wp)}%" if wp is not None else "?"
        if usage.get("reason") == "limit_reached":
            return {"source": "window-sync", "level": "warn",
                    "message": f"window limit reached — session {s}, weekly {w}"}
        return {"source": "window-sync", "level": "info",
                "message": f"window limit sync ok — session {s}, weekly {w}"}
    reason = usage.get("reason")
    if reason == "auth_error":
        return {"source": "window-sync", "level": "warn",
                "message": "window limit sync: logged out / token rejected (use Claude, then retry)"}
    return {"source": "window-sync", "level": "error",
            "message": f"window limit sync failed: {reason or 'unknown error'}"}


def _as_int(value, field: str) -> int:
    try:
        if isinstance(value, bool):
            raise TypeError
        return int(value)
    except (TypeError, ValueError):
        raise ScheduleError(f"{field} must be an integer")


def _validate_windows(windows) -> list[dict]:
    if not isinstance(windows, list) or not windows:
        raise ScheduleError("windows must be a non-empty list")
    if len(windows) > MAX_WINDOWS:
        raise ScheduleError(f"at most {MAX_WINDOWS} windows are allowed")
    cleaned = []
    seen = set()
    for w in windows:
        if not isinstance(w, dict):
            raise ScheduleError("each window must be an object")
        start = _as_int(w.get("server_start"), "server_start")
        if not 0 <= start <= 23:
            raise ScheduleError("server_start must be between 0 and 23")
        if start in seen:
            raise ScheduleError("window start hours must be unique")
        seen.add(start)
        cleaned.append({"server_start": start, "night_window": bool(w.get("night_window"))})
    cleaned.sort(key=lambda w: w["server_start"])
    return cleaned
