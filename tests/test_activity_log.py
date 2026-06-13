"""Tests for the volatile activity log (modules/activity_log.py)."""
import json
import logging
import os

from modules.activity_log import (
    ActivityLog,
    ActivityLogHandler,
    emit_activity,
    install_logging_bridge,
)
from modules.event_bus import EventBus


def _log(tmp_path, **kw):
    return ActivityLog(tmp_path / "activity.log", EventBus(), **kw)


def test_record_shape_and_seq(tmp_path):
    a = _log(tmp_path)
    e = a.record("hello", level="info", source="test", detail="more")
    assert e["message"] == "hello" and e["source"] == "test"
    assert e["detail"] == "more" and e["level"] == "info"
    assert isinstance(e["ts"], float) and e["seq"] >= 1
    # The startup banner is entry 1; ours follows it.
    assert e["seq"] == a.entries[-1]["seq"]


def test_invalid_level_falls_back_to_info(tmp_path):
    a = _log(tmp_path)
    assert a.record("x", level="bogus")["level"] == "info"


def test_ring_buffer_caps(tmp_path):
    a = _log(tmp_path, maxlen=5)
    for i in range(20):
        a.record(f"m{i}")
    assert len(a.entries) == 5
    assert a.entries[-1]["message"] == "m19"


def test_list_level_filter(tmp_path):
    a = _log(tmp_path)
    a.record("i", level="info")
    a.record("w", level="warn")
    a.record("e", level="error")
    msgs = [e["message"] for e in a.list(level="warn")]
    assert "w" in msgs and "e" in msgs and "i" not in msgs


def test_list_source_filter_and_limit(tmp_path):
    a = _log(tmp_path)
    for i in range(5):
        a.record(f"t{i}", source="task")
    a.record("other", source="vault")
    tasks = a.list(source="task", limit=3)
    assert len(tasks) == 3
    assert all(e["source"] == "task" for e in tasks)


def test_emit_activity_bus_channel_is_captured(tmp_path):
    bus = EventBus()
    a = ActivityLog(tmp_path / "activity.log", bus)
    emit_activity(bus, "explicit one", level="warn", source="window-sync")
    last = a.entries[-1]
    assert last["message"] == "explicit one"
    assert last["level"] == "warn" and last["source"] == "window-sync"


def test_auto_captures_existing_bus_events(tmp_path):
    bus = EventBus()
    a = ActivityLog(tmp_path / "activity.log", bus)
    bus.emit("task_updated", {"task_id": "abcdef123456", "state": "running"})
    bus.emit("session_crashed", {"vault": "alpha", "message": "ttyd exited"})
    msgs = [e["message"] for e in a.entries]
    assert any("abcdef12" in m and "running" in m for m in msgs)
    crash = next(e for e in a.entries if e["source"] == "session")
    assert crash["level"] == "error"


def test_file_is_written_and_close_removes_it(tmp_path):
    p = tmp_path / "activity.log"
    a = ActivityLog(p, EventBus())
    a.record("to disk")
    lines = [json.loads(l) for l in p.read_text().splitlines() if l.strip()]
    assert any(e["message"] == "to disk" for e in lines)
    a.close()
    assert not p.exists()


def test_clear_empties_buffer(tmp_path):
    a = _log(tmp_path)
    a.record("x")
    a.clear()
    # After clear the only entry is the "log cleared" marker.
    assert [e["message"] for e in a.entries] == ["log cleared"]


def test_logging_bridge_forwards_warnings(tmp_path):
    a = _log(tmp_path)
    handler = install_logging_bridge(a, logger_names=("test_bridge",))
    lg = logging.getLogger("test_bridge")
    lg.setLevel(logging.DEBUG)
    lg.warning("a warning happened")
    lg.info("an info is ignored")
    msgs = [e["message"] for e in a.entries]
    assert "a warning happened" in msgs
    assert "an info is ignored" not in msgs
    lg.removeHandler(handler)


def test_sweep_removes_dead_pid_files(tmp_path):
    # A leftover activity-<pid>.log from a dead PID is swept on startup; a live
    # one (our own) is kept.
    (tmp_path / "activity-999999.log").write_text("stale\n")  # PID very unlikely alive
    a = ActivityLog(tmp_path / f"activity-{os.getpid()}.log", EventBus())
    assert not (tmp_path / "activity-999999.log").exists()
    assert a.path.exists()  # our own file remains


def test_logging_handler_reentrancy_guard():
    # The guard flag prevents recursion when recording itself triggers a log.
    calls = []

    class FakeActivity:
        def record(self, *a, **k):
            calls.append(1)

    h = ActivityLogHandler(FakeActivity())
    h._busy = True
    h.emit(logging.LogRecord("x", logging.ERROR, "f", 1, "msg", None, None))
    assert calls == []  # skipped while busy
