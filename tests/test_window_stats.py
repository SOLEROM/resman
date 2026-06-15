"""Tests for the durable usage-reading store (modules/window_stats.py)."""
import json
import time

from modules.event_bus import EventBus
from modules.window_stats import WindowStats


def _store(tmp_path, **kw):
    return WindowStats(tmp_path / "window_samples.jsonl", EventBus(), **kw)


def test_record_persists_and_reloads(tmp_path):
    s = _store(tmp_path)
    s.record(source="auto", session_pct=10, weekly_pct=20)
    s.record(source="opener", session_pct=1, weekly_pct=None)
    assert len(s.list()) == 2
    # A fresh instance reads the same rows back off disk.
    s2 = _store(tmp_path)
    rows = s2.list()
    assert len(rows) == 2
    assert rows[0]["source"] == "auto" and rows[0]["session_pct"] == 10
    assert rows[1]["source"] == "opener"


def test_record_emits_window_sample_added(tmp_path):
    bus = EventBus()
    seen = []
    bus.subscribe("window_sample_added", lambda p: seen.append(p))
    s = WindowStats(tmp_path / "window_samples.jsonl", bus)
    entry = s.record(source="manual", session_pct=5, weekly_pct=6)
    assert seen and seen[0]["session_pct"] == 5
    assert entry["at"].startswith("20")  # ISO-ish local timestamp


def test_list_filters_by_source_and_since(tmp_path):
    s = _store(tmp_path)
    old = time.time() - 10 * 86400
    s.record(source="auto", session_pct=1, ts=old)
    s.record(source="opener", session_pct=2)
    s.record(source="auto", session_pct=3)
    assert len(s.list(source="auto")) == 2
    assert len(s.list(source="opener")) == 1
    recent = s.list(since_ts=time.time() - 86400)
    assert all(r["ts"] >= time.time() - 86400 for r in recent)
    assert len(recent) == 2


def test_latest_skips_openers_and_empty(tmp_path):
    s = _store(tmp_path)
    s.record(source="auto", session_pct=40, weekly_pct=50)
    s.record(source="opener", session_pct=None, weekly_pct=None)
    # canonical latest reading skips the opener/empty row
    latest = s.latest()
    assert latest["source"] == "auto" and latest["session_pct"] == 40
    # with_reading=False returns the raw last row
    assert s.latest(with_reading=False)["source"] == "opener"


def test_corrupt_lines_are_skipped(tmp_path):
    path = tmp_path / "window_samples.jsonl"
    now = time.time()
    path.write_text(
        json.dumps({"ts": now - 60, "source": "auto", "session_pct": 9}) + "\n"
        + "not json at all\n"
        + json.dumps({"ts": now - 30, "source": "auto", "session_pct": 11}) + "\n"
        + "{partial\n",
        encoding="utf-8",
    )
    s = WindowStats(path, EventBus())
    rows = s.list()
    assert len(rows) == 2
    assert [r["session_pct"] for r in rows] == [9, 11]


def test_retention_prunes_old_rows_on_load(tmp_path):
    path = tmp_path / "window_samples.jsonl"
    now = time.time()
    lines = [
        json.dumps({"ts": now - 200 * 86400, "source": "auto", "session_pct": 1}),
        json.dumps({"ts": now - 1 * 86400, "source": "auto", "session_pct": 2}),
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    s = WindowStats(path, EventBus(), retention_days=90)
    rows = s.list()
    assert len(rows) == 1 and rows[0]["session_pct"] == 2
    # The over-horizon row is compacted out of the file too.
    on_disk = [l for l in path.read_text().splitlines() if l.strip()]
    assert len(on_disk) == 1


def test_max_rows_cap(tmp_path):
    s = _store(tmp_path, max_rows=5)
    for i in range(12):
        s.record(source="auto", session_pct=i)
    rows = s.list()
    assert len(rows) == 5
    assert [r["session_pct"] for r in rows] == [7, 8, 9, 10, 11]


def test_summary_and_clear(tmp_path):
    s = _store(tmp_path)
    s.record(source="auto", session_pct=10, weekly_pct=20)
    s.record(source="auto", session_pct=30, weekly_pct=40)
    summ = s.summary()
    assert summ["count"] == 2
    assert summ["latest"]["session_pct"] == 30
    s.clear()
    assert s.list() == []
    assert s.summary()["count"] == 0
