"""REST API — reader highlights on Wiki tab pages: the only route that writes a
wiki page.

A highlight is ``<mark class="hl-COLOR">…</mark>`` in the page's own markdown
(modules/wiki_highlights.py owns the format and every safety rule). The
browser sends where and which color, never markup::

    POST /api/vaults/<name>/wiki/highlight
    {file, base_sha, op: "add",     start, end, expect, color [, dry_run]}
    {file, base_sha, op: "remove",  index               [, dry_run]}
    {file, base_sha, op: "recolor", index, color        [, dry_run]}

``file`` is vault-relative (``wiki/…``, as ``GET …/wiki?file=`` takes it;
``path`` is accepted as an alias). ``base_sha`` is the ``sha`` that GET served;
a page changed since (an agent, Obsidian, an rsync) answers 409 and nothing is
written. Offsets are code points into the served ``content``. ``dry_run``
returns the resulting page without writing, so the browser can render and
check it first. The answer is the page (``{file, content, sha,
highlightable}``).

Writable: markdown pages the Wiki tree lists (``routes.highlightable``). The
edit is the reader's own, so it does not flag the page unread
(``wiki_unread.settle_after_edit``).
"""
from __future__ import annotations

import logging
from pathlib import Path

from flask import Blueprint, jsonify, request

from . import wiki_highlights
from . import wiki_unread
from .routes import _csrf_required, _ctx, highlightable

log = logging.getLogger(__name__)
bp = Blueprint("api_highlights", __name__)


def _error(message: str, status: int):
    return jsonify({"error": message}), status


@bp.post("/api/vaults/<name>/wiki/highlight")
@_csrf_required
def vault_wiki_highlight(name):
    v = _ctx()["vault_registry"].get(name)
    if not v:
        return _error("vault not found", 404)
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return _error("request body must be a JSON object", 400)
    rel = body.get("file") or body.get("path")
    if not isinstance(rel, str) or not rel.strip():
        return _error("file required", 400)
    base_sha = body.get("base_sha")
    if not isinstance(base_sha, str) or not base_sha:
        return _error("base_sha required", 400)
    try:
        vault_root = Path(v.path).resolve()
        target = (vault_root / rel.strip()).resolve()
        vault_rel = target.relative_to(vault_root).as_posix()
    except (OSError, RuntimeError, ValueError):
        return _error("invalid path", 400)
    if not highlightable(vault_rel):
        return _error(f"not a highlightable page: {rel!r}", 400)
    if not target.is_file():
        return _error(f"not found: {vault_rel}", 404)

    dry_run = body.get("dry_run") is True
    wiki_root = vault_root / "wiki"
    wiki_rel = vault_rel[len("wiki/"):]
    was_unread = wiki_unread.is_unread(wiki_root, wiki_rel)
    try:
        result = wiki_highlights.apply_to_file(target, body, base_sha, dry_run=dry_run)
    except wiki_highlights.HighlightError as exc:
        return _error(str(exc), exc.status)
    except OSError as exc:
        log.warning("highlight write failed for %s: %s", target, exc)
        return _error(f"could not write {vault_rel}: {exc.strerror or exc}", 500)
    if not dry_run:
        try:
            wiki_unread.settle_after_edit(wiki_root, wiki_rel, was_unread)
        except Exception:               # read state is best-effort; the write stands
            log.exception("unread settle failed after highlighting %s", vault_rel)
    payload = {"file": vault_rel, "content": result["content"], "sha": result["sha"],
               "highlightable": True}
    if dry_run:
        payload["dry_run"] = True
    return jsonify(payload)
