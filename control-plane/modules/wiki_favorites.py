"""Wiki favorites — a hand-editable ``<vault>/.favorites.md`` link list.

Each vault keeps its favorite pages in a single markdown file at the vault
root. The file is owned jointly by the user (who edits it in any editor) and
resman (the ★ toggle in the Wiki tab), so this module follows two rules:

1. **Read leniently.** One favorite per line; a line may be a bare path
   (``wiki/concepts/gguf.md``), an Obsidian wikilink (``[[wiki/hot|Hot]]``),
   or a markdown link (``[Index](wiki/index.md)``), optionally behind a list
   bullet / number / checkbox. Headings, comments, blank lines and prose are
   ignored. Paths are vault-relative; a missing extension means ``.md``.
2. **Write conservatively.** ``add`` appends one line (creating the file with
   a ``# Favorites`` heading if needed); ``remove`` drops only the lines that
   resolve to the removed page and keeps everything else byte-for-byte. Both
   write atomically (temp file + ``os.replace``).

resman writes entries as ``- [[<path>|<page title>]]`` so the file doubles as
a working link list when opened inside Obsidian.
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

log = logging.getLogger(__name__)

FAVORITES_FILE = ".favorites.md"
HEADER = "# Favorites\n\n"
DEFAULT_EXT = ".md"
NEW_FILE_MODE = 0o644
# Characters that terminate the ``[[target|alias]]`` line this module writes.
# A path containing one could be written but never parsed back (a stuck
# entry), so it is rejected outright — Obsidian forbids them in note names too.
_LINK_BREAKERS = frozenset("[]|#")

_BULLET_RE = re.compile(r"^(?:[-*+]|\d+[.)])\s+")
_CHECKBOX_RE = re.compile(r"^\[[ xX]\]\s+")
# [[target]], [[target|alias]], [[target#heading|alias]], ![[embed]]
_WIKILINK_RE = re.compile(r"^!?\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
# [text](target), [text](<target with spaces>), [text](target "title")
_MDLINK_RE = re.compile(r"^\[[^\]]*\]\(\s*<?([^)>]+?)>?(?:\s+\"[^\"]*\")?\s*\)")
# Anything that could end the alias early: a lone bracket is enough.
_ALIAS_STRIP_RE = re.compile(r"[\[\]|]")

# One lock per vault so two toggles can't interleave their read-modify-write
# and drop each other's line (Flask serves requests concurrently).
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _vault_lock(vault_root: Path) -> threading.Lock:
    try:
        key = str(vault_root.resolve())
    except OSError:
        key = str(vault_root)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.Lock()
        return lock


# ----- paths -----
def normalize(rel: Optional[str]) -> Optional[str]:
    """Canonical vault-relative form of a favorites entry, or None if unsafe.

    Trims whitespace, drops a leading ``./``, collapses repeated slashes and
    appends ``.md`` when the last segment has no extension. Rejects empty,
    absolute, traversal (``..``), control-character paths and paths containing
    wikilink delimiters (``[ ] | #``).
    """
    s = (rel or "").strip()
    if not s or any(ch in s for ch in "\n\r\0"):
        return None
    if any(ch in _LINK_BREAKERS for ch in s):
        return None
    if s.startswith("/"):
        return None
    s = re.sub(r"/{2,}", "/", s)
    while s.startswith("./"):
        s = s[2:]
    parts = s.split("/")
    if any(p in ("", ".", "..") for p in parts):
        return None
    if "." not in parts[-1]:
        s += DEFAULT_EXT
    return s


def page_path(vault_root: Path, rel: str) -> Optional[Path]:
    """Absolute path of a favorite's page, or None if it would escape the vault
    (a symlink inside the vault can still point outside — resolve and check)."""
    n = normalize(rel)
    if n is None:
        return None
    try:
        root = vault_root.resolve()
        target = (root / n).resolve()
        target.relative_to(root)
    except (OSError, ValueError, RuntimeError):
        return None
    return target


def _require(rel: str) -> str:
    n = normalize(rel)
    if n is None:
        raise ValueError(f"invalid favorites path: {rel!r}")
    return n


# ----- parsing -----
def _line_target(line: str) -> Optional[str]:
    """The raw link target on one line of the favorites file, or None when the
    line carries no favorite (heading, comment, blank, prose)."""
    s = line.strip()
    if not s or s.startswith("#") or s.startswith("<!--") or s.startswith("%%"):
        return None
    s = _BULLET_RE.sub("", s, count=1)
    s = _CHECKBOX_RE.sub("", s, count=1)
    m = _WIKILINK_RE.match(s)
    if m:
        return m.group(1).strip()
    m = _MDLINK_RE.match(s)
    if m:
        return unquote(m.group(1).strip())
    # A bare path only counts when it looks like one (a slash or a .md suffix)
    # so a stray line of prose is never mistaken for a page.
    if "/" in s or s.lower().endswith(DEFAULT_EXT):
        return s
    return None


def _line_rel(line: str) -> Optional[str]:
    target = _line_target(line)
    return normalize(target) if target is not None else None


def parse(text: str) -> list[str]:
    """Ordered, de-duplicated list of normalized vault-relative paths."""
    out: list[str] = []
    seen: set[str] = set()
    for line in (text or "").splitlines():
        rel = _line_rel(line)
        if rel is None or rel in seen:
            continue
        seen.add(rel)
        out.append(rel)
    return out


# ----- file I/O -----
def _read(vault_root: Path) -> Optional[str]:
    """Contents of the favorites file, or None when it doesn't exist (or can't
    be read — logged, treated as empty so a bad file never breaks the UI)."""
    path = vault_root / FAVORITES_FILE
    try:
        # newline="" keeps CRLF intact so a rewrite really is byte-for-byte.
        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
            return fh.read()
    except FileNotFoundError:
        return None
    except OSError as exc:
        log.warning("could not read %s: %s", path, exc)
        return None


def _atomic_write(path: Path, content: str) -> None:
    """Write via temp file + rename. The replacement keeps the existing file's
    permission bits (mkstemp creates 0600, which would lock a shared/synced
    vault's file to one user); a new file gets NEW_FILE_MODE like the unread
    markers."""
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = NEW_FILE_MODE
    fd, tmp = tempfile.mkstemp(prefix=".favorites.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ----- titles -----
def _strip_frontmatter(text: str) -> str:
    if not text.startswith("---"):
        return text
    end = text.find("\n---", 3)
    return text[end + 4:] if end >= 0 else text


def _title_of(page: Path) -> str:
    """First H1 of the page (frontmatter skipped), else the file stem."""
    try:
        text = page.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return page.stem
    for line in _strip_frontmatter(text).splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip() or page.stem
    return page.stem


def _alias_for(vault_root: Path, rel: str) -> str:
    """Link alias for a written entry: the page title with the characters that
    would terminate a wikilink removed. Goes through page_path() so a symlink
    that escapes the vault can't leak an outside file's heading."""
    page = page_path(vault_root, rel)
    if page is None:
        raise ValueError(f"favorites path escapes the vault: {rel!r}")
    title = _title_of(page)
    return " ".join(_ALIAS_STRIP_RE.sub(" ", title).split()) or Path(rel).stem


# ----- public API -----
def list_favorites(vault_root: Path) -> list[str]:
    text = _read(vault_root)
    return parse(text) if text is not None else []


def is_favorite(vault_root: Path, rel: str) -> bool:
    n = normalize(rel)
    return n is not None and n in list_favorites(vault_root)


def add(vault_root: Path, rel: str) -> bool:
    """Append ``rel`` to the favorites file. Returns False when it was already
    listed (file untouched). Raises ValueError on an unsafe path, OSError on
    a write failure."""
    n = _require(rel)
    alias = _alias_for(vault_root, n)
    with _vault_lock(vault_root):
        text = _read(vault_root)
        if text is not None and n in parse(text):
            return False
        base = HEADER if text is None else text
        nl = "\r\n" if "\r\n" in base else "\n"
        if base and not base.endswith("\n"):
            base += nl
        _atomic_write(vault_root / FAVORITES_FILE, f"{base}- [[{n}|{alias}]]{nl}")
    return True


def remove(vault_root: Path, rel: str) -> bool:
    """Drop every line that resolves to ``rel``; keep all other lines verbatim.
    Returns False when nothing was listed. Raises ValueError / OSError as add."""
    n = _require(rel)
    with _vault_lock(vault_root):
        text = _read(vault_root)
        if text is None:
            return False
        lines = text.splitlines(keepends=True)
        kept = [ln for ln in lines if _line_rel(ln) != n]
        if len(kept) == len(lines):
            return False
        _atomic_write(vault_root / FAVORITES_FILE, "".join(kept))
    return True


def entries(vault_root: Path) -> list[dict]:
    """Favorites as the API returns them: ``[{file, title, exists}]`` in file
    order. ``exists`` is False for a dangling entry (page deleted or renamed)
    so the UI can flag the broken link instead of hiding it."""
    out: list[dict] = []
    for rel in list_favorites(vault_root):
        page = page_path(vault_root, rel)
        exists = bool(page and page.is_file())
        out.append({
            "file": rel,
            "title": _title_of(page) if exists else Path(rel).stem,
            "exists": exists,
        })
    return out
