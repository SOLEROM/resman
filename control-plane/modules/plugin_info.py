"""Read-only facts about the installed claude-obsidian plugin (the Skills tab).

resman drives every vault through the claude-obsidian Claude Code plugin
(see plugin_commands.py) but does not ship it: the plugin is installed once
per user by `claude plugin install`. This module finds that install and
describes it — version, skills, commands — and checks that every plugin
command resman sends actually exists in it.

Where the install is found, first hit wins:
  1. Claude Code's registry, ``<claude dir>/plugins/installed_plugins.json``,
     any entry ``claude-obsidian@<marketplace>`` — the marketplace name
     depends on how it was added (``claude-obsidian-marketplace`` from
     ``marketplace add``, ``agricidaniel-claude-obsidian`` when declared in
     settings.json). One enabled in ``<claude dir>/settings.json`` wins, then
     the default PLUGIN_ID; user scope first.
  2. The plugin cache, ``<claude dir>/plugins/cache/<any marketplace>/claude-obsidian/<version>/``,
     newest version — covers a registry that is missing or points at a
     version folder an update already removed.

``<claude dir>`` is tried in order: ``$CLAUDE_CONFIG_DIR`` when set, then the
account's own ``~/.claude`` (``$HOME``, then the passwd home) — so a resman
started with another config dir or HOME still finds the user's install.
Every read is best-effort: a missing or malformed file means "not
installed" or a warning, never an exception out of ``summary()``.
"""
from __future__ import annotations

import json
import os
import pwd
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml

from . import plugin_commands

PLUGIN_ID = plugin_commands.PLUGIN_ID
PLUGIN_NAME, _, MARKETPLACE = PLUGIN_ID.partition("@")
# The canvas companion plugin the docs recommend; reported, never required.
COMPANION_NAME = "claude-canvas"

MAX_FILE_BYTES = 512 * 1024
MAX_REGISTRY_BYTES = 1024 * 1024
FRONTMATTER_PEEK_BYTES = 8192
MAX_FILES_PER_SKILL = 60
_VERSION_PART_RE = re.compile(r"\d+")


@dataclass(frozen=True)
class PluginInstall:
    path: Path
    version: str
    located_by: str            # "registry" | "cache"
    scope: str = ""
    installed_at: str = ""
    last_updated: str = ""
    commit: str = ""
    claude_dir: Optional[Path] = None   # the <claude dir> it was found under
    plugin_id: str = PLUGIN_ID          # <plugin>@<marketplace> it is registered as


class PluginFileError(ValueError):
    """A requested plugin file is outside the install, not markdown, or unreadable."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def claude_dir() -> Path:
    env = (os.environ.get("CLAUDE_CONFIG_DIR") or "").strip()
    return Path(env).expanduser() if env else Path.home() / ".claude"


def _home_claude_dirs() -> list[Path]:
    """The account's own ~/.claude: $HOME's, then the passwd entry's."""
    homes = [Path.home()]
    try:
        homes.append(Path(pwd.getpwuid(os.getuid()).pw_dir))
    except (KeyError, OSError):
        pass
    return [h / ".claude" for h in homes]


def claude_dirs() -> list[Path]:
    """Every <claude dir> locate() searches, in order, without duplicates."""
    out: list[Path] = []
    for d in [claude_dir(), *_home_claude_dirs()]:
        if d not in out:
            out.append(d)
    return out


def _read_registry(base: Path) -> dict:
    path = base / "plugins" / "installed_plugins.json"
    try:
        if path.stat().st_size > MAX_REGISTRY_BYTES:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    plugins = data.get("plugins") if isinstance(data, dict) else None
    return plugins if isinstance(plugins, dict) else {}


def _version_key(name: str) -> tuple:
    return tuple(int(n) for n in _VERSION_PART_RE.findall(name)) or (-1,)


def _enabled_plugins(base: Path) -> set:
    try:
        data = json.loads((base / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    enabled = data.get("enabledPlugins") if isinstance(data, dict) else None
    return {k for k, v in enabled.items() if v} if isinstance(enabled, dict) else set()


def _from_registry(entries, base: Optional[Path] = None,
                   plugin_id: str = PLUGIN_ID) -> Optional[PluginInstall]:
    if not isinstance(entries, list):
        return None
    valid = [e for e in entries if isinstance(e, dict) and isinstance(e.get("installPath"), str)]
    valid.sort(key=lambda e: e.get("scope") != "user")      # user scope first
    for e in valid:
        path = Path(e["installPath"]).expanduser()
        if path.is_dir():
            return PluginInstall(
                path=path, version=str(e.get("version") or path.name),
                located_by="registry", scope=str(e.get("scope") or ""),
                installed_at=str(e.get("installedAt") or ""),
                last_updated=str(e.get("lastUpdated") or ""),
                commit=str(e.get("gitCommitSha") or ""), claude_dir=base,
                plugin_id=plugin_id)
    return None


def _registry_hit(base: Path) -> Optional[PluginInstall]:
    """The registry entry for claude-obsidian under any marketplace."""
    plugins = _read_registry(base)
    enabled = _enabled_plugins(base)
    keys = sorted((k for k in plugins if k.split("@", 1)[0] == PLUGIN_NAME),
                  key=lambda k: (k not in enabled, k != PLUGIN_ID, k))
    for key in keys:
        found = _from_registry(plugins[key], base, key)
        if found:
            return found
    return None


def _from_cache(base: Path, name: str) -> Optional[PluginInstall]:
    """The newest cached version folder of the plugin, under any marketplace."""
    versions = []
    try:
        for market in (base / "plugins" / "cache").iterdir():
            root = market / name
            if root.is_dir():
                versions += [(market.name, p) for p in root.iterdir()
                             if p.is_dir() and not p.name.startswith(".")]
    except OSError:
        return None
    if not versions:
        return None
    market, newest = max(versions, key=lambda mp: (_version_key(mp[1].name),
                                                   mp[0] == MARKETPLACE))
    return PluginInstall(path=newest, version=newest.name, located_by="cache",
                         claude_dir=base, plugin_id=f"{name}@{market}")


def locate(base: Optional[Path] = None) -> Optional[PluginInstall]:
    """The installed claude-obsidian plugin, or None when it is not installed.

    With ``base``, only that Claude dir is searched; else every claude_dirs().
    """
    for d in ([base] if base else claude_dirs()):
        found = _registry_hit(d) or _from_cache(d, PLUGIN_NAME)
        if found:
            return found
    return None


def plugin_dir(base: Optional[Path] = None) -> Optional[Path]:
    found = locate(base)
    return found.path if found else None


def _companion(base: Path) -> dict:
    for key, entries in _read_registry(base).items():
        if key.split("@", 1)[0] == COMPANION_NAME:
            found = _from_registry(entries)
            if found:
                return {"name": COMPANION_NAME, "installed": True, "version": found.version}
    return {"name": COMPANION_NAME, "installed": False, "version": None}


def _frontmatter(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            head = fh.read(FRONTMATTER_PEEK_BYTES)
    except OSError:
        return {}
    if not head.startswith("---"):
        return {}
    end = head.find("\n---", 3)
    if end < 0:
        return {}
    try:
        data = yaml.safe_load(head[3:end])
    except yaml.YAMLError:
        return {}
    return data if isinstance(data, dict) else {}


def _describe(path: Path) -> str:
    desc = _frontmatter(path).get("description")
    return " ".join(desc.split()) if isinstance(desc, str) else ""


def _md_files(folder: Path, root: Path) -> list[str]:
    """Markdown files under folder (no symlinks), as paths relative to root."""
    out: list[str] = []
    for p in sorted(folder.rglob("*.md")):
        if len(out) >= MAX_FILES_PER_SKILL:
            break
        if p.is_symlink() or not p.is_file():
            continue
        out.append(p.relative_to(root).as_posix())
    return out


def frontmatter(path: Path) -> dict:
    """The YAML frontmatter of a markdown file, ``{}`` when absent or broken."""
    return _frontmatter(path)


# The reader below works on any folder in the Claude Code plugin format:
# claude-obsidian's install, and resman's own skills/ (resman_skills.py).

def _skills_in(root: Path) -> list[dict]:
    skills = []
    base = root / "skills"
    try:
        folders = sorted(p for p in base.iterdir() if p.is_dir() and not p.is_symlink())
    except OSError:
        return skills
    for folder in folders:
        skill_md = folder / "SKILL.md"
        if not skill_md.is_file():
            continue
        files = _md_files(folder, root)
        main = skill_md.relative_to(root).as_posix()
        skills.append({
            "name": folder.name,
            "description": _describe(skill_md),
            "path": main,
            "files": [main] + [f for f in files if f != main],
        })
    return skills


def _commands_in(root: Path) -> list[dict]:
    try:
        files = sorted((root / "commands").glob("*.md"))
    except OSError:
        return []
    return [{"name": f.stem, "description": _describe(f),
             "path": f.relative_to(root).as_posix()}
            for f in files if f.is_file() and not f.is_symlink()]


def _manifest_in(root: Path) -> dict:
    try:
        data = json.loads((root / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


DOC_NAMES = ("README.md", "CHANGELOG.md", "WIKI.md")


def _docs_in(root: Path, names: tuple = DOC_NAMES) -> list[str]:
    return [n for n in names if (root / n).is_file()]


def describe(root: Path, docs: tuple = DOC_NAMES) -> dict:
    """A plugin folder's contents — manifest, skills (with their markdown
    files), commands, top-level docs — for any folder in the plugin format."""
    return {"manifest": _manifest_in(root), "skills": _skills_in(root),
            "commands": _commands_in(root), "docs": _docs_in(root, docs)}


def _skills(install: PluginInstall) -> list[dict]:
    return _skills_in(install.path)


def _commands(install: PluginInstall) -> list[dict]:
    return _commands_in(install.path)


def _manifest(install: PluginInstall) -> dict:
    return _manifest_in(install.path)


def summary(base: Optional[Path] = None) -> dict:
    """Everything the Skills tab shows, plus the warnings its badge counts."""
    install = locate(base)
    searched = [base] if base else claude_dirs()
    base = install.claude_dir if install and install.claude_dir else searched[0]
    uses_src = plugin_commands.PLUGIN_USES
    plugin = {"id": PLUGIN_ID, "name": PLUGIN_NAME, "installed": install is not None,
              "searched": [str(d) for d in searched]}
    if install is None:
        where = ", ".join(str(d / "plugins") for d in searched)
        return {
            "plugin": plugin, "companion": _companion(base),
            "uses": [{"name": n, "where": w, "provided": False, "kind": None}
                     for n, w in uses_src.items()],
            "skills": [], "commands": [], "docs": [],
            "warnings": [f"The {PLUGIN_NAME} plugin ({PLUGIN_ID}) was not found in "
                         f"{where} — resman's wiki tasks and the new-vault bootstrap "
                         f"need it. If `claude plugin list` shows it, this resman runs "
                         f"with another HOME or CLAUDE_CONFIG_DIR; otherwise install: "
                         f"claude plugin install {PLUGIN_ID}"],
        }

    manifest = _manifest(install)
    plugin.update({
        "id": install.plugin_id, "version": install.version, "path": str(install.path),
        "located_by": install.located_by, "scope": install.scope,
        "installed_at": install.installed_at, "last_updated": install.last_updated,
        "commit": install.commit, "claude_dir": str(base),
        "description": " ".join(str(manifest.get("description") or "").split()),
        "homepage": str(manifest.get("homepage") or ""),
    })
    skills = _skills(install)
    commands = _commands(install)
    skill_names = {s["name"] for s in skills}
    command_names = {c["name"] for c in commands}
    uses, warnings = [], []
    for name, where in uses_src.items():
        kind = ("skill+command" if name in skill_names and name in command_names
                else "skill" if name in skill_names
                else "command" if name in command_names else None)
        uses.append({"name": name, "where": where, "provided": kind is not None, "kind": kind})
        if kind is None:
            warnings.append(f"resman sends {PLUGIN_NAME}:{name} ({where}), but the "
                            f"installed plugin {install.version} has no skill or command "
                            f"by that name.")
    for s in skills:
        s["used"] = s["name"] in uses_src
    docs = _docs_in(install.path)
    return {"plugin": plugin, "companion": _companion(base), "uses": uses,
            "skills": skills, "commands": commands, "docs": docs, "warnings": warnings}


def read_file(rel: str, base: Optional[Path] = None) -> str:
    """Raw text of a markdown file inside the plugin install (traversal-safe)."""
    install = locate(base)
    if install is None:
        raise PluginFileError(f"the {PLUGIN_NAME} plugin is not installed", 404)
    return read_file_from(install.path, rel)


def read_file_from(folder: Path, rel: str) -> str:
    """Raw text of a markdown file inside ``folder`` (traversal-safe, ``.md``
    only, size-capped) — the same rules for every provider's folder."""
    rel = (rel or "").strip()
    if not rel:
        raise PluginFileError("path required")
    root = Path(folder).resolve()
    try:
        target = (root / rel).resolve()
        target.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        raise PluginFileError("path escapes the plugin folder")
    if target.suffix.lower() != ".md":
        raise PluginFileError("only .md files are served")
    if not target.is_file():
        raise PluginFileError(f"not found: {rel}", 404)
    if target.stat().st_size > MAX_FILE_BYTES:
        raise PluginFileError("file too large", 413)
    try:
        return target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise PluginFileError(str(exc), 500)
