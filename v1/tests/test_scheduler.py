"""Tests for the cron skip-when-inactive logic.

We avoid actually starting an APScheduler instance — we exercise the
_cron_tick path directly with a fake task manager and config.
"""
import json
from pathlib import Path

from modules.event_bus import EventBus
from modules.scheduler import Scheduler


class FakeTM:
    def __init__(self):
        self.created = []
        self.skipped = []
    def create_task(self, **kw):
        self.created.append(kw)
        class T: id = "t-x"
        return T()
    def cron_skipped(self, name, scheduled_at, skip_count):
        self.skipped.append((name, scheduled_at, skip_count))


class FakeCM:
    def __init__(self, cron):
        self.cron_tasks = cron


class FakePush:
    def push_all_vaults(self):
        return {"ok": 0, "failed": 0}


def test_cron_tick_skips_when_inactive(tmp_path):
    cm = FakeCM([{
        "name": "weekly", "cron": "0 8 * * 0", "vault": "ALL",
        "operation": "wiki-lint", "priority": "low",
    }])
    tm = FakeTM()
    bus = EventBus()
    sched = Scheduler(cm, tm, FakePush(), is_window_active=lambda: False, bus=bus)
    # Three skips → 4th tick will pass the threshold; check skip_count growth.
    for _ in range(3):
        sched._cron_tick(cm.cron_tasks[0])
    assert len(tm.skipped) == 3
    assert tm.skipped[-1][2] == 3
    # Window flips → next tick dispatches
    sched.is_window_active = lambda: True
    sched._cron_tick(cm.cron_tasks[0])
    assert len(tm.created) == 1


def test_cron_skip_warning_emitted_after_threshold(tmp_path):
    cm = FakeCM([{
        "name": "X", "cron": "0 8 * * 0", "vault": "ALL",
        "operation": "wiki-lint", "priority": "low",
    }])
    tm = FakeTM()
    bus = EventBus()
    warnings = []
    bus.subscribe("cron_skip_warning", lambda p: warnings.append(p))
    sched = Scheduler(cm, tm, FakePush(), is_window_active=lambda: False, bus=bus)
    for _ in range(3):
        sched._cron_tick(cm.cron_tasks[0])
    # threshold > 2 means the 3rd skip triggers
    assert any(w["skip_count"] == 3 for w in warnings)


def test_skip_count_resets_on_successful_fire():
    cm = FakeCM([{
        "name": "Y", "cron": "0 8 * * 0", "vault": "alpha",
        "operation": "wiki-lint", "priority": "low",
    }])
    tm = FakeTM()
    bus = EventBus()
    sched = Scheduler(cm, tm, FakePush(), is_window_active=lambda: False, bus=bus)
    sched._cron_tick(cm.cron_tasks[0])
    sched._cron_tick(cm.cron_tasks[0])
    assert sched._cron_state["Y"]["skip_count"] == 2
    sched.is_window_active = lambda: True
    sched._cron_tick(cm.cron_tasks[0])
    assert sched._cron_state["Y"]["skip_count"] == 0
    assert sched._cron_state["Y"]["last_fired_at"] is not None


def test_cron_status_table():
    cm = FakeCM([{
        "name": "abc", "cron": "0 8 * * 0", "vault": "alpha",
        "operation": "wiki-lint", "priority": "low",
    }])
    tm = FakeTM()
    sched = Scheduler(cm, tm, FakePush(), is_window_active=lambda: False)
    sched._cron_tick(cm.cron_tasks[0])
    rows = sched.cron_status()
    assert rows[0]["name"] == "abc"
    assert rows[0]["skip_count"] == 1
