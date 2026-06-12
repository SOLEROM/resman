"""ObsidianPush — writes _resman/status.md into each vault every 60s.

Obsidian's chokidar watcher detects new files within seconds; the file
appears in the graph view as a normal node. This gives the user ambient
health feedback inside Obsidian without any plugin.

Health priority (highest wins):
- red:    last task failed
- yellow: a task is currently running
- green:  active tmux session exists
- gray:   idle

Failures are non-fatal: OSError is caught, logged, and skipped.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

log = logging.getLogger(__name__)


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def compute_health(vault_name: str, task_states: Iterable[str], has_session: bool) -> str:
    """Reduce a vault's task states + session presence to a single dot color."""
    states = list(task_states)
    if any(s == "failed" for s in states):
        return "red"
    if any(s == "running" for s in states):
        return "yellow"
    if has_session:
        return "green"
    return "gray"


def render_status_md(vault_name: str, color: str, has_session: bool) -> str:
    detail = "Terminal session active" if has_session else "Idle"
    return (
        f"# {vault_name} — {color}\n\n"
        f"Updated: {_utcnow_iso()}\n"
        f"Health: {color}\n"
        f"{detail}\n\n"
        f"[[_resman/status]]\n"
    )


class ObsidianPush:
    def __init__(
        self,
        vault_iter: Callable[[], list],
        get_task_states: Callable[[str], list],
        has_session_for: Callable[[str], bool],
    ) -> None:
        self.vault_iter = vault_iter
        self.get_task_states = get_task_states
        self.has_session_for = has_session_for

    def push_vault_status(self, vault_name: str, vault_path: str) -> bool:
        if not vault_path or not Path(vault_path).is_dir():
            return False
        states = self.get_task_states(vault_name)
        has_sess = self.has_session_for(vault_name)
        color = compute_health(vault_name, states, has_sess)
        body = render_status_md(vault_name, color, has_sess)
        rdir = Path(vault_path) / "_resman"
        try:
            rdir.mkdir(exist_ok=True)
            (rdir / "status.md").write_text(body, encoding="utf-8")
            return True
        except OSError as exc:
            log.warning("ObsidianPush failed for %s: %s", vault_name, exc)
            return False

    def push_all_vaults(self) -> dict:
        ok = 0
        failed = 0
        for v in self.vault_iter():
            if v.path_exists is False:
                continue
            if self.push_vault_status(v.name, v.path):
                ok += 1
            else:
                failed += 1
        return {"ok": ok, "failed": failed}
