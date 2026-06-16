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

from . import plugin_commands
from . import vault_hints
from . import wiki_unread
from . import window_schedule as window_schedule_mod
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


def _activity(message: str, *, level: str = "info", source: str = "app",
              detail: str | None = None) -> None:
    """Emit an activity-log entry on the bus (no-op if the bus is absent)."""
    bus = _ctx().get("bus")
    if bus:
        bus.emit("activity", {"level": level, "source": source,
                              "message": message, "detail": detail})


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
    cm = _ctx()["config"]
    # Surface the optional vault_default_root_path so the new-vault wizard can
    # pre-fill its path field and seed the Browse picker. The frontend caches
    # this on `loadVaults()`, which is the same call it already makes to
    # render the sidebar — no extra round-trip.
    default_root = (cm.app.get("vault_default_root_path") or "").strip()
    return jsonify({
        "vaults": _ctx()["vault_registry"].to_list(),
        "vault_default_root": default_root or None,
    })


@bp.get("/api/landing")
def landing():
    """Per-vault summary cards for the landing page.

    Returns one entry per *registered* vault, pairing its identity with the
    generated ``wiki/hint.json`` (label/summary/tags/updated*). The hint is
    ``null`` when the vault has no hint file yet, so the SPA falls back to
    the bare vault name. A single call powers the whole grid — no N+1 from
    the client. Hint reads are skipped for vaults whose path is missing.
    """
    reg = _ctx()["vault_registry"]
    out = []
    for v in reg.registered:
        hint = vault_hints.read_hint(v.path) if v.path_exists else None
        has_wiki = (Path(v.path) / "wiki").is_dir() if v.path_exists else False
        out.append({
            "name": v.name,
            "path": v.path,
            "tags": list(v.tags or []),
            "path_exists": v.path_exists,
            "is_obsidian": v.is_obsidian,
            "mount": v.mount,
            "has_wiki": has_wiki,
            "hint": hint,
        })
    return jsonify({"vaults": out})


@bp.post("/api/vaults")
@_csrf_required
def register_vault():
    """Register an EXISTING vault directory in resman.yaml.

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
        _activity(f"vault register failed: {name} — {exc}", level="warn", source="vault")
        return jsonify({"error": str(exc)}), 400
    _activity(f"vault registered: {name}", source="vault", detail=path)
    return jsonify({"ok": True, "name": name})


@bp.post("/api/vaults/scaffold")
@_csrf_required
def scaffold_vault():
    """Create a new vault directory by invoking tools/new-vault.sh.

    Does NOT register the vault in resman.yaml — the wizard calls
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
        _activity(f"vault scaffold failed: {name} (exit {result.returncode})",
                  level="error", source="vault",
                  detail=(result.stderr or "").strip())
        return jsonify({
            "error": "new-vault.sh failed",
            "exit_code": result.returncode,
            "stderr": (result.stderr or "").strip(),
            "stdout": (result.stdout or "").strip(),
        }), 500
    _activity(f"vault scaffolded: {name}", source="vault", detail=str(target))
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
    mm = _ctx().get("mount_manager")
    return jsonify({
        "name": v.name,
        "path": v.path,
        "path_exists": p.exists(),
        "obsidian_dir": (p / ".obsidian").is_dir() if p.exists() else False,
        "wiki_home_exists": (p / WIKI_HOME).exists() if p.exists() else False,
        "last_session_at": last_session,
        "last_completed_task_at": last_completed,
        "tags": list(v.tags or []),
        "mount_point": v.mount,
        "mount_active": mm.is_mounted(v.name) if mm else False,
    })


@bp.post("/api/vaults/<name>/mount")
@_csrf_required
def mount_vault(name):
    """Bind-mount this vault at its configured mount point.

    Returns 400 if the vault has no ``mount`` configured, 409 if it is
    already mounted, and 500 if the mount command fails (e.g. permission
    denied — see Help > Mounts for the sudoers setup).
    """
    reg = _ctx()["vault_registry"]
    v = reg.get(name)
    if not v:
        return jsonify({"error": "vault not found"}), 404
    if not v.mount:
        return jsonify({"error": "vault has no mount point configured"}), 400
    mm = _ctx().get("mount_manager")
    if not mm:
        return jsonify({"error": "mount_manager not available"}), 503
    if mm.is_mounted(name):
        return jsonify({"ok": True, "mount_point": v.mount, "note": "already mounted"}), 200
    ok = mm.mount_one(v)
    if not ok:
        return jsonify({
            "error": "mount failed — run resman as root or add a sudoers rule. "
                     "See Help > Mounts.",
            "mount_point": v.mount,
        }), 500
    return jsonify({"ok": True, "mount_point": v.mount}), 200


@bp.delete("/api/vaults/<name>/mount")
@_csrf_required
def umount_vault(name):
    """Unmount this vault's bind-mount."""
    reg = _ctx()["vault_registry"]
    v = reg.get(name)
    if not v:
        return jsonify({"error": "vault not found"}), 404
    mm = _ctx().get("mount_manager")
    if not mm:
        return jsonify({"error": "mount_manager not available"}), 503
    if not mm.is_mounted(name):
        return jsonify({"ok": True, "note": "not currently mounted"}), 200
    ok = mm.umount_one(name)
    if not ok:
        return jsonify({"error": "umount failed — check server logs"}), 500
    return jsonify({"ok": True})


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


def _build_wiki_tree(wiki_root: Path, vault_root: Path, rel: Path = Path("."),
                     unread: set[str] | None = None) -> list[dict]:
    """Walk <vault>/wiki/ recursively, returning sorted dirs + .md files.

    Paths in the response are relative to the vault root (so the SPA can pass
    them straight to GET /api/vaults/<name>/wiki?file=…). Hidden entries and
    symlinks are skipped. ``unread`` is the set of wiki-relative page paths
    (e.g. ``concepts/gguf.md``) that carry an unread marker — each file node
    gets an ``unread`` flag the sidebar uses to render its indicator.
    """
    unread = unread or set()
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
                "children": _build_wiki_tree(wiki_root, vault_root, rel_child, unread),
            })
        elif child.is_file() and child.suffix.lower() == ".md":
            entries.append({
                "type": "file",
                "name": child.name,
                "path": vault_rel,
                "unread": rel_child.as_posix() in unread,
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
    # Reconcile read/unread markers on every tree load so freshly rsync'd
    # pages surface as unread (matches garage). Best-effort: a failure here
    # must never break the tree itself.
    try:
        unread = wiki_unread.reconcile(wiki_root)
    except Exception:
        log.exception("wiki unread reconcile failed for %s", name)
        unread = set()
    return jsonify({
        "missing": False,
        "tree": _build_wiki_tree(wiki_root, vault_root, Path("."), unread),
    })


@bp.post("/api/vaults/<name>/wiki/read")
@_csrf_required
def vault_wiki_set_read(name):
    """Toggle the read/unread marker for a wiki page.

    Body: ``{file: "wiki/concepts/gguf.md", read: true|false}``. ``read:true``
    removes the unread marker, ``read:false`` creates it. Returns the page's
    resulting unread state.
    """
    reg = _ctx()["vault_registry"]
    v = reg.get(name)
    if not v:
        return jsonify({"error": "vault not found"}), 404
    body = request.get_json(force=True, silent=True) or {}
    rel = (body.get("file") or "").strip()
    if not rel:
        return jsonify({"error": "file required"}), 400
    if not rel.startswith("wiki/"):
        return jsonify({"error": "file must be a wiki/ path"}), 400
    read = bool(body.get("read"))
    wiki_rel = rel[5:]
    wiki_root = Path(v.path).resolve() / "wiki"
    ok = (wiki_unread.mark_read(wiki_root, wiki_rel) if read
          else wiki_unread.mark_unread(wiki_root, wiki_rel))
    if not ok:
        return jsonify({"error": "invalid path"}), 400
    return jsonify({
        "file": "wiki/" + wiki_rel,
        "unread": wiki_unread.is_unread(wiki_root, wiki_rel),
    })


@bp.get("/api/vaults/<name>/wiki/random")
def vault_wiki_random(name):
    """Pick a random unread wiki page (reconciles first). Returns
    ``{file: "wiki/…"}`` or ``{file: null}`` when nothing is unread."""
    reg = _ctx()["vault_registry"]
    v = reg.get(name)
    if not v:
        return jsonify({"error": "vault not found"}), 404
    wiki_root = Path(v.path).resolve() / "wiki"
    if not wiki_root.is_dir():
        return jsonify({"file": None})
    rel = wiki_unread.pick_random_unread(wiki_root)
    return jsonify({"file": ("wiki/" + rel) if rel else None})


@bp.get("/api/vaults/<name>/wiki/search")
def vault_wiki_search(name):
    """Full-text search over the vault's wiki pages (titles weighted 5×)."""
    reg = _ctx()["vault_registry"]
    v = reg.get(name)
    if not v:
        return jsonify({"error": "vault not found"}), 404
    q = (request.args.get("q") or "").strip()
    wiki_root = Path(v.path).resolve() / "wiki"
    if not wiki_root.is_dir():
        return jsonify({"query": q, "hits": []})
    return jsonify({"query": q, "hits": wiki_unread.search(wiki_root, q)})


# ----- Help (in-app docs from the repo's man/ tree) -----
def _man_root() -> Path:
    """Locate the man/ help tree.

    Defaults to ``RESMAN_ROOT/man`` (the repo ships the manual at its root)
    so the docs travel with the source. Overridable via ``app.man_path`` in
    resman.yaml for installs that ship man pages elsewhere.
    """
    cm = _ctx()["config"]
    override = (cm.app.get("man_path") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (_ctx()["resman_root"] / "man").resolve()


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
    """Launch Obsidian for this vault using `obsidian_cmd` from resman.yaml.

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
        return jsonify({"error": "obsidian_cmd not configured in resman.yaml"}), 400
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


@bp.get("/api/sessions/stats")
def sessions_stats():
    """Enriched view of every tracked session for the overview modal.

    Read-only — no CSRF requirement. Returns ttyd PID + RSS, tmux pane PIDs
    and their full descendant trees with per-process RSS, and a roll-up
    total per session. Used by the clickable connection pill so the user
    can audit what's running and spot heavy or stale terminals.
    """
    sm = _ctx()["session_manager"]
    return jsonify(sm.stats())


@bp.post("/api/sessions/orphans/kill")
@_csrf_required
def kill_orphan_sessions():
    """Kill every orphaned tmux session matching the resman prefix.

    "Orphan" = a tmux session on our isolated socket whose name starts with
    our prefix but is not in the live registry — typically left over from
    a previous control-plane run. Used by the "Kill all" action in the
    sessions-overview modal. Best-effort per name; partial success is OK.
    """
    sm = _ctx()["session_manager"]
    if not sm.available:
        return jsonify({"killed": [], "failed": [],
                        "note": "ttyd unavailable; no sessions tracked"}), 200
    result = sm.kill_orphaned_tmux_sessions()
    killed, failed = len(result.get("killed", [])), len(result.get("failed", []))
    _activity(f"killed {killed} orphan session(s)" + (f", {failed} failed" if failed else ""),
              level="warn" if failed else "info", source="session")
    return jsonify(result)


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
    # Optional: wrap /claude-obsidian:wiki with the prefix/suffix instruction
    # files (tools/newValPrefix.md, tools/newValSuffix.md) and paste the whole
    # block into the Claude prompt as a single message. Used by the new-vault
    # wizard so plugin-presence is checked before bootstrap and the visual
    # workspace.json is copied after.
    initial_text = None
    if body.get("bootstrap_new_vault"):
        if session_type != "claude":
            return jsonify({"error": "bootstrap_new_vault requires type='claude'"}), 400
        if initial_command:
            return jsonify({
                "error": "bootstrap_new_vault and initial_command are mutually exclusive"
            }), 400
        repo_root = _ctx()["resman_root"]
        initial_text = plugin_commands.new_vault_bootstrap_prompt(
            repo_root / plugin_commands.NEW_VAULT_PREFIX_FILE,
            repo_root / plugin_commands.NEW_VAULT_SUFFIX_FILE,
        )
    try:
        s = sm.spawn(
            vault=v.name, vault_path=v.path, session_type=session_type,
            claude_cmd=_ctx()["config"].app.get("claude_cmd", "claude"),
            initial_command=initial_command,
            initial_text=initial_text,
        )
    except Exception as exc:
        log.exception("spawn session failed")
        _activity(f"session spawn failed: {v.name} ({session_type}) — {exc}",
                  level="error", source="session")
        return jsonify({"error": str(exc)}), 500
    _activity(f"session spawned: {v.name} ({session_type})", source="session")
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
            force=bool(body.get("force")),
            check_limits=bool(body.get("check_limits")),
        )
    except ValueError as exc:
        _activity(f"task rejected: {body.get('operation')} — {exc}",
                  level="warn", source="task")
        return jsonify({"error": str(exc)}), 400
    _activity(f"task queued: {t.operation} on {t.vault or 'all'} [{t.priority}]",
              source="task", detail=t.id)
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


@bp.post("/api/tasks/<tid>/attend")
@_csrf_required
def attend_task(tid):
    """Re-run a task's Claude prompt in an interactive REPL the user can attach to.

    The original task ran via `claude -p` and so couldn't accept input. We
    rebuild the same prompt, spawn a Claude session in the task's vault,
    and bracketed-paste the prompt into the REPL so the user lands inside a
    live run and can answer whatever the task asks.

    Only operations that drive Claude with a prompt are attendable
    (wiki-lint, wiki-autoresearch, wiki-canvas, wiki-update-hot-cache,
    wiki-bootstrap, wiki-hint, run-prompt). Shell-based operations
    (wiki-ingest, wiki-ingest-prefix, run-shell) return 400.
    """
    ctx = _ctx()
    tm = ctx["task_manager"]
    t = tm.get(tid)
    if not t:
        return jsonify({"error": "task not found"}), 404
    if t.vault == "ALL":
        return jsonify({"error": "cannot attend a parent ALL-vault task"}), 400
    prompt = tm.build_attend_prompt(t)
    if not prompt:
        return jsonify({
            "error": f"operation {t.operation!r} is not attendable "
                     "(no Claude prompt to re-run)",
        }), 400
    reg = ctx["vault_registry"]
    v = reg.get(t.vault)
    if not v:
        return jsonify({"error": f"vault {t.vault!r} is no longer registered"}), 400
    sm = ctx["session_manager"]
    if not sm.available:
        return jsonify({"error": "ttyd not installed"}), 503
    try:
        s = sm.spawn(
            vault=v.name, vault_path=v.path, session_type="claude",
            claude_cmd=ctx["config"].app.get("claude_cmd", "claude"),
            initial_text=prompt,
        )
    except Exception as exc:
        log.exception("attend session spawn failed")
        return jsonify({"error": str(exc)}), 500
    return jsonify(s.to_dict()), 201


@bp.post("/api/tasks/compact")
@_csrf_required
def compact_tasks():
    tm = _ctx()["task_manager"]
    return jsonify(tm.compact())


@bp.post("/api/tasks/clean")
@_csrf_required
def clean_tasks():
    """Archive all finished tasks (optionally for one vault) — the queue's
    'Clean finished' button. Soft delete: events remain in the log."""
    body = request.get_json(force=True, silent=True) or {}
    vault = body.get("vault") or None
    tm = _ctx()["task_manager"]
    result = tm.clean_terminal(vault=vault)
    _activity(f"cleaned {result.get('cleaned', 0)} finished task(s)"
              + (f" for {vault}" if vault else ""), source="task")
    return jsonify(result)


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


# ----- Window schedule (cld20-style daily/weekly windows) -----
@bp.get("/api/window/schedule")
def get_window_schedule():
    """Return the configured windows + live status (current/next/weekly) + log."""
    sched = _ctx().get("window_schedule")
    if not sched:
        return jsonify({"error": "window schedule unavailable"}), 503
    return jsonify(sched.to_dict())


@bp.put("/api/window/schedule")
@_csrf_required
def put_window_schedule():
    """Update window-schedule parameters (windows, weekly_anchor, offset, length)."""
    sched = _ctx().get("window_schedule")
    if not sched:
        return jsonify({"error": "window schedule unavailable"}), 503
    body = request.get_json(force=True, silent=True) or {}
    fields = {k: body[k] for k in
              ("windows", "weekly_anchor", "operator_hour_offset", "window_length_hours",
               "refresh_interval_minutes", "sync_interval_minutes", "collection_rate")
              if k in body}
    try:
        return jsonify(sched.update(**fields))
    except window_schedule_mod.ScheduleError as exc:
        return jsonify({"error": str(exc)}), 400


@bp.get("/api/window/next-night")
def get_next_night_window():
    """ISO start of the next night window, for scheduling night-window tasks."""
    sched = _ctx().get("window_schedule")
    if not sched:
        return jsonify({"error": "window schedule unavailable"}), 503
    return jsonify({"at": sched.next_night_window_iso()})


@bp.post("/api/window/sync")
@_csrf_required
def post_window_sync():
    """Refresh window/limit state on demand (the footer ⟳ sync button)."""
    sched = _ctx().get("window_schedule")
    if not sched:
        return jsonify({"error": "window schedule unavailable"}), 503
    return jsonify(sched.sync())


# ----- Window usage statistics (cld20-style sample history) -----
@bp.get("/api/window/stats")
def get_window_stats():
    """Recent usage readings + summary + the next opener/sample times. Powers the
    Windows-tab charts and per-window breakdown."""
    stats = _ctx().get("window_stats")
    sched = _ctx().get("window_schedule")
    if not stats:
        return jsonify({"samples": [], "summary": {}, "automation": {}})
    try:
        limit = int(request.args.get("limit", 500))
    except (TypeError, ValueError):
        limit = 500
    return jsonify({
        "samples": stats.list(limit=limit, source=request.args.get("source")),
        "summary": stats.summary(),
        "automation": sched.automation() if sched else {},
    })


@bp.post("/api/window/sample")
@_csrf_required
def post_window_sample():
    """Take one usage reading on demand (the Windows-tab "Collect now" button).
    Distinct from the footer ⟳ sync: this stores a sample in the history."""
    sampler = _ctx().get("window_sampler")
    if not sampler:
        return jsonify({"error": "window sampler unavailable"}), 503
    entry = sampler.collect_now()
    return jsonify({"sample": entry})


@bp.post("/api/window/stats/clear")
@_csrf_required
def clear_window_stats():
    """Clear the stored usage-reading history."""
    stats = _ctx().get("window_stats")
    if stats:
        stats.clear()
        _activity("window stats cleared", source="window")
    return jsonify({"ok": True})


# ----- Activity log (volatile, footer "Log" window) -----
@bp.get("/api/logs")
def get_logs():
    """Recent activity-log entries. Optional ?limit=&level=&source= filters."""
    activity = _ctx().get("activity")
    if not activity:
        return jsonify({"entries": []})
    try:
        limit = int(request.args.get("limit", 300))
    except (TypeError, ValueError):
        limit = 300
    entries = activity.list(
        limit=limit,
        level=request.args.get("level"),
        source=request.args.get("source"),
    )
    return jsonify({"entries": entries})


@bp.post("/api/logs/clear")
@_csrf_required
def clear_logs():
    """Clear the activity log."""
    activity = _ctx().get("activity")
    if activity:
        activity.clear()
    return jsonify({"ok": True})


# ----- Cron -----
@bp.get("/api/cron")
def list_cron():
    return jsonify({"cron_tasks": _ctx()["scheduler"].cron_status()})


# ----- Config (live YAML editor) -----
# `resman.yaml` is the canonical name; `system.yaml` is accepted as a legacy
# alias so old clients/scripts keep working until they've been updated.
_RESMAN_ALIASES = ("resman.yaml", "system.yaml")
_YAML_FILES = (*_RESMAN_ALIASES, "schedule.yaml")


def _resolve_yaml_path(cm, fname: str) -> Path:
    if fname in _RESMAN_ALIASES:
        return cm.resman_path
    return cm.config_dir / fname


def _resman_meta(cm) -> dict:
    """Surface where the live resman.yaml actually lives so the UI can label
    the editor (e.g. show `~/.resman.yaml` when the user override is in use)."""
    path = cm.resman_path
    home = str(Path.home())
    spath = str(path)
    display = "~" + spath[len(home):] if spath.startswith(home + "/") else spath
    return {
        "resman_path": spath,
        "resman_display_path": display,
        "using_user_override": bool(cm.using_user_override),
    }


@bp.get("/api/config/yaml")
def get_yaml():
    cm = _ctx()["config"]
    fname = request.args.get("file", "resman.yaml")
    if fname not in _YAML_FILES:
        return jsonify({"error": "unknown file"}), 400
    p = _resolve_yaml_path(cm, fname)
    body = {"file": fname, "content": ""}
    if fname in _RESMAN_ALIASES:
        body.update(_resman_meta(cm))
    if p.exists():
        body["content"] = p.read_text(encoding="utf-8")
    return jsonify(body)


@bp.post("/api/config/yaml")
@_csrf_required
def save_yaml():
    body = request.get_json(force=True, silent=True) or {}
    fname = body.get("file")
    content = body.get("content")
    if fname not in _YAML_FILES:
        return jsonify({"error": "unknown file"}), 400
    if not isinstance(content, str):
        return jsonify({"error": "content must be a string"}), 400
    cm = _ctx()["config"]
    try:
        if fname in _RESMAN_ALIASES:
            cm.save_resman_yaml(content)
        else:
            cm.save_schedule_yaml(content)
    except ConfigError as exc:
        _activity(f"config save failed: {fname} — {exc}", level="warn", source="config")
        return jsonify({"error": str(exc)}), 400
    _activity(f"config saved: {fname}", source="config")
    return jsonify({"ok": True})
