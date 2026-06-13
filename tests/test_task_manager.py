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


_DEFAULT_RUNNER = object()  # sentinel — distinct from None ("production streaming")


def make_tm(tmp_path: Path, vaults=("alpha",), active=True, runner=_DEFAULT_RUNNER):
    log_path = tmp_path / "tasks.jsonl"
    log_dir = tmp_path / "task-logs"
    bus = EventBus()
    win = FakeWindow(active)
    vault_paths = {v: str(tmp_path / v) for v in vaults}
    for v in vaults:
        Path(vault_paths[v]).mkdir(parents=True, exist_ok=True)
    if runner is _DEFAULT_RUNNER:
        runner = lambda cmd, cwd, log_file: 0
    # Passing runner=None to make_tm now exercises the production streaming
    # runner (real subprocess + bus chunk emission); useful for the streaming
    # / cancel-running tests below.
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


def test_force_bypasses_window_gate(tmp_path):
    runner_calls = []
    def runner(cmd, cwd, log_file):
        runner_calls.append(cmd)
        return 0
    tm, _, _ = make_tm(tmp_path, active=False, runner=runner)
    t = tm.create_task(
        "ingest", "alpha", "wiki-ingest",
        {"url": "https://example.com"}, "high", force=True,
    )
    assert t.state == "completed"
    events = [json.loads(l)["event"] for l in (tmp_path / "tasks.jsonl").read_text().strip().splitlines()]
    assert "deferred" not in events
    assert "started" in events


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


def test_wiki_ingest_passes_can_flag_when_update_canvas_true(tmp_path):
    seen = []
    def runner(cmd, cwd, log_file):
        seen.append(list(cmd))
        return 0
    tm, _, _ = make_tm(tmp_path, runner=runner)
    tm.create_task(
        "ingest", "alpha", "wiki-ingest",
        {"url": "https://example.com", "update_canvas": True}, "high",
    )
    assert len(seen) == 1
    cmd = seen[0]
    assert cmd[0].endswith("tools/ingest.sh")
    assert cmd[2] == "https://example.com"
    assert "--can" in cmd


def test_wiki_ingest_omits_can_flag_by_default(tmp_path):
    seen = []
    def runner(cmd, cwd, log_file):
        seen.append(list(cmd))
        return 0
    tm, _, _ = make_tm(tmp_path, runner=runner)
    tm.create_task(
        "ingest", "alpha", "wiki-ingest",
        {"url": "https://example.com"}, "high",
    )
    assert len(seen) == 1
    assert "--can" not in seen[0]


def test_wiki_ingest_prefix_passes_can_flag(tmp_path):
    seen = []
    def runner(cmd, cwd, log_file):
        seen.append(list(cmd))
        return 0
    tm, _, _ = make_tm(tmp_path, runner=runner)
    tm.create_task(
        "ingest", "alpha", "wiki-ingest-prefix",
        {"url": "https://example.com", "update_canvas": True}, "high",
    )
    assert len(seen) == 1
    cmd = seen[0]
    assert "--prefix" in cmd
    assert any(s.endswith("prompts/urlInjestPrefix.md") for s in cmd)
    assert "--can" in cmd


def test_wiki_ingest_prefix_validates_url(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    with pytest.raises(ValueError):
        tm.create_task("x", "alpha", "wiki-ingest-prefix", {"url": ""}, "high")
    with pytest.raises(ValueError):
        tm.create_task("x", "alpha", "wiki-ingest-prefix", {"url": "ftp://x"}, "high")


def test_wiki_ingest_prefix_invokes_ingest_with_prefix_path(tmp_path):
    seen = []
    def runner(cmd, cwd, log_file):
        seen.append(list(cmd))
        return 0
    tm, _, _ = make_tm(tmp_path, runner=runner)
    tm.create_task(
        "ingest", "alpha", "wiki-ingest-prefix",
        {"url": "https://example.com"}, "high",
    )
    # Command shape: [ingest.sh, vault_path, url, --prefix, prefix_file]
    assert len(seen) == 1
    cmd = seen[0]
    assert cmd[0].endswith("tools/ingest.sh")
    assert cmd[2] == "https://example.com"
    assert "--prefix" in cmd
    prefix_idx = cmd.index("--prefix")
    assert cmd[prefix_idx + 1].endswith("prompts/urlInjestPrefix.md")


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


def test_all_vault_force_bypasses_window_gate(tmp_path):
    """A 'run now' all-vaults task must dispatch every child immediately even
    when the window is closed — force has to reach the children, not just the
    parent. Without threading force through, children would be deferred."""
    tm, _, _ = make_tm(tmp_path, vaults=("alpha", "beta"), active=False)
    parent = tm.create_task("lint-all", "ALL", "wiki-lint", {}, "high", force=True)
    children = [t for t in tm._tasks.values() if t.parent_id == parent.id]
    assert len(children) == 2
    assert all(c.state == "completed" for c in children)
    assert tm.get(parent.id).state == "completed"


def test_all_vault_without_force_defers_children_when_window_closed(tmp_path):
    """Counterpart: with the window closed and no force, children defer (and so
    the parent does not complete). Guards the gate against being bypassed."""
    tm, _, _ = make_tm(tmp_path, vaults=("alpha", "beta"), active=False)
    parent = tm.create_task("lint-all", "ALL", "wiki-lint", {}, "high")
    children = [t for t in tm._tasks.values() if t.parent_id == parent.id]
    assert len(children) == 2
    assert all(c.state == "deferred" for c in children)
    assert tm.get(parent.id).state != "completed"


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
    """wiki-bootstrap must invoke `claude -p <prompt> ...` in the vault dir.
    The prompt embeds /claude-obsidian:wiki and (when the prefix/suffix
    instruction files exist alongside the repo) wraps it with them."""
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
    p_idx = call["cmd"].index("-p")
    prompt = call["cmd"][p_idx + 1]
    assert "/claude-obsidian:wiki" in prompt
    assert "--dangerously-skip-permissions" in call["cmd"]
    assert call["cwd"].endswith("alpha")


def test_wiki_bootstrap_wraps_prefix_and_suffix_when_files_present(tmp_path):
    """When tools/newValPrefix.md and tools/newValSuffix.md exist next to the
    repo, the wiki-bootstrap prompt is sandwiched between their contents so
    Claude checks the plugin before bootstrap and copies the visual
    workspace file after."""
    tools = tmp_path / "resman" / "tools"
    tools.mkdir(parents=True)
    (tools / "newValPrefix.md").write_text("PREFIX-CHECK-PLUGIN\n")
    (tools / "newValSuffix.md").write_text("SUFFIX-COPY-WORKSPACE\n")
    runner_calls = []
    def runner(cmd, cwd, log_file):
        runner_calls.append(list(cmd))
        return 0
    tm, _, _ = make_tm(tmp_path, runner=runner)
    tm.create_task("bootstrap", "alpha", "wiki-bootstrap", {}, "high")
    cmd = runner_calls[0]
    prompt = cmd[cmd.index("-p") + 1]
    pre = prompt.index("PREFIX-CHECK-PLUGIN")
    boot = prompt.index("/claude-obsidian:wiki")
    suf = prompt.index("SUFFIX-COPY-WORKSPACE")
    assert pre < boot < suf


def test_wiki_bootstrap_falls_back_when_prefix_suffix_missing(tmp_path):
    """No tools/newVal*.md files → prompt still contains the bootstrap
    slash command and does not crash."""
    runner_calls = []
    def runner(cmd, cwd, log_file):
        runner_calls.append(list(cmd))
        return 0
    tm, _, _ = make_tm(tmp_path, runner=runner)
    tm.create_task("bootstrap", "alpha", "wiki-bootstrap", {}, "high")
    cmd = runner_calls[0]
    prompt = cmd[cmd.index("-p") + 1]
    assert "/claude-obsidian:wiki" in prompt


def test_wiki_bootstrap_defers_when_window_inactive(tmp_path):
    """When the window is between/ended, bootstrap is queued as deferred —
    the wizard surfaces this to the user so they know to start the window."""
    tm, win, _ = make_tm(tmp_path, active=False)
    t = tm.create_task("bootstrap", "alpha", "wiki-bootstrap", {}, "high")
    assert t.state == "deferred"


def test_wiki_hint_runs_claude_with_correct_command(tmp_path):
    """wiki-hint runs `claude -p <WIKI_HINT prompt> --dangerously-skip-permissions`
    in the vault dir. The prompt must instruct Claude to write wiki/hint.json so
    the landing-page card has something to read."""
    runner_calls = []
    def runner(cmd, cwd, log_file):
        runner_calls.append({"cmd": list(cmd), "cwd": cwd})
        return 0
    tm, _, _ = make_tm(tmp_path, runner=runner)
    t = tm.create_task("hint", "alpha", "wiki-hint", {}, "high")
    assert t.state == "completed"
    assert len(runner_calls) == 1
    cmd = runner_calls[0]["cmd"]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    prompt = cmd[cmd.index("-p") + 1]
    assert "wiki/hint.json" in prompt
    assert "claude-obsidian:wiki-query" in prompt
    assert "--dangerously-skip-permissions" in cmd
    assert runner_calls[0]["cwd"].endswith("alpha")


def test_wiki_hint_takes_no_params(tmp_path):
    """wiki-hint is parameterless — like wiki-lint, extra params are ignored
    and an empty params dict is accepted."""
    tm, _, _ = make_tm(tmp_path)
    t = tm.create_task("hint", "alpha", "wiki-hint", {}, "medium")
    assert t.operation == "wiki-hint"
    assert t.state == "completed"


def test_wiki_canvas_validates_description(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    # description is optional — empty / missing is allowed
    tm.create_task("c1", "alpha", "wiki-canvas", {"description": ""}, "high")
    tm.create_task("c2", "alpha", "wiki-canvas", {}, "high")
    # but if supplied, it must be a string and ≤200 chars printable ASCII
    with pytest.raises(ValueError):
        tm.create_task("c3", "alpha", "wiki-canvas", {"description": "x" * 201}, "high")


def test_wiki_canvas_runs_claude_with_correct_command(tmp_path):
    runner_calls = []
    def runner(cmd, cwd, log_file):
        runner_calls.append({"cmd": list(cmd), "cwd": cwd})
        return 0
    tm, _, _ = make_tm(tmp_path, runner=runner)
    t = tm.create_task(
        "canvas", "alpha", "wiki-canvas",
        {"description": "map all ideas and their market connections"}, "high",
    )
    assert t.state == "completed"
    assert len(runner_calls) == 1
    cmd = runner_calls[0]["cmd"]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert any(
        s.startswith("/claude-obsidian:canvas ") and "map all ideas" in s
        for s in cmd
    )
    assert "--dangerously-skip-permissions" in cmd
    assert runner_calls[0]["cwd"].endswith("alpha")


def test_wiki_canvas_without_description_omits_args(tmp_path):
    """Blank description → call /claude-obsidian:canvas with no trailing args,
    letting the plugin use its own defaults."""
    runner_calls = []
    def runner(cmd, cwd, log_file):
        runner_calls.append(list(cmd))
        return 0
    tm, _, _ = make_tm(tmp_path, runner=runner)
    tm.create_task("canvas-default", "alpha", "wiki-canvas", {}, "high")
    assert len(runner_calls) == 1
    cmd = runner_calls[0]
    assert "/claude-obsidian:canvas" in cmd
    # No description appended → exact match, not a prefix-with-trailing-text
    assert not any(
        s.startswith("/claude-obsidian:canvas ") for s in cmd
    )


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


# ============================================================================
# scheduled_for / scheduled state
# ============================================================================

def _future_iso(seconds: int = 3600) -> str:
    from datetime import datetime, timedelta, timezone
    return (
        datetime.now(timezone.utc).replace(microsecond=0)
        + timedelta(seconds=seconds)
    ).isoformat().replace("+00:00", "Z")


def _past_iso(seconds: int = 3600) -> str:
    from datetime import datetime, timedelta, timezone
    return (
        datetime.now(timezone.utc).replace(microsecond=0)
        - timedelta(seconds=seconds)
    ).isoformat().replace("+00:00", "Z")


def test_scheduled_for_creates_scheduled_state(tmp_path):
    """A task with scheduled_for in the future does not dispatch immediately;
    it sits in `scheduled` state and writes a `scheduled` JSONL event so the
    Scheduler can re-arm its one-shot trigger across restarts."""
    runner_calls = []
    def runner(cmd, cwd, log_file):
        runner_calls.append(cmd); return 0
    tm, _, _ = make_tm(tmp_path, runner=runner)
    when = _future_iso(3600)
    t = tm.create_task("x", "alpha", "wiki-lint", {}, "high", scheduled_for=when)
    assert t.state == "scheduled"
    assert t.scheduled_for is not None
    # Runner was never called — task is parked.
    assert runner_calls == []
    # The scheduled event is in the log.
    events = [json.loads(l) for l in (tmp_path / "tasks.jsonl").read_text().splitlines() if l]
    kinds = [e["event"] for e in events if e.get("task_id") == t.id]
    assert "scheduled" in kinds
    assert "started" not in kinds


def test_scheduled_for_in_past_rejected(tmp_path):
    """Scheduling in the past is a user error — fail loud at the API boundary
    instead of silently re-interpreting it as 'now'."""
    tm, _, _ = make_tm(tmp_path)
    with pytest.raises(ValueError):
        tm.create_task("x", "alpha", "wiki-lint", {}, "high",
                       scheduled_for=_past_iso(60))


def test_scheduled_for_with_all_vault_rejected(tmp_path):
    """Mixing scheduled_for with vault=ALL is rejected in v1 — parent/child
    fan-out plus one-shot scheduling combinatorics are not worth the
    complexity until someone actually asks for it."""
    tm, _, _ = make_tm(tmp_path, vaults=("alpha", "beta"))
    with pytest.raises(ValueError):
        tm.create_task("x", "ALL", "wiki-lint", {}, "high",
                       scheduled_for=_future_iso(60))


def test_scheduled_task_emits_task_scheduled_event_on_bus(tmp_path):
    """The Scheduler subscribes to `task_scheduled` to arm a one-shot
    DateTrigger; the bus event must carry both task_id and scheduled_for."""
    tm, _, bus = make_tm(tmp_path)
    seen = []
    bus.subscribe("task_scheduled", lambda p: seen.append(p))
    when = _future_iso(3600)
    t = tm.create_task("x", "alpha", "wiki-lint", {}, "high", scheduled_for=when)
    assert any(p.get("task_id") == t.id and p.get("scheduled_for") for p in seen)


def test_promote_works_on_scheduled_state(tmp_path):
    """`promote` is the same code path used by the Scheduler's one-shot
    fire — it must transition `scheduled` to `pending` and dispatch."""
    runner_calls = []
    def runner(cmd, cwd, log_file):
        runner_calls.append(cmd); return 0
    tm, _, _ = make_tm(tmp_path, runner=runner)
    t = tm.create_task("x", "alpha", "wiki-lint", {}, "high",
                       scheduled_for=_future_iso(3600))
    promoted = tm.promote(t.id)
    assert promoted is not None
    # After promote, the task ran to completion via the runner.
    assert runner_calls
    assert tm.get(t.id).state == "completed"


def test_cancel_scheduled_task(tmp_path):
    """A scheduled task must be cancellable before it fires — otherwise the
    user has no way to abort a 'do this at 23:00' decision they regret at
    22:55."""
    tm, _, _ = make_tm(tmp_path)
    t = tm.create_task("x", "alpha", "wiki-lint", {}, "high",
                       scheduled_for=_future_iso(3600))
    assert tm.cancel(t.id) is True
    assert tm.get(t.id).state == "cancelled"


def test_replay_overdue_scheduled_surfaces_warning(tmp_path):
    """If the server was down at the moment a scheduled task should have
    fired, replay leaves the task in `scheduled` state but surfaces a
    warning. The UX shows it with an 'overdue' badge so the user picks
    whether to run-now or cancel."""
    created = json.dumps({
        "ts": "2026-01-01T00:00:00Z", "event": "created", "task_id": "t-sch",
        "data": {"name": "x", "vault": "alpha", "operation": "wiki-lint",
                  "params": {}, "priority": "high", "schedule": "background",
                  "parent_id": None, "scheduled_for": "2026-01-01T00:00:00Z"},
    })
    scheduled = json.dumps({
        "ts": "2026-01-01T00:00:00Z", "event": "scheduled", "task_id": "t-sch",
        "scheduled_for": "2026-01-01T00:00:00Z",
    })
    log = _write_corrupt_log(tmp_path, created + "\n" + scheduled + "\n")
    tm, _, _ = make_tm(tmp_path)
    summary = _replay_at(tm, log)
    t = tm.get("t-sch")
    assert t is not None
    assert t.state == "scheduled"
    assert any("overdue" in w for w in summary.get("warnings", []))


# ============================================================================
# cancel running + streaming + PID-aware replay
# ============================================================================

def _spawn_threaded(tm):
    """Use a background thread to run the streaming dispatch so tests can
    race with cancel/inspect _procs while the task is still running.
    Mirrors what server.py does with eventlet.spawn in production."""
    import threading
    tm.set_executor(
        lambda task: threading.Thread(
            target=tm._execute, args=(task,), daemon=True,
        ).start()
    )


def test_cancel_running_task_terminates_process(tmp_path):
    """A `running` task must be killable — the v0 implementation only
    accepted pending/deferred, leaving the user with no way to stop a
    hung claude session."""
    import time
    tm, _, _ = make_tm(tmp_path, runner=None)  # production streaming path
    _spawn_threaded(tm)
    t = tm.create_task(
        "sleep", "alpha", "run-shell", {"cmd_parts": ["sleep", "30"]}, "high",
    )
    for _ in range(200):
        if t.id in tm._procs:
            break
        time.sleep(0.02)
    assert t.id in tm._procs, "Popen handle should be tracked for running task"
    ok = tm.cancel(t.id)
    assert ok is True
    assert tm.get(t.id).state == "cancelled"


def test_streaming_runner_emits_task_log_appended(tmp_path):
    """The streaming runner must emit log chunks on the bus so the SPA can
    live-tail the output without polling /api/tasks/<id>/log."""
    import time
    tm, _, bus = make_tm(tmp_path, runner=None)
    _spawn_threaded(tm)
    chunks = []
    bus.subscribe("task_log_appended", lambda p: chunks.append(p))
    t = tm.create_task(
        "echo", "alpha", "run-shell",
        {"cmd_parts": ["sh", "-c", "echo hello && echo world"]}, "high",
    )
    deadline = time.time() + 5
    while time.time() < deadline and tm.get(t.id).state == "running":
        time.sleep(0.02)
    # Give the bus a brief moment to drain final chunks after process exit.
    time.sleep(0.05)
    matching = [c for c in chunks if c.get("task_id") == t.id]
    text = "".join(c.get("chunk", "") for c in matching)
    assert "hello" in text and "world" in text


def test_streaming_runner_records_pid_in_started_event(tmp_path):
    """The `started` event must carry the PID so replay can use
    os.kill(pid, 0) to distinguish 'crashed during run' from 'survived
    server reload' instead of unconditionally marking interrupted."""
    import time
    tm, _, _ = make_tm(tmp_path, runner=None)
    _spawn_threaded(tm)
    t = tm.create_task(
        "echo", "alpha", "run-shell",
        {"cmd_parts": ["sh", "-c", "echo done"]}, "high",
    )
    deadline = time.time() + 5
    while time.time() < deadline and tm.get(t.id).state == "running":
        time.sleep(0.02)
    time.sleep(0.05)
    events = [json.loads(l) for l in (tmp_path / "tasks.jsonl").read_text().splitlines() if l]
    started = [e for e in events if e.get("event") == "started" and e.get("task_id") == t.id]
    assert started, "expected a started event"
    assert isinstance(started[0].get("pid"), int)
    assert started[0]["pid"] > 0


def test_streaming_runner_caps_log_size(tmp_path):
    """A runaway plugin output (GB-scale) would OOM the browser if we
    streamed every byte. The runner truncates with a marker once the cap
    is exceeded."""
    import time
    tm, _, bus = make_tm(tmp_path, runner=None)
    _spawn_threaded(tm)
    chunks = []
    bus.subscribe("task_log_appended", lambda p: chunks.append(p))
    from modules import task_manager as tm_mod
    original_cap = tm_mod.LOG_MAX_BYTES
    tm_mod.LOG_MAX_BYTES = 128
    try:
        t = tm.create_task(
            "noise", "alpha", "run-shell",
            {"cmd_parts": ["sh", "-c", "yes hello | head -c 5000"]}, "high",
        )
        deadline = time.time() + 10
        while time.time() < deadline and tm.get(t.id).state == "running":
            time.sleep(0.02)
        time.sleep(0.05)
    finally:
        tm_mod.LOG_MAX_BYTES = original_cap
    text = "".join(c.get("chunk", "") for c in chunks if c.get("task_id") == t.id)
    assert "output capped" in text


def test_replay_with_alive_pid_keeps_task_running(tmp_path):
    """A `running` task whose recorded PID is still alive must NOT be
    marked interrupted on replay. Only genuinely dead PIDs flip to
    interrupted. Use os.getpid() as a guaranteed-alive PID."""
    import os
    alive_pid = os.getpid()
    created = json.dumps({
        "ts": "2026-01-01T00:00:00Z", "event": "created", "task_id": "t-live",
        "data": {"name": "x", "vault": "alpha", "operation": "wiki-lint",
                  "params": {}, "priority": "high", "schedule": "background",
                  "parent_id": None},
    })
    started = json.dumps({
        "ts": "2026-01-01T00:00:01Z", "event": "started",
        "task_id": "t-live", "pid": alive_pid,
    })
    log = _write_corrupt_log(tmp_path, created + "\n" + started + "\n")
    tm, _, _ = make_tm(tmp_path)
    _replay_at(tm, log)
    t = tm.get("t-live")
    assert t is not None
    # Process is alive → task stays in running, not interrupted.
    assert t.state == "running"


def test_replay_with_dead_pid_marks_interrupted(tmp_path):
    """A `running` task whose PID is gone is genuinely interrupted — same
    behavior as the v0 unconditional rule, but now backed by a real check."""
    # PID 1 is init/systemd and exists on Linux; we want a definitely-dead PID.
    # Pick a very high PID number that won't be in use. Use the 32-bit max
    # range; the kernel won't have allocated this.
    dead_pid = 999999
    created = json.dumps({
        "ts": "2026-01-01T00:00:00Z", "event": "created", "task_id": "t-dead",
        "data": {"name": "x", "vault": "alpha", "operation": "wiki-lint",
                  "params": {}, "priority": "high", "schedule": "background",
                  "parent_id": None},
    })
    started = json.dumps({
        "ts": "2026-01-01T00:00:01Z", "event": "started",
        "task_id": "t-dead", "pid": dead_pid,
    })
    log = _write_corrupt_log(tmp_path, created + "\n" + started + "\n")
    tm, _, _ = make_tm(tmp_path)
    _replay_at(tm, log)
    t = tm.get("t-dead")
    assert t is not None
    assert t.state == "interrupted"


# ----- build_attend_prompt -----
def test_build_attend_prompt_wiki_lint(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    t = tm.create_task("lint", "alpha", "wiki-lint", {}, "high")
    assert tm.build_attend_prompt(t) == "/claude-obsidian:wiki-lint"


def test_build_attend_prompt_wiki_autoresearch_includes_topic(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    t = tm.create_task("r", "alpha", "wiki-autoresearch", {"topic": "elixir"}, "high")
    prompt = tm.build_attend_prompt(t)
    assert prompt == "/claude-obsidian:autoresearch elixir"


def test_build_attend_prompt_wiki_canvas_with_description(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    t = tm.create_task("c", "alpha", "wiki-canvas", {"description": "hubs"}, "high")
    assert tm.build_attend_prompt(t) == "/claude-obsidian:canvas hubs"


def test_build_attend_prompt_wiki_canvas_empty_description(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    t = tm.create_task("c", "alpha", "wiki-canvas", {}, "high")
    assert tm.build_attend_prompt(t) == "/claude-obsidian:canvas"


def test_build_attend_prompt_wiki_update_hot_cache(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    t = tm.create_task("h", "alpha", "wiki-update-hot-cache", {}, "high")
    assert tm.build_attend_prompt(t) == "/claude-obsidian:update-hot-cache"


def test_build_attend_prompt_wiki_hint(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    t = tm.create_task("h", "alpha", "wiki-hint", {}, "high")
    prompt = tm.build_attend_prompt(t)
    assert prompt is not None
    assert "wiki/hint.json" in prompt


def test_build_attend_prompt_wiki_bootstrap_wraps_prefix_suffix(tmp_path):
    tools = tmp_path / "resman" / "tools"
    tools.mkdir(parents=True)
    (tools / "newValPrefix.md").write_text("ATTEND-PREFIX\n")
    (tools / "newValSuffix.md").write_text("ATTEND-SUFFIX\n")
    tm, _, _ = make_tm(tmp_path)
    t = tm.create_task("b", "alpha", "wiki-bootstrap", {}, "high")
    prompt = tm.build_attend_prompt(t)
    pre = prompt.index("ATTEND-PREFIX")
    boot = prompt.index("/claude-obsidian:wiki")
    suf = prompt.index("ATTEND-SUFFIX")
    assert pre < boot < suf


def test_build_attend_prompt_run_prompt_returns_user_prompt(tmp_path):
    tm, _, _ = make_tm(tmp_path)
    t = tm.create_task("p", "alpha", "run-prompt", {"prompt": "summarize wiki"}, "high")
    assert tm.build_attend_prompt(t) == "summarize wiki"


def test_build_attend_prompt_shell_ops_return_none(tmp_path):
    """Shell-based operations don't drive Claude with a prompt, so there's
    nothing meaningful to attend — the API returns None and the route
    rejects the attend request with 400."""
    tm, _, _ = make_tm(tmp_path)
    t1 = tm.create_task("i", "alpha", "wiki-ingest", {"url": "https://example.com"}, "high")
    t2 = tm.create_task("ip", "alpha", "wiki-ingest-prefix", {"url": "https://example.com"}, "high")
    t3 = tm.create_task("s", "alpha", "run-shell", {"cmd_parts": ["ls"]}, "high")
    assert tm.build_attend_prompt(t1) is None
    assert tm.build_attend_prompt(t2) is None
    assert tm.build_attend_prompt(t3) is None
