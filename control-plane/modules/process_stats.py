"""Read-only Linux /proc helpers for the sessions-overview modal.

Used by SessionManager.stats() to expose how much memory each tracked ttyd
+ tmux session is consuming, so the user can spot run-away Claude or shell
processes without leaving the browser. Everything here is best-effort: a
PID that disappears mid-read returns None / 0 rather than raising.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional, Set


_PROC = Path("/proc")


def read_proc(pid: int, proc_root: Path = _PROC) -> Optional[dict]:
    """Return {pid, comm, ppid, rss_kb} for a live PID, or None.

    Reads ``/proc/<pid>/status``. Returns None if the file is gone (the
    process exited between when we listed it and when we tried to read it)
    or unreadable.
    """
    if pid <= 0:
        return None
    try:
        text = (proc_root / str(pid) / "status").read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return None
    comm = ""
    ppid = 0
    rss_kb = 0
    for line in text.splitlines():
        if line.startswith("Name:"):
            comm = line.split(":", 1)[1].strip()
        elif line.startswith("PPid:"):
            try:
                ppid = int(line.split(":", 1)[1].strip())
            except ValueError:
                ppid = 0
        elif line.startswith("VmRSS:"):
            parts = line.split()
            if len(parts) >= 2:
                try:
                    rss_kb = int(parts[1])
                except ValueError:
                    rss_kb = 0
    return {"pid": pid, "comm": comm, "ppid": ppid, "rss_kb": rss_kb}


def build_ppid_index(proc_root: Path = _PROC) -> Dict[int, List[int]]:
    """Walk /proc once and return a parent_pid -> [child_pid, ...] map.

    One read per live process; cheap on a normal-sized system. Used to
    enumerate the full descendant tree of each tmux pane PID without
    relying on /proc/<pid>/task/.../children (which is only enabled when
    CONFIG_PROC_CHILDREN is set).
    """
    index: Dict[int, List[int]] = {}
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return index
    for entry in entries:
        name = entry.name
        if not name.isdigit():
            continue
        try:
            pid = int(name)
        except ValueError:
            continue
        info = read_proc(pid, proc_root=proc_root)
        if info is None:
            continue
        index.setdefault(info["ppid"], []).append(pid)
    return index


def descendants(root_pid: int, ppid_index: Dict[int, List[int]]) -> List[int]:
    """Return every transitive descendant of root_pid via a BFS walk.

    Does not include root_pid itself. Cycles are guarded against (shouldn't
    happen with real PIDs but cheap insurance).
    """
    seen: Set[int] = set()
    out: List[int] = []
    frontier: List[int] = list(ppid_index.get(root_pid, ()))
    while frontier:
        pid = frontier.pop()
        if pid in seen or pid == root_pid:
            continue
        seen.add(pid)
        out.append(pid)
        frontier.extend(ppid_index.get(pid, ()))
    return out


def process_tree(root_pid: int, proc_root: Path = _PROC) -> List[dict]:
    """Return [{pid, comm, ppid, rss_kb}, ...] for root_pid + every descendant.

    Order: root first, then a stable BFS expansion. Processes that vanish
    mid-walk are silently dropped — the modal just shows whatever is live
    at the moment it's opened.
    """
    if root_pid <= 0:
        return []
    root = read_proc(root_pid, proc_root=proc_root)
    if root is None:
        return []
    index = build_ppid_index(proc_root=proc_root)
    out: List[dict] = [root]
    for pid in descendants(root_pid, index):
        info = read_proc(pid, proc_root=proc_root)
        if info is not None:
            out.append(info)
    return out
