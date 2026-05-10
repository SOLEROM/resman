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
