"""Scheduler — APScheduler-based cron + ObsidianPush.

Cron tasks fire only when the window is active; otherwise emit a
cron_skipped event in the JSONL log. ObsidianPush fires every 60 seconds
regardless of window state.

The plan mandates GeventScheduler in production. For test/dev environments
without gevent, BackgroundScheduler is used as a compatibility fallback —
this is gated by a runtime check, never silently downgraded in production.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from .config_manager import ConfigManager
from .event_bus import EventBus, get_bus
from .obsidian_push import ObsidianPush
from .task_manager import TaskManager, _parse_iso

log = logging.getLogger(__name__)


def _make_scheduler():
    """Prefer GeventScheduler; fall back to BackgroundScheduler if gevent absent."""
    try:
        from apscheduler.schedulers.gevent import GeventScheduler

        return GeventScheduler(), "gevent"
    except Exception:
        from apscheduler.schedulers.background import BackgroundScheduler

        log.warning(
            "gevent not available; using BackgroundScheduler. "
            "In production with eventlet, install gevent or replace this fallback."
        )
        return BackgroundScheduler(), "background"


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Scheduler:
    def __init__(
        self,
        config: ConfigManager,
        task_manager: TaskManager,
        obsidian_push: ObsidianPush,
        is_window_active: Callable[[], bool],
        bus: Optional[EventBus] = None,
        push_interval_seconds: int = 60,
    ) -> None:
        self.config = config
        self.task_manager = task_manager
        self.obsidian_push = obsidian_push
        self.is_window_active = is_window_active
        self.bus = bus or get_bus()
        self.push_interval_seconds = push_interval_seconds
        self._scheduler = None
        self._kind = None
        self._cron_state: Dict[str, dict] = {}
        self._registered_jobs: List[str] = []
        self._one_shot_jobs: Dict[str, str] = {}  # task_id -> APScheduler job id
        self._started = False
        self.bus.subscribe("config_reloaded", self._on_config_reloaded)
        self.bus.subscribe("task_scheduled", self._on_task_scheduled)
        self.bus.subscribe("task_updated", self._on_task_updated)

    def start(self) -> None:
        if self._started:
            return
        self._scheduler, self._kind = _make_scheduler()
        self._register_cron_tasks()
        self._register_existing_scheduled_tasks()
        self._scheduler.add_job(
            self._push_tick, "interval", seconds=self.push_interval_seconds,
            id="obsidian-push", replace_existing=True,
        )
        self._scheduler.start()
        self._started = True

    def stop(self) -> None:
        if self._scheduler and self._started:
            try:
                self._scheduler.shutdown(wait=False)
            except Exception:
                pass
        self._started = False

    def _on_config_reloaded(self, _payload: dict) -> None:
        if not self._started:
            return
        # Remove existing user cron jobs and re-register
        for jid in list(self._registered_jobs):
            try:
                self._scheduler.remove_job(jid)
            except Exception:
                pass
        self._registered_jobs.clear()
        self._register_cron_tasks()

    def _register_cron_tasks(self) -> None:
        for entry in self.config.cron_tasks:
            cron_str = entry["cron"]
            try:
                trigger = CronTrigger.from_crontab(cron_str)
            except Exception as exc:
                log.error("invalid cron expression %r: %s", cron_str, exc)
                continue
            jid = f"cron::{entry['name']}"
            self._cron_state.setdefault(
                entry["name"], {"last_fired_at": None, "skip_count": 0},
            )
            self._scheduler.add_job(
                self._cron_tick, trigger, id=jid, replace_existing=True,
                kwargs={"entry": dict(entry)},
            )
            self._registered_jobs.append(jid)

    def _push_tick(self) -> None:
        try:
            self.obsidian_push.push_all_vaults()
        except Exception:
            log.exception("ObsidianPush tick raised")

    def _cron_tick(self, entry: dict) -> None:
        name = entry["name"]
        state = self._cron_state.setdefault(name, {"last_fired_at": None, "skip_count": 0})
        scheduled_at = _utcnow_iso()
        if not self.is_window_active():
            state["skip_count"] = state.get("skip_count", 0) + 1
            self.task_manager.cron_skipped(name, scheduled_at, state["skip_count"])
            if state["skip_count"] > 2:
                self.bus.emit("cron_skip_warning", {
                    "cron_name": name,
                    "skip_count": state["skip_count"],
                    "last_fired_at": state.get("last_fired_at"),
                })
            return
        try:
            self.task_manager.create_task(
                name=f"cron-{name}",
                vault=entry["vault"],
                operation=entry["operation"],
                params=entry.get("params", {}),
                priority=entry["priority"],
                schedule="background",
                run_now=True,
            )
            state["last_fired_at"] = scheduled_at
            state["skip_count"] = 0
        except Exception:
            log.exception("cron task %s dispatch failed", name)

    # ----- One-shot scheduled tasks -----
    def _on_task_scheduled(self, payload: dict) -> None:
        """Register a one-shot DateTrigger for a scheduled task."""
        task_id = payload.get("task_id")
        scheduled_for = payload.get("scheduled_for")
        if not task_id or not scheduled_for or not self._started:
            # If the scheduler hasn't started yet, _register_existing_scheduled_tasks
            # will pick up these tasks on start() instead.
            return
        self._register_one_shot(task_id, scheduled_for)

    def _on_task_updated(self, payload: dict) -> None:
        """When a scheduled task transitions out of `scheduled`, drop its
        pending one-shot job so it can't fire a second time after the user
        cancels or manually promotes it."""
        task_id = payload.get("task_id")
        new_state = payload.get("state")
        if not task_id or new_state == "scheduled":
            return
        jid = self._one_shot_jobs.pop(task_id, None)
        if jid and self._scheduler is not None:
            try:
                self._scheduler.remove_job(jid)
            except Exception:
                pass

    def _register_existing_scheduled_tasks(self) -> None:
        """At startup, re-arm one-shot triggers for tasks already in `scheduled`
        state from the replay. Tasks whose scheduled_for is already past are
        skipped — the replay path records them as overdue warnings; the user
        promotes them manually from the UI."""
        for task in self.task_manager._tasks.values():
            if task.state != "scheduled" or not task.scheduled_for:
                continue
            sched_dt = _parse_iso(task.scheduled_for)
            if sched_dt is None or sched_dt <= datetime.now(timezone.utc):
                continue
            self._register_one_shot(task.id, task.scheduled_for)

    def _register_one_shot(self, task_id: str, scheduled_for: str) -> None:
        if self._scheduler is None:
            return
        run_date = _parse_iso(scheduled_for)
        if run_date is None:
            log.warning("scheduled_for %r unparseable for task %s", scheduled_for, task_id)
            return
        jid = f"task::{task_id}"
        try:
            self._scheduler.add_job(
                self._fire_scheduled_task, DateTrigger(run_date=run_date),
                id=jid, replace_existing=True, kwargs={"task_id": task_id},
            )
            self._one_shot_jobs[task_id] = jid
        except Exception:
            log.exception("failed to register one-shot trigger for task %s", task_id)

    def _fire_scheduled_task(self, task_id: str) -> None:
        """Triggered at the scheduled moment — promote the task and dispatch."""
        try:
            self.task_manager.promote(task_id)
        except Exception:
            log.exception("scheduled task %s promotion failed", task_id)
        finally:
            self._one_shot_jobs.pop(task_id, None)

    def cron_status(self) -> List[dict]:
        out: List[dict] = []
        for entry in self.config.cron_tasks:
            s = self._cron_state.get(entry["name"], {"last_fired_at": None, "skip_count": 0})
            out.append({
                "name": entry["name"],
                "cron": entry["cron"],
                "vault": entry["vault"],
                "operation": entry["operation"],
                "priority": entry["priority"],
                "last_fired_at": s["last_fired_at"],
                "skip_count": s["skip_count"],
            })
        return out
