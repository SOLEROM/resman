"""Tests for TaskManager."""
import json
from pathlib import Path

import pytest

from modules.event_bus import EventBus
from modules.task_manager import TaskManager


class FakeWindow:
    def __init__(self, active: bool = True):
        self.active = active

    def __call__(self) -> bool:
        return self.active


def make_tm(tmp_path: Path, vaults=("alpha",), active=True, runner=None):
    log_path = tmp_path / "tasks.jsonl"
    log_dir = tmp_path / "task-logs"
    bus = EventBus()
    win = FakeWindow(active)
    vault_paths = {v: str(tmp_path / v) for v in vaults}
    for v in vaults:
        Path(vault_paths[v]).mkdir(parents=True, exist_ok=True)
    if runner is None:
        runner = lambda cmd, cwd, log_file: 0
    tm = TaskManager(
        log_path=log_path,
        log_dir=log_dir,
        resman_root=tmp_path / "resman",
        is_window_active=win,
        get_vault_path=lambda n: vault_paths.get(n),
        list_vault_names=lambda: list(vaults),
        bus=bus,
        runner=runner,
    )
    tm.replay()
    return tm, win, bus


def test_create_task_writes_created_event(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    t = tm.create_task("ingest", "alpha", "wiki-ingest", {"url": "https://example.com"}, "high")
    assert t.id.startswith("t-")
    log_lines = (tmp_path / "tasks.jsonl").read_text().strip().splitlines()
    events = [json.loads(l)["event"] for l in log_lines]
    assert "created" in events
    assert "started" in events


def test_window_inactive_defers(tmp_path):
    tm, win, _ = make_tm(tmp_path, active=False)
    t = tm.create_task("ingest", "alpha", "wiki-ingest", {"url": "https://example.com"}, "high")
    assert t.state == "deferred"
    # No 'started' event when deferred
    events = [json.loads(l)["event"] for l in (tmp_path / "tasks.jsonl").read_text().strip().splitlines()]
    assert "started" not in events
    assert "deferred" in events


def test_window_activate_promotes_high_medium_only(tmp_path):
    runner_calls = []
    def runner(cmd, cwd, log_file):
        runner_calls.append(cmd)
        return 0
    tm, win, bus = make_tm(tmp_path, active=False, runner=runner)
    t_high = tm.create_task("a", "alpha", "wiki-lint", {}, "high")
    t_low = tm.create_task("b", "alpha", "wiki-lint", {}, "low")
    assert t_high.state == "deferred"
    assert t_low.state == "deferred"
    # Flip window active and emit
    win.active = True
    bus.emit("window_activated", {})
    assert tm.get(t_high.id).state == "completed"
    assert tm.get(t_low.id).state == "deferred"


def test_invalid_url_rejected(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    with pytest.raises(ValueError):
        tm.create_task("x", "alpha", "wiki-ingest", {"url": "ftp://x"}, "high")


def test_invalid_topic_rejected(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    with pytest.raises(ValueError):
        tm.create_task("x", "alpha", "wiki-autoresearch", {"topic": "a" * 250}, "high")


def test_run_shell_requires_list(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    with pytest.raises(ValueError):
        tm.create_task("x", "alpha", "run-shell", {"cmd_parts": "rm -rf /"}, "high")


def test_run_shell_executes_argument_list(tmp_path):
    runner_args = []
    def runner(cmd, cwd, log_file):
        runner_args.append(list(cmd))
        return 0
    tm, _, _ = make_tm(tmp_path, runner=runner)
    tm.create_task("x", "alpha", "run-shell", {"cmd_parts": ["echo", "hi"]}, "high")
    assert runner_args[0][0] == "echo"


def test_failed_task_records_error(tmp_path):
    def runner(cmd, cwd, log_file):
        return 2
    tm, _, _ = make_tm(tmp_path, runner=runner)
    t = tm.create_task("x", "alpha", "wiki-lint", {}, "high")
    assert t.state == "failed"
    assert t.exit_code == 2


def test_replay_sees_interrupted(tmp_path):
    """A task left in 'started'/'running' across restarts becomes interrupted."""
    log = tmp_path / "tasks.jsonl"
    log.write_text(
        '{"ts":"2026-01-01T00:00:00Z","event":"created","task_id":"t-1","data":{"name":"a","vault":"alpha","operation":"wiki-lint","params":{},"priority":"high"}}\n'
        '{"ts":"2026-01-01T00:00:01Z","event":"started","task_id":"t-1"}\n'
    )
    tm, _, _ = make_tm(tmp_path)  # replay() called inside
    t = tm.get("t-1")
    assert t is not None
    assert t.state == "interrupted"


def test_replay_skips_bad_lines(tmp_path):
    log = tmp_path / "tasks.jsonl"
    log.write_text(
        'this is not json\n'
        '{"ts":"2026-01-01T00:00:00Z","event":"created","task_id":"t-1","data":{"name":"a","vault":"alpha","operation":"wiki-lint","params":{},"priority":"high"}}\n'
    )
    tm, _, _ = make_tm(tmp_path)
    summary = tm._replay_summary()
    assert summary["bad_lines"] >= 1
    assert tm.get("t-1") is not None


def test_replay_truncates_partial_last_line(tmp_path):
    log = tmp_path / "tasks.jsonl"
    log.write_text(
        '{"ts":"2026-01-01T00:00:00Z","event":"created","task_id":"t-1","data":{"name":"a","vault":"alpha","operation":"wiki-lint","params":{},"priority":"high"}}\n'
        '{"ts":"2026-01-01T00:00:01","event":"start'  # partial line
    )
    tm, _, _ = make_tm(tmp_path)
    summary = tm._replay_summary()
    assert summary["partial_truncated"] is True


def test_all_vault_creates_children(tmp_path):
    tm, _, bus = make_tm(tmp_path, vaults=("alpha", "beta"))
    parent = tm.create_task("lint-all", "ALL", "wiki-lint", {}, "high")
    children = [t for t in tm._tasks.values() if t.parent_id == parent.id]
    assert len(children) == 2
    # All ran (default runner returns 0)
    assert all(c.state == "completed" for c in children)
    assert tm.get(parent.id).state == "completed"


def test_cancel_pending_writes_cancelled_event(tmp_path):
    tm, _, _ = make_tm(tmp_path, active=False)
    t = tm.create_task("a", "alpha", "wiki-lint", {}, "low")
    assert t.state == "deferred"
    assert tm.cancel(t.id) is True
    assert tm.get(t.id).state == "cancelled"


def test_archive_terminal_state(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    t = tm.create_task("a", "alpha", "wiki-lint", {}, "high")
    assert t.state == "completed"
    assert tm.archive(t.id) is True
    assert tm.get(t.id).state == "archived"


def test_promote_low_priority_manually(tmp_path):
    runner_calls = []
    def runner(cmd, cwd, log_file):
        runner_calls.append(cmd)
        return 0
    tm, win, _ = make_tm(tmp_path, active=False, runner=runner)
    t = tm.create_task("low", "alpha", "wiki-lint", {}, "low")
    assert t.state == "deferred"
    win.active = True
    tm.promote(t.id)
    assert tm.get(t.id).state == "completed"


def test_invalid_task_name_rejected(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    with pytest.raises(ValueError, match="must match"):
        tm.create_task("bad name!", "alpha", "wiki-lint", {}, "high")


def test_unknown_operation_rejected(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    with pytest.raises(ValueError):
        tm.create_task("x", "alpha", "unknown-op", {}, "high")


def test_unknown_vault_rejected(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    with pytest.raises(ValueError):
        tm.create_task("x", "ghost", "wiki-lint", {}, "high")


def test_wiki_bootstrap_runs_claude_with_correct_command(tmp_path):
    """wiki-bootstrap must invoke `claude -p /claude-obsidian:wiki ...` in
    the vault directory. This is what the new-vault wizard queues after
    scaffolding."""
    runner_calls = []
    def runner(cmd, cwd, log_file):
        runner_calls.append({"cmd": list(cmd), "cwd": cwd})
        return 0
    tm, _, _ = make_tm(tmp_path, runner=runner)
    t = tm.create_task("bootstrap", "alpha", "wiki-bootstrap", {}, "high")
    assert t.state == "completed"
    assert len(runner_calls) == 1
    call = runner_calls[0]
    assert call["cmd"][0] == "claude"
    assert "-p" in call["cmd"]
    assert "/claude-obsidian:wiki" in call["cmd"]
    assert "--dangerously-skip-permissions" in call["cmd"]
    assert call["cwd"].endswith("alpha")


def test_wiki_bootstrap_defers_when_window_inactive(tmp_path):
    """When the window is between/ended, bootstrap is queued as deferred —
    the wizard surfaces this to the user so they know to start the window."""
    tm, win, _ = make_tm(tmp_path, active=False)
    t = tm.create_task("bootstrap", "alpha", "wiki-bootstrap", {}, "high")
    assert t.state == "deferred"


# ============================================================================
# ALL-vault parent/child aggregation
# ============================================================================

def test_all_vault_creates_one_child_per_vault(tmp_path):
    """ALL parent should fan out to one child per registered vault. Parent
    rolls up to completed when every child completed."""
    tm, _, _ = make_tm(tmp_path, vaults=("alpha", "beta", "gamma"))
    parent = tm.create_task("lint", "ALL", "wiki-lint", {}, "high")
    children = [t for t in tm._tasks.values() if t.parent_id == parent.id]
    assert len(children) == 3
    assert {c.vault for c in children} == {"alpha", "beta", "gamma"}
    # default runner returns 0 → all children complete → parent rolls up
    assert tm.get(parent.id).state == "completed"


def test_all_vault_parent_fails_when_one_child_fails(tmp_path):
    """If even one child fails, the parent must roll up to failed (not
    completed). This is the spec's most load-bearing aggregation rule —
    silently rolling up to completed would mean ALL tasks lie about
    success."""
    fail_for = {"beta"}  # this child fails; the others succeed
    def runner(cmd, cwd, log_file):
        # cwd is the per-vault working directory
        return 7 if cwd.endswith("beta") else 0
    tm, _, _ = make_tm(tmp_path, vaults=("alpha", "beta", "gamma"), runner=runner)
    parent = tm.create_task("lint", "ALL", "wiki-lint", {}, "high")
    states = {c.vault: c.state for c in tm._tasks.values() if c.parent_id == parent.id}
    assert states == {"alpha": "completed", "beta": "failed", "gamma": "completed"}
    assert tm.get(parent.id).state == "failed"


def test_all_vault_dispatch_started_event_carries_expected_count(tmp_path):
    """Crash-recovery hook: dispatch_started must be written BEFORE children
    are created, with the expected_child_count = number of registered vaults
    at dispatch time. If the server crashes mid-dispatch the integrity
    check on replay catches the mismatch."""
    tm, _, _ = make_tm(tmp_path, vaults=("alpha", "beta"))
    parent = tm.create_task("lint", "ALL", "wiki-lint", {}, "high")
    log_lines = [json.loads(l) for l in
                 (tmp_path / "tasks.jsonl").read_text().splitlines() if l]
    dispatch_evs = [e for e in log_lines if e.get("event") == "dispatch_started"
                    and e.get("task_id") == parent.id]
    child_evs = [e for e in log_lines if e.get("event") == "child_created"
                 and e.get("task_id") == parent.id]
    assert len(dispatch_evs) == 1
    assert dispatch_evs[0]["expected_child_count"] == 2
    assert len(child_evs) == 2
    # dispatch_started appears before any child_created on the same parent
    log_index = {id(e): i for i, e in enumerate(log_lines)}
    assert log_index[id(dispatch_evs[0])] < min(log_index[id(c)] for c in child_evs)


# ============================================================================
# JSONL crash-recovery
# ============================================================================

def _write_corrupt_log(parent_dir: Path, contents: str) -> Path:
    """Write a hand-crafted JSONL log to a non-default path so the
    `make_tm` helper's own replay() doesn't see it and pre-truncate it."""
    custom = parent_dir / "corrupt"
    custom.mkdir(parents=True, exist_ok=True)
    log = custom / "tasks.jsonl"
    log.write_text(contents)
    return log


def _replay_at(tm, log_path: Path) -> dict:
    tm.log_path = log_path
    return tm.replay()


def test_replay_skips_corrupt_lines(tmp_path):
    """A bad JSON line in the middle of the log must be counted, logged,
    and skipped — surrounding events must still apply."""
    good_create = json.dumps({
        "ts": "2026-01-01T00:00:00Z", "event": "created", "task_id": "t-aaa",
        "data": {"name": "x", "vault": "alpha", "operation": "wiki-lint",
                  "params": {}, "priority": "high", "schedule": "background",
                  "parent_id": None},
    })
    good_complete = json.dumps({
        "ts": "2026-01-01T00:00:01Z", "event": "completed",
        "task_id": "t-aaa", "exit_code": 0,
    })
    log = _write_corrupt_log(
        tmp_path, good_create + "\n{not valid json\n" + good_complete + "\n",
    )
    tm, _, _ = make_tm(tmp_path)
    summary = _replay_at(tm, log)
    assert summary["bad_lines"] == 1
    assert summary["lines"] == 3  # all lines counted, even the bad one
    # The good events around the bad line still applied
    t = tm.get("t-aaa")
    assert t is not None
    assert t.state == "completed"


def test_replay_truncates_partial_final_line(tmp_path):
    """An incomplete final line (no trailing newline) is truncated on
    startup so it can't poison the next replay."""
    complete = json.dumps({
        "ts": "2026-01-01T00:00:00Z", "event": "created", "task_id": "t-bbb",
        "data": {"name": "x", "vault": "alpha", "operation": "wiki-lint",
                  "params": {}, "priority": "high", "schedule": "background",
                  "parent_id": None},
    })
    # Final line has no \n at the end → simulates a write that crashed
    # mid-flush.
    partial = '{"ts":"2026-01-01T00:00:01Z","event":"completed","ta'
    log = _write_corrupt_log(tmp_path, complete + "\n" + partial)
    tm, _, _ = make_tm(tmp_path)
    summary = _replay_at(tm, log)
    assert summary["partial_truncated"] is True
    # File must now end with a newline so subsequent appends are clean.
    assert log.read_bytes().endswith(b"\n")
    # The partial 'completed' line was discarded; the created task is
    # still pending (no terminal event applied).
    t = tm.get("t-bbb")
    assert t is not None
    assert t.state == "pending"


def test_replay_marks_running_as_interrupted(tmp_path):
    """A task that was in 'running' (had a 'started' event but no terminal
    event) at server crash must be replayed as interrupted, not silently
    re-run, not silently dropped."""
    created = json.dumps({
        "ts": "2026-01-01T00:00:00Z", "event": "created", "task_id": "t-ccc",
        "data": {"name": "x", "vault": "alpha", "operation": "wiki-lint",
                  "params": {}, "priority": "high", "schedule": "background",
                  "parent_id": None},
    })
    started = json.dumps({
        "ts": "2026-01-01T00:00:01Z", "event": "started", "task_id": "t-ccc",
    })
    # No completed/failed event ever followed.
    log = _write_corrupt_log(tmp_path, created + "\n" + started + "\n")
    tm, _, _ = make_tm(tmp_path)
    summary = _replay_at(tm, log)
    t = tm.get("t-ccc")
    assert t is not None
    assert t.state == "interrupted"
    # The replay summary surfaces the warning so /api/health can show it.
    assert any("interrupted" in w for w in summary.get("warnings", []))


def test_replay_handles_empty_file(tmp_path):
    """An empty log is a valid first-run state — replay must not crash."""
    log = _write_corrupt_log(tmp_path, "")
    tm, _, _ = make_tm(tmp_path)
    summary = _replay_at(tm, log)
    assert summary["lines"] == 0
    assert summary["bad_lines"] == 0
    assert summary["tasks"] == 0


def test_replay_skips_blank_lines(tmp_path):
    """Blank lines in the log (e.g., from a half-flushed multi-line write)
    must not count as bad lines or cause crashes."""
    created = json.dumps({
        "ts": "2026-01-01T00:00:00Z", "event": "created", "task_id": "t-ddd",
        "data": {"name": "x", "vault": "alpha", "operation": "wiki-lint",
                  "params": {}, "priority": "high", "schedule": "background",
                  "parent_id": None},
    })
    log = _write_corrupt_log(tmp_path, "\n\n" + created + "\n\n")
    tm, _, _ = make_tm(tmp_path)
    summary = _replay_at(tm, log)
    assert summary["bad_lines"] == 0
    assert tm.get("t-ddd") is not None
