"""Integration tests for the HTTP API.

These do not start a real server; they use Flask's test_client against the
app composed by build_app(). Subprocess execution is mocked at the runner
boundary so tests run fast and offline.
"""
import sys
import os
from pathlib import Path

# Skip eventlet monkey-patch in tests; we test the Flask app directly.
os.environ.setdefault("RESMAN_TEST", "1")

import pytest

from modules.config_manager import ConfigManager
from modules.event_bus import get_bus
from modules.session_manager import SessionManager
from modules.task_manager import TaskManager
from modules.tmux_manager import TmuxManager
from modules.vault_registry import VaultRegistry
from modules.window_state import WindowState
from modules.window_schedule import WindowSchedule
from modules.obsidian_push import ObsidianPush
from modules.scheduler import Scheduler
from modules.activity_log import ActivityLog


def make_test_app(tmp_path: Path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    vault = tmp_path / "alpha"
    vault.mkdir()
    (vault / ".obsidian").mkdir()
    (cfg_dir / "system.yaml").write_text(
        f"app:\n  host: 127.0.0.1\n  port: 5090\nvaults:\n  - name: alpha\n    path: {vault}\n"
    )
    bus = get_bus()
    bus.clear()
    cm = ConfigManager(cfg_dir, bus)
    cm.load()
    tmux = TmuxManager()
    reg = VaultRegistry(cm, bus)
    reg.reload()
    ws = WindowState(cfg_dir / "budget.json", bus)
    ws.load()
    wsched = WindowSchedule(cfg_dir / "window_schedule.json", bus)
    wsched.load()
    sm = SessionManager(tmux=tmux, port_base=7680, port_max=7700, ttyd_path="ttyd-not-here")
    runner_calls = []
    def runner(cmd, cwd, log_file):
        runner_calls.append((list(cmd), cwd))
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        Path(log_file).write_text("$ ok\n")
        return 0
    tm = TaskManager(
        log_path=cfg_dir / "tasks.jsonl",
        log_dir=cfg_dir / "task-logs",
        resman_root=tmp_path / "resman",
        is_window_active=ws.is_window_active,
        get_vault_path=lambda n: (reg.get(n).path if reg.get(n) else None),
        list_vault_names=reg.all_names,
        bus=bus,
        runner=runner,
    )
    tm.replay()
    push = ObsidianPush(
        vault_iter=lambda: reg.registered,
        get_task_states=lambda n: [t.state for t in tm._tasks.values() if t.vault == n],
        has_session_for=lambda n: any(s.vault == n for s in sm.list()),
    )
    scheduler = Scheduler(cm, tm, push, ws.is_window_active, bus=bus)
    activity = ActivityLog(cfg_dir / "activity.log", bus)

    from flask import Flask, render_template
    template_dir = Path(__file__).resolve().parents[1] / "control-plane" / "templates"
    static_dir = Path(__file__).resolve().parents[1] / "control-plane" / "static"
    app = Flask("resman-test", template_folder=str(template_dir), static_folder=str(static_dir))
    app.config["RESMAN"] = {
        "config": cm, "tmux": tmux, "vault_registry": reg, "window": ws,
        "window_schedule": wsched,
        "session_manager": sm, "task_manager": tm, "obsidian_push": push,
        "scheduler": scheduler, "activity": activity, "bus": bus,
        "resman_root": tmp_path / "resman",
    }
    from modules.routes import bp
    app.register_blueprint(bp)
    return app, app.config["RESMAN"], runner_calls


def test_list_vaults_surfaces_default_root_when_configured(tmp_path):
    """GET /api/vaults must echo app.vault_default_root_path so the
    new-vault wizard can pre-fill its path input without a second fetch."""
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    vault = tmp_path / "alpha"; vault.mkdir(); (vault / ".obsidian").mkdir()
    (cfg_dir / "resman.yaml").write_text(
        "app:\n"
        "  host: 127.0.0.1\n  port: 5090\n"
        "  vault_default_root_path: /home/user/vaults\n"
        f"vaults:\n  - name: alpha\n    path: {vault}\n"
    )
    bus = get_bus(); bus.clear()
    cm = ConfigManager(cfg_dir, bus); cm.load()
    reg = VaultRegistry(cm, bus); reg.reload()
    from flask import Flask
    app = Flask("resman-test-default-root")
    app.config["RESMAN"] = {"config": cm, "vault_registry": reg}
    from modules.routes import bp
    app.register_blueprint(bp)
    rv = app.test_client().get("/api/vaults")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["vault_default_root"] == "/home/user/vaults"
    assert any(v["name"] == "alpha" for v in body["vaults"])


def test_list_vaults_default_root_null_when_unset(tmp_path):
    """Backward compat: the field is optional; an unset config must
    surface `null` rather than missing — frontend treats null as "no
    default" without needing presence checks."""
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/vaults")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["vault_default_root"] is None


def test_health(tmp_path):
    app, ctx, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/health")
    assert rv.status_code == 200
    j = rv.get_json()
    assert j["ttyd"] == "missing"


def test_csrf_required_on_post(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().post("/api/tasks", json={
        "vault": "alpha", "operation": "wiki-lint", "priority": "high"
    })
    assert rv.status_code == 403


def test_csrf_required_on_delete(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().delete("/api/tasks/t-x")
    assert rv.status_code == 403


def test_create_task_with_csrf(tmp_path):
    app, ctx, runner_calls = make_test_app(tmp_path)
    # window not active by default → defer
    ctx["window"].start_window(2)
    client = app.test_client()
    rv = client.post(
        "/api/tasks",
        json={"name": "lint", "vault": "alpha", "operation": "wiki-lint", "priority": "high"},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 201, rv.get_data(as_text=True)
    body = rv.get_json()
    assert body["state"] == "completed"
    # runner saw a claude command
    assert runner_calls and runner_calls[0][0][0] == "claude"


def test_create_task_validates(tmp_path):
    app, ctx, _ = make_test_app(tmp_path)
    ctx["window"].start_window(1)
    rv = app.test_client().post(
        "/api/tasks",
        json={"name": "bad name!", "vault": "alpha", "operation": "wiki-lint", "priority": "high"},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 400


def test_create_task_with_scheduled_for(tmp_path):
    """scheduled_for in the future parks the task in `scheduled` state
    without invoking the runner."""
    from datetime import datetime, timedelta, timezone
    app, ctx, runner_calls = make_test_app(tmp_path)
    ctx["window"].start_window(2)
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0)
    rv = app.test_client().post(
        "/api/tasks",
        json={
            "name": "lint", "vault": "alpha", "operation": "wiki-lint",
            "priority": "high",
            "scheduled_for": when.isoformat().replace("+00:00", "Z"),
        },
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 201, rv.get_data(as_text=True)
    body = rv.get_json()
    assert body["state"] == "scheduled"
    assert body["scheduled_for"]
    # The runner must NOT have fired yet — the whole point of scheduling.
    assert not runner_calls


def test_create_task_scheduled_for_in_past_rejected(tmp_path):
    """The API must reject past timestamps with 400 rather than silently
    treating them as 'run now'."""
    from datetime import datetime, timedelta, timezone
    app, ctx, _ = make_test_app(tmp_path)
    ctx["window"].start_window(2)
    when = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(microsecond=0)
    rv = app.test_client().post(
        "/api/tasks",
        json={
            "name": "lint", "vault": "alpha", "operation": "wiki-lint",
            "priority": "high",
            "scheduled_for": when.isoformat().replace("+00:00", "Z"),
        },
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 400


def test_create_task_scheduled_for_with_all_vault_rejected(tmp_path):
    """ALL + scheduled_for combinatorics are out of scope for v1."""
    from datetime import datetime, timedelta, timezone
    app, ctx, _ = make_test_app(tmp_path)
    ctx["window"].start_window(2)
    when = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0)
    rv = app.test_client().post(
        "/api/tasks",
        json={
            "name": "lint", "vault": "ALL", "operation": "wiki-lint",
            "priority": "high",
            "scheduled_for": when.isoformat().replace("+00:00", "Z"),
        },
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 400


def test_window_start_end(tmp_path):
    app, ctx, _ = make_test_app(tmp_path)
    client = app.test_client()
    rv = client.post(
        "/api/window",
        json={"action": "start", "duration_hours": 3},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 200
    assert rv.get_json()["window_state"] == "active"
    rv = client.post(
        "/api/window",
        json={"action": "end"},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.get_json()["window_state"] == "between"


def test_window_invalid_duration(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().post(
        "/api/window",
        json={"action": "start", "duration_hours": 0},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 400


def test_sessions_503_when_ttyd_missing(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().post(
        "/api/sessions",
        json={"vault": "alpha", "type": "shell"},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 503


def test_sessions_initial_command_validation(tmp_path):
    """initial_command must be a string, ≤200 chars, type='claude'."""
    app, ctx, _ = make_test_app(tmp_path)
    # Force ttyd available so we get past the 503 and into validation
    ctx["session_manager"]._available = True
    client = app.test_client()
    # Wrong type
    rv = client.post(
        "/api/sessions",
        json={"vault": "alpha", "type": "shell", "initial_command": "/wiki"},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 400
    assert "type='claude'" in rv.get_json()["error"]
    # Too long
    rv = client.post(
        "/api/sessions",
        json={"vault": "alpha", "type": "claude", "initial_command": "x" * 201},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 400
    # Wrong python type
    rv = client.post(
        "/api/sessions",
        json={"vault": "alpha", "type": "claude", "initial_command": 42},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 400


def test_sessions_bootstrap_new_vault_validation(tmp_path):
    """bootstrap_new_vault requires type='claude' and is mutually exclusive
    with initial_command."""
    app, ctx, _ = make_test_app(tmp_path)
    ctx["session_manager"]._available = True
    client = app.test_client()
    rv = client.post(
        "/api/sessions",
        json={"vault": "alpha", "type": "shell", "bootstrap_new_vault": True},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 400
    assert "type='claude'" in rv.get_json()["error"]
    rv = client.post(
        "/api/sessions",
        json={
            "vault": "alpha", "type": "claude",
            "bootstrap_new_vault": True,
            "initial_command": "/claude-obsidian:wiki",
        },
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 400
    assert "mutually exclusive" in rv.get_json()["error"]


def test_get_sessions_empty(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/sessions")
    j = rv.get_json()
    assert j["sessions"] == []
    assert j["available"] is False


def test_yaml_get_save(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    client = app.test_client()
    rv = client.get("/api/config/yaml?file=system.yaml")
    assert rv.status_code == 200
    body = rv.get_json()
    assert "alpha" in body["content"]
    new = body["content"]
    rv = client.post(
        "/api/config/yaml",
        json={"file": "system.yaml", "content": new},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 200


def test_yaml_save_rejects_bad(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().post(
        "/api/config/yaml",
        json={"file": "schedule.yaml", "content": "cron_tasks:\n  - name: x\n"},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 400


def test_fs_list_default_returns_home(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/fs/list")
    assert rv.status_code == 200
    body = rv.get_json()
    assert "path" in body and "entries" in body
    # Each entry has at least name + path + is_obsidian
    for e in body["entries"]:
        assert "name" in e and "path" in e and "is_obsidian" in e


def test_fs_list_with_explicit_path(tmp_path):
    """The make_test_app fixture creates `tmp_path/alpha` as its example
    vault, so we put our extras under a sibling subdirectory to avoid
    colliding with that."""
    base = tmp_path / "browse"
    base.mkdir()
    (base / "vault-here").mkdir()
    (base / "vault-here" / ".obsidian").mkdir()
    (base / "plain-dir").mkdir()
    (base / "afile").write_text("not a dir")
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/fs/list?path=" + str(base))
    assert rv.status_code == 200
    body = rv.get_json()
    names = {e["name"]: e for e in body["entries"]}
    # Files filtered out
    assert "afile" not in names
    # Directories shown
    assert "vault-here" in names and "plain-dir" in names
    # is_obsidian flagged correctly
    assert names["vault-here"]["is_obsidian"] is True
    assert names["plain-dir"]["is_obsidian"] is False


def test_fs_list_hides_dotfile_dirs(tmp_path):
    (tmp_path / ".secret").mkdir()
    (tmp_path / "visible").mkdir()
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/fs/list?path=" + str(tmp_path))
    body = rv.get_json()
    names = [e["name"] for e in body["entries"]]
    assert ".secret" not in names
    assert "visible" in names


def test_fs_list_invalid_path(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/fs/list?path=" + str(tmp_path / "nope"))
    assert rv.status_code == 400


def test_fs_list_file_path_rejected(tmp_path):
    f = tmp_path / "file.txt"
    f.write_text("x")
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/fs/list?path=" + str(f))
    assert rv.status_code == 400


def test_fs_list_no_csrf_required(tmp_path):
    """GET endpoint — no X-Requested-With needed."""
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/fs/list?path=" + str(tmp_path))
    assert rv.status_code == 200


def test_register_vault(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().post(
        "/api/vaults",
        json={"name": "beta", "path": str(tmp_path / "beta_path"), "tags": []},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 200
    rv = app.test_client().get("/api/vaults")
    names = [v["name"] for v in rv.get_json()["vaults"]]
    assert "beta" in names


def test_scaffold_csrf_required(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().post(
        "/api/vaults/scaffold",
        json={"name": "gamma", "path": str(tmp_path / "gamma")},
    )
    assert rv.status_code == 403


def test_scaffold_rejects_bad_name(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().post(
        "/api/vaults/scaffold",
        json={"name": "bad name!", "path": str(tmp_path / "x")},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 400


def test_scaffold_rejects_relative_path(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().post(
        "/api/vaults/scaffold",
        json={"name": "rel", "path": "relative/path"},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 400
    assert "absolute" in rv.get_json()["error"]


def test_scaffold_rejects_existing_path(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    existing = tmp_path / "exists"
    existing.mkdir()
    rv = app.test_client().post(
        "/api/vaults/scaffold",
        json={"name": "x", "path": str(existing)},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 409


def test_scaffold_500_when_script_missing(tmp_path):
    """resman_root in the test fixture points at tmp_path / 'resman' which has
    no tools/ subdir, so the script lookup fails — we should return a 500
    with a clear message rather than crashing."""
    app, _, _ = make_test_app(tmp_path)
    target = tmp_path / "newvault"
    rv = app.test_client().post(
        "/api/vaults/scaffold",
        json={"name": "nv", "path": str(target)},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 500
    assert "new-vault.sh" in rv.get_json()["error"]


def test_scaffold_runs_real_script(tmp_path, monkeypatch):
    """Point resman_root at the real repo root so tools/new-vault.sh runs."""
    app, ctx, _ = make_test_app(tmp_path)
    real_root = Path(__file__).resolve().parents[1]  # repo root
    ctx["resman_root"] = real_root
    target = tmp_path / "scaffolded-vault"
    rv = app.test_client().post(
        "/api/vaults/scaffold",
        json={"name": "scaffolded-vault", "path": str(target)},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 200, rv.get_data(as_text=True)
    # new-vault.sh creates the directory + scaffolding
    assert target.exists()
    assert (target / ".obsidian").is_dir()
    assert (target / "README.md").exists()
    assert (target / ".gitignore").exists()
    assert "_resman/" in (target / ".gitignore").read_text()


def test_scaffold_then_register_flow(tmp_path):
    """Full wizard flow: scaffold creates dirs, register adds to system.yaml."""
    app, ctx, _ = make_test_app(tmp_path)
    real_root = Path(__file__).resolve().parents[1]
    ctx["resman_root"] = real_root
    target = tmp_path / "wizard-flow"
    client = app.test_client()
    # Step 1: scaffold
    rv = client.post(
        "/api/vaults/scaffold",
        json={"name": "wizard-flow", "path": str(target)},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 200
    # Step 2: register
    rv = client.post(
        "/api/vaults",
        json={"name": "wizard-flow", "path": str(target), "tags": ["test"]},
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 200
    rv = client.get("/api/vaults")
    names = [v["name"] for v in rv.get_json()["vaults"]]
    assert "wizard-flow" in names


def test_promote_archive_cancel(tmp_path):
    app, ctx, _ = make_test_app(tmp_path)
    # Window inactive → tasks deferred
    rv = app.test_client().post(
        "/api/tasks",
        json={"name": "lint", "vault": "alpha", "operation": "wiki-lint", "priority": "low"},
        headers={"X-Requested-With": "resman"},
    )
    tid = rv.get_json()["id"]
    # Cancel
    rv = app.test_client().delete(
        "/api/tasks/" + tid, headers={"X-Requested-With": "resman"}
    )
    assert rv.get_json()["ok"]


def test_vault_wiki_default_is_overview_md(tmp_path):
    """Wiki tab defaults to wiki/overview.md — the landing page resman opens
    when the tab is shown. Hot / Index are reachable via the toolbar."""
    app, ctx, _ = make_test_app(tmp_path)
    vault = Path(ctx["vault_registry"].get("alpha").path)
    (vault / "wiki").mkdir()
    (vault / "wiki" / "overview.md").write_text("# Alpha overview\n\nHello.")
    rv = app.test_client().get("/api/vaults/alpha/wiki")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["file"] == "wiki/overview.md"
    assert "Hello" in body["content"]


def test_vault_wiki_canonical_pages(tmp_path):
    """The three canonical pages — hot/index/overview — are all explicit
    file= values, not implicit defaults; verify each is fetchable so the
    toolbar buttons stay honest."""
    app, ctx, _ = make_test_app(tmp_path)
    vault = Path(ctx["vault_registry"].get("alpha").path)
    (vault / "wiki").mkdir()
    for page in ("hot", "index", "overview"):
        (vault / "wiki" / f"{page}.md").write_text(f"# {page}")
    for page in ("hot", "index", "overview"):
        rv = app.test_client().get(f"/api/vaults/alpha/wiki?file=wiki/{page}.md")
        assert rv.status_code == 200, page
        assert page in rv.get_json()["content"]


def test_vault_wiki_explicit_subpage(tmp_path):
    app, ctx, _ = make_test_app(tmp_path)
    vault = Path(ctx["vault_registry"].get("alpha").path)
    (vault / "wiki").mkdir()
    (vault / "wiki" / "topics.md").write_text("# Topics")
    rv = app.test_client().get("/api/vaults/alpha/wiki?file=wiki/topics.md")
    assert rv.status_code == 200
    assert "Topics" in rv.get_json()["content"]


def test_vault_wiki_path_traversal_blocked(tmp_path):
    """Path traversal must be refused; ../ resolved up out of vault → 400."""
    (tmp_path / "secret.md").write_text("not for you")
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/vaults/alpha/wiki?file=../secret.md")
    assert rv.status_code == 400


def test_vault_wiki_missing_returns_404_with_file_hint(tmp_path):
    """When the default wiki page is absent the JSON 404 carries the requested
    file so the SPA can render a 'no wiki yet' empty state."""
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/vaults/alpha/wiki")
    assert rv.status_code == 404
    body = rv.get_json()
    assert body.get("file") == "wiki/overview.md"


def test_vault_wiki_unknown_vault(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/vaults/zzz/wiki")
    assert rv.status_code == 404


def test_vault_wiki_tree_lists_markdown_recursively(tmp_path):
    """The tree endpoint walks wiki/ recursively, sorts dirs before files,
    and emits vault-relative paths so the SPA can navigate directly."""
    app, ctx, _ = make_test_app(tmp_path)
    vault = Path(ctx["vault_registry"].get("alpha").path)
    (vault / "wiki").mkdir()
    (vault / "wiki" / "overview.md").write_text("# Overview")
    (vault / "wiki" / "index.md").write_text("# Index")
    (vault / "wiki" / "sources").mkdir()
    (vault / "wiki" / "sources" / "alpha.md").write_text("# Alpha")
    # Non-markdown is ignored.
    (vault / "wiki" / "ignored.txt").write_text("nope")
    rv = app.test_client().get("/api/vaults/alpha/wiki/tree")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["missing"] is False
    tree = data["tree"]
    # Dirs sort before files, both alpha-sorted within their bucket.
    assert [n["name"] for n in tree] == ["sources", "index.md", "overview.md"]
    # Subdir paths are wiki-rooted, vault-relative.
    sources = tree[0]
    assert sources["type"] == "dir"
    assert sources["path"] == "wiki/sources"
    assert sources["children"][0]["path"] == "wiki/sources/alpha.md"
    # Top-level file path also wiki-prefixed.
    assert tree[1]["path"] == "wiki/index.md"


def test_vault_wiki_tree_missing_dir_returns_missing_flag(tmp_path):
    """No wiki/ folder yet (fresh vault) → missing:true so the UI can
    render a 'no wiki' state instead of an empty list."""
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/vaults/alpha/wiki/tree")
    assert rv.status_code == 200
    data = rv.get_json()
    assert data["missing"] is True
    assert data["tree"] == []


def test_vault_wiki_tree_unknown_vault(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/vaults/zzz/wiki/tree")
    assert rv.status_code == 404


def test_vault_wiki_tree_skips_dotfiles_and_symlinks(tmp_path):
    """Hidden entries and symlinks are excluded — keeps the surface small
    and prevents accidental loops if the user drops one in."""
    app, ctx, _ = make_test_app(tmp_path)
    vault = Path(ctx["vault_registry"].get("alpha").path)
    (vault / "wiki").mkdir()
    (vault / "wiki" / "visible.md").write_text("# Visible")
    (vault / "wiki" / ".hidden.md").write_text("# Hidden")
    (vault / "wiki" / "link.md").symlink_to(vault / "wiki" / "visible.md")
    rv = app.test_client().get("/api/vaults/alpha/wiki/tree")
    assert rv.status_code == 200
    names = [n["name"] for n in rv.get_json()["tree"]]
    assert names == ["visible.md"]


def test_help_tree_walks_man_directory(tmp_path):
    """make_test_app sets resman_root = tmp_path/resman, so the default man
    location is tmp_path/resman/man — drop a small tree there and verify the API."""
    man = tmp_path / "resman" / "man"
    man.mkdir(parents=True)
    (man / "index.md").write_text("# Index")
    (man / "vaults.md").write_text("# Vaults")
    (man / "reference").mkdir()
    (man / "reference" / "api.md").write_text("# API")
    (man / "ignore.txt").write_text("not markdown")

    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/help/tree")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["missing"] is False
    names = sorted(n["name"] for n in body["tree"])
    # only .md files at top level + the reference/ dir
    assert names == ["index.md", "reference", "vaults.md"]
    ref = next(n for n in body["tree"] if n["name"] == "reference")
    assert ref["type"] == "dir"
    assert ref["children"][0]["name"] == "api.md"
    assert ref["children"][0]["path"] == "reference/api.md"


def test_help_page_default_is_index(tmp_path):
    man = tmp_path / "resman" / "man"
    man.mkdir(parents=True)
    (man / "index.md").write_text("# Welcome")
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/help/page")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["file"] == "index.md"
    assert "Welcome" in body["content"]


def test_help_page_path_traversal_blocked(tmp_path):
    man = tmp_path / "resman" / "man"
    man.mkdir(parents=True)
    (tmp_path / "secret.md").write_text("not for you")
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/help/page?file=../secret.md")
    assert rv.status_code == 400


def test_help_page_rejects_non_markdown(tmp_path):
    """Even if a non-.md file lives in man/, refuse to serve it — keeps the
    surface small and avoids the help tab leaking arbitrary repo files."""
    man = tmp_path / "resman" / "man"
    man.mkdir(parents=True)
    (man / "secret.txt").write_text("nope")
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/help/page?file=secret.txt")
    assert rv.status_code == 400


def test_help_tree_missing_directory(tmp_path):
    """When no man/ exists at all, return missing=true so the SPA can show a
    helpful empty state rather than a generic error."""
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/help/tree")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["missing"] is True
    assert body["tree"] == []


def test_open_obsidian_csrf_required(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().post("/api/vaults/alpha/open")
    assert rv.status_code == 403


def test_open_obsidian_no_command_configured(tmp_path):
    """If obsidian_cmd is empty (the default in the test config), 400."""
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().post(
        "/api/vaults/alpha/open", headers={"X-Requested-With": "resman"}
    )
    assert rv.status_code == 400
    assert "obsidian_cmd" in rv.get_json()["error"]


def test_open_obsidian_unknown_vault(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().post(
        "/api/vaults/zzz/open", headers={"X-Requested-With": "resman"}
    )
    assert rv.status_code == 404


def test_open_obsidian_launches_configured_cmd(tmp_path, monkeypatch):
    """obsidian_cmd is split with shlex; vault path is appended; subprocess
    runs detached. We assert the argv list passed to Popen rather than
    actually launching anything."""
    import modules.routes as routes_mod
    app, ctx, _ = make_test_app(tmp_path)
    ctx["config"].app["obsidian_cmd"] = "echo open-vault"

    captured = {}
    class FakePopen:
        def __init__(self, args, **kwargs):
            captured["args"] = list(args)
            captured["kwargs"] = kwargs
    monkeypatch.setattr(routes_mod.subprocess, "Popen", FakePopen)
    rv = app.test_client().post(
        "/api/vaults/alpha/open", headers={"X-Requested-With": "resman"}
    )
    assert rv.status_code == 200
    assert captured["args"][:2] == ["echo", "open-vault"]
    # vault path appended
    assert captured["args"][-1] == ctx["vault_registry"].get("alpha").path


def test_vault_health_includes_extras(tmp_path):
    """Health endpoint exposes last_session_at, last_completed_task_at, tags."""
    app, ctx, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/vaults/alpha/health")
    assert rv.status_code == 200
    body = rv.get_json()
    for key in (
        "name", "path", "path_exists", "obsidian_dir", "wiki_home_exists",
        "last_session_at", "last_completed_task_at", "tags",
    ):
        assert key in body, f"missing key {key}"


def test_task_log_endpoint(tmp_path):
    app, ctx, _ = make_test_app(tmp_path)
    ctx["window"].start_window(2)
    rv = app.test_client().post(
        "/api/tasks",
        json={"name": "lint", "vault": "alpha", "operation": "wiki-lint", "priority": "high"},
        headers={"X-Requested-With": "resman"},
    )
    tid = rv.get_json()["id"]
    rv = app.test_client().get("/api/tasks/" + tid + "/log")
    assert rv.status_code == 200
    assert rv.mimetype == "text/plain"


# ----- /api/tasks/<id>/attend -----
class _FakeSession:
    """Stand-in for SessionManager.spawn() in attend tests — no ttyd needed."""

    def __init__(self, vault: str, kwargs: dict):
        self.id = "s-test"
        self.vault = vault
        self.session_type = "claude"
        self.tmux_session = f"rsm-{vault}-claude-1"
        self.port = 7681
        self.kwargs = kwargs

    def to_dict(self):
        return {
            "id": self.id, "vault": self.vault, "session_type": self.session_type,
            "tmux_session": self.tmux_session, "port": self.port,
        }


def _install_fake_spawn(ctx, capture: list):
    """Patch session_manager so .spawn() records the kwargs instead of running ttyd."""
    sm = ctx["session_manager"]
    sm._available = True

    def fake_spawn(**kwargs):
        capture.append(kwargs)
        return _FakeSession(kwargs.get("vault", ""), kwargs)

    sm.spawn = fake_spawn  # type: ignore[assignment]


def _make_task(app, vault: str, operation: str, params: dict, ctx) -> str:
    ctx["window"].start_window(2)
    rv = app.test_client().post(
        "/api/tasks",
        json={
            "name": "x", "vault": vault, "operation": operation,
            "params": params, "priority": "high",
        },
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 201, rv.get_data(as_text=True)
    return rv.get_json()["id"]


def test_attend_unknown_task_returns_404(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().post(
        "/api/tasks/t-nope/attend",
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 404


def test_attend_shell_operation_rejected(tmp_path):
    """run-shell has no Claude prompt to re-run — attend returns 400."""
    app, ctx, _ = make_test_app(tmp_path)
    tid = _make_task(app, "alpha", "run-shell", {"cmd_parts": ["true"]}, ctx)
    capture: list = []
    _install_fake_spawn(ctx, capture)
    rv = app.test_client().post(
        f"/api/tasks/{tid}/attend",
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 400
    assert "not attendable" in rv.get_json()["error"]
    assert capture == []  # spawn never invoked


def test_attend_wiki_lint_spawns_claude_session_with_prompt(tmp_path):
    app, ctx, _ = make_test_app(tmp_path)
    tid = _make_task(app, "alpha", "wiki-lint", {}, ctx)
    capture: list = []
    _install_fake_spawn(ctx, capture)
    rv = app.test_client().post(
        f"/api/tasks/{tid}/attend",
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 201, rv.get_data(as_text=True)
    assert len(capture) == 1
    kwargs = capture[0]
    assert kwargs["vault"] == "alpha"
    assert kwargs["session_type"] == "claude"
    assert kwargs["initial_text"] == "/claude-obsidian:wiki-lint"
    # initial_command must not be set — we use bracketed paste instead.
    assert kwargs.get("initial_command") is None


def test_attend_wiki_autoresearch_passes_topic(tmp_path):
    app, ctx, _ = make_test_app(tmp_path)
    tid = _make_task(app, "alpha", "wiki-autoresearch", {"topic": "graphs"}, ctx)
    capture: list = []
    _install_fake_spawn(ctx, capture)
    rv = app.test_client().post(
        f"/api/tasks/{tid}/attend",
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 201
    assert capture[0]["initial_text"] == "/claude-obsidian:autoresearch graphs"


def test_attend_requires_csrf(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().post("/api/tasks/t-x/attend")
    assert rv.status_code == 403


def test_sessions_stats_returns_payload_shape(tmp_path):
    """The overview modal needs a stable payload shape even when no sessions
    are live. We stub SessionManager.stats() so the route is exercised in
    isolation from the /proc walker."""
    app, ctx, _ = make_test_app(tmp_path)
    fake = {
        "available": True,
        "session_count": 1,
        "total_rss_kb": 12345,
        "tmux_socket": "resman",
        "sessions": [{
            "id": "s-1", "vault": "alpha", "session_type": "claude",
            "tmux_session": "rsm-alpha-claude-1", "port": 7681,
            "created_at": "2026-05-14T08:00:00Z", "age_seconds": 60,
            "alive": True,
            "ttyd": {"pid": 100, "rss_kb": 5000, "comm": "ttyd"},
            "panes": [{
                "pane_pid": 200, "rss_kb": 7345,
                "processes": [
                    {"pid": 200, "comm": "bash", "ppid": 100, "rss_kb": 1500},
                    {"pid": 300, "comm": "claude", "ppid": 200, "rss_kb": 5845},
                ],
            }],
            "total_rss_kb": 12345,
        }],
        "orphaned_tmux_sessions": ["rsm-stale-claude-2"],
    }
    ctx["session_manager"].stats = lambda: fake  # type: ignore[assignment]
    rv = app.test_client().get("/api/sessions/stats")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body == fake


def test_kill_orphan_sessions_returns_report(tmp_path):
    """POST /api/sessions/orphans/kill must invoke the SessionManager method
    and pass its report straight through, so the UI can show killed/failed
    counts after the modal refresh."""
    app, ctx, _ = make_test_app(tmp_path)
    ctx["session_manager"]._available = True  # type: ignore[attr-defined]
    report = {"killed": ["rsm-stale-a", "rsm-stale-b"], "failed": []}
    ctx["session_manager"].kill_orphaned_tmux_sessions = lambda: report  # type: ignore[assignment]
    rv = app.test_client().post(
        "/api/sessions/orphans/kill",
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 200
    assert rv.get_json() == report


def test_kill_orphan_sessions_requires_csrf(tmp_path):
    """Without the CSRF header the endpoint must refuse — orphan-kill is a
    destructive action and we don't want cross-origin form submits to fire it."""
    app, ctx, _ = make_test_app(tmp_path)
    ctx["session_manager"]._available = True  # type: ignore[attr-defined]
    ctx["session_manager"].kill_orphaned_tmux_sessions = lambda: {  # type: ignore[assignment]
        "killed": [], "failed": [],
    }
    rv = app.test_client().post("/api/sessions/orphans/kill")
    assert rv.status_code == 403


def test_kill_orphan_sessions_ttyd_unavailable(tmp_path):
    """When ttyd isn't installed we don't even probe — return an empty
    report with a note so the UI can still render without error."""
    app, ctx, _ = make_test_app(tmp_path)
    ctx["session_manager"]._available = False  # type: ignore[attr-defined]
    rv = app.test_client().post(
        "/api/sessions/orphans/kill",
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["killed"] == []
    assert body["failed"] == []


def test_sessions_stats_no_csrf_required(tmp_path):
    """The overview is a read-only GET, so no header is required."""
    app, ctx, _ = make_test_app(tmp_path)
    ctx["session_manager"].stats = lambda: {  # type: ignore[assignment]
        "available": False, "session_count": 0, "total_rss_kb": 0,
        "tmux_socket": "resman", "sessions": [], "orphaned_tmux_sessions": [],
    }
    rv = app.test_client().get("/api/sessions/stats")
    assert rv.status_code == 200


def test_attend_returns_503_when_ttyd_missing(tmp_path):
    """Attend can't open an interactive session without ttyd."""
    app, ctx, _ = make_test_app(tmp_path)
    tid = _make_task(app, "alpha", "wiki-lint", {}, ctx)
    # Leave sm._available False (default in the fixture).
    rv = app.test_client().post(
        f"/api/tasks/{tid}/attend",
        headers={"X-Requested-With": "resman"},
    )
    assert rv.status_code == 503


# ----- Wiki read/unread + search + random -----
def _seed_wiki(tmp_path):
    """Create a small wiki/ tree inside the fixture's `alpha` vault."""
    wiki = tmp_path / "alpha" / "wiki"
    (wiki / "concepts").mkdir(parents=True)
    (wiki / "overview.md").write_text("# Overview\n\nThe vault landing page.\n")
    (wiki / "concepts" / "gguf.md").write_text("# GGUF\n\nA tensor file format.\n")
    return wiki


def test_wiki_tree_reports_unread_flags(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    _seed_wiki(tmp_path)
    rv = app.test_client().get("/api/vaults/alpha/wiki/tree")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["missing"] is False
    # First reconcile marks every page unread.
    flat = []
    def walk(ns):
        for n in ns:
            if n["type"] == "file":
                flat.append(n)
            else:
                walk(n.get("children", []))
    walk(body["tree"])
    assert flat, "expected file nodes"
    assert all(n["unread"] for n in flat)


def test_wiki_read_toggle_roundtrip(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    _seed_wiki(tmp_path)
    client = app.test_client()
    client.get("/api/vaults/alpha/wiki/tree")  # reconcile → all unread
    # Mark read.
    rv = client.post(
        "/api/vaults/alpha/wiki/read",
        headers={"X-Requested-With": "resman"},
        json={"file": "wiki/concepts/gguf.md", "read": True},
    )
    assert rv.status_code == 200
    assert rv.get_json()["unread"] is False
    # Mark unread again.
    rv = client.post(
        "/api/vaults/alpha/wiki/read",
        headers={"X-Requested-With": "resman"},
        json={"file": "wiki/concepts/gguf.md", "read": False},
    )
    assert rv.get_json()["unread"] is True


def test_wiki_read_requires_csrf(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    _seed_wiki(tmp_path)
    rv = app.test_client().post(
        "/api/vaults/alpha/wiki/read",
        json={"file": "wiki/overview.md", "read": True},
    )
    assert rv.status_code == 403


def test_wiki_random_returns_unread_then_none(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    _seed_wiki(tmp_path)
    client = app.test_client()
    rv = client.get("/api/vaults/alpha/wiki/random")
    assert rv.status_code == 200
    assert rv.get_json()["file"] in ("wiki/overview.md", "wiki/concepts/gguf.md")
    # Mark everything read, then random must report nothing.
    for f in ("wiki/overview.md", "wiki/concepts/gguf.md"):
        client.post("/api/vaults/alpha/wiki/read",
                    headers={"X-Requested-With": "resman"},
                    json={"file": f, "read": True})
    assert client.get("/api/vaults/alpha/wiki/random").get_json()["file"] is None


def test_wiki_search_ranks_and_highlights(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    _seed_wiki(tmp_path)
    rv = app.test_client().get("/api/vaults/alpha/wiki/search?q=gguf")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["query"] == "gguf"
    assert body["hits"], "expected a hit for 'gguf'"
    assert body["hits"][0]["rel"] == "concepts/gguf.md"
    assert "gguf" in body["hits"][0]["snippet"].lower()


def test_wiki_endpoints_404_for_unknown_vault(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    client = app.test_client()
    assert client.get("/api/vaults/ghost/wiki/random").status_code == 404
    assert client.get("/api/vaults/ghost/wiki/search?q=x").status_code == 404
    rv = client.post("/api/vaults/ghost/wiki/read",
                     headers={"X-Requested-With": "resman"},
                     json={"file": "wiki/x.md", "read": True})
    assert rv.status_code == 404


# ----- Window schedule (cld20 model) -----
def test_window_schedule_get_defaults(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/window/schedule")
    assert rv.status_code == 200
    body = rv.get_json()
    assert [w["server_start"] for w in body["windows"]] == [0, 5, 10, 15, 20]
    assert "status" in body and "current" in body["status"]


def test_window_schedule_put_updates(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    client = app.test_client()
    rv = client.put(
        "/api/window/schedule",
        headers={"X-Requested-With": "resman"},
        json={"windows": [
            {"server_start": 9, "night_window": False},
            {"server_start": 22, "night_window": True},
        ], "weekly_anchor": {"weekday": 0, "hour": 9}},
    )
    assert rv.status_code == 200
    body = rv.get_json()
    assert [w["server_start"] for w in body["windows"]] == [9, 22]
    assert body["windows"][1]["night_window"] is True


def test_window_schedule_put_validates(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().put(
        "/api/window/schedule",
        headers={"X-Requested-With": "resman"},
        json={"windows": [{"server_start": 99, "night_window": False}]},
    )
    assert rv.status_code == 400


def test_window_schedule_put_requires_csrf(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().put("/api/window/schedule", json={"operator_hour_offset": 1})
    assert rv.status_code == 403


def test_window_next_night(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    client = app.test_client()
    client.put("/api/window/schedule",
               headers={"X-Requested-With": "resman"},
               json={"windows": [{"server_start": 1, "night_window": True}]})
    rv = client.get("/api/window/next-night")
    assert rv.status_code == 200
    assert rv.get_json()["at"] is not None


def test_window_sync_stamps_state(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().post(
        "/api/window/sync", headers={"X-Requested-With": "resman"})
    assert rv.status_code == 200
    body = rv.get_json()
    # The sync stamps synced_at and returns the full state (status + log).
    assert body["status"]["usage"]["synced_at"] is not None
    assert any("manual sync" in e["message"] for e in body["log"])


def test_window_sync_requires_csrf(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().post("/api/window/sync")
    assert rv.status_code == 403


# ----- Activity log -----
def test_logs_get_returns_entries(tmp_path):
    app, ctx, _ = make_test_app(tmp_path)
    ctx["activity"].record("hello from test", source="test")
    rv = app.test_client().get("/api/logs")
    assert rv.status_code == 200
    msgs = [e["message"] for e in rv.get_json()["entries"]]
    assert "hello from test" in msgs


def test_logs_level_filter(tmp_path):
    app, ctx, _ = make_test_app(tmp_path)
    ctx["activity"].record("an info", level="info")
    ctx["activity"].record("an error", level="error")
    rv = app.test_client().get("/api/logs?level=error")
    msgs = [e["message"] for e in rv.get_json()["entries"]]
    assert "an error" in msgs and "an info" not in msgs


def test_logs_clear(tmp_path):
    app, ctx, _ = make_test_app(tmp_path)
    ctx["activity"].record("doomed")
    rv = app.test_client().post("/api/logs/clear", headers={"X-Requested-With": "resman"})
    assert rv.status_code == 200
    msgs = [e["message"] for e in app.test_client().get("/api/logs").get_json()["entries"]]
    assert "doomed" not in msgs


def test_logs_clear_requires_csrf(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    assert app.test_client().post("/api/logs/clear").status_code == 403


def test_window_sync_writes_activity_log(tmp_path):
    # The ⟳ sync button must leave a trail in the activity log (start + result).
    app, _, _ = make_test_app(tmp_path)
    client = app.test_client()
    client.post("/api/window/sync", headers={"X-Requested-With": "resman"})
    msgs = [e["message"] for e in client.get("/api/logs").get_json()["entries"]]
    assert any("window limit sync started" in m for m in msgs)
    assert any("window limit sync" in m and "started" not in m for m in msgs)


def test_task_create_writes_activity_log(tmp_path):
    app, _, _ = make_test_app(tmp_path)
    client = app.test_client()
    client.post(
        "/api/tasks",
        headers={"X-Requested-With": "resman"},
        json={"vault": "alpha", "operation": "wiki-lint", "priority": "high"},
    )
    msgs = [e["message"] for e in client.get("/api/logs").get_json()["entries"]]
    assert any("task queued" in m for m in msgs)
