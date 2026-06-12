"""Tmux integration tests on an isolated socket. Cleaned up in teardown."""
import pytest
from modules.tmux_manager import TmuxManager


@pytest.fixture
def tmux():
    if not TmuxManager.is_installed():
        pytest.skip("tmux not installed")
    t = TmuxManager(socket="resman-test", prefix="rsm-test-")
    yield t
    try:
        t.kill_server()
    except Exception:
        pass


def test_create_and_list(tmp_path, tmux):
    tmux.create_session("rsm-test-alpha", str(tmp_path))
    sessions = tmux.list_sessions()
    assert "rsm-test-alpha" in sessions


def test_session_exists(tmp_path, tmux):
    tmux.create_session("rsm-test-beta", str(tmp_path))
    assert tmux.session_exists("rsm-test-beta") is True
    assert tmux.session_exists("rsm-test-nope") is False


def test_session_exists_pattern(tmp_path, tmux):
    tmux.create_session("rsm-test-pattern-1", str(tmp_path))
    assert tmux.session_exists_pattern("rsm-test-pattern") is True


def test_kill_session(tmp_path, tmux):
    tmux.create_session("rsm-test-kill", str(tmp_path))
    tmux.kill_session("rsm-test-kill")
    assert tmux.session_exists("rsm-test-kill") is False


def test_reconcile_returns_prefixed(tmp_path, tmux):
    tmux.create_session("rsm-test-reconcile", str(tmp_path))
    out = tmux.reconcile()
    assert "rsm-test-reconcile" in out


def test_send_text_uses_load_and_paste_buffer(monkeypatch):
    """send_text must deliver text via load-buffer + paste-buffer -p (bracketed
    paste) + Enter, so multi-line input lands as ONE Claude message instead of
    being submitted line-by-line."""
    from modules import tmux_manager as tm_mod
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append({"cmd": list(cmd), "input": kwargs.get("input")})
        class R:
            returncode = 0
            stdout = b""
            stderr = b""
        return R()

    monkeypatch.setattr(tm_mod.subprocess, "run", fake_run)
    tm_mod.TmuxManager(socket="resman-test").send_text("rsm-test-pane", "line1\nline2")

    verbs = [c["cmd"][3] for c in calls]
    assert verbs == ["load-buffer", "paste-buffer", "send-keys"]
    # bracketed paste flag must be set so Claude treats the block as one message
    assert "-p" in calls[1]["cmd"]
    # the buffer payload arrives via stdin, not as a quoted CLI arg
    assert calls[0]["input"] == b"line1\nline2"
    # final stroke submits with Enter (not a literal newline keypress)
    assert calls[2]["cmd"][-1] == "Enter"


def test_send_text_skips_empty(monkeypatch):
    """Empty text → no subprocess calls at all (no buffer churn)."""
    from modules import tmux_manager as tm_mod
    calls = []
    monkeypatch.setattr(tm_mod.subprocess, "run",
                        lambda *a, **kw: calls.append(a) or None)
    tm_mod.TmuxManager().send_text("rsm-test-pane", "")
    assert calls == []


def test_create_session_applies_polish_options(tmp_path, tmux):
    """status off + mouse on are how we kill the green status bar and let
    tmux intercept the wheel before Claude can hijack it into arrow keys."""
    import subprocess
    tmux.create_session("rsm-test-opts", str(tmp_path))
    base = ["tmux", "-L", tmux.socket]
    def show(opt):
        out = subprocess.run(
            base + ["show-options", "-t", "rsm-test-opts", opt],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip()
    assert "off" in show("status")
    assert "on" in show("mouse")
    history = show("history-limit")
    # parse "history-limit 50000"
    n = int(history.split()[-1]) if history else 0
    assert n >= 10000


def test_list_panes_returns_pane_pids(tmp_path, tmux):
    """list_panes must return the foreground PID of every pane in the
    session — the sessions-overview modal uses these as roots for the
    process-tree walk."""
    tmux.create_session("rsm-test-panes", str(tmp_path))
    pids = tmux.list_panes("rsm-test-panes")
    assert len(pids) >= 1
    # PIDs must be live, positive ints
    for pid in pids:
        assert isinstance(pid, int) and pid > 0


def test_list_panes_missing_session_returns_empty(tmux):
    """A tmux session that doesn't exist → empty list, no exception. The
    overview modal must keep rendering even if a session vanished between
    snapshot and walk."""
    assert tmux.list_panes("rsm-test-doesnotexist") == []
