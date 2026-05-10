"""Tests for SessionManager logic that does not require a running ttyd."""
import socket
import threading
import time

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
