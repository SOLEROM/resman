"""REST API routes.

All mutating endpoints (POST, DELETE, PATCH) require the
`X-Requested-With: resman` header — checked before any state mutation.
"""
from __future__ import annotations

import logging
import subprocess
from functools import wraps
from pathlib import Path
from typing import Any

from flask import Blueprint, current_app, jsonify, request

from .config_manager import ConfigError, VAULT_NAME_RE

log = logging.getLogger(__name__)

bp = Blueprint("api", __name__)


def _csrf_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if request.headers.get("X-Requested-With") != "resman":
            return jsonify({"error": "missing X-Requested-With header"}), 403
        return view(*args, **kwargs)

    return wrapped


def _ctx() -> dict:
    return current_app.config["RESMAN"]


# ----- Filesystem browser (read-only, used by the new-vault picker) -----
@bp.get("/api/fs/list")
def fs_list():
    """List subdirectories of a path so the SPA can render a folder picker.

    Read-only; no CSRF required. Localhost-only by deployment, so the
    information disclosure surface is acceptable for the convenience of
    being able to pick any host path.

    Query params:
      path — directory to list. ~ is expanded. Defaults to $HOME.
    Returns:
      { path, parent, home, entries: [{name, path, is_obsidian}] }
    """
    from pathlib import Path as _Path
    raw = request.args.get("path") or "~"
    if raw in ("~", ""):
        target = _Path.home()
    else:
        target = _Path(raw).expanduser()
    try:
        target = target.resolve()
    except (OSError, RuntimeError):
        return jsonify({"error": f"invalid path: {raw}"}), 400
    if not target.is_dir():
        return jsonify({"error": f"not a directory: {target}"}), 400
    entries = []
    try:
        for entry in sorted(target.iterdir(), key=lambda e: e.name.lower()):
            if entry.name.startswith("."):
                continue  # hide dotfile dirs from the picker
            try:
                if entry.is_dir():
                    entries.append({
                        "name": entry.name,
                        "path": str(entry),
                        "is_obsidian": (entry / ".obsidian").is_dir(),
                    })
            except OSError:
                continue
    except PermissionError as exc:
        return jsonify({"error": str(exc)}), 403
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    parent = str(target.parent) if target.parent != target else None
    return jsonify({
        "path": str(target),
        "parent": parent,
        "home": str(_Path.home()),
        "entries": entries,
    })


# ----- Health -----
@bp.get("/api/health")
def get_health():
    ctx = _ctx()
    sm = ctx["session_manager"]
    return jsonify({
        "config": "ok",
        "tmux": "ok" if ctx["tmux"].is_installed() else "missing",
        "ttyd": "ok" if sm.available else "missing",
        "scheduler": "ok" if ctx["scheduler"]._started else "stopped",
        "tasks": ctx["task_manager"]._replay_summary(),
        "server": "ok",
    })


# ----- Vaults -----
@bp.get("/api/vaults")
def list_vaults():
    return jsonify({"vaults": _ctx()["vault_registry"].to_list()})


@bp.post("/api/vaults")
@_csrf_required
def register_vault():
    """Register an EXISTING vault directory in system.yaml.

    Does not create the directory. Use POST /api/vaults/scaffold first if
    the directory does not exist yet.
    """
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    path = (body.get("path") or "").strip()
    tags = body.get("tags") or []
    if not name or not path:
        return jsonify({"error": "name and path required"}), 400
    cm = _ctx()["config"]
    try:
        cm.add_vault(name, path, tags=tags)
    except ConfigError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True, "name": name})


@bp.post("/api/vaults/scaffold")
@_csrf_required
def scaffold_vault():
    """Create a new vault directory by invoking tools/new-vault.sh.

    Does NOT register the vault in system.yaml — the wizard calls
    POST /api/vaults afterwards. Splitting these means a partial failure
    leaves a clean, fixable state: directory created but not registered,
    or registered but not on disk.
    """
    body = request.get_json(force=True, silent=True) or {}
    name = (body.get("name") or "").strip()
    raw_path = (body.get("path") or "").strip()
    if not name or not raw_path:
        return jsonify({"error": "name and path required"}), 400
    if not VAULT_NAME_RE.match(name):
        return jsonify({"error": "name must match [a-zA-Z0-9_-]"}), 400
    target = Path(raw_path).expanduser()
    if not target.is_absolute():
        return jsonify({"error": "path must be absolute"}), 400
    if target.exists():
        return jsonify({"error": f"path already exists: {target}"}), 409
    script = _ctx()["resman_root"] / "tools" / "new-vault.sh"
    if not script.exists():
        return jsonify({"error": f"new-vault.sh not found at {script}"}), 500
    try:
        result = subprocess.run(
            [str(script), name, str(target)],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return jsonify({"error": "new-vault.sh timed out (30s)"}), 504
    except Exception as exc:
        log.exception("scaffold subprocess failed")
        return jsonify({"error": str(exc)}), 500
    if result.returncode != 0:
        return jsonify({
            "error": "new-vault.sh failed",
            "exit_code": result.returncode,
            "stderr": (result.stderr or "").strip(),
            "stdout": (result.stdout or "").strip(),
        }), 500
    return jsonify({
        "ok": True,
        "path": str(target),
        "stdout": (result.stdout or "").strip(),
    })


@bp.get("/api/vaults/<name>/health")
def vault_health(name):
    reg = _ctx()["vault_registry"]
    v = reg.get(name)
    if not v:
        return jsonify({"error": "vault not found"}), 404
    p = Path(v.path)
    tm = _ctx()["task_manager"]
    sm = _ctx()["session_manager"]
    last_session = None
    for s in sm.list():
        if s.vault == v.name:
            iso = s.created_at.isoformat().replace("+00:00", "Z")
            if not last_session or iso > last_session:
                last_session = iso
    last_completed = None
    for t in tm.list(vault=v.name, include_archived=True):
        if t.get("state") in ("completed", "failed") and t.get("updated_at"):
            ts = t["updated_at"]
            if not last_completed or ts > last_completed:
                last_completed = ts
    return jsonify({
        "name": v.name,
        "path": v.path,
        "path_exists": p.exists(),
        "obsidian_dir": (p / ".obsidian").is_dir() if p.exists() else False,
        "wiki_home_exists": (p / WIKI_HOME).exists() if p.exists() else False,
        "last_session_at": last_session,
        "last_completed_task_at": last_completed,
        "tags": list(v.tags or []),
    })


WIKI_HOME = "wiki/overview.md"


@bp.get("/api/vaults/<name>/wiki")
def vault_wiki(name):
    """Return the raw markdown of a wiki page produced by the Claude wiki plugin.

    Defaults to ``wiki/overview.md`` — the landing page resman opens when the
    Wiki tab is shown. The toolbar exposes explicit Hot / Index / Overview
    buttons for the three canonical pages produced by the plugin. Other wiki
    pages are reachable via ``?file=wiki/<page>.md``. Path traversal is
    blocked: the resolved file must live under the vault directory.
    """
    reg = _ctx()["vault_registry"]
    v = reg.get(name)
    if not v:
        return jsonify({"error": "vault not found"}), 404
    rel = (request.args.get("file") or WIKI_HOME).strip()
    if not rel:
        return jsonify({"error": "file required"}), 400
    try:
        vault_root = Path(v.path).resolve()
        target = (vault_root / rel).resolve()
    except (OSError, RuntimeError):
        return jsonify({"error": "invalid path"}), 400
    try:
        target.relative_to(vault_root)
    except ValueError:
        return jsonify({"error": "path escapes vault"}), 400
    if not target.is_file():
        return jsonify({"error": f"not found: {rel}", "file": rel}), 404
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"file": rel, "content": content})


def _build_wiki_tree(wiki_root: Path, vault_root: Path, rel: Path = Path(".")) -> list[dict]:
    """Walk <vault>/wiki/ recursively, returning sorted dirs + .md files.

    Paths in the response are relative to the vault root (so the SPA can pass
    them straight to GET /api/vaults/<name>/wiki?file=…). Hidden entries and
    symlinks are skipped.
    """
    entries: list[dict] = []
    base = wiki_root / rel
    try:
        children = sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError:
        return entries
    for child in children:
        if child.name.startswith("."):
            continue
        if child.is_symlink():
            continue
        rel_child = (rel / child.name) if str(rel) != "." else Path(child.name)
        vault_rel = (Path("wiki") / rel_child).as_posix()
        if child.is_dir():
            entries.append({
                "type": "dir",
                "name": child.name,
                "path": vault_rel,
                "children": _build_wiki_tree(wiki_root, vault_root, rel_child),
            })
        elif child.is_file() and child.suffix.lower() == ".md":
            entries.append({
                "type": "file",
                "name": child.name,
                "path": vault_rel,
            })
    return entries


@bp.get("/api/vaults/<name>/wiki/tree")
def vault_wiki_tree(name):
    """Return the tree of markdown pages under <vault>/wiki/.

    Powers the Wiki tab sidebar. Paths in the response are rooted at the
    vault (e.g. ``wiki/overview.md``) so each entry can be passed straight
    to ``GET /api/vaults/<name>/wiki?file=…``. Returns ``{"missing": true}``
    when the vault has no ``wiki/`` directory yet (new vault, pre-bootstrap).
    """
    reg = _ctx()["vault_registry"]
    v = reg.get(name)
    if not v:
        return jsonify({"error": "vault not found"}), 404
    try:
        vault_root = Path(v.path).resolve()
    except (OSError, RuntimeError):
        return jsonify({"error": "invalid vault path"}), 500
    wiki_root = vault_root / "wiki"
    if not wiki_root.is_dir():
        return jsonify({"missing": True, "tree": []})
    return jsonify({"missing": False, "tree": _build_wiki_tree(wiki_root, vault_root)})


# ----- Help (in-app docs from the repo's man/ tree) -----
def _man_root() -> Path:
    """Locate the man/ help tree.

    Defaults to the repo root sibling of v1/ (i.e. ``RESMAN_ROOT.parent/man``)
    so the docs travel with the source. Overridable via ``app.man_path`` in
    system.yaml for installs that ship man pages elsewhere.
    """
    cm = _ctx()["config"]
    override = (cm.app.get("man_path") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (_ctx()["resman_root"].parent / "man").resolve()


def _build_help_tree(root: Path, rel: Path = Path(".")) -> list[dict]:
    """Walk man/ recursively, returning a sorted tree of dirs + .md files.

    Hidden entries (dot-prefixed) are skipped. Symlinks are not followed —
    keeps the surface small and avoids loops if the user drops one in.
    """
    entries: list[dict] = []
    base = root / rel
    try:
        children = sorted(base.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except OSError:
        return entries
    for child in children:
        if child.name.startswith("."):
            continue
        if child.is_symlink():
            continue
        rel_child = (rel / child.name) if str(rel) != "." else Path(child.name)
        if child.is_dir():
            entries.append({
                "type": "dir",
                "name": child.name,
                "path": str(rel_child),
                "children": _build_help_tree(root, rel_child),
            })
        elif child.is_file() and child.suffix.lower() == ".md":
            entries.append({
                "type": "file",
                "name": child.name,
                "path": str(rel_child),
            })
    return entries


@bp.get("/api/help/tree")
def help_tree():
    """List the man/ documentation tree as nested dirs + markdown files."""
    root = _man_root()
    if not root.is_dir():
        return jsonify({"root": str(root), "tree": [], "missing": True})
    return jsonify({"root": str(root), "tree": _build_help_tree(root), "missing": False})


@bp.get("/api/help/page")
def help_page():
    """Return raw markdown for a help page under man/.

    Path traversal is blocked: the resolved file must live under man_root.
    Defaults to ``index.md`` so the SPA can render a landing page on first open.
    """
    root = _man_root()
    if not root.is_dir():
        return jsonify({"error": "man/ directory not found", "root": str(root)}), 404
    rel = (request.args.get("file") or "index.md").strip()
    if not rel:
        return jsonify({"error": "file required"}), 400
    try:
        target = (root / rel).resolve()
    except (OSError, RuntimeError):
        return jsonify({"error": "invalid path"}), 400
    try:
        target.relative_to(root)
    except ValueError:
        return jsonify({"error": "path escapes man/"}), 400
    if target.suffix.lower() != ".md":
        return jsonify({"error": "only .md files are served"}), 400
    if not target.is_file():
        return jsonify({"error": f"not found: {rel}", "file": rel}), 404
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"file": rel, "content": content})


@bp.post("/api/vaults/<name>/open")
@_csrf_required
def open_vault_in_obsidian(name):
    """Launch Obsidian for this vault using `obsidian_cmd` from system.yaml.

    The configured command is treated as a single launch string and split
    with shlex; the vault path is appended as a final argument. Subprocess
    runs detached (no wait, no shell) — Obsidian opens, resman returns.
    """
    import shlex
    reg = _ctx()["vault_registry"]
    v = reg.get(name)
    if not v:
        return jsonify({"error": "vault not found"}), 404
    if not Path(v.path).is_dir():
        return jsonify({"error": "vault path does not exist on disk"}), 400
    cm = _ctx()["config"]
    cmd_str = (cm.app.get("obsidian_cmd") or "").strip()
    if not cmd_str:
        return jsonify({"error": "obsidian_cmd not configured in system.yaml"}), 400
    parts = shlex.split(cmd_str) + [v.path]
    try:
        subprocess.Popen(
            parts, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True,
        )
    except FileNotFoundError:
        return jsonify({"error": f"obsidian binary not found: {parts[0]!r}"}), 400
    except OSError as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify({"ok": True, "cmd": parts})


# ----- Sessions -----
@bp.get("/api/sessions")
def list_sessions():
    sm = _ctx()["session_manager"]
    return jsonify({
        "sessions": [s.to_dict() for s in sm.list()],
        "available": sm.available,
        "orphaned": sm.orphaned_tmux_sessions() if sm.available else [],
    })


@bp.post("/api/sessions")
@_csrf_required
def spawn_session():
    sm = _ctx()["session_manager"]
    if not sm.available:
        return jsonify({"error": "ttyd not installed"}), 503
    body = request.get_json(force=True, silent=True) or {}
    vault_name = body.get("vault")
    session_type = body.get("type") or body.get("session_type")
    if session_type == "bash":
        session_type = "shell"
    reg = _ctx()["vault_registry"]
    v = reg.get(vault_name) if vault_name else None
    if not v:
        return jsonify({"error": "unknown vault"}), 400
    if session_type not in ("claude", "shell"):
        return jsonify({"error": "type must be 'claude' or 'shell'"}), 400
    # Optional: type a slash command into the Claude prompt once it's ready.
    # Used by the new-vault wizard to send /claude-obsidian:wiki so the user
    # answers prompts inside the terminal tab instead of running the command
    # blindly via `claude -p`.
    initial_command = body.get("initial_command")
    if initial_command is not None:
        if not isinstance(initial_command, str) or len(initial_command) > 200:
            return jsonify({"error": "initial_command must be a string ≤200 chars"}), 400
        if session_type != "claude":
            return jsonify({"error": "initial_command requires type='claude'"}), 400
    try:
        s = sm.spawn(
            vault=v.name, vault_path=v.path, session_type=session_type,
            claude_cmd=_ctx()["config"].app.get("claude_cmd", "claude"),
            initial_command=initial_command,
        )
    except Exception as exc:
        log.exception("spawn session failed")
        return jsonify({"error": str(exc)}), 500
    return jsonify(s.to_dict()), 201


@bp.delete("/api/sessions/<sid>")
@_csrf_required
def delete_session(sid):
    sm = _ctx()["session_manager"]
    if not sm.available:
        return jsonify({"error": "ttyd not installed"}), 503
    ok = sm.kill(sid)
    return jsonify({"ok": ok})


# ----- Tasks -----
@bp.get("/api/tasks")
def list_tasks():
    tm = _ctx()["task_manager"]
    args = request.args
    items = tm.list(
        vault=args.get("vault"),
        priority=args.get("priority"),
        state=args.get("state"),
        include_archived=args.get("include_archived") == "true",
        limit=int(args["limit"]) if args.get("limit") else None,
        offset=int(args.get("offset", 0)),
    )
    return jsonify({"tasks": items})


@bp.post("/api/tasks")
@_csrf_required
def create_task():
    body = request.get_json(force=True, silent=True) or {}
    tm = _ctx()["task_manager"]
    scheduled_for = body.get("scheduled_for")
    if isinstance(scheduled_for, str) and not scheduled_for.strip():
        scheduled_for = None
    try:
        t = tm.create_task(
            name=body.get("name") or "task",
            vault=body.get("vault"),
            operation=body.get("operation"),
            params=body.get("params") or {},
            priority=body.get("priority", "medium"),
            schedule=body.get("schedule", "background"),
            run_now=True,
            scheduled_for=scheduled_for,
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(t.to_dict()), 201


@bp.get("/api/tasks/<tid>")
def get_task(tid):
    tm = _ctx()["task_manager"]
    t = tm.get(tid)
    if not t:
        return jsonify({"error": "task not found"}), 404
    return jsonify(t.to_dict())


@bp.get("/api/tasks/<tid>/log")
def get_task_log(tid):
    tm = _ctx()["task_manager"]
    return current_app.response_class(tm.read_log(tid), mimetype="text/plain")


@bp.delete("/api/tasks/<tid>")
@_csrf_required
def cancel_task(tid):
    tm = _ctx()["task_manager"]
    ok = tm.cancel(tid)
    return jsonify({"ok": ok})


@bp.post("/api/tasks/<tid>/promote")
@_csrf_required
def promote_task(tid):
    tm = _ctx()["task_manager"]
    t = tm.promote(tid)
    if not t:
        return jsonify({"error": "task is not deferred or scheduled"}), 400
    return jsonify(t.to_dict())


@bp.post("/api/tasks/<tid>/archive")
@_csrf_required
def archive_task(tid):
    tm = _ctx()["task_manager"]
    ok = tm.archive(tid)
    return jsonify({"ok": ok})


@bp.post("/api/tasks/compact")
@_csrf_required
def compact_tasks():
    tm = _ctx()["task_manager"]
    return jsonify(tm.compact())


# ----- Window -----
@bp.get("/api/window")
def get_window():
    ws = _ctx()["window"]
    return jsonify(ws.to_dict())


@bp.post("/api/window")
@_csrf_required
def set_window():
    body = request.get_json(force=True, silent=True) or {}
    action = body.get("action")
    ws = _ctx()["window"]
    try:
        if action == "start":
            return jsonify(ws.start_window(body.get("duration_hours")))
        if action == "end":
            return jsonify(ws.end_window())
        if action == "start_weekly":
            return jsonify(ws.start_weekly(body.get("period_hours", 24 * 7)))
        if action == "end_weekly":
            return jsonify(ws.end_weekly())
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"error": f"unknown action {action!r}"}), 400


# ----- Cron -----
@bp.get("/api/cron")
def list_cron():
    return jsonify({"cron_tasks": _ctx()["scheduler"].cron_status()})


# ----- Config (live YAML editor) -----
@bp.get("/api/config/yaml")
def get_yaml():
    cm = _ctx()["config"]
    fname = request.args.get("file", "system.yaml")
    if fname not in ("system.yaml", "schedule.yaml"):
        return jsonify({"error": "unknown file"}), 400
    p = cm.config_dir / fname
    if not p.exists():
        return jsonify({"file": fname, "content": ""})
    return jsonify({"file": fname, "content": p.read_text(encoding="utf-8")})


@bp.post("/api/config/yaml")
@_csrf_required
def save_yaml():
    body = request.get_json(force=True, silent=True) or {}
    fname = body.get("file")
    content = body.get("content")
    if fname not in ("system.yaml", "schedule.yaml"):
        return jsonify({"error": "unknown file"}), 400
    if not isinstance(content, str):
        return jsonify({"error": "content must be a string"}), 400
    cm = _ctx()["config"]
    try:
        if fname == "system.yaml":
            cm.save_system_yaml(content)
        else:
            cm.save_schedule_yaml(content)
    except ConfigError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"ok": True})
