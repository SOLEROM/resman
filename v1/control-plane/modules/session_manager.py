"""SessionManager — ttyd-backed terminal sessions.

Each session is a ttyd process bound to a specific tmux session on a specific
port. The browser embeds it as <iframe src="http://127.0.0.1:{port}">.

resman only:
- spawns the ttyd process pointing at the correct tmux session,
- tracks the port,
- and cleans up on disconnect.

ttyd handles PTY management, xterm.js protocol, resize, WebSocket streaming.
"""
from __future__ import annotations

import logging
import socket
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Dict, List, Optional

from .tmux_manager import TmuxManager, TmuxSessionError

log = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Session:
    id: str
    vault: str
    session_type: str  # "claude" | "shell"
    tmux_session: str
    port: int
    proc: Optional[subprocess.Popen] = None
    created_at: datetime = field(default_factory=_utcnow)

    bind_host: str = "127.0.0.1"

    def to_dict(self) -> dict:
        # url uses 127.0.0.1 when locked to loopback; LAN clients build their
        # own from `port` + window.location.hostname (see app.js renderSessions).
        url_host = "127.0.0.1" if self.bind_host in ("127.0.0.1", "localhost") else self.bind_host
        return {
            "id": self.id,
            "vault": self.vault,
            "session_type": self.session_type,
            "tmux_session": self.tmux_session,
            "port": self.port,
            "url": f"http://{url_host}:{self.port}",
            "bind_host": self.bind_host,
            "created_at": self.created_at.isoformat().replace("+00:00", "Z"),
            "alive": self.is_alive(),
        }

    def is_alive(self) -> bool:
        if self.proc is None:
            return False
        return self.proc.poll() is None


class TtydNotInstalledError(RuntimeError):
    pass


class NoFreePortError(RuntimeError):
    pass


def _try_bind(port: int) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False
        finally:
            s.close()
    except OSError:
        return False


def _wait_for_listen(port: int, timeout: float = 5.0, interval: float = 0.05) -> bool:
    """Block until something is accepting on 127.0.0.1:port (or timeout).

    Returns True once a TCP connection can be established. Used right after
    spawning ttyd so the spawn API only succeeds once the iframe URL will
    actually load — otherwise the browser races ttyd's startup and shows
    a connection-refused error.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(interval)
    return False


class SessionManager:
    def __init__(
        self,
        tmux: TmuxManager,
        port_base: int = 7680,
        port_max: int = 7999,
        ttyd_path: Optional[str] = "ttyd",
        bind_host: str = "127.0.0.1",
        emit=None,
    ) -> None:
        self.tmux = tmux
        self.port_base = port_base
        self.port_max = port_max
        self.ttyd_path = ttyd_path or "ttyd"
        self.bind_host = bind_host
        self.emit = emit  # SocketIO emitter (vault, event, payload)
        self._sessions: Dict[str, Session] = {}
        self._counters: Dict[str, int] = {}  # vault+type -> count
        self._lock = RLock()
        self._available = self._probe_ttyd()
        self._monitors_started = False

    def _probe_ttyd(self) -> bool:
        try:
            subprocess.run(
                [self.ttyd_path, "--version"], capture_output=True, timeout=3
            )
            return True
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @property
    def available(self) -> bool:
        return self._available

    def _next_session_name(self, vault: str, session_type: str) -> str:
        key = f"{vault}|{session_type}"
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + 1
            n = self._counters[key]
        return f"{self.tmux.prefix}{vault}-{session_type}-{n}"

    def _find_free_port(self) -> int:
        used_ports = {s.port for s in self._sessions.values()}
        for p in range(self.port_base, self.port_max + 1):
            if p in used_ports:
                continue
            if _try_bind(p):
                return p
        raise NoFreePortError(
            f"No free port in range {self.port_base}-{self.port_max}"
        )

    def spawn(
        self,
        vault: str,
        vault_path: str,
        session_type: str,
        claude_cmd: str = "claude --dangerously-skip-permissions",
        initial_command: Optional[str] = None,
        initial_command_delay: float = 5.0,
    ) -> Session:
        """Spawn a tmux+ttyd session.

        initial_command — when set and session_type=="claude", typed into the
        Claude prompt after `initial_command_delay` seconds. Used by the new-
        vault wizard to send `/claude-obsidian:wiki` so the user can answer
        any prompts the bootstrap command asks. The keystroke is scheduled
        with `threading.Timer`, which eventlet patches to a cooperative
        greenlet — so the API call returns immediately and the keystroke
        fires in the background once Claude is ready.
        """
        if session_type not in ("claude", "shell"):
            raise ValueError(f"invalid session_type {session_type!r}")
        if not self._available:
            raise TtydNotInstalledError("ttyd not installed")
        if initial_command and session_type != "claude":
            raise ValueError("initial_command requires session_type='claude'")
        tmux_name = self._next_session_name(vault, session_type)
        try:
            self.tmux.create_session(tmux_name, vault_path)
        except TmuxSessionError:
            raise
        if session_type == "claude":
            # Quote the entire claude_cmd string by splitting; user already wrote
            # it as a shell-style command, but for tmux send-keys we treat it as
            # one line and let tmux pass through to the underlying shell.
            self.tmux.send_keys(tmux_name, ["sh", "-c", f"cd {vault_path} && {claude_cmd}"])
        port = self._find_free_port()
        ttyd_cmd = [
            self.ttyd_path,
            "--port", str(port),
            "--interface", self.bind_host,
            "--writable",
            "--check-origin=false",
            "tmux", "-L", self.tmux.socket, "attach-session", "-t", tmux_name,
        ]
        try:
            proc = subprocess.Popen(
                ttyd_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except FileNotFoundError as exc:
            raise TtydNotInstalledError("ttyd not installed") from exc
        # Block briefly until ttyd accepts connections — without this, the
        # browser iframe races ttyd's startup and renders a connection-refused
        # error page. Eventlet's socket monkey-patch makes this cooperative,
        # so other requests are not blocked during the wait.
        if not _wait_for_listen(port, timeout=5.0):
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                pass
            raise TtydNotInstalledError(
                f"ttyd did not start listening on port {port} within 5s"
            )
        session = Session(
            id=str(uuid.uuid4()),
            vault=vault,
            session_type=session_type,
            tmux_session=tmux_name,
            port=port,
            proc=proc,
            bind_host=self.bind_host,
        )
        with self._lock:
            self._sessions[session.id] = session
        log.info("spawned session %s on port %d (%s)", session.id, port, tmux_name)
        if initial_command:
            self._schedule_initial_command(tmux_name, initial_command, initial_command_delay)
        return session

    def _schedule_initial_command(self, tmux_name: str, text: str, delay: float) -> None:
        """Type `text` into the Claude prompt after a delay.

        Claude takes a few seconds to render its REPL after launch. We type
        slightly after that so the slash command lands in the prompt — not
        in the bash session that briefly precedes Claude.
        """
        def _send():
            try:
                self.tmux.send_keys(tmux_name, [text])
            except Exception:
                log.exception("deferred send-keys failed for %s", tmux_name)

        timer = threading.Timer(max(0.0, float(delay)), _send)
        timer.daemon = True
        timer.start()

    def list(self) -> List[Session]:
        return list(self._sessions.values())

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def kill(self, session_id: str) -> bool:
        with self._lock:
            session = self._sessions.pop(session_id, None)
        if not session:
            return False
        if session.proc and session.proc.poll() is None:
            try:
                session.proc.terminate()
                try:
                    session.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    session.proc.kill()
            except Exception:
                log.exception("error killing ttyd process for %s", session_id)
        # tmux session is intentionally left alive — user may want to reattach.
        return True

    def kill_all(self) -> None:
        for sid in list(self._sessions.keys()):
            self.kill(sid)

    def poll_monitor(self) -> List[dict]:
        """Check every session's proc; emit session_crashed for unexpected exits.

        Called periodically (5s) by a server-owned greenlet/thread.
        """
        crashed: List[dict] = []
        with self._lock:
            sessions = list(self._sessions.items())
        for sid, session in sessions:
            if session.proc is None:
                continue
            rc = session.proc.poll()
            if rc is None:
                continue
            with self._lock:
                self._sessions.pop(sid, None)
            payload = {
                "session_id": sid,
                "vault": session.vault,
                "message": f"ttyd exited with code {rc}",
            }
            crashed.append(payload)
            if self.emit:
                try:
                    self.emit("session_crashed", payload)
                except Exception:
                    log.exception("emit session_crashed failed")
        return crashed

    def orphaned_tmux_sessions(self) -> List[str]:
        """Return tmux session names matching our prefix that are not tracked."""
        live = self.tmux.reconcile()
        tracked = {s.tmux_session for s in self._sessions.values()}
        return [name for name in live if name not in tracked]
