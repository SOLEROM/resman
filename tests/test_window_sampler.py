"""Tests for the in-process opener/collector (modules/window_sampler.py)."""
from modules.event_bus import EventBus
from modules.window_schedule import WindowSchedule, collection_offset_minutes
from modules.window_stats import WindowStats
from modules.window_sampler import WindowSampler


def _build(tmp_path, *, usage=None, wakeup=None):
    bus = EventBus()
    sched = WindowSchedule(tmp_path / "window_schedule.json", bus)
    sched.load()
    stats = WindowStats(tmp_path / "window_samples.jsonl", bus)
    sampler = WindowSampler(
        schedule=sched, stats=stats, bus=bus,
        usage_fetch=usage or (lambda: {"reason": "ok", "session_pct": 11,
                                       "weekly_pct": 22}),
        wakeup=wakeup or (lambda: "ok"),
    )
    return bus, sched, stats, sampler


def _all_windows(marks):
    """Default 5 starts, each tagged with the given marks dict."""
    return [{"server_start": h, **marks} for h in (0, 5, 10, 15, 20)]


# ----- pure offset math -----
def test_collection_offsets_spacing():
    assert collection_offset_minutes(5, 0) == []
    # rate 1 in a 5h window → one read ~5 min before close (300-5)
    assert collection_offset_minutes(5, 1) == [295]
    # rate 2 → mid + near-close
    assert collection_offset_minutes(5, 2) == [150, 295]
    offs = collection_offset_minutes(5, 5)
    assert offs[-1] <= 295 and offs[0] >= 1
    assert offs == sorted(offs)


# ----- job derivation -----
def test_jobs_empty_when_nothing_ticked(tmp_path):
    _, sched, _, sampler = _build(tmp_path)
    assert all(not w["open"] and not w["collect"] for w in sched.windows)
    assert sampler.jobs() == []


def test_jobs_opener_only_for_open_windows(tmp_path):
    _, sched, _, sampler = _build(tmp_path)
    sched.update(windows=[
        {"server_start": 0, "open": True},
        {"server_start": 10, "open": False},
        {"server_start": 20, "open": True},
    ])
    openers = [j for j in sampler.jobs() if j["kind"] == "opener"]
    assert len(openers) == 2
    assert sorted(j["hour"] for j in openers) == [0, 20]
    assert all(j["minute"] == 0 for j in openers)


def test_jobs_collection_only_for_collect_windows(tmp_path):
    _, sched, _, sampler = _build(tmp_path)
    sched.update(windows=[
        {"server_start": 0, "collect": True},
        {"server_start": 10, "collect": False},
        {"server_start": 20, "collect": True},
    ], collection_rate=3)
    coll = [j for j in sampler.jobs() if j["kind"] == "collection"]
    # 3 reads × 2 collecting windows
    assert len(coll) == 3 * 2
    assert {j["window_index"] for j in coll} == {1, 3}  # not the middle window
    # window starting at 0 with offsets [100,200,295] → hours 1,3,4
    w0 = [j for j in coll if j["window_index"] == 1]
    offs = collection_offset_minutes(sched.window_length_hours, 3)
    expected = [((0 * 60 + off) // 60, (0 * 60 + off) % 60) for off in offs]
    assert sorted((j["hour"], j["minute"]) for j in w0) == sorted(expected)


def test_jobs_collection_rate_zero_registers_none(tmp_path):
    _, sched, _, sampler = _build(tmp_path)
    sched.update(windows=_all_windows({"collect": True}), collection_rate=0)
    assert [j for j in sampler.jobs() if j["kind"] == "collection"] == []


def test_jobs_minute_of_day_wraps_past_midnight(tmp_path):
    _, sched, _, sampler = _build(tmp_path)
    # single window starting at 22:00, length 5h, 1 read 295 min in → 02:55
    sched.update(windows=[{"server_start": 22, "collect": True}], collection_rate=1)
    coll = [j for j in sampler.jobs() if j["kind"] == "collection"]
    assert len(coll) == 1
    assert (coll[0]["hour"], coll[0]["minute"]) == (2, 55)


# ----- runners -----
def test_run_collection_records_and_logs(tmp_path):
    bus, sched, stats, sampler = _build(tmp_path)
    sched.update(windows=_all_windows({"collect": True}), collection_rate=2)
    acts = []
    bus.subscribe("activity", lambda p: acts.append(p))
    entry = sampler.run_collection(window_index=3, window_count=5, slot=1, slot_count=2)
    assert entry["source"] == "auto"
    assert entry["session_pct"] == 11 and entry["weekly_pct"] == 22
    assert entry["window_index"] == 3
    assert len(stats.list()) == 1
    assert any("usage sample" in a["message"] for a in acts)


def test_run_collection_noop_when_window_not_collecting(tmp_path):
    _, sched, stats, sampler = _build(tmp_path)
    # rate > 0 but window #1 is not ticked collect → no read taken
    sched.update(windows=_all_windows({"collect": False}), collection_rate=2)
    assert sampler.run_collection(1, 5, 1, 1) is None
    assert stats.list() == []


def test_run_opener_anchors_and_records_zero(tmp_path):
    usage = lambda: {"reason": "ok", "session_pct": 0, "weekly_pct": 60}
    bus, sched, stats, sampler = _build(tmp_path, usage=usage, wakeup=lambda: "ok")
    sched.update(windows=_all_windows({"open": True}))
    acts = []
    bus.subscribe("activity", lambda p: acts.append(p))
    entry = sampler.run_opener(window_index=2, window_count=5)
    assert entry["source"] == "opener"
    assert entry["session_pct"] == 0
    assert entry["weekly_pct"] is None  # openers drop the weekly half
    assert any("opened window 2/5" in a["message"] for a in acts)


def test_run_opener_noop_when_window_not_open(tmp_path):
    _, sched, stats, sampler = _build(tmp_path)
    sched.update(windows=_all_windows({"open": False}))
    assert sampler.run_opener(1, 5) is None
    assert stats.list() == []


def test_run_opener_at_limit(tmp_path):
    usage = lambda: {"reason": "limit_reached", "session_pct": None}
    bus, sched, stats, sampler = _build(tmp_path, usage=usage, wakeup=lambda: "limit")
    sched.update(windows=_all_windows({"open": True}))
    acts = []
    bus.subscribe("activity", lambda p: acts.append(p))
    entry = sampler.run_opener(1, 5)
    assert entry["session_pct"] == 100  # synthesised at-limit reading
    assert any(a["level"] == "warn" and "at usage limit" in a["message"] for a in acts)


def test_run_opener_skips_when_wakeup_disabled(tmp_path):
    bus, sched, stats, sampler = _build(tmp_path, wakeup=lambda: "disabled")
    sched.update(windows=_all_windows({"open": True}))
    acts = []
    bus.subscribe("activity", lambda p: acts.append(p))
    assert sampler.run_opener(1, 5) is None
    assert stats.list() == []  # nothing recorded
    assert any("wakeup disabled" in a["message"] for a in acts)


def test_collect_now_uses_manual_source(tmp_path):
    # Collect-now is independent of the per-window collect flag (it's manual).
    _, sched, stats, sampler = _build(tmp_path)
    entry = sampler.collect_now()
    assert entry["source"] == "manual"
    assert len(stats.list()) == 1


def test_safe_fetch_survives_usage_exception(tmp_path):
    def boom():
        raise RuntimeError("network down")
    _, sched, stats, sampler = _build(tmp_path, usage=boom)
    sched.update(windows=_all_windows({"collect": True}), collection_rate=1)
    entry = sampler.run_collection(1, 5, 1, 1)
    assert entry["reason"] == "fetch_error"  # classified, never raised
