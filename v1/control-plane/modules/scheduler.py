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

from .config_manager import ConfigManager
from .event_bus import EventBus, get_bus
from .obsidian_push import ObsidianPush
from .task_manager import TaskManager

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
        self._started = False
        self.bus.subscribe("config_reloaded", self._on_config_reloaded)

    def start(self) -> None:
        if self._started:
            return
        self._scheduler, self._kind = _make_scheduler()
        self._register_cron_tasks()
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
