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


# ----- window opener/collector job registration -----
class FakeAPS:
    """Records add/remove_job calls so we can assert window-job wiring without
    spinning up a real APScheduler."""
    def __init__(self):
        self.jobs = {}
    def add_job(self, fn, trigger=None, id=None, replace_existing=False, **kw):
        self.jobs[id] = (fn, trigger)
    def remove_job(self, jid):
        self.jobs.pop(jid, None)


def _window_sampler(tmp_path, bus):
    from modules.window_schedule import WindowSchedule
    from modules.window_stats import WindowStats
    from modules.window_sampler import WindowSampler
    sched = WindowSchedule(tmp_path / "window_schedule.json", bus)
    sched.load()
    stats = WindowStats(tmp_path / "window_samples.jsonl", bus)
    sampler = WindowSampler(schedule=sched, stats=stats, bus=bus,
                            usage_fetch=lambda: {"reason": "ok"}, wakeup=lambda: "ok")
    return sched, sampler


def _ticked(marks):
    return [{"server_start": h, **marks} for h in (0, 5, 10, 15, 20)]


def test_window_jobs_registered_and_refreshed(tmp_path):
    bus = EventBus()
    sched_obj = _make_started(FakeCM([]), bus)
    wsched, sampler = _window_sampler(tmp_path, bus)
    sched_obj.set_window_sampler(sampler)
    # Nothing ticked by default → no window jobs registered.
    assert not [j for j in sched_obj._scheduler.jobs if j.startswith("window::")]

    # Ticking open+collect on every window emits window_schedule_updated, which
    # the scheduler observes and re-derives the jobs from.
    wsched.update(windows=_ticked({"open": True, "collect": True}), collection_rate=2)
    ids = [j for j in sched_obj._scheduler.jobs if j.startswith("window::")]
    openers = [j for j in ids if "opener" in j]
    samples = [j for j in ids if "sample" in j]
    assert len(openers) == len(wsched.windows)
    assert len(samples) == 2 * len(wsched.windows)


def test_window_jobs_cleared_when_disabled(tmp_path):
    bus = EventBus()
    sched_obj = _make_started(FakeCM([]), bus)
    wsched, sampler = _window_sampler(tmp_path, bus)
    sched_obj.set_window_sampler(sampler)
    wsched.update(windows=_ticked({"collect": True}), collection_rate=2)
    assert any(j.startswith("window::sample") for j in sched_obj._scheduler.jobs)
    wsched.update(collection_rate=0)
    assert not any(j.startswith("window::") for j in sched_obj._scheduler.jobs)


def _make_started(cm, bus):
    """A Scheduler with a fake APS injected and marked started, so window-job
    registration runs without a live scheduler/event loop."""
    sched = Scheduler(cm, FakeTM(), FakePush(), is_window_active=lambda: True, bus=bus)
    sched._scheduler = FakeAPS()
    sched._started = True
    return sched
