"""Wiki read/unread tracking — ported from the garage resman model.

State is tracked with **sidecar marker files** colocated with each wiki page:
a page ``concepts/gguf.md`` is *unread* iff ``concepts/.gguf.unrd`` exists.
Reading a page does NOT auto-mark it; the user toggles state explicitly (the
existence-of-marker model is what survives the rsync that mirrors the same
wiki pages between the garage resman and this project).

A baseline file ``<wiki>/.unrd-scan`` records (via its mtime) when the last
reconcile ran. ``reconcile()`` marks any page whose ctime is newer than the
baseline as unread — so freshly rsync'd pages surface as unread — and prunes
markers whose page was deleted. The first scan (no baseline) marks everything
unread.

All paths handed to this module are **relative to the wiki directory** (e.g.
``concepts/gguf.md`` — no leading ``wiki/``). The route layer strips that
prefix before calling in.
"""
from __future__ import annotations

import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

BASELINE = ".unrd-scan"
MARKER_SUFFIX = ".unrd"
SEARCH_MAX_FILES = 2000
SEARCH_LIMIT = 50
TITLE_WEIGHT = 5
BODY_WEIGHT = 1


# ----- path helpers -----
def _safe_rel(wiki_dir: Path, rel: str) -> Optional[Path]:
    """Resolve ``rel`` under ``wiki_dir``; return the page Path or None if the
    path is empty, contains traversal, or escapes the wiki directory."""
    rel = (rel or "").strip()
    if not rel or rel.startswith("/") or "\n" in rel or ".." in rel.split("/"):
        return None
    try:
        wd = wiki_dir.resolve()
        target = (wd / rel).resolve()
        target.relative_to(wd)
    except (ValueError, OSError):
        return None
    return target


def _marker_for(wiki_dir: Path, rel: str) -> Path:
    """Marker path for a wiki-relative page path (``a/b.md`` → ``a/.b.unrd``)."""
    p = wiki_dir / rel
    return p.parent / ("." + p.stem + MARKER_SUFFIX)


def _iter_pages(wiki_dir: Path):
    """Yield absolute paths of every ``.md`` page, skipping dot-dirs/files and symlinks."""
    for root, dirs, files in os.walk(wiki_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.startswith(".") or not f.endswith(".md"):
                continue
            p = Path(root) / f
            if p.is_symlink():
                continue
            yield p


def _iter_markers(wiki_dir: Path):
    """Yield absolute paths of every ``.*.unrd`` marker (markers live in real dirs)."""
    for root, dirs, files in os.walk(wiki_dir):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            if f.startswith(".") and f.endswith(MARKER_SUFFIX):
                yield Path(root) / f


def _create_marker(marker: Path) -> None:
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(marker), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        os.close(fd)
    except FileExistsError:
        pass
    except OSError as exc:
        log.debug("could not create unread marker %s: %s", marker, exc)


def _touch_baseline(path: Path, when: float) -> None:
    try:
        if not path.exists():
            os.close(os.open(str(path), os.O_CREAT | os.O_WRONLY, 0o644))
        os.utime(str(path), (when, when))
    except OSError as exc:
        log.debug("could not stamp baseline %s: %s", path, exc)


# ----- public API -----
def list_unread(wiki_dir: Path) -> set[str]:
    """Set of wiki-relative page paths that currently have an unread marker."""
    out: set[str] = set()
    if not wiki_dir.is_dir():
        return out
    for md in _iter_pages(wiki_dir):
        rel = md.relative_to(wiki_dir).as_posix()
        if _marker_for(wiki_dir, rel).exists():
            out.add(rel)
    return out


def is_unread(wiki_dir: Path, rel: str) -> bool:
    if _safe_rel(wiki_dir, rel) is None:
        return False
    return _marker_for(wiki_dir, rel).exists()


def mark_read(wiki_dir: Path, rel: str) -> bool:
    """Remove the unread marker (idempotent). Returns False on a bad path."""
    if _safe_rel(wiki_dir, rel) is None:
        return False
    try:
        _marker_for(wiki_dir, rel).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return False
    return True


def mark_unread(wiki_dir: Path, rel: str) -> bool:
    """Create the unread marker (idempotent). Returns False on a bad path."""
    if _safe_rel(wiki_dir, rel) is None:
        return False
    _create_marker(_marker_for(wiki_dir, rel))
    return True


def reconcile(wiki_dir: Path) -> set[str]:
    """Scan the wiki for new/changed/deleted pages and update markers.

    First scan (no baseline) marks every page unread. Later scans mark pages
    whose ctime is newer than the baseline as unread (catches rsync'd pages)
    and prune markers for deleted pages. Returns the resulting unread set.
    """
    if not wiki_dir.is_dir():
        return set()
    baseline = wiki_dir / BASELINE
    first = not baseline.exists()
    base_mtime = 0.0 if first else _safe_mtime(baseline)
    scan_start = time.time()

    page_rels: set[str] = set()
    for md in _iter_pages(wiki_dir):
        rel = md.relative_to(wiki_dir).as_posix()
        page_rels.add(rel)
        marker = _marker_for(wiki_dir, rel)
        if first:
            _create_marker(marker)
        elif not marker.exists():
            ctime = _safe_ctime(md)
            if ctime > base_mtime:
                _create_marker(marker)

    # Prune markers whose page was deleted.
    for marker in _iter_markers(wiki_dir):
        page = marker.parent / (marker.name[1:-len(MARKER_SUFFIX)] + ".md")
        try:
            rel = page.relative_to(wiki_dir).as_posix()
        except ValueError:
            continue
        if rel not in page_rels:
            try:
                marker.unlink()
            except OSError:
                pass

    _touch_baseline(baseline, scan_start)
    return list_unread(wiki_dir)


def pick_random_unread(wiki_dir: Path) -> Optional[str]:
    """Reconcile, then return a random unread wiki-relative path (or None)."""
    unread = reconcile(wiki_dir)
    if not unread:
        return None
    return random.choice(sorted(unread))


# ----- search -----
def search(wiki_dir: Path, query: str, limit: int = SEARCH_LIMIT) -> list[dict]:
    """Rank wiki pages against a query. Titles weigh 5×, body 1×; all tokens
    must appear (AND). Returns ``[{file, rel, title, snippet, score}]``."""
    tokens = [t for t in re.split(r"\s+", (query or "").lower().strip()) if len(t) >= 2]
    if not tokens or not wiki_dir.is_dir():
        return []
    hits: list[dict] = []
    scanned = 0
    for md in _iter_pages(wiki_dir):
        scanned += 1
        if scanned > SEARCH_MAX_FILES:
            log.info("wiki search capped at %d files", SEARCH_MAX_FILES)
            break
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        title = _extract_title(text, md)
        score = _score(title, text, tokens)
        if score <= 0:
            continue
        rel = md.relative_to(wiki_dir).as_posix()
        hits.append({
            "file": "wiki/" + rel,
            "rel": rel,
            "title": title,
            "snippet": _snippet(text, tokens),
            "score": score,
        })
    hits.sort(key=lambda h: (-h["score"], h["rel"]))
    return hits[:limit]


def _extract_title(text: str, md: Path) -> str:
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return md.stem


def _score(title: str, body: str, tokens: list[str]) -> int:
    tl, bl = title.lower(), body.lower()
    score = 0
    for tok in tokens:
        ct, cb = tl.count(tok), bl.count(tok)
        if ct == 0 and cb == 0:
            return 0  # AND logic — every token must appear
        score += ct * TITLE_WEIGHT + cb * BODY_WEIGHT
    return score


def _snippet(body: str, tokens: list[str], width: int = 200) -> str:
    """Return a PLAIN-TEXT excerpt around the first match. Highlighting (the
    <mark> wrapping) is done client-side so no server HTML is ever injected
    into the DOM — the client escapes this text before rendering."""
    bl = body.lower()
    positions = [bl.find(t) for t in tokens if bl.find(t) >= 0]
    pos = min(positions) if positions else 0
    start = max(0, pos - 40)
    chunk = body[start:start + width].replace("\n", " ").strip()
    prefix = "…" if start > 0 else ""
    suffix = "…" if start + width < len(body) else ""
    return prefix + chunk + suffix


# ----- internal -----
def _safe_mtime(p: Path) -> float:
    try:
        return p.stat().st_mtime
    except OSError:
        return 0.0


def _safe_ctime(p: Path) -> float:
    try:
        return p.stat().st_ctime
    except OSError:
        return 0.0
