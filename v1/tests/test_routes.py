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
from modules.obsidian_push import ObsidianPush
from modules.scheduler import Scheduler


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

    from flask import Flask, render_template
    template_dir = Path(__file__).resolve().parents[1] / "control-plane" / "templates"
    static_dir = Path(__file__).resolve().parents[1] / "control-plane" / "static"
    app = Flask("resman-test", template_folder=str(template_dir), static_folder=str(static_dir))
    app.config["RESMAN"] = {
        "config": cm, "tmux": tmux, "vault_registry": reg, "window": ws,
        "session_manager": sm, "task_manager": tm, "obsidian_push": push,
        "scheduler": scheduler, "bus": bus, "resman_root": tmp_path / "resman",
    }
    from modules.routes import bp
    app.register_blueprint(bp)
    return app, app.config["RESMAN"], runner_calls


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
    """Point resman_root at the real v1 tree so tools/new-vault.sh runs."""
    app, ctx, _ = make_test_app(tmp_path)
    real_root = Path(__file__).resolve().parents[1]  # v1/
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


def test_help_tree_walks_man_directory(tmp_path):
    """make_test_app sets resman_root = tmp_path/resman, so the default man
    location is tmp_path/man — drop a small tree there and verify the API."""
    man = tmp_path / "man"
    man.mkdir()
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
    man = tmp_path / "man"
    man.mkdir()
    (man / "index.md").write_text("# Welcome")
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/help/page")
    assert rv.status_code == 200
    body = rv.get_json()
    assert body["file"] == "index.md"
    assert "Welcome" in body["content"]


def test_help_page_path_traversal_blocked(tmp_path):
    man = tmp_path / "man"
    man.mkdir()
    (tmp_path / "secret.md").write_text("not for you")
    app, _, _ = make_test_app(tmp_path)
    rv = app.test_client().get("/api/help/page?file=../secret.md")
    assert rv.status_code == 400


def test_help_page_rejects_non_markdown(tmp_path):
    """Even if a non-.md file lives in man/, refuse to serve it — keeps the
    surface small and avoids the help tab leaking arbitrary repo files."""
    man = tmp_path / "man"
    man.mkdir()
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
