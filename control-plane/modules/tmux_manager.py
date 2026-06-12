"""TmuxManager — tmux session lifecycle on an isolated socket.

resman uses an isolated tmux socket (default name: `resman`) so it never
shares with the user's personal tmux. All commands are argument-list form;
the OS shell is never invoked.
"""
from __future__ import annotations

import logging
import shlex
import subprocess
from typing import Iterable, List, Optional, Sequence

log = logging.getLogger(__name__)


class TmuxNotInstalledError(RuntimeError):
    pass


class TmuxSessionError(RuntimeError):
    pass


class TmuxManager:
    def __init__(self, socket: str = "resman", prefix: str = "rsm-") -> None:
        self.socket = socket
        self.prefix = prefix

    @staticmethod
    def is_installed() -> bool:
        try:
            subprocess.run(
                ["tmux", "-V"], capture_output=True, check=True, timeout=5
            )
            return True
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            return False

    def _base_cmd(self) -> List[str]:
        return ["tmux", "-L", self.socket]

    def list_sessions(self) -> List[str]:
        try:
            out = subprocess.run(
                self._base_cmd() + ["list-sessions", "-F", "#{session_name}"],
                capture_output=True, text=True, timeout=5,
            )
        except FileNotFoundError as exc:
            raise TmuxNotInstalledError("tmux not installed") from exc
        if out.returncode != 0:
            # `no server running` is normal — return empty list
            stderr = (out.stderr or "").lower()
            if "no server" in stderr or "no current session" in stderr:
                return []
            return []
        return [line for line in out.stdout.strip().splitlines() if line]

    def session_exists(self, name: str) -> bool:
        try:
            out = subprocess.run(
                self._base_cmd() + ["has-session", "-t", name],
                capture_output=True, timeout=5,
            )
            return out.returncode == 0
        except FileNotFoundError as exc:
            raise TmuxNotInstalledError("tmux not installed") from exc

    def session_exists_pattern(self, prefix: str) -> bool:
        return any(s.startswith(prefix) for s in self.list_sessions())

    def create_session(
        self,
        name: str,
        cwd: str,
        initial_command: Optional[Sequence[str]] = None,
    ) -> None:
        cmd = self._base_cmd() + ["new-session", "-d", "-s", name, "-c", cwd]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        except FileNotFoundError as exc:
            raise TmuxNotInstalledError("tmux not installed") from exc
        if res.returncode != 0:
            stderr = (res.stderr or "").strip()
            if "duplicate session" in stderr.lower():
                # Already exists — that's fine.
                pass
            else:
                raise TmuxSessionError(f"tmux create-session failed: {stderr}")
        self._apply_session_options(name)
        if initial_command:
            self.send_keys(name, list(initial_command))

    def _apply_session_options(self, name: str) -> None:
        """Per-session polish so the embedded ttyd terminal feels like a
        plain xterm instead of a dev's tmux:

        - status off: hides the green status bar at the bottom.
        - mouse on: tmux intercepts wheel events into copy-mode scrolling so
          full-screen TUIs (e.g. Claude Code) can't hijack the wheel into
          arrow keys.
        - history-limit: large scrollback so wheel scrolling actually has
          buffer to walk through.
        - allow-rename / set-titles off: the inner shell can't rewrite the
          session name we display in our tab.
        """
        opts = [
            ("status", "off"),
            ("mouse", "on"),
            ("history-limit", "50000"),
            ("default-terminal", "xterm-256color"),
            ("allow-rename", "off"),
            ("set-titles", "off"),
        ]
        for key, value in opts:
            try:
                subprocess.run(
                    self._base_cmd() + ["set-option", "-t", name, key, value],
                    capture_output=True, timeout=3,
                )
            except FileNotFoundError:
                # tmux disappeared mid-call; the next command will fail with a
                # clearer error.
                return
        try:
            subprocess.run(
                self._base_cmd()
                + ["set-window-option", "-t", name, "aggressive-resize", "on"],
                capture_output=True, timeout=3,
            )
        except FileNotFoundError:
            return

    def send_keys(self, name: str, parts: Iterable[str]) -> None:
        # Construct a shell-safe quoted command for tmux send-keys.
        line = " ".join(shlex.quote(p) for p in parts)
        cmd = self._base_cmd() + ["send-keys", "-t", name, line, "Enter"]
        try:
            subprocess.run(cmd, capture_output=True, timeout=5)
        except FileNotFoundError as exc:
            raise TmuxNotInstalledError("tmux not installed") from exc

    def send_text(self, name: str, text: str) -> None:
        """Deliver multi-line `text` to the pane as a single bracketed paste.

        Unlike send_keys (which submits on every newline), this route loads
        the block into a named buffer and pastes it with `-p` (bracketed
        paste mode), so an interactive REPL such as Claude treats the whole
        chunk as one message. A trailing send-keys Enter then submits.
        """
        if not text:
            return
        buffer = f"resman-init-{name}"
        base = self._base_cmd()
        try:
            subprocess.run(
                base + ["load-buffer", "-b", buffer, "-"],
                input=text.encode("utf-8"), capture_output=True, timeout=5,
            )
            subprocess.run(
                base + ["paste-buffer", "-p", "-d", "-t", name, "-b", buffer],
                capture_output=True, timeout=5,
            )
            subprocess.run(
                base + ["send-keys", "-t", name, "Enter"],
                capture_output=True, timeout=5,
            )
        except FileNotFoundError as exc:
            raise TmuxNotInstalledError("tmux not installed") from exc

    def list_panes(self, name: str) -> List[int]:
        """Return the foreground PIDs of every pane in a tmux session.

        Used by the sessions-overview modal to walk what's actually running
        inside each tmux session (typically a shell whose child is Claude).
        Returns an empty list if the session is gone, tmux isn't installed,
        or the command otherwise fails.
        """
        try:
            res = subprocess.run(
                self._base_cmd() + [
                    "list-panes", "-t", name, "-F", "#{pane_pid}",
                ],
                capture_output=True, text=True, timeout=5,
            )
        except FileNotFoundError:
            return []
        if res.returncode != 0:
            return []
        pids: List[int] = []
        for line in res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                pids.append(int(line))
            except ValueError:
                continue
        return pids

    def kill_session(self, name: str) -> None:
        cmd = self._base_cmd() + ["kill-session", "-t", name]
        try:
            subprocess.run(cmd, capture_output=True, timeout=5)
        except FileNotFoundError as exc:
            raise TmuxNotInstalledError("tmux not installed") from exc

    def kill_server(self) -> None:
        try:
            subprocess.run(
                self._base_cmd() + ["kill-server"], capture_output=True, timeout=5
            )
        except FileNotFoundError:
            pass

    def reconcile(self) -> List[str]:
        """Return tmux sessions on our socket that match our prefix.

        Used at startup to detect orphaned sessions from a previous run.
        """
        sessions = self.list_sessions()
        return [s for s in sessions if s.startswith(self.prefix)]
