import json
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from modules.event_bus import EventBus
from modules.window_state import WindowState


def test_missing_budget_initializes_between(tmp_path):
    bus = EventBus()
    ws = WindowState(tmp_path / "budget.json", bus)
    ws.load()
    assert ws.state == "between"
    assert (tmp_path / "budget.json").exists()


def test_corrupt_budget_resets_safely(tmp_path):
    p = tmp_path / "budget.json"
    p.write_text("not json")
    bus = EventBus()
    ws = WindowState(p, bus)
    ws.load()  # must not raise
    assert ws.state == "between"


def test_start_window_emits_activated(tmp_path):
    bus = EventBus()
    activated = []
    bus.subscribe("window_activated", lambda p: activated.append(p))
    ws = WindowState(tmp_path / "budget.json", bus)
    ws.load()
    ws.start_window(2)
    assert ws.state == "active"
    assert ws.is_window_active() is True
    assert len(activated) == 1


def test_starting_active_window_does_not_re_emit(tmp_path):
    bus = EventBus()
    activated = []
    bus.subscribe("window_activated", lambda p: activated.append(p))
    ws = WindowState(tmp_path / "budget.json", bus)
    ws.load()
    ws.start_window(2)
    ws.start_window(3)
    # Only first transition emits window_activated
    assert len(activated) == 1


def test_end_window(tmp_path):
    ws = WindowState(tmp_path / "budget.json", EventBus())
    ws.load()
    ws.start_window(1)
    ws.end_window()
    assert ws.state == "between"
    assert ws.is_window_active() is False


def test_invalid_duration_rejected(tmp_path):
    ws = WindowState(tmp_path / "budget.json", EventBus())
    ws.load()
    with pytest.raises(ValueError):
        ws.start_window(0)
    with pytest.raises(ValueError):
        ws.start_window(20)
    with pytest.raises(ValueError):
        ws.start_window(None)


def test_persistence_round_trip(tmp_path):
    bus = EventBus()
    ws = WindowState(tmp_path / "budget.json", bus)
    ws.load()
    ws.start_window(3)
    # Re-load and ensure state survived
    ws2 = WindowState(tmp_path / "budget.json", EventBus())
    ws2.load()
    assert ws2.state == "active"
    assert ws2.window_ends_at is not None


def test_overrun_detected(tmp_path):
    p = tmp_path / "budget.json"
    past = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    p.write_text(json.dumps({
        "window_state": "active",
        "window_started_at": past,
        "window_ends_at": past,
    }))
    ws = WindowState(p, EventBus())
    ws.load()
    assert ws.state == "active"
    assert ws.is_window_active() is False
    assert ws.overrun_seconds() > 0
