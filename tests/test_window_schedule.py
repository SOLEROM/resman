"""Tests for the cld20-style window schedule (modules/window_schedule.py)."""
from datetime import datetime

import pytest

from modules.event_bus import EventBus
from modules.window_schedule import (
    DEFAULT_WINDOW_STARTS,
    ScheduleError,
    WindowSchedule,
)


def _sched(tmp_path):
    return WindowSchedule(tmp_path / "window_schedule.json", EventBus())


def test_defaults_and_persistence(tmp_path):
    s = _sched(tmp_path)
    s.load()
    assert [w["server_start"] for w in s.windows] == DEFAULT_WINDOW_STARTS
    assert (tmp_path / "window_schedule.json").exists()
    # Reload picks up the persisted config.
    s2 = _sched(tmp_path)
    s2.load()
    assert [w["server_start"] for w in s2.windows] == DEFAULT_WINDOW_STARTS


def test_current_and_next_window(tmp_path):
    s = _sched(tmp_path)
    # Default windows [0,5,10,15,20], length 5h. At 12:30 the current window is
    # the one starting at 10:00 (index 3), next starts at 15:00.
    now = datetime(2026, 6, 12, 12, 30, 0)
    st = s.status(now)
    assert st["current"]["server_start"] == 10
    assert st["current"]["index"] == 3
    assert st["next"]["server_start"] == 15
    assert st["next"]["seconds_until_start"] == int((datetime(2026, 6, 12, 15, 0) - now).total_seconds())


def test_next_night_window(tmp_path):
    s = _sched(tmp_path)
    s.update(windows=[
        {"server_start": 0, "night_window": True},
        {"server_start": 8, "night_window": False},
        {"server_start": 16, "night_window": False},
    ])
    now = datetime(2026, 6, 12, 10, 0, 0)
    # The only night window starts at 00:00 → next occurrence is tomorrow 00:00.
    iso = s.next_night_window_iso(now)
    assert iso == "2026-06-13T00:00:00"
    assert s.status(now)["next_night"]["night"] is True


def test_weekly_progress(tmp_path):
    s = _sched(tmp_path)
    s.update(weekly_anchor={"weekday": 0, "hour": 0})  # Monday 00:00
    # 2026-06-12 is a Friday 12:00 → ~4.5/7 through the week.
    now = datetime(2026, 6, 12, 12, 0, 0)
    wk = s.status(now)["weekly"]
    assert wk["weekday_name"] == "Monday"
    assert 0.6 < wk["fraction"] < 0.7
    assert wk["start"].startswith("2026-06-08")  # the Monday of that week


def test_update_validates(tmp_path):
    s = _sched(tmp_path)
    with pytest.raises(ScheduleError):
        s.update(windows=[])
    with pytest.raises(ScheduleError):
        s.update(windows=[{"server_start": 24, "night_window": False}])
    with pytest.raises(ScheduleError):
        s.update(windows=[{"server_start": 5}, {"server_start": 5}])  # duplicate
    with pytest.raises(ScheduleError):
        s.update(weekly_anchor={"weekday": 9, "hour": 0})
    with pytest.raises(ScheduleError):
        s.update(operator_hour_offset=99)
    with pytest.raises(ScheduleError):
        s.update(window_length_hours=0)
    with pytest.raises(ScheduleError):
        s.update(refresh_interval_minutes=0)       # below min (1)
    with pytest.raises(ScheduleError):
        s.update(refresh_interval_minutes=61)      # above max (60)
    with pytest.raises(ScheduleError):
        s.update(sync_interval_minutes=0)          # below min (1)
    with pytest.raises(ScheduleError):
        s.update(sync_interval_minutes=1441)       # above max (1440)


def test_poll_intervals_default_persist_and_update(tmp_path):
    s = _sched(tmp_path)
    s.load()
    assert s.refresh_interval_minutes == 1
    assert s.sync_interval_minutes == 10
    s.update(refresh_interval_minutes=2, sync_interval_minutes=30)
    s2 = _sched(tmp_path)
    s2.load()
    assert s2.refresh_interval_minutes == 2
    assert s2.sync_interval_minutes == 30


def test_update_sorts_and_persists_windows(tmp_path):
    s = _sched(tmp_path)
    s.update(windows=[
        {"server_start": 12, "night_window": True},
        {"server_start": 3, "night_window": False},
    ])
    assert [w["server_start"] for w in s.windows] == [3, 12]
    s2 = _sched(tmp_path)
    s2.load()
    assert [w["server_start"] for w in s2.windows] == [3, 12]
    assert s2.windows[1]["night_window"] is True


def test_log_records_config_updates(tmp_path):
    s = _sched(tmp_path)
    s.load()
    s.update(operator_hour_offset=3)
    messages = [e["message"] for e in s.events]
    assert any("config updated" in m for m in messages)


def test_corrupt_file_falls_back_to_defaults(tmp_path):
    p = tmp_path / "window_schedule.json"
    p.write_text("not json{{{")
    s = WindowSchedule(p, EventBus())
    s.load()
    assert [w["server_start"] for w in s.windows] == DEFAULT_WINDOW_STARTS


def test_to_dict_shape(tmp_path):
    s = _sched(tmp_path)
    s.load()
    d = s.to_dict()
    assert set(d) >= {"windows", "weekly_anchor", "operator_hour_offset",
                      "window_length_hours", "refresh_interval_minutes",
                      "sync_interval_minutes", "status", "log", "weekday_names"}
    assert set(d["status"]) >= {"now", "current", "next", "next_night",
                                "upcoming", "weekly", "usage"}


def test_current_window_reports_time_fraction(tmp_path):
    s = _sched(tmp_path)
    # Default 5h windows [0,5,10,15,20]. At 12:30 the current window started at
    # 10:00, so 2.5h of 5h have elapsed → fraction ≈ 0.5.
    now = datetime(2026, 6, 12, 12, 30, 0)
    cur = s.status(now)["current"]
    assert cur["server_start"] == 10
    assert abs(cur["fraction"] - 0.5) < 0.01


def test_usage_is_unknown_until_synced(tmp_path):
    s = _sched(tmp_path)
    s.load()
    usage = s.status(datetime(2026, 6, 12, 12, 0, 0))["usage"]
    # Limit metering is out of scope, so the figures stay None (UI shows "?").
    assert usage["window_limit_pct"] is None
    assert usage["weekly_limit_pct"] is None
    assert usage["synced_at"] is None


def test_sync_stamps_synced_at_and_logs(tmp_path):
    s = _sched(tmp_path)
    s.load()
    now = datetime(2026, 6, 12, 12, 0, 0)
    out = s.sync(now)
    assert out["status"]["usage"]["synced_at"] == "2026-06-12T12:00:00"
    assert any("manual sync" in e["message"] for e in s.events)


def test_sync_pulls_usage_from_provider(tmp_path):
    def provider():
        return {
            "reason": "ok",
            "session_pct": 7.0, "weekly_pct": 9.0,
            "session_resets_at": "2026-06-12T21:00:00Z",
            "weekly_resets_at": "2026-06-17T20:00:00Z",
        }
    s = WindowSchedule(tmp_path / "window_schedule.json", EventBus(),
                       usage_provider=provider)
    s.load()
    u = s.sync(datetime(2026, 6, 12, 12, 0, 0))["status"]["usage"]
    assert u["window_limit_pct"] == 7.0
    assert u["weekly_limit_pct"] == 9.0
    assert u["session_resets_at"] == "2026-06-12T21:00:00Z"
    assert u["reason"] == "ok"
    assert any("session 7%, weekly 9%" in e["message"] for e in s.events)


def test_sync_survives_provider_exception(tmp_path):
    def boom():
        raise RuntimeError("network down")
    s = WindowSchedule(tmp_path / "window_schedule.json", EventBus(),
                       usage_provider=boom)
    s.load()
    u = s.sync(datetime(2026, 6, 12, 12, 0, 0))["status"]["usage"]
    # The sync must not crash; limits stay unknown and reason is fetch_error.
    assert u["window_limit_pct"] is None
    assert u["reason"] == "fetch_error"
    assert u["synced_at"] == "2026-06-12T12:00:00"


def test_upcoming_lists_future_windows(tmp_path):
    s = _sched(tmp_path)
    s.update(windows=[
        {"server_start": 0, "night_window": False},
        {"server_start": 8, "night_window": True},
        {"server_start": 16, "night_window": False},
    ])
    now = datetime(2026, 6, 12, 10, 0, 0)  # inside the 08:00 window
    up = s.status(now)["upcoming"]
    assert up, "expected upcoming windows"
    # All upcoming windows start strictly in the future, in chronological order.
    assert all(u["seconds_until_start"] > 0 for u in up)
    assert up == sorted(up, key=lambda u: u["start"])
    # The very next one is 16:00 today.
    assert up[0]["server_start"] == 16


# ----- cld20-style automation config (per-window open/collect + rate) -----
def test_automation_defaults_off(tmp_path):
    s = _sched(tmp_path)
    s.load()
    assert s.collection_rate == 0
    assert all(not w["open"] and not w["collect"] for w in s.windows)
    d = s.to_dict()
    assert d["collection_rate"] == 0
    assert d["max_collection_rate"] >= 1
    assert d["automation"]["open_windows_count"] == 0
    assert d["automation"]["collect_windows_count"] == 0


def test_collection_rate_validates(tmp_path):
    s = _sched(tmp_path)
    s.load()
    with pytest.raises(ScheduleError):
        s.update(collection_rate=-1)
    with pytest.raises(ScheduleError):
        s.update(collection_rate=999)        # above MAX_COLLECTION_RATE
    with pytest.raises(ScheduleError):
        s.update(collection_rate="lots")     # not an int


def test_per_window_open_collect_persist_and_reload(tmp_path):
    s = _sched(tmp_path)
    s.load()
    s.update(windows=[
        {"server_start": 0, "open": True, "collect": False},
        {"server_start": 10, "open": False, "collect": True},
    ], collection_rate=3)
    s2 = _sched(tmp_path)
    s2.load()
    assert s2.collection_rate == 3
    by_start = {w["server_start"]: w for w in s2.windows}
    assert by_start[0]["open"] is True and by_start[0]["collect"] is False
    assert by_start[10]["open"] is False and by_start[10]["collect"] is True


def test_window_marks_accept_truthy_spellings(tmp_path):
    s = _sched(tmp_path)
    s.load()
    s.update(windows=[{"server_start": 0, "open": "true", "collect": 1}])
    assert s.windows[0]["open"] is True and s.windows[0]["collect"] is True


def test_legacy_global_flags_migrate_to_per_window(tmp_path):
    """An old window_schedule.json (global open_windows_enabled + collection_rate,
    no per-window marks) upgrades so every window inherits the old behaviour."""
    import json
    p = tmp_path / "window_schedule.json"
    p.write_text(json.dumps({
        "windows": [{"server_start": 0, "night_window": False},
                    {"server_start": 12, "night_window": False}],
        "open_windows_enabled": True,
        "collection_rate": 2,
    }), encoding="utf-8")
    s = WindowSchedule(p, EventBus())
    s.load()
    assert all(w["open"] and w["collect"] for w in s.windows)
    assert s.collection_rate == 2


def test_update_emits_window_schedule_updated(tmp_path):
    bus = EventBus()
    seen = []
    bus.subscribe("window_schedule_updated", lambda p: seen.append(p))
    s = WindowSchedule(tmp_path / "window_schedule.json", bus)
    s.load()
    s.update(collection_rate=2)
    assert seen, "expected a window_schedule_updated event"
    assert seen[-1]["config"]["collection_rate"] == 2


def test_automation_summary_counts_and_next_times(tmp_path):
    s = _sched(tmp_path)
    s.load()
    now = datetime(2026, 6, 12, 12, 30, 0)
    # nothing ticked → no next times
    a = s.automation(now)
    assert a["next_opener"] is None and a["next_sample"] is None
    assert a["open_windows_count"] == 0 and a["collect_windows_count"] == 0
    # tick the 15:00 window for open + collect, rate 1
    s.update(windows=[
        {"server_start": 0}, {"server_start": 5}, {"server_start": 10},
        {"server_start": 15, "open": True, "collect": True}, {"server_start": 20},
    ], collection_rate=1)
    a = s.automation(now)
    assert a["open_windows_count"] == 1 and a["collect_windows_count"] == 1
    assert a["next_opener"] == "2026-06-12T15:00:00"   # the next ticked-open start
    assert a["next_sample"] is not None
    assert a["sample_offsets_minutes"] == [295]
