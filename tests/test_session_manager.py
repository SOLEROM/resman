"""Tests for SessionManager logic that does not require a running ttyd."""
import socket
import threading
import time
from datetime import datetime, timedelta, timezone

from modules import process_stats
from modules.session_manager import Session, SessionManager, _try_bind, _wait_for_listen
from modules.tmux_manager import TmuxManager


def test_try_bind_finds_open_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    # That port is now in use
    assert _try_bind(port) is False
    s.close()


def test_session_manager_marks_unavailable_when_ttyd_missing():
    sm = SessionManager(
        tmux=TmuxManager(), port_base=7680, port_max=7700,
        ttyd_path="this-binary-does-not-exist",
    )
    assert sm.available is False


def test_initial_command_rejected_for_shell():
    """initial_command only makes sense for Claude — sending slash commands
    into a bash prompt is meaningless and likely a bug."""
    import pytest
    sm = SessionManager(
        tmux=TmuxManager(socket="resman-test-no-such"),
        port_base=7680, port_max=7700,
        ttyd_path="this-binary-does-not-exist",
    )
    # ttyd not available will fail first, but we expect ValueError before that
    # check if we explicitly use shell+initial_command.
    sm._available = True  # bypass for unit test
    with pytest.raises(ValueError, match="initial_command requires"):
        sm.spawn(
            vault="alpha", vault_path="/tmp", session_type="shell",
            initial_command="/claude-obsidian:wiki",
        )


def test_initial_text_and_initial_command_mutually_exclusive():
    import pytest
    sm = SessionManager(
        tmux=TmuxManager(socket="resman-test-no-such"),
        port_base=7680, port_max=7700,
        ttyd_path="this-binary-does-not-exist",
    )
    sm._available = True
    with pytest.raises(ValueError, match="mutually exclusive"):
        sm.spawn(
            vault="alpha", vault_path="/tmp", session_type="claude",
            initial_command="/claude-obsidian:wiki",
            initial_text="some long bootstrap instruction block",
        )


def test_initial_text_rejected_for_shell():
    import pytest
    sm = SessionManager(
        tmux=TmuxManager(socket="resman-test-no-such"),
        port_base=7680, port_max=7700,
        ttyd_path="this-binary-does-not-exist",
    )
    sm._available = True
    with pytest.raises(ValueError, match="initial_text requires"):
        sm.spawn(
            vault="alpha", vault_path="/tmp", session_type="shell",
            initial_text="bootstrap block",
        )


def test_orphaned_returns_empty_when_no_sessions():
    sm = SessionManager(
        tmux=TmuxManager(socket="resman-test-no-such"),
        port_base=7680, port_max=7700,
        ttyd_path="this-binary-does-not-exist",
    )
    # Will return empty list if tmux server isn't running on that socket
    out = sm.orphaned_tmux_sessions()
    assert isinstance(out, list)


def test_wait_for_listen_returns_false_when_nothing_listens():
    # Pick a port nothing is listening on. _try_bind succeeding tells us the
    # port is free, which implies _wait_for_listen will time out fast.
    free_port = None
    for p in range(46000, 46050):
        if _try_bind(p):
            free_port = p
            break
    assert free_port is not None
    start = time.monotonic()
    assert _wait_for_listen(free_port, timeout=0.3) is False
    assert time.monotonic() - start < 1.0


def test_wait_for_listen_returns_true_when_socket_starts_late():
    """Server is started in a background thread after a 200ms delay.
    _wait_for_listen should poll and return True once the listen succeeds —
    this is exactly the race we hit between spawn() returning and the
    iframe loading the ttyd URL."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    def delayed_listen():
        time.sleep(0.2)
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("127.0.0.1", port))
        srv.listen(1)
        try:
            conn, _ = srv.accept()
            conn.close()
        except OSError:
            pass
        srv.close()

    t = threading.Thread(target=delayed_listen, daemon=True)
    t.start()
    try:
        assert _wait_for_listen(port, timeout=2.0, interval=0.02) is True
    finally:
        t.join(timeout=2.0)


def test_session_default_bind_host_is_loopback():
    """Without --public, ttyd should be locked to 127.0.0.1 so LAN can't reach
    the terminal sockets even if Flask is unbound for some other reason."""
    sm = SessionManager(
        tmux=TmuxManager(socket="resman-test-no-such"),
        port_base=7680, port_max=7700,
        ttyd_path="this-binary-does-not-exist",
    )
    assert sm.bind_host == "127.0.0.1"


def test_session_to_dict_url_uses_bind_host():
    """When bound to 0.0.0.0 the url field exposes that — clients pick the
    real hostname themselves, so the field is informational; we just check the
    field reflects the bind correctly so debugging the API output is not
    misleading."""
    s_local = Session(
        id="x", vault="v", session_type="claude",
        tmux_session="t", port=7680, bind_host="127.0.0.1",
    )
    assert s_local.to_dict()["url"] == "http://127.0.0.1:7680"
    assert s_local.to_dict()["bind_host"] == "127.0.0.1"

    s_pub = Session(
        id="x", vault="v", session_type="claude",
        tmux_session="t", port=7680, bind_host="0.0.0.0",
    )
    assert s_pub.to_dict()["url"] == "http://0.0.0.0:7680"
    assert s_pub.to_dict()["bind_host"] == "0.0.0.0"


# ----- stats() — sessions-overview modal payload -----
class _StubProc:
    """Minimal stand-in for subprocess.Popen so we can plant ttyd PIDs."""

    def __init__(self, pid: int):
        self.pid = pid

    def poll(self):
        return None  # "still alive"


class _StubTmux:
    """Tmux double that lets the test prescribe what list_panes returns
    per session without spinning up a real tmux server."""

    socket = "resman"

    def __init__(self, panes: dict):
        self._panes = panes
        self.killed: list = []
        self.fail_kills_for: set = set()

    def list_panes(self, name):
        return list(self._panes.get(name, []))

    def reconcile(self):
        return list(self._panes.keys())

    def kill_session(self, name):
        if name in self.fail_kills_for:
            raise RuntimeError("simulated tmux failure")
        self.killed.append(name)
        self._panes.pop(name, None)


def test_stats_rolls_up_per_session_rss(monkeypatch):
    """stats() should attribute ttyd RSS + every pane process tree to its
    owning session, and surface a roll-up total. We monkeypatch the proc
    walker so the test doesn't depend on host PIDs."""
    fake_procs = {
        100: {"pid": 100, "comm": "ttyd", "ppid": 1, "rss_kb": 5000},
        200: {"pid": 200, "comm": "bash", "ppid": 1, "rss_kb": 1500},
        300: {"pid": 300, "comm": "claude", "ppid": 200, "rss_kb": 240_000},
    }
    monkeypatch.setattr(
        process_stats, "read_proc",
        lambda pid, proc_root=None: fake_procs.get(pid),
    )
    monkeypatch.setattr(
        process_stats, "build_ppid_index",
        lambda proc_root=None: {200: [300]},
    )
    monkeypatch.setattr(
        process_stats, "descendants",
        lambda root_pid, idx: idx.get(root_pid, []),
    )

    tmux = _StubTmux({"rsm-alpha-claude-1": [200]})
    sm = SessionManager(
        tmux=tmux, port_base=7680, port_max=7700,
        ttyd_path="this-binary-does-not-exist",
    )
    sess = Session(
        id="s-1", vault="alpha", session_type="claude",
        tmux_session="rsm-alpha-claude-1", port=7681,
        proc=_StubProc(100),
        created_at=datetime.now(timezone.utc) - timedelta(seconds=120),
    )
    sm._sessions["s-1"] = sess

    out = sm.stats()
    assert out["session_count"] == 1
    assert out["sessions"][0]["ttyd"]["pid"] == 100
    assert out["sessions"][0]["ttyd"]["rss_kb"] == 5000
    # pane subtree rss = bash(1500) + claude(240000) = 241500
    pane = out["sessions"][0]["panes"][0]
    assert pane["pane_pid"] == 200
    assert pane["rss_kb"] == 241_500
    # session total = ttyd + pane subtree
    assert out["sessions"][0]["total_rss_kb"] == 5000 + 241_500
    assert out["total_rss_kb"] == 5000 + 241_500
    # age is non-negative; we created 120s ago
    assert out["sessions"][0]["age_seconds"] >= 100
    # claude process is reported with its actual ppid so the UI can indent
    pids_in_tree = {p["pid"] for p in pane["processes"]}
    assert pids_in_tree == {200, 300}


def test_kill_tears_down_tmux_session_too():
    """Closing the browser tab (the `×` button) calls DELETE /api/sessions/{id}
    → SessionManager.kill(). Previously tmux was left alive on the assumption
    the user might reattach; now we kill it so closing a tab is a full
    "I'm done with this terminal" — no orphan accumulation."""
    tmux = _StubTmux({"rsm-alpha-claude-1": [200]})
    sm = SessionManager(
        tmux=tmux, port_base=7680, port_max=7700,
        ttyd_path="this-binary-does-not-exist",
    )
    sm._sessions["s-1"] = Session(
        id="s-1", vault="alpha", session_type="claude",
        tmux_session="rsm-alpha-claude-1", port=7681,
    )
    assert sm.kill("s-1") is True
    # Registry no longer tracks it AND tmux session was killed
    assert "s-1" not in sm._sessions
    assert "rsm-alpha-claude-1" in tmux.killed


def test_kill_tolerates_tmux_failure():
    """A failure killing the tmux session must not prevent kill() from
    reporting success — the user closed the tab, the ttyd is gone, and the
    registry entry is dropped. A leftover tmux session is a degraded outcome
    but it's the same outcome we used to ship deliberately."""
    tmux = _StubTmux({"rsm-alpha-claude-1": [200]})
    tmux.fail_kills_for = {"rsm-alpha-claude-1"}
    sm = SessionManager(
        tmux=tmux, port_base=7680, port_max=7700,
        ttyd_path="this-binary-does-not-exist",
    )
    sm._sessions["s-1"] = Session(
        id="s-1", vault="alpha", session_type="claude",
        tmux_session="rsm-alpha-claude-1", port=7681,
    )
    assert sm.kill("s-1") is True
    assert "s-1" not in sm._sessions


def test_kill_orphaned_tmux_sessions_kills_only_untracked():
    """Tracked tmux sessions must NOT be killed — they belong to live ttyd
    processes that the user is still using. Only sessions matching our
    prefix but not in the registry should be killed."""
    tmux = _StubTmux({
        "rsm-alpha-claude-1": [200],   # tracked (will be in registry)
        "rsm-stale-claude-2": [201],   # orphan
        "rsm-stale-shell-3": [202],    # orphan
    })
    sm = SessionManager(
        tmux=tmux, port_base=7680, port_max=7700,
        ttyd_path="this-binary-does-not-exist",
    )
    sm._sessions["s-1"] = Session(
        id="s-1", vault="alpha", session_type="claude",
        tmux_session="rsm-alpha-claude-1", port=7681,
    )
    out = sm.kill_orphaned_tmux_sessions()
    assert sorted(out["killed"]) == ["rsm-stale-claude-2", "rsm-stale-shell-3"]
    assert out["failed"] == []
    assert "rsm-alpha-claude-1" not in tmux.killed


def test_kill_orphaned_tmux_sessions_reports_failures_per_name():
    """A failure on one kill must not abort the rest — the user needs every
    reclaim attempted, and the report must list which ones failed."""
    tmux = _StubTmux({
        "rsm-stale-a": [1],
        "rsm-stale-b": [2],
        "rsm-stale-c": [3],
    })
    tmux.fail_kills_for = {"rsm-stale-b"}
    sm = SessionManager(
        tmux=tmux, port_base=7680, port_max=7700,
        ttyd_path="this-binary-does-not-exist",
    )
    out = sm.kill_orphaned_tmux_sessions()
    assert sorted(out["killed"]) == ["rsm-stale-a", "rsm-stale-c"]
    assert [f["name"] for f in out["failed"]] == ["rsm-stale-b"]
    assert "simulated tmux failure" in out["failed"][0]["error"]


def test_stats_when_no_sessions_returns_empty_payload():
    sm = SessionManager(
        tmux=_StubTmux({}), port_base=7680, port_max=7700,
        ttyd_path="this-binary-does-not-exist",
    )
    out = sm.stats()
    assert out["session_count"] == 0
    assert out["sessions"] == []
    assert out["total_rss_kb"] == 0
    # Orphans aren't probed when ttyd is unavailable — the caller would just
    # get a useless empty list. The route handles the "ttyd missing" message.
    assert out["orphaned_tmux_sessions"] == []
