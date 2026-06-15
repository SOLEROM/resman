"""WindowSampler — the in-process port of cld20's opener + sampler crons.

cld20 runs two operator-crontab lines: an **opener** (``cld20-open.sh``) that
fires ``claude -p "hi"`` at each window's *start* hour to anchor Claude's rolling
5-hour window to that boundary, and a **sampler** (``cld20-sample.sh``) that, near
each window's *close*, reads ``GET /api/oauth/usage`` and stores the reading.

resman has no operator crontab / sudo install path (deliberately out of scope —
see the garage-to-resman port notes), so this module derives the equivalent jobs
from the :class:`~modules.window_schedule.WindowSchedule` and hands them to the
already-running APScheduler (via :class:`~modules.scheduler.Scheduler`). The
behaviour is opted in **per window** (every mark defaults OFF):

* a window ticked ``open`` gets an opener job (``claude -p "hi"`` at its start).
* a window ticked ``collect`` gets ``collection_rate`` collector jobs — a single
  management setting deciding how many evenly spaced reads to take in each
  collecting window (``collection_offset_minutes`` places them; the last lands
  ~5 min before close, like cld20). ``collection_rate == 0`` registers none.

Every run is **classified, never fatal** (mirrors cld20/usage.sh) and is logged
to the activity bus so it streams to the footer Log window. Readings are appended
to :class:`~modules.window_stats.WindowStats` for the Windows-tab charts.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from . import claude_usage
from .event_bus import EventBus, get_bus
from .window_schedule import WindowSchedule, collection_offset_minutes
from .window_stats import WindowStats

log = logging.getLogger(__name__)


def _pct(v) -> str:
    return f"{round(v)}%" if isinstance(v, (int, float)) else "?"


class WindowSampler:
    """Derives + runs the opener/collector jobs for the current schedule config.

    Stateless w.r.t. scheduling: :meth:`jobs` is a pure projection of the live
    config, so the Scheduler can drop and re-register everything whenever the
    config changes. The ``run_*`` methods perform the actual wakeup / usage read.
    """

    def __init__(self, schedule: WindowSchedule, stats: WindowStats,
                 bus: Optional[EventBus] = None, *,
                 usage_fetch: Optional[Callable[[], dict]] = None,
                 wakeup: Optional[Callable[[], str]] = None) -> None:
        self.schedule = schedule
        self.stats = stats
        self.bus = bus or get_bus()
        self._usage_fetch = usage_fetch or claude_usage.fetch_usage
        self._wakeup = wakeup or claude_usage.wakeup

    # ----- activity logging -----
    def _activity(self, message: str, *, level: str = "info",
                  source: str = "window") -> None:
        self.bus.emit("activity", {"level": level, "source": source,
                                   "message": message})

    # ----- job derivation (pure) -----
    def _ordered_windows(self) -> list[dict]:
        return sorted(self.schedule.windows, key=lambda w: w["server_start"])

    def _window_at(self, index: int) -> Optional[dict]:
        """The (1-based) window in start-hour order, or None if it's gone."""
        windows = self._ordered_windows()
        return windows[index - 1] if 1 <= index <= len(windows) else None

    def jobs(self) -> list[dict]:
        """Project the live config into a list of cron-job descriptors::

            {"id", "kind": "opener"|"collection", "hour", "minute",
             "window_index", "window_count", "slot", "run": callable}

        Opener jobs come from windows ticked ``open``; collector jobs from windows
        ticked ``collect`` (when ``collection_rate > 0``). The Scheduler turns each
        into an APScheduler ``CronTrigger(hour=, minute=)``; ``run`` takes no args.
        """
        windows = self._ordered_windows()
        count = len(windows)
        out: list[dict] = []

        for idx, w in enumerate(windows, start=1):
            if not w.get("open"):
                continue
            out.append({
                "id": f"window::opener::{idx}",
                "kind": "opener",
                "hour": w["server_start"] % 24,
                "minute": 0,
                "window_index": idx,
                "window_count": count,
                "slot": None,
                "run": self._bind(self.run_opener, idx, count),
            })

        rate = self.schedule.collection_rate
        if rate > 0:
            offsets = collection_offset_minutes(
                self.schedule.window_length_hours, rate)
            for idx, w in enumerate(windows, start=1):
                if not w.get("collect"):
                    continue
                for slot, off in enumerate(offsets, start=1):
                    minute_of_day = (w["server_start"] * 60 + off) % 1440
                    out.append({
                        "id": f"window::sample::{idx}::{slot}",
                        "kind": "collection",
                        "hour": minute_of_day // 60,
                        "minute": minute_of_day % 60,
                        "window_index": idx,
                        "window_count": count,
                        "slot": slot,
                        "run": self._bind(self.run_collection, idx, count, slot,
                                          len(offsets)),
                    })
        return out

    @staticmethod
    def _bind(fn, *args):
        def _run():
            return fn(*args)
        return _run

    # ----- runners (never raise) -----
    def run_opener(self, window_index: int, window_count: int) -> Optional[dict]:
        """Anchor the window: ``claude -p "hi"`` then read the (≈0%) usage so the
        chart gets cld20's start-of-window marker. Tagged ``source="opener"``;
        the weekly half is dropped (openers don't represent a close-of-window)."""
        w = self._window_at(window_index)
        if not (w and w.get("open")):
            return None  # window un-ticked since this job was registered
        try:
            state = self._wakeup()
        except Exception:
            log.exception("window opener wakeup raised")
            state = "fail"

        if state == "disabled":
            self._activity("window opener skipped: wakeup disabled "
                           "(RESMAN_USAGE_WAKEUP=0)", level="warn")
            return None
        if state == "unavailable":
            self._activity("window opener: claude CLI not found", level="warn")
            return None
        if state == "auth":
            self._activity("window opener: logged out / token rejected "
                           "(use Claude, then retry)", level="warn")
            return None
        if state == "fail":
            self._activity(f"window opener failed (window {window_index})",
                           level="error")
            return None

        # state in ("ok", "limit"): window is open — read the fresh ≈0% point.
        usage = self._safe_fetch()
        session_pct = usage.get("session_pct")
        if state == "limit" and session_pct is None:
            session_pct = 100
        entry = self.stats.record(
            source="opener",
            session_pct=session_pct,
            weekly_pct=None,
            session_resets_at=usage.get("session_resets_at"),
            reason="limit_reached" if state == "limit" else usage.get("reason"),
            window_index=window_index,
            window_count=window_count,
        )
        if state == "limit":
            self._activity(f"opened window {window_index}/{window_count} — "
                           f"at usage limit", level="warn")
        else:
            self._activity(f"opened window {window_index}/{window_count} — "
                           f"session {_pct(session_pct)}")
        return entry

    def run_collection(self, window_index: int, window_count: int,
                       slot: int, slot_count: int) -> Optional[dict]:
        """Take one usage reading (no token spend on the healthy path) and store
        it. Tagged ``source="auto"``."""
        w = self._window_at(window_index)
        if self.schedule.collection_rate <= 0 or not (w and w.get("collect")):
            return None  # rate set to 0 or window un-ticked since registration
        return self._collect(source="auto", window_index=window_index,
                             window_count=window_count, slot=slot,
                             slot_count=slot_count)

    def collect_now(self) -> dict:
        """On-demand reading from the Windows-tab "Collect now" button. Tagged
        ``source="manual"``; bound to whatever window is current."""
        cur = (self.schedule.status() or {}).get("current") or {}
        return self._collect(source="manual",
                             window_index=cur.get("index"),
                             window_count=cur.get("count"),
                             slot=None, slot_count=None)

    # ----- shared read path -----
    def _collect(self, *, source: str, window_index, window_count,
                 slot, slot_count) -> dict:
        usage = self._safe_fetch()
        reason = usage.get("reason")
        entry = self.stats.record(
            source=source,
            session_pct=usage.get("session_pct"),
            weekly_pct=usage.get("weekly_pct"),
            session_resets_at=usage.get("session_resets_at"),
            weekly_resets_at=usage.get("weekly_resets_at"),
            reason=reason,
            window_index=window_index,
            window_count=window_count,
        )
        where = f"window {window_index}" if window_index else "current window"
        slot_str = f" {slot}/{slot_count}" if slot and slot_count else ""
        s, w = _pct(usage.get("session_pct")), _pct(usage.get("weekly_pct"))
        if reason == "ok":
            self._activity(f"usage sample{slot_str} {where} — session {s}, weekly {w}")
        elif reason == "limit_reached":
            self._activity(f"usage sample{slot_str} {where} — at limit "
                           f"(session {s}, weekly {w})", level="warn")
        elif reason == "auth_error":
            self._activity(f"usage sample{slot_str} {where}: logged out / token "
                           f"rejected (use Claude, then retry)", level="warn")
        else:
            self._activity(f"usage sample{slot_str} {where} failed: "
                           f"{reason or 'unknown error'}", level="error")
        return entry

    def _safe_fetch(self) -> dict:
        try:
            return self._usage_fetch() or {}
        except Exception as exc:
            log.warning("usage fetch raised: %s", exc)
            return {"reason": "fetch_error"}
