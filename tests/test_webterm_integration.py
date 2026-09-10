"""webterm integration — resman's session model mapped onto the shared library.

The library owns the PTY, transport, rendering and security; everything
resman-specific lives in the spawn resolver, so these tests are about the
SpawnSpec it produces and the policy around it. No ttyd, no ports.
"""
from __future__ import annotations

import re

import pytest

from modules import webterm_integration as wi
from modules.session_plan import SessionPlan
from webterm import SpawnError


def make_app(tmp_path, *, use_webterm=True):
    from server import build_app
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    vault = tmp_path / "alpha"
    vault.mkdir(exist_ok=True)
    (vault / ".obsidian").mkdir(exist_ok=True)
    (cfg / "resman.yaml").write_text(
        f"app:\n  host: 127.0.0.1\n  port: 5090\n  tmux_socket: resman-test\n"
        f"  tmux_prefix: rst-\nvaults:\n  - name: alpha\n    path: {vault}\n")
    return build_app(cfg, async_mode="threading", use_webterm=use_webterm)


@pytest.fixture
def app_ctx(tmp_path):
    app, _sio, ctx = make_app(tmp_path)
    app.config["TESTING"] = True
    return app, ctx


@pytest.fixture
def resolver(app_ctx):
    app, ctx = app_ctx
    with app.test_request_context():
        yield wi.make_spawn_resolver(ctx)


# ----- the browser-facing shape -----
def test_a_shell_session_opens_in_the_vault(resolver, tmp_path):
    spec = resolver({"vault": "alpha", "type": "shell"}).validate()
    assert spec.cwd == str(tmp_path / "alpha")
    assert spec.command is None
    assert spec.initial_command is None      # a shell is tmux's own default
    assert spec.initial_paste is None
    assert spec.metadata["vault"] == "alpha"
    assert spec.metadata["session_type"] == "shell"


def test_a_claude_session_launches_the_configured_command(resolver):
    spec = resolver({"vault": "alpha", "type": "claude"}).validate()
    # the configured claude_cmd is a shell-style string, so it keeps its shell
    assert spec.initial_command == ["sh", "-c", "claude"]
    assert spec.command is None               # typed into a fresh shell


def test_bash_is_accepted_as_an_alias_for_shell(resolver):
    assert resolver({"vault": "alpha", "type": "bash"}).metadata[
        "session_type"] == "shell"


def test_an_unknown_vault_is_refused(resolver):
    with pytest.raises(SpawnError):
        resolver({"vault": "ghost", "type": "shell"})


def test_an_unknown_session_type_is_refused(resolver):
    with pytest.raises(SpawnError):
        resolver({"vault": "alpha", "type": "root"})


# ----- seeded sessions: the paste that submits -----
def test_a_slash_command_is_delivered_as_a_submitted_message(resolver):
    spec = resolver({"vault": "alpha", "type": "claude",
                     "initial_command": "/claude-obsidian:wiki"}).validate()
    assert spec.initial_paste == "/claude-obsidian:wiki"
    assert spec.submit_after_paste is True
    assert spec.metadata["seeded"] is True


def test_a_seeded_session_waits_for_the_repl(resolver):
    """The delivery ends in Enter, so it must not fire at whatever is on
    screen: a paste into the bash shell that precedes Claude would be
    *executed*."""
    spec = resolver({"vault": "alpha", "type": "claude",
                     "initial_command": "/claude-obsidian:wiki"})
    probe = spec.paste_when_ready
    assert probe is not None
    assert probe.timeout_seconds >= 15


def test_an_unseeded_session_never_presses_enter(resolver):
    spec = resolver({"vault": "alpha", "type": "claude"})
    assert spec.submit_after_paste is False
    assert spec.paste_when_ready is None


def test_the_readiness_pattern_matches_a_real_claude_repl_pane():
    """Captured from Claude Code v2.1.235 in tmux. A bare shell prompt and a
    still-loading REPL must not match — that is what stops the Enter landing
    in bash."""
    ready = ("╰────────────────────────────────╯\n"
             "──────────────────────────────────────────────────\n"
             "❯ \n"
             "──────────────────────────────────────────────────\n"
             "  ⏵⏵ auto mode on (shift+tab to cycle)\n")
    shell = "(main)vlad@x1Carbon:~/vaults/alpha $ \n"
    loading = ("╭─── Claude Code v2.1.235 ────────────────────────╮\n"
               "│              Welcome back vlad!                 │\n")
    pattern = re.compile(wi.CLAUDE_REPL_READY)
    assert pattern.search(ready)
    assert not pattern.search(shell)
    assert not pattern.search(loading)


def test_an_over_long_slash_command_is_refused(resolver):
    with pytest.raises(SpawnError):
        resolver({"vault": "alpha", "type": "claude",
                  "initial_command": "/x" * 200})


def test_a_slash_command_needs_a_claude_session(resolver):
    with pytest.raises(SpawnError):
        resolver({"vault": "alpha", "type": "shell",
                  "initial_command": "/claude-obsidian:wiki"})


# ----- attend -----
def test_attending_an_unknown_task_is_refused(resolver):
    with pytest.raises(SpawnError):
        resolver({"attend_task": "no-such-task"})


def test_attend_takes_its_prompt_from_the_task_not_the_request(app_ctx):
    """The client names a task; resman rebuilds the prompt. Nothing in the
    request body reaches the REPL."""
    app, ctx = app_ctx
    tm = ctx["task_manager"]
    task = tm.create_task(name="lint-alpha", vault="alpha",
                          operation="wiki-lint", params={}, run_now=False)
    with app.test_request_context():
        resolver = wi.make_spawn_resolver(ctx)
        spec = resolver({"attend_task": task.id,
                         "initial_command": "/evil"}).validate()
    assert spec.metadata["vault"] == "alpha"
    assert spec.submit_after_paste is True
    assert "/evil" not in (spec.initial_paste or "")


# ----- tmux naming -----
@pytest.mark.parametrize("vault, kind, expected", [
    ("alpha", "shell", "alpha-shell"),
    ("my.vault", "claude", "my-vault-claude"),
    ("a/b", "shell", "a-b-shell"),
    ("...", "shell", "vault-shell"),
])
def test_names_become_valid_tmux_names(vault, kind, expected):
    assert wi.safe_tmux_name(vault, kind) == expected


# ----- network policy -----
def test_the_default_terminal_trust_is_loopback_plus_the_tailnet(monkeypatch):
    monkeypatch.delenv("RESMAN_WEBTERM_TRUSTED_NETS", raising=False)
    import webterm
    assert wi.trusted_networks() == (webterm.TAILSCALE_CGNAT,)


def test_trusted_networks_can_be_overridden_per_host(monkeypatch):
    monkeypatch.setenv("RESMAN_WEBTERM_TRUSTED_NETS", "10.4.0.0/16, 10.5.0.0/16")
    assert wi.trusted_networks() == ("10.4.0.0/16", "10.5.0.0/16")


def test_an_empty_override_means_loopback_only(monkeypatch):
    monkeypatch.setenv("RESMAN_WEBTERM_TRUSTED_NETS", "")
    assert wi.trusted_networks() == ()


def test_no_hardcoded_address_literals_in_the_integration():
    """CLAUDE.md: apps reference library constants, never address literals."""
    import inspect
    source = inspect.getsource(wi)
    code = "\n".join(line for line in source.splitlines()
                     if not line.lstrip().startswith("#"))
    assert not re.search(r"\b\d{1,3}(\.\d{1,3}){3}/\d{1,2}", code)


# ----- orphan safety: resman's reaper actually kills -----
def test_no_session_is_reported_as_an_orphan(app_ctx, monkeypatch):
    app, ctx = app_ctx
    sm = ctx["session_manager"]
    sm._available = True
    monkeypatch.setattr(sm, "orphaned_tmux_sessions", lambda: ["rst-a", "rst-b"])
    assert app.test_client().get("/api/sessions").get_json()["orphaned"] == []


def test_killing_orphans_is_refused_rather_than_killing_live_tabs(app_ctx,
                                                                  monkeypatch):
    app, ctx = app_ctx
    sm = ctx["session_manager"]
    sm._available = True
    killed = []
    monkeypatch.setattr(sm, "kill_orphaned_tmux_sessions",
                        lambda: killed.append("ran") or {"killed": [], "failed": []})
    resp = app.test_client().post("/api/sessions/orphans/kill",
                                  headers={"X-Requested-With": "resman"})
    assert resp.status_code == 409
    assert killed == [], "the legacy reaper must not run under webterm"


def test_the_legacy_stack_still_reports_and_reaps_orphans(tmp_path, monkeypatch):
    app, _sio, ctx = make_app(tmp_path, use_webterm=False)
    sm = ctx["session_manager"]
    sm._available = True
    monkeypatch.setattr(sm, "orphaned_tmux_sessions", lambda: ["rst-old"])
    monkeypatch.setattr(sm, "kill_orphaned_tmux_sessions",
                        lambda: {"killed": ["rst-old"], "failed": []})
    client = app.test_client()
    assert client.get("/api/sessions").get_json()["orphaned"] == ["rst-old"]
    resp = client.post("/api/sessions/orphans/kill",
                       headers={"X-Requested-With": "resman"})
    assert resp.status_code == 200
    assert resp.get_json()["killed"] == ["rst-old"]


# ----- wiring -----
def test_webterm_is_mounted_and_serves_its_config(app_ctx):
    app, ctx = app_ctx
    assert ctx["webterm"] is not None
    data = app.test_client().get("/webterm/api/config").get_json()["data"]
    assert data["font_size"] == wi.TERMINAL_FONT_SIZE
    assert "dark" in data["themes"]


def test_the_terminal_shares_the_apps_tmux_socket_and_prefix(app_ctx):
    _app, ctx = app_ctx
    config = ctx["webterm"].config
    assert config.tmux_socket == "resman-test"
    assert config.tmux_prefix == "rst-"


def test_the_page_mounts_the_terminal(app_ctx):
    app, _ctx = app_ctx
    page = app.test_client().get("/").get_data(as_text=True)
    assert "webterm-root" in page
    assert "/webterm/static/webterm.css" in page


def test_the_legacy_stack_is_still_reachable_behind_the_flag(tmp_path):
    app, _sio, ctx = make_app(tmp_path, use_webterm=False)
    assert ctx["webterm"] is None
    client = app.test_client()
    assert client.get("/webterm/api/config").status_code == 404
    page = client.get("/").get_data(as_text=True)
    assert "webterm-root" not in page
