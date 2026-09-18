"""Wiki highlights — colored reader highlights stored in the page's own markdown.

A highlight is one inline tag pair, written exactly as

    <mark class="hl-yellow">the highlighted words</mark>

so it travels with the vault: any markdown renderer shows a ``<mark>``, and an
app that ships the ``hl-*`` colors shows the chosen one. Six fixed colors
(``COLORS``); the file names the color, the theme decides the shade.

This is the only code that writes a wiki page, so it keeps a narrow promise:

1. **Marks only.** An operation adds, removes or recolors tag pairs and nothing
   else. The client sends offsets and a color name, never markup; the tags are
   built here. ``strip_marks(after) == strip_marks(before)`` is asserted before
   every write.
2. **Inline only.** A highlight never spans a blank line (inline HTML cannot
   cross blocks), never lands in frontmatter, fenced code, an HTML comment or
   inside a code span or a tag, and hugs its text so the tag is never alone on
   a line (CommonMark would read that as an HTML block).
3. **Paint over.** A new highlight that touches existing ones absorbs them, so
   highlights never nest or overlap by our own hand.
4. **The reader's view.** Offsets are code points into the text as the page
   API serves it (universal newlines). ``content_sha`` of that text is the
   concurrency token: agents and editors write these files too, and a stale
   token is a 409, not a lost update. CRLF files keep their newlines; the write
   is atomic (temp file + ``os.replace``) and keeps the file mode.

Marks are addressed by index in document order, counting only the ones a
renderer would show (not those quoted in code, comments or frontmatter) — the
same order the browser sees them in.
"""
from __future__ import annotations

import bisect
import hashlib
import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

COLORS = ("yellow", "green", "blue", "pink", "orange", "purple")
CLASS_PREFIX = "hl-"
CLOSE_TAG = "</mark>"
MAX_FILE_BYTES = 2 * 1024 * 1024      # same cap as the page reader
NEW_FILE_MODE = 0o644

# Every scan below is linear on hostile input (a 2 MB page of `<mark<mark…`,
# `<!--<!--…` or backtick runs): tag bodies stop at the next `<` or newline,
# and comments and code spans are found with str.find, not lazy regexes.
_MARK_TOKEN_RE = re.compile(r"<mark\b([^<>\n]*)>|</mark\s*>", re.IGNORECASE)
_CLASS_ATTR_RE = re.compile(r"""\bclass\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"</?[A-Za-z][^<>\n]*>")
_FENCE_RE = re.compile(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$")
_BACKTICK_RUN_RE = re.compile(r"`+")
# A paragraph break: an empty line, also one that only carries `>` markers.
_BLANK_LINE_RE = re.compile(r"\n[ \t]*(?:>[ \t]*)*\n")


class HighlightError(ValueError):
    """A refused highlight operation. The message is user-facing; ``status``
    is the HTTP status the route answers with (400 malformed, 409 the page
    changed under the reader, 422 this text cannot carry a highlight)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class Mark:
    open_start: int
    open_end: int
    close_start: int
    close_end: int
    color: str


@dataclass(frozen=True)
class Edit:
    """Replace ``remove`` characters at ``pos`` with ``insert``."""
    pos: int
    remove: int
    insert: str


def open_tag(color: str) -> str:
    return f'<mark class="{CLASS_PREFIX}{color}">'


def to_view(raw: str) -> str:
    """The text as the page API serves it (``read_text`` universal newlines)."""
    return raw.replace("\r\n", "\n").replace("\r", "\n")


def content_sha(view: str) -> str:
    return hashlib.sha256(view.encode("utf-8", errors="replace")).hexdigest()


# ----- what a renderer does not show as markup -----
def _frontmatter_end(view: str) -> int:
    if not view.startswith("---\n"):
        return 0
    end = view.find("\n---", 4)
    if end == -1:
        return 0
    line_end = view.find("\n", end + 4)
    return len(view) if line_end == -1 else line_end + 1


def _fence_ranges(view: str, start: int) -> list[tuple[int, int]]:
    """Fenced code blocks (``` or ~~~) from ``start`` on, as [begin, end)
    offsets that include both fence lines. A backtick fence whose info string
    holds a backtick is not a fence (CommonMark)."""
    ranges: list[tuple[int, int]] = []
    fence: Optional[tuple[str, int, int]] = None      # (char, length, begin)
    pos = start
    for line in view[start:].split("\n"):
        m = _FENCE_RE.match(line)
        if fence is not None:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] \
                    and not m.group(2).strip():
                ranges.append((fence[2], pos + len(line)))
                fence = None
        elif m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
            fence = (m.group(1)[0], len(m.group(1)), pos)
        pos += len(line) + 1
    if fence is not None:
        ranges.append((fence[2], len(view)))          # unclosed: runs to the end
    return ranges


def _inside(starts: list[int], ranges: list[tuple[int, int]], pos: int) -> bool:
    """`ranges` sorted and non-overlapping, `starts` their start offsets."""
    k = bisect.bisect_right(starts, pos) - 1
    return k >= 0 and pos < ranges[k][1]


def _comments(view: str, a: int, b: int) -> list[tuple[int, int]]:
    """HTML comments in [a, b); an unterminated one runs to b (conservative:
    the renderer may treat it as a comment block)."""
    out: list[tuple[int, int]] = []
    i = view.find("<!--", a, b)
    while i != -1:
        j = view.find("-->", i + 4, b)
        if j == -1:
            out.append((i, b))
            break
        out.append((i, j + 3))
        i = view.find("<!--", j + 3, b)
    return out


def _code_spans(text: str, offset: int) -> list[tuple[int, int]]:
    """Inline code spans of one paragraph: a backtick run closed by the next
    run of the same length (CommonMark); an unclosed run is literal."""
    runs = [(m.start(), m.end() - m.start()) for m in _BACKTICK_RUN_RE.finditer(text)]
    by_length: dict[int, list[int]] = {}
    for k, (_start, length) in enumerate(runs):
        by_length.setdefault(length, []).append(k)
    out: list[tuple[int, int]] = []
    k = 0
    while k < len(runs):
        start, length = runs[k]
        same = by_length[length]
        n = bisect.bisect_right(same, k)
        if n < len(same):
            close = runs[same[n]]
            out.append((offset + start, offset + close[0] + close[1]))
            k = same[n] + 1
        else:
            k += 1
    return out


def _gaps(total: tuple[int, int], holes: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out, cursor = [], total[0]
    for a, b in sorted(holes):
        if a > cursor:
            out.append((cursor, a))
        cursor = max(cursor, b)
    if cursor < total[1]:
        out.append((cursor, total[1]))
    return out


def _masks(view: str) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """(blocks, code_spans): ``blocks`` are frontmatter, fenced code and HTML
    comments — no highlight may touch them; ``code_spans`` are inline code —
    a highlight may wrap one whole but not cut into it."""
    fm_end = _frontmatter_end(view)
    blocks = [(0, fm_end)] if fm_end else []
    blocks += _fence_ranges(view, fm_end)
    for a, b in _gaps((fm_end, len(view)), list(blocks)):
        blocks += _comments(view, a, b)
    spans: list[tuple[int, int]] = []
    for a, b in _gaps((0, len(view)), blocks):
        cursor = a
        for sep in _BLANK_LINE_RE.finditer(view, a, b):
            spans += _code_spans(view[cursor:sep.start()], cursor)
            cursor = sep.end()
        spans += _code_spans(view[cursor:b], cursor)
    return sorted(blocks), spans


def _color_of(attrs: str) -> Optional[str]:
    m = _CLASS_ATTR_RE.search(attrs or "")
    if not m:
        return None
    for token in (m.group(1) or m.group(2) or m.group(3) or "").split():
        if token.startswith(CLASS_PREFIX) and token[len(CLASS_PREFIX):] in COLORS:
            return token[len(CLASS_PREFIX):]
    return None


def find_marks(view: str) -> list[Mark]:
    """Our highlights in document order (by opening tag). Foreign ``<mark>``
    tags pair up like ours but are not reported; unpaired tags are ignored."""
    blocks, spans = _masks(view)
    hidden = sorted(blocks + spans)
    starts = [a for a, _b in hidden]
    stack: list[tuple[int, int, Optional[str]]] = []
    marks: list[Mark] = []
    for m in _MARK_TOKEN_RE.finditer(view):
        if _inside(starts, hidden, m.start()):
            continue
        if not m.group(0).startswith("</"):
            stack.append((m.start(), m.end(), _color_of(m.group(1))))
        elif stack:
            open_start, open_end, color = stack.pop()
            if color is not None:
                marks.append(Mark(open_start, open_end, m.start(), m.end(), color))
    return sorted(marks, key=lambda mark: mark.open_start)


# ----- edits -----
def apply_edits(text: str, edits: list[Edit]) -> str:
    merged: dict[int, Edit] = {}
    for e in edits:
        prev = merged.get(e.pos)
        merged[e.pos] = e if prev is None else \
            Edit(e.pos, prev.remove + e.remove, prev.insert + e.insert)
    for pos in sorted(merged, reverse=True):
        e = merged[pos]
        text = text[:pos] + e.insert + text[pos + e.remove:]
    return text


def _tag_deletions(marks: list[Mark]) -> list[Edit]:
    out: list[Edit] = []
    for m in marks:
        out.append(Edit(m.open_start, m.open_end - m.open_start, ""))
        out.append(Edit(m.close_start, m.close_end - m.close_start, ""))
    return out


def strip_marks(view: str) -> str:
    """The page without our highlights — what every operation must preserve."""
    return apply_edits(view, _tag_deletions(find_marks(view)))


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_color(color) -> str:
    if not isinstance(color, str) or color not in COLORS:
        raise HighlightError(f"color must be one of {', '.join(COLORS)}", 400)
    return color


def plan_add(view: str, start, end, color, expect=None) -> list[Edit]:
    color = _require_color(color)
    if not (_is_int(start) and _is_int(end)) or not 0 <= start < end <= len(view):
        raise HighlightError("start/end must be offsets inside the page, start < end", 400)
    if expect is not None:
        if not isinstance(expect, str):
            raise HighlightError("expect must be a string", 400)
        if view[start:end] != expect:
            raise HighlightError("the page text changed; reload and select again", 409)
    while start < end and view[start].isspace():
        start += 1
    while end > start and view[end - 1].isspace():
        end -= 1
    if start == end:
        raise HighlightError("nothing to highlight", 400)

    marks = find_marks(view)
    grown = True
    while grown:                      # paint over every highlight we touch
        grown = False
        for m in marks:
            if m.open_start < end and m.close_end > start and \
                    (m.open_start < start or m.close_end > end):
                start, end = min(start, m.open_start), max(end, m.close_end)
                grown = True

    blocks, spans = _masks(view)
    if any(a < end and b > start for a, b in blocks):
        raise HighlightError("cannot highlight inside code, comments or frontmatter", 422)
    if any(a < end and b > start and not (start <= a and b <= end) for a, b in spans):
        raise HighlightError("cannot highlight part of a code span; select all of it", 422)
    if _BLANK_LINE_RE.search(view, start, end):
        raise HighlightError("a highlight cannot span paragraphs", 422)
    for tag in _ANY_TAG_RE.finditer(view):
        if tag.start() < start < tag.end() or tag.start() < end < tag.end():
            raise HighlightError("cannot start or end a highlight inside a tag", 422)

    absorbed = [m for m in marks if start <= m.open_start and m.close_end <= end]
    return _tag_deletions(absorbed) + [Edit(start, 0, open_tag(color)), Edit(end, 0, CLOSE_TAG)]


def _mark_at(view: str, index) -> Mark:
    if not _is_int(index):
        raise HighlightError("index must be an integer", 400)
    marks = find_marks(view)
    if not 0 <= index < len(marks):
        raise HighlightError("no such highlight; reload the page", 409)
    return marks[index]


def plan_remove(view: str, index) -> list[Edit]:
    return _tag_deletions([_mark_at(view, index)])


def plan_recolor(view: str, index, color) -> list[Edit]:
    color = _require_color(color)
    m = _mark_at(view, index)
    return [Edit(m.open_start, m.open_end - m.open_start, open_tag(color))]


def plan(view: str, op) -> list[Edit]:
    """The edits for one client operation ``{op: add|remove|recolor, …}``."""
    if not isinstance(op, dict):
        raise HighlightError("operation must be an object", 400)
    kind = op.get("op")
    if kind == "add":
        return plan_add(view, op.get("start"), op.get("end"), op.get("color"), op.get("expect"))
    if kind == "remove":
        return plan_remove(view, op.get("index"))
    if kind == "recolor":
        return plan_recolor(view, op.get("index"), op.get("color"))
    raise HighlightError("op must be add, remove or recolor", 400)


def add(view: str, start, end, color, expect=None) -> str:
    return apply_edits(view, plan_add(view, start, end, color, expect))


def remove(view: str, index) -> str:
    return apply_edits(view, plan_remove(view, index))


def recolor(view: str, index, color) -> str:
    return apply_edits(view, plan_recolor(view, index, color))


# ----- files -----
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _file_lock(path: Path) -> threading.Lock:
    key = str(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.Lock()
        return lock


def _raw_edits(raw: str, edits: list[Edit]) -> list[Edit]:
    """View offsets → raw offsets: every CRLF before a position is one char
    longer in the file. Edits never touch a newline, so lengths carry over."""
    if "\r\n" not in raw:
        return edits
    # Where each CRLF's "\n" sits in the view; every one before a position
    # adds one raw character.
    view_at = [m.start() - n for n, m in enumerate(re.finditer("\r\n", raw))]
    return [Edit(e.pos + bisect.bisect_left(view_at, e.pos), e.remove, e.insert) for e in edits]


def _atomic_write(path: Path, content: str) -> None:
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        mode = NEW_FILE_MODE
    fd, tmp = tempfile.mkstemp(prefix=".highlight.", suffix=".tmp", dir=str(path.parent))
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


def apply_to_file(path: Path, op, base_sha, dry_run: bool = False) -> dict:
    """Run one operation against ``path``. ``base_sha`` is the ``content_sha``
    of the text the reader is looking at. Returns ``{content, sha}`` for the
    resulting view; with ``dry_run`` nothing is written. Raises HighlightError
    (refused) or OSError (the write itself failed)."""
    path = Path(path)
    with _file_lock(path):
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                raise HighlightError("page is too large to highlight", 422)
            data = path.read_bytes()
        except OSError as exc:
            raise HighlightError(f"cannot read the page: {exc.strerror or exc}", 404) from exc
        if len(data) > MAX_FILE_BYTES:          # grew between stat and read
            raise HighlightError("page is too large to highlight", 422)
        try:
            raw = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HighlightError("page is not valid UTF-8; it cannot be rewritten safely", 422) from exc
        view = to_view(raw)
        if not isinstance(base_sha, str) or base_sha != content_sha(view):
            raise HighlightError("the page changed on disk; reload and select again", 409)
        edits = plan(view, op)
        new_view = apply_edits(view, edits)
        if strip_marks(new_view) != strip_marks(view):
            log.error("highlight op on %s would change page text: %r", path, op)
            raise HighlightError("refused: the operation would change the page text", 422)
        if not dry_run:
            _atomic_write(path, apply_edits(raw, _raw_edits(raw, edits)))
    return {"content": new_view, "sha": content_sha(new_view)}
